"""Native admin shortcuts on a private X server; never switch a real console."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

FILES = Path(__file__).parents[1] / 'ansible/roles/kiosk/files'
sys.path.insert(0, str(FILES))
from admin_shortcuts import GlobalShortcut


class ShortcutTests(unittest.TestCase):
    def test_admin_entry_uses_existing_pause_and_busy_guards(self):
        import elevbjaelke
        toolbar = Mock()
        toolbar.pause_overlay.paused = True
        toolbar.admin_login_busy = False
        with patch.object(elevbjaelke.threading, 'Thread') as thread:
            elevbjaelke.Bjaelke.admin_login(toolbar)
            thread.assert_not_called()
            toolbar.pause_overlay.paused = False
            toolbar.admin_login_busy = True
            elevbjaelke.Bjaelke.admin_login(toolbar)
            thread.assert_not_called()

    def test_grabs_from_another_app_and_suppresses_repeat_with_locks(self):
        import tkinter as tk
        from Xlib import X, XK, display
        from Xlib.ext import xtest
        from test_pause_overlay_x11 import _Xvfb
        with _Xvfb() as server:
            old = os.environ.get('DISPLAY')
            os.environ['DISPLAY'] = server.display
            root = tk.Tk()
            connection = display.Display(server.display)
            try:
                calls = []
                shortcut = GlobalShortcut(root, 'a', lambda: calls.append('admin'))
                root.update()
                screen = connection.screen()
                other = screen.root.create_window(0, 0, 400, 300, 0, screen.root_depth,
                                                   event_mask=X.KeyPressMask)
                other.set_wm_class('workshop', 'Workshop')
                other.map()
                other.set_input_focus(X.RevertToParent, X.CurrentTime)
                connection.sync()
                def pump(seconds=.12):
                    end = time.monotonic() + seconds
                    while time.monotonic() < end:
                        root.update()
                        time.sleep(.005)
                codes = [connection.keysym_to_keycode(XK.string_to_keysym(k))
                         for k in ('Control_L', 'Alt_L', 'a')]
                for code in codes:
                    xtest.fake_input(connection, X.KeyPress, code)
                connection.sync()
                pump()
                self.assertEqual(calls, ['admin'])
                pump(.8)
                self.assertEqual(calls, ['admin'], 'Holding the key must not reopen login')
                # Traditional X autorepeat is release+press with identical time.
                for _ in range(4):
                    xtest.fake_input(connection, X.KeyRelease, codes[-1])
                    xtest.fake_input(connection, X.KeyPress, codes[-1])
                connection.sync()
                pump()
                self.assertEqual(calls, ['admin'])
                for code in reversed(codes):
                    xtest.fake_input(connection, X.KeyRelease, code)
                connection.sync()
                pump()
                for code in codes:
                    xtest.fake_input(connection, X.KeyPress, code)
                for code in reversed(codes):
                    xtest.fake_input(connection, X.KeyRelease, code)
                connection.sync()
                pump()
                self.assertEqual(calls, ['admin', 'admin'])
                shortcut.close()
                # Caps Lock must not disable the separate admin desktop key.
                caps = connection.keysym_to_keycode(XK.string_to_keysym('Caps_Lock'))
                xtest.fake_input(connection, X.KeyPress, caps)
                xtest.fake_input(connection, X.KeyRelease, caps)
                shortcut = GlobalShortcut(root, 'b', lambda: calls.append('return'))
                codes[-1] = connection.keysym_to_keycode(XK.string_to_keysym('b'))
                for code in codes:
                    xtest.fake_input(connection, X.KeyPress, code)
                for code in reversed(codes):
                    xtest.fake_input(connection, X.KeyRelease, code)
                connection.sync()
                pump()
                self.assertEqual(calls[-1], 'return')
                shortcut.close()
            finally:
                connection.close()
                root.destroy()
                if old is None:
                    os.environ.pop('DISPLAY', None)
                else:
                    os.environ['DISPLAY'] = old


if __name__ == '__main__':
    unittest.main()
