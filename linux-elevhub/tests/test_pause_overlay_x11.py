"""Integration tests for the real Tk/X11 pause overlay.

The test suite deliberately owns its X server.  It never connects to the
developer's display (in particular, never to ``:0``), and it leaves both
DISPLAY and XAUTHORITY unchanged after the test class finishes.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import pathlib
import secrets
import select
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


if sys.platform.startswith("linux"):
    _FILES = pathlib.Path(__file__).parents[1] / "ansible" / "roles" / "kiosk" / "files"
    sys.path.insert(0, str(_FILES))
    try:
        from pause_overlay import PauseOverlay
        from pause_safety import PauseSafety
    except ImportError:  # pragma: no cover - gives a useful skip on partial checkouts
        PauseOverlay = None
        PauseSafety = None
else:  # pragma: no cover - this suite is intentionally Linux/X11-only
    PauseOverlay = None
    PauseSafety = None


_SCREEN_WIDTH = 1280
_SCREEN_HEIGHT = 720


_PROBE_PROGRAM = r"""
import tkinter as tk

root = tk.Tk()
root.title('pause-overlay-input-probe')
root.geometry('320x180+40+40')
root.resizable(False, False)
root.configure(background='white')

def record(event):
    value = getattr(event, 'keysym', '') or getattr(event, 'num', '')
    # Tkinter exposes event.type as the numeric X event code on some Python
    # versions and as EventType on others.
    event_name = getattr(event.type, 'name', '') or str(event.type)
    if event_name in ('2', 'KeyPress'):
        event_name = 'KeyPress'
    elif event_name in ('4', 'ButtonPress'):
        event_name = 'ButtonPress'
    if event_name == 'ButtonPress':
        print('ButtonPress', flush=True)
    else:
        print(f'{event_name}:{value}', flush=True)

root.bind('<KeyPress>', record)
root.bind('<ButtonPress>', record)
root.update_idletasks()
print(f'READY {root.winfo_id()}', flush=True)
root.focus_force()
root.mainloop()
"""


_WATCHDOG_PROGRAM = r"""
import sys
import time
import tkinter as tk
from pause_overlay import PauseOverlay
from pause_safety import PauseSafety

ledger, key, duration = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
hold = len(sys.argv) > 4 and sys.argv[4] == 'hold'
root = tk.Tk()
root.geometry('120x80+900+500')
safety = PauseSafety(ledger, heartbeat_timeout=0.6, grace=0.2)
overlay = PauseOverlay(root, safety=safety)
overlay.update_pause({
    'paused': True, 'version': key, 'until': 2_000_000_000 + key,
    'remaining': duration, 'message': 'pause',
})
root.update()
print(f'READY {int(overlay.paused)} {safety.process.pid if safety.process else 0}', flush=True)
if hold:
    # Keep the X11 grab and heartbeat alive while deliberately bypassing the
    # overlay's own deadline timer. This isolates the watchdog deadline path.
    if overlay._timer is not None:
        root.after_cancel(overlay._timer)
        overlay._timer = None
    def heartbeat():
        if safety.heartbeat():
            root.after(50, heartbeat)
    root.after(50, heartbeat)
