"""Actual key grabs/replay on a private Xvfb; never connects to the pupil's display."""
import importlib.util
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'ansible/roles/kiosk/files/browser_close_guard.py'
spec = importlib.util.spec_from_file_location('browser_close_guard', SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PolicyTests(unittest.TestCase):
    def test_only_container_close_and_chromium_global_quit_are_blocked(self):
        protected = (guard.INSTANCE, guard.CLASS)
        self.assertTrue(guard.blocked(protected, False, 'w', False))
        self.assertFalse(guard.blocked(protected, True, 'w', False))
        self.assertFalse(guard.blocked(('chromium', 'Chromium'), False, 'w', False))
        self.assertTrue(guard.blocked(('chromium', 'Chromium'), False, 'q', True))
        self.assertFalse(guard.blocked(('cadoodle', 'CaDoodle'), False, 'q', True))
        self.assertFalse(guard.blocked((guard.INSTANCE, 'WrongClass'), False, 'w', False))


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('Xvfb'), 'Linux/Xvfb required')
class PrivateX11Tests(unittest.TestCase):
    def test_real_grabs_replay_dialogs_and_one_time_toolbar_reset(self):
        from Xlib import X, XK, display
        from Xlib.ext import xtest
        from test_pause_overlay_x11 import _Xvfb
        server = _Xvfb()
        server.__enter__()
        process = connection = None
        navigation_home = tempfile.TemporaryDirectory()
        try:
            address = server.display
            connection = display.Display(address)
            screen = connection.screen()
            env = {**os.environ, 'DISPLAY': address, 'HOME': navigation_home.name}
            process = subprocess.Popen([sys.executable, str(SOURCE)], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertTrue(select.select([process.stdout], [], [], 5)[0], 'Close guard did not start')
            self.assertTrue(process.stdout.readline().startswith('READY:'))
            navigation_request = Path(navigation_home.name) / '.local/state/elevhub/navigation/finish.request'
            screen.root.change_property(connection.intern_atom(guard.FINISH_ATOM),
                                        connection.intern_atom('CARDINAL'), 32, [123])
            connection.flush()
            deadline = time.monotonic() + 2
            while not navigation_request.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(navigation_request.is_file(), 'WM close must request the same toolbar finish flow')
            navigation_request.unlink()
            parent = screen.root.create_window(0, 0, 400, 300, 0, screen.root_depth,
                                               event_mask=X.KeyPressMask | X.KeyReleaseMask)
            parent.map()
            cases = [
                ('container', (guard.INSTANCE, guard.CLASS), False, 'w', False, None, False),
                ('container-shift', (guard.INSTANCE, guard.CLASS), False, 'w', True, None, False),
                ('container-ctrl-f4', (guard.INSTANCE, guard.CLASS), False, 'F4', False, None, False),
                ('other-browser', ('chromium', 'Chromium'), False, 'w', False, None, True),
                ('dialog', (guard.INSTANCE, guard.CLASS), True, 'w', False, None, True),
                ('other-app', ('cadoodle', 'CaDoodle'), False, 'w', False, None, True),
                ('global-quit', ('chromium', 'Chromium'), False, 'q', True, None, False),
                ('other-app-quit', ('cadoodle', 'CaDoodle'), False, 'q', True, None, True),
                ('toolbar-reset', (guard.INSTANCE, guard.CLASS), False, 'w', False, int(time.time()) + 5, True),
                ('expired-reset', (guard.INSTANCE, guard.CLASS), False, 'w', False, int(time.time()) - 5, False),
            ]
            for name, pair, transient, key, shifted, permission, expected in cases:
                with self.subTest(name=name):
                    window = screen.root.create_window(20, 30, 350, 250, 0, screen.root_depth,
                                                        event_mask=X.KeyPressMask | X.KeyReleaseMask)
                    window.set_wm_class(*pair)
                    if transient:
                        window.set_wm_transient_for(parent)
                    if permission:
                        window.change_property(connection.intern_atom(guard.RESET_ATOM),
                                               connection.intern_atom('CARDINAL'), 32, [permission])
                    window.map()
                    window.set_input_focus(X.RevertToParent, X.CurrentTime)
                    connection.sync()
                    while connection.pending_events():
                        connection.next_event()
                    codes = [connection.keysym_to_keycode(XK.string_to_keysym('Control_L'))]
                    if shifted:
                        codes.append(connection.keysym_to_keycode(XK.string_to_keysym('Shift_L')))
                    wanted = connection.keysym_to_keycode(XK.string_to_keysym(key))
                    codes.append(wanted)
                    for code in codes:
                        xtest.fake_input(connection, X.KeyPress, code)
                    for code in reversed(codes):
                        xtest.fake_input(connection, X.KeyRelease, code)
                    connection.sync()
                    received = False
                    end = time.monotonic() + 0.25
                    while time.monotonic() < end:
                        while connection.pending_events():
                            event = connection.next_event()
                            if event.type == X.KeyPress and event.detail == wanted:
                                received = True
                        time.sleep(0.01)
                    self.assertEqual(received, expected)
                    if not expected:
                        self.assertTrue(navigation_request.is_file(), name)
                        navigation_request.unlink()
                    self.assertIsNone(process.poll())
                    if permission and expected:
                        self.assertIsNone(window.get_full_property(connection.intern_atom(guard.RESET_ATOM), X.AnyPropertyType))
                    window.destroy()
                    connection.sync()
        finally:
            if process:
                process.terminate()
                process.wait(timeout=3)
                process.stdout.close()
                process.stderr.close()
            if connection:
                connection.close()
            server.__exit__(None, None, None)
            navigation_home.cleanup()


if __name__ == '__main__':
    unittest.main()
