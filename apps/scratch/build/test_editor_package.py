"""Scratchs editor skal kunne pakkes uden et historisk hovedsystemsnapshot."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class EditorPackageTest(unittest.TestCase):
    def test_editor_files_get_an_explicit_app_owned_lock_and_private_metadata_stays_out(self):
        with tempfile.TemporaryDirectory(prefix="scratch-package-test-") as temporary:
            root = Path(temporary)
            build = root / "editor"
            build.mkdir()
            for name in ("index.html", "guistandalone.js", "source.zip", "LICENSE",
                         "LICENSE-scratch-gui", "TRADEMARK", "TRADEMARK-scratch-gui",
                         "CREDITS.txt", "EXTRA-LIBRARY-CREDITS.txt",
                         "library-assets-manifest.json", "extra-library-manifest.json"):
                (build / name).write_bytes(("syntetisk " + name).encode())
            (build / "static/library-assets").mkdir(parents=True)
            (build / "static/library-assets/new.png").write_bytes(b"new-public-asset")
            (build / "creative").mkdir()
            (build / "creative/autosave.js").write_bytes(b"app-owned; added by files.json")
            (build / "installation.json").write_text('{"sourcePath":"privat byggesti"}')
            lock = root / "scratch-editor.json"
            result = subprocess.run([sys.executable, "-Xutf8", "-B", str(Path(__file__).with_name("make_editor_lock.py")),
                                     "--build-dir", str(build), "--output", str(lock)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, "Appens pakkeopskrift mangler: " + result.stderr)
            data = json.loads(lock.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "makervaerkstedet.debian-inputs.v2")
            self.assertEqual(data["package"], "makervaerkstedet-scratch")
            expected = json.loads(Path(__file__).resolve().parents[1].joinpath("app.json").read_text())
            self.assertEqual(data["version"], expected["version"])
            self.assertEqual(len(data["files"]), 12)
            for entry in data["files"]:
                self.assertTrue(entry["path"].startswith("usr/local/share/elevhub/apps/scratch/"))
                self.assertNotIn("installation.json", entry["path"])
                self.assertNotIn("/creative/", entry["path"])
                self.assertEqual(entry["sha256"], hashlib.sha256(Path(entry["source"]).read_bytes()).hexdigest())
                self.assertEqual(entry["mode"], 0o644)


if __name__ == "__main__":
    unittest.main()
