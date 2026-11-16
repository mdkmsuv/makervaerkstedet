"""Prøv den app-ejede figurpakke med rigtige CC0-kilder og PNG-billeder."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image
from extra_library import install_library, LIBRARY, ARCHIVES


class ExtraLibraryTest(unittest.TestCase):
    def test_native_library_preserves_standard_figures_and_has_ordered_animation_frames(self):
        script = Path(__file__).with_name("extra_library.py")
        with tempfile.TemporaryDirectory(prefix="scratch-library-test-") as temporary:
            root = Path(temporary)
            library = root / "gui/src/lib/libraries"
            library.mkdir(parents=True)
            standard = {"name": "Eksisterende figur", "tags": ["animals"], "costumes": []}
            (library / "sprites.json").write_text(json.dumps([standard]), encoding="utf-8")
            (library / "costumes.json").write_text("[]", encoding="utf-8")
            output = root / "output"
            result = subprocess.run(
                [sys.executable, "-Xutf8", "-B", str(script), "--gui-dir", str(root / "gui"),
                 "--output-dir", str(output)], capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(result.returncode, 0, "Figurpakken skal kunne bygges: " + result.stderr)
            sprites = json.loads((library / "sprites.json").read_text(encoding="utf-8"))
            self.assertEqual(sprites[0], standard)
            self.assertEqual(len(sprites), 11)
            extra = sprites[1:]
            self.assertEqual(sum(len(sprite["costumes"]) for sprite in extra), 442)
            robot = next(sprite for sprite in extra if sprite["name"] == "Kenney robot")
            self.assertEqual(robot["costumes"][0]["name"], "stå")
            walking = [costume for costume in robot["costumes"] if costume["name"].startswith("gå-")]
            self.assertEqual([costume["name"] for costume in walking], [f"gå-{i:02}" for i in range(1, 9)])
            self.assertEqual(len({costume["assetId"] for costume in walking}), 8)
            frog = next(sprite for sprite in extra if sprite["name"] == "Pixel ninja-frø")
            running = [costume for costume in frog["costumes"] if costume["name"].startswith("løb-")]
            self.assertEqual([costume["name"] for costume in running], [f"løb-{i:02}" for i in range(1, 13)])
            self.assertGreater(len({costume["assetId"] for costume in running}), 1)
            with zipfile.ZipFile(ARCHIVES / "pixel-adventure-1.zip") as archive:
                with Image.open(io.BytesIO(archive.read("Free/Main Characters/Ninja Frog/Run (32x32).png"))) as sheet:
                    for frame, costume in enumerate(running):
                        expected = sheet.crop((frame * 32, 0, (frame + 1) * 32, 32)).convert("RGBA").tobytes()
                        with Image.open(output / "static/library-assets" / costume["md5ext"]) as actual:
                            self.assertEqual(actual.convert("RGBA").tobytes(), expected)
            for sprite in extra:
                self.assertEqual(sprite["blocks"], {})
                for costume in sprite["costumes"]:
                    data = (output / "static/library-assets" / costume["md5ext"]).read_bytes()
                    self.assertEqual(hashlib.md5(data).hexdigest(), costume["assetId"])
                    with Image.open(io.BytesIO(data)) as image:
                        self.assertEqual(image.format, "PNG")
                        if sprite["name"].startswith("Pixel"):
                            self.assertEqual(image.size, (32, 32))
                            self.assertEqual((costume["rotationCenterX"], costume["rotationCenterY"]), (16, 16))
            costumes = json.loads((library / "costumes.json").read_text(encoding="utf-8"))
            self.assertEqual(len(costumes), 442)
            self.assertTrue(all("mkv-extra-library" in costume["tags"] for costume in costumes))
            self.assertTrue(any(costume["name"] == "løb-06 · Pixel ninja-frø" for costume in costumes))
            receipt = json.loads((output / "extra-library-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["sprite_count"], 10)
            self.assertEqual(receipt["costume_count"], 442)
            self.assertTrue(all(pack["license"] == "CC0-1.0" for pack in receipt["sources"]))
            second_output = root / "second-output"
            install_library(root / "gui", second_output)
            self.assertEqual(json.loads((library / "sprites.json").read_text(encoding="utf-8")), sprites)
            self.assertEqual((output / "extra-library-manifest.json").read_bytes(),
                             (second_output / "extra-library-manifest.json").read_bytes())
            for asset in (output / "static/library-assets").iterdir():
                self.assertEqual(asset.read_bytes(), (second_output / "static/library-assets" / asset.name).read_bytes())

    def test_changed_source_is_rejected_before_existing_library_or_output_is_changed(self):
        with tempfile.TemporaryDirectory(prefix="scratch-library-integrity-") as temporary:
            root = Path(temporary)
            library = root / "gui/src/lib/libraries"
            library.mkdir(parents=True)
            original = b'[{"name":"Bevar denne figur","costumes":[]}]'
            (library / "sprites.json").write_bytes(original)
            (library / "costumes.json").write_text("[]", encoding="utf-8")
            sources = root / "sources"
            sources.mkdir()
            shutil.copyfile(LIBRARY / "sources.json", sources / "sources.json")
            data = (ARCHIVES / "kenney_toonCharacters1.zip").read_bytes()
            (sources / "kenney_toonCharacters1.zip").write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            with self.assertRaisesRegex(ValueError, "SHA256"):
                install_library(root / "gui", root / "output", sources, sources)
            self.assertEqual((library / "sprites.json").read_bytes(), original)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
