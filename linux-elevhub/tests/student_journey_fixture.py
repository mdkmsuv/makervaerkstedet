"""Real loopback product services, ephemeral ports and a disposable pupil store.

Installed third-party assets are served read-only. No active pilot, printer,
browser profile, school configuration or pupil directory is accessed.
"""
import json
import os
import contextlib
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'linux-elevhub/projekter'), str(ROOT/'linux-elevhub/server/elevhub')]
from lokal import make_server, Navigation, Handler as LocalHandler
from projektarkiv import Projektarkiv
from app.laser_local import create_local_servers, ForwardingInbox
from app.laser_server import create_server
from app.server import Application, Server as PrintServer, Handler as PrintHandler
from elevhub import Server, Indgang
from regler import laser as laser_rules
from sikkerhed import tls_kontekst
from tilstand import Tilstand
import kiosk_browser_fixture as kiosk


class StyledAssets(LocalHandler):
    """Serve only the small output of the real versioned integration overlay.

    All upstream bundles still go through the normal loopback asset handler.
    This avoids mutating installed assets or copying an entire build for tests.
    """
    def do_GET(self):
        relative = urlsplit(self.path).path.lstrip('/') or 'index.html'
        target = (self.server.overlay / relative).resolve()
        if (self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}' and
                target.is_relative_to(self.server.overlay) and target.is_file()):
            import mimetypes
            data = target.read_bytes()
            if target.name == 'index.html':
                from lokal import BRIDGE
                data = data.replace(b'</head>', (BRIDGE+'</head>').encode())
                self.server.navigation.visit(self.server.app)
            self.svar(200, data, mimetypes.guess_type(target.name)[0])
            return
        super().do_GET()


def main():
    with tempfile.TemporaryDirectory(prefix='makervaerksted-student-') as temp:
        root = Path(temp)
        store, navigation = Projektarkiv(root/'projects'), Navigation()
        local = make_server(0, store, navigation=navigation)
        laser, folie = create_local_servers('http://127.0.0.1:1', laser_port=0, folie_port=0, projects_port=local.server_port)
        print_app = Application(root/'print', False)
        printer = PrintServer(('127.0.0.1', 0), PrintHandler,
                              project_origins=[f'http://127.0.0.1:{local.server_port}'])
        printer.app = print_app
        central = create_server(root/'laser', port=0,
                                project_origins=[f'http://127.0.0.1:{local.server_port}'])
        teacher = create_server(root/'laser', port=0, surface='teacher', inbox=central.inbox)
        laser.inbox = folie.inbox = ForwardingInbox(f'http://127.0.0.1:{central.server_port}')
        cert, key = root/'test.crt', root/'test.key'
        openssl = shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
        subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-keyout', str(key), '-out', str(cert),
                        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       capture_output=True, check=True, timeout=30)
        gateway = Server(('127.0.0.1', 0), Indgang,
                         tls=tls_kontekst({'tls': {'certifikat':str(cert), 'noegle':str(key)}}, root))
        app = {'id':'laser', 'navn':'Laser', 'standard_aaben':True, 'regler':'laser'}
        gateway.navn, gateway.app, gateway.regel = 'laser', app, laser_rules
        gateway.bagved, gateway.tilstand = central.server_address, Tilstand(root/'hub', [app])
        workshops = {'laser':f'http://127.0.0.1:{laser.port}/laser.html',
                     'folie':f'http://127.0.0.1:{folie.port}/folie.html',
                     '3dprint':f'http://127.0.0.1:{printer.server_port}/'}
        if os.environ.get('ELEVHUB_CENTRAL_LASER_IMPORT') == '1':
            workshops['laser'] = f'http://school.example.test:{central.server_port}/laser.html'
        sources = {'kredsloeb':ROOT/'linux-elevhub/server/kredsloeb',
                   'microbit':ROOT/'linux-elevhub/server/microbit-controller'}
        for name in ('scratch', 'datatraener'):
            record = json.loads((ROOT/f'linux-elevhub/server/{name}/installation.json').read_text(encoding='utf-8-sig'))
            sources[name] = Path(record['outputPath'])
        servers = [local, laser, folie, printer, central, teacher, gateway]
        urls = {'projekter':f'http://127.0.0.1:{local.server_port}',
                'print':workshops['3dprint'].rstrip('/'), 'laser':workshops['laser'],
                'folie':workshops['folie'], 'design':f'http://127.0.0.1:{central.server_port}',
                'gateway':f'https://127.0.0.1:{gateway.server_port}/'}
        urls['laserTeacher'] = f'http://127.0.0.1:{teacher.server_port}'
        urls['laserTeacherCode'] = central.inbox.code
        untrusted_assets = root/'untrusted-parent'
        untrusted_assets.mkdir()
        (untrusted_assets/'index.html').write_text(
            '<html><head></head><body><script>window.received=[];'
            'addEventListener("message",e=>received.push(e.data));</script>'
            f'<iframe id="blocked" src="{urls["print"]}/?elevhub_import=1" '
            'onload="this.contentWindow.postMessage({type:\'elevhub-file-hello\',request:\'untrusted\'},\'*\')"></iframe>'
            '</body></html>', encoding='utf-8')
        untrusted = make_server(0, store, app='untrusted', assets=untrusted_assets, workshops=workshops)
        servers.append(untrusted)
        urls['untrusted'] = f'http://127.0.0.1:{untrusted.server_port}'
        apps = []
        for name, source in sources.items():
            if not (source/'index.html').is_file():
                raise FileNotFoundError(f'Real {name} assets missing: {source}')
            server = make_server(0, store, app=name, assets=source, navigation=navigation,
                                 sessions=local.workshop_sessions, workshops=workshops)
            if name == 'datatraener':
                # The retained upstream adapter build predates the school shell.
                # Apply the maintained real integration to a tiny overlay only.
                overlay = root/'styled-datatraener'
                overlay.mkdir()
                shutil.copy2(source/'index.html', overlay/'index.html')
                spec = importlib.util.spec_from_file_location('school_style', ROOT/'linux-elevhub/server/style-workshop.py')
                style = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(style)
                with contextlib.redirect_stdout(io.StringIO()):
                    style.apply(overlay, name)
                server.overlay = overlay.resolve()
                server.RequestHandlerClass = StyledAssets
            servers.append(server)
            apps.append({'id':name, 'port':server.server_port})
            urls[name] = f'http://127.0.0.1:{server.server_port}'
        for server in servers:
            server.projekt_apps = apps
            server.workshop_urls = workshops
        kiosk.Handler.mode = 'down'
        hub = kiosk.ThreadingHTTPServer(('127.0.0.1',0), kiosk.Handler)
        kiosk.PORT = hub.server_port
        urls['hub'] = f'http://127.0.0.1:{hub.server_port}'
        servers.append(hub)
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print(json.dumps(urls), flush=True)
        try:
            sys.stdin.readline()
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            print_app.shutdown()


if __name__ == '__main__':
    main()
