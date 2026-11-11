"""Security and workflow tests for the local administrator updater."""
import importlib.util
import ast
import inspect
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'ansible/roles/adminkonto/files/elevhub-local-update.py'
spec = importlib.util.spec_from_file_location('local_admin_update', SOURCE)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class LocalAdminUpdateTests(unittest.TestCase):
    def test_cli_accepts_only_fixed_actions_without_arguments(self):
        for action in ('state', 'check', 'update', 'restart', 'retry'):
            self.assertEqual(updater.parse_action([action]), action)
        for argv in ([], ['update', '/etc/passwd'], ['--action', 'update'], ['unknown']):
            with self.assertRaises(ValueError):
                updater.parse_action(argv)

    def test_version_comparison_handles_prereleases_and_four_part_versions(self):
        self.assertLess(updater._version_key('2.4.0-rc.1'), updater._version_key('2.4.0'))
        self.assertLess(updater._version_key('2.4.0-alpha.9'), updater._version_key('2.4.0-alpha.10'))
        self.assertGreater(updater._version_key('2026.10.27.1'), updater._version_key('2026.10.27'))
        self.assertEqual(updater._version_key('2.4.0+build.2'), updater._version_key('2.4.0'))

    def test_restart_requires_matching_staged_release_before_reboot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approval = {'schema': 1, 'computer': 'vaerksted-05', 'version': '2.4.0',
                        'package_sha256': 'a' * 64, 'restart_authorized': False}
            calls = []
            result = updater.authorize_restart(
                approval, computer='vaerksted-05',
                staged={'status': 'staged_awaiting_restart', 'computer': 'vaerksted-05',
                        'version': '2.4.0',
                        'package_sha256': 'a' * 64, 'release_id': 'r7',
                        'operation': 'github-linux-' + 'a' * 24,
                        'receipt_computer': 'vaerksted-05'},
                preservation_needed=False,
                write_approval=lambda value: calls.append(('approval', value.copy())),
                reboot=lambda: calls.append(('reboot', None)))
            self.assertTrue(result)
            self.assertEqual([item[0] for item in calls], ['approval', 'reboot'])
            self.assertTrue(calls[0][1]['restart_authorized'])

    def test_restart_fails_closed_for_missing_or_mismatched_stage_and_preservation(self):
        approval = {'schema': 1, 'computer': 'vaerksted-05', 'version': '2.4.0',
                    'package_sha256': 'a' * 64, 'restart_authorized': False}
        staged = {'status': 'staged_awaiting_restart', 'computer': 'vaerksted-05',
                  'version': '2.4.0',
                  'package_sha256': 'a' * 64, 'release_id': 'r7',
                  'operation': 'github-linux-' + 'a' * 24,
                  'receipt_computer': 'vaerksted-05'}
        for stage, preservation in ((None, False), ({**staged, 'version': '2.5.0'}, False),
                                    ({**staged, 'receipt_computer': 'other'}, False),
                                    (staged, True), ({**staged, 'status': 'applying'}, False)):
            events = []
            with self.assertRaises(updater.UpdateError):
                updater.authorize_restart(
                    approval, computer='vaerksted-05', staged=stage,
                    preservation_needed=preservation,
                    write_approval=lambda value: events.append('approval'),
                    reboot=lambda: events.append('reboot'))
            self.assertEqual(events, [])

    def test_device_token_is_read_only_by_helper_and_never_in_state(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            updater.write_json(paths.kiosk, {'hub': 'https://hub.example:8773',
                                             'computer': 'vaerksted-05',
                                             'ca_fil': '/etc/elevhub/ca.pem'})
            updater.write_json(paths.device, {'computer': 'vaerksted-05',
                                               'token': chr(120) * 64,
                                               'root_token': chr(121) * 64})
            updater.write_json(paths.release, {'version': '2.3.0'})
            updater.write_json(paths.admin, {'username': 'makeradmin'})
            client = Mock()
            client.hent_json.return_value = {
                'schema': 1, 'computer': 'vaerksted-05',
                'available': {'version': '2.4.0', 'package_sha256': 'a' * 64,
                              'release_id': 'r7', 'notes': 'Rettelser'},
                'job': None, 'request': None, 'feed_state': 'ready'}
            result = updater.read_remote_state(paths, client=client)
            self.assertEqual(result['installed_version'], '2.3.0')
            self.assertEqual(result['available']['version'], '2.4.0')
            self.assertFalse(result['install_ready'])
            self.assertNotIn('token', json.dumps(result))
            self.assertEqual(client.hent_json.call_args.kwargs['token'], 'x' * 64)

    def test_second_update_press_reuses_persisted_request_id_and_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            client = Mock()
            client.hent_json.return_value = self._state()
            updater.prepare_update(paths, client=client, request_id_factory=lambda: '1' * 32)
            updater.prepare_update(paths, client=client, request_id_factory=lambda: '2' * 32)
            request_ids = [call.kwargs['data']['request_id'] for call in client.hent_json.call_args_list
                           if call.kwargs['data'] is not None]
            self.assertEqual(request_ids, ['1' * 32, '1' * 32])
            self.assertEqual(json.loads(paths.approval.read_text())['restart_authorized'], False)
            self.assertEqual(json.loads(paths.request.read_text())['request_id'], '1' * 32)
            self.assertNotIn('dateformat', client.hent_json.call_args.kwargs['data'])

    def test_active_different_server_intent_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            old_approval = {'schema': 1, 'computer': 'vaerksted-05', 'version': '2.2.0',
                            'package_sha256': 'c' * 64, 'restart_authorized': False}
            old_request = {'schema': 1, 'computer': 'vaerksted-05', 'request_id': '4' * 32,
                           'version': '2.2.0', 'package_sha256': 'c' * 64}
            updater.write_json(paths.approval, old_approval)
            updater.write_json(paths.request, old_request)
            client = Mock()
            client.hent_json.return_value = {
                **self._state(), 'request': {'status': 'waiting_for_package',
                                             'version': '2.2.0',
                                             'package_sha256': 'c' * 64, 'error': ''}}
            with self.assertRaisesRegex(updater.UpdateError, '2.2.0'):
                updater.prepare_update(paths, client=client,
                                      request_id_factory=lambda: '5' * 32)
            self.assertEqual(json.loads(paths.approval.read_text()), old_approval)
            self.assertEqual(json.loads(paths.request.read_text()), old_request)
            self.assertEqual(client.hent_json.call_count, 1)

    def test_explicit_server_rejection_restores_old_approval_and_request(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            old_approval = {'schema': 1, 'computer': 'vaerksted-05', 'version': '2.2.0',
                            'package_sha256': 'c' * 64, 'restart_authorized': False}
            old_request = {'schema': 1, 'computer': 'vaerksted-05', 'request_id': '4' * 32,
                           'version': '2.2.0', 'package_sha256': 'c' * 64}
            updater.write_json(paths.approval, old_approval)
            updater.write_json(paths.request, old_request)
            rejected = urllib.error.HTTPError('https://hub.example', 400, 'rejected', {}, None)
            client = Mock()
            client.hent_json.side_effect = [self._state() | {'request': None}, rejected]
            try:
                with self.assertRaises(updater.ServerRejected):
                    updater.prepare_update(paths, client=client,
                                          request_id_factory=lambda: '5' * 32)
            finally:
                rejected.close()
            self.assertEqual(json.loads(paths.approval.read_text()), old_approval)
            self.assertEqual(json.loads(paths.request.read_text()), old_request)

    def test_manual_check_posts_a_fresh_nonce_then_reads_status(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths, installed={'release_id': '2.3.0'})
            client = Mock()
            client.hent_json.return_value = self._state()
            result = updater.check_update(paths, client=client,
                                         request_id_factory=lambda: '3' * 32)
            calls = client.hent_json.call_args_list
            self.assertEqual(calls[0].kwargs['data'], {'request_id': '3' * 32, 'action': 'check'})
            self.assertIsNone(calls[1].kwargs['data'])
            self.assertEqual(result['installed_version'], '2.3.0')

    def test_each_retry_click_persists_a_fresh_request_nonce(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            updater.write_json(paths.approval, {
                'schema': 1, 'computer': 'vaerksted-05', 'version': '2.4.0',
                'package_sha256': 'a' * 64, 'restart_authorized': False})
            updater.write_json(paths.request, {
                'schema': 1, 'computer': 'vaerksted-05', 'request_id': '1' * 32,
                'version': '2.4.0', 'package_sha256': 'a' * 64})
            client = Mock()
            client.hent_json.return_value = self._state()
            updater.retry_update(paths, client=client, request_id_factory=lambda: '2' * 32)
            updater.retry_update(paths, client=client, request_id_factory=lambda: '3' * 32)
            ids = [call.kwargs['data']['request_id'] for call in client.hent_json.call_args_list]
            self.assertEqual(ids, ['2' * 32, '3' * 32])
            self.assertEqual(json.loads(paths.request.read_text())['retry_request_id'], '3' * 32)
            self.assertTrue(all('dateformat' not in call.kwargs['data'] for call in client.hent_json.call_args_list))

    def test_retry_without_saved_update_does_not_contact_server(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            client = Mock()
            with self.assertRaises(updater.UpdateError):
                updater.retry_update(paths, client=client)
            client.hent_json.assert_not_called()

    def test_gui_uses_one_sudo_action_without_root_credentials_or_payload(self):
        gui = SOURCE.parent / 'elevhub-local-update-gui.py'
        source = gui.read_text(encoding='utf-8')
        ast.parse(source, filename=str(gui))
        self.assertIn("['sudo', '-n', HELPER, action]", source)
        self.assertNotIn('root_token', source)
        self.assertNotIn('package_sha256', source)
        self.assertIn("busy['value']", source)
        self.assertIn("button.configure(state='disabled' if value else 'normal')", source)
        self.assertIn("value or not install_ready['value']", source)
        self.assertIn('bestillingen fortsætter', source)
        self.assertIn('Den godkendte, hentede opdatering kan stadig installeres.', source)
        self.assertIn('root.after(15000, poll_status)', source)
        self.assertIn("text='Log ud', command=root.destroy", source)

    def test_gui_starts_with_fake_tk_without_display(self):
        gui_path = SOURCE.parent / 'elevhub-local-update-gui.py'
        gui_spec = importlib.util.spec_from_file_location('local_admin_update_gui', gui_path)
        gui = importlib.util.module_from_spec(gui_spec)
        gui_spec.loader.exec_module(gui)

        class Root:
            def title(self, *_): pass
            def geometry(self, *_): pass
            def minsize(self, *_): pass
            def configure(self, **_): pass
            def protocol(self, *_): pass
            def destroy(self): pass
            def after(self, *_): pass
            def mainloop(self): pass

        class Var:
            def __init__(self, value=''): self.value = value
            def set(self, value): self.value = value
            def get(self): return self.value

        class Widget:
            def __init__(self, *_args, **_kwargs): pass
            def pack(self, **_kwargs): pass
            def configure(self, **_kwargs): pass

        class Style:
            def __init__(self, *_): pass
            def theme_use(self, *_): pass
            def configure(self, *_args, **_kwargs): pass
            def map(self, *_args, **_kwargs): pass

        with (patch.object(gui.tk, 'Tk', Root),
              patch.object(gui.tk, 'StringVar', Var),
              patch.object(gui.ttk, 'Style', Style),
              patch.object(gui.ttk, 'Frame', Widget),
              patch.object(gui.ttk, 'Label', Widget),
              patch.object(gui.ttk, 'Button', Widget)):
            gui.main()

    def test_actual_installed_client_accepts_the_helper_call_signature(self):
        client_path = Path(__file__).resolve().parents[1] / 'ansible/roles/kiosk/files/elevhub_klient.py'
        client_spec = importlib.util.spec_from_file_location('actual_elevhub_client', client_path)
        client = importlib.util.module_from_spec(client_spec)
        client_spec.loader.exec_module(client)
        parameters = inspect.signature(client.hent_json).parameters
        self.assertTrue({'token', 'computer', 'ca_fil', 'data', 'timeout'} <= set(parameters))

    def test_update_only_ansible_entry_does_not_manage_admin_credentials(self):
        task_file = SOURCE.parent.parent / 'tasks/update-interface.yml'
        source = task_file.read_text(encoding='utf-8')
        self.assertIn('/etc/elevhub/admin-login.json', source)
        self.assertIn('python3-tk', source)
        self.assertIn('xinit', source)
        self.assertIn('adminkonto_update_interface_paths', source)
        self.assertIn('elevhub-admin-return.py', source)
        self.assertNotIn('ansible.builtin.user:', source)
        self.assertNotIn('password:', source)

    def test_graphical_session_is_local_tty2_only_and_falls_back_to_shell(self):
        session = (SOURCE.parent / 'elevhub-admin-session.sh').read_text(encoding='utf-8')
        profile = (SOURCE.parent.parent / 'templates/admin-bash-profile.j2').read_text(encoding='utf-8')
        tasks = (SOURCE.parent.parent / 'tasks/update-interface.yml').read_text(encoding='utf-8')
        self.assertIn('/dev/tty2', session)
        self.assertIn('SSH_CONNECTION', session)
        self.assertIn('startx', session)
        self.assertIn('elevhub-admin-return', session)
        self.assertLess(session.index('startx '), session.index('elevhub-admin-return'))
        self.assertIn('fortsat logget ind i den lokale terminal', session)
        self.assertIn('/dev/tty2', profile)
        self.assertIn('SSH_CONNECTION', profile)
        self.assertIn('ELEVHUB_ADMIN_SESSION_STARTED', profile)
        self.assertIn('adminkonto_ui_bruger | default(adminkonto_navn)', profile)
        self.assertIn('exit 0', profile)
        self.assertNotIn('/home/', tasks)

    def test_sudo_helper_requires_configured_admin_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            with patch.object(updater.os, 'geteuid', return_value=0, create=True):
                with self.assertRaises(updater.UpdateError):
                    updater._authorize_sudo(paths, {'SUDO_USER': 'elev'})
                updater._authorize_sudo(paths, {'SUDO_USER': 'makeradmin'})

    def test_return_helper_switches_only_to_tty1_for_configured_admin(self):
        source = SOURCE.parent / 'elevhub-admin-return.py'
        spec = importlib.util.spec_from_file_location('admin_return', source)
        returner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(returner)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'admin.json'
            config.write_text(json.dumps({'username': 'makeradmin'}), encoding='utf-8')
            config.chmod(0o644)
            calls = []
            with self.assertRaises(RuntimeError):
                returner.return_to_pupil(config, run=lambda argv, check: calls.append(argv),
                                         euid=0, environ={'SUDO_USER': 'elev'})
            self.assertEqual(calls, [])
            returner.return_to_pupil(config, run=lambda argv, check: calls.append((argv, check)),
                                     euid=0, environ={'SUDO_USER': 'makeradmin'})
            self.assertEqual(calls, [(['/usr/bin/chvt', '1'], True)])

    def test_large_pinned_manifest_is_bounded_but_not_limited_to_json_status_size(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            digest = 'a' * 64
            self._stage(paths, digest)
            stage = paths.update_root / ('github-linux-' + digest[:24])
            manifest = {'version': '2.4.0',
                        'files': {f'assets/file-{index}.py': 'b' * 64
                                  for index in range(2200)}}
            updater.write_json(stage / 'pins.json', {
                'payload_sha256': 'b' * 64, 'package_sha256': digest,
                'release': '2.4.0', 'manifest': manifest})
            self.assertGreater((stage / 'pins.json').stat().st_size, updater.MAX_JSON)
            self.assertTrue(updater._local_stage_ready(paths, 'vaerksted-05'))

    def test_restart_reboots_only_for_root_owned_package_bound_to_saved_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            digest = 'a' * 64
            self._stage(paths, digest)
            calls = []
            result = updater._restart(paths, run=lambda argv, check: calls.append((argv, check)))
            self.assertEqual(result['status'], 'restarting')
            self.assertEqual(calls, [(['/usr/bin/systemctl', 'reboot'], True)])
            self.assertTrue(json.loads(paths.approval.read_text())['restart_authorized'])

    def test_verified_download_remains_installable_when_server_is_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            self._stage(paths, 'a' * 64)
            client = Mock()
            client.hent_json.side_effect = OSError('synthetic offline')
            state = updater.read_remote_state(paths, client=client)
            self.assertEqual(state['feed_state'], 'offline')
            self.assertTrue(state['install_ready'])
            calls = []
            updater._restart(paths, run=lambda argv, check: calls.append((argv, check)))
            self.assertEqual(calls, [(['/usr/bin/systemctl', 'reboot'], True)])

    def test_restart_refuses_unbound_or_missing_stage_without_reboot(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = updater.Paths.for_test(Path(directory))
            self._configure(paths)
            digest = 'a' * 64
            updater.write_json(paths.approval, {
                'schema': 1, 'computer': 'vaerksted-05', 'version': '2.4.0',
                'package_sha256': digest, 'restart_authorized': False})
            calls = []
            with self.assertRaises(updater.UpdateError):
                updater._restart(paths, run=lambda *args, **kwargs: calls.append(args))
            self.assertEqual(calls, [])
            self.assertFalse(json.loads(paths.approval.read_text())['restart_authorized'])

    def _configure(self, paths, installed=None):
        updater.write_json(paths.kiosk, {'hub': 'https://hub.example:8773',
                                         'computer': 'vaerksted-05',
                                         'ca_fil': str(paths.ca)})
        updater.write_json(paths.device, {'computer': 'vaerksted-05',
                                            'token': chr(120) * 64})
        updater.write_json(paths.release, installed or {'version': '2.3.0'})
        updater.write_json(paths.admin, {'username': 'makeradmin'})
        paths.ca.parent.mkdir(parents=True, exist_ok=True)
        paths.ca.write_text('synthetic CA', encoding='utf-8')

    @staticmethod
    def _stage(paths, digest):
        stage = paths.update_root / ('github-linux-' + digest[:24])
        stage.mkdir(parents=True)
        operation = 'github-linux-' + digest[:24]
        updater.write_json(paths.approval, {
            'schema': 1, 'computer': 'vaerksted-05', 'version': '2.4.0',
            'package_sha256': digest, 'restart_authorized': False})
        updater.write_json(stage / 'journal.json', {
            'release': '2.4.0', 'operation': operation,
            'status': 'staged_awaiting_restart', 'computer': 'vaerksted-05',
            'package_sha256': digest})
        updater.write_json(stage / 'pins.json', {
            'payload_sha256': 'b' * 64, 'package_sha256': digest,
            'release': '2.4.0', 'manifest': {'version': '2.4.0', 'files': {}}})
        (stage / 'install.py').write_text("PACKAGE_SHA = '" + digest + "'\n", encoding='utf-8')

    @staticmethod
    def _state():
        return {'schema': 1, 'computer': 'vaerksted-05',
                'available': {'version': '2.4.0', 'package_sha256': 'a' * 64,
                              'release_id': 'r7', 'notes': 'Rettelser'},
                'job': None,
                'request': {'status': 'requested', 'version': '2.4.0',
                            'package_sha256': 'a' * 64, 'error': ''},
                'feed_state': 'ready'}


if __name__ == '__main__':
    unittest.main()
