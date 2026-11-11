"""Exercise the real WPF controller offscreen with inert installer callbacks.

These tests do not call ShowDialog, start installers, touch certificate stores,
or discover/change the host network. The PowerShell fixture creates hidden WPF
objects and drives their routed events and DispatcherTimer on an STA thread.
"""
import json
import shutil
import subprocess
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/setup_wizard_view_checks.ps1"


class SetupWizardViewTests(unittest.TestCase):
    def run_case(self, case):
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell and WPF are required")
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
             "-ExecutionPolicy", "Bypass", "-File", str(FIXTURE),
             "-Repository", str(ROOT), "-Case", case],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["case"], case)
        self.assertTrue(receipt["passed"])
        self.assertFalse(receipt["visibleWindows"])
        self.assertFalse(receipt["installationInvoked"])

    def test_view_loads_with_accessible_controls_and_scroll(self):
        self.run_case("load_view")

    def test_review_precedes_start_and_success_requires_worker_result(self):
        self.run_case("successful_flow")

    def test_step_scoped_validation_blocks_advance(self):
        self.run_case("step_scoped_validation")

    def test_network_candidate_uses_danish_dhcp_labels(self):
        self.run_case("danish_dhcp")

    def test_station_skips_network_step(self):
        self.run_case("station_skips_network")

    def test_station_review_identifies_school_server_and_model(self):
        self.run_case("station_summary")

    def test_station_back_preserves_connection_file(self):
        self.run_case("station_back")

    def test_address_changes_reset_it_confirmation(self):
        self.run_case("address_confirmation_reset")

    def test_nonzero_exit_is_not_reported_as_success(self):
        self.run_case("nonzero_success_refused")

    def test_polling_failure_keeps_running_close_guard(self):
        self.run_case("poll_failure_keeps_guard")

    def test_missing_network_candidates_explains_manual_help(self):
        self.run_case("no_network_candidates")

    def test_closing_during_installation_is_blocked(self):
        self.run_case("closing_while_running")

    def test_context_help_is_embedded_and_examples_are_not_defaults(self):
        self.run_case("embedded_context_help")

    def test_worker_guidance_and_technical_help_are_copyable_text(self):
        self.run_case("worker_guidance_and_technical_help")

    def test_start_failure_shows_concrete_help_inside_the_guide(self):
        self.run_case("start_failure_has_own_help")

    def test_log_and_checklist_copy_use_an_inert_callback(self):
        self.run_case("copy_log_and_checklist_are_inert")

    def test_old_callbacks_keep_next_steps_and_fallback_help(self):
        self.run_case("legacy_callbacks_keep_guidance_fallback")


if __name__ == "__main__":
    unittest.main()
