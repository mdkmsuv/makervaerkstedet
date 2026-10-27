"""Regression checks for the protected machine installation stop path."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import desktop


ROOT = Path(__file__).resolve().parents[1]


class S2WindowsProtectionTests(unittest.TestCase):
    def test_machine_data_acl_does_not_grant_users(self):
        source = (ROOT / 'scripts/install-background.ps1').read_text(encoding='utf-8')
        data_acl = next(line for line in source.splitlines() if 'icacls.exe $dataPath' in line and '/grant:r' in line)
        self.assertNotIn('S-1-5-32-545', data_acl)
        self.assertIn('S-1-5-19:(OI)(CI)M', data_acl)
        self.assertIn('/T', data_acl)
        remove_acl = next(line for line in source.splitlines() if 'icacls.exe $dataPath' in line and '/remove:g' in line)
        self.assertIn('/remove:g', remove_acl)
        for sid in ('S-1-5-32-545', 'S-1-5-11', 'S-1-1-0'):
            self.assertIn(sid, remove_acl)

    @unittest.skipUnless(shutil.which('icacls.exe'), 'icacls.exe is only available on Windows')
    def test_real_icacls_removes_explicit_users_ace(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'data'
            target.mkdir()
            file = target / 'secret.txt'
            file.write_text('secret', encoding='utf-8')
            sid = '*S-1-5-32-545'
            subprocess.run(['icacls.exe', str(target), '/grant:r', f'{sid}:(OI)(CI)RX'], check=True,
                           capture_output=True, text=True)
            before = subprocess.run(['icacls.exe', str(target)], check=True, capture_output=True, text=True).stdout
            self.assertTrue('Brugere:' in before or 'Users:' in before or 'S-1-5-32-545' in before, before)
            subprocess.run(['icacls.exe', str(target), '/remove:g', sid, '/T'], check=True,
                           capture_output=True, text=True)
            after = subprocess.run(['icacls.exe', str(target)], check=True, capture_output=True, text=True).stdout
            self.assertNotIn('Brugere:', after)
            self.assertNotIn('Users:', after)
            self.assertNotIn('S-1-5-32-545', after)

    def test_boot_registers_a_manual_localservice_stop_task(self):
        source = (ROOT / 'scripts/autostart.ps1').read_text(encoding='utf-8')
        self.assertIn("$stopAction = New-ScheduledTaskAction", source)
        self.assertIn("-m app.desktop --privileged-stop --no-window", source)
        self.assertIn("$TaskName + '-Stop'", source)
        self.assertIn("-UserId 'S-1-5-19' -LogonType ServiceAccount", source)
        self.assertIn("(A;;GRGX;;;BU)", source)

    def test_shortcut_stop_runs_fixed_task_without_reading_token(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'school'
            folder.mkdir()
            calls = []
            health_values = iter([{'ok': True, 'service': 'printvaerksted',
                                   'instance': desktop.instance_id(folder), 'demo': False}, None])
            with mock.patch.object(desktop, 'installation', return_value={'task_name': 'Printvaerksted-Test', 'autostart': 'Boot'}), \
                 mock.patch.object(desktop, 'health', side_effect=lambda url: next(health_values)), \
                 mock.patch.object(desktop.subprocess, 'run', side_effect=lambda *args, **kwargs: calls.append(args[0]) or mock.Mock(returncode=0)) as run:
                desktop.stop_service(folder, 19876, False)
            self.assertEqual(calls, [['schtasks.exe', '/Run', '/TN', 'Printvaerksted-Test-Stop']])
            run.assert_called_once()
            self.assertFalse((folder / 'laererkode.txt').exists())

    def test_privileged_stop_keeps_bearer_compatibility(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'school'
            folder.mkdir()
            (folder / 'laererkode.txt').write_text('teacher-token\n', encoding='utf-8')
            requests = []

            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self): return b'{}'

            def open_url(request, timeout):
                requests.append(request)
                return Response()

            health_values = iter([{'ok': True, 'service': 'printvaerksted',
                                   'instance': desktop.instance_id(folder), 'demo': False}, None])
            with mock.patch.object(desktop, 'installation', return_value={}), \
                 mock.patch.object(desktop, 'health', side_effect=lambda url: next(health_values)), \
                 mock.patch.object(desktop, 'urlopen', side_effect=open_url):
                desktop.stop_service(folder, 19876, False, privileged=True)
            self.assertEqual(requests[0].get_header('Authorization'), 'Bearer teacher-token')

    def test_privileged_stop_does_not_try_to_stop_user_demo_data(self):
        source = (ROOT / 'app/desktop.py').read_text(encoding='utf-8')
        self.assertIn("modes = ((False, service_port()),) if args.privileged_stop", source)


if __name__ == '__main__':
    unittest.main()
