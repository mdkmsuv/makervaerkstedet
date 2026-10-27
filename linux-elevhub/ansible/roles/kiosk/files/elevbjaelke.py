#!/usr/bin/env python3
"""Elevbjælken: en fast bjælke øverst på elevens skærm.

- "Til start" bringer eleven tilbage til startsiden fra ethvert værksted og lukker et internetvindue.
- Melder hvert 3. sekund til elevhubben, at computeren er tændt, og hvilken version af lærerens
  valg elevhub-regler faktisk har sat (/run/elevhub/anvendt.json).
- Læser den lokale projektservices loopback-health, så læreren ser installerede lokale apps og
  den aktuelle Scratch/Datatræner/Kredsløb-visning uden at sende projektdata.
- Viser en reversibel besked oven på det aktive værksted, hvis læreren lukker det.
- Lukker internetvinduer (også dem, en elev selv åbner), når Internet er lukket.
- Viser en indsat USB-nøgle og kan tage den sikkert ud.

Styres af Ansible (rollen kiosk). Indstillinger: /etc/elevhub/kiosk.json
"""
import json
import http.client
import math
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elevhub_klient import hent_json  # noqa: E402
try:
    from elevhub_klient import server_utilgaengelig  # noqa: E402
except ImportError:  # kept for older staged bundles during rolling updates
    import ssl
    import urllib.error
    from elevhub_klient import RedirectError
    def server_utilgaengelig(fejl):
        return (isinstance(fejl, (OSError, TimeoutError)) and
                not isinstance(fejl, (urllib.error.HTTPError, RedirectError, ssl.SSLError)))
from pause_overlay import PauseOverlay  # noqa: E402
from update_status import STATUS_PATH, current_boot_id, message, read_status  # noqa: E402

BRUGER = os.environ.get('USER', 'elev')
HOEJDE = 60
ANVENDT = '/run/elevhub/anvendt.json'
APP_VINDUE = '^usr_local_share_elevhub_start[.]html$'  # Chromium --app=file:///.../start.html
ALMINDELIGT_VINDUE = '^chromium$'      # almindelige browservinduer (internet)
START_TITEL = 'Værkstedet · Makerværkstedet'   # <title> på startsiden (start.html.j2)
BRAND, BRAND_MOERK, INK, MUTED, LINJE = '#c62b2f', '#8c1d24', '#333333', '#666666', '#e1e1e1'
THEME_COLORS = {
    'red': ('#c62b2f', '#8c1d24'),
    'yellow': ('#a36a00', '#704600'),
    'green': ('#28734e', '#1a5037'),
}


def apply_theme(theme):
    """Apply the configured accent; malformed values safely use red."""
    global BRAND, BRAND_MOERK
    BRAND, BRAND_MOERK = THEME_COLORS.get(theme, THEME_COLORS['red'])
    return BRAND, BRAND_MOERK
SKRIFT = 'Metrophobic'
BATTERI_ROOT = '/sys/class/power_supply'
BATTERI_LAVT = 20
BATTERI_KRITISK = 5


def xdo(*argumenter):
    return subprocess.run(['xdotool', *argumenter], capture_output=True, text=True, timeout=5).stdout.split()


def anvendt_version(sti=ANVENDT, nu=None, maks_alder=15):
    """Returnér den nyligt anvendte politikversion, ellers 0.

    Statusfilen kan blive liggende, når elevhub-regler er stoppet. Derfor må
    versionen kun meldes som anvendt, hvis filens tidsstempel er gyldigt,
    højst ``maks_alder`` sekunder gammelt og ikke ligger i fremtiden.
    """
    if nu is None:
        nu = time.time()
    try:
        with open(sti, encoding='utf-8') as f:
            data = json.load(f)
        version = data.get('version') if isinstance(data, dict) else None
        tid = data.get('tid') if isinstance(data, dict) else None
        if (type(version) is not int or version <= 0 or
                isinstance(tid, bool) or not isinstance(tid, (int, float)) or
                not math.isfinite(tid) or tid > nu or nu - tid > maks_alder):
            return 0
        return version
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return 0


