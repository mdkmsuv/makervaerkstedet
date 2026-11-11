import json
from contextlib import nullcontext
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from app import school_tls


class Controller:
    def __init__(self):
        self.calls = []
        self.fail = None

    def capture(self, runtime, record):
        self.calls.append('capture')
        return {'format': 1, 'tasks': ['ElevhubServer'],
                'states': [{'name': 'ElevhubServer', 'enabled': True, 'running': True}]}

    def stop(self, runtime, record, receipt):
        self.calls.append('stop')
        if self.fail == 'stop':
            raise RuntimeError('stop interrupted')

    def start(self, runtime, record, receipt):
        self.calls.append('start')


class SchoolTlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name) / 'runtime'
        self.control = self.runtime / 'control'
        self.program = self.runtime / 'program'
        self.data = Path(self.tmp.name) / 'data'
        for path in (self.control, self.program, self.data / 'security'):
            path.mkdir(parents=True)
        (self.data / 'konfiguration.json').write_text(
            json.dumps({'elev_adresse': '192.0.2.2'}), encoding='utf-8')
        self.record = {
            'format': 1, 'installation_id': 'a' * 32, 'revision': 1,
            'runtime_root': str(self.runtime), 'program_root': str(self.program),
            'data_root': str(self.data), 'manifest_sha256': 'b' * 64,
        }
        self.controller = Controller()
        self.selector = lambda runtime: dict(self.record)
        self.renew_calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def renew(self, security, server_ip, runtime, operation_id):
        self.renew_calls.append((security, server_ip, runtime))
        return {'schemaVersion': 1, 'state': 'complete', 'operationId': operation_id,
                'serverIp': server_ip, 'oldServerSha256': 'a' * 64,
                'newServerSha256': 'b' * 64, 'caSha256': 'c' * 64,
                'keySha256': 'd' * 64}

    def execute(self):
        return school_tls.execute(runtime_root=self.runtime,
                                  controller=self.controller,
                                  selector=self.selector, renewer=self.renew,
                                  locker=lambda record: nullcontext())

    def test_complete_flow_and_idempotent_retry(self):
        result = self.execute()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.controller.calls, ['capture', 'stop', 'start'])
        self.assertEqual(len(self.renew_calls), 1)
        journal = json.loads((self.control / school_tls.JOURNAL).read_text())
        self.assertEqual(journal['phase'], 'complete')
        self.assertEqual(school_tls.execute(runtime_root=self.runtime, resume=True,
                                            controller=self.controller, selector=self.selector,
                                            renewer=self.renew, locker=lambda record: nullcontext())['state'], 'complete')
        self.assertEqual(self.controller.calls, ['capture', 'stop', 'start'])
        self.assertEqual(len(self.renew_calls), 1)
        self.assertEqual(self.execute()['state'], 'complete')
        self.assertEqual(self.controller.calls, ['capture', 'stop', 'start', 'capture', 'stop', 'start'])
        self.assertEqual(len(self.renew_calls), 2)

    def test_renew_failure_is_journalled_and_resume_does_not_recapture(self):
        failed = {'value': True}

        def renew(security, server_ip, runtime, operation_id):
            self.renew_calls.append(1)
            if failed['value']:
                failed['value'] = False
                raise RuntimeError('renew interrupted')
            return {'schemaVersion': 1, 'state': 'complete', 'operationId': operation_id,
                    'serverIp': server_ip, 'oldServerSha256': 'a' * 64,
                    'newServerSha256': 'b' * 64, 'caSha256': 'c' * 64,
                    'keySha256': 'd' * 64}

        with self.assertRaisesRegex(RuntimeError, 'renew interrupted'):
            school_tls.execute(runtime_root=self.runtime, controller=self.controller,
                               selector=self.selector, renewer=renew,
                               locker=lambda record: nullcontext())
        journal = json.loads((self.control / school_tls.JOURNAL).read_text())
        self.assertEqual(journal['phase'], 'renewing')
        school_tls.execute(runtime_root=self.runtime, controller=self.controller,
                           selector=self.selector, renewer=renew,
                           locker=lambda record: nullcontext())
        self.assertEqual(self.controller.calls, ['capture', 'stop', 'start'])
        self.assertEqual(len(self.renew_calls), 2)

    def test_selection_change_is_rejected_before_existing_journal_stops(self):
        self.controller.fail = 'stop'
        with self.assertRaises(RuntimeError):
            self.execute()
        self.controller.calls.clear()
        changed = dict(self.record, revision=2)
        with self.assertRaisesRegex(ValueError, 'generationsvalg'):
            school_tls.execute(runtime_root=self.runtime, controller=self.controller,
                               selector=lambda runtime: changed, renewer=self.renew)
        self.assertEqual(self.controller.calls, [])

    def test_completed_history_allows_renewal_of_a_later_generation(self):
        self.execute()
        previous = json.loads((self.control / school_tls.JOURNAL).read_text())
        self.record = dict(self.record, revision=2)
        result = self.execute()
        current = json.loads((self.control / school_tls.JOURNAL).read_text())
        self.assertEqual(result['state'], 'complete')
        self.assertNotEqual(current['operation_id'], previous['operation_id'])
        self.assertEqual(current['source_record']['revision'], 2)
        self.assertEqual(self.controller.calls, ['capture', 'stop', 'start', 'capture', 'stop', 'start'])
        self.assertTrue((self.control / ('school-tls-renewal-' + previous['operation_id'] + '.json')).is_file())

    def test_legacy_without_selection_fails_before_controller(self):
        with self.assertRaises(Exception):
            school_tls.execute(runtime_root=self.runtime, controller=self.controller,
                               selector=lambda runtime: (_ for _ in ()).throw(ValueError('legacy')),
                               renewer=self.renew)
        self.assertEqual(self.controller.calls, [])

    def test_selection_change_after_crypto_blocks_start(self):
        changed = {'value': False}

        def selector(runtime):
            return dict(self.record, revision=2) if changed['value'] else dict(self.record)

        def renew(security, server_ip, runtime, operation_id):
            changed['value'] = True
            return {'schemaVersion': 1, 'state': 'complete', 'operationId': operation_id,
                    'serverIp': server_ip, 'oldServerSha256': 'a' * 64,
                    'newServerSha256': 'b' * 64, 'caSha256': 'c' * 64,
                    'keySha256': 'd' * 64}

        with self.assertRaisesRegex(ValueError, 'genstart'):
            school_tls.execute(runtime_root=self.runtime, controller=self.controller,
                               selector=selector, renewer=renew,
                               locker=lambda record: nullcontext())
        self.assertEqual(self.controller.calls, ['capture', 'stop'])

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'),
                         'PowerShell-menufixture kræver Windows PowerShell.')
    def test_menu_function_blocks_legacy_before_any_task_command(self):
        script = (Path(__file__).resolve().parents[1] / 'deployment' / 'school-setup' /
                  'Manage-School.ps1').as_posix().replace("'", "''")
        command = ("$source=Get-Content -Raw -LiteralPath '" + script + "'; "
                   "$tokens=$null; $errors=$null; $tree=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors); "
                   "$fn=$tree.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-TlsRenewal'},$true)[0]; "
                   ". ([scriptblock]::Create($fn.Extent.Text)); $startupMode='legacy'; "
                   "try { Invoke-TlsRenewal; exit 2 } catch { if($_.Exception.Message -notmatch 'generation-v1'){exit 3}; 'blocked' }")
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-Command', command],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('blocked', result.stdout)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'),
                         'PowerShell-menufixture kræver Windows PowerShell.')
    def test_menu_function_calls_fixed_school_tls_operator_without_task_cmdlets(self):
        root = Path(self.tmp.name)
        fake = root / 'fixed python.cmd'
        log = root / 'operator-args.txt'
        fake.write_text('@echo off\r\necho %* > "' + str(log) + '"\r\necho {"ok":true,"state":"complete"}\r\n', encoding='utf-8')
        script = (Path(__file__).resolve().parents[1] / 'deployment' / 'school-setup' /
                  'Manage-School.ps1').as_posix().replace("'", "''")
        fake_text = str(fake).replace("'", "''")
        package = str(root).replace("'", "''")
        command = ("$source=Get-Content -Raw -LiteralPath '" + script + "'; "
                   "$tokens=$null; $errors=$null; $tree=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors); "
                   "$fn=$tree.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-TlsRenewal'},$true)[0]; "
                   ". ([scriptblock]::Create($fn.Extent.Text)); "
                   "function global:Get-ScheduledTask { throw 'task cmdlet called' }; function global:Stop-ScheduledTask { throw 'task cmdlet called' }; "
                   "function global:Start-ScheduledTask { throw 'task cmdlet called' }; function global:Get-CimInstance { throw 'task cmdlet called' }; "
                   "$startupMode='generation-v1'; $PackageRoot='" + package + "'; $python='" + fake_text + "'; "
                   "Invoke-TlsRenewal")
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-Command', command],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = log.read_text(encoding='utf-8').strip()
        self.assertRegex(arguments, r'(^|\s)-B\s+-I\s+-m\s+app\.school_tls\s+renew\s+--runtime-root\s+')


if __name__ == '__main__':
    unittest.main()
