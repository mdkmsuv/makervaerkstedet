"""Finish/navigation contracts without touching the real display or pupil files."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'ansible/roles/kiosk/files'))
import cadoodle_vaerksted as cad
import elevbjaelke as bar
import workshop_launcher as launcher
import power_controls as power


class SessionClientTests(unittest.TestCase):
    def test_waits_for_positive_completion(self):
        request_id = 'a' * 32
        replies = [{'id': request_id, 'state': state} for state in ('pending', 'pending', 'completed')]
        with patch.object(launcher, 'session_request', side_effect=replies) as request, patch.object(launcher.time, 'sleep'):
            self.assertEqual(launcher.finish_web_sessions()['state'], 'completed')
        self.assertEqual(request.call_count, 3)

    def test_continue_does_not_issue_another_request(self):
        with patch.object(launcher, 'session_request', return_value={'id': 'a' * 32, 'state': 'cancelled'}) as request:
            self.assertEqual(launcher.finish_web_sessions()['state'], 'cancelled')
        request.assert_called_once()

    def test_timeout_or_bad_ack_cancels_remote_request(self):
        request_id = 'a' * 32
        for response in ({'id': 'b' * 32, 'state': 'completed'}, OSError('disconnected')):
            with self.subTest(response=response), patch.object(launcher, 'session_request', side_effect=[
                    {'id': request_id, 'state': 'pending'}, response, {}]) as request, patch.object(launcher.time, 'sleep'):
                self.assertEqual(launcher.finish_web_sessions()['state'], 'error')
                self.assertEqual(request.call_args.args, ('POST', 'cancel', {'id': request_id}))
        with patch.object(launcher, 'session_request', side_effect=[{'id': request_id, 'state': 'pending'}, {}]) as request:
            self.assertEqual(launcher.finish_web_sessions(timeout=0)['state'], 'error')
            self.assertEqual(request.call_args.args, ('POST', 'cancel', {'id': request_id}))

    def test_keyboard_requests_are_coalesced_and_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(launcher.Path, 'home', return_value=Path(directory)):
            self.assertTrue(launcher.request_navigation())
            self.assertTrue(launcher.request_navigation())
            self.assertTrue(launcher.consume_navigation_request())
            self.assertFalse(launcher.consume_navigation_request())


class NativeFinishTests(unittest.TestCase):
    def test_native_print_workshop_waits_for_the_same_finish_guard(self):
        workshop = cad.Vaerksted.__new__(cad.Vaerksted)
        workshop.aaben = Mock(return_value=True)
        workshop.skaermpause = Mock(return_value=False)
        workshop.bjaelke = SimpleNamespace(konf={'hub': 'http://school:8773'}, afslut_foer=Mock())
        workshop.app = {'port': 8774}
        with patch.object(cad.subprocess, 'Popen') as launch:
            workshop.print()
            launch.assert_not_called()
            self.assertEqual(workshop.bjaelke.afslut_foer.call_args.kwargs, {'reason': 'switch'})
            workshop.bjaelke.afslut_foer.call_args.args[0]()
            launch.assert_called_once()

    def test_native_library_request_is_scoped_and_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cad.Path, 'home', return_value=Path(directory)):
            marker = Path(directory) / 'Projekter/.elevhub/cadoodle-projects.ready'
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({'created': cad.time.time()}))
            workshop = cad.Vaerksted.__new__(cad.Vaerksted)
            workshop.skaermpause = Mock(return_value=False)
            workshop.aaben = Mock(return_value=True)
            workshop.bjaelke = SimpleNamespace(aabn_native_projekter=Mock())
            with patch.object(cad.subprocess, 'Popen') as launch:
                workshop.aabn_projekthylde()
                workshop.aabn_projekthylde()
            launch.assert_not_called()
            workshop.bjaelke.aabn_native_projekter.assert_called_once()
            self.assertFalse(marker.exists())

    def test_stale_or_closed_library_request_does_not_open_a_window(self):
        for opened, age in ((False, 0), (True, 121)):
            with tempfile.TemporaryDirectory() as directory, patch.object(cad.Path, 'home', return_value=Path(directory)):
                marker = Path(directory) / 'Projekter/.elevhub/cadoodle-projects.ready'
                marker.parent.mkdir(parents=True)
                marker.write_text(json.dumps({'created': cad.time.time() - age}))
                workshop = cad.Vaerksted.__new__(cad.Vaerksted)
                workshop.skaermpause = Mock(return_value=False)
                workshop.aaben = Mock(return_value=opened)
                with patch.object(cad.subprocess, 'Popen') as launch:
                    workshop.aabn_projekthylde()
                launch.assert_not_called()

    def test_entry_request_is_bounded_and_explicit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cad.Path, 'home', return_value=Path(directory)):
            cad.show_native_entry()
            request = next((Path(directory) / 'Projekter/.elevhub/finish-requests').glob('*.ready'))
            result = json.loads(request.read_text())
            self.assertEqual(result['id'], request.stem)
            self.assertEqual(result['action'], 'show')

    def test_native_ack_controls_outcome_and_only_ipc_files_are_removed(self):
        for state in ('completed', 'cancelled', 'error'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                saved = root / 'saved-project.zip'
                saved.write_bytes(b'keep')
                def reply(_seconds):
                    request = next(root.glob('*.ready'))
                    data = json.loads(request.read_text())
                    self.assertEqual(data['action'], 'finish')
                    request.with_suffix('.status.json').write_text(json.dumps({'id': data['id'], 'state': state}))
                with patch.object(cad.time, 'sleep', side_effect=reply):
                    self.assertEqual(cad.finish_native_session(root)['state'], state)
                self.assertEqual(list(root.iterdir()), [saved])

    def test_native_no_ack_is_an_error_not_permission_to_leave(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(cad.finish_native_session(directory, timeout=0)['state'], 'error')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_hidden_native_editor_is_restored_before_finish(self):
        workshop = cad.Vaerksted.__new__(cad.Vaerksted)
        workshop.cad_vinduer = Mock(return_value=['hidden-main', 'hidden-dialog'])
        workshop.xdo = Mock()
        with patch.object(cad, 'finish_native_session', return_value={'state': 'cancelled'}):
            self.assertEqual(workshop.afslut_session()['state'], 'cancelled')
        self.assertEqual(workshop.xdo.call_args_list[0].args, ('windowmap', 'hidden-main', 'windowactivate', 'hidden-main'))
        self.assertFalse(any('windowunmap' in call.args for call in workshop.xdo.call_args_list))

    def test_no_native_process_does_not_create_a_finish_request(self):
        workshop = cad.Vaerksted.__new__(cad.Vaerksted)
        workshop.cad_vinduer = Mock(return_value=[])
        with patch.object(cad.CadStart, 'running', return_value=False), patch.object(cad, 'finish_native_session') as finish:
            self.assertEqual(workshop.afslut_session()['state'], 'completed')
        finish.assert_not_called()


class ToolbarFinishTests(unittest.TestCase):
    def toolbar(self):
        toolbar = bar.Bjaelke.__new__(bar.Bjaelke)
        toolbar.pause_overlay = SimpleNamespace(paused=False)
        toolbar.rod = SimpleNamespace(after=lambda _delay, callback: callback())
        toolbar.start, toolbar.besked = Mock(), Mock()
        toolbar.cadoodle = Mock()
        toolbar.cadoodle.afslut_session.return_value = {'state': 'completed'}
        toolbar.paa_start = Mock(return_value=False)
        return toolbar

    def run_finish(self, toolbar, action, web_state='completed'):
        with patch.object(bar.threading, 'Thread') as thread, patch.object(bar, 'xdo', return_value=['browser']), \
                patch.object(launcher, 'finish_web_sessions', return_value={'state': web_state}) as web:
            thread.side_effect = lambda *, target, **_kwargs: SimpleNamespace(start=target)
            toolbar.afslut_foer(action)
        return web

    def test_native_continue_never_hides_or_navigates_or_resets_web(self):
        toolbar, action = self.toolbar(), Mock()
        toolbar.cadoodle.afslut_session.return_value = {'state': 'cancelled'}
        web = self.run_finish(toolbar, action)
        web.assert_not_called()
        action.assert_not_called()
        toolbar.cadoodle.skjul.assert_not_called()
        self.assertFalse(toolbar.navigation_busy)

    def test_web_continue_or_error_never_hides_or_navigates(self):
        for state in ('cancelled', 'error'):
            with self.subTest(state=state):
                toolbar, action = self.toolbar(), Mock()
                self.run_finish(toolbar, action, state)
                action.assert_not_called()
                toolbar.cadoodle.skjul.assert_not_called()

    def test_every_editor_completed_allows_one_hide_and_navigation(self):
        toolbar, action = self.toolbar(), Mock()
        self.run_finish(toolbar, action)
        toolbar.cadoodle.skjul.assert_called_once()
        action.assert_called_once()

    def test_home_page_does_not_need_project_service_but_still_finishes_native(self):
        toolbar, action = self.toolbar(), Mock()
        toolbar.paa_start.return_value = True
        web = self.run_finish(toolbar, action)
        web.assert_not_called()
        toolbar.cadoodle.afslut_session.assert_called_once()
        action.assert_called_once()

    def test_repeated_click_and_screen_pause_do_not_start_another_finish(self):
        for busy, paused in ((True, False), (False, True)):
            toolbar, action, completed = self.toolbar(), Mock(), Mock()
            toolbar.navigation_busy, toolbar.pause_overlay.paused = busy, paused
            self.assertFalse(toolbar.afslut_foer(action, completed=completed))
            toolbar.cadoodle.afslut_session.assert_not_called()
            action.assert_not_called()
            completed.assert_called_once_with({'state': 'cancelled'})

    def test_native_library_reuses_kiosk_container_even_when_internet_is_closed(self):
        import threading
        toolbar = self.toolbar()
        toolbar.internet_aaben = False
        toolbar.workshop_launcher = SimpleNamespace(state_lock=threading.Lock(), navigation_target=None)
        toolbar._til_start = Mock()
        toolbar.afslut_foer = Mock()
        toolbar.aabn_native_projekter()
        self.assertEqual(toolbar.afslut_foer.call_args.kwargs, {'reason': 'switch'})
        toolbar._til_start.assert_not_called()
        with patch.object(bar.threading, 'Thread', side_effect=lambda *, target, **_kwargs: SimpleNamespace(start=target)):
            toolbar.afslut_foer.call_args.args[0]()
        toolbar._til_start.assert_called_once()
        self.assertEqual(toolbar.workshop_launcher.navigation_target['page'], 'cadoodle-projects')


class PowerFinishTests(unittest.TestCase):
    def control(self):
        control = power.PowerControls.__new__(power.PowerControls)
        control.pending = False
        control.dialog = Mock()
        control.paused = lambda: False
        control.cancel_button, control.confirm_button = Mock(), Mock()
        control.buttons = [Mock(), Mock()]
        control._dispatch = Mock()
        return control

    def test_shutdown_waits_for_guard_and_continue_restores_buttons(self):
        for state in ('cancelled', 'error'):
            control = self.control()
            control.before_power = Mock()
            control.confirm('poweroff')
            self.assertTrue(control.pending)
            control._dispatch.assert_not_called()
            control.dialog.grab_release.assert_called_once()
            control.before_power.call_args.kwargs['completed']({'state': state})
            self.assertFalse(control.pending)
            self.assertIsNone(control.dialog)
            control._dispatch.assert_not_called()
            for button in control.buttons:
                button.configure.assert_called_with(state='normal')

    def test_shutdown_only_dispatches_after_guard_accepts(self):
        control = self.control()
        control.before_power = Mock()
        control.confirm('reboot')
        control.confirm('reboot')
        control.before_power.assert_called_once()
        control.before_power.call_args.args[0]()
        control.before_power.call_args.kwargs['completed']({'state': 'completed'})
        control._dispatch.assert_called_once_with('reboot')


if __name__ == '__main__':
    unittest.main()
