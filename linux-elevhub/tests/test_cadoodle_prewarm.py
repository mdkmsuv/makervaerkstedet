"""Teacher-triggered background start; no pupil app or display is controlled."""
import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / 'ansible/roles/kiosk/files'))
import cadoodle_vaerksted as cad

TOKEN = '0123456789abcdef0123456789abcdef'


class PrewarmTests(unittest.TestCase):
    def workshop(self):
        w = cad.Vaerksted.__new__(cad.Vaerksted)
        w.bjaelke = SimpleNamespace(rod=Mock(), server_status='online')
        w.aaben = Mock(return_value=True)
        w.skaermpause = Mock(return_value=False)
        w.cad_hovedvinduer = Mock(return_value=[])
        w.start_job = w.startvisning = w.proces = w.vindue = None
        w.start_i_forgrund = False
        w.prewarm_token = None
        w.prewarm_requested = w.prewarm_attempted = False
        w.skjulte = set()
        return w

    def test_teacher_opens_once_and_polling_does_not_spawn_again(self):
        w = self.workshop()
        with patch.object(cad, 'existing_prewarm', return_value=None), \
                patch.object(cad.threading, 'Thread') as thread, patch.object(cad, 'Startvisning') as panel:
            for _ in range(4):
                w.opdater({'apps': [{'id': '3dprint', 'aaben': True, 'klar': True}]})
                w.forstart_cad()
        self.assertEqual(thread.call_count, 1)
        self.assertTrue(w.start_job.background)
        self.assertFalse(w.start_i_forgrund)
        panel.assert_not_called()

    def test_no_background_launch_offline_closed_or_paused(self):
        for reason in ('offline', 'closed', 'paused', 'not_ready'):
            w = self.workshop()
            w.opdater({'apps': [{'id': '3dprint', 'aaben': reason != 'closed', 'klar': reason != 'not_ready'}]})
            if reason == 'offline': w.bjaelke.server_status = 'offline'
            if reason == 'paused': w.skaermpause.return_value = True
            with patch.object(cad.threading, 'Thread') as thread:
                w.forstart_cad()
            thread.assert_not_called()

    def test_background_ready_does_not_show_or_create_feedback(self):
        w = self.workshop()
        w.start_job = SimpleNamespace(events=queue.Queue(), process=Mock())
        w.start_job.events.put(('warm', TOKEN))
        w.start_cad = Mock()
        w.foelg_start()
        self.assertEqual(w.prewarm_token, TOKEN)
        w.start_cad.assert_not_called()
        self.assertIsNone(w.startvisning)

    def test_click_during_prewarm_waits_and_reuses_the_completed_start(self):
        w = self.workshop()
        job = w.start_job = SimpleNamespace(events=queue.Queue(), process=Mock())
        with patch.object(cad, 'Startvisning') as panel, patch.object(cad.threading, 'Thread') as thread:
            w.start_cad()
            thread.assert_not_called()
            self.assertEqual(panel.call_count, 1)
        self.assertIs(w.start_job, job)
        job.events.put(('warm', TOKEN))
        with patch.object(w, 'start_cad') as show:
            w.foelg_start()
        show.assert_called_once()
        self.assertEqual(w.prewarm_token, TOKEN)

    def test_back_or_policy_change_cancels_a_pending_show_marker(self):
        w = self.workshop()
        with tempfile.TemporaryDirectory() as d, patch.object(cad, 'prewarm_root', return_value=Path(d)):
            job = w.start_job = cad.CadStart(Mock(), token=TOKEN)
            job.show_warm()
            self.assertTrue((Path(d) / (TOKEN + '.show')).exists())
            w.skjul_start()
            job.show_warm()
            self.assertFalse((Path(d) / (TOKEN + '.show')).exists())

    def test_an_existing_foreground_editor_is_not_hidden_by_teacher_open(self):
        job = cad.CadStart(Mock(return_value=['42']), background=True)
        with patch.object(cad.subprocess, 'Popen') as launch, patch.object(cad, 'ready_prewarm', return_value=False):
            job.run()
        launch.assert_not_called()
        self.assertEqual(job.events.get_nowait(), ('existing', ['42']))

    def test_background_launch_passes_unique_nonce_and_waits_for_ready(self):
        process = Mock()
        process.poll.return_value = None
        job = cad.CadStart(Mock(return_value=[]), background=True)
        with tempfile.TemporaryDirectory() as d, patch.object(cad.Path, 'home', return_value=Path(d)), \
                patch.object(cad, 'ready_prewarm', side_effect=[False, True]), \
                patch.object(cad, 'existing_prewarm', return_value=None), \
                patch.object(cad.subprocess, 'Popen', return_value=process) as launch:
            job.run()
        self.assertEqual(launch.call_args.kwargs['env']['ELEVHUB_CADOODLE_PREWARM'], job.token)
        self.assertEqual(job.events.get_nowait(), ('warm', job.token))

    def test_ready_background_instance_is_reused_without_another_process(self):
        process = Mock()
        process.poll.return_value = None
        job = cad.CadStart(Mock(return_value=['42']), process, token=TOKEN)
        with patch.object(cad, 'ready_prewarm', return_value=True), \
                patch.object(cad, 'request_prewarm') as show, patch.object(cad.subprocess, 'Popen') as launch:
            job.run()
        launch.assert_not_called()
        self.assertTrue(any(call.args == (TOKEN, True) for call in show.call_args_list))
        self.assertEqual(job.events.get_nowait(), ('ready', ['42']))

    def test_prewarm_does_not_finish_on_an_early_native_window(self):
        process = Mock()
        process.poll.return_value = None
        windows = Mock(side_effect=[[], ['early']])
        job = cad.CadStart(windows, background=True)
        with tempfile.TemporaryDirectory() as d, patch.object(cad.Path, 'home', return_value=Path(d)), \
                patch.object(cad, 'ready_prewarm', side_effect=[False, False, True]), \
                patch.object(cad, 'existing_prewarm', return_value=None), \
                patch.object(cad.time, 'sleep'), patch.object(cad.subprocess, 'Popen', return_value=process):
            job.run()
        self.assertEqual(windows.call_count, 1)
        self.assertEqual(job.events.get_nowait(), ('warm', job.token))

    def test_click_after_back_reenables_the_same_pending_job(self):
        w = self.workshop()
        w.start_job = cad.CadStart(Mock(), token=TOKEN)
        w.start_job.cancel_show()
        with patch.object(cad, 'Startvisning'), patch.object(cad.threading, 'Thread') as thread:
            w.start_cad()
        self.assertTrue(w.start_job.allow_show)
        thread.assert_not_called()


if __name__ == '__main__':
    unittest.main()
