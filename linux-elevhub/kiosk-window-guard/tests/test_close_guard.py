#!/usr/bin/env python3
"""Focused Xvfb probe for the Matchbox per-WM_CLASS close guard."""
from __future__ import annotations
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BIN = Path(os.environ.get('MATCHBOX_BIN', ROOT / 'bin/matchbox-window-manager'))
THEME = os.environ.get('MATCHBOX_THEME')
KBD = os.environ.get('MATCHBOX_KBD')
CLIENT = [sys.executable, str(HERE / 'x_client.py')]
INSTANCE = 'usr_local_share_elevhub_start.html'
CLASS = 'Chromium'


def wait_until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class CloseGuardProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for command in ('Xvfb', 'xdotool', 'xprop'):
            if shutil.which(command) is None:
                raise unittest.SkipTest(f'{command} is required for the Xvfb probe')
        if not BIN.is_file():
            raise unittest.SkipTest(f'MATCHBOX_BIN not found: {BIN}')
        sys.path.insert(0, str(HERE.parents[1] / 'tests'))
        from test_pause_overlay_x11 import _Xvfb
        cls.xserver = _Xvfb()
        cls.xserver.__enter__()
        cls.display = cls.xserver.display
        env = os.environ.copy()
        env['DISPLAY'] = cls.display
        cls.env = env
        wm_args = [str(BIN), '-use_titlebar', 'yes']
        if THEME:
            wm_args += ['-theme', THEME]
        if KBD:
            wm_args += ['-kbdconfig', KBD]
        env['MB_BLOCK_CLOSE_INSTANCE'] = INSTANCE
        env['MB_BLOCK_CLOSE_CLASS'] = CLASS
        cls.wm = subprocess.Popen(wm_args, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.35)
        if cls.wm.poll() is not None:
            detail = cls.wm.stderr.read()
            cls.xserver.__exit__(None, None, None)
            raise AssertionError(f'Matchbox exited: {detail}')

    @classmethod
    def tearDownClass(cls):
        for process in (getattr(cls, 'wm', None),):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if process:
                process.stdout.close()
                process.stderr.close()
        cls.xserver.__exit__(None, None, None)

    def client(self, instance=INSTANCE, class_name=CLASS, transient_for=None):
        args = CLIENT + ['--instance', instance, '--class-name', class_name]
        if transient_for is not None:
            args += ['--transient-for', str(transient_for)]
        process = subprocess.Popen(args, env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        line = process.stdout.readline().strip()
        if not line.isdigit():
            try:
                detail = process.communicate(timeout=2)[1]
            except subprocess.TimeoutExpired:
                process.kill()
                detail = process.communicate()[1]
            self.fail(f'probe client did not return a window id: {detail}')
        from Xlib import X, display
        connection = display.Display(self.display)
        window = connection.create_resource_object('window', int(line))
        atom = connection.intern_atom('_NET_WM_ALLOWED_ACTIONS')
        try:
            self.assertTrue(wait_until(lambda: window.get_full_property(atom, X.AnyPropertyType) is not None),
                            'Window manager did not register probe client')
        finally:
            connection.close()
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.stderr.close)
        return process, int(line)

    def close(self, window, keyboard=False):
        subprocess.run(['xdotool', 'windowactivate', str(window)], env=self.env,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if keyboard:
            command = ['xdotool', 'key', '--clearmodifiers', 'alt+F4']
            subprocess.run(command, env=self.env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        else:
            # xdotool windowclose destroys a client window directly. Send the
            # actual EWMH request to the WM instead, just as a desktop does.
            from Xlib import X, display, protocol
            connection = display.Display(self.display)
            event = protocol.event.ClientMessage(window=window,
                client_type=connection.intern_atom('_NET_CLOSE_WINDOW'), data=(32, [0, 2, 0, 0, 0]))
            connection.screen().root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            connection.sync()
            connection.close()

    def test_protected_app_requests_finish_for_ewmh_close_and_alt_f4(self):
        process, window = self.client()
        from Xlib import X, display
        connection = display.Display(self.display)
        root = connection.screen().root
        finish = connection.intern_atom('_ELEVHUB_FINISH_REQUEST')
        try:
            allowed = subprocess.check_output(['xprop', '-id', str(window), '_NET_WM_ALLOWED_ACTIONS'],
                                              env=self.env, text=True)
            self.assertIn('_NET_WM_ACTION_CLOSE', allowed)
            for keyboard in (False, True):
                root.delete_property(finish)
                connection.sync()
                self.close(window, keyboard=keyboard)
                self.assertTrue(wait_until(lambda: root.get_full_property(finish, X.AnyPropertyType) is not None))
                self.assertEqual(list(root.get_full_property(finish, X.AnyPropertyType).value), [window])
                self.assertIsNone(process.poll())
        finally:
            connection.close()
            process.terminate()
            process.wait(timeout=2)

    def test_other_app_closes_normally(self):
        process, window = self.client('ordinary', 'Chromium')
        try:
            allowed = subprocess.check_output(['xprop', '-id', str(window), '_NET_WM_ALLOWED_ACTIONS'],
                                              env=self.env, text=True)
            self.assertIn('_NET_WM_ACTION_CLOSE', allowed)
            self.close(window, keyboard=True)
            self.assertTrue(wait_until(lambda: process.poll() is not None))
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)

    def test_same_wm_class_transient_dialog_closes_normally(self):
        parent, parent_window = self.client()
        dialog, dialog_window = self.client(transient_for=parent_window)
        try:
            self.close(dialog_window)
            self.assertTrue(wait_until(lambda: dialog.poll() is not None))
            self.assertEqual(dialog.returncode, 0)
            self.assertIsNone(parent.poll())
        finally:
            for process in (dialog, parent):
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()
