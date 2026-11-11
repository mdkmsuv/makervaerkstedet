import importlib.util
import io
import json
import ssl
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch


FIL = Path(__file__).parents[1] / 'ansible/roles/kiosk/files/elevhub_klient.py'
SPEC = importlib.util.spec_from_file_location('elevhub_klient', FIL)
KLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(KLIENT)


class KlientSikkerhedTest(unittest.TestCase):
    def test_offline_adskilles_fra_afvist_login_og_defekt_konfiguration(self):
        for error in (TimeoutError(), ConnectionRefusedError(), OSError('no route'),
                      urllib.error.HTTPError('', 503, '', {}, None)):
            self.assertTrue(KLIENT.server_utilgaengelig(error))
        for error in (ValueError(), ssl.SSLCertVerificationError(), KLIENT.RedirectError(),
                      urllib.error.HTTPError('', 403, '', {}, None),
                      urllib.error.HTTPError('', 401, '', {}, None)):
            self.assertFalse(KLIENT.server_utilgaengelig(error))

    def test_worker_bevarer_tlsfejl_som_tls_ikke_netudfald(self):
        config = {'url': 'https://hub.example:8773/', 'headers': {}, 'timeout': 1}
        error = urllib.error.URLError(ssl.SSLCertVerificationError('test'))
        with patch.object(KLIENT, '_direct_request', side_effect=error):
            result = KLIENT._worker_json(config)
        self.assertEqual(result['type'], 'SSLCertVerificationError')
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(result).encode(), b'')
        with patch.object(KLIENT.subprocess, 'Popen', return_value=process):
            with self.assertRaises(ssl.SSLError):
                KLIENT._run_worker(config, 1)

    def test_http_er_afvist_uden_eksplicit_testflag(self):
        with self.assertRaises(ValueError):
            KLIENT.valider_hub('http://hub.example:8773', False)
        with self.assertRaises(ValueError):
            KLIENT.valider_hub('https://hub.example:8773', True)
        self.assertEqual(KLIENT.valider_hub('http://hub.example:8773', True),
                         'http://hub.example:8773')

    def test_https_request_bruger_begge_headers_og_no_redirect(self):
        with patch.object(KLIENT, '_run_worker', return_value=json.dumps({'ok': True}).encode()) as run:
            self.assertEqual(KLIENT.hent_json('https://hub.example:8773', '/api/enhed/politik',
                                               token='s' * 43, computer='pc-1',
                                               ca_fil='/etc/elevhub/ca.pem'), {'ok': True})
        config = run.call_args.args[0]
        self.assertEqual(config['headers']['Authorization'], 'Bearer ' + 's' * 43)
        self.assertEqual(config['headers']['X-Elevhub-Computer'], 'pc-1')
        self.assertEqual(config['url'], 'https://hub.example:8773/api/enhed/politik')
        self.assertEqual(run.call_args.args[1], 4)

    def test_ssl_context_verificerer_ca_og_hostname(self):
        kontekst = Mock()
        with patch.object(KLIENT.ssl, 'create_default_context', return_value=kontekst) as opret:
            resultat = KLIENT.ssl_kontekst('/ca.pem')
        opret.assert_called_once_with(cafile='/ca.pem')
        self.assertIs(resultat, kontekst)
        self.assertTrue(kontekst.check_hostname)
        self.assertEqual(kontekst.verify_mode, ssl.CERT_REQUIRED)

    def test_redirecthandler_afviser_redirect(self):
        with self.assertRaises(KLIENT.RedirectError):
            KLIENT._AfvisRedirect().redirect_request(None, None, 302, 'Found', {},
                                                      'https://anden.example:8773/')

    def test_samlet_request_timeout_begraenser_trickle_peer(self):
        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: time.sleep(.1)
        with patch.object(KLIENT.urllib.request, 'build_opener', return_value=opener), \
                patch.object(KLIENT, 'ssl_kontekst', return_value=Mock()):
            with self.assertRaises(TimeoutError):
                KLIENT.hent_json('https://hub.example:8773', '/api/enhed/politik',
                                 token='s' * 43, computer='pc-1', ca_fil='/ca.pem', timeout=.01)

    def test_gentagne_timeout_killer_hver_worker_foer_ny_request(self):
        processer = []

        class Hængende:
            returncode = None

            def communicate(self, *args, **kwargs):
                if kwargs.get('timeout') is not None:
                    raise KLIENT.subprocess.TimeoutExpired('worker', kwargs['timeout'])
                self.returncode = -9
                return b'', b''

            def kill(self):
                self.killed = True

        def start(*args, **kwargs):
            p = Hængende()
            p.killed = False
            processer.append(p)
            return p

        with patch.object(KLIENT.subprocess, 'Popen', side_effect=start):
            for _ in range(3):
                with self.assertRaises(TimeoutError):
                    KLIENT.hent_json('https://hub.example:8773', '/api/enhed/politik',
                                     token='s' * 43, computer='pc-1', ca_fil='/ca.pem', timeout=.01)
        self.assertEqual(len(processer), 3)
        self.assertTrue(all(p.killed for p in processer))


if __name__ == '__main__':
    unittest.main()
