"""Exercise the IoT page over TLS and the real loopback print bridge."""
import http.client
import json
from pathlib import Path
import ssl
import tempfile
import threading
from types import SimpleNamespace
import unittest

from app.core import Queue
from app.server import Handler, Server as PrintServer
from tests import test_laser_station_gateway as gateway
from makerspace_printers import PrinterAdministration


@unittest.skipUnless(gateway.OPENSSL, 'Test certificate needs OpenSSL')
class PlateGatewayTests(unittest.TestCase):
    def test_network_scope_and_physical_confirmation(self):
        gateway.LaserStationGatewayTests.setUpClass()
        self.addCleanup(gateway.LaserStationGatewayTests.tearDownClass)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue = Queue(root, [{'id': 'p1', 'name': 'Testprinter', 'model': 'P1S', 'enabled': True}])
            key = 'mkbridge_' + 'a' * 43
            keypath = root / 'bridge.key'
            keypath.write_text(key, encoding='ascii')
            print_server = PrintServer(('127.0.0.1', 0), Handler)
            print_server.app = SimpleNamespace(queue=queue, printer_bridge=SimpleNamespace(token=key))
            hub = gateway.Server(('127.0.0.1', 0), gateway.QuietTeacher, tls=gateway.LaserStationGatewayTests.tls)
            hub.tilstand = SimpleNamespace(mappe=root)
            hub.makerspace = SimpleNamespace(printers=PrinterAdministration(
                {'backend': f'127.0.0.1:{print_server.server_port}', 'key_file': keypath.name}, root))
            threads = [threading.Thread(target=server.serve_forever) for server in (print_server, hub)]
            for thread in threads:
                thread.start()
            context = ssl.create_default_context(cafile=str(gateway.LaserStationGatewayTests.cert))
            origin = f'https://localhost:{hub.server_port}'

            def call(path, body=None, **headers):
                connection = http.client.HTTPSConnection('localhost', hub.server_port, context=context, timeout=4)
                try:
                    connection.request('GET' if body is None else 'POST', path,
                                       None if body is None else json.dumps(body).encode(),
                                       {'Content-Type': 'application/json', **headers})
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            try:
                queue.update_report('p1', state='IDLE', task='', error=0)
                network_file = root / 'pladenet.json'
                self.assertEqual(call('/ryd-plade')[0], 403)
                network_file.write_text('{"cidr":"192.0.2.0/24"}')
                self.assertEqual(call('/api/printplader', **{'X-Forwarded-For': '192.0.2.4'})[0], 403)
                network_file.write_text('{"cidr":"127.0.0.1/32"}')
                self.assertEqual(call('/ryd-plade')[0], 200)
                code, body = call('/api/printplader')
                self.assertEqual(code, 200)
                plate, = json.loads(body)['plates']
                request = {'id': plate['id'], 'token': plate['token']}
                self.assertEqual(call('/api/printplader', request)[0], 403)
                self.assertEqual(call('/api/printplader', request, Origin=origin)[0], 200)
                self.assertEqual(call('/api/printplader', request, Origin=origin)[0], 409)
                self.assertEqual(json.loads(call('/api/printplader')[1])['plates'][0]['status'], 'clear')
            finally:
                for server in (hub, print_server):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join()
                queue.close()
