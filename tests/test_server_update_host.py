"""Exercise the real update bridge with inert scheduler/process doubles."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'system/windows-elevhub/server/server-update.ps1'
FIXTURE = ROOT / 'tests/fixtures/server_update_host.ps1'
PS = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
COMMAND = '11111111-2222-3333-4444-555555555555'


@unittest.skipUnless(os.name == 'nt' and PS.is_file(), 'Windows PowerShell required')
class ServerUpdateHostTests(unittest.TestCase):
    def test_status_does_not_require_the_enqueue_mutex(self):
        value = self.invoke('Status', 'denied-mutex')
        self.assertIsNone(value['error'])
        self.assertTrue(value['result']['available'])
        self.assertEqual(value['events'], [])

    def test_execute_disposes_creator_handle_before_worker_or_installer(self):
        value = self.invoke('Execute', 'mutex-lifetime')
        self.assertIsNone(value['error'])
        self.assertTrue(value['mutex_disposed_before_worker'])
        self.assertEqual(value['events'], ['python-prepare', 'installer', 'python-finish'])

    def test_real_kernel_mutex_uses_minimal_rights_with_live_creator_handle(self):
        value = self.invoke(scenario='kernel-mutex')
        self.assertIsNone(value['error'])
        result = value['result']
        self.assertEqual(result['rule_count'], 3)
        self.assertEqual(result['localservice_rule_count'], 1)
        self.assertEqual(result['localservice_rights'], 0x100001)  # Synchronize | Modify
        for name in ('protected', 'busy_while_owned', 'opens_after_release',
                     'creator_handle_still_alive', 'default_full_open_denied',
                     'minimal_open_works', 'existing_acl_unchanged'):
            self.assertTrue(result[name], name)

    def invoke(self, mode='Status', scenario='healthy', command=COMMAND, manifest='a' * 64):
        self.assertTrue(HELPER.is_file())
        with tempfile.TemporaryDirectory(prefix='server-update-host-') as folder:
            result = subprocess.run([str(PS), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', str(FIXTURE), '-Source', str(HELPER), '-FixtureRoot', folder,
                '-Mode', mode, '-Scenario', scenario, '-CommandId', command, '-ManifestSha256', manifest],
                capture_output=True, encoding='utf-8-sig', timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_status_never_queues_or_installs(self):
        value = self.invoke()
        self.assertIsNone(value['error'])
        self.assertTrue(value['result']['available'])
        self.assertEqual(value['events'], [])

    def test_check_and_install_only_enqueue_fixed_inbox_and_task(self):
        for mode, action in [('Check', 'check'), ('Request', 'install')]:
            with self.subTest(mode=mode):
                value = self.invoke(mode)
                self.assertIsNone(value['error'])
                self.assertEqual(value['events'], ['task-run'])
                self.assertEqual(value['request']['action'], action)
                self.assertEqual(value['request']['command_id'], COMMAND)
                self.assertEqual(value['request']['manifest_sha256'], None if mode == 'Check' else 'a' * 64)
                self.assertTrue(value['task_arguments_null'])

    def test_receipt_idempotency_and_conflicts(self):
        self.assertEqual(self.invoke('Request', 'receipt')['events'], [])
        for scenario in ('receipt-conflict', 'pending-conflict'):
            value = self.invoke('Request', scenario)
            self.assertTrue(value['error'])
            self.assertEqual(value['events'], [])
        self.assertTrue(self.invoke('Request', 'pending')['result']['already_pending'])

    def test_untrusted_task_owner_or_acl_is_never_repaired_by_request(self):
        for scenario in ('foreign-action', 'foreign-owner', 'foreign-acl', 'task-trigger', 'task-retry',
                         'extra-action', 'missing-task', 'owner-changed', 'store-owner-changed', 'task-logon'):
            with self.subTest(scenario=scenario):
                value = self.invoke('Request', scenario)
                self.assertTrue(value['error'])
                self.assertEqual(value['events'], [])

    def test_invalid_command_and_manifest_cannot_become_arguments(self):
        for command, manifest in [('not-a-guid;whoami', 'a' * 64), (COMMAND, 'A' * 64),
                                  (COMMAND, 'a' * 64 + ';whoami')]:
            value = self.invoke('Request', command=command, manifest=manifest)
            self.assertTrue(value['error'])
            self.assertEqual(value['events'], [])

    def test_execute_waits_for_prepare_then_installer_then_finish(self):
        value = self.invoke('Execute')
        self.assertIsNone(value['error'])
        self.assertEqual(value['events'], ['python-prepare', 'installer', 'python-finish'])
        self.assertEqual(value['installer_flags'][:4], ['/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])
        self.assertEqual(value['installer_flags'][4], '/UPDATEID=' + COMMAND)
        self.assertTrue(value['installer_flags'][5].startswith('/LOG='))
        self.assertTrue(value['installer_wait_hidden'])
        self.assertTrue(value['cwd_is_store'])

    def test_execute_check_never_starts_installer(self):
        value = self.invoke('Execute', 'check-request')
        self.assertIsNone(value['error'])
        self.assertEqual(value['events'], ['python-check'])

    def test_unsafe_plan_or_changed_exe_never_runs(self):
        for scenario in ('plan-escape', 'plan-relative', 'plan-command', 'plan-extra', 'plan-bad-hash', 'plan-hash-mismatch'):
            with self.subTest(scenario=scenario):
                value = self.invoke('Execute', scenario)
                self.assertTrue(value['error'])
                self.assertNotIn('installer', value['events'])

    def test_failed_installer_exit_is_passed_to_finish(self):
        value = self.invoke('Execute', 'installer-failed')
        self.assertIsNone(value['error'])
        self.assertEqual(value['finish_exit_code'], 42)
        self.assertEqual(value['events'], ['python-prepare', 'installer', 'python-finish'])

    def test_interrupted_status_is_read_only_and_fresh_queue_is_not_interrupted(self):
        self.assertEqual(self.invoke('Status', 'interrupted')['result']['state']['state'], 'interrupted')
        value = self.invoke('Status', 'fresh-queue')
        self.assertNotEqual(value['result']['state']['state'], 'interrupted')
        self.assertEqual(value['events'], [])

    def test_install_is_demand_only_and_foreign_remove_is_rejected(self):
        value = self.invoke('Install', 'missing-task')
        self.assertIsNone(value['error'])
        self.assertEqual(value['events'], ['initialize', 'register', 'task-acl'])
        value = self.invoke('Remove', 'foreign-action')
        self.assertTrue(value['error'])
        self.assertNotIn('remove', value['events'])

    def test_protected_acl_rejects_localservice_mutation(self):
        self.assertTrue(self.invoke(scenario='acl-read')['result']['allowed'])
        for scenario in ('acl-write', 'acl-delete', 'acl-dacl', 'acl-owner', 'acl-child-delete'):
            with self.subTest(scenario=scenario):
                self.assertFalse(self.invoke(scenario=scenario)['result']['allowed'])

    def test_only_inbox_grants_localservice_write(self):
        value = self.invoke(scenario='directory-acls')
        self.assertIsNone(value['error'])
        self.assertFalse(value['result']['store_write'])
        self.assertTrue(value['result']['inbox_write'])

    def test_worker_failure_is_generic_and_newer_inbox_is_preserved(self):
        value = self.invoke('Execute', 'prepare-failed')
        self.assertEqual(value['events'], ['python-prepare', 'python-fail'])
        self.assertNotIn('sensitive stderr', value['error'])
        self.assertIsNone(value['request'])
        value = self.invoke('Execute', 'newer-inbox')
        self.assertIsNone(value['error'])
        self.assertEqual(value['request']['command_id'], 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff')

    def test_remove_is_noop_when_never_installed(self):
        value = self.invoke('Remove', 'not-installed')
        self.assertIsNone(value['error'])
        self.assertFalse(value['result']['removed'])
        self.assertEqual(value['events'], [])

    def test_python_worker_has_fixed_isolated_arguments(self):
        value = self.invoke(scenario='python-arguments')
        self.assertIsNone(value['error'])
        calls = value['result']['calls']
        for action, arguments in zip(('check', 'prepare', 'finish', 'fail'), calls):
            expected = ['-I', '-B', value['result']['worker'], '--action', action,
                        '--store', value['result']['store']]
            if action == 'finish':
                expected += ['--exit-code', '42']
            if action == 'fail':
                expected += ['--code', 'worker_failed']
            self.assertEqual(arguments, expected)

    def test_malformed_inbox_never_reaches_worker(self):
        for scenario in ('request-boolean-schema', 'request-extra-field'):
            value = self.invoke('Execute', scenario)
            self.assertTrue(value['error'])
            self.assertEqual(value['events'], [])

    def test_atomic_request_replacement_uses_true_null_backup_on_windows_powershell(self):
        value = self.invoke(scenario='replace-request')
        self.assertIsNone(value['error'])
        self.assertEqual(value['result']['value'], 2)
        self.assertEqual(value['result']['pending'], 0)

    def test_remove_already_absent_task_preserves_owned_store(self):
        value = self.invoke('Remove', 'missing-task-preserved-store')
        self.assertIsNone(value['error'])
        self.assertFalse(value['result']['removed'])
        self.assertTrue(value['result']['store_preserved'])
        self.assertEqual(value['events'], [])
