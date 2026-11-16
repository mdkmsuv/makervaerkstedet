"""Real local shelf/editors, disposable pupil data, no school server or queue."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
sys.path[:0] = [str(ROOT), str(ROOT / 'system'), str(ROOT / 'system/linux-elevhub/projekter')]
from lokal import Handler, make_server
from projektarkiv import Projektarkiv
from workshop_sessions import WorkshopSessions


class AppOverlay(Handler):
    def do_GET(self):
        route = urlsplit(self.path).path
        if (self.server.app == 'scratch' and route in ('/creative/creative-app.js', '/creative/autosave.js')
                and self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'):
            self.svar(200, (ROOT / 'apps/scratch/web' / Path(route).name).read_bytes(), 'text/javascript; charset=utf-8')
        else:
            super().do_GET()


def main():
    scratch = Path(os.environ['ELEVHUB_TEST_SCRATCH_ASSETS'])
    assert (scratch / 'index.html').is_file(), 'Supply existing read-only real Scratch assets'
    with tempfile.TemporaryDirectory(prefix='mkv-autosave-editor-') as temp, contextlib.ExitStack() as cleanup:
        apps = [json.loads((ROOT / 'apps' / app / 'app.json').read_bytes()) for app in ('scratch', '3dprint')]
        store = Projektarkiv(Path(temp) / 'Projekter', apps=apps)
        sessions = WorkshopSessions(kinds={'scratch', '3dprint'})
        servers, urls = [], {}
        shelf = make_server(0, store, apps=apps, sessions=sessions, workshops={})
        servers.append(shelf)
        for app in apps:
            app['assets'] = str(scratch if app['id'] == 'scratch' else ROOT / 'apps/3dprint/web')
            server = make_server(0, store, app=app['id'], assets=app['assets'], apps=apps,
                sessions=sessions, descriptor=app, workshops={}, shared_root=ROOT / 'system/web')
            server.RequestHandlerClass = AppOverlay
            app['port'] = server.server_port
            app['start'] = f"http://127.0.0.1:{server.server_port}/" + ('model3d.html' if app['id'] == '3dprint' else '')
            urls[app['id']] = app['start']
            servers.append(server)
        for server in servers:
            server.workshop_urls = {app['id']: app['start'] for app in apps}
            cleanup.callback(server.server_close)
            cleanup.callback(server.shutdown)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print(json.dumps({'shelf':f'http://127.0.0.1:{shelf.server_port}/_elevhub/projekter/', **urls}), flush=True)
        sys.stdin.readline()


if __name__ == '__main__':
    main()
