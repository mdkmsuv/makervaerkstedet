"""Lokal 3D-indgang til CaDoodle og den eksisterende elev-printkø.

Indlæses kun af elevbjælken, når CaDoodle-launcheren er installeret.
Ingen netværkslytter eller shell-kommandoer fra browseren.
"""
import os
import json
import re
import subprocess
import time
import queue
import threading
import uuid
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

LAUNCHER = '/usr/local/bin/elevhub-cadoodle'
DEFAULT_SCHOOL_NAME = 'Makerværkstedet'
MAX_NATIVE_SCHOOL_NAME_LENGTH = 80
CREDIT_TEXT = 'Kreditering: Klostermarksskolen.'


def native_school_name(konf):
    """Return a bounded, single-line school name for native Tk surfaces.

    The school profile is administrator input. Strip Unicode control/format
    characters as well as line breaks so a malformed profile cannot alter the
    native window layout or inject a second visual line. The fixed credit is
    rendered separately and is never derived from this value.
    """
    raw = konf.get('school_name') if isinstance(konf, dict) else None
    if not isinstance(raw, str):
        return DEFAULT_SCHOOL_NAME
    cleaned_chars = []
    for char in raw:
        if unicodedata.category(char).startswith('C'):
            if char.isspace():
                cleaned_chars.append(' ')
            continue
        cleaned_chars.append(char)
    cleaned = ''.join(cleaned_chars)
    cleaned = ' '.join(cleaned.split()).strip()
    cleaned = cleaned[:MAX_NATIVE_SCHOOL_NAME_LENGTH].rstrip()
    return cleaned or DEFAULT_SCHOOL_NAME


def prewarm_root():
    return Path.home() / '.local/state/elevhub/prewarm'


def ready_prewarm(token):
    """Only reuse a nonce belonging to a live CaDoodle of this pupil."""
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        return False
    try:
        marker = prewarm_root() / (token + '.ready')
        if marker.stat().st_size > 4096:
            return False
        data = json.loads(marker.read_text(encoding='utf-8'))
        pid = data.get('pid')
        if data.get('nonce') != token or type(pid) is not int or pid < 1:
            return False
        proc = Path('/proc') / str(pid)
        if proc.stat().st_uid != os.getuid():
            return False
        args = proc.joinpath('cmdline').read_bytes().split(b'\0')
        return any(arg.endswith((b'CaDoodle-Application.jar', b'CaDoodle-Kloster.jar')) for arg in args)
    except (OSError, ValueError, TypeError):
        return False


def existing_prewarm():
    for path in prewarm_root().glob('*.ready'):
        if ready_prewarm(path.stem):
            return path.stem
    return None


def request_prewarm(token, show):
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        return
    request = prewarm_root() / (token + '.show')
    try:
        if show:
            request.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            request.write_text(token, encoding='ascii')
        else:
            request.unlink(missing_ok=True)
    except OSError:
        pass  # The ordinary foreground start remains available.


