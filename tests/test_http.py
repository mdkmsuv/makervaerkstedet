import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.server import Application, Server, Handler
from app.core import Queue
from scripts.smoke_slice import cube


class HttpTests(unittest.TestCase):
    def test_print_block_and_teacher_unlock_over_http(self):
        queue = self.server.app.queue
        queue.demo = True
        queue.pause(True)
        headers = {'X-Filename': 'test.stl', 'X-Colour': 'any'}
        for number in range(9):
            headers['X-Request-ID'] = f'quota-http-request-{number}'
            self.assertEqual(self.request('/api/jobs', cube(), headers)[0], 201)
        self.assertEqual(self.request('/api/jobs', cube(), headers)[0], 201)
        headers['X-Request-ID'] = 'quota-http-request-extra'
        status, body = self.request('/api/jobs', cube(), headers)
        self.assertEqual(status, 429)
        self.assertIn('Computer', json.loads(body)['error'])
        access = json.loads(self.request('/api/state')[1])['print_access']
        self.assertFalse(access['allowed'])
        self.assertEqual(self.request('/api/print-access')[0], 401)
        admin = {'Authorization': 'Bearer ' + self.server.app.token}
        overview = json.loads(self.request('/api/print-access', headers=admin)[1])
        block_id = overview['blocked'][0]['id']
        self.assertNotIn(block_id.encode(), self.request('/api/state')[1])
        payload = json.dumps({'id':block_id}).encode()
        self.assertEqual(self.request('/api/print-access/unblock', payload)[0], 401)
        with self.assertRaises(HTTPError) as blocked:
            urlopen(Request(self.base + '/api/jobs', data=cube(), headers=headers), timeout=3)
        with blocked.exception as response:
            self.assertEqual(response.status, 429)
            self.assertTrue(1 <= int(response.headers['Retry-After']) <= 3600)
            self.assertEqual(json.loads(response.read())['retry_after'], int(response.headers['Retry-After']))
        self.assertEqual(self.request('/api/print-access/unblock', payload, admin)[0], 200)
        self.assertEqual(self.request('/api/jobs', cube(), headers)[0], 201)
        settings = b'{"trigger_count":30}'
        self.assertEqual(self.request('/api/print-access/settings', settings)[0], 401)
        status, body = self.request('/api/print-access/settings', settings, admin)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['settings']['trigger_count'], 30)

    def test_remote_client_cannot_spoof_source(self):
        handler = object.__new__(Handler)
        handler.client_address = ('203.0.113.11', 12345)
        handler.headers = {'X-Print-Source': '203.0.113.99', 'X-Forwarded-For': '203.0.113.99'}
        self.assertEqual(handler.submission_source(), 'ip:203.0.113.11')
        handler.client_address = ('127.0.0.1', 12345)
        self.assertEqual(handler.submission_source(), 'ip:203.0.113.99')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        (Path(self.temp.name) / 'laererkode.txt').write_text('old-installation-code')
        self.server=Server(('127.0.0.1',0),Handler)
        self.server.app=Application(self.temp.name,False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.thread.join()
        self.server.app.shutdown();self.server.app.queue.close();self.server.server_close()
        self.temp.cleanup()

    def request(self,path,data=None,headers=None):
        try:
            response=urlopen(Request(self.base+path,data=data,headers=headers or {}),timeout=3)
        except HTTPError as error:
            response=error
        with response:
            return response.status,response.read()

    def test_admin_is_protected_and_student_state_has_no_secrets(self):
        self.assertEqual(self.request('/api/settings')[0],401)
        self.assertEqual(self.request('/api/queue/pause',b'{"paused":true}')[0],401)
        status,body=self.request('/api/settings',headers={'Authorization':'Bearer '+self.server.app.token})
        self.assertEqual(status,200)
        status,body=self.request('/api/state')
        self.assertNotIn(self.server.app.token.encode(),body)
        self.assertNotIn(b'access_code',body)

    def test_maintenance_is_teacher_only_and_records_without_printer_commands(self):
        self.assertEqual(self.request('/api/maintenance')[0],401)
        self.assertEqual(self.request('/api/maintenance/complete',b'{"id":"test"}')[0],401)
        self.server.app.queue.configs['test'] = {'id':'test','name':'E1','model':'A1 mini'}
        headers = {'Authorization':'Bearer '+self.server.app.token}
        status,body = self.request('/api/maintenance',headers=headers)
        self.assertEqual(status,200)
        self.assertIsNone(json.loads(body)['printers'][0]['completed_on'])
        status,body = self.request('/api/maintenance/complete',b'{"id":"test"}',headers)
        self.assertEqual(status,200)
        self.assertIsNotNone(json.loads(body)['printers'][0]['completed_on'])
        self.assertEqual(self.server.app.drivers,{})
        self.assertEqual(self.server.app.queue.snapshot()['jobs'],[])
        self.assertEqual(self.request('/api/maintenance/complete',b'{"id":"unknown"}',headers)[0],400)

    def test_camera_images_and_actions_require_teacher_code(self):
        self.assertEqual(self.request('/api/printers/release',b'{"id":"test"}')[0],401)
        self.assertEqual(self.request('/api/camera/overview')[0],401)
        self.assertEqual(self.request('/api/camera/sample-storage')[0],401)
        self.assertEqual(self.request('/api/camera/image/test')[0],401)
        self.assertEqual(self.request('/api/camera/status/test')[0],401)
        for action in ('capture','capture-all','reference-all','check','reference','automatic','vision','validate-ai'):
            self.assertEqual(self.request('/api/camera/'+action,b'{"id":"test"}')[0],401)

    def test_retired_camera_configuration_and_archive_endpoints_are_unavailable(self):
        self.assertEqual(self.server.app.queue.configs, {})
        self.assertFalse(hasattr(self.server.app.cameras, 'training_samples'))
        headers = {'Authorization': 'Bearer ' + self.server.app.token}
        for endpoint in ('sample-storage', 'overview'):
            self.assertEqual(self.request('/api/camera/' + endpoint, headers=headers)[0], 404)
        for endpoint in ('automatic', 'vision', 'validate-ai', 'reference', 'check', 'capture-all', 'reference-all'):
            self.assertEqual(self.request('/api/camera/' + endpoint, b'{}', headers)[0], 404)

    def test_teacher_release_calls_queue_directly_without_camera_capture(self):
        with patch.object(self.server.app.queue, 'release_bed') as release, \
             patch.object(self.server.app.cameras, 'capture') as capture:
            status,body = self.request('/api/printers/release', b'{"id":"test"}',
                                      {'Authorization':'Bearer '+self.server.app.token})
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body),{'ok':True})
            release.assert_called_once_with('test')
            capture.assert_not_called()
        self.assertEqual(self.request('/training-samples/test/sample.jpg')[0],404)

    def test_aborted_print_can_be_resolved_by_teacher_then_plate_released(self):
        original_queue, original_cameras = self.server.app.queue, self.server.app.cameras
        queue = Queue(Path(self.temp.name)/'abort-case', [dict(id='mini', name='E1', model='A1 mini', enabled=True)])
        self.server.app.queue, self.server.app.cameras = queue, None
        try:
            queue.pause(True)
            job = queue.submit(cube(), 'test.stl', 'any', 'http-aborted-print-case')
            queue._change(job['id'], status='attention', printer_id='mini', remote_name='pv_test.gcode.3mf')
            queue.update_report('mini', state='FAILED', task='pv_test', error=0)
            payload = json.dumps({'id':job['id'],'result':'cancelled'}).encode()
            self.assertEqual(self.request('/api/jobs/resolve',payload)[0],401)
            headers = {'Authorization':'Bearer '+self.server.app.token}
            self.assertEqual(self.request('/api/printers/release',b'{"id":"mini"}',headers)[0],400)
            self.assertEqual(self.request('/api/jobs/resolve',payload,headers)[0],200)
            state = json.loads(self.request('/api/state')[1])
            self.assertEqual(state['jobs'][0]['status'],'cancelled')
            self.assertTrue(state['printers'][0]['can_release'])
            self.assertFalse(state['printers'][0]['bed_clear'])
            self.assertEqual(self.request('/api/printers/release',b'{"id":"mini"}',headers)[0],200)
            self.assertTrue(json.loads(self.request('/api/state')[1])['printers'][0]['bed_clear'])
        finally:
            self.server.app.queue, self.server.app.cameras = original_queue, original_cameras
            queue.close()

    def test_firmware_is_teacher_only_and_never_changes_queue_or_printers(self):
        self.assertEqual(self.request('/api/firmware')[0],401)
        self.assertEqual(self.request('/api/firmware/check',b'{}')[0],401)
        self.assertEqual(self.request('/api/firmware/acknowledge',b'{}')[0],401)
        checker = self.server.app.firmware
        checker.enabled = True
        checker.fetch = lambda: {'version':'01.08.01.00','published_on':'2026-06-03'}
        headers = {'Authorization':'Bearer '+self.server.app.token}
        status, body = self.request('/api/firmware/check',b'{}',headers)
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['unread'])
        status, body = self.request('/api/firmware/acknowledge',b'{}',headers)
        self.assertFalse(json.loads(body)['unread'])
        status, body = self.request('/api/firmware',headers=headers)
        self.assertEqual(json.loads(body)['latest']['version'],'01.08.01.00')
        self.assertEqual(self.server.app.drivers,{})
        self.assertEqual(self.server.app.queue.snapshot()['jobs'],[])

    def test_existing_installation_code_is_preserved(self):
        self.assertEqual(self.request('/api/settings',headers={'Authorization':'Bearer old-installation-code'})[0],200)
        self.assertEqual(self.request('/api/settings',headers={'Authorization':'Bearer wrong-code'})[0],401)

    def test_demo_requires_the_same_teacher_code(self):
        self.server.app.demo = True
        self.assertEqual(self.request('/api/settings')[0],401)
        self.assertEqual(self.request('/api/queue/pause',b'{"paused":true}')[0],401)
        self.assertEqual(self.request('/api/settings',headers={'Authorization':'Bearer '+self.server.app.token})[0],200)

    def test_teacher_code_is_not_exposed_to_browser_assets(self):
        for path in ('/', '/laerer', '/app.js', '/api/state', '/api/health'):
            with self.subTest(path=path):
                status,body=self.request(path)
                self.assertEqual(status,200)
                self.assertNotIn(self.server.app.token.encode(),body)
        self.assertEqual(self.request('/app/server.py')[0],404)

    def test_path_traversal_and_cross_site_requests_rejected(self):
        self.assertEqual(self.request('/../data/settings.json')[0],404)
        status,body=self.request('/api/jobs',cube(),{'Origin':'https://unrelated.invalid'})
        self.assertEqual(status,401)

    def test_real_mode_never_invents_printers(self):
        status,body=self.request('/api/state')
        data=json.loads(body)
        self.assertFalse(data['demo']);self.assertEqual(data['printers'],[])
        status,body=self.request('/api/jobs',cube(),{'X-Filename':'cube.stl','X-Request-ID':'test-request-123456','X-Colour':'any'})
        self.assertEqual(status,400)

    def test_root_and_viewer_are_served_locally(self):
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/app.js')[0],200)
        self.assertEqual(self.request('/style.css')[0],200)

    def test_discovery_is_teacher_only_cached_and_does_not_add_printers(self):
        found={'printers':[{'serial':'TESTPRINTER123','model':'A1 mini','host':'192.168.1.40','name':'A1 mini','connection':'cloud'}],'issues':[]}
        with patch('app.server.discover',return_value=found) as search:
            self.assertEqual(self.request('/api/printers/discover',b'{}')[0],401)
            search.assert_not_called()
            for _ in range(2):
                status,body=self.request('/api/printers/discover',b'{}',{'Authorization':'Bearer '+self.server.app.token})
                self.assertEqual(status,200)
                self.assertEqual(json.loads(body),found)
            search.assert_called_once_with()
            self.assertEqual(self.server.app.settings['printers'],[])

    def test_demo_never_contacts_network_discovery(self):
        self.server.app.demo=True
        with patch('app.server.discover') as search:
            status,_=self.request('/api/printers/discover',b'{}',{'Authorization':'Bearer '+self.server.app.token})
            self.assertEqual(status,400)
            search.assert_not_called()


if __name__=='__main__':unittest.main()
