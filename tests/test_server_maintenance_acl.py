import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from app.backup_guard import write_protocol


class ServerMaintenanceAclTests(unittest.TestCase):
    def test_existing_valid_protocol_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'backup-protocol.json'
            target.write_text('{"protocol":1,"role":"server"}\n', encoding='utf-8')
            before = target.read_bytes()
            with mock.patch('app.backup_guard.os.replace') as replace, \
                 mock.patch('app.backup_guard.tempfile.NamedTemporaryFile',
                            side_effect=PermissionError('must not write')):
                result = write_protocol(folder, 'server')
            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), before)
            replace.assert_not_called()

    def test_missing_and_changed_markers_use_atomic_write(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'backup-protocol.json'
            write_protocol(folder, 'server')
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),
                             {'protocol': 1, 'role': 'server'})
            for marker in ('{"protocol":99,"role":"old"}',
                           '{"protocol":true,"role":"server"}',
                           '{"protocol":1.0,"role":"server"}'):
                target.write_text(marker, encoding='utf-8')
                write_protocol(folder, 'server')
                self.assertEqual(json.loads(target.read_text(encoding='utf-8')),
                                 {'protocol': 1, 'role': 'server'})

    def test_malformed_marker_is_replaced_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'backup-protocol.json'
            target.write_bytes(b'not-json')
            write_protocol(folder, 'server')
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),
                             {'protocol': 1, 'role': 'server'})

    def test_corrupt_or_mismatched_readonly_marker_fails_without_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'backup-protocol.json'
            for marker in (b'not-json', b'{"protocol":true,"role":"server"}',
                           b'{"protocol":1,"role":"other"}'):
                target.write_bytes(marker)
                with mock.patch('app.backup_guard.tempfile.NamedTemporaryFile',
                                side_effect=PermissionError('readonly')):
                    with self.assertRaises(PermissionError):
                        write_protocol(folder, 'server')
                self.assertEqual(target.read_bytes(), marker)

    def test_links_and_non_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'backup-protocol.json'
            target.mkdir()
            with self.assertRaises(ValueError):
                write_protocol(root, 'server')
            target.rmdir()
            try:
                target.symlink_to(root / 'other.json')
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
            with self.assertRaises(ValueError):
                write_protocol(root, 'server')

    def test_installer_grants_only_lock_modify_and_protocol_read(self):
        script = (Path(__file__).parents[1] / 'windows-elevhub' / 'server' / 'install.ps1').read_text(encoding='utf-8')
        self.assertIn("$instanceLockPath = Join-Path $DataRoot 'instance.lock'", script)
        self.assertIn("$protocolPath = Join-Path $DataRoot 'backup-protocol.json'", script)
        self.assertIn("'*S-1-5-19:M'", script)
        self.assertIn("'*S-1-5-19:R'", script)
        self.assertIn("([ordered]@{ protocol=1; role='server' })", script)

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL integration is Windows-only')
    def test_update_repair_grants_localservice_read_only_to_owner_marker(self):
        system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        bundled_powershell = system_root / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        powershell_exe = str(bundled_powershell) if bundled_powershell.exists() else 'powershell.exe'
        powershell_env = os.environ.copy()
        system_modules = str(system_root / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'Modules')
        module_paths = [powershell_env.get('PSModulePath', ''), system_modules]
        powershell_env['PSModulePath'] = os.pathsep.join(path for path in module_paths if path)
        powershell = subprocess.run(
            [powershell_exe, '-NoProfile', '-Command',
             'if (Get-Command Get-Acl -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }'],
            capture_output=True, text=True, env=powershell_env,
        )
        if powershell.returncode:
            self.skipTest('PowerShell Security module with Get-Acl is unavailable')
        root = Path(tempfile.mkdtemp(prefix='makervaerkstedet-acl-'))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        data = root / 'data'; data.mkdir()
        lock = data / 'instance.lock'; lock.write_bytes(b'lock-fixture')
        owner = data / 'installation-owner.json'; owner.write_bytes(b'{"schemaVersion":1,"product":"ElevhubServer"}\n')
        config = data / 'konfiguration.json'; config.write_bytes(b'{"elev_adresse":"198.51.100.10"}\n')
        private_key = data / 'unrelated-private.key'; private_key.write_bytes(b'synthetic-private-key-fixture\n')
        receipt = root / 'receipt.json'
        repair = Path(__file__).parents[1] / 'scripts' / 'school_server_update' / 'Repair-SchoolRuntimeAccess.ps1'
        evidence = Path(__file__).parents[1] / 'output' / 'github-skin-ready-20261020' / 'acl-source-tests'
        evidence.mkdir(parents=True, exist_ok=True)

        def acl(path):
            quoted = "'" + str(path).replace("'", "''") + "'"
            command = (
                "$a=Get-Acl -LiteralPath " + quoted + "; "
                "$rules=@($a.Access | ForEach-Object { "
                "$sid=$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value; "
                "[ordered]@{sid=$sid;type=[string]$_.AccessControlType;inherited=[bool]$_.IsInherited;rights=[int]$_.FileSystemRights} }); "
                "[ordered]@{sddl=$a.Sddl;rules=$rules} | ConvertTo-Json -Depth 6 -Compress"
            )
            completed = subprocess.run(
                [powershell_exe, '-NoProfile', '-Command', command],
                check=True, capture_output=True, text=True, env=powershell_env,
            )
            return json.loads(completed.stdout)

        paths = {'root': data, 'owner': owner, 'private_key': private_key}
        before_acl = {name: acl(path) for name, path in paths.items()}
        byte_paths = {'lock': lock, 'owner': owner, 'private_key': private_key}
        before_bytes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in byte_paths.items()}
        command = [powershell_exe, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(repair),
                   '-DataRoot', str(data), '-Receipt', str(receipt), '-Apply']
        completed = subprocess.run(command, capture_output=True, text=True, env=powershell_env)
        after_acl = {name: acl(path) for name, path in paths.items()}
        after_bytes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in byte_paths.items()}
        result = {
            'command': command,
            'returncode': completed.returncode,
            'stdout': completed.stdout,
            'stderr': completed.stderr,
            'before_sha256': before_bytes,
            'after_sha256': after_bytes,
            'before_acl': before_acl,
            'after_acl': after_acl,
            'receipt': json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else None,
        }
        (evidence / 'native-acl-test.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(before_bytes, after_bytes)
        self.assertEqual(before_acl['root']['sddl'], after_acl['root']['sddl'])
        self.assertEqual(before_acl['private_key']['sddl'], after_acl['private_key']['sddl'])
        owner_rules = [rule for rule in after_acl['owner']['rules'] if rule['sid'] == 'S-1-5-19'
                       and rule['type'] == 'Allow' and not rule['inherited']]
        self.assertTrue(owner_rules, after_acl['owner'])
        self.assertTrue(any(rule['rights'] & 1 for rule in owner_rules))
        self.assertTrue(all(not (rule['rights'] & 2) and not (rule['rights'] & 4) for rule in owner_rules))
        self.assertEqual(result['receipt']['owner_existed'], True)
        self.assertEqual(result['receipt']['owner_acl_before'], before_acl['owner']['sddl'])
        self.assertEqual(result['receipt']['owner_acl_after'], after_acl['owner']['sddl'])


if __name__ == '__main__':
    unittest.main()
