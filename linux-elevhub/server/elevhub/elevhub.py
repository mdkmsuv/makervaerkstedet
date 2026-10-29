"""Elevhub-serveren: lærerpanel, elevhub og elevindgange til værkstederne.

Én proces med flere porte (standard, se konfiguration.json):
  8770  3D-print    elevindgang til Printværkstedet (127.0.0.1:8765), kun elevdelen
  8771  Laser       elevindgang til Designværkstedet (127.0.0.1:8766), kun lasersiden
  8772  Folie       elevindgang til Designværkstedet (127.0.0.1:8766), kun foliesiden
  8773  Elevhub     kioskerne henter åbne apps og melder status
  8780  Lærerpanel  eget login: åbn og luk apps, se elevcomputerne

Internet er en app uden elevindgang (type "browser"). Den håndhæves på elevcomputerne af
elevhub-regler (Chromium-politik og nftables for elevkontoen) ud fra elevhubbens besked.

    python elevhub.py --konfiguration konfiguration.json --data C:\\ProgramData\\Elevhub
    python elevhub.py --data ... --nulstil-laererkode

Når læreren lukker en app, afviser elevindgangen alle forespørgsler til den (også
direkte links og allerede åbne sider). Igangværende maskinjob påvirkes ikke.
Kun Pythons standardbibliotek.
"""
import argparse
import html
import hashlib
import http.client
import http.cookies
import ipaddress
import json
import logging
import logging.handlers
import mimetypes
import re
import socket
import sqlite3
import ssl
import threading
import tempfile
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from pathlib import PurePosixPath

from regler import REGLER, ren_sti
from tilstand import IKKE_ANGIVET, Laererkode, Tilstand
from pause import PauseStore
from sikkerhed import Enhedsregister, lokal_backend, tls_kontekst
from apps import valider, klargoer_lokale_apps
from lokale_filer import find_fil
from svg_library import Library
from system_overview import (CertificateSnapshot, PrinterSnapshot,
                             computers as system_computers, release_info)
from makerspace_api import MakerspaceService, handle as makerspace_request
from makerspace_printers import PrinterAdministration
from makerspace_laser import LaserSnapshots
from makerspace_laser_admin import LaserAdministration
from laser_station_gateway import handle as laser_station_request
from data_lock import InstanceLock
from update_admin import UpdateAdministration, handle as updates_request
from release_feed import ReleaseFeed, ReleaseFeedRunner, handle as release_feed_request
from device_updates import DeviceUpdates
from server_restart import status as server_restart_status, request as server_restart_request

MAPPE = Path(__file__).resolve().parent
WEB = MAPPE / 'web'
MAKS_UPLOAD = 26 * 1024 * 1024          # Printværkstedet tillader 25 MiB
MAKS_JSON = 16 * 1024
UDELAD = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
          'transfer-encoding', 'upgrade', 'authorization', 'cookie', 'x-print-source'}
EGNE_FILER = {'/_elevhub/metrophobic.ttf': 'assets/metrophobic-latin.ttf', '/_elevhub/favicon.svg': 'assets/favicon.svg',
              '/_elevhub/logo.png': 'assets/klostermarksskolen-logo.png'}
LAERER_FILER = {'/': 'laerer.html', '/kontrolpanel': 'laerer.html', '/om': 'om.html',
                '/makerspace': 'makerspace.html', '/makerspace.js': 'makerspace.js',
                '/makerspace-printers.js': 'makerspace-printers.js',
                '/makerspace-print-workflows.js': 'makerspace-print-workflows.js',
                '/makerspace-lasers.js': 'makerspace-lasers.js',
                '/makerspace.css': 'makerspace.css',
                '/laerer.css': 'laerer.css', '/laerer.js': 'laerer.js',
                '/update-admin.js': 'update-admin.js',
                '/system-overview.js': 'system-overview.js',
                '/system-overview.css': 'system-overview.css',
                '/favicon.svg': 'assets/favicon.svg', '/favicon.ico': 'assets/favicon.svg',
                '/assets/metrophobic-latin.ttf': 'assets/metrophobic-latin.ttf',
                '/assets/klostermarksskolen-logo.png': 'assets/klostermarksskolen-logo.png',
                '/assets/Metrophobic-OFL.txt': 'assets/Metrophobic-OFL.txt'}

# Keep this reader byte-identical to app/school_appearance.py for standalone use.
from school_appearance import (_manifest as _local_appearance,
                               school_appearance as _appearance_public,
                               school_appearance_manifest_present as _appearance_manifest_present,
                               school_appearance_asset,
                               render_local_html)
LOCAL_APPEARANCE_VERSION = 1

log = logging.getLogger('elevhub')


def system_snapshot(server):
    """Shared teacher read model; zero pupil registrations is a valid setup."""
    release = release_info(getattr(server, 'release_path', MAPPE / 'release.json'))
    register = getattr(server, 'enheder', None)
    inventory = register.inventar() if register is not None else None
    printer = getattr(server, 'printer_snapshot', None)
    if printer is None:
        printer = PrinterSnapshot(server.apps)
        server.printer_snapshot = printer
    certificates = getattr(server, 'certificate_snapshot', None)
    if certificates is None:
        certificates = CertificateSnapshot(getattr(server, 'tls_status_paths', None))
        server.certificate_snapshot = certificates
    return {'server': release,
            'tls': certificates.snapshot(),
            'computers_status': 'ok' if inventory is not None else 'unknown',
            'computers': system_computers(server.tilstand.oversigt(), inventory,
                                         release['expected_client_release']) if inventory is not None else [],
            'printers': printer.snapshot()}

BODY_TIMEOUT = 5
# A local workshop backend is trusted, but a malformed/compromised backend must
# not be able to make the gateway retain an unbounded response in memory.
MAKS_BACKEND_SVAR = 64 * 1024 * 1024


class Kropsfejl(ValueError):
    """En kontrolleret fejl i HTTP-requestens body-framing."""

    def __init__(self, status, besked):
        super().__init__(besked)
        self.status = status
        self.besked = besked


