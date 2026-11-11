"""Focused tests for the kiosk startup screen helpers.

The HTTP test uses a loopback-only server.  The optional X11 test starts its
own private Xvfb and never touches the user's display.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import pathlib
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


_ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(_ROOT / 'ansible' / 'roles' / 'kiosk' / 'files'))
_FILE = _ROOT / "ansible" / "roles" / "kiosk" / "files" / "startup_screen.py"
_SPEC = importlib.util.spec_from_file_location("startup_screen", _FILE)
startup_screen = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = startup_screen
_SPEC.loader.exec_module(startup_screen)

try:
    # Reuse the repository's private-display fixture.  Importing it does not
    # start an X server; each test owns the context explicitly.
    sys.path.insert(0, str(_ROOT / "tests"))
    from test_pause_overlay_x11 import _Xvfb
except (ImportError, OSError):  # pragma: no cover - Linux/WSL only
    _Xvfb = None


class _Health(http.server.BaseHTTPRequestHandler):
    status = 503

    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
        elif self.path == "/ok":
            self.send_response(200)
        else:
            self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


class StartupScreenTests(unittest.TestCase):
    def test_update_warning_stays_visible_despite_ready_windows_and_timeout(self):
        for status in ('applying','recovery_required'):
            with self.subTest(status=status):
                screen = object.__new__(startup_screen.StartupScreen)
                screen._closed = False
                screen._failure_shown = False
                screen.root, screen.heading, screen.message = Mock(), Mock(), Mock()
                screen._probe = SimpleNamespace(dead=False, has_ready_windows=lambda: (True,True))
                screen._job = SimpleNamespace(done=SimpleNamespace(is_set=lambda: True), result=True)
                screen._start_job, screen._show_failure, screen.close = Mock(), Mock(), Mock()
                screen.timeout = SimpleNamespace(expired=lambda: True)
                value = {'status':status,'phase':'installing' if status=='applying' else status}
                with patch.object(startup_screen,'read_status',return_value=value):
                    screen._tick()
                screen.close.assert_not_called()
                screen._show_failure.assert_not_called()
                self.assertIn('OPDATER',screen.message.configure.call_args.kwargs['text'])
                screen.root.after.assert_called_once()

    def test_only_literal_loopback_http_urls_are_allowed(self):
        self.assertTrue(startup_screen.validate_local_url("http://127.0.0.1:8791/_elevhub/health"))
        self.assertTrue(startup_screen.validate_local_url("http://[::1]:8791/health"))
        for value in (
            "https://127.0.0.1:8791/health",
            "http://localhost:8791/health",
            "http://192.168.1.10:8791/health",
            "http://127.0.0.1:8791/health#x",
            "http://user:pass@127.0.0.1:8791/health",
        ):
            self.assertFalse(startup_screen.validate_local_url(value), value)

    def test_http_ready_requires_200_and_does_not_follow_redirect(self):
        with socketserver.TCPServer(("127.0.0.1", 0), _Health) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            self.assertTrue(startup_screen.http_ready([f"http://127.0.0.1:{port}/ok"]))
            self.assertFalse(startup_screen.http_ready([f"http://127.0.0.1:{port}/redirect"]))
            server.shutdown()
            thread.join(timeout=2)

    def test_timeout_state_uses_monotonic_values_and_can_retry(self):
        state = startup_screen.TimeoutState.start(now=10, max_seconds=45)
        self.assertFalse(state.expired(now=54.99))
        self.assertTrue(state.expired(now=55))
        state.reset(now=100)
        self.assertFalse(state.expired(now=100.1))

    def test_readiness_config_distinguishes_valid_empty_from_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "startup.json"
            path.write_text(json.dumps({"urls": []}), encoding="utf-8")
            self.assertEqual(startup_screen.load_readiness_urls(path), [])
            path.write_text(json.dumps({"urls": ["http://example.invalid/health"]}), encoding="utf-8")
            self.assertIsNone(startup_screen.load_readiness_urls(path))
            path.write_text(json.dumps({"urls": None}), encoding="utf-8")
            self.assertIsNone(startup_screen.load_readiness_urls(path))


@unittest.skipUnless(_Xvfb is not None and shutil.which("Xvfb") and sys.platform.startswith("linux"),
                     "private Xvfb is required")
class PrivateX11Tests(unittest.TestCase):
    """Exercise the real Tk/X11 process on a display owned by this test."""

    @classmethod
    def setUpClass(cls):
        cls._xvfb = _Xvfb()
        cls._xvfb.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._xvfb.__exit__(None, None, None)

    def _windows(self, title=startup_screen.READY_TITLE, bar_mapped=True):
        import tkinter as tk
        from Xlib import display

        root = tk.Tk(className="elevbjaelke")
        root.geometry("260x40+0+0")
        if not bar_mapped:
            root.withdraw()
        main = tk.Toplevel(root)
        main.title(title)
        main.geometry("520x320+180+120")
        root.update_idletasks()
        root.update()

        # Set the EWMH UTF8 title explicitly.  This mirrors Chromium's
        # _NET_WM_NAME even on Tk builds that only update WM_NAME.
        xdisplay = display.Display(os.environ["DISPLAY"])
        wm_class = xdisplay.intern_atom("WM_CLASS")
        string = xdisplay.intern_atom("STRING")
        xdisplay.create_resource_object("window", root.winfo_id()).change_property(
            wm_class, string, 8,
            (startup_screen.BAR_INSTANCE + "\0" + startup_screen.BAR_CLASS + "\0").encode("ascii"))
        xdisplay.create_resource_object("window", main.winfo_id()).change_property(
            wm_class, string, 8,
            (startup_screen.MAIN_INSTANCE + "\0" + startup_screen.MAIN_CLASS + "\0").encode("ascii"))
        atom = xdisplay.intern_atom("_NET_WM_NAME")
        utf8 = xdisplay.intern_atom("UTF8_STRING")
        xwindow = xdisplay.create_resource_object("window", main.winfo_id())
        xwindow.change_property(atom, utf8, 8, title.encode("utf-8"))
        xdisplay.sync()
        return root, main, xdisplay

    @staticmethod
    def _write_config(directory, port, status=503):
        _Health.status = status
        path = pathlib.Path(directory) / "startup.json"
        path.write_text(json.dumps({"urls": [f"http://127.0.0.1:{port}/health"]}), encoding="utf-8")
        return path

    @staticmethod
    def _start_screen(config, max_seconds=2):
        marker = config.parent / "mapped.marker"
        process = subprocess.Popen(
            [sys.executable, str(_FILE), "--config", str(config),
             "--mapped-file", str(marker), "--max-seconds", str(max_seconds)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=os.environ.copy(),
        )
        return process, marker

    @staticmethod
    def _pump(root, process, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and process.poll() is None:
            root.update()
            time.sleep(0.02)
        return process.poll()

    def test_fake_windows_hold_until_title_bar_and_all_health_checks_are_ready(self):
        with socketserver.TCPServer(("127.0.0.1", 0), _Health) as server:
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            with tempfile.TemporaryDirectory(prefix="startup-screen-test-") as directory:
                config = self._write_config(directory, server.server_address[1], status=503)
                root, main, xdisplay = self._windows(title="Værkstedet starter", bar_mapped=False)
                process, marker = self._start_screen(config)
                try:
                    self.assertIsNone(self._pump(root, process, 0.4))
                    marker_deadline = time.monotonic() + 3
                    while not marker.exists() and process.poll() is None and time.monotonic() < marker_deadline:
                        root.update()
                        time.sleep(0.02)
                    self.assertTrue(marker.exists(), f"startup screen exited {process.poll()}")
                    _Health.status = 200
                    self.assertIsNone(self._pump(root, process, 0.5))
                    main.title(startup_screen.READY_TITLE)
                    atom = xdisplay.intern_atom("_NET_WM_NAME")
                    utf8 = xdisplay.intern_atom("UTF8_STRING")
                    xdisplay.create_resource_object("window", main.winfo_id()).change_property(
                        atom, utf8, 8, startup_screen.READY_TITLE.encode("utf-8"))
                    xdisplay.sync()
                    self.assertIsNone(self._pump(root, process, 0.5))
                    root.deiconify()
                    root.update_idletasks()
                    root.update()
                    self.assertEqual(self._pump(root, process, 3), 0)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=2)
                    if process.stdout:
                        process.stdout.close()
                    if process.stderr:
                        process.stderr.close()
                    root.destroy()
                    xdisplay.close()
            server.shutdown()
            server_thread.join(timeout=2)

    def test_timeout_buttons_retry_and_close_release_x(self):
        import tkinter as tk

        screen = startup_screen.StartupScreen([], max_seconds=0.01)
        try:
            screen._probe.has_ready_windows = lambda: (False, False)
            screen._tick()
            time.sleep(0.02)
            screen._tick()
            self.assertTrue(screen._failure_shown)
            self.assertEqual({child.cget("text") for child in screen.actions.winfo_children()},
                             {"Prøv igen", "Åbn værkstedet"})
            screen.retry()
            self.assertFalse(screen._failure_shown)
            self.assertEqual(screen.message.cget("text"), "")
            self.assertEqual(screen.root.cget("bg"), "#000000")
            screen.close()
            try:
                exists = screen.root.winfo_exists()
            except tk.TclError:
                exists = 0
            self.assertEqual(exists, 0)
            self.assertIsNone(screen._probe._display)
        finally:
            screen.close()

    def test_overlay_is_raised_against_a_new_mapped_window(self):
        import tkinter as tk

        screen = startup_screen.StartupScreen([], max_seconds=45)
        other = tk.Toplevel(screen.root)
        other.geometry("100x80+10+10")
        screen.root.update_idletasks()
        screen.root.update()
        screen._probe.has_ready_windows = lambda: (False, False)
        screen._tick()
        from Xlib import display
        xdisplay = display.Display(os.environ["DISPLAY"])
        try:
            children = xdisplay.screen().root.query_tree().children
            ids = [child.id for child in children]
            details = [(child.id, child.get_wm_class()) for child in children]
            overlay_id = next(child.id for child in children
                              if child.get_wm_class() == ("elevhub-startup-screen",
                                                          "Elevhub-startup-screen"))
            other_id = next(child.id for child in children
                            if child.get_wm_class() == ("!toplevel", "Toplevel"))
            self.assertGreater(ids.index(overlay_id), ids.index(other_id))
        finally:
            xdisplay.close()
        other.destroy()
        screen.close()


if __name__ == "__main__":
    unittest.main()
