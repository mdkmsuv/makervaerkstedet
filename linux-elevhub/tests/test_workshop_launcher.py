import http.client
import importlib.util
import json
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).parents[1] / 'ansible/roles/kiosk/files/workshop_launcher.py'
SPEC = importlib.util.spec_from_file_location('workshop_launcher_test', SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.open = True
        self.server = M.make_server('vaerksted-05', 't' * 43, lambda: self.open, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method='POST', path='/finish', headers=None, body=None):
        h = {'Origin': 'null', 'Authorization': 'Bearer ' + 't' * 43, 'X-Elevhub-Computer': 'vaerksted-05'}
        h.update(headers or {})
        h = {key: value for key, value in h.items() if value is not None}
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        c.request(method, path, body, h)
        r = c.getresponse()
        self.response_headers = dict(r.getheaders())
        result = r.status, r.read()
        c.close()
        return result

    def test_valid_click_queues_one_fixed_action(self):
        self.assertEqual(self.request()[0], 202)
        self.assertEqual(self.request()[0], 202)
        self.assertEqual(self.server.requests.qsize(), 1)
        self.assertTrue(self.server.page_ready)

    def test_native_open_is_gone_even_when_old_policy_allows_it(self):
        self.assertEqual(self.request(path='/open')[0], 410)
        self.assertTrue(self.server.requests.empty())

    def test_untrusted_origin_host_token_or_computer_cannot_launch(self):
        for headers in ({'Origin': 'https://example.com'}, {'Host': 'example.com'},
                        {'Authorization': 'Bearer wrong'}, {'X-Elevhub-Computer': 'vaerksted-07'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertTrue(self.server.requests.empty())

    def test_paths_and_payload_are_bounded(self):
        self.assertEqual(self.request(path='/run')[0], 404)
        self.assertEqual(self.request(body=b'run command')[0], 400)
        self.assertTrue(self.server.requests.empty())

    def test_health_probe_does_not_hide_old_page_shortcut(self):
        self.assertEqual(self.request('GET', '/status')[0], 200)
        self.assertFalse(self.server.page_ready)
        self.assertEqual(self.request('GET', '/status', {'X-Elevhub-Page': 'workshops-v1'})[0], 200)
        self.assertTrue(self.server.page_ready)
        self.assertTrue(self.server.requests.empty())

    def test_preflight_only_allows_expected_headers(self):
        headers = {'Access-Control-Request-Method': 'POST',
                   'Access-Control-Request-Headers': 'authorization,x-elevhub-computer'}
        self.assertEqual(self.request('OPTIONS', headers=headers)[0], 200)
        headers['Access-Control-Request-Headers'] = 'x-arbitrary-command'
        self.assertEqual(self.request('OPTIONS', headers=headers)[0], 403)

    def test_installed_http_home_preflights_and_authenticates_catalogue_and_finish(self):
        origin = 'http://127.0.0.1:8791'
        for path, method in (('/apps', 'GET'), ('/finish', 'POST'), ('/finish/status', 'GET')):
            with self.subTest(path=path):
                headers = {'Origin': origin, 'Access-Control-Request-Method': method,
                           'Authorization': None, 'X-Elevhub-Computer': None,
                           'Access-Control-Request-Headers': 'authorization,x-elevhub-computer'}
                self.assertEqual(self.request('OPTIONS', path, headers)[0], 200)
                self.assertEqual(self.response_headers.get('Access-Control-Allow-Origin'), origin)
        with tempfile.TemporaryDirectory() as directory:
            self.server.manifest_root = Path(directory)
            status, raw = self.request('GET', '/apps', {'Origin': origin})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw), {'apps': []})
            self.assertEqual(self.response_headers.get('Access-Control-Allow-Origin'), origin)
        status, raw = self.request(headers={'Origin': origin})
        self.assertEqual(status, 202)
        request = json.loads(raw)
        self.assertEqual(self.response_headers.get('Access-Control-Allow-Origin'), origin)
        self.assertEqual(self.server.requests.get_nowait(), {'id': request['id'], 'action': '/finish'})
        status, raw = self.request('GET', '/finish/status?id=' + request['id'], {'Origin': origin})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['state'], 'pending')
        self.assertEqual(self.response_headers.get('Access-Control-Allow-Origin'), origin)

    def test_http_home_cannot_bypass_host_token_computer_or_preflight_limits(self):
        for wrong in ({'Host': 'example.com'}, {'Authorization': 'Bearer wrong'},
                      {'X-Elevhub-Computer': 'another'}, {'Authorization': None},
                      {'X-Elevhub-Computer': None}, {'Origin': None}):
            with self.subTest(wrong=wrong):
                self.assertEqual(self.request(headers={'Origin': 'http://127.0.0.1:8791', **wrong})[0], 403)
        headers = {'Origin': 'http://127.0.0.1:8791', 'Access-Control-Request-Method': 'POST',
                   'Authorization': None, 'X-Elevhub-Computer': None,
                   'Access-Control-Request-Headers': 'authorization,x-elevhub-computer'}
        for wrong in ({'Host': 'example.com'}, {'Access-Control-Request-Method': 'DELETE'},
                      {'Access-Control-Request-Headers': 'x-arbitrary-command'}):
            with self.subTest(wrong=wrong):
                self.assertEqual(self.request('OPTIONS', headers={**headers, **wrong})[0], 403)
        self.assertEqual(self.request('OPTIONS', '/run', headers)[0], 403)
        self.assertTrue(self.server.requests.empty())

    def test_only_canonical_http_home_origin_is_trusted(self):
        origins = ['http://localhost:8791', 'https://127.0.0.1:8791',
                   'http://127.0.0.1:8791/', 'http://127.0.0.1:8791.evil.test',
                   'http://127.0.0.1:8791@evil.test', 'http://127.0.0.1:87910',
                   *('http://127.0.0.1:' + str(port) for port in (8790, 8792, 8795, 8796, 8797, 8798))]
        for origin in origins:
            with self.subTest(origin=origin):
                headers = {'Origin': origin, 'Access-Control-Request-Method': 'POST',
                           'Access-Control-Request-Headers': 'authorization,x-elevhub-computer'}
                self.assertEqual(self.request('OPTIONS', headers=headers)[0], 403)
                self.assertNotIn('Access-Control-Allow-Origin', self.response_headers)
                self.assertEqual(self.request(headers={'Origin': origin})[0], 403)
        self.assertTrue(self.server.requests.empty())

    def test_installed_apps_follow_manifest_removal_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            self.server.manifest_root = Path(directory)
            folder = Path(directory)/'laser'; folder.mkdir()
            manifest = {'schema':1,'id':'laser','navn':'Laser','version':'2.0.0',
                        'start':'http://127.0.0.1:8795/','offline':True,
                        'aflevering':'laser-submit','kraever_internet':False,'licens':'MIT'}
            file = folder/'app.json'; file.write_text(json.dumps(manifest))
            status, raw = self.request('GET', '/apps')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw), {'apps':[manifest]})
            file.unlink()
            self.assertEqual(json.loads(self.request('GET', '/apps')[1]), {'apps':[]})
            self.assertTrue(self.server.requests.empty())

    def test_app_catalog_rejects_invalid_manifest_and_untrusted_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            self.server.manifest_root = Path(directory)
            folder = Path(directory)/'laser'; folder.mkdir()
            (folder/'app.json').write_text('{"schema":1,"unexpected_field":"not in error"}')
            status, raw = self.request('GET', '/apps')
            self.assertEqual(status, 503)
            self.assertNotIn(b'unexpected_field', raw)
            for headers in ({'Origin':'https://example.com'}, {'Authorization':'Bearer wrong'},
                            {'Host':'example.com'}, {'X-Elevhub-Computer':'another'}):
                self.assertEqual(self.request('GET', '/apps', headers)[0], 403)

    def test_app_catalog_preflight_uses_existing_local_authentication(self):
        headers = {'Access-Control-Request-Method':'GET',
                   'Access-Control-Request-Headers':'authorization,x-elevhub-computer'}
        self.assertEqual(self.request('OPTIONS', '/apps', headers)[0], 200)
        self.assertEqual(self.response_headers.get('Access-Control-Allow-Origin'), 'null')
        self.assertEqual(self.request('POST', '/apps')[0], 404)

    def test_finish_is_available_when_teacher_closed_3d_and_has_pollable_status(self):
        self.open = False
        status, raw = self.request(path='/finish')
        self.assertEqual(status, 202)
        request = json.loads(raw)
        self.assertEqual(request['state'], 'pending')
        self.assertEqual(self.server.requests.get_nowait(), {'id': request['id'], 'action': '/finish'})
        status, raw = self.request('GET', '/finish/status?id=' + request['id'])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['state'], 'pending')

    def test_finish_requires_same_authentication_as_launch(self):
        self.assertEqual(self.request(path='/finish', headers={'Origin': 'https://example.com'})[0], 403)
        self.assertTrue(self.server.requests.empty())

    def test_native_project_target_is_delivered_once_only_to_start_page(self):
        import time
        self.server.navigation_target = {'page': 'cadoodle-projects', 'created': time.time()}
        status, raw = self.request('GET', '/status')
        self.assertIsNone(json.loads(raw)['navigation'])
        self.assertIsNotNone(self.server.navigation_target)
        status, raw = self.request('GET', '/status', {'X-Elevhub-Page': 'workshops-v1'})
        self.assertEqual(json.loads(raw)['navigation'], 'cadoodle-projects')
        self.assertIsNone(self.server.navigation_target)


