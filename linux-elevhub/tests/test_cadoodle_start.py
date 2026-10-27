"""Startup lifecycle with controlled processes; never launches the pupil app."""
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'ansible/roles/kiosk/files'))
import cadoodle_vaerksted as cad


class StartWorkerTests(unittest.TestCase):
    def test_existing_window_does_not_spawn_or_replace_log(self):
        job = cad.CadStart(Mock(return_value=['42']))
        with patch.object(cad.subprocess, 'Popen') as spawn:
            job.run()
        spawn.assert_not_called()
        self.assertEqual(job.events.get_nowait(), ('ready', ['42']))

    def test_waits_for_editor_window_not_merely_a_live_process(self):
        process = Mock()
        process.poll.return_value = None
        job = cad.CadStart(Mock(side_effect=[[], [], ['42']]), process)
        with patch.object(cad.time, 'sleep') as sleep, patch.object(cad.subprocess, 'Popen') as spawn:
            job.run()
        spawn.assert_not_called()
        sleep.assert_called_once_with(0.75)
        self.assertEqual(job.events.get_nowait(), ('ready', ['42']))

    def test_cold_start_spawns_once_and_errors_if_process_exits(self):
        process = Mock()
        process.poll.return_value = 1
        job = cad.CadStart(Mock(return_value=[]))
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cad.Path, 'home', return_value=Path(directory)), \
                patch.object(cad.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(job, 'running', return_value=False):
            job.run()
        self.assertEqual(spawn.call_args.args[0], [cad.LAUNCHER])
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(job.events.get_nowait()[0], 'error')

    def test_failed_launch_is_reported(self):
        job = cad.CadStart(Mock(return_value=[]))
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cad.Path, 'home', return_value=Path(directory)), \
                patch.object(cad.subprocess, 'Popen', side_effect=OSError('missing')):
            job.run()
        self.assertEqual(job.events.get_nowait()[0], 'error')

    def test_other_launch_in_progress_is_not_reported_as_crash(self):
        process = Mock()
        process.poll.side_effect = [None, 0]
        job = cad.CadStart(Mock(side_effect=[[], [], ['42']]), process)
        with patch.object(job, 'running', return_value=True), patch.object(cad.time, 'sleep'):
            job.run()
        self.assertEqual(job.events.get_nowait(), ('ready', ['42']))