class Server(ThreadingHTTPServer):
    # Requests must drain before the hub releases its data lock.  A daemon
    # worker could otherwise still mutate SQLite after another process starts.
    daemon_threads = False
    block_on_close = True

    def __init__(self, adresse, handler, tls=None, maks_forbindelser=32):
        self.tls = tls
        self.usikker_test = False
        self.enheder = None
        self._pladser = threading.BoundedSemaphore(maks_forbindelser)
        super().__init__(adresse, handler)

    def get_request(self):
        forbindelse, adresse = super().get_request()
        forbindelse.settimeout(10)
        return forbindelse, adresse

    def process_request(self, forbindelse, adresse):
        if not self._pladser.acquire(blocking=False):
            self.shutdown_request(forbindelse)
            return
        try:
            super().process_request(forbindelse, adresse)
        except BaseException:
            self._pladser.release()
            raise

    def process_request_thread(self, forbindelse, adresse):
        # TLS-handshake sker i en begrænset worker, ikke i accept-løkken.
        try:
            if self.tls:
                try:
                    forbindelse = self.tls.wrap_socket(forbindelse, server_side=True)
                except (OSError, ssl.SSLError):
                    forbindelse.close()
                    return
            super().process_request_thread(forbindelse, adresse)
        finally:
            self._pladser.release()