class AdapterTests(unittest.TestCase):
    def test_finish_works_with_no_native_workshop(self):
        toolbar = Mock()
        toolbar.konf = {'computer': 'fixture', 'ui_token': 't' * 43}
        server = M.make_server('fixture', 't' * 43, lambda: True, port=0)
        try:
            with patch.object(M, 'make_server', return_value=server):
                M.attach(None, toolbar)
            server.requests.put({'id': 'a' * 32, 'action': '/finish'})
            toolbar.afslut_foer.side_effect = lambda action, reason, completed: (action(), completed({'state': 'completed'}))
            toolbar.rod.after.call_args.args[1]()
            self.assertEqual(server.results['a' * 32]['state'], 'completed')
            self.assertFalse(server.available())
        finally:
            server.shutdown()
            server.server_close()

    def test_port_conflict_keeps_existing_toolbar_usable(self):
        workshop, toolbar = Mock(), Mock()
        toolbar.konf = {'computer': 'fixture', 'ui_token': 't' * 43}
        previous = workshop.vis_genvej
        with patch.object(M, 'make_server', side_effect=OSError('port busy')):
            self.assertIsNone(M.attach(workshop, toolbar))
        self.assertIs(workshop.vis_genvej, previous)


if __name__ == '__main__':
    unittest.main()
