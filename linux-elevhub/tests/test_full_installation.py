"""Bounded tests for the bundled physical-machine installation flow."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import tarfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
INSTALLATION = ROOT / "installation"
sys.path.insert(0, str(INSTALLATION))
import build  # noqa: E402
import firstboot  # noqa: E402


# Synthetic SSH wire data made solely from fixed test bytes; never a school key.
import base64
import struct
_fixture_type = b'ssh-ed25519'
_fixture_wire = struct.pack('>I', len(_fixture_type)) + _fixture_type + struct.pack('>I', 32) + bytes(range(32))
KEY = 'ssh-ed25519 ' + base64.b64encode(_fixture_wire).decode('ascii') + ' synthetic-fixture'
CA = "-----BEGIN CERTIFICATE-----\npublic\n-----END CERTIFICATE-----\n"
TOKEN = "A" * 43


class FullInstallationTests(unittest.TestCase):
    def test_local_design_bundle_contains_laser_import_closure(self):
        manifest = ROOT / "ansible/roles/kiosk/tasks/main.yml"
        project_root = ROOT.parent
        text = manifest.read_text(encoding="utf-8")
        entries = text.split("    kiosk_lokal_design_filer:\n", 1)[1].split("\n  block:", 1)[0]
        bundled = [line.removeprefix("      - ").strip()
                   for line in entries.splitlines() if line.startswith("      - ")]
        required = (
            "app/backup_guard.py", "app/generation_runtime.py", "app/project_origins.py",
            "app/hardware_workflows.py", "app/laser_inbox_lifecycle.py",
            "app/laser_workflows.py", "app/printer_config.py", "app/profiles.py",
        )
        for relative in required:
            with self.subTest(file=relative):
                self.assertIn(relative, bundled)

        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "bundle"
            package = staging / "app"
            package.mkdir(parents=True)
            for relative in bundled:
                if not relative.startswith("app/") or not relative.endswith(".py"):
                    continue
                source = project_root / relative
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "-m", "app.laser_local", "--help"],
                cwd=staging, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage", result.stdout.lower())

    def test_firstboot_uses_the_bundled_makecode_controller(self):
        bundle = Path('/opt/elevhub-install')
        variables = firstboot.make_variables(self.profile(), KEY,
            {'files': {'assets/CaDoodle-ApplicationInstall.zip': '0' * 64}}, bundle=bundle)
        self.assertEqual(variables['kiosk_lokale_app_kilder']['microbit'],
                         str(bundle / 'linux-elevhub/server/microbit-controller'))

    def test_software_bundle_contains_controller_and_shared_session_runtime(self):
        # Exercise the actual bundle/manifest path with small app fixtures.
        # Unrelated large artwork and third-party sources are omitted here.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ('deb', 'bootstrap', 'kloster'):
                (base / name).write_bytes(name.encode())
            for name in ('scratch', 'datatraener'):
                (base / name).mkdir()
                (base / name / 'index.html').write_text('<html></html>')
            args = SimpleNamespace(output=base / 'output', version='test',
                **{name: base / name for name in ('deb', 'bootstrap', 'kloster', 'scratch', 'datatraener')})
            original_add = build.add_path
            included = {ROOT.parent / 'CREDITS.md', ROOT / 'projekter', ROOT / 'server/microbit-controller',
                        ROOT / 'kiosk-window-guard/bin/matchbox-window-manager'}
            def bounded_add(source, destination):
                source = Path(source)
                if source in included or source.is_relative_to(base) or any(source.is_relative_to(path) for path in included):
                    return original_add(source, destination)
            with mock.patch.object(build, 'add_path', side_effect=bounded_add), \
                    mock.patch.object(build, 'DEB_HASH', build.sha(args.deb)), \
                    mock.patch.object(build, 'CADoodle17_JAR_HASH', build.sha(args.kloster)):
                build.bundle(args)
            manifest = json.loads((args.output / 'manifest.json').read_text())
            expected = ('CREDITS.md', 'linux-elevhub/server/microbit-controller/index.html',
                        'linux-elevhub/server/microbit-controller/app.js',
                        'linux-elevhub/projekter/workshop_sessions.py',
                        'linux-elevhub/projekter/web/project-session.js',
                        'linux-elevhub/projekter/web/projekt-bridge.js',
                        'linux-elevhub/kiosk-window-guard/bin/matchbox-window-manager')
            with tarfile.open(args.output / 'payload.tar.gz') as archive:
                for name in expected:
                    data = archive.extractfile(name).read()
                    self.assertEqual(manifest['files'][name], hashlib.sha256(data).hexdigest())

    def test_optional_local_admin_reaches_ansible_without_changing_elev_or_ssh(self):
        settings = self.profile()
        hashed = '$6$testsalt$' + 'a' * 86
        settings['local_admin'] = {'username': 'makeradmin', 'password_hash': hashed}
        firstboot.validate_profile(settings, self.device(), CA, KEY)
        variables = firstboot.make_variables(settings, KEY,
                     {'files': {'assets/CaDoodle-ApplicationInstall.zip': '0' * 64}})
        self.assertIs(variables['adminkonto_aktiveret'], True)
        self.assertEqual(variables['adminkonto_navn'], 'makeradmin')
        self.assertEqual(variables['adminkonto_adgangskode_hash'], hashed)
        self.assertEqual(variables['grundsystem_driftsnoegle'], KEY)
        self.assertNotIn('elevkonto_adgangskode_hash', variables)
        for admin in ({'username': 'elev', 'password_hash': hashed},
                      {'username': 'root', 'password_hash': hashed},
                      {'username': 'elevhub', 'password_hash': hashed},
                      {'username': 'makeradmin', 'password_hash': '!'},
                      {'username': 'makeradmin', 'password': 'plaintext'},
                      {'username': 'makeradmin', 'password_hash': hashed + '\ncommand'}):
            settings['local_admin'] = admin
            with self.subTest(admin=admin), self.assertRaises(ValueError):
                firstboot.validate_profile(settings, self.device(), CA, KEY)

    def profile(self, computer="vaerksted-01", server="198.51.100.50"):
        return {"computer": computer, "server": server}

    def device(self, computer="vaerksted-01", token=TOKEN, ui_token="B" * 43):
        return {"computer": computer, "token": token, "ui_token": ui_token}

    def test_profile_name_ip_device_and_token_validation(self):
        firstboot.validate_profile(self.profile(), self.device(), CA, KEY)
        cases = [
            (self.profile("Bad_Name"), self.device(), "computernavn"),
            (self.profile(server="not-an-ip"), self.device(), "IP"),
            (self.profile(), self.device("vaerksted-02"), "computernavn"),
            (self.profile(), self.device(token="short"), "adgangsværdier"),
            (self.profile(), self.device(ui_token=TOKEN), "forskellige"),
        ]
        for settings, device, message in cases:
            with self.subTest(message=message), self.assertRaises(ValueError):
                firstboot.validate_profile(settings, device, CA, KEY)

    def test_optional_school_label_and_theme_are_forwarded(self):
        settings = self.profile()
        settings.update(school_name='Maker skole', theme='green')
        firstboot.validate_profile(settings, self.device(), CA, KEY)
        variables = firstboot.make_variables(
            settings, KEY, {'files': {'assets/CaDoodle-ApplicationInstall.zip': '0' * 64}})
        self.assertEqual(variables['kiosk_school_name'], 'Maker skole')
        self.assertEqual(variables['kiosk_theme'], 'green')
        for bad in ('blue', '', None):
            settings['theme'] = bad
            with self.subTest(theme=bad), self.assertRaises(ValueError):
                firstboot.validate_profile(settings, self.device(), CA, KEY)

    def test_onboarding_admin_sources_are_ip_allowlist(self):
        settings = self.profile()
        settings['onboarding'] = True
        variables = firstboot.make_variables(
            settings, KEY, {'files': {'assets/CaDoodle-ApplicationInstall.zip': '0' * 64}})
        self.assertEqual(variables['grundsystem_admin_kilder'], [settings['server']])
        settings['admin_sources'] = ['198.51.100.61']
        firstboot.validate_profile(settings, self.device(), CA, KEY)
        variables = firstboot.make_variables(
            settings, KEY, {'files': {'assets/CaDoodle-ApplicationInstall.zip': '0' * 64}})
        self.assertEqual(variables['grundsystem_admin_kilder'], ['198.51.100.61'])
        for bad in (['not-an-ip'], '198.51.100.61', []):
            settings['admin_sources'] = bad
            with self.subTest(sources=bad), self.assertRaises(ValueError):
                firstboot.validate_profile(settings, self.device(), CA, KEY)

    def test_bundle_rejects_path_escape_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            payload = bundle / "safe.txt"
            payload.write_text("safe", encoding="utf-8")
            (bundle / "manifest.json").write_text(json.dumps({"files": {"../escape": "0"}}))
            with self.assertRaises(ValueError):
                firstboot.verify_bundle(bundle)
            (bundle / "manifest.json").write_text(json.dumps({"files": {"safe.txt": "0"}}))
            with self.assertRaises(ValueError):
                firstboot.verify_bundle(bundle)

    def test_prepare_preseed_includes_payload_hash_ssh_and_ansible(self):
        template = (ROOT / "testmiljoe/vm/preseed-usb.skabelon.cfg").read_text(encoding="utf-8")
        payload_hash = "a" * 64
        result = build.prepare_preseed(template, "vaerksted-01", KEY, payload_hash)
        self.assertNotIn('d-i partman-auto/disk string /dev/sda', result)
        self.assertIn('d-i partman-auto/disk string /dev/elevhub-unselected', result)
        self.assertNotIn('d-i partman/early_command', result)
        self.assertNotIn('while :; do sleep', result)
        self.assertIn("openssh-server sudo python3 python3-apt ansible", result)
        self.assertIn(payload_hash + "  /cdrom/elevhub/payload.tar.gz", result)
        self.assertIn("in-target systemctl enable ssh.service", result)
        self.assertIn("in-target systemctl enable elevhub-firstboot.service", result)
        self.assertNotIn("@@", result)
        late = result.split('d-i preseed/late_command string ', 1)[1].split('\n\n', 1)[0]
        # cdebconf's line protocol can turn backslash-n inside the value into
        # a real newline, truncating the command at GET. Avoid these escapes.
        self.assertNotIn(r'\n', late)

    def test_generic_preseed_has_no_school_key_or_profile(self):
        template = (ROOT / "testmiljoe/vm/preseed-usb.skabelon.cfg").read_text(encoding="utf-8")
        result = build.prepare_generic_preseed(template, "elevhub-setup", "a" * 64)
        self.assertNotIn("authorized_keys", result)
        self.assertNotIn("@@", result)
        self.assertIn("/cdrom/elevhub/onboarding.py", result)
        self.assertIn("systemctl enable elevhub-onboarding.service", result)
        self.assertIn("systemctl disable getty@tty1.service", result)
        self.assertIn("/dev/elevhub-unselected", result)

    def test_optional_wifi_preseed_keeps_credentials_out_of_shell_commands(self):
        template = (ROOT / "testmiljoe/vm/preseed-usb.skabelon.cfg").read_text(encoding="utf-8")
        password = "test-'$()@@-secret"
        result = build.prepare_preseed(template, "vaerksted-01", KEY, "a" * 64,
                                       {"ssid": "Test netværk", "password": password})
        self.assertIn('d-i netcfg/wireless_essid string Test netværk\n', result)
        self.assertIn('d-i netcfg/wireless_security_type select wpa\n', result)
        self.assertIn('d-i netcfg/wireless_wpa string ' + password + '\n', result)
        late = result.split('d-i preseed/late_command string ', 1)[1].split('\n\n', 1)[0]
        self.assertNotIn(password, late)
        self.assertNotIn('netcfg/wireless_wpa', build.prepare_preseed(template, "vaerksted-01", KEY, "a" * 64))

    def test_wifi_rejects_bad_lengths_and_preseed_line_injection(self):
        for ssid, password in [("x", "short"), ("x", "z" * 64), ("æ" * 17, "testpassword"),
                               ("wifi\nd-i bad", "testpassword"), ("wifi", "line\npassword"),
                               ("wifi", "test\\npassword"), (" wifi", "testpassword")]:
            with self.subTest(ssid=ssid), self.assertRaises(ValueError):
                build.wifi_preseed({"ssid": ssid, "password": password})
        self.assertIn('string ' + 'a' * 64, build.wifi_preseed({"ssid": "wifi", "password": "a" * 64}))

    def write_install_fixture(self, root: Path):
        bundle = root / "bundle"
        config = root / "config"
        bundle.mkdir()
        config.mkdir()
        tracked = bundle / "linux-elevhub/ansible/site.yml"
        tracked.parent.mkdir(parents=True)
        tracked.write_text("site", encoding="utf-8")
        asset = bundle / "assets/CaDoodle-ApplicationInstall.zip"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"bootstrap zip")
        manifest = {"version": "test", "files": {
            "linux-elevhub/ansible/site.yml": hashlib.sha256(b"site").hexdigest(),
            "assets/CaDoodle-ApplicationInstall.zip": hashlib.sha256(b"bootstrap zip").hexdigest(),
        }}
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (config / "settings.json").write_text(json.dumps(self.profile()), encoding="utf-8")
        (config / "device.json").write_text(json.dumps(self.device()), encoding="utf-8")
        (config / "ca.crt").write_text(CA, encoding="utf-8")
        (config / "admin.pub").write_text(KEY, encoding="utf-8")
        return bundle, config

    def test_firstboot_records_completion_only_after_all_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, config = self.write_install_fixture(root)
            state = root / "state"
            with mock.patch.multiple(firstboot, BUNDLE=bundle, CONFIG=config, STATE=state), \
                 mock.patch.object(firstboot.os, "geteuid", return_value=0, create=True), \
                 mock.patch.object(firstboot.subprocess, "run") as run:
                firstboot.main()
                self.assertEqual(run.call_count, 3)
                self.assertTrue((state / "installation-complete.json").exists())
                run.reset_mock()
                firstboot.main()
                run.assert_not_called()

    def test_firstboot_failure_never_records_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, config = self.write_install_fixture(root)
            state = root / "state"
            with mock.patch.multiple(firstboot, BUNDLE=bundle, CONFIG=config, STATE=state), \
                 mock.patch.object(firstboot.os, "geteuid", return_value=0, create=True), \
                 mock.patch.object(firstboot.subprocess, "run", side_effect=RuntimeError("playbook failed")):
                with self.assertRaises(RuntimeError):
                    firstboot.main()
            self.assertFalse((state / "installation-complete.json").exists())


if __name__ == "__main__":
    unittest.main()
