import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

INSTALL = Path(__file__).resolve().parents[1] / 'installation'
sys.path.insert(0, str(INSTALL))
import build

spec = importlib.util.spec_from_file_location('apply_identity', INSTALL / 'apply-install-identity.py')
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
# Synthetic SSH wire data made solely from fixed test bytes; never a school key.
import base64
import struct
_fixture_type = b'ssh-ed25519'
_fixture_wire = struct.pack('>I', len(_fixture_type)) + _fixture_type + struct.pack('>I', 32) + bytes(range(32))
KEY = 'ssh-ed25519 ' + base64.b64encode(_fixture_wire).decode('ascii') + ' synthetic-fixture'
CA = '-----BEGIN CERTIFICATE-----\npublic\n-----END CERTIFICATE-----\n'


class SharedInstallationTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'ca.crt').write_text(CA)
        (root / 'admin.pub').write_text(KEY)
        for number, tokens in ((1, ('A', 'B')), (2, ('C', 'D'))):
            (root / f'{number}.json').write_text(json.dumps({'computer': f'vaerksted-{number:02d}',
                'token': tokens[0]*43, 'ui_token': tokens[1]*43}))
        return {'server': '198.51.100.50', 'ca_file': 'ca.crt', 'admin_public_key': 'admin.pub',
                'device_files': ['1.json', '2.json']}

    def test_shared_profiles_have_independent_names_and_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = self.fixture(root)
            reusable, profiles, ca, key = build.installation_profiles(profile, root)
            self.assertTrue(reusable)
            self.assertEqual(list(profiles), ['vaerksted-01', 'vaerksted-02'])
            self.assertEqual(profiles['vaerksted-02'][0]['computer'], profiles['vaerksted-02'][1]['computer'])
            self.assertEqual((ca, key), (CA, KEY))
            profile['device_files'] = ['1.json', '1.json']
            with self.assertRaises(ValueError): build.installation_profiles(profile, root)
            profile['device_files'] = ['1.json', '2.json']
            second = json.loads((root / '2.json').read_text())
            second['token'] = 'A'*43
            (root / '2.json').write_text(json.dumps(second))
            with self.assertRaises(ValueError): build.installation_profiles(profile, root)

    def test_shared_late_command_copies_only_selected_profile(self):
        template = (INSTALL.parent / 'testmiljoe/vm/preseed-usb.skabelon.cfg').read_text(encoding='utf-8')
        text = build.prepare_preseed(template, 'vaerksted-installation', KEY, 'a'*64, reusable=True)
        self.assertIn('sh /cdrom/elevhub/copy-selected-profile.sh', text)
        self.assertNotIn('cp /cdrom/elevhub/profile/settings.json', text)
        self.assertNotIn('cp -r /cdrom/elevhub/profiles', text)
        self.assertIn('in-target python3 /usr/local/lib/elevhub-apply-install-identity.py', text)

    def test_final_hostname_is_validated_and_written_without_touching_other_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'etc/elevhub-install'
            config.mkdir(parents=True)
            profile = self.fixture(config)
            (config / 'device.json').write_bytes((config / '2.json').read_bytes())
            (config / 'settings.json').write_text(json.dumps({'server': profile['server'], 'computer': 'vaerksted-02'}))
            (root / 'etc/hostname').write_text('vaerksted-installation\n')
            (root / 'etc/hosts').write_text('127.0.0.1 localhost\n127.0.1.1 vaerksted-installation\n::1 localhost\n')
            identity.apply_identity(root, INSTALL / 'firstboot.py')
            self.assertEqual((root / 'etc/hostname').read_text(), 'vaerksted-02\n')
            self.assertEqual((root / 'etc/hosts').read_text(), '127.0.0.1 localhost\n127.0.1.1\tvaerksted-02\n::1 localhost\n')
            (config / 'device.json').write_bytes((config / '1.json').read_bytes())
            with self.assertRaises(ValueError): identity.apply_identity(root, INSTALL / 'firstboot.py')
            self.assertEqual((root / 'etc/hostname').read_text(), 'vaerksted-02\n')


if __name__ == '__main__':
    unittest.main()
