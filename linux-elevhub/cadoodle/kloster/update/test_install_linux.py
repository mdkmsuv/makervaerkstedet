#!/usr/bin/env python3
"""Scratch-only transaction tests for the Linux CaDoodle updater."""
import json
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import install_linux as update


def jar(path, marker):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(update.ENTRY, b"same-main-class")
        archive.writestr("version.txt", marker)


class LinuxUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "install/CaDoodle-Application.jar"
        self.target.parent.mkdir()
        jar(self.target, "old")
        self.cache = self.root / "cache/CaDoodle-Kloster.jar"
        self.state = self.root / "state"
        self.package = self.root / "package"
        self.package.mkdir()
        self.payload = self.package / "CaDoodle-Kloster.jar"
        jar(self.payload, "new")
        self.manifest = {
            "schema": 1, "product": "CaDoodle", "app_version": update.VERSION,
            "platform": "linux", "payload": self.payload.name,
            "size": self.payload.stat().st_size, "sha256": update.digest(self.payload),
            "compatibility": {"required_entry": update.ENTRY, "entry_sha256": update.entry_digest(self.payload)},
        }
        (self.package / "manifest.json").write_text(json.dumps(self.manifest))

    def test_install_idempotency_and_rollback_with_absent_cache(self):
        before = update.digest(self.target)
        manifest, payload = update.load_package(self.package)
        update.install(manifest, payload, self.target, self.cache, self.state)
        self.assertEqual(update.digest(self.target), manifest["sha256"])
        self.assertEqual(update.digest(self.cache), manifest["sha256"])
        receipts = list(self.state.glob("transaction-*"))
        update.install(manifest, payload, self.target, self.cache, self.state)
        self.assertEqual(list(self.state.glob("transaction-*")), receipts)
        update.rollback(self.target, self.cache, self.state)
        self.assertEqual(update.digest(self.target), before)
        self.assertFalse(self.cache.exists())
        update.rollback(self.target, self.cache, self.state)

    def test_rollback_restores_distinct_cache_and_rejects_modified_cache(self):
        self.cache.parent.mkdir()
        jar(self.cache, "cached-old")
        original_cache = update.digest(self.cache)
        manifest, payload = update.load_package(self.package)
        update.install(manifest, payload, self.target, self.cache, self.state)
        jar(self.cache, "external-change")
        with self.assertRaisesRegex(ValueError, "ændret"):
            update.rollback(self.target, self.cache, self.state)
        update.atomic_copy(payload, self.cache)
        update.rollback(self.target, self.cache, self.state)
        self.assertEqual(update.digest(self.cache), original_cache)

    def test_corrupt_payload_and_failed_cache_replace_leave_originals(self):
        manifest, payload = update.load_package(self.package)
        old_target = update.digest(self.target)
        old_cache = update.current_hash(self.cache)
        original_copy = update.atomic_copy

        def fail_cache(source, destination):
            if destination == self.cache and source == payload:
                raise OSError("simulated cache failure")
            return original_copy(source, destination)

        with mock.patch.object(update, "atomic_copy", side_effect=fail_cache):
            with self.assertRaisesRegex(OSError, "simulated"):
                update.install(manifest, payload, self.target, self.cache, self.state)
        self.assertEqual(update.current_hash(self.target), old_target)
        self.assertEqual(update.current_hash(self.cache), old_cache)
        self.assertFalse((self.state / "active.json").exists())
        self.payload.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            update.load_package(self.package)

    def test_rollback_failure_restores_updated_target_and_cache(self):
        self.cache.parent.mkdir()
        jar(self.cache, "cached-old")
        manifest, payload = update.load_package(self.package)
        update.install(manifest, payload, self.target, self.cache, self.state)
        original_copy = update.atomic_copy

        def fail_original_cache(source, destination):
            if destination == self.cache and source.name == "cache.jar" and source.parent.name.startswith("transaction-"):
                raise OSError("simulated rollback failure")
            return original_copy(source, destination)

        with mock.patch.object(update, "atomic_copy", side_effect=fail_original_cache):
            with self.assertRaisesRegex(OSError, "simulated rollback failure"):
                update.rollback(self.target, self.cache, self.state)
        self.assertEqual(update.digest(self.target), manifest["sha256"])
        self.assertEqual(update.digest(self.cache), manifest["sha256"])
        self.assertTrue((self.state / "active.json").exists())

    def test_pgrep_failure_is_fail_closed(self):
        with mock.patch.object(update.subprocess, "run", return_value=mock.Mock(returncode=2, stdout="")):
            with self.assertRaisesRegex(ValueError, "pgrep fejlede"):
                update.assert_not_running()

    def test_running_app_is_rejected(self):
        process = subprocess.Popen(["bash", "-c", "exec -a java python3 -c 'import time; time.sleep(30)' CaDoodle-Application.jar"])
        try:
            time.sleep(0.1)
            with self.assertRaisesRegex(ValueError, "CaDoodle kører"):
                update.assert_not_running()
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_lock_rejects_concurrent_update(self):
        lock = self.root / "launcher.lock"
        with update.launcher_lock(lock):
            with self.assertRaisesRegex(ValueError, "CaDoodle-start"):
                with update.launcher_lock(lock):
                    pass


if __name__ == "__main__":
    unittest.main()
