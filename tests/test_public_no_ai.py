"""The distributable runs without private inference modules or services."""
import ast
import json
from pathlib import Path
import unittest

from scripts import public_source


class PublicNoAiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = public_source._repo_root()
        cls.files = public_source._collect(cls.root, public_source._load_policy(cls.root), content_checks=False)
        cls.selected = {item.relative for item in cls.files}

    def test_private_experiments_are_not_distributed(self):
        forbidden = {
            "system/app/bed_ai.py", "system/app/local_ai.py", "system/app/vision.py",
            "system/app/laser_vision.py", "system/app/laser_vision_client.py", "system/app/laser_vision_server.py",
            "system/app/server_ai_artifact.py", "system/app/plate_camera_client.py", "system/app/training_samples.py",
            "system/web/camera-batch.js", "system/web/plate-camera.js",
            "scripts/build_vm_mobile_sam.py", "scripts/setup_laser_vision.py",
            "scripts/install_vm_mobile_sam.ps1", "scripts/vm_vision_bootstrap.py",
            "tests/plate_camera_browser_fixture.py",
        }
        self.assertFalse(self.selected & forbidden)
        self.assertFalse(any(name.startswith("pladekamera/") for name in self.selected))
        for name in ("system/app/camera.py", "system/app/process_tree.py", "system/app/laser_station.py"):
            self.assertIn(name, self.selected)

    def test_retained_python_has_no_imports_of_excluded_modules(self):
        excluded = {"local_ai", "bed_ai", "vision", "laser_vision", "laser_vision_client",
                    "laser_vision_server", "plate_camera_client", "server_ai_artifact", "training_samples",
                    "platecam"}
        violations = []
        for item in self.files:
            if not item.relative.endswith(".py") or item.relative.startswith("system/third_party/"):
                continue
            for node in ast.walk(ast.parse(item.path.read_text(encoding="utf-8-sig"), filename=item.relative)):
                if isinstance(node, ast.Import):
                    names = [part for alias in node.names for part in alias.name.split(".")]
                elif isinstance(node, ast.ImportFrom):
                    names = (node.module or "").split(".")
                    if node.level or node.module == "app":
                        names += [alias.name for alias in node.names]
                else:
                    continue
                if excluded.intersection(names):
                    violations.append(f"{item.relative}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_standard_configurations_keep_current_workshops_and_omit_retired_apps(self):
        for name in ("system/linux-elevhub/server/elevhub/konfiguration.json",
                     "system/windows-elevhub/server/hub/konfiguration.windows.json"):
            with self.subTest(name=name):
                config = json.loads((self.root / name).read_text(encoding="utf-8-sig"))
                apps = {item["id"]: item for item in config["apps"]}
                self.assertTrue({'3dprint', 'laser', 'folie', 'scratch', 'microbit', 'internet'} <= set(apps))
                self.assertFalse({'datatraener', 'skolegpt', 'kredsloeb', 'cadoodle'} & set(apps))
                self.assertEqual(apps['scratch']['type'], 'lokal')
                self.assertEqual(apps['microbit']['start_url'], 'https://makecode.microbit.org/')


if __name__ == "__main__":
    unittest.main()
