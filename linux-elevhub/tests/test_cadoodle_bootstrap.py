"""Safety and idempotence checks for the offline CaDoodle bootstrap."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cadoodle"))
import bootstrap  # noqa: E402


JAR_HASH = hashlib.sha256(b"official jar").hexdigest()


class CadoodleBootstrapTests(unittest.TestCase):
    def archive(self, directory: Path, jar: bytes = b"official jar") -> Path:
        archive = directory / "install.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("CaDoodle-ApplicationInstall/", "")
            output.writestr("CaDoodle-ApplicationInstall/currentversion.txt", "0.46.094\n")
            output.writestr("CaDoodle-ApplicationInstall/0.46.094/", "")
            output.writestr("CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.jar", jar)
            output.writestr("CaDoodle-ApplicationInstall/0.46.094/jvm.json", "{}")
            output.writestr("CaDoodle-ApplicationInstall/0.46.094/gitcache.zip", b"gitcache")
            output.writestr("CaDoodle-ApplicationInstall/0.46.094/other.dat", b"official other")
        return archive

    def run_bootstrap(self, archive: Path, home: Path, runtime: Path | None = None):
        # Exercise the real ownership path without requiring root in WSL.
        # The isolated fixture's files belong to this test user.
        owner = mock.Mock(pw_uid=os.getuid(), pw_gid=os.getgid())
        with mock.patch.object(bootstrap.pwd, "getpwnam", return_value=owner), mock.patch.object(
            bootstrap.subprocess, "run", return_value=mock.Mock(returncode=1)
        ):
            return bootstrap.bootstrap(archive, home, JAR_HASH, runtime)

    def test_extracts_official_resources_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            home = root / "home" / "elev"
            self.assertTrue(self.run_bootstrap(archive, home))
            jar = home / "bin/CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.jar"
            self.assertEqual(jar.read_bytes(), b"official jar")
            self.assertFalse(self.run_bootstrap(archive, home))

    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escape", "x")
            with self.assertRaises(ValueError):
                self.run_bootstrap(archive, root / "home")

    def test_preserves_existing_unmodified_or_custom_jar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            jar = root / "home/bin/CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"tampered")
            self.assertTrue(self.run_bootstrap(archive, root / "home"))
            self.assertEqual(jar.read_bytes(), b"tampered")

    def test_preserves_modified_installed_jar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            home = root / "home"
            jar = home / "bin/CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"installed Kloster jar")
            self.assertTrue(self.run_bootstrap(archive, home))
            self.assertEqual(jar.read_bytes(), b"installed Kloster jar")

    def test_fresh_install_is_offline_ready_before_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            runtime = root / bootstrap.RUNTIME_ARCHIVE
            runtime.write_bytes(b"local zulu archive")
            home = root / "home"
            self.assertTrue(self.run_bootstrap(archive, home, runtime))
            install = home / "bin/CaDoodle-ApplicationInstall"
            self.assertTrue((home / "bin/BowlerStudioInstall").is_dir())
            self.assertEqual((install / "currentversion.txt").read_bytes(), b"0.46.094\n")
            self.assertEqual((install / "0.46.094/jvm.json").read_bytes(), b"{}")
            self.assertEqual((install / "0.46.094/gitcache.zip").read_bytes(), b"gitcache")
            self.assertEqual((install / bootstrap.RUNTIME_ARCHIVE).read_bytes(), runtime.read_bytes())

    def test_existing_modified_jar_is_preserved_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            runtime = root / bootstrap.RUNTIME_ARCHIVE
            runtime.write_bytes(b"local zulu archive")
            home = root / "home"
            jar = home / "bin/CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"installed Kloster jar")
            self.assertTrue(self.run_bootstrap(archive, home, runtime))
            self.assertEqual(jar.read_bytes(), b"installed Kloster jar")
            self.assertTrue((home / "bin/BowlerStudioInstall").is_dir())
            self.assertFalse(self.run_bootstrap(archive, home, runtime))
            self.assertEqual(jar.read_bytes(), b"installed Kloster jar")

    def test_failed_runtime_preparation_does_not_mark_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            home = root / "home"
            with self.assertRaises(ValueError):
                self.run_bootstrap(archive, home, root / "missing-zulu.tar.gz")
            self.assertFalse((home / "bin/BowlerStudioInstall").exists())

    def test_existing_marker_repairs_missing_runtime_without_replacing_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            runtime = root / bootstrap.RUNTIME_ARCHIVE
            runtime.write_bytes(b"local zulu archive")
            home = root / "home"
            install = home / "bin/CaDoodle-ApplicationInstall"
            jar = install / "0.46.094/CaDoodle-Application.jar"
            other = install / "0.46.094/other.dat"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"installed Kloster jar")
            other.write_bytes(b"local custom file")
            (home / "bin/BowlerStudioInstall").mkdir(parents=True)
            self.assertTrue(self.run_bootstrap(archive, home, runtime))
            self.assertEqual((install / bootstrap.RUNTIME_ARCHIVE).read_bytes(), runtime.read_bytes())
            self.assertEqual(other.read_bytes(), b"local custom file")
            self.assertEqual(jar.read_bytes(), b"installed Kloster jar")

    def test_bad_source_hash_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root, b"wrong")
            home = root / "home"
            with self.assertRaises(RuntimeError):
                self.run_bootstrap(archive, home)
            self.assertFalse((home / "bin").exists())

    def test_rejects_outside_prefix_and_existing_symlink_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.zip"
            with zipfile.ZipFile(outside, "w") as output:
                output.writestr("other/file", "x")
            with self.assertRaises(ValueError):
                self.run_bootstrap(outside, root / "home")

            archive = self.archive(root)
            home = root / "home"
            target = home / "bin/CaDoodle-ApplicationInstall"
            target.parent.mkdir(parents=True)
            target.symlink_to(root / "elsewhere", target_is_directory=True)
            with self.assertRaises(ValueError):
                self.run_bootstrap(archive, home)


if __name__ == "__main__":
    unittest.main()
