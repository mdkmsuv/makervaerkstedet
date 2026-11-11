import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

try:
    from app.laser_station import Station, create_station_server
    from app.laser_machines import get_machine
    _LASER_IMPORT_ERROR = None
except ModuleNotFoundError as error:
    Station = create_station_server = get_machine = None
    _LASER_IMPORT_ERROR = error


class _Overview(BaseHTTPRequestHandler):
    calls = 0
    payload = {}

    def do_GET(self):
        type(self).calls += 1
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(_LASER_IMPORT_ERROR is None, 'laserstation dependencies unavailable: %s' % _LASER_IMPORT_ERROR)
class LaserMakerspaceTests(unittest.TestCase):
    def test_station_overview_is_minimal_and_read_only(self):
        station = Station.__new__(Station)
        station.station_id = 'laser-fbb2'
        station.machine = get_machine('fbb2')
        station.beam_studio = Path('Beam Studio.exe')
        station.ghost = type('Ghost', (), {'executable': Path('flux_api.exe')})()
        server = create_station_server(station, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/station/overview') as response:
                result = json.load(response)
            self.assertEqual(result['items'][0]['id'], 'laser-fbb2')
            self.assertEqual(result['items'][0]['model'], 'fbb2')
            self.assertFalse(result['items'][0]['online'])
            self.assertNotIn('server', result['items'][0])
            self.assertNotIn('jobs', result['items'][0])
            self.assertNotIn('materialScene', result)
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
