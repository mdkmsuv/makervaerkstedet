import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


FIL = Path(__file__).parents[1] / 'ansible/roles/kiosk/files/elevbjaelke.py'
SPEC = importlib.util.spec_from_file_location('elevbjaelke', FIL)
ELEVBAELKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ELEVBAELKE)


class AnvendtVersionTest(unittest.TestCase):
    NU = 1_700_000_000

    def version(self, data):
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as fil:
            json.dump(data, fil)
            sti = fil.name
        try:
            return ELEVBAELKE.anvendt_version(sti, nu=self.NU)
        finally:
            Path(sti).unlink()

    def test_gyldig_nu_og_15_sekunder_gammel(self):
        self.assertEqual(self.version({'version': 7, 'tid': self.NU}), 7)
        self.assertEqual(self.version({'version': 7, 'tid': self.NU - 15}), 7)

    def test_16_sekunder_gammel_eller_fremtidig_er_ugyldig(self):
        self.assertEqual(self.version({'version': 7, 'tid': self.NU - 16}), 0)
        self.assertEqual(self.version({'version': 7, 'tid': self.NU + 1}), 0)

    def test_manglende_eller_defekt_fil_er_ugyldig(self):
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as fil:
            sti = fil.name
            fil.write('{"version": 7,')
        try:
            self.assertEqual(ELEVBAELKE.anvendt_version(sti, nu=self.NU), 0)
        finally:
            Path(sti).unlink()
        self.assertEqual(ELEVBAELKE.anvendt_version('findes-ikke.json', nu=self.NU), 0)

    def test_forkerte_datatyper_og_ikke_positive_version_er_ugyldig(self):
        for version in (True, 1.0, '7', 0, -1, None):
            with self.subTest(version=version):
                self.assertEqual(self.version({'version': version, 'tid': self.NU}), 0)
        for tid in (True, '1700000000', None, float('inf'), float('nan'), 10**1000):
            with self.subTest(tid=tid):
                self.assertEqual(self.version({'version': 7, 'tid': tid}), 0)


class BjaelkeKontaktTest(unittest.TestCase):
    def test_netfejl_aabner_offline_mode(self):
        bar = ELEVBAELKE.Bjaelke.__new__(ELEVBAELKE.Bjaelke)
        bar.konf = {'computer': 'pc-1', 'hub': 'https://hub:8773', 'ui_token': 'ui-token',
                    'ca_fil': '/ca.pem', 'internet_app': 'internet'}
        bar.internet_aaben = True
        bar.server_status = 'online'
        bar.rod = type('Root', (), {'after': lambda self, _delay, callback: callback()})()
        class Overlay:
            def acknowledgement(self):
                return None
            def offline(self):
                self.called = True
        bar.pause_overlay = Overlay()
        with patch.object(ELEVBAELKE, 'usb_noegler', return_value=[]), \
                patch.object(ELEVBAELKE, 'hent_json', side_effect=OSError('forbindelse')):
            bar._meld()
        self.assertTrue(bar.internet_aaben)
        self.assertEqual(bar.server_status, 'offline')
        self.assertTrue(bar.pause_overlay.called)

    def test_reachable_http_failure_does_not_open_offline_mode(self):
        bar = ELEVBAELKE.Bjaelke.__new__(ELEVBAELKE.Bjaelke)
        bar.konf = {'computer': 'pc-1', 'hub': 'https://hub:8773', 'ui_token': 'ui-token',
                    'ca_fil': '/ca.pem', 'internet_app': 'internet'}
        bar.internet_aaben = True
        bar.server_status = 'offline'
        bar.rod = type('Root', (), {'after': lambda self, _delay, callback: callback()})()
        bar.pause_overlay = type('Overlay', (), {'acknowledgement': lambda self: None,
            'offline': lambda self: (_ for _ in ()).throw(
            AssertionError('reachable failure must not release overlay'))})()
        import urllib.error
        with patch.object(ELEVBAELKE, 'usb_noegler', return_value=[]), \
                patch.object(ELEVBAELKE, 'hent_json', side_effect=urllib.error.HTTPError(
                    'https://hub:8773/api/status', 401, 'unauthorized', {}, None)):
            bar._meld()
        self.assertFalse(bar.internet_aaben)
        self.assertEqual(bar.server_status, 'online')

    def test_malformed_reachable_reply_invalidates_old_cadoodle_policy(self):
        bar = ELEVBAELKE.Bjaelke.__new__(ELEVBAELKE.Bjaelke)
        bar.konf = {'computer': 'pc-1', 'hub': 'https://hub:8773', 'ui_token': 'ui-token',
                    'ca_fil': '/ca.pem', 'internet_app': 'internet'}
        bar.internet_aaben = True
        bar.server_status = 'offline'
        bar.rod = type('Root', (), {'after': lambda self, _delay, callback: callback()})()
        bar.pause_overlay = type('Overlay', (), {'acknowledgement': lambda self: None,
            'offline': lambda self: (_ for _ in ()).throw(AssertionError('must stay closed'))})()
        bar.cadoodle = type('Cad', (), {'opdater': lambda self, svar: setattr(self, 'svar', svar)})()
        with patch.object(ELEVBAELKE, 'usb_noegler', return_value=[]), \
                patch.object(ELEVBAELKE, 'hent_json', return_value={'not': 'a status'}):
            bar._meld()
        self.assertEqual(bar.server_status, 'online')
        self.assertFalse(bar.internet_aaben)
        self.assertIsNone(bar.cadoodle.svar)