root.mainloop()
"""


class _Xvfb:
    """Start a private Xvfb and scope the process environment to it."""

    def __init__(self):
        self._temporary = None
        self._process = None
        self._old_display = None
        self._old_xauthority = None
        self.display = None
        self.xauthority = None

    def __enter__(self):
        xvfb = shutil.which("Xvfb")
        if not xvfb:
            raise unittest.SkipTest("Xvfb is required for the Tk/X11 integration test")

        try:
            import tkinter  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"Tkinter is required for the integration test: {exc}") from exc

        self._temporary = tempfile.TemporaryDirectory(prefix="pause-overlay-x11-")
        self.xauthority = os.path.join(self._temporary.name, "Xauthority")
        pathlib.Path(self.xauthority).touch()
        xauth = shutil.which("xauth")
        cookie = secrets.token_hex(16)
        if xauth:
            # xauth accepts a display number before the server exists.  This
            # lets us retain access control while trying a few private slots.
            for display_number in range(90, 201):
                display_name = f":{display_number}"
                result = subprocess.run(
                    [xauth, "-f", self.xauthority, "add", display_name,
                     ".", cookie],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if result.returncode:
                    continue
                process = subprocess.Popen(
                    [xvfb, display_name, "-screen", "0",
                     f"{_SCREEN_WIDTH}x{_SCREEN_HEIGHT}x24", "-nolisten", "tcp",
                     "-auth", self.xauthority],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                time.sleep(0.08)
                if process.poll() is None:
                    self.display = display_name
                    self._process = process
                    break
                process.stderr.close()
        else:
            # An isolated server with access control disabled is still safer
            # than falling back to the user's display.  XAUTHORITY remains a
            # private path for all child processes.
            for display_number in range(90, 201):
                display_name = f":{display_number}"
                process = subprocess.Popen(
                    [xvfb, display_name, "-screen", "0",
                     f"{_SCREEN_WIDTH}x{_SCREEN_HEIGHT}x24", "-nolisten", "tcp", "-ac"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                time.sleep(0.08)
                if process.poll() is None:
                    self.display = display_name
                    self._process = process
                    break
                process.stderr.close()

        if self._process is None:
            self._temporary.cleanup()
            self._temporary = None
            raise unittest.SkipTest("could not allocate an unused private Xvfb display")

        self._old_display = os.environ.get("DISPLAY")
        self._old_xauthority = os.environ.get("XAUTHORITY")
        os.environ["DISPLAY"] = self.display
        os.environ["XAUTHORITY"] = self.xauthority
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        if self._old_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = self._old_display
        if self._old_xauthority is None:
            os.environ.pop("XAUTHORITY", None)
        else:
            os.environ["XAUTHORITY"] = self._old_xauthority

        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            if self._process.stderr:
                self._process.stderr.close()
        if self._temporary is not None:
            self._temporary.cleanup()


class _XTest:
    """Small ctypes wrapper for genuine server-side X11 input events."""

    def __init__(self):
        x11_name = ctypes.util.find_library("X11")
        xtst_name = ctypes.util.find_library("Xtst")
        if not x11_name or not xtst_name:
            raise unittest.SkipTest("libX11 and libXtst are required for X11 input testing")
        self.x11 = ctypes.CDLL(x11_name)
        self.xtst = ctypes.CDLL(xtst_name)
        display_type = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = display_type
        self.x11.XCloseDisplay.argtypes = [display_type]
        self.x11.XCloseDisplay.restype = ctypes.c_int
        self.x11.XFlush.argtypes = [display_type]
        self.x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.x11.XStringToKeysym.restype = ctypes.c_ulong
        self.x11.XKeysymToKeycode.argtypes = [display_type, ctypes.c_ulong]
        self.x11.XKeysymToKeycode.restype = ctypes.c_uint
        self.xtst.XTestFakeMotionEvent.argtypes = [display_type, ctypes.c_int,
                                                   ctypes.c_int, ctypes.c_int,
                                                   ctypes.c_ulong]
        self.xtst.XTestFakeMotionEvent.restype = ctypes.c_int
        self.xtst.XTestFakeButtonEvent.argtypes = [display_type, ctypes.c_uint,
                                                   ctypes.c_int, ctypes.c_ulong]
        self.xtst.XTestFakeButtonEvent.restype = ctypes.c_int
        self.xtst.XTestFakeKeyEvent.argtypes = [display_type, ctypes.c_uint,
                                                ctypes.c_int, ctypes.c_ulong]
        self.xtst.XTestFakeKeyEvent.restype = ctypes.c_int
        self._display = self.x11.XOpenDisplay(os.environ["DISPLAY"].encode())
        if not self._display:
            raise unittest.SkipTest("could not open the private Xvfb display with XTest")

    def close(self):
        if self._display:
            self.x11.XCloseDisplay(self._display)
            self._display = None

    def _keycode(self, keysym):
        symbol = self.x11.XStringToKeysym(keysym.encode())
        code = self.x11.XKeysymToKeycode(self._display, symbol)
        if not code:
            raise AssertionError(f"X11 has no keycode for {keysym!r}")
        return code

    def key_down(self, keysym):
        self.xtst.XTestFakeKeyEvent(self._display, self._keycode(keysym), 1, 0)

    def key_up(self, keysym):
        self.xtst.XTestFakeKeyEvent(self._display, self._keycode(keysym), 0, 0)

    def key(self, keysym):
        self.key_down(keysym)
        self.key_up(keysym)
        self.flush()

    def click(self, x=100, y=100):
        self.xtst.XTestFakeMotionEvent(self._display, 0, x, y, 0)
        self.xtst.XTestFakeButtonEvent(self._display, 1, 1, 0)
        self.xtst.XTestFakeButtonEvent(self._display, 1, 0, 0)
        self.flush()

    def flush(self):
        self.x11.XFlush(self._display)


class _InputProbe:
    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-c", _PROBE_PROGRAM],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )
        self.lines = []
        self._pending = []
        self._buffer = ''
        self.wait_for("READY", timeout=3)

    def _read(self, timeout):
        readable, _, _ = select.select([self.process.stdout], [], [], timeout)
        if not readable:
            return False
        data = os.read(self.process.stdout.fileno(), 4096)
        if not data:
            return False
        self._buffer += data.decode()
        lines = self._buffer.split('\n')
        self._buffer = lines.pop()
        self._pending.extend(line.strip() for line in lines if line.strip())
        return True

    def wait_for(self, text, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pending and not self._read(max(0, deadline - time.monotonic())):
                break
            line = self._pending.pop(0) if self._pending else ''
            if line:
                self.lines.append(line)
                if text in line:
                    return line
        raise AssertionError(f"input probe did not emit {text!r}; got {self.lines!r}")

    def drain(self):
        while self._read(0):
            pass
        self.lines.extend(self._pending)
        self._pending.clear()

    def clear(self):
        self.lines.clear()

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()


class PauseOverlayX11Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if PauseOverlay is None:
            raise unittest.SkipTest("pause_overlay.py or a Linux Tk runtime is unavailable")
        cls._xvfb = _Xvfb()
        cls._xvfb.__enter__()
        try:
            cls._xtest = _XTest()
        except unittest.SkipTest:
            cls._xvfb.__exit__(None, None, None)
            raise

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_xtest", None):
            cls._xtest.close()
        if getattr(cls, "_xvfb", None):
            cls._xvfb.__exit__(None, None, None)

    def setUp(self):
        import tkinter as tk

        self.root = tk.Tk()
        self.root.title("pause-overlay-host")
        self.root.geometry("120x80+900+500")
        self.root.update_idletasks()
        self.probe = _InputProbe()
        self._safety_temp = tempfile.TemporaryDirectory(prefix='pause-overlay-safety-')
        self.overlay = PauseOverlay(
            self.root,
            safety=PauseSafety(path=pathlib.Path(self._safety_temp.name) / 'ledger.json'),
        )
        self._pump(0.15)
        self.probe.drain()
        self.probe.clear()

    def tearDown(self):
        try:
            self.overlay.update_pause({"paused": False, "version": 999,
                                       "until": None, "remaining": 0, "message": ""})
            self._pump(0.05)
        except Exception:
            pass
        if getattr(self, "probe", None):
            self.probe.stop()
        if getattr(self, "root", None):
            try:
                self.root.destroy()
            except Exception:
                pass
        if getattr(self, '_safety_temp', None):
            self._safety_temp.cleanup()

    def _pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)

    def _pause(self, version=1, duration=30):
        self.overlay.update_pause({
            "paused": True,
            "version": version,
            "until": time.time() + duration,
            "remaining": duration,
            "message": "Gem dit arbejde først",
        })
        self._pump(0.15)
        self.assertIsNotNone(self.overlay.window)
        self.assertTrue(self.overlay.window.winfo_viewable())

    def _watchdog_toolbar(self, ledger, key=1, duration=30, hold=False):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(_FILES) + os.pathsep + env.get('PYTHONPATH', '')
        process = subprocess.Popen(
            [sys.executable, '-u', '-c', _WATCHDOG_PROGRAM,
             str(ledger), str(key), str(duration)] + (['hold'] if hold else []),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        self.addCleanup(self._cleanup_toolbar, process)
        deadline = time.monotonic() + 3
        lines = []
        while time.monotonic() < deadline:
            if process.poll() is not None:
                error = process.stderr.read() if process.stderr else ''
                self.fail(f'watchdog toolbar exited ({process.returncode}): {error!r}')
            readable, _, _ = select.select([process.stdout], [], [],
                                           max(0, deadline - time.monotonic()))
            if readable:
                line = process.stdout.readline().strip()
                if not line and process.poll() is not None:
                    error = process.stderr.read() if process.stderr else ''
                    self.fail(f'watchdog toolbar exited ({process.returncode}): {error!r}')
                lines.append(line)
                if line.startswith('READY '):
                    self.addCleanup(self._cleanup_process, process)
                    return process, line, lines
        process.kill()
        process.wait(timeout=2)
        self.fail(f'watchdog toolbar did not start: {lines!r}')

    @staticmethod
    def _cleanup_toolbar(process):
        if process.poll() is None:
            process.kill()  # Also works if the test deliberately SIGSTOPped it.
        process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()

    @staticmethod
    def _cleanup_process(process):
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)

    @staticmethod
    def _wait_dead(process, timeout=3):
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            raise AssertionError('watchdog did not terminate the frozen toolbar')

    def test_watchdog_kills_sigstopped_toolbar_and_releases_input(self):
        with tempfile.TemporaryDirectory(prefix='pause-watchdog-') as temporary:
            process, ready, _ = self._watchdog_toolbar(pathlib.Path(temporary) / 'ledger.json')
            self.assertTrue(ready.startswith('READY 1 '))
            process.send_signal(signal.SIGSTOP)
            self._wait_dead(process, timeout=2)
            self._xtest.click()
            self._xtest.key('d')
            self.probe.wait_for('ButtonPress')
            self.probe.wait_for('KeyPress:d')

    def test_watchdog_deadline_kills_even_with_heartbeats(self):
        with tempfile.TemporaryDirectory(prefix='pause-watchdog-') as temporary:
            process, ready, _ = self._watchdog_toolbar(
                pathlib.Path(temporary) / 'ledger.json', duration=.35, hold=True)
            self.assertTrue(ready.startswith('READY 1 '))
            self._wait_dead(process, timeout=2)
            self._xtest.click()
            self._xtest.key('e')
            self.probe.wait_for('ButtonPress')
            self.probe.wait_for('KeyPress:e')

    def test_dead_watchdog_fails_open_on_next_overlay_callback(self):
        with tempfile.TemporaryDirectory(prefix='pause-watchdog-') as temporary:
            process, ready, _ = self._watchdog_toolbar(
                pathlib.Path(temporary) / 'ledger.json', duration=30)
            self.assertTrue(ready.startswith('READY 1 '))
            guard_pid = int(ready.split()[2])
            os.kill(guard_pid, signal.SIGKILL)
            # The toolbar remains alive; its next 250 ms callback detects the
            # dead guard, releases the X11 grab, and marks this version failed.
            time.sleep(.4)
            self.assertIsNone(process.poll())
            self._xtest.click()
            self._xtest.key('g')
            self.probe.wait_for('ButtonPress')
            self.probe.wait_for('KeyPress:g')
            process.terminate()
            process.wait(timeout=2)

    def test_blocked_version_cannot_relock_after_toolbar_restart(self):
        with tempfile.TemporaryDirectory(prefix='pause-watchdog-') as temporary:
            ledger = pathlib.Path(temporary) / 'ledger.json'
            process, ready, _ = self._watchdog_toolbar(ledger, key=9)
            self.assertTrue(ready.startswith('READY 1 '))
            process.send_signal(signal.SIGSTOP)
            self._wait_dead(process, timeout=2)
            replacement, ready, _ = self._watchdog_toolbar(ledger, key=9)
            self.assertTrue(ready.startswith('READY 0 '))
            self._xtest.click()
            self._xtest.key('f')
            self.probe.wait_for('ButtonPress')
            self.probe.wait_for('KeyPress:f')
            replacement.terminate()
            replacement.wait(timeout=2)

    def test_watchdog_start_failure_fails_open(self):
        import tkinter as tk

        class FailedSafety:
            def arm(self, _key, _deadline):
                return None
            def heartbeat(self):
                return False
            def block(self):
                pass
            def stop(self):
                pass

        overlay = PauseOverlay(self.root, safety=FailedSafety())
        overlay.update_pause({'paused': True, 'version': 50,
                              'until': time.time() + 30, 'remaining': 30})
        self._pump(.1)
        self.assertFalse(overlay.paused)
        self.assertIsNone(overlay.window)

    def test_pause_is_full_screen_global_and_blocks_escape_shortcuts(self):
        # Establish that the second process really receives input before the
        # overlay is shown.
        self._xtest.key("a")
        self.probe.wait_for("KeyPress:a")
        self.probe.drain()
        self.probe.clear()

        self._pause()
        window = self.overlay.window
        self.assertEqual((window.winfo_width(), window.winfo_height()),
                         (_SCREEN_WIDTH, _SCREEN_HEIGHT))
        self.assertEqual((window.winfo_rootx(), window.winfo_rooty()), (0, 0))
        self.assertEqual(window.grab_status(), "global")
        self.assertEqual(self.overlay.acknowledgement(), {"version": 1, "shown": True})

        self._xtest.click()
        self._xtest.key("Escape")
        self._xtest.key_down("Alt_L")
        self._xtest.key("Tab")
        self._xtest.key_up("Alt_L")
        self._xtest.key_down("Alt_L")
        self._xtest.key("F4")
        self._xtest.key_up("Alt_L")
        self._pump(0.25)
        self.probe.drain()

        self.assertTrue(self.overlay.paused)
        self.assertIsNotNone(self.overlay.window)
        self.assertEqual(self.overlay.acknowledgement(), {"version": 1, "shown": True})
        self.assertEqual(self.probe.lines, [], "paused input reached the other Tk process")

    def test_explicit_release_restores_input_without_global_binding_leak(self):
        self._pause()
        self.overlay.update_pause({"paused": False, "version": 2,
                                   "until": None, "remaining": 0, "message": ""})
        self._pump(0.2)
        self.assertFalse(self.overlay.paused)
        self.assertIsNone(self.overlay.window)
        self.assertEqual(self.overlay.acknowledgement(), {"version": 2, "shown": False})

        self._xtest.click()
        self._xtest.key("b")
        self.probe.wait_for("ButtonPress")
        self.probe.wait_for("KeyPress:b")

    def test_deadline_release_restores_input(self):
        self._pause(duration=0.45)
        self._pump(1.4)
        self.assertFalse(self.overlay.paused)
        self.assertIsNone(self.overlay.window)
        self.assertEqual(self.overlay.acknowledgement(), {"version": 1, "shown": False})

        self._xtest.click()
        self._xtest.key("c")
        self.probe.wait_for("ButtonPress")
        self.probe.wait_for("KeyPress:c")


if __name__ == "__main__":
    unittest.main()
