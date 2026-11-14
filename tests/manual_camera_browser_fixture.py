"""Actual manual-camera HTTP/auth/queue with synthetic JPEGs and no hardware."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.server import Application, Handler, Server


def main():
    with tempfile.TemporaryDirectory(prefix='mkv-manual-camera-') as folder:
        root = Path(folder)
        # No access code means no printer driver is created. The only image
        # source is this generated JPEG; background hardware work is isolated.
        config = {'id': 'one', 'name': 'Isoleret testprinter', 'model': 'P1S',
                  'serial': 'SYNTHETIC', 'host': '192.0.2.10', 'enabled': True}
        (root / 'settings.json').write_text(json.dumps({'studio_path': 'not-used.exe', 'printers': [config]}), encoding='utf-8')
        image = io.BytesIO()
        Image.new('RGB', (640, 480), '#808080').save(image, 'JPEG')
        with patch('app.server.Worker'), patch('app.server.FirmwareChecks'):
            app = Application(root, demo=False)
        server = None
        worker = None
        try:
            app.cameras.capture_image = lambda *_: image.getvalue()
            app.queue.update_report('one', state='IDLE', task='', error=0)
            server = Server(('127.0.0.1', 0), Handler)
            server.app = app
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'code': app.token}), flush=True)
            sys.stdin.readline()
        finally:
            if server is not None:
                if worker is not None:
                    server.shutdown()
                    worker.join(5)
                server.server_close()
            app.shutdown()


if __name__ == '__main__':
    main()
