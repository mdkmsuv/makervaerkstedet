"""The component's MIT endpoint must not serve the aggregate GPL licence."""
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import elevhub


class ComponentLicenseTests(unittest.TestCase):
    def test_real_teacher_endpoint_prefers_component_notice(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'hub').mkdir()
            (root / 'licenses').mkdir()
            (root / 'LICENSE').write_text('GNU GPL aggregate notice', encoding='utf-8')
            mit = b'MIT License\nCopyright test fixture\n'
            (root / 'licenses/Elevhub-LICENSE.txt').write_bytes(mit)
            handler = type('QuietTeacher', (elevhub.Laerer,), {'log_message': lambda *args: None})
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(elevhub, 'MAPPE', root / 'hub'):
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    connection.request('GET', '/LICENSE')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), mit)
                    self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                    connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)
