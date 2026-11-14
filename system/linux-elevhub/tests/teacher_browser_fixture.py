"""Real teacher HTTP/store fixture; ephemeral loopback port and disposable data.

No pupil registrations, hardware, installed pilot, or administrator actions.
The parent closes stdin to drain the server and remove its own temporary data.
"""
from pathlib import Path
import json
import sys
import tempfile
import threading

HUB = Path(__file__).resolve().parents[1] / 'server' / 'elevhub'
sys.path.insert(0, str(HUB))
from elevhub import Laerer, Server, system_snapshot
from makerspace_api import MakerspaceService
from pause import PauseStore
from sikkerhed import Enhedsregister
from tilstand import Laererkode, Tilstand


def main():
    with tempfile.TemporaryDirectory(prefix='makervaerksted-teacher-') as folder:
        root = Path(folder)
        (root / 'register.json').write_text('{"enheder": {}}', encoding='utf-8')
        (root / 'school.json').write_text(json.dumps({'school_name': 'Rejseskolen', 'theme': 'green'}), encoding='utf-8')
        server = Server(('127.0.0.1', 0), Laerer)
        server.navn = 'laerer'
        server.apps = [{'id': 'internet', 'navn': 'Internet', 'type': 'browser'}]
        server.tilstand = Tilstand(root / 'state', server.apps)
        server.enheder = Enhedsregister(root / 'register.json')
        server.pause = PauseStore(root)
        server.kode = Laererkode(root)
        server.kode.vaelg('teacher-fixture-123')
        server.makerspace = MakerspaceService(root / 'makerspace.sqlite3', overview=lambda: system_snapshot(server))
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        print(json.dumps({'url': f'http://127.0.0.1:{server.server_address[1]}'}), flush=True)
        try:
            sys.stdin.readline()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    main()
