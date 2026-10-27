"""Internetpolitikkens fejlsituationer; ændrer ingen faktiske regler eller systemfiler."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

FIL = Path(__file__).parents[1] / 'ansible/roles/kiosk/files/elevhub-regler.py'
SPEC = importlib.util.spec_from_file_location('internetregler', FIL)
REGLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGLER)


class InternetreglerTest(unittest.TestCase):
    def setUp(self):
        self.mappe = tempfile.TemporaryDirectory()
        self.addCleanup(self.mappe.cleanup)
        self.root = Path(self.mappe.name)
        self.konf = {'elev': 'elev', 'server': 'vaert.invalid', 'hub': 'https://vaert.invalid:8773',
                     'ca_fil': 'ca.pem', 'usikker_test': False,
                     'elev_porte': [8770, 8771, 8772, 8773], 'internet_app': 'internet',
                     'lokale_app_urler': {'projekter': 'http://127.0.0.1:8791/',
                                          'scratch': 'http://127.0.0.1:8792/',
                                          'datatraener': 'http://127.0.0.1:8793/',
                                          'kredsloeb': 'http://127.0.0.1:8794/'}}
        self.credentials = {'computer': 'vaertsted-01', 'token': 'root-token-123456789'}
        self.addCleanup(patch.stopall)
        patch.object(REGLER, 'KONF', self.konf).start()
        patch.object(REGLER, 'CREDENTIALS', self.credentials).start()
        for navn in ('POLITIK', 'ANVENDT'):
            patch.object(REGLER, navn, self.root / (navn + '.json')).start()
        self.url = {True: self.root / 'aaben.json', False: self.root / 'lukket.json'}
        for vaerdi, sti in self.url.items():
            sti.write_text(json.dumps({'aaben': vaerdi}))
        patch.object(REGLER, 'URL_FILER', self.url).start()

    def hent(self, data):
        with patch.object(REGLER, 'hent_json', return_value=data):
            return REGLER.hent_valg()

    def test_ugyldigt_svar_maa_ikke_aabne_internet(self):
        for vaerdi in ('false', 'true', None, 0, 1, [], {}):
            with self.subTest(vaerdi=vaerdi), self.assertRaises(ValueError):
                self.hent({'version': 2, 'apps': [{'id': 'internet', 'aaben': vaerdi}]})
        for version in (True, '2', 2.0, 0, -1):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.hent({'version': version, 'apps': [{'id': 'internet', 'aaben': True}]})
        for apps in ({}, [None], [{'id': 'internet', 'aaben': True}] * 2):
            with self.subTest(apps=apps), self.assertRaises(ValueError):
                self.hent({'version': 2, 'apps': apps})

    def test_godkendt_svar_og_manglende_internetapp(self):
        self.assertEqual(self.hent({'version': 2, 'apps': [{'id': 'internet', 'aaben': False}]}), (2, False))
        self.assertEqual(self.hent({'version': 2, 'apps': [{'id': 'internet', 'aaben': True}]}), (2, True))
        self.assertEqual(self.hent({'version': 2, 'apps': []}), (2, False))

    def test_lokale_apps_foelger_laererens_aabning_i_chromium_politik(self):
        apps = [
            {'id': 'scratch', 'type': 'lokal', 'aaben': True},
            {'id': 'datatraener', 'type': 'lokal', 'aaben': False},
        ]
        lukket = json.loads(REGLER._chromium_url_policy(False, apps))
        self.assertIn('http://127.0.0.1:8792/', lukket['URLAllowlist'])
        self.assertNotIn('http://127.0.0.1:8793/', lukket['URLAllowlist'])
        aaben = json.loads(REGLER._chromium_url_policy(True, apps))
        self.assertNotIn('http://127.0.0.1:8792/', aaben['URLBlocklist'])
        self.assertIn('http://127.0.0.1:8793/', aaben['URLBlocklist'])

    def test_lokalt_designvaerksted_foelger_laererens_aabning(self):
        # Laser og folie er stadig "vaerksted" på serveren (afleveringer går
        # gennem indgangen), men tegnefladen kører på denne computer.
        self.konf['lokale_app_urler'].update({'laser': 'http://127.0.0.1:8795/',
                                              'folie': 'http://127.0.0.1:8796/',
                                              'internet': 'http://127.0.0.1:8797/'})
        apps = [
            {'id': 'laser', 'aaben': True},
            {'id': 'folie', 'type': 'vaerksted', 'aaben': False},
            {'id': '3dprint', 'aaben': True},
            {'id': 'internet', 'type': 'browser', 'aaben': True},
        ]
        lukket = json.loads(REGLER._chromium_url_policy(False, apps))
        self.assertIn('http://127.0.0.1:8795/', lukket['URLAllowlist'])
        self.assertIn('http://localhost:8795/', lukket['URLAllowlist'])
        self.assertNotIn('http://127.0.0.1:8796/', lukket['URLAllowlist'])
        # En browserapp får aldrig en lokal undtagelse, selv med en lokal URL.
        self.assertNotIn('http://127.0.0.1:8797/', lukket['URLAllowlist'])
        aaben = json.loads(REGLER._chromium_url_policy(True, apps))
        self.assertIn('http://127.0.0.1:8796/', aaben['URLBlocklist'])
        self.assertNotIn('http://127.0.0.1:8795/', aaben['URLBlocklist'])
        # 3D-print har ingen lokal adresse og styres fortsat af serverens indgang.
        self.assertFalse(any(':8770' in url for url in lukket['URLAllowlist'] + aaben['URLBlocklist']))

    def test_local_makecode_controller_follows_web_workshop_policy(self):
        self.konf['lokale_app_urler']['microbit'] = 'http://127.0.0.1:8797/'
        app = {'id': 'microbit', 'type': 'web', 'aaben': True,
               'start_url': 'https://makecode.microbit.org/',
               'domaener': ['makecode.microbit.org']}
        offline_internet = json.loads(REGLER._chromium_url_policy(False, [app]))
        self.assertIn('http://127.0.0.1:8797/', offline_internet['URLAllowlist'])
        self.assertIn('https://.makecode.microbit.org/', offline_internet['URLAllowlist'])
        closed = json.loads(REGLER._chromium_url_policy(True, [dict(app, aaben=False)]))
        self.assertIn('http://127.0.0.1:8797/', closed['URLBlocklist'])
        self.assertIn('https://.makecode.microbit.org/', closed['URLBlocklist'])

    def test_kold_offline_start_aabner_uden_cache_eller_dns(self):
        konto = types.SimpleNamespace(getpwnam=lambda _: types.SimpleNamespace(pw_uid=1001))
        with patch.dict(sys.modules, {'pwd': konto}), patch.object(REGLER, 'anvend') as anvend, \
                patch.object(REGLER, 'server_ip', side_effect=AssertionError('DNS må ikke bruges')), \
                patch.object(REGLER, 'hent_valg', side_effect=TimeoutError()), \
                patch.object(REGLER, 'dns_adresser', return_value=[]), patch.object(REGLER, 'saet_download'):
            REGLER.main(kun_sikker_start=True)
        anvend.assert_called_once_with(True, None, 1001, [])
        self.assertFalse(REGLER.ANVENDT.exists())
        self.assertEqual(REGLER.AKTUELLE_APPS, [])

    def test_skolegpt_chat_api_foelger_vaerkstedet_i_browseren(self):
        config = json.loads((Path(__file__).parents[1] / 'server/elevhub/konfiguration.json').read_text(encoding='utf-8'))
        app = next(a for a in config['apps'] if a['id'] == 'skolegpt')
        allowed = json.loads(REGLER._chromium_url_policy(False, [dict(app, aaben=True)]))
        blocked = json.loads(REGLER._chromium_url_policy(True, [dict(app, aaben=False)]))
        exact_origin = 'https://.glyph-gate.dbc.dk/'
        self.assertIn(exact_origin, allowed['URLAllowlist'])
        self.assertIn(exact_origin, blocked['URLBlocklist'])
        self.assertNotIn(exact_origin, blocked.get('URLAllowlist', []))

    def test_dns_kun_til_konfigurerede_resolvere(self):
        sti = self.root / 'resolv.conf'
        sti.write_text('nameserver 192.0.2.53\nnameserver 2001:db8::53\nnameserver ugyldig\nsearch skole\n')
        self.assertEqual(REGLER.dns_adresser(sti), ['192.0.2.53', '2001:db8::53'])
        regler = REGLER.nft_regler(False, None, 1001, REGLER.dns_adresser(sti))
        self.assertIn('ip daddr 192.0.2.53 udp dport 53 accept', regler)
        self.assertIn('ip6 daddr 2001:db8::53 tcp dport 53 accept', regler)
        self.assertNotIn('\n        udp dport 53 accept', regler)

    def test_nft_fejl_efterlader_ikke_gammel_kvittering(self):
        REGLER.meld_anvendt(4, True)
        with patch.object(REGLER.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'nft')):
            with self.assertRaises(subprocess.CalledProcessError):
                REGLER.anvend(False, '203.0.113.1', 1001, [])
        self.assertFalse(REGLER.ANVENDT.exists())
        self.assertFalse(json.loads(REGLER.POLITIK.read_text())['aaben'])

    def test_browserfejl_lukker_firewall_igen(self):
        REGLER.meld_anvendt(4, False)
        with patch.object(REGLER, 'skriv_atomisk', side_effect=OSError('Disk fuld')), \
                patch.object(REGLER.subprocess, 'run') as nft:
            with self.assertRaises(OSError):
                REGLER.anvend(True, '203.0.113.1', 1001, [])
        self.assertEqual(nft.call_count, 2)
        self.assertIn('\n        reject\n', nft.call_args.kwargs['input'])
        self.assertFalse(REGLER.ANVENDT.exists())

    def test_offline_fjerner_gamle_lukninger_af_lokale_apps(self):
        policy = json.loads(REGLER._chromium_url_policy(True, []))
        self.assertNotIn('http://127.0.0.1:8792/', policy.get('URLBlocklist', []))

    def test_authfejl_fjerner_friskt_statusstempel(self):
        REGLER.meld_anvendt(4, True)
        with patch.object(REGLER, 'hent_json', side_effect=ValueError('TLS')):
            with self.assertRaises(ValueError):
                REGLER.hent_valg()
        # En fejlet privileged GET må ikke give bjælken et gammelt rootstempel.
        self.assertFalse(REGLER.ANVENDT.exists())

    def test_legacy_http_bruger_kun_offentlige_legacy_routes(self):
        REGLER.KONF.update({'hub': 'http://vaert.invalid:8773', 'usikker_test': True,
                            'computer': 'vaertsted-01'})
        policy = {'version': 2, 'apps': [{'id': 'internet', 'aaben': True}]}
        status = {'version': 2, 'apps': policy['apps'], 'gaa_hjem': False}
        with patch.object(REGLER, 'hent_offentlig_json', side_effect=[policy, status]) as offentlig, \
                patch.object(REGLER, 'hent_json') as privilegeret:
            self.assertEqual(REGLER.hent_valg(), (2, True))
            self.assertEqual(REGLER.meld_status(2)['version'], 2)
        self.assertEqual(offentlig.call_args_list[0].args[:2],
                         ('http://vaert.invalid:8773', '/api/apps'))
        self.assertEqual(offentlig.call_args.kwargs['data'],
                         {'computer': 'vaertsted-01', 'kilde': 'bjaelke', 'version': 2})
        privilegeret.assert_not_called()

    def run_contacts(self, contacts, acknowledgements=None):
        konto = types.SimpleNamespace(getpwnam=lambda _: types.SimpleNamespace(pw_uid=1001))
        svar = {'version': 3, 'apps': [], 'gaa_hjem': False}
        anvend = []

        def optag(aaben, server, uid, dns):
            anvend.append((aaben, list(REGLER.AKTUELLE_APPS)))

        class StopLoop(Exception):
            pass

        with patch.dict(sys.modules, {'pwd': konto}), \
                patch.object(REGLER, 'saet_download'), \
                patch.object(REGLER, 'dns_adresser', return_value=[]), \
                patch.object(REGLER, 'server_ip', return_value='192.0.2.1'), \
                patch.object(REGLER, 'anvend', side_effect=optag), \
                patch.object(REGLER, 'hent_json', side_effect=contacts), \
                patch.object(REGLER, 'meld_status', side_effect=acknowledgements, return_value=svar), \
                patch.object(REGLER.time, 'sleep', side_effect=[None] * (len(contacts) - 1) + [StopLoop()]):
            with self.assertRaises(StopLoop):
                REGLER.main()
        return anvend

    def test_offline_online_lukket_udfald_online_aaben(self):
        # A real outage (three missed replies in a row) opens everything and
        # forgets closed apps; the next valid policy applies again.
        closed = {'version': 2, 'apps': [{'id': 'internet', 'aaben': False},
                                       {'id': 'scratch', 'aaben': False}]}
        opened = {'version': 3, 'apps': [{'id': 'internet', 'aaben': True}]}
        transitions = self.run_contacts([TimeoutError(), closed, OSError('offline'), OSError('offline'),
                                         OSError('offline'), opened])
        self.assertEqual([item[0] for item in transitions], [True, False, True, True])
        self.assertEqual(transitions[2][1], [])
        self.assertEqual(json.loads(REGLER.ANVENDT.read_text())['version'], 3)

    def test_enkelt_udfald_efter_online_bevarer_laererens_lukning(self):
        # A pupil starving the CPU can make one request time out; that alone
        # must not open Internet.
        closed = {'version': 2, 'apps': [{'id': 'internet', 'aaben': False}]}
        transitions = self.run_contacts([closed, TimeoutError(), closed, TimeoutError(), TimeoutError(), closed])
        self.assertEqual([item[0] for item in transitions], [False])

    def test_kold_opstart_uden_server_aabner_straks(self):
        transitions = self.run_contacts([TimeoutError()])
        self.assertEqual([item[0] for item in transitions], [True])

    def test_statusudfald_aabner_og_genforbindelse_lukker(self):
        closed = {'version': 2, 'apps': [{'id': 'internet', 'aaben': False}]}
        ok = {'version': 2, 'apps': [], 'gaa_hjem': False}
        timeout = TimeoutError()
        transitions = self.run_contacts([closed, closed, closed, closed, closed],
                                       [ok, timeout, timeout, timeout, ok])
        self.assertEqual([item[0] for item in transitions], [False, True, False])

    def test_enkelt_statusudfald_bevarer_laererens_lukning(self):
        closed = {'version': 2, 'apps': [{'id': 'internet', 'aaben': False}]}
        ok = {'version': 2, 'apps': [], 'gaa_hjem': False}
        transitions = self.run_contacts([closed, closed, closed], [ok, TimeoutError(), ok])
        self.assertEqual([item[0] for item in transitions], [False])

    def test_reachable_afvisning_er_ikke_offline(self):
        transitions = self.run_contacts([TimeoutError(), urllib.error.HTTPError('', 403, '', {}, None)])
        self.assertEqual([item[0] for item in transitions], [True, False])
        self.assertFalse(REGLER.ANVENDT.exists())

    def test_lukning_der_fejler_bevarer_ukendt_state(self):
        with patch.object(REGLER, 'anvend', side_effect=OSError('nft')), \
                patch.object(REGLER, 'meld_status'):
            self.assertFalse(REGLER.luk_ved_fejl('192.0.2.1', 1001, []))

    def test_gentagne_offline_poll_genstarter_ikke_proxy_igen(self):
        transitions = self.run_contacts([TimeoutError(), OSError('offline'), TimeoutError()])
        self.assertEqual(transitions, [(True, [])])
        self.assertFalse(REGLER.ANVENDT.exists())


if __name__ == '__main__':
    unittest.main()
