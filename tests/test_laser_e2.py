"""Focused E2 regressions for the local laser/foil workshop gateway."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.laser_local import create_local_servers
from app.laser_server import create_server


class _ProjectService(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<html><head></head><body>project picker</body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; frame-ancestors http://upstream.invalid")
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _HealthService(BaseHTTPRequestHandler):
    inbox = True

    def do_GET(self):
        body = json.dumps({'surface': 'student', 'inbox': self.inbox}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class LaserE2GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.central = create_server(self.temp.name, '127.0.0.1', 0,
                                     code='e2-test-code', surface='student')
        threading.Thread(target=self.central.serve_forever, daemon=True).start()
        self.addCleanup(self.central.server_close)
        self.addCleanup(self.central.shutdown)

    def add_server_cleanup(self, server):
        # ThreadingHTTPServer owns worker sockets; shutdown must run before
        # server_close so every E2 case leaves no listening _folie/server.
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def request(self, server, method, route):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        connection.request(method, route)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_local_health_reflects_unreachable_central(self):
        # Bind and release a port so no service can answer the health probe.
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        laser, folie = create_local_servers(f'http://127.0.0.1:{port}', laser_port=0, folie_port=0)
        threading.Thread(target=laser.serve_forever, daemon=True).start()
        threading.Thread(target=folie.serve_forever, daemon=True).start()
        self.add_server_cleanup(laser)
        self.add_server_cleanup(folie)
        status, _headers, body = self.request(laser, 'GET', '/api/laser/health')
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {'surface': 'student', 'inbox': False})

    def test_inbox_health_switches_with_central_state(self):
        central = HTTPServer(('127.0.0.1', 0), _HealthService)
        threading.Thread(target=central.serve_forever, daemon=True).start()
        self.add_server_cleanup(central)
        laser, folie = create_local_servers(
            f'http://127.0.0.1:{central.server_port}', laser_port=0, folie_port=0)
        threading.Thread(target=laser.serve_forever, daemon=True).start()
        threading.Thread(target=folie.serve_forever, daemon=True).start()
        self.add_server_cleanup(laser)
        self.add_server_cleanup(folie)
        status, _headers, body = self.request(laser, 'GET', '/api/laser/health')
        self.assertEqual((status, json.loads(body)), (200, {'surface': 'student', 'inbox': True}))
        _HealthService.inbox = False
        self.addCleanup(setattr, _HealthService, 'inbox', True)
        status, _headers, body = self.request(laser, 'GET', '/api/laser/health')
        self.assertEqual((status, json.loads(body)), (200, {'surface': 'student', 'inbox': False}))
        _HealthService.inbox = True
        status, _headers, body = self.request(laser, 'GET', '/api/laser/health')
        self.assertEqual((status, json.loads(body)), (200, {'surface': 'student', 'inbox': True}))

    def test_project_proxy_uses_single_same_origin_csp(self):
        projects = HTTPServer(('127.0.0.1', 0), _ProjectService)
        threading.Thread(target=projects.serve_forever, daemon=True).start()
        self.add_server_cleanup(projects)
        laser, folie = create_local_servers(
            f'http://127.0.0.1:{self.central.server_port}', laser_port=0, folie_port=0,
            projects_port=projects.server_port)
        threading.Thread(target=laser.serve_forever, daemon=True).start()
        threading.Thread(target=folie.serve_forever, daemon=True).start()
        self.add_server_cleanup(laser)
        self.add_server_cleanup(folie)
        status, headers, body = self.request(laser, 'GET', '/_elevhub/projekter/?pick=laser')
        self.assertEqual(status, 200)
        self.assertIn(b'project picker', body)
        policy = headers['Content-Security-Policy']
        self.assertIn("frame-ancestors 'self'", policy)
        self.assertNotIn('upstream.invalid', policy)


if __name__ == '__main__':
    unittest.main()