def finish_native_session(root=None, timeout=120):
    """Ask Java to finish, including hidden editors, without closing its process.

    A pending acknowledgement means the pupil is choosing or saving. There
    is deliberately no timer on that choice. A missing acknowledgement is
    an error, never permission to hide or discard a model.
    """
    root = Path(root) if root is not None else Path.home() / 'Projekter/.elevhub/finish-requests'
    request_id = uuid.uuid4().hex
    request = root / (request_id + '.ready')
    status = root / (request_id + '.status.json')
    temporary = root / (request_id + '.tmp')
    acknowledged = False
    next_process_check = 0
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary.write_text(json.dumps({'id': request_id, 'created': time.time(), 'action': 'finish'}), encoding='utf-8')
        os.replace(temporary, request)
        deadline = time.monotonic() + timeout
        while True:
            if status.is_file():
                if status.stat().st_size > 4096:
                    raise ValueError('Ugyldigt svar fra CaDoodle.')
                result = json.loads(status.read_text(encoding='utf-8'))
                if result.get('id') != request_id:
                    raise ValueError('Ugyldigt svar fra CaDoodle.')
                if result.get('state') in ('completed', 'cancelled', 'error'):
                    return result
                if result.get('state') != 'pending':
                    raise ValueError('Ugyldigt svar fra CaDoodle.')
                acknowledged = True
            if not acknowledged and time.monotonic() >= deadline:
                raise TimeoutError('CaDoodle svarede ikke.')
            if acknowledged and time.monotonic() >= next_process_check:
                if not CadStart.running():
                    raise OSError('CaDoodle lukkede før afslutningen blev bekræftet.')
                next_process_check = time.monotonic() + 2
            time.sleep(0.25)
    except (OSError, ValueError, TypeError):
        return {'state': 'error', 'message': 'CaDoodle svarer ikke. Dit projekt er stadig åbent. Prøv igen.'}
    finally:
        # Only this request's IPC files, never a saved project or model.
        for path in (temporary, request, status):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def show_native_entry():
    """Notify Java only for a pupil's foreground opening, never prewarming."""
    root = Path.home() / 'Projekter/.elevhub/finish-requests'
    request_id = uuid.uuid4().hex
    temporary = root / (request_id + '.tmp')
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary.write_text(json.dumps({'id': request_id, 'created': time.time(), 'action': 'show'}), encoding='utf-8')
        os.replace(temporary, root / (request_id + '.ready'))
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class CadStart:
    """Launch and observe on a worker; only the toolbar thread touches Tk.

    A process or splash is not evidence that the editor window is available.
    Waiting never kills a model or launches a second copy on a timer.
    """
    def __init__(self, find_windows, process=None, *, background=False, token=None):
        self.find_windows = find_windows
        self.process = process
        self.events = queue.Queue()
        self.started = time.monotonic()
        self.background = background
        self.token = token or (uuid.uuid4().hex if background else None)
        self.allow_show = not background
        self.request_lock = threading.Lock()

    def cancel_show(self):
        with self.request_lock:
            self.allow_show = False
            request_prewarm(self.token, False)

    def resume_show(self):
        with self.request_lock:
            self.allow_show = True

    def show_warm(self):
        with self.request_lock:
            if self.allow_show:
                request_prewarm(self.token, True)

    @staticmethod
    def running():
        try:
            result = subprocess.run(
                ['pgrep', '-u', str(os.getuid()), '-f',
                 r'CaDoodle-(Application|Kloster)\.jar'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            return result.returncode != 1  # An unavailable check is not a crash.
        except (OSError, subprocess.TimeoutExpired):
            return True

    def run(self):
        try:
            warm = self.token is not None and ready_prewarm(self.token)
            windows = [] if warm else self.find_windows()
            if windows:
                self.events.put(('existing' if self.background else 'ready', windows))
                return
            if warm and not self.background:
                self.show_warm()
            if not warm and (self.process is None or self.process.poll() is not None):
                logs = Path.home() / '.local/state/elevhub'
                logs.mkdir(parents=True, exist_ok=True)
                with (logs / 'cadoodle.log').open('w') as log:
                    options = {}
                    if self.background:
                        options['env'] = dict(os.environ, ELEVHUB_CADOODLE_PREWARM=self.token)
                    self.process = subprocess.Popen(
                        [LAUNCHER], stdout=log, stderr=log, start_new_session=True, **options)
            while True:
                token = self.token if ready_prewarm(self.token) else existing_prewarm()
                if token:
                    self.token = token
                    if self.background:
                        self.events.put(('warm', token))
                        return
                    self.show_warm()
                # During prewarming the primary stage briefly obtains a native
                # peer before hiding. Only the model-ready marker finishes this
                # job; an early window is not a ready editor.
                windows = [] if self.background else self.find_windows()
                if windows:
                    self.events.put(('ready', windows))
                    return
                if (self.process is None or self.process.poll() is not None) and not self.running():
                    self.events.put(('error', 'CaDoodle kunne ikke åbne. Prøv igen, eller spørg din lærer.'))
                    return
                time.sleep(0.75)
        except Exception:
            self.events.put(('error', 'CaDoodle kunne ikke åbne. Prøv igen, eller spørg din lærer.'))


class Startvisning:
    """Small non-modal feedback, dismissed when the real editor appears."""
    def __init__(self, parent, back, retry, *, school_name=DEFAULT_SCHOOL_NAME):
        import tkinter as tk
        from tkinter import ttk
        self.school_name = native_school_name({'school_name': school_name})
        self.window = tk.Toplevel(parent)
        self.window.title(f'Åbner 3D-værkstedet · {self.school_name}')
        self.window.configure(bg='white')
        self.window.protocol('WM_DELETE_WINDOW', back)
        self.window.geometry('560x410')
        self.window.minsize(480, 390)
        body = tk.Frame(self.window, bg='white', padx=28, pady=24)
        body.pack(fill='both', expand=True)
        tk.Label(body, text=f'{self.school_name.upper()[:MAX_NATIVE_SCHOOL_NAME_LENGTH]}  /  3D-VÆRKSTED',
                 font=('Metrophobic', 11), fg='#b4121a', bg='white', anchor='w').pack(fill='x')
        self.heading = tk.Label(body, text='Vi åbner CaDoodle', font=('Metrophobic', 24),
                                fg='#333333', bg='white', anchor='w')
        self.heading.pack(fill='x', pady=(18, 8))
        self.message = tk.Label(body, text='Gør klar til at bygge din model …',
                                font=('Metrophobic', 14), fg='#444444', bg='white',
                                justify='left', anchor='w', wraplength=490)
        self.message.pack(fill='x', pady=(0, 16))
        self.progress = ttk.Progressbar(body, mode='indeterminate')
        self.progress.pack(fill='x')
        self.progress.start(45)
        self.tip = tk.Label(body, text='En idé imens: Hvad vil du bygge med en kasse og en kugle?',
                 font=('Metrophobic', 13), fg='#555555', bg='white',
                 anchor='w', justify='left', wraplength=490)
        self.tip.pack(fill='x', pady=(18, 14))
        self.credit = tk.Label(body, text=CREDIT_TEXT, font=('Metrophobic', 11),
                               fg='#666666', bg='white', anchor='w')
        self.credit.pack(fill='x', pady=(0, 10))
        actions = tk.Frame(body, bg='white')
        actions.pack(fill='x', side='bottom')
        tk.Button(actions, text='Tilbage', command=back, font=('Metrophobic', 14),
                  bg='white', fg='#444444', padx=16, pady=8).pack(side='left')
        self.retry = tk.Button(actions, text='Prøv igen', command=retry,
                               font=('Metrophobic', 14), bg='#b4121a', fg='white',
                               activebackground='#920008', activeforeground='white',
                               relief='flat', padx=16, pady=8)
        def reflow(event):
            for label in (self.message, self.tip):
                label.configure(wraplength=max(260, event.width - 56))
        body.bind('<Configure>', reflow)

    def show(self):
        self.window.deiconify()
        self.window.lift()

    def hide(self):
        self.window.withdraw()

    def waiting(self, seconds):
        if seconds >= 90:
            text = 'Det tager usædvanligt lang tid. Du kan gå tilbage og bede din lærer om hjælp.'
        elif seconds >= 20:
            text = 'CaDoodle er stadig ved at åbne. Du behøver ikke trykke igen.'
        else:
            text = 'Gør klar til at bygge din model …'
        self.message.configure(text=text)

    def error(self, message):
        self.progress.stop()
        self.heading.configure(text='CaDoodle åbnede ikke')
        self.message.configure(text=message)
        self.retry.pack(side='right')

    def close(self):
        self.progress.stop()
        self.window.destroy()


def print_adresse(konf, app):
    hub = urlsplit(konf['hub'])
    port = app.get('port')
    if hub.scheme not in ('http', 'https') or not hub.hostname or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Ugyldig adresse til printkøen')
    host = '[' + hub.hostname + ']' if ':' in hub.hostname else hub.hostname
    return urlunsplit((hub.scheme, f'{host}:{port}', '/', '', ''))


class Vaerksted:
    def __init__(self, bjaelke, ramme):
        import tkinter as tk
        self.tk, self.bjaelke = tk, bjaelke
        self.school_name = native_school_name(getattr(bjaelke, 'konf', None))
        self.app, self.sidst, self.vindue = None, 0, None
        self.proces = None
        self.start_job, self.startvisning = None, None
        self.start_i_forgrund = False
        self.prewarm_token = None
        self.prewarm_requested = False
        self.prewarm_attempted = False
        self.skjulte = set()
        self.paa_startside = False
        self.entry_request = show_native_entry
        self.skip_entry = False
        self.knap = tk.Button(ramme, text='3D-værksted', command=self.vis,
                              font=('Metrophobic', 14), bg='white', fg='#b4121a',
                              relief='solid', bd=1, padx=16, cursor='hand2')
        self.tjek()

    def opdater(self, svar):
        if svar is None:
            self.app = None
            self.sidst = 0
            self.prewarm_requested = False
            return
        apps = svar.get('apps', []) if isinstance(svar, dict) else []
        self.app = next((a for a in apps if isinstance(a, dict) and a.get('id') == '3dprint'), None)
        self.sidst = time.monotonic()
        teacher_open = (isinstance(self.app, dict) and self.app.get('aaben') is True
                        and self.app.get('klar') is not False)
        if teacher_open:
            self.prewarm_requested = True
        else:
            self.prewarm_requested = False
            self.prewarm_attempted = False

    def aaben(self):
        # With no reachable hub, all local workshops are available.  Once a
        # valid/failed hub response exists, honour its explicit app policy.
        if getattr(self.bjaelke, 'server_status', 'online') == 'offline':
            return True
        return (time.monotonic() - self.sidst < 15 and isinstance(self.app, dict)
                and self.app.get('aaben') is True and self.app.get('klar') is not False)

    def skaermpause(self):
        overlay = getattr(self.bjaelke, 'pause_overlay', None)
        return bool(overlay and overlay.paused)

    @staticmethod
    def xdo(*args):
        try:
            # Runs on the toolbar's Tk thread: a stuck X call must not freeze
            # the pupil's toolbar for long.
            return subprocess.run(['xdotool', *args], capture_output=True, text=True, timeout=1).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            return []

    def cad_vinduer(self, synlige=False):
        # JavaFX main window and its dialogs share this class. The updater's
        # 10x10 helper has a different class and must not be mapped.
        args = ['search'] + (['--onlyvisible'] if synlige else [])
        return self.xdo(*args, '--class', r'^com\.commonwealthrobotics\.Main$')

    def cad_hovedvinduer(self):
        # The main editor title is set immediately before Stage.show().
        # Do not mistake the updater or an early splash for the editor.
        return [wid for wid in self.cad_vinduer()
                if re.match(r'^cadoodle\s+(?:version\s+|v\s*)[0-9]',
                            ' '.join(self.xdo('getwindowname', wid)), re.IGNORECASE)]

    def skjul(self):
        self.skjul_start()
        if self.vindue is not None:
            self.vindue.withdraw()
        for wid in self.cad_vinduer(synlige=True):
            self.skjulte.add(wid)
            self.xdo('windowunmap', wid)

    def afslut_session(self):
        """Worker entry: restore hidden editors so their finish choice is visible."""
        windows = self.cad_vinduer()
        if not windows and not CadStart.running():
            return {'state': 'completed'}
        for wid in windows:
            self.xdo('windowmap', wid, 'windowactivate', wid)
        return finish_native_session()

    def paa_start(self):
        """Genvejen hører kun til startsiden, også med native vinduer åbne.

        Et klik på selve bjælken flytter fokus til Tk. I det tilfælde beholdes
        den sidst observerede visning; bjælken er ikke i sig selv startsiden.
        """
        aktive = self.xdo('getactivewindow')
        if not aktive:
            return False
        navn = ' '.join(self.xdo('getwindowname', aktive[0]))
        if navn == self.bjaelke.rod.title():
            return self.paa_startside
        # The 3D inspiration page explicitly points pupils to this launcher.
        # Keep normal home detection unchanged so "Til start" still goes home.
        return self.bjaelke.paa_start(aktive[0]) or navn.startswith('3D-print · Værkstedet')

    def vis_genvej(self, vis):
        self.paa_startside = vis
        if vis and not self.knap.winfo_manager():
            self.knap.pack(side='left', padx=(0, 16), pady=8, fill='y',
                           before=self.bjaelke.tekst)
        elif not vis and self.knap.winfo_manager():
            self.knap.pack_forget()

    def tjek(self):
        aaben = self.aaben()
        self.vis_genvej(aaben and not self.skaermpause() and self.paa_start())
        self.knap.configure(state='normal' if aaben else 'disabled')
        if not aaben and not getattr(self.bjaelke, 'navigation_busy', False):
            self.skjul()
        self.forstart_cad()
        self.aabn_gemt_projekt()
        self.aabn_projekthylde()
        # Two xdotool processes per check; 1 s is responsive enough and halves
        # the load on old PCs.
        self.bjaelke.rod.after(1000, self.tjek)

    def aabn_projekthylde(self):
        """The native blank entry screen can open its scoped local library."""
        if self.skaermpause():
            return
        marker = Path.home() / 'Projekter/.elevhub/cadoodle-projects.ready'
        try:
            if not marker.is_file():
                return
            if marker.stat().st_size > 256:
                marker.unlink()
                return
            data = json.loads(marker.read_text(encoding='utf-8'))
            marker.unlink()
            if not self.aaben() or not 0 <= time.time() - data['created'] < 120:
                return
            # Reuse the protected kiosk container. A normal browser window
            # would be closed by the independent Internet teacher policy.
            self.bjaelke.aabn_native_projekter()
        except (OSError, ValueError, TypeError, KeyError):
            return

    def aabn_gemt_projekt(self):
        """Approve only bounded local archive requests while 3D is open."""
        if self.skaermpause():
            return
        root = Path.home() / 'Projekter/.elevhub/open-requests'
        if not root.is_dir():
            return
        for path in root.glob('*.json'):
            if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
                continue
            try:
                if path.stat().st_size > 4096:
                    continue
                request = json.loads(path.read_text(encoding='utf-8'))
                if request.get('id') != path.stem:
                    continue
                error = None
                if not 0 <= time.time() - request['created'] < 120:
                    error = 'Åbningen udløb. Gå tilbage til Dine projekter, og prøv igen.'
                elif not self.aaben():
                    error = '3D-værkstedet er lukket lige nu. Spørg din lærer.'
                if error:
                    path.with_suffix('.status.json').write_text(json.dumps({'state':'error','message':error}), encoding='utf-8')
                    path.unlink()
                    # The raw project copy is only a transfer file; the pupil's
                    # original stays in Projekter. Do not let copies pile up.
                    for suffix in ('.stl', '.klosterskitse', '.zip'):
                        path.with_suffix(suffix).unlink(missing_ok=True)
                    continue
                os.replace(path, path.with_suffix('.ready'))
                # This explicit project choice already supplies the entry
                # action. Do not race its importer with a second start dialog.
                self.skip_entry = True
                self.start_cad()
            except (OSError, ValueError, TypeError, KeyError):
                continue

    def vis(self):
        # Kontrollér igen ved klik, så man heller ikke kan skifte værksted
        # i det korte interval før bjælkens næste opdatering.
        if not self.aaben() or self.skaermpause() or not self.paa_start():
            return
        self.vis_genvej(False)
        tk = self.tk
        if self.vindue is not None:
            self.vindue.deiconify()
            self.vindue.lift()
            return
        school_name = native_school_name(
            {'school_name': getattr(self, 'school_name', DEFAULT_SCHOOL_NAME)})
        win = self.vindue = tk.Toplevel(self.bjaelke.rod)
        win.title(f'3D-værksted · {school_name}')
        win.configure(bg='#f4f3f0')
        win.protocol('WM_DELETE_WINDOW', win.withdraw)
        body = tk.Frame(win, bg='#f4f3f0', padx=30, pady=16)
        body.pack(fill='both', expand=True)
        def label(text, size, color='#333333', **kw):
            widget = tk.Label(body, text=text, font=('Metrophobic', size), fg=color,
                              bg='#f4f3f0', justify='left', anchor='w', **kw)
            widget.pack(fill='x', pady=(0, 7))
            return widget
        label(f'{school_name.upper()[:MAX_NATIVE_SCHOOL_NAME_LENGTH]}  /  DESIGN OG FREMSTILLING', 12, '#b4121a')
        label(CREDIT_TEXT, 11, '#666666')
        # The same maker image as the hub, converted to PNG for Tk.
        hero = tk.Frame(body, bg='#f4f3f0')
        hero.pack(fill='x', pady=(0, 10))
        try:
            self.hero_image = tk.PhotoImage(file='/usr/local/share/elevhub/assets/workshop-3d-card.png')
            tk.Label(hero, image=self.hero_image, bd=0).pack(side='right', padx=(22, 0))
        except tk.TclError:
            pass
        tk.Label(hero, text='Byg en\n3D-model', font=('Metrophobic', 26),
                 fg='#333333', bg='#f4f3f0', justify='left', anchor='w').pack(fill='x', pady=(6, 5))
        tk.Label(hero, text='Tegn en figur, en holder eller en modelbil.',
                 font=('Metrophobic', 14), fg='#555555', bg='#f4f3f0', justify='left', anchor='w').pack(fill='x')
        for title, text, action, button in [
            ('1  Tegn i CaDoodle', 'Brug kasser, kugler eller dine egne former.\nGem dit projekt, når du vil beholde det.', self.start_cad, 'Åbn CaDoodle'),
            ('2  Send til print', 'Vælg Send til ledig printer i CaDoodle.', None, None),
            ('3  Følg printet', 'Se printkøen, eller send en STL-fil til print.', self.print, 'Åbn Printværksted')]:
            card = tk.Frame(body, bg='white', padx=18, pady=8, highlightbackground='#e1e1e1', highlightthickness=1)
            card.pack(fill='x', pady=(0, 8))
            tk.Label(card, text=title, font=('Metrophobic', 16), fg='#333333', bg='white', anchor='w').pack(fill='x')
            tk.Label(card, text=text, font=('Metrophobic', 14), fg='#555555', bg='white', justify='left', anchor='w').pack(side='left', pady=(5, 0))
            if action:
                tk.Button(card, text=button, command=action, font=('Metrophobic', 15), bg='#b4121a', fg='white',
                          activebackground='#920008', activeforeground='white', relief='flat', padx=16, pady=8,
                          cursor='hand2').pack(side='right', padx=(18, 0))
        challenge = tk.Label(body, text='IDÉ  ·  Lav en robot som brik til et brætspil.',
                             font=('Metrophobic', 14), fg='#282925', bg='#ffc745', padx=18, pady=10, anchor='w')
        challenge.pack(fill='x', pady=(2, 8))
        label('Tegn i millimeter. Din model må højst være 175 × 175 × 175 mm.', 13)
        label('Dine CaDoodle-projekter: Projekter/3D-print/CaDoodle\nBrug “Til start” øverst. Du vælger, om dit arbejde skal gemmes, før du afslutter.', 12, '#666666')

    def forstart_cad(self):
        """One quiet start per teacher opening; offline access still works normally."""
        if (not getattr(self, 'prewarm_requested', False)
                or getattr(self, 'prewarm_attempted', False)
                or not self.aaben() or self.skaermpause()
                or getattr(self.bjaelke, 'server_status', 'offline') != 'online'):
            return
        if self.start_job is not None or self.start_i_forgrund:
            return
        self.prewarm_attempted = True
        token = existing_prewarm()
        if token:
            self.prewarm_token = token
            return
        self.start_job = CadStart(self.cad_hovedvinduer, self.proces, background=True)
        threading.Thread(target=self.start_job.run, daemon=True, name='cadoodle-prewarm').start()
        self.bjaelke.rod.after(100, self.foelg_start)

    def start_cad(self):
        if self.skaermpause():
            return
        if not self.aaben():
            return
        self.start_i_forgrund = True
        if self.start_job is not None:
            if isinstance(self.start_job, CadStart):
                self.start_job.resume_show()
            if self.startvisning is None:
                self.startvisning = Startvisning(
                    self.bjaelke.rod, self.skjul_start, self.start_cad,
                    school_name=getattr(self, 'school_name', DEFAULT_SCHOOL_NAME))
            else:
                self.startvisning.show()
            return
        if self.startvisning is not None:
            self.startvisning.close()
        self.startvisning = Startvisning(
            self.bjaelke.rod, self.skjul_start, self.start_cad,
            school_name=getattr(self, 'school_name', DEFAULT_SCHOOL_NAME))
        if self.vindue is not None:
            self.vindue.withdraw()
        token = getattr(self, 'prewarm_token', None) or existing_prewarm()
        self.start_job = CadStart(self.cad_hovedvinduer, self.proces, token=token)
        threading.Thread(target=self.start_job.run, daemon=True, name='cadoodle-start').start()
        self.bjaelke.rod.after(100, self.foelg_start)

    def skjul_start(self):
        self.start_i_forgrund = False
        token = getattr(self, 'prewarm_token', None)
        if token:
            request_prewarm(token, False)
        job = getattr(self, 'start_job', None)
        if isinstance(job, CadStart):
            job.cancel_show()
        if getattr(self, 'startvisning', None) is not None:
            self.startvisning.hide()

    def foelg_start(self):
        job = self.start_job
        if job is None:
            return
        # Teacher policy and navigation can change while the worker starts Java.
        if not self.aaben() or self.skaermpause():
            self.skjul_start()
        try:
            state, value = job.events.get_nowait()
        except queue.Empty:
            if self.startvisning is not None:
                self.startvisning.waiting(time.monotonic() - job.started)
            self.bjaelke.rod.after(150, self.foelg_start)
            return
        self.proces = job.process
        self.start_job = None
        if state == 'warm':
            self.prewarm_token = value
            if self.start_i_forgrund and self.aaben() and not self.skaermpause():
                self.start_cad()
            return
        if state == 'existing':
            # A teacher opening the workshop must not hide a pupil's existing window.
            if self.start_i_forgrund and self.aaben() and not self.skaermpause():
                self.start_cad()
            return
        if state == 'error':
            if self.startvisning is not None:
                self.startvisning.error(value)
            return
        if self.startvisning is not None:
            self.startvisning.close()
        self.startvisning = None
        self.prewarm_token = None
        foreground = self.start_i_forgrund and self.aaben() and not self.skaermpause()
        # Restore dialogs too when returning to an existing project.
        windows = self.cad_vinduer()
        for wid in dict.fromkeys([*value, *windows]):
            if foreground:
                self.xdo('windowmap', wid, 'windowactivate', wid)
                self.skjulte.discard(wid)
            else:
                self.skjulte.add(wid)
                self.xdo('windowunmap', wid)
        if foreground and not getattr(self, 'skip_entry', False):
            self.entry_request()
        if foreground:
            self.skip_entry = False

    def print(self):
        if not self.aaben() or self.skaermpause():
            return
        url = print_adresse(self.bjaelke.konf, self.app)
        def open_print():
            subprocess.Popen(['chromium', '--app=' + url, '--start-maximized'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.bjaelke.afslut_foer(open_print, reason='switch')


def tilfoej(bjaelke, ramme):
    return Vaerksted(bjaelke, ramme) if os.access(LAUNCHER, os.X_OK) else None
