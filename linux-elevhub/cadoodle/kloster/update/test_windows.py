"""Scratch-only PowerShell installer tests; run with Python on Windows."""
import hashlib
import json
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRY = "com/commonwealthrobotics/Main.class"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jar(path, marker):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(ENTRY, b"same-main-class")
        archive.writestr("version.txt", marker)


class WindowsUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "install"
        app = self.install / "app"
        app.mkdir(parents=True)
        (self.install / "runtime").mkdir()
        self.target = app / "CaDoodle-Kloster.jar"
        jar(self.target, "old")
        (app / "CaDoodle.cfg").write_text("app.mainclass=com.commonwealthrobotics.Main\napp.classpath=$APPDIR\\CaDoodle-Kloster.jar\n")
        (self.install / "runtime/release").write_text('MODULES="java.base java.logging jdk.zipfs"\n')
        self.package = self.root / "package"
        self.package.mkdir()
        self.payload = self.package / "CaDoodle-Kloster.jar"
        jar(self.payload, "new")
        self.manifest = {
            "schema": 1, "product": "CaDoodle", "app_version": "0.46.094",
            "platform": "windows", "payload": self.payload.name,
            "size": self.payload.stat().st_size, "sha256": sha(self.payload),
            "compatibility": {"required_entry": ENTRY, "entry_sha256": hashlib.sha256(b"same-main-class").hexdigest()},
        }
        (self.package / "manifest.json").write_text(json.dumps(self.manifest))
        self.state = self.root / "state"

    def run_installer(self, *extra):
        return subprocess.run([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "Install-Windows.ps1"),
            "-Package", str(self.package), "-InstallRoot", str(self.install), "-StateDir", str(self.state), *extra,
        ], capture_output=True, text=True)

    def test_apply_idempotency_and_rollback(self):
        old = sha(self.target)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sha(self.target), sha(self.payload))
        receipts = list(self.state.glob("transaction-*"))
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.state.glob("transaction-*")), receipts)
        result = self.run_installer("-Rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sha(self.target), old)
        self.assertEqual(self.run_installer("-Rollback").returncode, 0)

    def test_corrupt_payload_rejected(self):
        old = sha(self.target)
        self.payload.write_bytes(b"corrupt")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sha(self.target), old)

    def test_failure_after_replacement_restores_original(self):
        old = sha(self.target)
        self.state.mkdir()
        (self.state / "active.json").mkdir()  # Prevent the final metadata replacement.
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sha(self.target), old)

    def test_running_java_process_is_rejected(self):
        source = self.root / "Sleeper.cs"
        source.write_text("using System.Threading; class Sleeper { static void Main(string[] args) { Thread.Sleep(30000); } }")
        executable = self.root / "java.exe"
        compiler = Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe")
        built = subprocess.run([str(compiler), "/nologo", f"/out:{executable}", str(source)], capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        process = subprocess.Popen([str(executable), str(self.target)])
        try:
            time.sleep(0.25)
            result = self.run_installer()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CaDoodle", result.stderr)
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_rollback_metadata_failure_restores_updated_jar(self):
        self.assertEqual(self.run_installer().returncode, 0)
        updated = sha(self.target)
        (self.state / "last-rollback.json").mkdir()
        result = self.run_installer("-Rollback")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sha(self.target), updated)
        self.assertTrue((self.state / "active.json").exists())


if __name__ == "__main__":
    unittest.main()
