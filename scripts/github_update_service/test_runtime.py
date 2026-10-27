"""Small source fixtures; no GitHub, SSH, SYSTEM task or live installation."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import shutil
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

from scripts.github_update_service import common, linux, runtime, server

SOURCE = Path(__file__).resolve().parents[2]
VERSION = "2026.10.20"


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = self.root / "key"; self.key.write_text("private fixture")
        self.known = self.root / "known"; self.known.write_text("laser ssh-ed25519 " + "A" * 68)
        self.identity = self.root / "identities.json"
        common.write(self.identity, {"enheder": {"student": {"token_sha256": "a" * 64}}})
        self.registry = self.root / "registry.json"; common.write(self.registry, {})
        self.profiles = [
            {"id": "server", "name": "Server", "role": "windows_server", "worker_target": "server", "layout": "school-flat-v1",
             "baseline_version": "2026.10.19"},
            {"id": "student", "name": "Student", "role": "linux_student", "worker_target": "student",
             "ssh_key": str(self.key), "known_hosts": str(self.root / "student-known"),
             "host_key_alias": "student", "host_key_policy": "enrollment_proof",
             "identity_file": str(self.identity), "identity_sha256": "a" * 64,
             "registry": str(self.registry), "allowed_networks": ["192.0.2.0/24"]},
            {"id": "laser", "name": "Laser", "role": "windows_laser_teacher", "worker_target": "laser",
             "ssh_key": str(self.key), "known_hosts": str(self.known), "host_key_alias": "laser",
             "user": "teacher", "hosts": ["laser.example.test"], "install_path_policy": "user_default"},
        ]
        config = {"schema": 1, "repository": "example/project", "code_root": str(SOURCE),
                  **{key: str(self.root / key) for key in ("data_root", "work_root", "store_root", "staging_root")},
                  "profiles": self.profiles}
        self.config = self.root / "config.json"; common.write(self.config, config)
        self.controller = runtime.Controller(self.config, check_permissions=False)
        # Native Windows ACL creation requires an elevated/SYSTEM process. It
        # is deliberately outside these source-only unprivileged fixtures.
        self.addCleanup(patch.stopall)
        patch.object(runtime, "write_public_config", side_effect=common.write).start()

    def package(self, role, files):
        format_ = {"windows_server": "school-server-v1", "linux_student": "linux-queue-v1",
                   "windows_laser_teacher": "laser-code-v1"}[role]
        files = {**files, "CREDITS.md": b"Klostermarksskolen"}
        marker = {"schema": 1, "role": role, "format": format_, "version": VERSION,
                  "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        archive = self.root / (uuid.uuid4().hex + ".zip")
        with zipfile.ZipFile(archive, "w") as z:
            for name, data in files.items(): z.writestr(name, data)
            z.writestr("update-package.json", json.dumps(marker))
        package = {"download_id": uuid.uuid4().hex, "repository": "example/project", "release_id": 123,
                   "role": role, "format": format_, "version": VERSION, "asset": archive.name,
                   "size": archive.stat().st_size, "sha256": common.digest(archive), "path": str(archive)}
        return self.controller.importer.import_ready_package(package, self.controller.store_root,
            trusted_repository="example/project", trusted_package=package)

    def server_package(self):
        appearance = b"LOCAL_APPEARANCE_VERSION = 1\n"
        ordinary = b"pass\n"
        hub_files = {
            "app/a.py": hashlib.sha256(ordinary).hexdigest(),
            "app/school_appearance.py": hashlib.sha256(appearance).hexdigest(),
            "app/server.py": hashlib.sha256(ordinary).hexdigest(),
            "app/laser_server.py": hashlib.sha256(ordinary).hexdigest(),
            "web/school-theme.js": hashlib.sha256(ordinary).hexdigest(),
            "hub/elevhub.py": hashlib.sha256(appearance).hexdigest(),
            "hub/school_appearance.py": hashlib.sha256(appearance).hexdigest(),
        }
        print_files = {
            "app/a.py": hashlib.sha256(ordinary).hexdigest(),
            "app/school_appearance.py": hashlib.sha256(appearance).hexdigest(),
            "app/server.py": hashlib.sha256(ordinary).hexdigest(),
            "app/laser_server.py": hashlib.sha256(ordinary).hexdigest(),
            "web/school-theme.js": hashlib.sha256(ordinary).hexdigest(),
        }
        files = {"hub": hub_files, "print": print_files}
        candidate = {"format": "makervaerkstedet-school-flat-update-v1", "version": VERSION,
                     "trusted_runtime": {}, "files": files,
                     "appearance_hook": {
                         "hub": {"schemaVersion": 1, "files": {name: hub_files[name] for name in (
                             "app/school_appearance.py", "app/server.py", "app/laser_server.py",
                             "web/school-theme.js", "hub/elevhub.py", "hub/school_appearance.py")}},
                         "print": {"schemaVersion": 1, "files": {name: print_files[name] for name in (
                             "app/school_appearance.py", "app/server.py", "app/laser_server.py",
                             "web/school-theme.js")}},
                     }}
        payload = {
            "candidate/candidate.json": json.dumps(candidate).encode(),
            "candidate/hub/app/a.py": ordinary,
            "candidate/hub/app/school_appearance.py": appearance,
            "candidate/hub/app/server.py": ordinary,
            "candidate/hub/app/laser_server.py": ordinary,
            "candidate/hub/web/school-theme.js": ordinary,
            "candidate/hub/hub/elevhub.py": appearance,
            "candidate/hub/hub/school_appearance.py": appearance,
            "candidate/print/app/a.py": ordinary,
            "candidate/print/app/school_appearance.py": appearance,
            "candidate/print/app/server.py": ordinary,
            "candidate/print/app/laser_server.py": ordinary,
            "candidate/print/web/school-theme.js": ordinary,
        }
        return self.package("windows_server", payload)

    def linux_package(self, version=VERSION):
        files = {"linux-elevhub/server/elevhub/release.json": json.dumps({"release_id": version}).encode(),
                 "CREDITS.md": b"Klostermarksskolen"}
        files.update({
            "linux-elevhub/ansible/roles/adminkonto/tasks/update-interface.yml": b"- debug: {msg: fixture}\n",
            "linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update.py": b"admin helper\n",
            "linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update-gui.py": b"admin gui\n",
            "linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-xsession.sh": b"admin xsession\n",
            "linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-session.sh": b"admin session\n",
            "linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-return.py": b"admin return\n",
            "linux-elevhub/ansible/roles/adminkonto/templates/admin-bash-profile.j2": b"admin profile\n",
        })
        files["manifest.json"] = json.dumps({"version": version, "files": {
            name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}).encode()
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as tar:
            for name, data in files.items():
                info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))
        return self.package("linux_student", {"payload.tar.gz": output.getvalue()})

    def laser_package(self):
        files = {name: b"pass\n" for name in ("app/instance.py", "app/laser_station.py", "app/backup_guard.py")}
        files.update({"CREDITS.md": b"Klostermarksskolen", "web/laserstation.html": b"<p>Fixture</p>"})
        manifest = {"format": "makervaerkstedet-station-code-v1", "release": VERSION,
                    "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                              for name, data in files.items()}}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as z:
            for name, data in files.items(): z.writestr(name, data)
            z.writestr("manifest.json", json.dumps(manifest))
        return self.package("windows_laser_teacher", {"station-code.zip": output.getvalue()})

    def test_three_role_imports_create_real_ready_catalog_without_jobs(self):
        for receipt in (self.server_package(), self.linux_package(), self.laser_package()):
            self.assertTrue(self.controller.provision_import(receipt)["ready"])
        state = self.controller.admin.snapshot()
        self.assertEqual(len(state["releases"]), 3)
        self.assertTrue(all(r["ready"] for r in state["releases"]))
        self.assertTrue(all(t["job"] is None for t in state["targets"]))
        self.assertNotIn(str(self.key), json.dumps(state))
        self.assertNotIn(str(self.config), json.dumps(state))

    def test_server_enqueue_is_durable_and_import_idempotent(self):
        receipt = self.server_package()
        result = self.controller.provision_import(receipt)
        self.controller.admin.execute("teacher", {"request_id": "1" * 32, "action": "enqueue",
            "params": {"release_id": result["release_id"], "target_ids": ["server"]}})
        job = self.controller.admin.snapshot()["targets"][0]["job"]
        restarted = runtime.Controller(self.config, check_permissions=False)
        restarted.provision_import(receipt)
        self.assertEqual(restarted.admin.snapshot()["targets"][0]["job"], job)

    def test_missing_profile_does_not_fabricate_target(self):
        self.controller.profiles = []
        self.assertFalse(self.controller.provision_import(self.server_package())["ready"])
        self.assertEqual(self.controller.admin.snapshot()["targets"], [])

    def test_package_collision_and_profile_change_do_not_rebind_existing(self):
        receipt = self.server_package(); self.controller.provision_import(receipt)
        with self.assertRaisesRegex(ValueError, "collision"):
            self.controller.provision_import({**receipt, "package_sha256": "b" * 64})
        self.controller.profiles[0]["name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "profile_changed"):
            self.controller.provision_import(receipt)

    def test_wrong_linux_inner_version_blocks_readiness(self):
        with self.assertRaisesRegex(ValueError, "version_mismatch"):
            self.controller.provision_import(self.linux_package("2026.10.19"))
        self.assertFalse((self.controller.data_root / "updates/catalog.json").exists())

    def test_crash_after_catalog_publication_is_reconciled_without_job_changes(self):
        receipt = self.server_package()
        def interrupted(path, value):
            if Path(path).name == "targets.json": raise OSError("power loss")
            common.write(path, value)
        with patch.object(runtime, "write_public_config", side_effect=interrupted):
            with self.assertRaises(OSError): self.controller.provision_import(receipt)
        self.controller.provision_import(receipt)
        self.assertEqual(len(self.controller.admin.snapshot()["targets"]), 1)

    def test_import_failure_does_not_suspend_existing_dispatch(self):
        self.controller.provision_import(self.server_package())
        with patch.object(self.controller, "import_downloads", side_effect=OSError("offline")), \
             patch.object(server, "tick") as dispatch:
            self.controller.tick()
            dispatch.assert_called_once()

    def test_corrupt_worker_does_not_suspend_another_release(self):
        self.controller.provision_import(self.server_package())
        bad = self.controller.work_root / "github-aaa"; bad.mkdir()
        (bad / "config.json").write_text("{broken")
        with patch.object(self.controller, "import_downloads"), patch.object(server, "tick") as dispatch:
            self.controller.tick()
            dispatch.assert_called_once()

    def test_replaced_latest_release_reports_blocked(self):
        feed = Mock()
        feed.ready_packages.return_value = [{"release_id": 4, "asset": "old.zip", "download_id": "a" * 32}]
        feed.discover.return_value = {"release_id": 5, "version": VERSION, "packages": []}
        self.controller.feed = feed
        self.controller.import_downloads()
        feed.mark_outcome.assert_called_once_with("a" * 32, "blocked", "release_no_longer_current")

    def test_invalid_archive_reports_blocked_without_exposing_parser_output(self):
        feed = Mock()
        feed.ready_packages.return_value = [{"release_id": 5, "asset": "bad.zip", "download_id": "a" * 32}]
        feed.discover.return_value = {"release_id": 5, "version": VERSION, "packages": [{"asset": "bad.zip"}]}
        self.controller.feed = feed
        self.controller.importer = Mock()
        self.controller.importer.import_ready_package.side_effect = zipfile.BadZipFile("private parser detail")
        self.controller.import_downloads()
        feed.mark_outcome.assert_called_once_with("a" * 32, "blocked", "package_import_rejected")

    def test_local_transport_validation_rejects_shell_names_and_missing_networks(self):
        for changed in ({"worker_target": "student;bad"}, {"allowed_networks": []}, {"identity_sha256": "invalid"}):
            with self.assertRaises(ValueError):
                runtime.validate_profile({**self.profiles[1], **changed})
        with self.assertRaisesRegex(ValueError, "unsupported_server_layout"):
            runtime.validate_profile({**self.profiles[0], "layout": "clean-install"})

    def test_laser_result_requires_exact_operation_and_version(self):
        result = self.controller.provision_import(self.laser_package())
        root = self.controller.work_root / result["release_id"]
        config = common.read(root / "config.json")
        admin = Mock(); admin.worker_allowed.return_value = True; admin.worker_job.return_value = {"id": "d" * 32}
        self.controller.admin = admin
        worker = Mock(); worker.deliver.return_value = {"status": "complete", "release": "2026.10.19", "operation": "d" * 32}
        with patch.object(runtime, "load", return_value=worker):
            self.controller.laser_tick(root, config, self.profiles[2])
        self.assertEqual(admin.report.call_args.args[2], "blocked")

    def test_server_complete_requires_installed_hash_verification_after_restart(self):
        result = self.controller.provision_import(self.server_package())
        config = common.read(self.controller.work_root / result["release_id"] / "config.json")
        transaction = self.root / "operation"; transaction.mkdir()
        common.write(transaction / "server-journal/journal.json", {
            "state": "complete", "version": VERSION, "candidate_sha256": config["candidate_sha256"]})
        updater = Mock()
        self.assertEqual(server.inspect_result(transaction, config, updater), "complete")
        updater.installed.assert_called_once()
        updater.installed.side_effect = ValueError("changed files")
        with self.assertRaises(ValueError): server.inspect_result(transaction, config, updater)

    def test_server_interrupted_and_rollback_states_are_distinct(self):
        config = {"candidate_sha256": "c" * 64, "version": VERSION}
        transaction = self.root / "operation"; transaction.mkdir()
        for state, verified, expected in [
            ("prepared", False, "recovery_required"), ("applied_paused", False, "recovery_required"),
            ("program_rolled_back", False, "recovery_required"), ("rolled_back", False, "recovery_required"),
            ("rolled_back", True, "failed_rolled_back")]:
            common.write(transaction / "server-journal/journal.json", {
                **config, "state": state, "rollback_verified": verified})
            self.assertEqual(server.inspect_result(transaction, config, Mock()), expected)

    def server_dispatch_fixture(self):
        result = self.controller.provision_import(self.server_package())
        root = self.controller.work_root / result["release_id"]
        config = common.read(root / "config.json")
        admin = Mock(); admin.worker_allowed.return_value = True
        admin.worker_job.return_value = {"id": "a" * 32}
        updater = Mock(unsafe=True)
        updater.INSTALLS = {"hub": self.root / "installed-hub"}
        updater.verify.return_value = {"version": VERSION}
        return root, config, admin, updater

    def test_server_active_hardware_prevents_any_native_apply(self):
        root, config, admin, updater = self.server_dispatch_fixture()
        updater.assert_idle.side_effect = ValueError("active job")
        invoke = Mock()
        with patch.object(server, "load", return_value=updater):
            server.tick(root, config, self.profiles[0], admin, SOURCE, invoke)
        invoke.assert_not_called()
        self.assertEqual(admin.report.call_args.args[2], "waiting_for_idle")

    def test_server_native_completion_is_durable_across_controller_restart(self):
        root, config, admin, updater = self.server_dispatch_fixture()
        def invoke(argv, **kwargs):
            journal = Path(argv[argv.index("-Journal") + 1])
            common.write(journal / "journal.json", {"state": "complete", "version": VERSION,
                "candidate_sha256": config["candidate_sha256"]})
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(server, "load", return_value=updater):
            server.tick(root, config, self.profiles[0], admin, SOURCE, invoke)
            restarted_process = Mock()
            server.tick(root, config, self.profiles[0], admin, SOURCE, restarted_process)
        restarted_process.assert_not_called()
        self.assertEqual(admin.report.call_args.args[2], "complete")
        self.assertEqual(admin.report.call_args.kwargs["package_sha256"], config["package_sha256"])

    def test_server_interrupted_apply_does_not_restart_or_restore_databases(self):
        root, config, admin, updater = self.server_dispatch_fixture()
        transaction = root / ("operation-" + "a" * 32)
        common.write(transaction / "controller.json", {"started": True})
        common.write(transaction / "server-journal/journal.json", {"state": "prepared",
            "version": VERSION, "candidate_sha256": config["candidate_sha256"]})
        invoke = Mock()
        with patch.object(server, "load", return_value=updater):
            server.tick(root, config, self.profiles[0], admin, SOURCE, invoke)
        invoke.assert_not_called()
        updater.restore_program.assert_not_called()
        self.assertEqual(admin.report.call_args.args[2], "recovery_required")

    def test_server_failed_native_exit_never_claims_success(self):
        root, config, admin, updater = self.server_dispatch_fixture()
        def invoke(argv, **kwargs):
            journal = Path(argv[argv.index("-Journal") + 1])
            common.write(journal / "journal.json", {"state": "complete", "version": VERSION,
                "candidate_sha256": config["candidate_sha256"]})
            return subprocess.CompletedProcess(argv, 1)
        with patch.object(server, "load", return_value=updater):
            server.tick(root, config, self.profiles[0], admin, SOURCE, invoke)
        self.assertEqual(admin.report.call_args.args[2], "recovery_required")

    def test_actual_server_finalizer_checks_native_receipt_and_installed_bytes(self):
        result = self.controller.provision_import(self.server_package())
        config = common.read(self.controller.work_root / result["release_id"] / "config.json")
        updater = common.load(SOURCE / "scripts/school_server_update/update.py", "test_finalizer")
        installs = {role: self.root / ("installed-" + role) for role in ("hub", "print")}
        data = {"hub": self.root / "data-hub", "print": self.root / "data-print"}
        for role, path in installs.items():
            shutil.copytree(Path(config["candidate"]) / role, path)
            appearance = data[role] / "local-appearance"
            appearance.mkdir(parents=True)
            common.write(appearance / "manifest.json", {"schemaVersion": 1, "theme": "fixture"})
        journal = self.root / "finish-journal"
        with patch.object(updater, "INSTALLS", installs), patch.object(updater, "DATA", data):
            customization = updater.capture_customization(self.root / "customization-before")
            for path in installs.values():
                common.write(path / "school-update.json", {"version": VERSION,
                    "candidate_sha256": config["candidate_sha256"], "customization": customization})
        common.write(journal / "journal.json", {"state": "applied_paused", "candidate_sha256": config["candidate_sha256"]})
        with patch.object(updater, "INSTALLS", installs), patch.object(updater, "DATA", data):
            with self.assertRaisesRegex(ValueError, "Native task"):
                updater.finish(Path(config["candidate"]), journal, config["candidate_sha256"])
            common.write(Path(str(journal) + ".tasks") / "after.json", [])
            updater.finish(Path(config["candidate"]), journal, config["candidate_sha256"])
            self.assertEqual(common.read(journal / "journal.json")["state"], "complete")
            (installs["hub"] / "app/a.py").write_text("modified")
            with self.assertRaisesRegex(ValueError, "files differ"):
                updater.installed(Path(config["candidate"]), config["candidate_sha256"])

    def test_linux_profile_scope_is_not_the_old_twelve_computers(self):
        result = self.controller.provision_import(self.linux_package())
        config = common.read(self.controller.work_root / result["release_id"] / "config.json")
        helper = (self.controller.work_root / result["release_id"] / "remote.py").read_text()
        self.assertNotIn("vaerksted-01", helper)
        self.assertEqual(config["profiles"][0]["worker_target"], "student")
        self.assertNotIn("@@", helper)
        self.assertIn("pins.json", helper)

    def test_linux_prepare_binds_full_package_sha_to_installer(self):
        result = self.controller.provision_import(self.linux_package())
        config = common.read(self.controller.work_root / result["release_id"] / "config.json")
        helper = (self.controller.work_root / result["release_id"] / "remote.py").read_text()
        encoded = helper.split("INSTALLER_B64='", 1)[1].split("'", 1)[0]
        installer = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("PACKAGE_SHA='" + config["package_sha256"] + "'", installer)
        self.assertNotIn("UNBOUND_PACKAGE_SHA", installer)
        self.assertIn("'local_admin_approved':approval is not None", helper)
        self.assertIn("'restart_authorized':bool(approval", helper)
        self.assertIn("installer.tree(target) if target.is_dir()", helper)

    def test_linux_missing_local_approval_blocks_before_transfer(self):
        result = self.controller.provision_import(self.linux_package())
        root = self.controller.work_root / result["release_id"]; config = common.read(root / "config.json")
        common.write(self.registry, {"student": {"ip": "192.0.2.4", "sidst_set": linux.time.time()}})
        admin = Mock(); admin.worker_allowed.return_value = True; invoke = Mock()
        response = {"operation": config["operation"], "update_status": None,
                    "installed_release": "2026.10.19", "restart_authorized": False,
                    "local_admin_approved": False, "package_sha256": config["package_sha256"]}
        with patch.object(linux, "probe", return_value=(response, self.known)):
            linux.tick(root, config, self.profiles[1], admin, invoke)
        invoke.assert_not_called()
        self.assertEqual(admin.report.call_args.args[2], "blocked")
        self.assertEqual(admin.report.call_args.kwargs["code"], "local_admin_required")

    def test_linux_local_admin_approval_can_transfer_before_restart_approval(self):
        result = self.controller.provision_import(self.linux_package())
        root = self.controller.work_root / result["release_id"]; config = common.read(root / "config.json")
        common.write(self.registry, {"student": {"ip": "192.0.2.4", "sidst_set": linux.time.time()}})
        admin = Mock(); admin.worker_allowed.return_value = True
        response = {"operation": config["operation"], "update_status": None,
                    "installed_release": "2026.10.19", "restart_authorized": False,
                    "local_admin_approved": True, "package_sha256": config["package_sha256"]}
        staged = {"status": "staged_awaiting_restart", "operation": config["operation"],
                  "package_sha256": config["package_sha256"]}
        invoke = Mock()
        with patch.object(linux, "probe", return_value=(response, self.known)), \
             patch.object(linux, "command", side_effect=["/home/elevhub/" + config["operation"] + "-ABC123\n", "", json.dumps(staged)]):
            linux.tick(root, config, self.profiles[1], admin, invoke)
        self.assertEqual(admin.report.call_args.args[2], "staged_awaiting_restart")

    def test_linux_offline_never_sends_payload_or_claims_completion(self):
        result = self.controller.provision_import(self.linux_package())
        root = self.controller.work_root / result["release_id"]; config = common.read(root / "config.json")
        admin = Mock(); admin.worker_allowed.return_value = True
        invoke = Mock()
        linux.tick(root, config, self.profiles[1], admin, invoke)
        invoke.assert_not_called()
        self.assertEqual(admin.report.call_args.args[2], "waiting_for_computer")

    def test_linux_completed_probe_must_match_version_and_payload(self):
        result = self.controller.provision_import(self.linux_package())
        root = self.controller.work_root / result["release_id"]; config = common.read(root / "config.json")
        common.write(self.registry, {"student": {"ip": "192.0.2.4", "sidst_set": linux.time.time()}})
        admin = Mock(); admin.worker_allowed.return_value = True
        good = {"operation": config["operation"], "update_status": "complete",
                "release": VERSION, "payload_sha256": config["payload_sha256"]}
        for returned, expected in [(good, "complete"), ({**good, "payload_sha256": "e" * 64}, "retry")]:
            with patch.object(linux, "probe", return_value=(returned, self.known)):
                linux.tick(root, config, self.profiles[1], admin)
            self.assertEqual(admin.report.call_args.args[2], expected)

    def test_network_scope_and_freshness_are_required(self):
        profile = self.profiles[1]
        self.assertEqual(linux.address(profile, {"student": {"ip": "192.0.2.4", "sidst_set": 50}}, 60), "192.0.2.4")
        for ip, seen in [("198.51.100.4", 50), ("192.0.2.255", 50), ("192.0.2.4", 0), ("not-host", 50)]:
            self.assertIsNone(linux.address(profile, {"student": {"ip": ip, "sidst_set": seen}}, 200))

    def test_linux_identity_proof_is_required_before_first_key_pin(self):
        result = self.controller.provision_import(self.linux_package())
        root = self.controller.work_root / result["release_id"]; config = common.read(root / "config.json")
        profile = self.profiles[1]; public = "ssh-ed25519 " + "A" * 68
        def invoke(argv, **kwargs):
            (root / "student.candidate").write_text("student " + public)
            nonce = argv[-1].split()[-1]
            proof = linux.hmac.new(bytes.fromhex(profile["identity_sha256"]),
                (nonce + "\nstudent\n" + public).encode(), hashlib.sha256).hexdigest()
            return subprocess.CompletedProcess(argv, 0, json.dumps({"hostname": "student", "publickey": public, "proof": proof}))
        linux.probe(root, config, profile, "192.0.2.4", invoke)
        self.assertEqual(Path(profile["known_hosts"]).read_text(), "student " + public)
        Path(profile["known_hosts"]).unlink()
        def rejected(argv, **kwargs):
            response = invoke(argv, **kwargs)
            data = json.loads(response.stdout); data["proof"] = "0" * 64
            return subprocess.CompletedProcess(argv, 0, json.dumps(data))
        with self.assertRaisesRegex(ValueError, "proof_rejected"):
            linux.probe(root, config, profile, "192.0.2.4", rejected)
        self.assertFalse(Path(profile["known_hosts"]).exists())

    def test_known_versions_require_upgrade_and_unknown_requires_verified_baseline(self):
        common.require_upgrade("2026.10.19", VERSION)
        common.require_upgrade("legacy-marker", VERSION, "2026.10.19")
        for current, selected, baseline in [
                (None, VERSION, None), ("legacy-marker", VERSION, None),
                (VERSION, VERSION, None), ("2026.10.21", VERSION, "2026.10.19")]:
            with self.assertRaises(ValueError):
                common.require_upgrade(current, selected, baseline)

    def test_linux_known_same_or_newer_version_never_transfers(self):
        result = self.controller.provision_import(self.linux_package())
        root = self.controller.work_root / result["release_id"]; config = common.read(root / "config.json")
        common.write(self.registry, {"student": {"ip": "192.0.2.4", "sidst_set": linux.time.time()}})
        for current in (None, VERSION, "2026.10.21"):
            admin = Mock(); admin.worker_allowed.return_value = True
            response = {"operation": config["operation"], "update_status": None, "installed_release": current}
            invoke = Mock()
            with patch.object(linux, "probe", return_value=(response, self.known)):
                linux.tick(root, config, self.profiles[1], admin, invoke)
            invoke.assert_not_called()
            self.assertEqual(admin.report.call_args.args[2], "blocked")

    def test_server_unknown_or_newer_version_never_stops_services(self):
        root, config, admin, updater = self.server_dispatch_fixture()
        install = updater.INSTALLS["hub"]; install.mkdir()
        for current in (None, "2026.10.21"):
            marker = install / "school-update.json"
            if current is not None: common.write(marker, {"version": current})
            else: marker.unlink(missing_ok=True)
            profile = {k: v for k, v in self.profiles[0].items() if k != "baseline_version"}
            invoke = Mock()
            with patch.object(server, "load", return_value=updater):
                server.tick(root, config, profile, admin, SOURCE, invoke)
            invoke.assert_not_called()
            self.assertEqual(admin.report.call_args.args[2], "blocked")

    def test_laser_legacy_version_requires_explicit_baseline_and_refuses_downgrade(self):
        from scripts.queued_laser_update import remote_runner as remote
        install = self.root / "guarded-laser"; install.mkdir()
        candidate = self.root / "guarded-candidate"; candidate.mkdir()
        common.write(install / "laser-installation.json", {"role": "Station"})
        (candidate / "app").mkdir(); (candidate / "app/instance.py").write_bytes(b"pass\n")
        manifest = {"release": VERSION, "files": {"app/instance.py": {
            "sha256": common.digest(candidate / "app/instance.py"), "size": 5}}}
        with self.assertRaisesRegex(ValueError, "unknown"):
            remote.apply(install, candidate, manifest, "a" * 32, "b" * 64, True)
        common.write(install / "queued-release.json", {"release": "2026.10.21"})
        with self.assertRaisesRegex(ValueError, "not_older"):
            remote.apply(install, candidate, manifest, "a" * 32, "b" * 64, True, "2026.10.19")
        (install / "queued-release.json").unlink()
        with patch.object(remote, "candidate_probe"):
            self.assertEqual(remote.apply(install, candidate, manifest, "a" * 32, "b" * 64,
                                          True, "2026.10.19")["status"], "complete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
