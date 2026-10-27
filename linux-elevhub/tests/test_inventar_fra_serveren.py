"""Student PCs are recognised by the server, so they need no fixed IPs."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fra_serveren', ROOT / 'ansible/inventory/skole/fra-serveren.py')
INV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INV)
sys.path.insert(0, str(ROOT / 'server/elevhub'))
from tilstand import Tilstand  # noqa: E402

KEY = 'ssh-ed25519 ' + 'A' * 68
NU = 1_000_000.0


def ind(temp):
    return {'minutter': 15, 'elev_kendte': Path(temp) / 'known_hosts'}


class InventarTest(unittest.TestCase):
    def test_recent_pcs_are_listed_by_name_with_trusted_key(self):
        data = {'vaerksted-01': {'ip': '198.51.100.23', 'sidst_set': NU - 30, 'ssh_host_key': KEY},
                'vaerksted-02': {'ip': '198.51.100.31', 'sidst_set': NU - 60 * 60, 'ssh_host_key': KEY}}
        with tempfile.TemporaryDirectory() as temp:
            hosts, kendte, noter = INV.inventar(data, NU, ind(temp))
        self.assertEqual(list(hosts), ['vaerksted-01'])
        self.assertEqual(hosts['vaerksted-01']['ansible_host'], '198.51.100.23')
        self.assertIn('HostKeyAlias=vaerksted-01', hosts['vaerksted-01']['ansible_ssh_common_args'])
        self.assertIn('StrictHostKeyChecking=yes', hosts['vaerksted-01']['ansible_ssh_common_args'])
        self.assertEqual(kendte, ['vaerksted-01 ' + KEY])
        self.assertTrue(any('vaerksted-02' in n and 'slukket' in n for n in noter))

    def test_pc_without_reported_key_is_never_trusted_blindly(self):
        data = {'vaerksted-03': {'ip': '198.51.100.40', 'sidst_set': NU}}
        with tempfile.TemporaryDirectory() as temp:
            hosts, kendte, noter = INV.inventar(data, NU, ind(temp))
        self.assertIn('StrictHostKeyChecking=yes', hosts['vaerksted-03']['ansible_ssh_common_args'])
        self.assertEqual(kendte, [])
        self.assertTrue(any('SSH-nøgle' in n for n in noter))

    def test_bad_names_and_addresses_are_ignored(self):
        data = {'server; rm -rf': {'ip': '198.51.100.5', 'sidst_set': NU},
                'vaerksted-04': {'ip': '8.8.8.8', 'sidst_set': NU},
                'vaerksted-05': {'ip': '127.0.0.1', 'sidst_set': NU},
                'vaerksted-06': {'ip': 'ikke-en-ip', 'sidst_set': NU}}
        with tempfile.TemporaryDirectory() as temp:
            hosts, _, _ = INV.inventar(data, NU, ind(temp))
        self.assertEqual(hosts, {})

    def test_windows_openssh_is_used_when_the_key_only_exists_in_windows(self):
        uden_wsl_noegle = lambda sti: sti == INV.WINDOWS_SSH
        with mock.patch.dict('os.environ', {'ELEVHUB_SERVER_SSH': 'admin@server.example.invalid'}, clear=True), \
                mock.patch.object(INV.os.path, 'isfile', uden_wsl_noegle), \
                mock.patch.object(INV, 'windows_profil', return_value='C:\\Users\\Laerer'):
            ind = INV.indstillinger()
        self.assertEqual(ind['ssh'], INV.WINDOWS_SSH)
        self.assertEqual(ind['noegle'], 'C:\\Users\\Laerer\\.ssh\\printvaerksted_vm_ed25519')
        self.assertEqual(ind['kendte'], 'C:\\Users\\Laerer\\.ssh\\printvaerksted_vm_known_hosts')
        with mock.patch.dict('os.environ', {'ELEVHUB_SERVER_SSH': 'admin@server.example.invalid'}, clear=True), \
                mock.patch.object(INV.os.path, 'isfile', lambda sti: True):
            self.assertEqual(INV.indstillinger()['ssh'], 'ssh')  # a key inside WSL wins

    def test_server_address_is_explicit_and_never_a_school_default(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'ELEVHUB_SERVER_SSH'):
                INV.indstillinger()
        with mock.patch.dict('os.environ', {'ELEVHUB_SERVER_SSH': '-oProxyCommand=invalid'}, clear=True):
            with self.assertRaises(ValueError):
                INV.indstillinger()

    def test_known_hosts_file_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            sti = Path(temp) / 'ssh' / 'known_hosts'
            INV.gem_kendte(sti, ['vaerksted-01 ' + KEY])
            self.assertEqual(sti.read_text(), 'vaerksted-01 ' + KEY + '\n')


class ServerStoresHostKeyTest(unittest.TestCase):
    def test_valid_key_is_kept_and_invalid_input_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tilstand(temp, [{'id': 'internet', 'navn': 'Internet', 'type': 'browser'}], ur=lambda: NU)
            t.status_fra_elev('vaerksted-01', '198.51.100.23', 'bjaelke', version=0, ssh_host_key=KEY)
            self.assertEqual(t.computere['vaerksted-01']['ssh_host_key'], KEY)
            t.status_fra_elev('vaerksted-01', '198.51.100.23', 'bjaelke', version=0,
                              ssh_host_key='ssh-rsa AAAA\nvaerksted-02 ssh-ed25519 ' + 'B' * 68)
            self.assertEqual(t.computere['vaerksted-01']['ssh_host_key'], KEY)
            self.assertEqual(t.computere['vaerksted-01']['ip'], '198.51.100.23')


if __name__ == '__main__':
    unittest.main()