def usb_noegler():
    """Monterede USB-nøgler (udiskie monterer under /media/<bruger>/)."""
    fundet = []
    try:
        with open('/proc/mounts', encoding='utf-8') as mounts:
            for linje in mounts:
                enhed, sted = linje.split()[:2]
                # /proc/mounts skriver mellemrum m.m. som oktale koder (backslash + tre cifre).
                sted = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), sted)
                if sted.startswith(f'/media/{BRUGER}/') and enhed.startswith('/dev/'):
                    # MICROBIT is the workshop device's firmware volume, not
                    # a student USB drive for project files.
                    if Path(sted).name.upper().startswith('MICROBIT'):
                        continue
                    fundet.append((enhed, sted))
    except OSError:
        pass
    return fundet


@dataclass(frozen=True)
class BatteriStatus:
    """A small, UI-independent snapshot of the local power supply state."""

    percent: int | None = None
    oplader: bool | None = None
    stroemforsyning: bool | None = None
    batteri_fundet: bool = False


def _sysfs_tekst(sti):
    try:
        return Path(sti).read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        return None


def _sysfs_energi(mappe):
    try:
        nu = float(_sysfs_tekst(Path(mappe) / 'energy_now'))
        fuld = float(_sysfs_tekst(Path(mappe) / 'energy_full'))
        if (math.isfinite(nu) and math.isfinite(fuld) and
                0 <= nu <= fuld and fuld > 0):
            return nu, fuld
    except (TypeError, ValueError):
        pass
    return None


def _sysfs_procent(mappe):
    raw = _sysfs_tekst(Path(mappe) / 'capacity')
    try:
        procent = int(raw)
        if 0 <= procent <= 100:
            return procent
    except (TypeError, ValueError):
        pass

    # Some systems omit ``capacity`` but expose energy_now/full instead.  Use
    # those only when both values are finite and positive; never invent 0%.
    energi = _sysfs_energi(mappe)
    if energi is not None:
        nu, fuld = energi
        return round(100 * nu / fuld)
    return None


def batteri_status(root=BATTERI_ROOT):
    """Read battery and mains state from sysfs without assuming a battery exists."""
    base = Path(root)
    try:
        forsyninger = [sti for sti in base.iterdir() if sti.is_dir()]
    except OSError:
        return BatteriStatus()

    batterier = []
    mains_signaler = []
    for forsyning in forsyninger:
        forsyningstype = _sysfs_tekst(forsyning / 'type')
        if forsyningstype is None:
            er_batteri = forsyning.name.upper().startswith('BAT')
        else:
            er_batteri = forsyningstype.lower() == 'battery'
        if er_batteri:
            batterier.append(forsyning)
            continue
        online = _sysfs_tekst(forsyning / 'online')
        if online == '1':
            mains_signaler.append(True)
        elif online == '0':
            mains_signaler.append(False)

    if not batterier:
        return BatteriStatus()

    energi = [_sysfs_energi(sti) for sti in batterier]
    if all(par is not None for par in energi):
        samlet_nu = sum(par[0] for par in energi)
        samlet_fuld = sum(par[1] for par in energi)
        percent = round(100 * samlet_nu / samlet_fuld) if samlet_fuld else None
    else:
        # Capacity values are not comparable across differently sized packs;
        # use the lowest known value until complete energy data is available.
        procenter = [procent for procent in (_sysfs_procent(sti) for sti in batterier)
                     if procent is not None]
        percent = min(procenter) if procenter else None
    tilstande = []
    for sti in batterier:
        tilstand = _sysfs_tekst(sti / 'status')
        if tilstand:
            tilstande.append(tilstand.lower())
    oplader = True if 'charging' in tilstande else (
        False if any(tilstand in ('discharging', 'not charging', 'full') for tilstand in tilstande)
        else None)
    if True in mains_signaler:
        stroemforsyning = True
    elif False in mains_signaler:
        stroemforsyning = False
    elif 'charging' in tilstande or 'full' in tilstande:
        stroemforsyning = True
    elif 'discharging' in tilstande:
        stroemforsyning = False
    else:
        stroemforsyning = None
    return BatteriStatus(percent, oplader, stroemforsyning, True)