class LokalStatusTest(unittest.TestCase):
    def test_health_snapshot_validates_apps_and_navigation(self):
        class Response:
            status = 200
            def read(self, _limit):
                return json.dumps({'local': True,
                                   'apps': [{'id': 'scratch', 'port': 8792}],
                                   'navigation': {'id': 'scratch', 'version': 'a' * 32}}).encode()
        class Connection:
            def __init__(self, host, port, timeout):
                self.args = (host, port, timeout)
            def request(self, *_args, **_kwargs):
                pass
            def getresponse(self):
                return Response()
            def close(self):
                pass
        with patch.object(ELEVBAELKE.http.client, 'HTTPConnection', Connection):
            result = ELEVBAELKE.lokal_status(
                {'lokale_app_urler': {'projekter': 'http://127.0.0.1:8791/'}})
        self.assertEqual(result['apps'], [{'id': 'scratch', 'ready': True}])
        self.assertEqual(result['navigation']['id'], 'scratch')

    def test_health_without_navigation_still_reports_readiness(self):
        class Response:
            status = 200
            def read(self, _limit):
                return b'{"local":true,"apps":[],"navigation":null}'
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(ELEVBAELKE.http.client, 'HTTPConnection', Connection):
            self.assertEqual(ELEVBAELKE.lokal_status(
                {'lokale_app_urler': {'projekter': 'http://127.0.0.1:8791/'}})['apps'], [])

    def test_navigation_is_sent_once_and_committed_after_hub_post(self):
        bar = ELEVBAELKE.Bjaelke.__new__(ELEVBAELKE.Bjaelke)
        bar.konf = {'computer': 'pc-1', 'hub': 'https://hub:8773', 'ui_token': 'ui-token',
                    'ca_fil': '/ca.pem', 'internet_app': 'internet',
                    'lokale_app_urler': {'projekter': 'http://127.0.0.1:8791/'}}
        bar.internet_aaben = False
        bar.local_navigation_version = None
        bar.cadoodle = None
        local = {'apps': [{'id': 'scratch', 'ready': True}],
                 'navigation': {'id': 'scratch', 'version': 'b' * 32}}
        with patch.object(ELEVBAELKE, 'usb_noegler', return_value=[]), \
                patch.object(ELEVBAELKE, 'anvendt_version', return_value=2), \
                patch.object(ELEVBAELKE, 'lokal_status', return_value=local), \
                patch.object(ELEVBAELKE, 'hent_json', return_value={'apps': [
                    {'id': 'internet', 'aaben': False}]} ) as post:
            bar._meld()
            bar._meld()
        first = post.call_args_list[0].kwargs['data']
        second = post.call_args_list[1].kwargs['data']
        self.assertEqual(first['visning'], 'scratch')
        self.assertEqual(first['local_apps'], [{'id': 'scratch', 'ready': True}])
        self.assertNotIn('visning', second)


if __name__ == '__main__':
    unittest.main()
