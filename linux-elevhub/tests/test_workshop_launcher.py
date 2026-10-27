import http.client
import importlib.util
import json
from pathlib import Path
import threading
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

    def request(self, method='POST', path='/open', headers=None, body=None):
        h = {'Origin': 'null', 'Authorization': 'Bearer ' + 't' * 43, 'X-Elevhub-Computer': 'vaerksted-05'}
        h.update(headers or {})
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        c.request(method, path, body, h)
        r = c.getresponse()
        result = r.status, r.read()
        c.close()
        return result

    def test_valid_click_queues_one_fixed_action(self):
        self.assertEqual(self.request()[0], 202)
        self.assertEqual(self.request()[0], 202)
        self.assertEqual(self.server.requests.qsize(), 1)
        self.assertTrue(self.server.page_ready)

    def test_teacher_closure_or_pause_rejects_launch(self):
        self.open = False
        self.assertEqual(self.request()[0], 403)
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
    def test_old_entry_survives_until_page_loaded_and_launch_rechecks_policy(self):
        workshop, toolbar = Mock(), Mock()
        toolbar.konf = {'computer': 'fixture', 'ui_token': 't' * 43}
        workshop.aaben.return_value = True
        workshop.skaermpause.return_value = False
        server = M.make_server('fixture', 't' * 43, lambda: True, port=0)
        previous = workshop.vis_genvej
        try:
            with patch.object(M, 'make_server', return_value=server):
                M.attach(workshop, toolbar)
            previous.assert_called_once_with(False)
            workshop.knap.pack_forget.assert_not_called()
            server.page_ready = True
            workshop.vis_genvej(True)
            workshop.knap.pack_forget.assert_called_once()
            server.requests.put({'id': 'a' * 32, 'action': '/open'})
            server.available = lambda: False
            toolbar.rod.after.call_args.args[1]()
            workshop.start_cad.assert_not_called()
            server.requests.put({'id': 'b' * 32, 'action': '/open'})
            server.available = lambda: True
            toolbar.afslut_foer.side_effect = lambda action, reason, completed: (action(), completed({'state': 'completed'}))
            toolbar.rod.after.call_args.args[1]()
            workshop.start_cad.assert_called_once()
            self.assertEqual(server.results['b' * 32]['state'], 'completed')
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
