"""Safety and Tk integration tests for the local power controls."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import threading
import time
import unittest
from unittest import mock


_ROOT = pathlib.Path(__file__).parents[1]
_FILES = _ROOT / "ansible" / "roles" / "kiosk" / "files"
sys.path.insert(0, str(_FILES))
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import power_controls  # noqa: E402


def _load_power_helper():
    path = _FILES / "elevhub-power.py"
    spec = importlib.util.spec_from_file_location("elevhub_power_test_module", path)
    if spec is None or spec.loader is None:  # pragma: no cover - broken checkout
        raise ImportError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


elevhub_power = _load_power_helper()


class PowerHelperTest(unittest.TestCase):
    def test_power_command_allows_only_exact_systemctl_commands(self):
        self.assertEqual(
            elevhub_power.power_command(["poweroff"]),
            ["/usr/bin/systemctl", "--no-block", "poweroff"],
        )
        self.assertEqual(
            elevhub_power.power_command(["reboot"]),
            ["/usr/bin/systemctl", "--no-block", "reboot"],
        )

        for args in ([], ["halt"], ["poweroff", "--force"], ["reboot", "now"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                elevhub_power.power_command(args)

    def test_invalid_helper_args_are_rejected_before_privileged_checks_or_dispatch(self):
        run = mock.Mock()
        with mock.patch.object(elevhub_power.os, "geteuid", return_value=0), mock.patch.object(
            elevhub_power, "subprocess"
        ) as subprocess:
            for args in ([], ["halt"], ["poweroff", "--force"]):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    elevhub_power.main(args)
            subprocess.run.assert_not_called()
        run.assert_not_called()

    def test_helper_dispatches_the_fixed_command_with_restricted_environment(self):
        run = mock.Mock()
        with mock.patch.object(elevhub_power, "read_status", return_value=None), mock.patch.object(
            elevhub_power.os, "geteuid", return_value=0), mock.patch.object(
            elevhub_power.subprocess, "run", run
        ):
            elevhub_power.main(["reboot"])
        run.assert_called_once_with(
            ["/usr/bin/systemctl", "--no-block", "reboot"],
            check=True,
            timeout=12,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )

    def test_request_power_validates_before_calling_runner_and_uses_fixed_sudo_command(self):
        run = mock.Mock()
        with self.assertRaises(ValueError):
            power_controls.request_power("halt", run=run)
        run.assert_not_called()

        power_controls.request_power("poweroff", run=run)
        run.assert_called_once_with(
            ["sudo", "-n", power_controls.HELPER, "poweroff"],
            check=True,
            capture_output=True,
            timeout=15,
        )

    def test_root_power_helper_blocks_shutdown_during_update(self):
        with mock.patch.object(elevhub_power, "read_status", return_value={
            "schema": 1, "status": "applying", "phase": "installing", "boot_id": "boot",
        }), mock.patch.object(elevhub_power.os, "geteuid", return_value=0), mock.patch.object(
            elevhub_power.subprocess, "run"
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "OPDATERER"):
                elevhub_power.main(["poweroff"])
            run.assert_not_called()

    def test_root_power_helper_allows_reboot_when_restart_is_needed(self):
        with mock.patch.object(elevhub_power, "read_status", return_value={
            "schema": 1, "status": "staged_awaiting_restart", "phase": "complete", "boot_id": "boot",
        }), mock.patch.object(elevhub_power.os, "geteuid", return_value=0), mock.patch.object(
            elevhub_power.subprocess, "run"
        ) as run:
            elevhub_power.main(["reboot"])
            run.assert_called_once()


@unittest.skipUnless(sys.platform.startswith("linux"), "Tk integration is Linux-only")
class PowerControlsTkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter  # noqa: F401
            from test_pause_overlay_x11 import _Xvfb
        except ImportError as exc:
            raise unittest.SkipTest(f"Tk/Xvfb integration unavailable: {exc}") from exc
        cls._xvfb = _Xvfb()
        cls._xvfb.__enter__()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_xvfb", None):
            cls._xvfb.__exit__(None, None, None)

    def setUp(self):
        import tkinter as tk

        self._status_patch = mock.patch.object(power_controls, "read_status", return_value=None)
        self._status_patch.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.parent = tk.Frame(self.root)
        self.parent.pack()
        self.controls = power_controls.PowerControls(self.root, self.parent)
        self.root.update()

    def tearDown(self):
        try:
            if self.controls.dialog is not None:
                self.controls.cancel()
            self.root.destroy()
        except Exception:
            pass
        self._status_patch.stop()

    def _pump_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Tk state did not reach the expected result")

    def test_opening_and_cancelling_dialog_never_dispatches_power(self):
        with mock.patch.object(power_controls, "request_power") as request:
            self.controls.ask("poweroff")
            self.root.update()
            self.assertIsNotNone(self.controls.dialog)
            self.controls.cancel()
            self.root.update()
            self.assertIsNone(self.controls.dialog)
            request.assert_not_called()

    def test_only_confirmation_dispatches_the_selected_action_once(self):
        request = mock.Mock()
        with mock.patch.object(power_controls, "request_power", request), mock.patch.object(
            power_controls.subprocess, "run"
        ) as run:
            self.controls.ask("reboot")
            self.root.update()
            self.controls.confirm_button.invoke()
            self.controls.confirm_button.invoke()
            self._pump_until(lambda: request.called)
            request.assert_called_once_with("reboot")
            run.assert_not_called()

    def test_repeated_button_clicks_open_one_dialog_and_one_dispatch(self):
        release = threading.Event()

        def blocked_request(action):
            release.wait(1)

        with mock.patch.object(power_controls, "request_power", side_effect=blocked_request) as request:
            self.controls.buttons[0].invoke()
            self.controls.buttons[0].invoke()
            self.root.update()
            first_dialog = self.controls.dialog
            self.assertIsNotNone(first_dialog)
            self.assertIs(first_dialog, self.controls.dialog)
            self.controls.confirm_button.invoke()
            self.controls.confirm_button.invoke()
            self._pump_until(lambda: request.called)
            self.assertEqual(request.call_count, 1)
            release.set()

    def test_dispatch_failure_keeps_dialog_and_programs_available(self):
        marker = pathlib.Path(self._testMethodName + ".marker")
        marker.write_text("saved project", encoding="utf-8")
        try:
            with mock.patch.object(power_controls, "request_power", side_effect=OSError("unavailable")):
                self.controls.ask("poweroff")
                self.root.update()
                self.controls.confirm_button.invoke()
                self._pump_until(lambda: not self.controls.pending)
                self.assertIsNotNone(self.controls.dialog)
                self.assertTrue(self.root.winfo_exists())
                self.assertEqual(marker.read_text(encoding="utf-8"), "saved project")
                self.assertEqual(str(self.controls.cancel_button["state"]), "normal")
                self.assertEqual(str(self.controls.confirm_button["state"]), "normal")
                self.assertIn("stadig åbne", self.controls.status["text"])
        finally:
            marker.unlink(missing_ok=True)

    def test_active_update_warns_before_workshop_guard_runs(self):
        guard = mock.Mock()
        active = {"schema": 1, "status": "applying", "phase": "installing", "boot_id": "boot"}
        with mock.patch.object(power_controls, "read_status", return_value=active):
            controls = power_controls.PowerControls(self.root, self.parent, before_power=guard)
            try:
                controls.ask("reboot")
                self.root.update()
                self.assertIsNotNone(controls.dialog)
                self.assertIn("OPDATERER", " ".join(str(child.cget("text")) for child in controls.dialog.winfo_children()[0].winfo_children()))
                guard.assert_not_called()
            finally:
                controls.cancel()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