class Grund(BaseHTTPRequestHandler):
    server_version = 'Elevhub'
    protocol_version = 'HTTP/1.1'

    def parse_request(self):
        # Python versions differ in whether an HTTP/0.9 request waits for
        # headers. Reject its two-part request line before the base parser so
        # a client cannot occupy a worker while waiting for our error reply.
        linje = self.raw_requestline.decode('iso-8859-1').rstrip('\r\n')
        dele = linje.split()
        if len(dele) == 2:
            self.requestline = linje
            self.command = dele[0]
            self.request_version = 'HTTP/1.1'
            self.close_connection = True
            self.svar(400, {'error': 'En eksplicit HTTP-version kræves.'})
            return False
        return super().parse_request()

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            # Closing a tab or cancelling a file download is normal. The
            # worker and socket are still released by the server's finally.
            self.close_connection = True

    def log_message(self, fmt, *args):
        log.info('%s %s %s', self.server.navn, self.client_address[0], fmt % args)

    def svar(self, status, krop=b'', type_='application/json; charset=utf-8', ekstra=None):
        # HTTP/0.9 parsing in BaseHTTPRequestHandler leaves ``headers`` as a
        # plain dict and suppresses status lines.  Elevhub is HTTP/1.1 only;
        # turn the response into a normal, bounded HTTP error instead of
        # raising while inspecting headers and logging a traceback.
        if self.request_version == 'HTTP/0.9':
            self.request_version = 'HTTP/1.1'
        if isinstance(krop, (dict, list)):
            krop = json.dumps(krop, ensure_ascii=False).encode('utf-8')
        elif isinstance(krop, str):
            krop = krop.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', type_)
        self.send_header('Content-Length', str(len(krop)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if self.close_connection:
            self.send_header('Connection', 'close')
        for k, v in (ekstra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(krop)

    def kropslaengde(self, maksimum=None):
        """Læs og valider Content-Length uden at stole på HTTP-serverens parser."""
        if not hasattr(self.headers, 'get_all'):
            self.close_connection = True
            raise Kropsfejl(400, 'HTTP/1.1 eller nyere kræves.')
        for navn in ('Host', 'Origin'):
            if len(self.headers.get_all(navn) or []) > 1:
                self.close_connection = True
                raise Kropsfejl(400, f'Der må kun være ét {navn}-header.')
        if self.headers.get_all('Transfer-Encoding') is not None:
            self.close_connection = True
            raise Kropsfejl(411, 'Transfer-Encoding understøttes ikke.')
        vaerdier = self.headers.get_all('Content-Length')
        if vaerdier and (len(vaerdier) != 1 or ',' in vaerdier[0]):
            self.close_connection = True
            raise Kropsfejl(400, 'Ugyldig Content-Length.')
        if not vaerdier:
            return 0
        raaw = vaerdier[0].strip()
        if not raaw or not raaw.isascii() or not raaw.isdecimal():
            self.close_connection = True
            raise Kropsfejl(400, 'Ugyldig Content-Length.')
        try:
            laengde = int(raaw)
        except ValueError:
            self.close_connection = True
            raise Kropsfejl(400, 'Ugyldig Content-Length.')
        if maksimum is not None and laengde > maksimum:
            self.close_connection = True
            raise Kropsfejl(413, 'Forespørgslen er for stor.')
        return laengde

    def laes_krop(self, laengde):
        if not laengde:
            return b''
        gammel_timeout = self.connection.gettimeout()
        self.connection.settimeout(BODY_TIMEOUT)
        try:
            data = self.rfile.read(laengde)
        except socket.timeout:
            self.close_connection = True
            raise Kropsfejl(408, 'Forespørgslen indeholdt ikke hele body.')
        finally:
            self.connection.settimeout(gammel_timeout)
        if len(data) != laengde:
            self.close_connection = True
            raise Kropsfejl(400, 'Forespørgslen indeholdt ikke hele body.')
        return data

    def kropsfejl(self, fejl):
        self.close_connection = True
        return self.svar(fejl.status, {'error': fejl.besked})

    def laes_json(self, laengde=None):
        if laengde is None:
            laengde = self.kropslaengde(MAKS_JSON)
        if not 0 < laengde <= MAKS_JSON:
            raise ValueError('Forespørgslen er tom eller for stor.')
        data = json.loads(self.laes_krop(laengde))
        if not isinstance(data, dict):
            raise ValueError('Ugyldig forespørgsel.')
        return data

    def fil(self, relativ, status=200):
        sti = WEB / relativ
        type_ = mimetypes.guess_type(sti.name)[0] or 'application/octet-stream'
        if sti.suffix in ('.html', '.css', '.js', '.txt'):
            type_ += '; charset=utf-8'
        self.svar(status, sti.read_bytes(), type_)


# --- Elevindgange --------------------------------------------------------------------------
class Indgang(Grund):
    """Én elevindgang = én app. Lukker kun appens elevdel igennem, og kun når den er åben."""

    def haandter(self):
        app, tilstand = self.server.app, self.server.tilstand
        try:
            laengde = self.kropslaengde(MAKS_UPLOAD)
        except Kropsfejl as fejl:
            return self.kropsfejl(fejl)
        sti = ren_sti(self.path)
        if sti in EGNE_FILER and self.command in ('GET', 'HEAD'):
            if laengde:
                self.close_connection = True
            return self.fil(EGNE_FILER[sti])
        if sti == '/_elevhub/aaben':
            if laengde:
                self.close_connection = True
            return self.svar(200, {'aaben': tilstand.aaben(app['id'])})
        # Bibliotekerne er særskilte, read-only elev-ruter.  Folie må også
        # ligge på den centrale Laser-indgang, så et lukket Laser-værksted
        # ikke blokerer Folie.  Den autoritative gate vælges fra ruten.
        bibliotek_app = None
        if sti and (sti.startswith('/api/laser/library') or sti.startswith('/api/folie/library')):
            match = re.fullmatch(r'/api/(laser|folie)/library(?:/status|/file/[a-f0-9]{64})?', sti)
            if match:
                bibliotek_app = match.group(1)
            if (bibliotek_app is None or self.command != 'GET' or self.path != sti or laengde
                    or (app['id'] != bibliotek_app and not (app['id'] == 'laser' and bibliotek_app == 'folie'))):
                if laengde:
                    self.close_connection = True
                return self.svar(403, {'error': 'Biblioteksadgangen er begrænset til det relevante værksted.'})
            return self.bibliotek(sti, bibliotek_app)
        if not tilstand.aaben(app['id']):
            return self.lukket(laengde)
        afgoerelse, maal = self.server.regel(self.command, self.path)
        if afgoerelse == 'omdiriger':
            if laengde:
                self.close_connection = True
            return self.svar(302, b'', 'text/plain', {'Location': maal})
        if afgoerelse != 'tillad':
            if laengde:
                self.close_connection = True
            log.warning('%s AFVIST %s %s %s', app['id'], self.client_address[0], self.command, self.path)
            return self.svar(403, {'error': f'Denne del af {app["navn"]} er kun for læreren.'})
        if self.command == 'POST':
            oprindelse = self.headers.get('Origin')
            protokol = 'https://' if isinstance(self.connection, ssl.SSLSocket) else 'http://'
            if oprindelse is not None and oprindelse != protokol + self.headers.get('Host', ''):
                if laengde:
                    self.close_connection = True
                return self.svar(403, {'error': 'Brug værkstedets egen side.'})
        self.videresend(laengde)

    def bibliotek(self, sti, workshop=None):
        tilstand = self.server.tilstand
        workshop = workshop or ('folie' if sti.startswith('/api/folie/') else 'laser')
        try:
            with tilstand.laas:
                if not tilstand.aaben(workshop):
                    return self.svar(423, {'error': f'Læreren har lukket {workshop.capitalize()}.'})
                version = tilstand.version
            if sti == f'/api/{workshop}/library/status':
                return self.svar(200, {'enabled': True, 'version': version})
            library = getattr(self.server, 'svg_library', None)
            if library is None:
                return self.svar(503, {'error': 'SVG-biblioteket er ikke aktiveret på serveren.'})
            if sti == f'/api/{workshop}/library':
                payload = {'items': library.catalogue()}
            elif sti.startswith(f'/api/{workshop}/library/file/'):
                payload = {'svg': library.svg(sti.removeprefix(f'/api/{workshop}/library/file/'))}
            else:
                return self.svar(404, {'error': 'Biblioteksfilen findes ikke.'})
            # A close/reopen during the fetch revokes this response too.
            with tilstand.laas:
                if not tilstand.aaben(workshop) or tilstand.version != version:
                    return self.svar(423, {'error': 'Lærerens valg ændrede sig. Vælg filen igen.'})
                return self.svar(200, {**payload, 'version': version})
        except (OSError, ValueError) as error:
            log.warning('SVG library unavailable: %s', type(error).__name__)
            return self.svar(502, {'error': 'Biblioteket kunne ikke hentes. Prøv igen senere.'})

    def lukket(self, laengde):
        navn = self.server.app['navn']
        if laengde:
            # Requesten afvises, så luk forbindelsen i stedet for at lade body
            # blive fortolket som starten på næste HTTP/1.1-request.
            self.close_connection = True
        navigation = self.headers.get('Sec-Fetch-Dest') == 'document' or 'text/html' in self.headers.get('Accept', '')
        if navigation and self.command in ('GET', 'HEAD'):
            side = (WEB / 'lukket.html').read_text(encoding='utf-8').replace('{{navn}}', navn)
            return self.svar(423, side, 'text/html; charset=utf-8')
        self.svar(423, {'error': f'Læreren har lukket {navn} lige nu. Gå tilbage til start.'})

    def videresend(self, laengde):
        try:
            krop = self.laes_krop(laengde) if laengde else None
        except Kropsfejl as fejl:
            return self.kropsfejl(fejl)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in UDELAD}
        if self.command == 'POST' and self.headers.get('Origin') is not None:
            # haandter verified the browser origin against the actual TLS
            # connection. The next hop is HTTP on loopback and retains Host;
            # its origin check must see that hop's scheme. Never trust a
            # forwarded-proto header, or give native clients a new Origin.
            headers = {k: v for k, v in headers.items() if k.lower() != 'origin'}
            headers['Origin'] = 'http://' + self.headers.get('Host', '')
        # Use the connection address for both CaDoodle and browser submissions.
        # Never accept a source identity chosen by the pupil's request headers.
        if self.server.app.get('regler') == 'printvaerksted':
            headers['X-Print-Source'] = self.client_address[0]
        vaert, port = self.server.bagved
        if self.server.app.get('regler') == 'printvaerksted':
            # The print service validates its own Host allowlist. The pupil's
            # public address was checked here and must not become a backend Host.
            headers = {k: v for k, v in headers.items() if k.lower() not in ('host', 'origin')}
            backend_host = f'[{vaert}]:{port}' if ':' in vaert else f'{vaert}:{port}'
            headers['Host'] = backend_host
            if self.command == 'POST' and self.headers.get('Origin') is not None:
                headers['Origin'] = 'http://' + backend_host
        forbindelse = http.client.HTTPConnection(vaert, port, timeout=120)
        try:
            forbindelse.request(self.command, self.path, body=krop, headers=headers)
            bagved = forbindelse.getresponse()
            data = bagved.read(MAKS_BACKEND_SVAR + 1)
            if len(data) > MAKS_BACKEND_SVAR:
                log.error('%s sendte et svar større end %d bytes', self.server.app['navn'], MAKS_BACKEND_SVAR)
                self.close_connection = True
                return self.svar(502, {'error': f'{self.server.app["navn"]} svarede med for meget data.'})
        except OSError as fejl:
            log.error('%s svarer ikke: %s', self.server.app['navn'], fejl)
            return self.svar(502, {'error': f'{self.server.app["navn"]} svarer ikke lige nu. Prøv igen om lidt.'})
        finally:
            forbindelse.close()
        data = self.tilpas(bagved, data)
        self.send_response(bagved.status, bagved.reason)
        for k, v in bagved.getheaders():
            if k.lower() not in UDELAD and k.lower() != 'content-length':
                self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def tilpas(self, bagved, data):
        """Skjul links, der ikke giver mening i kiosken (fx "Til læreren"), uden at ændre værkstedets filer."""
        skjul = self.server.app.get('skjul')
        if not skjul or 'text/html' not in (bagved.getheader('Content-Type') or '') or bagved.getheader('Content-Encoding'):
            return data
        stil = ('<style id="elevhub">' + ', '.join(skjul) + ' { display: none !important; }</style>').encode()
        slut = data.lower().find(b'</head>')
        return data[:slut] + stil + data[slut:] if slut >= 0 else data

    do_GET = do_HEAD = do_POST = haandter

    def ikke_tilladt(self):
        try:
            laengde = self.kropslaengde(MAKS_UPLOAD)
        except Kropsfejl as fejl:
            return self.kropsfejl(fejl)
        if laengde:
            self.close_connection = True
        self.svar(405, {'error': 'Ikke tilladt.'})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = ikke_tilladt


# --- Elevhub (kioskerne) --------------------------------------------------------------------
class LokaltVaerksted(Indgang):
    """Scratch/editorfiler fra en valgt mappe bag samme værkstedsafbryder."""

    def haandter(self):
        try:
            laengde = self.kropslaengde(0)
        except Kropsfejl as fejl:
            return self.kropsfejl(fejl)
        if self.command not in ('GET', 'HEAD'):
            self.close_connection = True
            return self.svar(405, {'error': 'Kun læsning er tilladt.'}, ekstra={'Allow': 'GET, HEAD'})
        try:
            aaben = self.server.tilstand.aaben(self.server.app['id'])
        except OSError:
            return self.svar(503, {'error': 'Værkstedets status kan ikke opdateres.'})
        if ren_sti(self.path) == '/_elevhub/aaben':
            return self.svar(200, {'aaben': aaben})
        if not aaben:
            return self.lukket(laengde)
        if not self.server.app.get('klar', True):
            return self.svar(503, {'error': 'Det lokale værksted er ikke installeret endnu.'})
        fil = find_fil(self.server.app.get('mappe'), self.path)
        if fil is None:
            return self.svar(404, {'error': 'Filen findes ikke.'})
        try:
            size = fil.stat().st_size
            # Scratch's corresponding-source archive exceeds 64 MiB. It is
            # streamed with the same bounded chunks as other static files.
            maksimum = (128 if fil.name == 'source.zip' else 64) * 1024 * 1024
            if size > maksimum:
                return self.svar(413, {'error': 'Filen er for stor.'})
            with fil.open('rb') as f:
                type_ = {'.js': 'text/javascript', '.wasm': 'application/wasm'}.get(fil.suffix.lower())
                type_ = type_ or mimetypes.guess_type(fil.name)[0] or 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', type_)
                self.send_header('Content-Length', str(size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'self' blob: data:; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'")
                self.end_headers()
                if self.command != 'HEAD':
                    resterende = size
                    while resterende:
                        stykke = f.read(min(64 * 1024, resterende))
                        if not stykke:
                            self.close_connection = True
                            break
                        self.wfile.write(stykke)
                        resterende -= len(stykke)
        except (OSError, ValueError):
            self.close_connection = True

    do_GET = do_HEAD = do_POST = haandter


class Hub(Grund):
    """Kioskens startside og elevbjælke. Offentlig: viser kun app-navne og åben/lukket."""

    CORS = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Elevhub-Computer',
            'Access-Control-Max-Age': '600'}

    def device_update(self, length=None):
        # Native administrator helper only. UI credentials, plaintext test mode,
        # browser origins and caller-selected computer names grant no access.
        if self.headers.get('Origin') is not None:
            self.close_connection = True
            return self.svar(403, {'error': 'Brug computerens lokale administration.'})
        computer = self.enhed('root')
        if computer is None:
            return
        service = getattr(self.server, 'device_updates', None)
        if service is None:
            return self.svar(503, {'error': 'Opdateringstjenesten er ikke klar.'})
        try:
            result = service.snapshot(computer) if length is None else service.execute(computer, self.laes_json(length))
            return self.svar(200, result)
        except Kropsfejl as error:
            self.close_connection = True
            return self.svar(error.status, {'error': error.besked})
        except (ValueError, TypeError):
            return self.svar(409, {'error': 'Opdateringen kunne ikke bestilles. Hent status og prøv igen.'})
        except (OSError, sqlite3.Error):
            return self.svar(503, {'error': 'Opdateringstjenesten er midlertidigt utilgængelig.'})

    def enhed(self, rolle):
        navne = self.headers.get_all('X-Elevhub-Computer') or []
        tokens = self.headers.get_all('Authorization') or []
        register = self.server.enheder
        if (not isinstance(self.connection, ssl.SSLSocket) or register is None
                or len(navne) != 1 or len(tokens) != 1
                or not register.godkend(navne[0], tokens[0], rolle)):
            self.close_connection = True
            self.svar(401, {'error': 'Computeren kunne ikke godkendes.'}, ekstra=self.CORS)
            return None
        return navne[0]

    def do_OPTIONS(self):
        try:
            laengde = self.kropslaengde(MAKS_JSON)
        except Kropsfejl as fejl:
            self.close_connection = True
            return self.svar(fejl.status, {'error': fejl.besked}, ekstra=self.CORS)
        if laengde:
            self.close_connection = True
        self.svar(204, b'', 'text/plain', self.CORS)

    def do_GET(self):
        try:
            laengde = self.kropslaengde(MAKS_JSON)
        except Kropsfejl as fejl:
            self.close_connection = True
            return self.svar(fejl.status, {'error': fejl.besked}, ekstra=self.CORS)
        if laengde:
            self.close_connection = True
        sti = ren_sti(self.path)
        if sti == '/api/enhed/politik':
            if self.enhed('root') is None:
                return
            return self.svar(200, self.server.tilstand.apps_til_elever())
        if sti == '/api/enhed/opdatering':
            return self.device_update()
        if sti == '/api/apps':
            return self.svar(200, self.server.tilstand.apps_til_elever(), ekstra=self.CORS)
        if sti == '/api/health':
            return self.svar(200, {'ok': True, 'service': 'elevhub'}, ekstra=self.CORS)
        self.svar(404, {'error': 'Findes ikke.'}, ekstra=self.CORS)

    do_HEAD = do_GET

    def do_POST(self):
        try:
            laengde = self.kropslaengde(MAKS_JSON)
        except Kropsfejl as fejl:
            self.close_connection = True
            return self.svar(fejl.status, {'error': fejl.besked}, ekstra=self.CORS)
        sti = ren_sti(self.path)
        if sti == '/api/enhed/opdatering':
            return self.device_update(laengde)
        if sti not in ('/api/status', '/api/enhed/status'):
            if laengde:
                self.close_connection = True
            return self.svar(404, {'error': 'Findes ikke.'}, ekstra=self.CORS)
        try:
            d = self.laes_json(laengde)
            if self.server.usikker_test and sti == '/api/status':
                navn = str(d.get('computer', ''))
                svar = self.server.tilstand.status_fra_elev(
                    navn, self.client_address[0], d.get('kilde'),
                    visning=d.get('visning'), version=d.get('version'), usb=d.get('usb'))
            else:
                navn = self.enhed('root' if sti == '/api/enhed/status' else 'ui')
                if navn is None:
                    return
                if d.get('computer') != navn:
                    return self.svar(403, {'error': 'Forkert computer.'}, ekstra=self.CORS)
                if sti == '/api/enhed/status':
                    svar = self.server.tilstand.status_fra_elev(
                        navn, self.client_address[0], 'bjaelke', version=d.get('version'),
                        usb=d.get('usb'), opdater_usb='usb' in d,
                        local_apps=d.get('local_apps'), ssh_host_key=d.get('ssh_host_key'),
                        software_version=d.get('software_version', IKKE_ANGIVET))
                else:
                    svar = self.server.tilstand.visning_fra_elev(
                        navn,
                        d.get('visning'),
                        d.get('usb', IKKE_ANGIVET),
                        d.get('local_apps'),
                    )
            if sti == '/api/status' and getattr(self.server, 'pause', None) is not None:
                ack = d.get('pause_ack')
                if isinstance(ack, dict):
                    self.server.pause.acknowledge(navn, ack.get('version'), ack.get('shown'))
                svar['pause'] = self.server.pause.state()
            self.svar(200, svar, ekstra=self.CORS)
        except Kropsfejl as fejl:
            self.close_connection = True
            return self.svar(fejl.status, {'error': fejl.besked}, ekstra=self.CORS)
        except (ValueError, TypeError, json.JSONDecodeError) as fejl:
            self.svar(400, {'error': str(fejl)}, ekstra=self.CORS)


# --- Lærerpanel ------------------------------------------------------------------------------
class Laerer(Grund):
    SIKKERHED = {'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
                 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer'}

    def appearance_asset(self, asset):
        result = school_appearance_asset(self.server.tilstand.mappe, asset)
        if result is None:
            return self.svar(404, {'error': 'Den lokale skoleprofil har ikke denne fil.'})
        return self.svar(200, result[0], result[1])

    def fil(self, relativ, status=200):
        local = _local_appearance(self.server.tilstand.mappe)
        if local is None and _appearance_manifest_present(self.server.tilstand.mappe):
            return self.svar(503, {'error': 'Den lokale skoleprofil kunne ikke valideres.'})
        if local and '/' + relativ in local.get('_static', {}):
            path, content = local['_static']['/' + relativ]
            return self.svar(status, content, mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        # A distributable school keeps its public identity beside the hub data.
        # Legacy installations without this profile retain their existing UI.
        if relativ not in ('laerer.html', 'om.html', 'laerer.css', 'makerspace.html', 'makerspace.css'):
            return super().fil(relativ, status)
        path = self.server.tilstand.mappe.parent / 'school.json'
        try:
            profile = local
            if profile is None:
                if path.is_symlink() or path.stat().st_size > 65536:
                    return super().fil(relativ, status)
                profile = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return super().fil(relativ, status)
        if not isinstance(profile, dict):
            return super().fil(relativ, status)
        name, theme = profile.get('school_name'), profile.get('theme')
        palettes = {'red': ('#c62b2f', '#8c1d24'), 'yellow': ('#a36a00', '#704600'),
                    'green': ('#28734e', '#1a5037')}
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or not isinstance(theme, str) or theme not in palettes):
            return super().fil(relativ, status)
        content = (WEB / relativ).read_text(encoding='utf-8')
        if relativ.endswith('.html'):
            content = render_local_html(content.encode('utf-8'), profile).decode('utf-8')
            # Local identity is distinct from the mandatory project credit.
            # Never replace the school's name throughout an entire document.
            content = re.sub(r'(<span class="brand-school">)[^<]*(</span>)',
                             lambda m: m[1] + html.escape(name) + m[2], content)
            content = content.replace('<title>Lærerpanel · Klostermarksskolen</title>',
                                      '<title>Lærerpanel · ' + html.escape(name) + '</title>')
            content = content.replace('alt="Klostermarksskolen"', 'alt="' + html.escape(name) + '"')
            if local and local.get('style', {}).get('logo'):
                content = content.replace('src="/assets/klostermarksskolen-logo.png"', 'src="/school-appearance/logo"')
            if local and local.get('style', {}).get('website_css'):
                content = content.replace('</head>', '<link rel="stylesheet" href="/school-appearance/website.css"></head>', 1)
            return self.svar(status, content.encode('utf-8'), 'text/html; charset=utf-8')
        brand, deep = palettes[theme]
        content += ('\n:root{--brand:' + brand + ';--brand-dark:' + deep
                    + ';--section:#f3edd6;--pale:#f8f3e4;--ink:#121611;--line:#d8d3c0}\n')
        return self.svar(status, content.encode('utf-8'), 'text/css; charset=utf-8')

    def lokal(self):
        peer = ipaddress.ip_address(self.client_address[0])
        # A listener bound to the server's LAN address also receives same-host
        # browser connections with that address as source, not 127.0.0.1.
        bound = ipaddress.ip_address(self.server.server_address[0])
        return peer.is_loopback or (not bound.is_unspecified and peer == bound)

    def token(self):
        try:
            kage = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            return ''
        return kage['elevhub_laerer'].value if 'elevhub_laerer' in kage else ''

    def logget_ind(self):
        return self.server.kode.gyldig(self.token())

    def sikker_kage(self):
        return '; Secure' if isinstance(self.connection, ssl.SSLSocket) else ''

    def svar(self, status, krop=b'', type_='application/json; charset=utf-8', ekstra=None):
        super().svar(status, krop, type_, {**self.SIKKERHED, **(ekstra or {})})

    def do_GET(self):
        try:
            laengde = self.kropslaengde(MAKS_JSON)
        except Kropsfejl as fejl:
            return self.kropsfejl(fejl)
        if laengde:
            self.close_connection = True
        sti = ren_sti(self.path)
        local = _local_appearance(self.server.tilstand.mappe)
        if local and sti in local.get('_static', {}):
            path, content = local['_static'][sti]
            return self.svar(200, content, mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        if sti in ('/school-appearance/website.css', '/school-appearance/logo'):
            if local is None and _appearance_manifest_present(self.server.tilstand.mappe):
                return self.svar(503, {'error': 'Den lokale skoleprofil kunne ikke valideres.'})
            return self.appearance_asset(sti.rsplit('/', 1)[-1])
        if sti in LAERER_FILER:
            return self.fil(LAERER_FILER[sti])
        if sti == '/api/school-appearance':
            appearance = _appearance_public(self.server.tilstand.mappe)
            if appearance is None and _appearance_manifest_present(self.server.tilstand.mappe):
                return self.svar(503, {'error': 'Den lokale skoleprofil kunne ikke valideres.'})
            return self.svar(200, appearance)
        if laser_station_request(self, sti, laengde):
            return
        if release_feed_request(self, sti):
            return
        if updates_request(self, sti):
            return
        if makerspace_request(self, sti):
            return
        if sti == '/LICENSE':
            for license_path in (MAPPE.parent / 'licenses' / 'Elevhub-LICENSE.txt',
                                 MAPPE.parent.parent / 'licenses' / 'Elevhub-LICENSE.txt',
                                 MAPPE.parent / 'LICENSE',
                                 MAPPE.parent.parent / 'LICENSE'):
                if license_path.is_file():
                    return self.svar(200, license_path.read_bytes(), 'text/plain; charset=utf-8')
            return self.svar(404, {'error': 'Licensen findes ikke.'})
        if sti == '/api/laerer/session':
            return self.svar(200, {'logget_ind': self.logget_ind(), 'kode_valgt': self.server.kode.er_valgt(),
                                   'lokal': self.lokal()})
        if sti == '/api/laerer/oversigt':
            if not self.logget_ind():
                return self.svar(401, {'error': 'Log ind igen.'})
            oversigt = self.server.tilstand.oversigt()
            if getattr(self.server, 'pause', None) is not None:
                oversigt['pause'] = self.server.pause.state()
                for computer in oversigt.get('computere', []):
                    computer['pause_ack'] = self.server.pause.acknowledgement(computer.get('navn', ''))
            for a in oversigt['apps']:
                konf = next(k for k in self.server.apps if k['id'] == a['id'])
                laererside = konf.get('laererside', '')
                note = konf.get('laererside_note', '')
                # A Host header is supplied by the caller and must not become
                # an authenticated link target.  Until a canonical public
                # server origin is configured, placeholders are shown as a
                # server-local note only.
                if '{vaert}' in laererside:
                    laererside = ''
                    note = (note + ' Åbn denne lærerside i serverens egen browser.').strip()
                a['laererside'] = laererside
                a['laererside_note'] = note
            return self.svar(200, oversigt)
        if sti == '/api/laerer/system':
            if not self.logget_ind():
                return self.svar(401, {'error': 'Log ind igen.'})
            return self.svar(200, system_snapshot(self.server))
        if sti == '/api/laerer/server-restart':
            if not self.logget_ind():
                return self.svar(401, {'error': 'Log ind igen.'})
            return self.svar(200, server_restart_status())
        self.svar(404, {'error': 'Findes ikke.'})

    do_HEAD = do_GET

    def do_POST(self):
        sti = ren_sti(self.path)
        try:
            laengde = self.kropslaengde(MAKS_JSON)
            if laser_station_request(self, sti, laengde):
                return
        except Kropsfejl as fejl:
            return self.kropsfejl(fejl)
        oprindelse = self.headers.get('Origin')
        protokol = 'https://' if isinstance(self.connection, ssl.SSLSocket) else 'http://'
        if oprindelse and oprindelse != protokol + self.headers.get('Host', ''):
            if laengde:
                self.close_connection = True
            return self.svar(403, {'error': 'Forespørgslen kom ikke fra lærerpanelet.'})
        # SameSite cookies cover normal browser cross-site requests, but a
        # cookie-authenticated mutation must still carry the panel origin.
        if sti not in ('/api/laerer/opsaet', '/api/laerer/login') and self.token() and not oprindelse:
            if laengde:
                self.close_connection = True
            return self.svar(403, {'error': 'Forespørgslen kom ikke fra lærerpanelet.'})
        if self.headers.get_content_type() != 'application/json':
            if laengde:
                self.close_connection = True
            return self.svar(415, {'error': 'Forkert indholdstype.'})
        try:
            d = self.laes_json(laengde)
            if release_feed_request(self, sti, d):
                return
            if updates_request(self, sti, d):
                return
            if makerspace_request(self, sti, d):
                return
            kode = self.server.kode
            if sti == '/api/laerer/opsaet':
                if not self.lokal():
                    raise PermissionError('Lærerkoden vælges på selve serveren.')
                if d.get('kode') != d.get('kode_igen'):
                    raise ValueError('De to koder er ikke ens.')
                kode.vaelg(str(d.get('kode', '')))
                log.warning('Lærerkoden er valgt fra serveren.')
                return self.svar(200, {'ok': True})
            if sti == '/api/laerer/login':
                token = kode.log_ind(str(d.get('kode', '')), self.client_address[0])
                log.info('Lærer logget ind fra %s', self.client_address[0])
                return self.svar(200, {'ok': True}, ekstra={
                    'Set-Cookie': f'elevhub_laerer={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{self.sikker_kage()}'})
            if sti == '/api/laerer/logout':
                kode.log_ud(self.token())
                return self.svar(200, {'ok': True}, ekstra={
                    'Set-Cookie': 'elevhub_laerer=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0' + self.sikker_kage()})
            if not self.logget_ind():
                return self.svar(401, {'error': 'Log ind igen.'})
            if sti == '/api/laerer/server-restart':
                if d != {'confirmation': 'GENSTART HELE SERVEREN'}:
                    raise ValueError('Bekræft genstart af hele Windows-serveren med GENSTART HELE SERVEREN.')
                result = server_restart_request()
                log.warning('Genstart af Windows-server bestilt fra %s: accepted=%s',
                            self.client_address[0], result['accepted'])
                return self.svar(202 if result['accepted'] else 503, result)
            if sti == '/api/laerer/pause':
                if getattr(self.server, 'pause', None) is None:
                    return self.svar(503, {'error': 'Skærmpause er ikke konfigureret.'})
                state = self.server.pause.set(
                    d.get('paused'), message=d.get('message', ''), expires_in=d.get('expires_in'))
                log.warning('Skærmpause ændret fra %s: paused=%s version=%s',
                            self.client_address[0], state['paused'], state['version'])
                return self.svar(200, {'pause': state})
            tilstand = self.server.tilstand
            aaben = d.get('aaben')
            if not isinstance(aaben, bool):
                raise ValueError('Feltet aaben skal være true eller false.')
            if sti == '/api/laerer/app':
                version = tilstand.saet({str(d.get('id')): aaben})
            elif sti == '/api/laerer/alle':
                # Internet kræver altid sit eget, udtrykkelige lærerklik.
                version = tilstand.saet({a['id']: aaben for a in self.server.apps
                                        if a.get('type', 'vaerksted') != 'browser'})
            else:
                if laengde:
                    self.close_connection = True
                return self.svar(404, {'error': 'Findes ikke.'})
            log.warning('Apps ændret fra %s: %s', self.client_address[0], tilstand.aabne)
            self.svar(200, {'version': version})
        except PermissionError as fejl:
            if fejl.errno is not None:
                # Filesystem permission errors are persistence failures, not
                # rejected teacher credentials. Never expose a private path.
                log.exception('Kunne ikke gemme lærerens ændring')
                self.svar(503, {'error': 'Kunne ikke gemme ændringen lige nu. Prøv igen.'})
            else:
                self.svar(403, {'error': str(fejl)})
        except KeyError:
            self.svar(404, {'error': 'Ukendt app.'})
        except Kropsfejl as fejl:
            return self.kropsfejl(fejl)
        except (ValueError, TypeError, json.JSONDecodeError) as fejl:
            self.svar(400, {'error': str(fejl)})
        except OSError:
            log.exception('Kunne ikke gemme lærerens app-valg')
            self.svar(503, {'error': 'Kunne ikke gemme ændringen lige nu. Prøv igen.'})


# --- Start ----------------------------------------------------------------------------------
def adresse(tekst):
    vaert, _, port = tekst.rpartition(':')
    return vaert, int(port)


def laes_konfiguration(sti):
    konf = json.loads(Path(sti).read_text(encoding='utf-8'))
    return valider(konf, REGLER)


def elevindgange(konf):
    """Yield only apps with a central HTTP endpoint.

    Native pupil applications are announced through the hub and handled by the
    pupil agent; they must never make the central server bind a fake port.
    """
    return (app for app in konf['apps']
            if app.get('type', 'vaerksted') not in ('browser', 'web', 'native')
            and not (app.get('type') == 'lokal' and app.get('lokal_service') is True))


def _restore_hold(data):
    """Return the first restore marker in the data path or its ancestors."""
    folder = Path(data).absolute()
    for parent in (folder, *folder.parents):
        marker = parent / '.restore-hold.json'
        if os.path.lexists(str(marker)):
            return marker
    return None


def _write_capabilities(data):
    target = Path(data) / 'backup-protocol.json'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=data,
                                         prefix='.backup-protocol-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'protocol': 1, 'role': 'hub'}, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(args):
    args.data = Path(args.data)
    args.konfiguration = Path(args.konfiguration)
    hold = _restore_hold(args.data)
    if hold is not None:
        raise RuntimeError('Datamappen er markeret til gendannelse.')
    args.data.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(args.data)
    servere = []
    running = []
    file_handler = None
    release_runner = None
    try:
        # Recheck after taking the lock: restore tooling may create the marker
        # between the initial guard check and lock acquisition.
        if _restore_hold(args.data) is not None:
            raise RuntimeError('Datamappen er markeret til gendannelse.')
        _write_capabilities(args.data)
        if args.nulstil_laererkode:
            Laererkode(args.data).nulstil()
            print('Lærerkoden er glemt. Vælg en ny i lærerpanelet på serveren (http://localhost:8780/).')
            return

        file_handler = logging.handlers.RotatingFileHandler(args.data / 'elevhub.log',
            maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[file_handler])
        konf = laes_konfiguration(args.konfiguration)
        basis = args.konfiguration.resolve().parent
        printer_administration = PrinterAdministration(konf.get('makerspace_printers'), basis)
        laser_stations = LaserSnapshots(konf.get('makerspace_laser_stations', []))
        laser_administration = LaserAdministration(konf.get('makerspace_laser_queue'), basis)
        klargoer_lokale_apps(konf, basis)
        tls = tls_kontekst(konf, basis)
        tls_config = konf.get('tls') if isinstance(konf.get('tls'), dict) else {}
        tls_status_paths = {
            'certificate': str(Path(basis) / tls_config['certifikat'])
            if isinstance(tls_config.get('certifikat'), str) else None,
            'ca': str(Path(basis) / tls_config['ca'])
            if isinstance(tls_config.get('ca'), str) else None,
        }
        usikker = konf.get('usikker_test', False)
        enheder = None
        if not usikker:
            if not konf.get('enhedsregister'):
                raise ValueError('Enhedsregister kræves til sikker drift.')
            enheder = Enhedsregister(basis / konf['enhedsregister'])
            for app in konf['apps']:
                if app.get('type', 'vaerksted') == 'vaerksted' and not lokal_backend(app['bagved']):
                    raise ValueError('Værkstedernes backend skal lytte på loopback på samme server.')
        else:
            log.warning('USIKKER TEST: ukrypteret trafik og status uden serveridentitet.')
        tilstand = Tilstand(args.data, konf['apps'])
        pause = PauseStore(args.data)
        kode = Laererkode(args.data)

        for app in elevindgange(konf):
            type_ = app.get('type', 'vaerksted')
            s = Server((konf['elev_adresse'], app['port']), LokaltVaerksted if type_ == 'lokal' else Indgang, tls=tls)
            servere.append(s)
            s.navn, s.app, s.tilstand = app['id'], app, tilstand
            if type_ == 'vaerksted':
                s.regel, s.bagved = REGLER[app['regler']], adresse(app['bagved'])
                if app['id'] in ('laser', 'folie'):
                    s.svg_library = Library()
        hub = Server((konf['elev_adresse'], konf['hub_port']), Hub, tls=tls)
        servere.append(hub)
        hub.navn, hub.tilstand, hub.pause = 'hub', tilstand, pause
        hub.enheder, hub.usikker_test = enheder, usikker
        laerer = Server((konf['laerer_adresse'], konf['laerer_port']), Laerer, tls=tls)
        servere.append(laerer)
        laerer.navn, laerer.tilstand, laerer.kode, laerer.apps, laerer.pause = 'laerer', tilstand, kode, konf['apps'], pause
        laerer.enheder = enheder
        laerer.update_administration = UpdateAdministration(args.data)
        laerer.release_feed = ReleaseFeed(args.data)
        hub.device_updates = DeviceUpdates(laerer.update_administration, laerer.release_feed)
        release_runner = ReleaseFeedRunner(laerer.release_feed, args.data / 'updates' / 'downloads').start()
        laerer.tls_status_paths = tls_status_paths
        laerer.certificate_snapshot = CertificateSnapshot(tls_status_paths)
        laerer.printer_snapshot = PrinterSnapshot(konf['apps'])
        laerer.makerspace = MakerspaceService(args.data / 'makerspace.sqlite3', overview=lambda: system_snapshot(laerer),
            printer_administration=printer_administration, laser_stations=laser_stations,
            laser_administration=laser_administration)
        for s in servere[:-1]:
            thread = threading.Thread(target=s.serve_forever, daemon=True)
            thread.start()
            running.append((s, thread))
        log.info('Elevhub kører: elever på %s (apps %s, hub %s), lærerpanel på %s:%s', konf['elev_adresse'],
                 ', '.join(f"{a['navn']} {a.get('port', '-')}" for a in konf['apps']), konf['hub_port'],
                 konf['laerer_adresse'], konf['laerer_port'])
        laerer_vaert = konf['laerer_adresse'] if konf['laerer_adresse'] not in ('0.0.0.0', '::') else 'localhost'
        print(f"Elevhub kører. Lærerpanel: {'https' if tls else 'http'}://{laerer_vaert}:{konf['laerer_port']}/", flush=True)
        try:
            servere[-1].serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        if release_runner is not None:
            release_runner.stop()
        for s, thread in running:
            s.shutdown()
            thread.join()
        for s in servere:
            s.server_close()
        if file_handler is not None:
            logging.getLogger().removeHandler(file_handler)
            file_handler.close()
        lock.close()


def main():
    parser = argparse.ArgumentParser(description='Elevhub-serveren')
    parser.add_argument('--konfiguration', type=Path, default=MAPPE / 'konfiguration.json')
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--nulstil-laererkode', action='store_true',
                        help='glem lærerkoden, så en ny kan vælges på serveren')
    args = parser.parse_args()
    run(args)

if __name__ == '__main__':
    main()
