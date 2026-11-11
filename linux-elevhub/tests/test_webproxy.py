"""Pure policy tests for the loopback Squid web workshop proxy."""
import importlib.util
import json
import unittest
from pathlib import Path


FIL = Path(__file__).parents[1] / 'ansible/roles/kiosk/files/elevhub-webproxy.py'
SPEC = importlib.util.spec_from_file_location('elevhub_webproxy_test', FIL)
PROXY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROXY)


class WebproxyPolicyTest(unittest.TestCase):
    def setUp(self):
        self.web = {
            'id': 'microbit', 'type': 'web', 'aaben': True,
            'start_url': 'https://makecode.microbit.org/',
            'domaener': ['makecode.microbit.org', 'cdn.makecode.com'],
        }

    def test_domain_injection_and_wildcards_are_rejected(self):
        for bad in ['*', '.example.org', 'https://evil.invalid', 'foo;http_access allow all',
                    'EXAMPLE.ORG', '127.0.0.1', 'example.org.']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PROXY.valid_hostname(bad)

    def test_closed_workshop_remains_denied_when_general_internet_opens(self):
        closed = dict(self.web, aaben=False)
        text = PROXY.render_policy([closed], True, ['203.0.113.1'])
        self.assertIn('acl closed_workshops dstdomain cdn.makecode.com makecode.microbit.org', text)
        self.assertIn('http_access deny closed_workshops', text)
        self.assertIn('http_access allow CONNECT https_port', text)
        self.assertNotIn('http_access allow CONNECT closed_workshops', text)

    def test_closed_internet_allows_only_open_workshop_domains(self):
        text = PROXY.render_policy([self.web], False)
        self.assertIn('acl open_workshops dstdomain cdn.makecode.com makecode.microbit.org', text)
        self.assertIn('http_access allow CONNECT open_workshops', text)
        self.assertNotIn('http_access allow CONNECT https_port', text)
        self.assertIn('http_access deny !CONNECT', text)
        self.assertIn('http_access deny CONNECT !https_port', text)
        self.assertIn('acl open_sni ssl::server_name --client-requested cdn.makecode.com makecode.microbit.org', text)
        self.assertIn('ssl_bump peek step1', text)
        self.assertIn('acl step1 at_step SslBump1', text)
        self.assertIn('ssl_bump terminate !open_sni', text)

    def test_private_and_server_destinations_are_denied(self):
        text = PROXY.render_policy([self.web], True, ['203.0.113.1'])
        private_acl = next(line for line in text.splitlines() if line.startswith('acl blocked_private dst '))
        for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'):
            self.assertIn(network, private_acl)
        self.assertIn('acl blocked_private6 dst ', text)
        self.assertIn('::ffff:192.168.0.0/112', text)
        self.assertNotIn('::ffff:0:0/96', text)
        self.assertIn('acl blocked_server_0 dst 203.0.113.1', text)
        self.assertIn('http_access deny blocked_server_0', text)

    def test_skolegpt_chat_api_follows_workshop_access_in_shipped_configs(self):
        root = Path(__file__).parents[1]
        for relative in ('server/elevhub/konfiguration.json', 'testmiljoe/vaert/elevhub-test.json'):
            with self.subTest(config=relative):
                config = json.loads((root / relative).read_text(encoding='utf-8'))
                app = next(a for a in config['apps'] if a['id'] == 'skolegpt')
                # The UI and the streaming chat API use different origins.
                self.assertIn('glyph-gate.dbc.dk', PROXY.web_domains(app))
                opened = PROXY.render_policy([dict(app, aaben=True)], False)
                closed = PROXY.render_policy([dict(app, aaben=False)], True)
                for policy, acl in ((opened, 'open'), (closed, 'closed')):
                    for suffix in ('_workshops dstdomain', '_sni ssl::server_name --client-requested'):
                        line = next(s for s in policy.splitlines() if s.startswith('acl ' + acl + suffix))
                        self.assertIn('glyph-gate.dbc.dk', line.split())
                self.assertNotIn('http_access allow CONNECT https_port', opened)
                self.assertLess(closed.index('http_access deny closed_workshops'),
                                closed.index('http_access allow CONNECT https_port'))

    def test_stop_isolated_proxy_uses_only_named_service(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))

        PROXY.service_stop(run)
        self.assertEqual(calls[0][0], ['systemctl', 'stop', 'elevhub-webproxy.service'])
        self.assertTrue(calls[0][1]['check'])


if __name__ == '__main__':
    unittest.main()
