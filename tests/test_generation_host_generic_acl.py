"""Regress actual Windows ancestor masks without modifying ACLs or services."""
import base64
import ctypes
import json
import ntpath
import os
from pathlib import Path
import subprocess
import unittest

from app import generation_host as host
from tests.test_generation_host import (DATA, OLD_DATA, RUNTIME, SOURCE, TARGET,
                                       acl, record, rule, snapshot)


class GenericAclPolicyTests(unittest.TestCase):
    def check(self, value):
        return host.WindowsGenerationHost(lambda _: value).check(
            RUNTIME, record(SOURCE, OLD_DATA), record(TARGET, DATA, 2))

    def test_observed_parent_masks_pass_full_generation_check(self):
        # These are the forms found on real Windows ancestors by the Claude
        # probe, not a change to the rights granted by the installation.
        value = snapshot()
        for item in value['paths']:
            path = host._canonical(item['path'])
            if path in ('c:\\', r'c:\program files'):
                item['acl'] = [rule(host.SYSTEM, 0x10000000), rule(host.ADMIN, 0x10000000),
                               rule('S-1-3-0', 0x10000000, inherit_only=True),
                               rule(host.USERS, 0xA0000000, inherit_only=True),
                               rule('S-1-5-11', -536805376, inherit_only=True)]
        self.assertIsNone(self.check(value))

    def test_generic_rights_keep_unsafe_writes_and_private_reads_rejected(self):
        for path, sid, mask in ((TARGET, host.USERS, 0x40000000),
                                (r'C:\Program Files', host.USERS, 0x10000000),
                                (TARGET, host.USERS, 0x10000040),
                                (DATA + r'\admin-ssh\id_ed25519', host.USERS, 0x80000000)):
            with self.subTest(path=path, mask=mask):
                value = snapshot()
                item = next(row for row in value['paths'] if host._canonical(row['path']) == host._canonical(path))
                item['acl'].append(rule(sid, mask))
                with self.assertRaises(ValueError):
                    self.check(value)

    def test_security_tree_rejects_broad_reads_in_specific_and_generic_forms(self):
        for child in ('security', r'security\server.key', r'security\enheder.json',
                      r'security\enheder.lock', r'security\enrollment'):
            for mask in (host.READ, 0x80000000):
                with self.subTest(child=child, mask=mask):
                    value = snapshot()
                    item = next(row for row in value['paths']
                                if host._canonical(row['path']) == host._canonical(DATA + '\\' + child))
                    item['acl'].append(rule(host.USERS, mask))
                    with self.assertRaises(ValueError):
                        self.check(value)

    def test_security_allows_service_read_and_keeps_shared_laserplates_writable(self):
        value = snapshot()
        item = next(row for row in value['paths']
                    if host._canonical(row['path']) == host._canonical(DATA + r'\security\server.key'))
        item['acl'].append(rule(host.SERVICE, 0x80000000))
        self.assertIsNone(self.check(value))
        value = snapshot('makerspace')
        item = next(row for row in value['paths']
                    if host._canonical(row['path']) == host._canonical(DATA + r'\Laserplader'))
        item['acl'].append(rule(host.USERS, host.MODIFY))
        self.assertIsNone(self.check(value))

    def test_deny_and_unknown_bits_still_fail_even_on_inherit_only_rules(self):
        for extra in (rule(host.SYSTEM, 0x10000000, type='Deny', inherit_only=True),
                      rule(host.SYSTEM, 0x18000000, inherit_only=True),
                      rule(host.SYSTEM, 0x2000000, inherit_only=True)):
            item = acl(r'C:\Program Files')
            item['acl'].append(extra)
            with self.assertRaises(ValueError):
                host._acl(item, host.PRIVILEGED | {host.TRUSTED_INSTALLER}, ancestor=True)

    def test_signed_unsigned_and_invalid_ranges(self):
        self.assertEqual(host._file_access_mask(-2147483648), 0x120089)
        self.assertEqual(host._file_access_mask(0xE0010000), 0x1301BF)
        self.assertEqual(host._file_access_mask(-536805376), 0x1301BF)
        for invalid in (True, False, 0, -1, -(2**31)-1, 2**32, 0x80000200, '0x80000000'):
            with self.subTest(mask=invalid), self.assertRaises(ValueError):
                host._file_access_mask(invalid)

    @unittest.skipUnless(os.name == 'nt', 'Windows native mapping required')
    def test_matches_native_windows_file_generic_mapping(self):
        class GenericMapping(ctypes.Structure):
            _fields_ = [('read', ctypes.c_uint32), ('write', ctypes.c_uint32),
                        ('execute', ctypes.c_uint32), ('all', ctypes.c_uint32)]
        mapping = GenericMapping(0x120089, 0x120116, 0x1200A0, 0x1F01FF)
        native = ctypes.WinDLL('advapi32', use_last_error=True).MapGenericMask
        native.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(GenericMapping)]
        native.restype = None
        for generic in range(1, 16):
            for specific in (0, 0x40, 0x10000, 0x20089):
                raw = (generic << 28) | specific
                expected = ctypes.c_uint32(raw)
                native(ctypes.byref(expected), ctypes.byref(mapping))
                with self.subTest(mask=raw):
                    self.assertEqual(host._file_access_mask(raw), expected.value)

    @unittest.skipUnless(os.name == 'nt', 'Windows read-only ACL probe required')
    def test_actual_windows_ancestor_descriptors(self):
        functions = host.COLLECTOR[host.COLLECTOR.index('function Sid'):host.COLLECTOR.index('$roots =')]
        ancestors = [str(Path(os.environ['ProgramFiles']).anchor), os.environ['ProgramFiles'],
                     os.environ['ProgramData']]
        script = "$ErrorActionPreference='Stop'\n" + functions + "\n@((ConvertFrom-Json $env:GENERATION_TEST_ANCESTORS) | ForEach-Object { PathInfo $_ }) | ConvertTo-Json -Depth 6 -Compress"
        env = os.environ.copy()
        env['GENERATION_TEST_ANCESTORS'] = json.dumps(ancestors)
        system = next(value for key, value in env.items() if key.lower() == 'systemroot')
        env['PSModulePath'] = ntpath.join(system, r'System32\WindowsPowerShell\v1.0\Modules')
        result = subprocess.run([ntpath.join(system, r'System32\WindowsPowerShell\v1.0\powershell.exe'),
                                 '-NoProfile', '-NonInteractive', '-EncodedCommand',
                                 base64.b64encode(script.encode('utf-16le')).decode()],
                                env=env, capture_output=True, text=True, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 3)
        for item in rows:
            with self.subTest(path=item['path']):
                host._acl(item, host.PRIVILEGED | {host.TRUSTED_INSTALLER}, ancestor=True)


if __name__ == '__main__':
    unittest.main()
