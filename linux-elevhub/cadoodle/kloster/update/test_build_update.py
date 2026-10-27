"""Small fixture tests for the offline CaDoodle package builder."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


class BuilderTests(unittest.TestCase):
    def test_platform_archives_and_existing_output_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = []
            for platform in ("windows", "linux"):
                source = root / f"{platform}.jar"
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("com/commonwealthrobotics/Main.class", b"same-entry")
                    archive.writestr("build.txt", platform)
                inputs.append(source)
            out = root / "release"
            command = [sys.executable, str(HERE / "build_update.py"), "--windows-jar", str(inputs[0]), "--linux-jar", str(inputs[1]), "--out", str(out)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ("windows", "linux"):
                manifest = json.loads((out / name / "manifest.json").read_text())
                self.assertEqual(manifest["release_id"], "2026.10.06.17")
                self.assertEqual(manifest["app_version"], "0.46.094")
                archive_path = root / f"release-{name}.zip"
                with zipfile.ZipFile(archive_path) as archive:
                    members = {item.filename: item for item in archive.infolist()}
                    checksum_lines = archive.read("release/SHA256SUMS.txt").decode().splitlines()
                    for line in checksum_lines:
                        checksum, relative = line.split("  ", 1)
                        self.assertEqual(hashlib.sha256(archive.read("release/" + relative)).hexdigest(), checksum)
                    self.assertTrue(all("release/" + line.split("  ", 1)[1] in members for line in checksum_lines))
                    self.assertNotIn("release/" + ("linux" if name == "windows" else "windows") + "/manifest.json", members)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertTrue((out / "windows/manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