def batteri_tekst(status):
    """Return the short, always-visible status used in the pupil bar."""
    if not status or not status.batteri_fundet:
        return ''
    dele = []
    if status.percent is not None:
        dele.append(f'Batteri {status.percent} %')
    else:
        dele.append('Batteri')
    if status.oplader is True:
        dele.append('Oplader')
    elif status.stroemforsyning is True:
        dele.append('Tilsluttet strøm')
    elif status.stroemforsyning is False:
        dele.append('På batteri')
    else:
        dele.append('Strømstatus ukendt')
    return ' · '.join(dele)


def batteri_advarsel(status):
    """Return a gentle inline warning, or ``None`` when no warning is needed."""
    if (not status or not status.batteri_fundet or status.percent is None or
            status.stroemforsyning is True or status.oplader is True):
        return None
    if status.percent <= BATTERI_KRITISK:
        return 'Kritisk batteri · Tilslut strøm nu'
    if status.percent <= BATTERI_LAVT:
        return 'Lavt batteri · Tilslut strøm'
    return None


def lokal_status(konf, timeout=0.5):
    """Read the loopback project service's bounded health snapshot.

    The kiosk start page is a ``file:`` document and cannot make a safe CORS
    health request.  The privileged, root-authenticated status heartbeat is
    the right place to carry readiness and the current local app navigation.
    """
    raw = konf.get('lokale_app_urler', {}).get('projekter', '')
    try:
        url = urlsplit(raw)
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port:
            return None
        path = '/_elevhub/health'
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
        try:
            connection.request('GET', path, headers={'Host': f'127.0.0.1:{url.port}'})
            response = connection.getresponse()
            if response.status != 200:
                return None
            data = json.loads(response.read(64 * 1024))
        finally:
            connection.close()
        if not isinstance(data, dict) or data.get('local') is not True:
            return None
        apps = data.get('apps')
        navigation = data.get('navigation')
        if not isinstance(apps, list) or len(apps) > 32:
            return None
        ready = []
        ids = set()
        for app in apps:
            if not isinstance(app, dict) or not isinstance(app.get('id'), str):
                return None
            app_id = app['id']
            if app_id in ids or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', app_id):
                return None
            ids.add(app_id)
            if type(app.get('ready', True)) is not bool:
                return None
            ready.append({'id': app_id, 'ready': app.get('ready', True)})
        if navigation is None:
            return {'apps': ready, 'navigation': None}
        if not isinstance(navigation, dict):
            return None
        nav_id = navigation.get('id')
        nav_version = navigation.get('version')
        if nav_id != 'start' and nav_id not in ids:
            return None
        if not isinstance(nav_version, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', nav_version):
            return None
        return {'apps': ready, 'navigation': {'id': nav_id, 'version': nav_version}}
    except (OSError, ValueError, TypeError, json.JSONDecodeError, http.client.HTTPException):
        return None


class Bjaelke:
    def __init__(self, konf):
        # Import only when the graphical process starts; status helpers/tests
        # must also work on headless controller/WSL environments.
        global tk
        import tkinter as tk
        self.konf = konf
        self.rod = tk.Tk(className='elevbjaelke')
        self.rod.protocol('WM_DELETE_WINDOW', lambda: None)
        bredde = self.rod.winfo_screenwidth()
        self.rod.geometry(f'{bredde}x{HOEJDE}+0+0')
        self.rod.configure(bg='white')
        try:
            self.rod.wm_attributes('-type', 'dock')
        except tk.TclError:
            pass
        self.rod.wm_attributes('-topmost', True)
        self.rod.overrideredirect(False)

        self.internet_aaben = True
        # A cold-start network outage is an offline workshop mode.  A valid
        # response (or a reachable-but-invalid response) changes this to
        # online; only transport unavailability returns to offline mode.
        self.server_status = 'offline'
        self.local_navigation_version = None
        self.besked_timer = None
        self.laas = threading.Lock()
        self.navigation_busy = False
        # The overlay is deliberately local to this Tk process. It keeps the
        # current Chromium document alive, so Scratch work can remain in the
        # open editor while a teacher temporarily closes the workshop.
        self.pause_overlay = PauseOverlay(self.rod)

        ramme = tk.Frame(self.rod, bg='white', highlightthickness=0)
        ramme.pack(fill='both', expand=True)
        tk.Frame(self.rod, bg=LINJE, height=1).place(relx=0, rely=1.0, relwidth=1, anchor='sw')

        self.start = tk.Button(ramme, text='⌂  Til start', command=self.til_start, font=(SKRIFT, 17),
                               bg=BRAND, fg='white', activebackground=BRAND_MOERK, activeforeground='white',
                               relief='flat', bd=0, padx=26, cursor='hand2', highlightthickness=0)
        self.start.pack(side='left', fill='y', padx=(10, 16), pady=8)

        self.tekst = tk.Label(ramme, text=self.konf['computer_visning'], font=(SKRIFT, 14), bg='white', fg=MUTED)
        self.tekst.pack(side='left')
        self.update_band = tk.Frame(ramme, bg='white', width=390, height=58)
        self.update_band.pack_propagate(False)
        self.opdateringsstatus = tk.Label(self.update_band, text='', font=(SKRIFT, 12, 'bold'),
                                          bg='white', fg=BRAND, anchor='w', justify='left',
                                          wraplength=380)

        from power_controls import PowerControls
        self.power = PowerControls(self.rod, ramme, paused=lambda: self.pause_overlay.paused,
                                   before_power=self.afslut_foer)

        self.batteri = tk.Frame(ramme, bg='white', width=190, height=42)
        self.batteri.pack_propagate(False)
        self.batteri_status = tk.Label(self.batteri, text='', font=(SKRIFT, 13),
                                       bg='white', fg=MUTED, anchor='e', justify='right',
                                       wraplength=190)
        self.batteri_status.pack(fill='x')
        self.batteri_advarsel = tk.Label(self.batteri, text='', font=(SKRIFT, 11),
                                         bg='white', fg=BRAND, anchor='e', justify='right',
                                         wraplength=190)

        from cadoodle_vaerksted import tilfoej
        self.cadoodle = tilfoej(self, ramme)
        from workshop_launcher import attach
        self.workshop_launcher = attach(self.cadoodle, self)

        if Path('/etc/elevhub/admin-login.json').is_file():
            self.admin_knap = tk.Button(ramme, text='Admin', command=self.admin_login, font=(SKRIFT, 13),
                                        bg='white', fg=MUTED, activebackground='#f4f3f0', relief='flat', bd=0,
                                        padx=18, cursor='hand2', highlightthickness=0)
            self.admin_knap.pack(side='right', fill='y', padx=(8, 10), pady=8)

        self.usb_knap = tk.Button(ramme, text='Tag USB ud', command=self.skub_ud, font=(SKRIFT, 13),
                                  bg='white', fg=INK, activebackground='#f4f3f0', relief='solid', bd=1,
                                  padx=16, cursor='hand2', highlightthickness=0)
        self.usb_tekst = tk.Label(ramme, text='', font=(SKRIFT, 11), bg='white', fg=INK,
                                  wraplength=145, justify='right')

        self.rod.after(500, self.meld)
        self.rod.after(1000, self.tjek_usb)
        self.rod.after(1000, self.tjek_batteri)
        self.rod.after(1000, self.tjek_opdatering)
        self.rod.after(2000, self.tjek_vinduer)

    # --- start og beskeder --------------------------------------------------------------
    def admin_login(self):
        if self.pause_overlay.paused:
            return
        # This changes only the active console. Projects and the X session stay alive.
        def open_login():
            try:
                subprocess.run(['sudo', '-n', '/usr/local/sbin/elevhub-admin-login'],
                               check=True, capture_output=True, timeout=25)
            except (OSError, subprocess.SubprocessError):
                self.rod.after(0, lambda: self.besked('Admin-login kunne ikke åbnes.'))
        threading.Thread(target=open_login, daemon=True).start()

    def luk_internetvinduer(self):
        """Luk almindelige browservinduer pænt (Ctrl+Shift+W). Rør aldrig app-vinduet eller en
        gem-/åbn-dialog: kun vinduer med rollen "browser" er internetvinduer."""
        lukkede = 0
        for vindue in xdo('search', '--onlyvisible', '--classname', ALMINDELIGT_VINDUE):
            rolle = subprocess.run(['xprop', '-id', vindue, 'WM_WINDOW_ROLE'], capture_output=True, text=True).stdout
            if '"browser"' not in rolle:
                continue
            xdo('windowactivate', '--sync', vindue, 'key', '--clearmodifiers', 'ctrl+shift+w')
            lukkede += 1
        return lukkede

    def til_start(self):
        self.afslut_foer(lambda: threading.Thread(target=self._til_start, daemon=True).start())

    def aabn_native_projekter(self):
        """Open the native library in the same protected kiosk container."""
        launcher = getattr(self, 'workshop_launcher', None)
        if launcher is None:
            self.besked('Projekthylden er ikke klar endnu. Prøv igen om lidt.')
            return
        def navigate():
            def return_to_start():
                try:
                    self._til_start()
                    with launcher.state_lock:
                        launcher.navigation_target = {'page': 'cadoodle-projects', 'created': time.time()}
                except Exception:
                    self.rod.after(0, lambda: self.besked('Projekthylden kunne ikke åbnes. Prøv igen.'))
            threading.Thread(target=return_to_start, daemon=True, name='native-project-library').start()
        self.afslut_foer(navigate, reason='switch')

    def afslut_foer(self, action, reason='home', completed=None):
        """Serialize exits; Tk only changes windows after every editor agrees."""
        if self.pause_overlay.paused or getattr(self, 'navigation_busy', False):
            if completed:
                completed({'state': 'cancelled'})
            return False
        self.navigation_busy = True
        self.start.configure(state='disabled')

        def deliver(result):
            try:
                if result.get('state') == 'completed' and not self.pause_overlay.paused:
                    if self.cadoodle:
                        self.cadoodle.skjul()
                    action()
                elif result.get('state') == 'completed':
                    result = {'state': 'cancelled'}
                elif result.get('state') == 'error':
                    self.besked(result.get('message') or 'Værkstedet kunne ikke afsluttes. Dit arbejde er stadig åbent.')
            except Exception:
                result = {'state': 'error', 'message': 'Det næste værksted kunne ikke åbnes. Prøv igen.'}
                self.besked(result['message'])
            finally:
                self.navigation_busy = False
                self.start.configure(state='normal')
                if completed:
                    completed(result)

        def finish():
            from workshop_launcher import finish_web_sessions
            try:
                result = self.cadoodle.afslut_session() if self.cadoodle else {'state': 'completed'}
                if result.get('state') == 'completed':
                    windows = xdo('search', '--onlyvisible', '--classname', APP_VINDUE)
                    # A genuine start page has no active web editor. It must
                    # remain usable when the local project service is down.
                    at_home = bool(windows) and all(self.paa_start(wid) for wid in windows)
                    if not at_home:
                        result = finish_web_sessions(reason)
                self.rod.after(0, lambda: deliver(result))
            except Exception:
                self.rod.after(0, lambda: deliver({'state': 'error',
                    'message': 'Værkstedet kunne ikke afsluttes. Dit arbejde er stadig åbent. Prøv igen.'}))
        threading.Thread(target=finish, daemon=True, name='workshop-finish').start()
        return True

    def _til_start(self):
        """Luk internetvinduer, og gå tilbage i app-vinduet, til startsiden vises.
        Chromium slår "Hjem" (Alt+Home) fra i app-vinduer, men "Tilbage" (Alt+venstre) virker.
        Kommer vi ikke frem, lukkes app-vinduet; kiosksessionen starter det igen på startsiden."""
        with self.laas:
            self.luk_internetvinduer()
            for vindue in xdo('search', '--onlyvisible', '--classname', APP_VINDUE):
                # Every project has acknowledged finish before this method is
                # called. Never press Escape: it can dismiss a save dialog.
                xdo('windowactivate', '--sync', vindue)
                for _ in range(15):
                    if self.paa_start(vindue):
                        break
                    xdo('windowactivate', '--sync', vindue, 'key', '--clearmodifiers', 'alt+Left')
                    for _ in range(8):                      # vent op til 2 s på, at siden skifter
                        time.sleep(0.25)
                        if self.paa_start(vindue):
                            break
                else:
                    # Only this deliberate toolbar recovery may close the protected container.
                    subprocess.run(['xprop', '-id', vindue, '-f', '_ELEVHUB_ALLOW_CLOSE_ONCE', '32c',
                                    '-set', '_ELEVHUB_ALLOW_CLOSE_ONCE', str(int(time.time()) + 5)],
                                   capture_output=True, timeout=5, check=True)
                    xdo('windowactivate', '--sync', vindue, 'key', '--clearmodifiers', 'ctrl+w')

    @staticmethod
    def paa_start(vindue):
        navn = subprocess.run(['xdotool', 'getwindowname', vindue], capture_output=True, text=True).stdout
        return navn.startswith(START_TITEL)

    def besked(self, tekst, farve=None, sekunder=10):
        if farve is None:
            farve = BRAND
        self.tekst.configure(text=tekst, fg=farve)
        if self.besked_timer:
            self.rod.after_cancel(self.besked_timer)
        self.besked_timer = self.rod.after(sekunder * 1000, lambda: self.tekst.configure(
            text=self.konf['computer_visning'], fg=MUTED))

    def tjek_opdatering(self):
        """Show only a current, local, root-owned update heartbeat."""
        value = read_status(STATUS_PATH, boot_id=current_boot_id())
        text = message(value)
        if text:
            self.opdateringsstatus.configure(text=text, fg=BRAND)
            if not self.opdateringsstatus.winfo_ismapped():
                self.update_band.pack(side='left', fill='y', padx=(18, 8), before=self.power.frame)
                self.opdateringsstatus.pack(fill='both', expand=True)
        elif self.opdateringsstatus.winfo_ismapped():
            self.opdateringsstatus.pack_forget()
            self.update_band.pack_forget()
        self.rod.after(1000, self.tjek_opdatering)

    # --- melding til elevhubben ---------------------------------------------------------
    def meld(self):
        threading.Thread(target=self._meld, daemon=True).start()
        self.rod.after(3000, self.meld)

    def _meld(self):
        usb = usb_noegler()
        data = {'computer': self.konf['computer'], 'kilde': 'bjaelke', 'version': anvendt_version(),
                'usb': os.path.basename(usb[0][1]) if usb else None}
        local = lokal_status(self.konf)
        pending_navigation = None
        if local is not None:
            data['local_apps'] = local['apps']
            navigation = local['navigation']
            if (navigation is not None
                    and navigation['version'] != getattr(self, 'local_navigation_version', None)):
                data['visning'] = navigation['id']
                pending_navigation = navigation['version']
        elif self.konf.get('lokale_app_urler'):
            # A missing local service is a readiness failure, not a reason to
            # leave the teacher-facing app cards falsely marked ready.
            data['local_apps'] = []
        overlay = getattr(self, 'pause_overlay', None)
        ack = overlay.acknowledgement() if overlay is not None else None
        if ack is not None:
            # Acknowledgement is sent separately from the desired pause state:
            # the server must never treat a requested overlay as visible before
            # Tk has made it visible on this machine.
            data['pause_ack'] = ack
        try:
            svar = hent_json(self.konf['hub'], '/api/status', token=self.konf['ui_token'],
                             computer=self.konf['computer'], ca_fil=self.konf['ca_fil'],
                             usikker_test=self.konf.get('usikker_test', False),
                             data=data, timeout=4)
        except Exception as fejl:
            # Transport loss is deliberately permissive: the local workshop
            # catalogue and local apps remain usable while the hub is down.
            # HTTP/auth/TLS failures are reachable-server failures and must
            # retain the closed state until a valid policy is received.
            if server_utilgaengelig(fejl):
                self.server_status = 'offline'
                self.internet_aaben = True
                self.rod.after(0, self.pause_overlay.offline)
            else:
                self.server_status = 'online'
                self.internet_aaben = False
                if getattr(self, 'cadoodle', None) is not None:
                    self.cadoodle.opdater(None)
            return
        try:
            # Reaching the hub is enough to leave offline mode; malformed or
            # policy-invalid replies must not inherit the offline allowance.
            self.server_status = 'online'
            if not isinstance(svar, dict) or not isinstance(svar.get('apps'), list):
                raise ValueError('ugyldigt UI-svar')
            internet = next((a for a in svar['apps']
                             if isinstance(a, dict) and a.get('id') == self.konf['internet_app']), None)
            self.internet_aaben = bool(internet and type(internet.get('aaben')) is bool and internet['aaben'])
        except (TypeError, ValueError, AttributeError):
            self.internet_aaben = False
            if getattr(self, 'cadoodle', None) is not None:
                self.cadoodle.opdater(None)
            return
        if pending_navigation is not None:
            # Do not acknowledge a navigation state that the hub did not
            # accept.  A service restart gets a fresh version and therefore
            # resets a stale app view back to its new health snapshot.
            self.local_navigation_version = pending_navigation
        pause = svar.get('pause')
        if overlay is not None and isinstance(pause, dict):
            self.rod.after(0, lambda p=pause: overlay.update_pause(p))
        # A closed workshop gets an attention overlay, but the browser window
        # is intentionally left alone. When it is opened again the overlay is
        # removed and the live project is still in the same editor document.
        if overlay is not None:
            lukket_app = svar.get('lukket_app')
            lukket = bool(lukket_app) or svar.get('gaa_hjem') is True
            vaerksted = lukket_app if isinstance(lukket_app, str) else 'værkstedet'
            self.rod.after(0, lambda lukket=lukket, navn=vaerksted:
                           overlay.update_workshop_closed(lukket, navn))
        if self.cadoodle:
            self.rod.after(0, lambda: self.cadoodle.opdater(svar))

    def tjek_vinduer(self):
        self.pause_overlay.check_stale()
        # Internetvinduer må kun findes, når læreren har åbnet Internet (også Ctrl+N-vinduer).
        if not self.internet_aaben and not self.pause_overlay.paused:
            threading.Thread(target=self._luk_hvis_lukket, daemon=True).start()
        self.rod.after(2000, self.tjek_vinduer)

    def _luk_hvis_lukket(self):
        if not self.laas.acquire(blocking=False):
            return
        try:
            if self.luk_internetvinduer():
                self.rod.after(0, lambda: self.besked('Internettet er lukket af læreren.'))
        finally:
            self.laas.release()

    # --- USB ----------------------------------------------------------------------------
    def tjek_usb(self):
        usb = usb_noegler()
        if usb:
            # elevhub-regler peger Chromiums downloads på nøglen, så længe den sidder i.
            navn = os.path.basename(usb[0][1])
            navn = navn if len(navn) <= 18 else navn[:17] + '…'
            self.usb_tekst.configure(text=f'Gemmer på USB\n«{navn}»')
            self.usb_knap.pack(side='right', fill='y', padx=(8, 12), pady=10)
            self.usb_tekst.pack(side='right')
        else:
            self.usb_knap.pack_forget()
            self.usb_tekst.pack_forget()
        self.rod.after(2000, self.tjek_usb)

    def tjek_batteri(self):
        status = batteri_status()
        tekst = batteri_tekst(status)
        advarsel = batteri_advarsel(status)
        if not tekst:
            self.batteri.pack_forget()
        else:
            self.batteri_status.configure(text=tekst)
            self.batteri_advarsel.configure(
                text='Kritisk · tilslut strøm nu' if advarsel and status.percent is not None and
                status.percent <= BATTERI_KRITISK else 'Lavt · tilslut strøm' if advarsel else '',
                fg=BRAND if status.percent is not None and status.percent <= BATTERI_KRITISK
                else '#8a5700')
            if advarsel:
                self.batteri_advarsel.pack(fill='x')
            else:
                self.batteri_advarsel.pack_forget()
            if not self.batteri.winfo_ismapped():
                self.batteri.pack(side='right', padx=(8, 10))
        self.rod.after(10000, self.tjek_batteri)

    def skub_ud(self):
        if self.pause_overlay.paused:
            return
        for enhed, _ in usb_noegler():
            forael = subprocess.run(['lsblk', '-no', 'pkname', enhed], capture_output=True, text=True).stdout.strip()
            ud = subprocess.run(['udisksctl', 'unmount', '-b', enhed], capture_output=True, text=True)
            if ud.returncode != 0:
                self.besked('USB-nøglen er i brug. Luk den fil, du gemmer, og prøv igen.')
                return
            if forael:
                subprocess.run(['udisksctl', 'power-off', '-b', f'/dev/{forael}'], capture_output=True)
        self.besked('Nu kan du tage USB-nøglen ud.', farve='#3d694c')

    def koer(self):
        self.rod.mainloop()


if __name__ == '__main__':
    with open('/etc/elevhub/kiosk.json', encoding='utf-8') as f:
        KONF = json.load(f)
    apply_theme(KONF.get('theme'))
    Bjaelke(KONF).koer()
