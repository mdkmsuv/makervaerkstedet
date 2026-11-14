"""Browser policy type and bounded local checker; no managed host policy changes."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from jinja2 import Environment, StrictUndefined

ROOT = repo_root(__file__)
KIOSK = source_path('linux-elevhub/ansible/roles/kiosk', ROOT)
spec = importlib.util.spec_from_file_location('proxy_policy', KIOSK / 'files/proxy_policy.py')
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class KioskProxyPolicyTests(unittest.TestCase):
    def policy(self, bypass='school.example.test,127.0.0.1,localhost'):
        return {'ProxyMode': 'fixed_servers', 'ProxyServer': 'http=127.0.0.1:3128;https=127.0.0.1:3128',
                'ProxyBypassList': bypass}

    def test_real_template_uses_string_and_only_school_local_bypass(self):
        env = Environment(undefined=StrictUndefined)
        env.filters['to_json'] = json.dumps
        env.filters['bool'] = bool
        # Read the authoritative defaults, not a second policy-shaped template.
        import yaml
        values = yaml.safe_load((KIOSK / 'defaults/main.yml').read_text(encoding='utf-8'))
        values.update(kiosk_server='school.example.test', kiosk_lokale_app_urler={}, kiosk_enhed={'ui_token': 'x'*32})
        source = (KIOSK / 'templates/chromium-politik.json.j2').read_text(encoding='utf-8')
        policy = json.loads(env.from_string(source).render(**values))
        self.assertTrue(proxy.validate_policy(policy, 'school.example.test'))
        self.assertEqual(policy['ProxyBypassList'], 'school.example.test,127.0.0.1,localhost')

    def test_legacy_rules_preserved_and_nested_contract_checked(self):
        normalized = proxy.normalized_bypass(['custom.example.test', 'LOCALHOST'], 'school.example.test')
        self.assertEqual(normalized, 'custom.example.test,LOCALHOST,school.example.test,127.0.0.1')
        self.assertTrue(proxy.validate_policy({'ProxySettings': self.policy()}, 'school.example.test'))
        self.assertEqual(proxy.normalized_bypass(normalized, 'school.example.test'), normalized)

    def test_malformed_and_unbounded_policy_rejected(self):
        for value in ([], {}, None, 7, 'school.example.test', 'school.example.test,127.0.0.1,localhost\x00'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                proxy.validate_policy(self.policy(value), 'school.example.test')
        for value in (['host,extra'], [7], ['a']*257, ['a'*2049]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                proxy.normalized_bypass(value, 'school.example.test')
        for server in ('bad;host', '', 'a'*2049):
            with self.assertRaises(ValueError):
                proxy.normalized_bypass([], server)

    def test_regular_bounded_file_is_read_and_oversized_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'policy.json'
            path.write_text(json.dumps(self.policy()), encoding='utf-8')
            self.assertEqual(proxy.read_policy(path), self.policy())
            path.write_bytes(b' ' * (proxy.MAX_POLICY_BYTES + 1))
            with self.assertRaises(ValueError):
                proxy.read_policy(path)

    @unittest.skipUnless(os.name == 'posix', 'Unprivileged link creation requires POSIX')
    def test_linked_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data/policy.json').write_text(json.dumps(self.policy()))
            (root / 'link').symlink_to(root / 'data', target_is_directory=True)
            with self.assertRaises(ValueError):
                proxy.read_policy(root / 'link/policy.json')