class ToolbarStartTests(unittest.TestCase):
    def workshop(self):
        w = cad.Vaerksted.__new__(cad.Vaerksted)
        w.bjaelke = SimpleNamespace(rod=Mock())
        w.aaben, w.skaermpause = Mock(return_value=True), Mock(return_value=False)
        w.cad_vinduer = Mock(return_value=['42'])
        w.xdo = Mock(return_value=[])
        w.start_job, w.startvisning, w.proces, w.vindue = None, None, None, Mock()
        w.start_i_forgrund = True
        w.skjulte = set()
        w.entry_request = Mock()
        return w

    def ready(self, w):
        job = SimpleNamespace(events=queue.Queue(), process=Mock(), started=time.monotonic())
        job.events.put(('ready', ['42']))
        w.start_job, w.startvisning = job, Mock()
        return w.startvisning

    def test_repeated_click_does_not_launch_twice(self):
        w = self.workshop()
        with patch.object(cad, 'Startvisning') as panel, patch.object(cad.threading, 'Thread') as worker:
            w.start_cad()
            w.start_cad()
        self.assertEqual(panel.call_count, 1)
        self.assertEqual(panel.call_args.kwargs['school_name'], cad.DEFAULT_SCHOOL_NAME)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(worker.return_value.start.call_count, 1)
        panel.return_value.show.assert_called_once()

    def test_slow_probe_does_not_block_click_handler(self):
        w = self.workshop()
        entered, release = threading.Event(), threading.Event()
        def find():
            entered.set()
            release.wait(3)
            return ['42']
        w.cad_hovedvinduer = find
        try:
            with patch.object(cad, 'Startvisning'):
                w.start_cad()
            self.assertTrue(entered.wait(1))
            # Worker is held; the click handler has already returned and scheduled Tk.
            self.assertFalse(release.is_set())
            w.bjaelke.rod.after.assert_called_once()
        finally:
            release.set()

    def test_slow_start_keeps_feedback_and_never_starts_again(self):
        w = self.workshop()
        w.start_job = SimpleNamespace(events=queue.Queue(), started=time.monotonic() - 200)
        w.startvisning = Mock()
        with patch.object(cad.subprocess, 'Popen') as spawn:
            w.foelg_start()
        w.startvisning.waiting.assert_called_once()
        self.assertGreater(w.startvisning.waiting.call_args.args[0], 199)
        w.startvisning.close.assert_not_called()
        w.bjaelke.rod.after.assert_called_once()
        spawn.assert_not_called()

    def test_window_ready_closes_feedback_and_restores_editor(self):
        w = self.workshop()
        panel = self.ready(w)
        w.foelg_start()
        panel.close.assert_called_once()
        w.xdo.assert_called_with('windowmap', '42', 'windowactivate', '42')
        self.assertIsNone(w.start_job)
        w.entry_request.assert_called_once()

    def test_saved_project_open_does_not_race_import_with_start_dialog(self):
        w = self.workshop()
        self.ready(w)
        w.skip_entry = True
        w.foelg_start()
        w.entry_request.assert_not_called()
        self.assertFalse(w.skip_entry)

    def test_back_or_teacher_change_never_brings_late_window_forward(self):
        for reason in ('back', 'closed', 'pause'):
            with self.subTest(reason=reason):
                w = self.workshop()
                panel = self.ready(w)
                if reason == 'back':
                    w.skjul_start()
                elif reason == 'closed':
                    w.aaben.return_value = False
                else:
                    w.skaermpause.return_value = True
                w.foelg_start()
                panel.close.assert_called_once()
                w.xdo.assert_called_once_with('windowunmap', '42')
                self.assertIn('42', w.skjulte)
                w.entry_request.assert_not_called()

    def test_error_ends_wait_and_allows_explicit_retry(self):
        w = self.workshop()
        panel = self.ready(w)
        w.start_job.events.get_nowait()
        w.start_job.events.put(('error', 'Fejl'))
        w.foelg_start()
        panel.error.assert_called_once_with('Fejl')
        self.assertIsNone(w.start_job)
        w.xdo.assert_not_called()

    def test_only_native_versioned_main_window_counts_as_ready(self):
        w = self.workshop()
        w.cad_vinduer.return_value = ['1', '2', '3', '4', '5']
        w.xdo.side_effect = [['CaDoodle', 'Updater'], ['Loading'],
                             ['CaDoodle', 'v', '0.46.094'], ['Project', 'Manager'],
                             ['CaDoodle', 'Version', '0.46.094']]
        self.assertEqual(w.cad_hovedvinduer(), ['3', '5'])


class NativeBrandingContractTests(unittest.TestCase):
    def test_profile_school_name_is_safe_bounded_and_has_fallback(self):
        self.assertEqual(cad.native_school_name(None), cad.DEFAULT_SCHOOL_NAME)
        self.assertEqual(cad.native_school_name({'school_name': None}), cad.DEFAULT_SCHOOL_NAME)
        self.assertEqual(cad.native_school_name({'school_name': '\u202e\x00'}), cad.DEFAULT_SCHOOL_NAME)
        name = cad.native_school_name({'school_name': '  Søndervang\n\tSkole  '})
        self.assertEqual(name, 'Søndervang Skole')
        self.assertLessEqual(len(name), cad.MAX_NATIVE_SCHOOL_NAME_LENGTH)
        self.assertTrue(all(not cad.unicodedata.category(char).startswith('C') for char in name))
        self.assertNotRegex(name, r'[\r\n\t]')

    def test_fixed_credit_is_a_separate_constant(self):
        self.assertEqual(cad.CREDIT_TEXT, 'Kreditering: Klostermarksskolen.')
        self.assertNotEqual(cad.CREDIT_TEXT, cad.DEFAULT_SCHOOL_NAME)


class PrivateTkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != 'linux':
            raise unittest.SkipTest('Private Xvfb tests are Linux-only')
        from test_pause_overlay_x11 import _Xvfb
        cls.display = _Xvfb()
        cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    @unittest.skipUnless(sys.platform == 'linux', 'Private Xvfb test is Linux-only')
    def test_feedback_paints_and_animates_while_window_lookup_is_held(self):
        import tkinter as tk
        with self.subTest(display='private Xvfb'):
            root = tk.Tk()
            root.withdraw()
            w = ToolbarStartTests().workshop()
            w.bjaelke.rod = root
            entered, release = threading.Event(), threading.Event()
            def find():
                entered.set()
                release.wait(3)
                return ['42']
            w.cad_hovedvinduer = find
            try:
                before = time.monotonic()
                w.start_cad()
                self.assertLess(time.monotonic() - before, 1)
                self.assertTrue(entered.wait(1))
                root.update()
                panel = w.startvisning
                self.assertTrue(panel.window.winfo_ismapped())
                initial = panel.progress['value']
                deadline = time.monotonic() + 0.2
                while time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertNotEqual(initial, panel.progress['value'])
                self.assertFalse(release.is_set())
                # Release the fake window detection, then consume the actual queued result.
                release.set()
                deadline = time.monotonic() + 2
                while w.start_job is not None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertIsNone(w.start_job)
                self.assertIsNone(w.startvisning)
            finally:
                release.set()
                root.destroy()

    @unittest.skipUnless(sys.platform == 'linux', 'Private Xvfb test is Linux-only')
    def test_panel_reflows_and_keeps_back_and_retry_reachable(self):
        import tkinter as tk
        with self.subTest(display='private Xvfb'):
            root = tk.Tk()
            root.withdraw()
            panel = cad.Startvisning(root, Mock(), Mock())
            try:
                for size in ('560x410', '480x390'):
                    panel.window.geometry(size)
                    for state in ('initial', 'slow', 'error'):
                        if state == 'slow':
                            panel.waiting(100)
                        elif state == 'error':
                            panel.error('CaDoodle kunne ikke åbne. Prøv igen, eller spørg din lærer.')
                        root.update()
                        for node in (panel.heading, panel.message, panel.tip, panel.retry):
                            if node.winfo_ismapped():
                                self.assertGreater(node.winfo_height(), 10, (size, state))
                                bottom = node.winfo_rooty() + node.winfo_height()
                                self.assertLessEqual(bottom, panel.window.winfo_rooty() + panel.window.winfo_height())
                        self.assertLessEqual(panel.message.winfo_rooty() + panel.message.winfo_height(),
                                             panel.progress.winfo_rooty())
                        self.assertLessEqual(panel.tip.winfo_rooty() + panel.tip.winfo_height(),
                                             panel.retry.master.winfo_rooty())
                self.assertGreaterEqual(panel.retry.winfo_height(), 44)
            finally:
                panel.close()
                root.destroy()

    @staticmethod
    def _texts(widget):
        import tkinter as tk
        texts = []
        for child in widget.winfo_children():
            try:
                value = child.cget('text')
            except tk.TclError:
                value = ''
            if value:
                texts.append(value)
            texts.extend(PrivateTkTests._texts(child))
        return texts

    @unittest.skipUnless(sys.platform == 'linux', 'Private Xvfb test is Linux-only')
    def test_configured_school_and_fixed_credit_reach_native_titles_and_widgets(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        panel = cad.Startvisning(root, Mock(), Mock(), school_name='Søndervang Skole')
        workshop = None
        try:
            root.update_idletasks()
            self.assertEqual(panel.window.title(), 'Åbner 3D-værkstedet · Søndervang Skole')
            start_text = self._texts(panel.window)
            self.assertIn('SØNDERVANG SKOLE  /  3D-VÆRKSTED', start_text)
            self.assertIn(cad.CREDIT_TEXT, start_text)

            ramme = tk.Frame(root)
            bjaelke = SimpleNamespace(konf={'school_name': 'Søndervang Skole'}, rod=root,
                                      server_status='offline', navigation_busy=False)
            with patch.object(cad.Vaerksted, 'xdo', return_value=[]):
                workshop = cad.Vaerksted(bjaelke, ramme)
            workshop.paa_start = Mock(return_value=True)
            workshop.vis_genvej = Mock()
            workshop.start_cad = Mock()
            workshop.print = Mock()
            workshop.vis()
            root.update_idletasks()
            self.assertEqual(workshop.vindue.title(), '3D-værksted · Søndervang Skole')
            native_text = self._texts(workshop.vindue)
            self.assertIn('SØNDERVANG SKOLE  /  DESIGN OG FREMSTILLING', native_text)
            self.assertIn(cad.CREDIT_TEXT, native_text)
        finally:
            panel.close()
            if workshop is not None and workshop.vindue is not None:
                workshop.vindue.destroy()
            if 'ramme' in locals():
                ramme.destroy()
            root.destroy()


if __name__ == '__main__':
    unittest.main()
