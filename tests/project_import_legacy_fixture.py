"""Small real design servers for mixed-revision receiver compatibility checks."""
from pathlib import Path
import json
import sys
import tempfile
import threading
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.laser_server import Handler, create_server


class LegacyHandler(Handler):
    def do_GET(self):
        if urlsplit(self.path).path == '/project-import-config.js':
            return self.reply(404, {'error': 'Siden findes ikke.'})
        return super().do_GET()


def main():
    with tempfile.TemporaryDirectory(prefix='makervaerksted-legacy-import-') as folder:
        root = Path(folder)
        # Actual default production CSP, but the older missing endpoint.
        legacy = create_server(root / 'legacy', port=0)
        legacy.RequestHandlerClass = LegacyHandler
        disabled = create_server(root / 'disabled', port=0, project_origins=[])
        custom = create_server(root / 'custom', port=0, project_origins=['http://127.0.0.1:8792'])
        servers = {'legacy': legacy, 'disabled': disabled, 'custom': custom}
        workers = []
        for server in servers.values():
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            workers.append(worker)
        print(json.dumps({name: f'http://127.0.0.1:{server.server_port}' for name, server in servers.items()}), flush=True)
        try:
            sys.stdin.readline()
        finally:
            for server, worker in zip(servers.values(), workers):
                server.shutdown()
                worker.join()
                server.server_close()


if __name__ == '__main__':
    main()
