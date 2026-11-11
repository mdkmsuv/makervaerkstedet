"""The pupil's limited sudo command must only open the authenticated console."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'ansible/roles/adminkonto/files/elevhub-admin-login.py'
spec = importlib.util.spec_from_file_location('admin_login', SOURCE)
admin_login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin_login)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Run in Linux as root to test real config ownership.')
class AdminLoginTests(unittest.TestCase):
    def test_fixed_login_console_requires_an_unlocked_separate_account(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'admin.json'
            config.write_text(json.dumps({'username': 'makeradmin'}))
            config.chmod(0o644)
            run = Mock(return_value=subprocess.CompletedProcess([], 0, 'makeradmin:$6$hash:0:0:99999:7:::\n'))
            admin_login.open_login(config, run)
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ['/usr/bin/getent', 'shadow', 'makeradmin'],
                ['/usr/bin/systemctl', 'start', 'getty@tty2.service'],
                ['/usr/bin/chvt', '2'],
            ])
            for username in ('root', 'elev', 'elevhub', 'makeradmin; id'):
                config.write_text(json.dumps({'username': username}))
                run.reset_mock()
                with self.assertRaises(RuntimeError):
                    admin_login.open_login(config, run)
                run.assert_not_called()
            config.write_text(json.dumps({'username': 'makeradmin'}))
            run.reset_mock()
            run.return_value.stdout = 'makeradmin:!:0:0:99999:7:::\n'
            with self.assertRaises(RuntimeError):
                admin_login.open_login(config, run)
            self.assertEqual(run.call_count, 1)

    def test_untrusted_config_and_unprivileged_calls_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'admin.json'
            config.write_text(json.dumps({'username': 'makeradmin'}))
            config.chmod(0o666)
            run = Mock()
            with self.assertRaises(RuntimeError):
                admin_login.open_login(config, run)
            run.assert_not_called()
            config.chmod(0o644)
            with patch.object(admin_login.os, 'geteuid', return_value=1001), self.assertRaises(RuntimeError):
                admin_login.open_login(config, run)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
