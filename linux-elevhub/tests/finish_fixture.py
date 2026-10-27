"""Isolated services for project lifecycle tests; never the pupil's home/store."""
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'linux-elevhub/projekter')]
from lokal import make_server
from projektarkiv import Projektarkiv
from app.laser_local import create_local_servers

with tempfile.TemporaryDirectory(prefix='elevhub-finish-test-') as temp:
    store = Projektarkiv(Path(temp) / 'archive')
    local = make_server(0, store)
    laser, folie = create_local_servers('http://127.0.0.1:1', laser_port=0, folie_port=0, projects_port=local.server_port)
    local.workshop_urls.update(laser=f'http://127.0.0.1:{laser.port}/laser.html', folie=f'http://127.0.0.1:{folie.port}/folie.html')
    servers = [local, laser, folie]
    app_ports = {}
    if '--adapters' in sys.argv:
        sources = {
            'scratch': Path(json.loads((ROOT/'linux-elevhub/server/scratch/installation.json').read_text(encoding='utf-8-sig'))['outputPath']),
            'datatraener': Path(json.loads((ROOT/'linux-elevhub/server/datatraener/installation.json').read_text(encoding='utf-8-sig'))['outputPath']),
            'kredsloeb': ROOT/'linux-elevhub/server/kredsloeb',
        }
        for name, source in sources.items():
            server = make_server(0, store, app=name, assets=source, sessions=local.workshop_sessions)
            app_ports[name] = server.server_port
            servers.append(server)
        for server in servers:
            server.projekt_apps = [{'id':name,'port':port} for name,port in app_ports.items()]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print(json.dumps({'local':local.server_port, 'laser':laser.port, 'folie':folie.port, **app_ports}), flush=True)
    try:
        sys.stdin.readline()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
