"""Run the restart helper against local fixtures; never schedule or reboot Windows."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'windows-elevhub/server/server-restart.ps1'
FIXTURE = ROOT / 'tests/fixtures/server_restart_host.ps1'
PS = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


@unittest.skipUnless(os.name == 'nt' and PS.is_file(), 'Windows PowerShell required')
class ServerRestartHostTests(unittest.TestCase):
    def test_selection_lease_allows_running_apps_and_blocks_generation_writer(self):
        from app.generation_lock import GenerationLock
        with tempfile.TemporaryDirectory(prefix='restart-selection-') as folder:
            root = Path(folder)
            (root / 'control').mkdir()
            lock_path = root / 'control/generation.lock'
            lock_path.write_bytes(b'1')
            env = dict(os.environ, RESTART_TEST_SOURCE=str(HELPER), RESTART_TEST_ROOT=str(root))
            command = r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($env:RESTART_TEST_SOURCE),[ref]$tokens,[ref]$errors)
foreach($function in @($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})){Invoke-Expression $function.Extent.Text}
try {$lock=Open-RestartSelectionLock ([pscustomobject]@{Root=$env:RESTART_TEST_ROOT});$lock.Dispose();'unlocked'}catch{'blocked'}
'''
            for exclusive, expected in ((False, 'unlocked'), (True, 'blocked')):
                with self.subTest(exclusive=exclusive), GenerationLock(lock_path, exclusive=exclusive):
                    result = subprocess.run([str(PS), '-NoProfile', '-NonInteractive', '-Command', command],
                                            env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_acl_validator_accepts_read_execute_but_rejects_every_mutation_right(self):
        self.assertTrue(self.invoke(scenario='acl-read')['result']['allowed'])
        for scenario in ('acl-write', 'acl-delete', 'acl-dacl', 'acl-owner', 'acl-child-delete'):
            with self.subTest(scenario=scenario):
                self.assertFalse(self.invoke(scenario=scenario)['result']['allowed'])

    def test_generation_lock_conflicts_with_python_instance_lock(self):
        from app.instance import InstanceLock
        with tempfile.TemporaryDirectory(prefix='restart-lock-') as folder:
            root = Path(folder)
            (root / 'control').mkdir()
            env = dict(os.environ, RESTART_TEST_SOURCE=str(HELPER), RESTART_TEST_ROOT=str(root))
            command = r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($env:RESTART_TEST_SOURCE),[ref]$tokens,[ref]$errors)
foreach($function in @($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})){Invoke-Expression $function.Extent.Text}
try {$lock=Open-RestartGenerationLock ([pscustomobject]@{Root=$env:RESTART_TEST_ROOT});$lock.Dispose();'unlocked'}catch{'blocked'}
'''
            with InstanceLock(root / 'control'):
                result = subprocess.run([str(PS), '-NoProfile', '-NonInteractive', '-Command', command],
                                        env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'blocked')

    def invoke(self, mode='Status', scenario='healthy'):
        self.assertTrue(HELPER.is_file(), 'The fixed restart helper must exist')
        with tempfile.TemporaryDirectory(prefix='restart-host-') as folder:
            result = subprocess.run([str(PS), '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                     'Bypass', '-File', str(FIXTURE), '-Source', str(HELPER),
                                     '-FixtureRoot', folder, '-Mode', mode, '-Scenario', scenario],
                                    capture_output=True, encoding='utf-8-sig', timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_status_is_read_only_and_request_only_queues_fixed_task(self):
        status = self.invoke()
        self.assertTrue(status['result']['available'])
        self.assertEqual(status['events'], [])
        request = self.invoke('Request')
        self.assertTrue(request['result']['accepted'])
        self.assertEqual(request['events'], ['request'])

    def test_equivalent_task_acl_order_is_accepted(self):
        result = self.invoke(scenario='acl-reordered-task')
        self.assertIsNone(result['error'])
        self.assertTrue(result['result']['available'])

    def test_execute_uses_immediate_unforced_reboot_after_delay(self):
        result = self.invoke('Execute')
        self.assertEqual(result['events'], ['delay', 'shutdown'])
        self.assertEqual(result['shutdown_args'], ['/r', '/t', '0', '/d', 'p:4:1', '/c', 'Makervaerkstedet: teacher requested server restart'])

    def test_foreign_task_definition_or_acl_never_queues_or_reboots(self):
        for scenario in ('foreign-action', 'foreign-owner', 'task-trigger', 'task-retry', 'foreign-acl', 'disabled'):
            for mode in ('Request', 'Execute'):
                with self.subTest(scenario=scenario, mode=mode):
                    result = self.invoke(mode, scenario)
                    self.assertTrue(result['error'])
                    self.assertNotIn('request', result['events'])
                    self.assertNotIn('shutdown', result['events'])

    def test_execute_independently_rejects_changed_installation_and_maintenance(self):
        for scenario in ('owner-changed', 'removed', 'maintenance', 'tls-pending', 'generation-transition', 'busy'):
            with self.subTest(scenario=scenario):
                result = self.invoke('Execute', scenario)
                self.assertTrue(result['error'])
                self.assertNotIn('shutdown', result['events'])

    def test_generation_selected_owner_is_validated(self):
        self.assertIn('shutdown', self.invoke('Execute', 'generation')['events'])
        result = self.invoke('Execute', 'generation-owner-changed')
        self.assertTrue(result['error'])
        self.assertNotIn('shutdown', result['events'])

    def test_completed_maintenance_does_not_block(self):
        self.assertIn('shutdown', self.invoke('Execute', 'tls-complete')['events'])

    def test_install_never_starts_and_remove_refuses_foreign_task(self):
        result = self.invoke('Install', 'missing-task')
        self.assertEqual(result['events'], ['register', 'task-acl'])
        result = self.invoke('Remove', 'foreign-action')
        self.assertTrue(result['error'])
        self.assertNotIn('remove', result['events'])

    def test_missing_task_is_unavailable_and_never_created_by_request(self):
        result = self.invoke('Request', 'missing-task')
        self.assertTrue(result['error'])
        self.assertEqual(result['events'], [])

    def test_remove_is_noop_when_optional_feature_was_never_enabled(self):
        result = self.invoke('Remove', 'not-installed')
        self.assertIsNone(result['error'])
        self.assertFalse(result['result']['removed'])
        self.assertEqual(result['events'], [])
