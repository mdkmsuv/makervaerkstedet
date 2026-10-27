import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from workshop_sessions import WorkshopSessions
from lokal import make_server
from projektarkiv import Projektarkiv


class Handoff(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sessions = WorkshopSessions(lambda: self.now)

    def poll(self, client, dirty=True):
        return self.sessions.poll({'client':client * 32, 'kind':'laser', 'dirty':dirty})

    def test_two_editors_both_must_confirm(self):
        self.poll('a'); self.poll('b')
        r = self.sessions.request()
        self.assertEqual(r['state'], 'pending')
        self.sessions.acknowledge({'client':'a'*32, 'id':r['id'], 'state':'completed'})
        self.assertEqual(self.sessions.status(r['id'])['state'], 'pending')
        self.sessions.acknowledge({'client':'b'*32, 'id':r['id'], 'state':'cancelled'})
        self.assertEqual(self.sessions.status(r['id'])['state'], 'cancelled')
        self.assertTrue(self.sessions.clients['b'*32]['active'])

    def test_late_reply_after_native_cancel_never_completes(self):
        self.poll('a'); r = self.sessions.request()
        self.sessions.cancel(r['id'])
        self.sessions.acknowledge({'client':'a'*32, 'id':r['id'], 'state':'completed'})
        self.assertEqual(self.sessions.status(r['id'])['state'], 'cancelled')
        self.assertTrue(self.sessions.clients['a'*32]['active'])

    def test_unknown_editor_cannot_ack_other_work(self):
        self.poll('a'); r = self.sessions.request()
        with self.assertRaises(PermissionError):
            self.sessions.acknowledge({'client':'b'*32, 'id':r['id'], 'state':'completed'})

    def test_no_editor_and_saved_inactive_editor_complete(self):
        self.assertEqual(self.sessions.request()['state'], 'completed')
        self.poll('a', False); r = self.sessions.request()
        self.sessions.acknowledge({'client':'a'*32, 'id':r['id'], 'state':'completed'})
        self.assertEqual(self.sessions.request()['state'], 'completed')

    def test_silent_dirty_editor_blocks_navigation(self):
        self.poll('a'); self.now = 31
        self.assertEqual(self.sessions.request()['state'], 'error')
        self.poll('a')
        self.assertEqual(self.sessions.request()['state'], 'pending')


class SessionHttp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_server(0, Projektarkiv(Path(self.temp.name)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.temp.cleanup()

    def call(self, action, data, headers):
        req = Request(self.origin+'/_elevhub/session/'+action, data=json.dumps(data).encode(), headers={'Content-Type':'application/json', **headers})
        with urlopen(req) as response: return json.load(response)

    def test_native_and_browser_contract(self):
        browser = {'Origin':self.origin, 'X-Elevhub-Session':'1'}
        self.call('poll', {'client':'a'*32, 'kind':'scratch', 'dirty':True}, browser)
        r = self.call('request', {}, {'X-Elevhub-Navigation':'1'})
        self.assertEqual(r['state'], 'pending')
        self.call('ack', {'client':'a'*32, 'id':r['id'], 'state':'cancelled'}, browser)
        with urlopen(self.origin+'/_elevhub/session/status?id='+r['id']) as response:
            self.assertEqual(json.load(response)['state'], 'cancelled')

    def test_forged_web_origin_and_unmarked_native_are_rejected(self):
        for headers in ({}, {'Origin':'https://evil.example', 'X-Elevhub-Session':'1'},
                        {'Origin':'null', 'X-Elevhub-Navigation':'1'}):
            with self.assertRaises(HTTPError) as error: self.call('request', {}, headers)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_project_names_never_appear_in_handoff(self):
        data = self.call('poll', {'client':'a'*32, 'kind':'laser', 'dirty':True, 'name':'Private name'}, {'Origin':self.origin, 'X-Elevhub-Session':'1'})
        self.assertNotIn('Private name', json.dumps(data))
        self.assertNotIn('Private name', json.dumps(self.server.workshop_sessions.clients))


if __name__ == '__main__': unittest.main()
