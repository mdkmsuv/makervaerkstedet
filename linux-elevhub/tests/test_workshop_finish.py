"""Finish/navigation contracts without touching the real display or pupil files."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'ansible/roles/kiosk/files'))
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
