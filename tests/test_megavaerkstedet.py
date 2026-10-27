import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.megavaerkstedet import print_url, render
from app import laser_station

ROOT = Path(__file__).resolve().parent.parent


class MegaTests(unittest.TestCase):
    def test_address_validation(self):
        for value in ('javascript:alert(1)', 'file:///test', 'http://u:p@localhost/',
                      'https://localhost/#x', 'https://localhost/?token=x', 'https://localhost:bad',
                      'https://localhost/;x', 'https://localhost/\n', 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                print_url(value)
        self.assertEqual(print_url('http://print.example:8765/laerer'), 'http://print.example:8765/laerer')
        self.assertEqual(print_url(''), '')

    def test_render_keeps_installed_laser_body_and_does_not_launch_script(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'megavaerkstedet.json'
            config.write_text(json.dumps({'print_url': 'http://print.example:8765/laerer'}))
            page, policy = render(ROOT / 'web', config)
            self.assertIn('id="login-form"', page)
            self.assertEqual(page.count('id="login-form"'), 1)
            self.assertIn('Under udvikling', page)
            self.assertIn('frame-src http://print.example:8765;', policy)
            self.assertNotIn('<script src="/laserstation.js"', page)
            self.assertNotIn('<!--LASER_BODY-->', page)
            self.assertNotIn('<!--MEGA_SETTINGS-->', page)

    def test_missing_or_invalid_configuration_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'megavaerkstedet.json'
            page, policy = render(ROOT / 'web', config)
            self.assertIn('"printUrl": ""', page)
            self.assertIn("frame-src 'none';", policy)
            config.write_text('{broken')
            page, _ = render(ROOT / 'web', config)
            self.assertIn('skal kontrolleres', page)

    def test_only_new_route_has_print_frame_policy_and_host_checks_remain(self):
        class FakeStation:
            def state(self):
                return {'loggedIn': False, 'server': 'http://127.0.0.1:1'}
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'megavaerkstedet.json'
            config.write_text(json.dumps({'print_url': 'http://print.example:8765'}))
            original = laser_station.render_megavaerkstedet
            with patch.object(laser_station, 'render_megavaerkstedet', side_effect=lambda web: original(web, config)):
                server = laser_station.create_station_server(FakeStation(), 0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    for route in ('/', '/megavaerkstedet.html', '/megavaerkstedet.js', '/megavaerkstedet.css', '/api/station/state'):
                        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
                        conn.request('GET', route)
                        response = conn.getresponse()
                        self.assertEqual(response.status, 200, route)
                        policy = response.getheader('Content-Security-Policy')
                        if route == '/megavaerkstedet.html':
                            self.assertIn('frame-src http://print.example:8765;', policy)
                        else:
                            self.assertNotIn('frame-src', policy)
                        self.assertIn("frame-ancestors 'none'", policy)
                        response.read(); conn.close()
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
                    conn.request('GET', '/megavaerkstedet.html', headers={'Host': 'wrong.example'})
                    response = conn.getresponse(); self.assertEqual(response.status, 403)
                    response.read(); conn.close()
                finally:
                    server.shutdown(); thread.join(); server.server_close()


if __name__ == '__main__':
    unittest.main()
