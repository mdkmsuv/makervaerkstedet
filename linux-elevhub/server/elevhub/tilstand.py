"""Elevhubbens tilstand: åbne apps, sete elevcomputere og lærerens kode.

Alt gemmes som små JSON-filer i datamappen og skrives atomisk.
Lærerkoden gemmes kun som PBKDF2-SHA256-hash med salt.
"""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from pathlib import Path

ONLINE_SEKUNDER = 15
COMPUTERNAVN = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
SOFTWARE_VERSION = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
SSH_VAERTSNOEGLE = re.compile(r'ssh-ed25519 [A-Za-z0-9+/]{68}')
ITERATIONER = 600_000
SESSION_SEKUNDER = 8 * 3600
VAERKSTED_LUKNING_SEKUNDER = 2 * 3600
IKKE_ANGIVET = object()
# Keep failed-login bookkeeping bounded even when a hostile network presents
# many source addresses.  A source is still limited to ten attempts/minute;
# the two caps below bound both dictionary keys and total retained timestamps.
MAKS_FORSOEG_KILDER = 256
MAKS_FORSOEG_I_ALT = 4096


def skriv_json(sti, data):
    midlertidig = sti.with_suffix(sti.suffix + '.ny')
    midlertidig.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(midlertidig, sti)


def laes_json(sti, standard):
    try:
        return json.loads(sti.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return standard


class Tilstand:
    """Hvilke apps er åbne, og hvad elevcomputerne sidst har meldt."""

    def __init__(self, mappe, apps, ur=time.time):
        self.mappe = Path(mappe)
        self.mappe.mkdir(parents=True, exist_ok=True)
        self.apps = apps                      # liste af app-konfigurationer i visningsorden
        self.vaerksteder = {a['id'] for a in apps
                           if a.get('type', 'vaerksted') != 'browser'
                           and a.get('luk_ved_genstart') is not True}
        self.ur = ur
        self.laas = threading.RLock()
        gemt = laes_json(self.mappe / 'tilstand.json', {})
        tilstand_fejl = not isinstance(gemt, dict)
        if not isinstance(gemt, dict):
            gemt = {}
        gemte_aabne = gemt.get('aabne', {})
        aabne_fejl = tilstand_fejl or not isinstance(gemte_aabne, dict)
        if not isinstance(gemte_aabne, dict):
            gemte_aabne = {}
        gemt_version = gemt.get('version', 1)
        version = gemt_version if type(gemt_version) is int and gemt_version > 0 else 1

        # Gemte lærer-valg vinder over standarden. Manglende valg bruger
        # standard_aaben; ugyldige værdier starter lukket, så "false" aldrig
        # bliver sand via bool().
        aabne = {}
        opstart_aendrede = aabne_fejl
        for app in apps:
            app_id = app['id']
            gemt_aaben = gemte_aabne.get(app_id, IKKE_ANGIVET)
            if aabne_fejl:
                aaben = False
            elif app.get('luk_ved_genstart') is True:
                aaben = False
            elif gemt_aaben is IKKE_ANGIVET:
                standard = app.get('standard_aaben')
                aaben = standard if type(standard) is bool else False
            elif type(gemt_aaben) is bool:
                aaben = gemt_aaben
            else:
                aaben = False
            aabne[app_id] = aaben

            # En manglende værdi med lukket standard er uændret implicit. Alle
            # andre afvigelser kan ellers genåbne eller genbruge gammel politik.
            if gemt_aaben is IKKE_ANGIVET:
                opstart_aendrede |= aaben
            else:
                opstart_aendrede |= type(gemt_aaben) is not bool or gemt_aaben != aaben

        # Frister gemmes som klokkeslæt, så en servergenstart ikke giver to nye
        # timer. Gamle gemte lukninger uden frist får en frist én gang ved migration.
        gemte_frister = gemt.get('lukket_indtil', {})
        if not isinstance(gemte_frister, dict):
            gemte_frister = {}
            opstart_aendrede = True
        frister = {}
        nu = self.ur()
        for app_id in self.vaerksteder:
            if aabne[app_id]:
                continue
            frist = gemte_frister.get(app_id)
            gyldig = type(frist) in (int, float) and math.isfinite(frist) and frist > 0
            if gyldig:
                if frist <= nu:
                    aabne[app_id] = True
                    opstart_aendrede = True
                else:
                    frister[app_id] = frist
            elif app_id in gemte_aabne:
                frister[app_id] = nu + VAERKSTED_LUKNING_SEKUNDER
        opstart_aendrede |= frister != gemte_frister
        if opstart_aendrede:
            version += 1
            skriv_json(self.mappe / 'tilstand.json',
                       {'aabne': aabne, 'version': version, 'lukket_indtil': frister})
        self.aabne = aabne
        self.lukket_indtil = frister
        self.version = version
        self.computere = laes_json(self.mappe / 'computere.json', {})
        for c in self.computere.values():
            c['online_siden'] = None
            # En gemt heartbeat er historik, ikke en bekræftelse efter servergenstart.
            c['version'] = 0
            c['sidst_bekraeftet'] = None
            c.pop('gaa_hjem_venter', None)
        self.sidst_gemt_computere = 0.0
        self.antal_gemt = len(self.computere)

    # --- apps -------------------------------------------------------------
    def _gem_valg(self, aabne, frister):
        """Kald under låsen. Aktiv politik ændres først efter vellykket disk-write."""
        version = self.version + 1
        skriv_json(self.mappe / 'tilstand.json',
                   {'aabne': aabne, 'version': version, 'lukket_indtil': frister})
        self.aabne, self.lukket_indtil, self.version = aabne, frister, version
        for c in self.computere.values():
            if aabne.get(c.get('gaa_hjem_venter'), False):
                c.pop('gaa_hjem_venter', None)

    def _lokale_apps_status(self, local_apps):
        if not isinstance(local_apps, list) or len(local_apps) > 32:
            raise ValueError('Ugyldig status for lokale apps.')
        ready = {}
        for app in local_apps:
            if (not isinstance(app, dict) or not isinstance(app.get('id'), str)
                    or type(app.get('ready')) is not bool
                    or app['id'] in ready):
                raise ValueError('Ugyldig status for lokal app.')
            ready[app['id']] = app['ready']
        allowed = {a['id'] for a in self.apps
                   if (a.get('type') == 'lokal' and a.get('lokal_service') is True)
                   # A web workshop can have a local companion (micro:bit's
                   # project page). Health is observation, never permission
                   # to open the workshop or widen its network policy.
                   or a.get('type') in ('native', 'web')}
        if set(ready) - allowed:
            raise ValueError('Ukendt lokal app i status.')
        return ready

    def _udloeb_lukninger(self, nu=None):
        """Kald under låsen før læsning. Elevpolling virker også uden lærerbrowser."""
        nu = self.ur() if nu is None else nu
        udloebne = {app_id for app_id, frist in self.lukket_indtil.items() if frist <= nu}
        if udloebne:
            self._gem_valg({**self.aabne, **{app_id: True for app_id in udloebne}},
                           {k: v for k, v in self.lukket_indtil.items() if k not in udloebne})

    def aaben(self, app_id):
        with self.laas:
            self._udloeb_lukninger()
            return self.aabne.get(app_id, False)

    def saet(self, aendringer):
        """aendringer: {app_id: bool}. Returnerer den nye version."""
        with self.laas:
            ukendte = set(aendringer) - set(self.aabne)
            if ukendte:
                raise KeyError(', '.join(sorted(ukendte)))
            if any(type(v) is not bool for v in aendringer.values()):
                raise ValueError('App-status skal være true eller false.')
            nu = self.ur()
            aabne, frister = dict(self.aabne), dict(self.lukket_indtil)
            for app_id, frist in list(frister.items()):
                if frist <= nu:
                    aabne[app_id] = True
                    del frister[app_id]
            for app_id, aaben in aendringer.items():
                aabne[app_id] = aaben
                if aaben:
                    frister.pop(app_id, None)
                elif app_id in self.vaerksteder:
                    # Et gentaget Luk alle flytter ikke en eksisterende frist.
                    frister.setdefault(app_id, nu + VAERKSTED_LUKNING_SEKUNDER)
            if aabne != self.aabne or frister != self.lukket_indtil:
                self._gem_valg(aabne, frister)
            return self.version

    def apps_til_elever(self):
        with self.laas:
            self._udloeb_lukninger()
            return self._apps_til_elever_laast()

    def _apps_til_elever_laast(self, computer=None):
        # Byg ét sammenhængende snapshot uden at udløbe flere frister midt i et svar.
        lokal = {}
        lokal_set = False
        if computer:
            gemt = self.computere.get(computer, {}).get('local_apps')
            if isinstance(gemt, dict):
                lokal = gemt
                lokal_set = self.computere.get(computer, {}).get('local_apps_seen') is True
        c = self.computere.get(computer, {})
        aktuel = c.get('aktuel_visning', c.get('visning', 'start'))
        lukket_app = aktuel if aktuel in self.aabne and not self.aabne[aktuel] else None
        return {'version': self.version, 'lukket_app': lukket_app, 'apps': [
             {'id': a['id'], 'navn': a['navn'], 'beskrivelse': a.get('beskrivelse', ''),
              'type': a.get('type', 'vaerksted'), 'native_app': a.get('native_app'),
              'port': a.get('port'), 'start_url': a.get('start_url'),
             'domaener': a.get('domaener', []), 'kategori': a.get('kategori', 'Værksteder'),
              'klar': (lokal.get(a['id'], False)
                       if a.get('type') == 'native' and lokal_set
                       else lokal.get(a['id'], False)
                       if a.get('type') == 'lokal' and a.get('lokal_service') is True and lokal_set
                      else a.get('klar', True)),
             'aaben': self.aabne[a['id']],
             'lukket_indtil': self.lukket_indtil.get(a['id'])} for a in self.apps]}

    # --- elevcomputere ----------------------------------------------------
    def status_fra_elev(self, computer, ip, kilde, visning=None, version=None, usb=None,
                        opdater_usb=True, local_apps=None, ssh_host_key=None,
                        software_version=IKKE_ANGIVET):
        """Registrér en melding fra en elevcomputer. Returnerer svaret til den."""
        if not COMPUTERNAVN.fullmatch(computer or ''):
            raise ValueError('Ugyldigt computernavn.')
        if kilde not in ('start', 'bjaelke'):
            raise ValueError('Ukendt statuskilde.')
        if visning is not None and not isinstance(visning, str):
            raise ValueError('Ugyldig visning.')
        if (software_version is not IKKE_ANGIVET
                and (not isinstance(software_version, str)
                     or not SOFTWARE_VERSION.fullmatch(software_version))):
            raise ValueError('Ugyldig softwareversion.')
        nu = self.ur()
        with self.laas:
            self._udloeb_lukninger(nu)
            # Validate before changing heartbeat or accepting part of a report.
            ready = self._lokale_apps_status(local_apps) if local_apps is not None else None
            c = self.computere.setdefault(computer, {'visning': 'start', 'version': 0, 'usb': None})
            c.setdefault('aktuel_visning', c['visning'])
            if (c.get('online_siden') is None or c.get('ip') != ip
                    or nu - c.get('sidst_set', 0) > ONLINE_SEKUNDER):
                c['online_siden'] = nu
                c['version'] = 0
                c['sidst_bekraeftet'] = None
            c.update(ip=ip, sidst_set=nu)
            # Only reached through the device-authenticated route: the admin
            # uses this public key to trust the PC by name when its IP changes.
            if isinstance(ssh_host_key, str) and SSH_VAERTSNOEGLE.fullmatch(ssh_host_key):
                c['ssh_host_key'] = ssh_host_key
            if local_apps is not None:
                c['local_apps'] = ready
                c['local_apps_seen'] = True
            if visning is not None and (visning == 'start' or visning in self.aabne):
                c['visning'] = visning
                c['aktuel_visning'] = visning
            if kilde == 'bjaelke':
                # Only the root-authenticated caller reaches this method with
                # a software version. Policy's integer version stays separate.
                c['software_version'] = (None if software_version is IKKE_ANGIVET
                                         else software_version)
                # bool, fremtidige versioner og manglende data er ikke kvitteringer.
                gyldig = type(version) is int and 0 < version <= self.version
                c['version'] = version if gyldig else 0
                c['sidst_bekraeftet'] = nu if gyldig else None
                if opdater_usb:
                    c['usb'] = usb if isinstance(usb, str) and len(usb) <= 64 else None
            gaa_hjem = c['visning'] in self.aabne and not self.aabne[c['visning']]
            if gaa_hjem:
                c['gaa_hjem_venter'] = c['visning']
                c['visning'] = 'start'
            if nu - self.sidst_gemt_computere > 30 or len(self.computere) != self.antal_gemt:
                self._gem_computere(nu)
            svar = self._apps_til_elever_laast(computer)
            svar['gaa_hjem'] = gaa_hjem
            return svar

    def _gem_computere(self, nu):
        self.sidst_gemt_computere = nu
        self.antal_gemt = len(self.computere)
        skriv_json(self.mappe / 'computere.json',
                   {k: {n: v for n, v in c.items() if n not in ('online_siden', 'sidst_bekraeftet', 'gaa_hjem_venter')}
                   for k, c in self.computere.items()})

    def visning_fra_elev(self, computer, visning=None, usb=IKKE_ANGIVET, local_apps=None):
        """Browserens egen visning er ikke en kvittering eller et livstegn fra root."""
        if not COMPUTERNAVN.fullmatch(computer or ''):
            raise ValueError('Ugyldigt computernavn.')
        if visning is not None and (not isinstance(visning, str)
                                   or visning not in ('start', *self.aabne)):
            raise ValueError('Ugyldig visning.')
        with self.laas:
            self._udloeb_lukninger()
            ready = self._lokale_apps_status(local_apps) if local_apps is not None else None
            c = self.computere.get(computer)
            aktuel = visning if visning is not None else (c or {}).get('visning', 'start')
            gaa_hjem = bool((c or {}).get('gaa_hjem_venter')) or (aktuel in self.aabne and not self.aabne[aktuel])
            # UI kan hverken oprette en computer, holde den online eller kvittere.
            if c is not None:
                # Keep actual navigation separate from the legacy one-shot
                # go-home signal. The pupil bar leaves the editor document
                # open, so consuming that signal must not lift its overlay.
                c.setdefault('aktuel_visning', c.get('visning', 'start'))
                if visning is not None:
                    c['aktuel_visning'] = visning
                c.pop('gaa_hjem_venter', None)
                if visning is not None or gaa_hjem:
                    c['visning'] = 'start' if gaa_hjem else aktuel
                if usb is not IKKE_ANGIVET:
                    c['usb'] = usb if isinstance(usb, str) and len(usb) <= 64 else None
                if local_apps is not None:
                    c['local_apps'] = ready
                    c['local_apps_seen'] = True
            svar = self._apps_til_elever_laast(computer)
            if c is None:
                # A UI caller cannot create a trusted computer, but still
                # receives current closure policy before its first heartbeat.
                svar['lukket_app'] = aktuel if aktuel in self.aabne and not self.aabne[aktuel] else None
            svar['gaa_hjem'] = gaa_hjem
            return svar

    def oversigt(self):
        nu = self.ur()
        with self.laas:
            self._udloeb_lukninger(nu)
            computere = []
            for navn in sorted(self.computere):
                c = self.computere[navn]
                online = c.get('online_siden') is not None and 0 <= nu - c.get('sidst_set', 0) <= ONLINE_SEKUNDER
                bekraeftet_tid = c.get('sidst_bekraeftet')
                frisk = bekraeftet_tid is not None and 0 <= nu - bekraeftet_tid <= ONLINE_SEKUNDER
                computere.append({
                    'navn': navn, 'ip': c.get('ip'), 'online': online,
                    'sidst_set_sekunder': int(nu - c['sidst_set']) if c.get('sidst_set') else None,
                    'visning': c.get('visning', 'start') if online else None,
                    'bekraeftet': online and frisk and c.get('version') == self.version,
                    'usb': c.get('usb') if online else None,
                    'software_version': c.get('software_version')
                    if isinstance(c.get('software_version'), str)
                    and SOFTWARE_VERSION.fullmatch(c['software_version']) else None,
                })
            apps = self._apps_til_elever_laast()['apps']
            for a in apps:
                a['i_brug'] = sum(1 for c in computere if c['visning'] == a['id'])
            return {'version': self.version, 'apps': apps, 'computere': computere}


class Laererkode:
    """Lærerens egen kode. Vælges af læreren; Elevhub kender kun hashen."""

    def __init__(self, mappe, ur=time.time):
        self.sti = Path(mappe) / 'laerer.json'
        self.ur = ur
        self.laas = threading.Lock()
        self.sessioner = {}
        self.forsoeg = {}

    def er_valgt(self):
        return self.sti.exists()

    def vaelg(self, kode):
        if len(kode) < 8:
            raise ValueError('Lærerkoden skal have mindst 8 tegn.')
        with self.laas:
            if self.sti.exists():
                raise PermissionError('Lærerkoden er allerede valgt.')
            salt = secrets.token_bytes(16)
            skriv_json(self.sti, {'salt': salt.hex(), 'iterationer': ITERATIONER,
                                  'hash': hashlib.pbkdf2_hmac('sha256', kode.encode(), salt, ITERATIONER).hex()})

    def nulstil(self):
        with self.laas:
            self.sti.unlink(missing_ok=True)
            self.sessioner.clear()

    def log_ind(self, kode, ip):
        nu = self.ur()
        with self.laas:
            aktive = {}
            antal = 0
            for kilde, tider in self.forsoeg.items():
                friske = [t for t in tider if nu - t < 60]
                if friske:
                    aktive[kilde] = friske
                    antal += len(friske)
            self.forsoeg = aktive
            seneste = self.forsoeg.get(ip, [])
            if len(seneste) >= 10:
                raise PermissionError('For mange forsøg. Vent et minut.')
            if ip not in self.forsoeg and len(self.forsoeg) >= MAKS_FORSOEG_KILDER:
                raise PermissionError('For mange forsøg. Vent et minut.')
            if antal >= MAKS_FORSOEG_I_ALT:
                raise PermissionError('For mange forsøg. Vent et minut.')
            self.forsoeg[ip] = seneste + [nu]
            gemt = laes_json(self.sti, None)
            if not gemt:
                raise PermissionError('Lærerkoden er ikke valgt endnu. Den vælges på serveren.')
            beregnet = hashlib.pbkdf2_hmac('sha256', kode.encode(), bytes.fromhex(gemt['salt']), gemt['iterationer'])
            if not hmac.compare_digest(beregnet.hex(), gemt['hash']):
                raise PermissionError('Forkert lærerkode.')
            self.forsoeg.pop(ip, None)
            token = secrets.token_urlsafe(32)
            self.sessioner = {k: v for k, v in self.sessioner.items() if v > nu}
            self.sessioner[token] = nu + SESSION_SEKUNDER
            return token

    def gyldig(self, token):
        with self.laas:
            return bool(token) and self.sessioner.get(token, 0) > self.ur()

    def log_ud(self, token):
        with self.laas:
            self.sessioner.pop(token, None)
