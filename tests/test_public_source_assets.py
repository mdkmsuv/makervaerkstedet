"""Regression coverage for the explicit public illustration allowlist."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import public_source
from tests.test_public_source_export import _tree


class PublicSourceAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = public_source._repo_root()
        cls.policy = public_source._load_policy(cls.root)
        cls.items = cls.policy["publicImages"]
        cls.by_path = {item["path"]: item for item in cls.items}

    def test_schema_pins_existing_non_logo_runtime_assets(self):
        self.assertEqual(len(self.items), 27)
        self.assertEqual(len(self.by_path), len(self.items))
        explicit = set(self.policy["explicitFiles"])
        self.assertTrue(set(self.by_path).issubset(explicit))
        self.assertFalse(any("logo" in path.lower() for path in self.by_path))
        self.assertFalse(any("output" in Path(path).parts for path in self.by_path))
        for item in self.items:
            path = self.root / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(item["provenance"].strip(), item["path"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"], item["path"])

    def test_policy_audit_selects_every_pinned_image(self):
        audited = public_source.audit(self.root, self.policy)
        self.assertGreater(audited["files"], len(self.items))
        collected = {item.relative: item for item in public_source._collect(self.root, self.policy)}
        self.assertTrue(set(self.by_path).issubset(collected))
        for path, expected in self.by_path.items():
            self.assertEqual(collected[path].sha256, expected["sha256"], path)

    def test_hash_pin_rejects_changed_image_before_export(self):
        item = self.items[0]
        original = (self.root / item["path"]).read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root, policy = _tree(parent)
            relative = 'web/assets/' + Path(item['path']).name
            candidate = root / relative
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(original)
            policy['explicitFiles'].append(relative)
            policy['publicImages'] = [dict(item, path=relative)]
            good = public_source.export_source(root, policy, str(parent / 'good'))
            self.assertEqual((Path(good['output']) / relative).read_bytes(), original)
            candidate.write_bytes(original + b'changed')
            output = parent / 'changed'
            with self.assertRaisesRegex(public_source.ExportError, 'publicImages'):
                public_source.export_source(root, policy, str(output))
            self.assertFalse(output.exists())

    def test_unknown_images_and_relabelled_non_images_are_rejected(self):
        item = self.items[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(item['path']).name
            path.write_bytes((self.root / item['path']).read_bytes())
            with self.assertRaisesRegex(public_source.ExportError, 'publicImages'):
                public_source._read_source(path, 64 * 1024 * 1024)
            path.write_bytes(b'not an image')
            pin = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(public_source.ExportError, 'format'):
                public_source._read_source(path, 1024, pin)

    def test_school_logo_cannot_be_added_as_a_public_illustration(self):
        item = dict(self.items[0], path='web/assets/klostermarksskolen-logo.png')
        policy = dict(self.policy, explicitFiles=self.policy['explicitFiles'] + [item['path']], publicImages=[item])
        with self.assertRaisesRegex(public_source.ExportError, 'skolelogo'):
            public_source._public_image_map(policy)

    def test_runtime_reference_set_is_allowlisted(self):
        expected = {
            "web/assets/maker-hero-v1.webp",
            "web/assets/maker-game-piece-v1.webp",
            "web/assets/maker-pencil-pot-v1.webp",
            "web/assets/maker-rover-v1.webp",
            "web/assets/workshop-laser-v1.webp",
            "web/assets/workshop-vinyl-v1.webp",
            "web/assets/workshop-3d-bauhaus-v1.png",
            "linux-elevhub/server/elevhub/web/assets/hub-bauhaus-v1.png",
            "linux-elevhub/server/elevhub/web/assets/workshop-3d-card.png",
            "linux-elevhub/server/kredsloeb/assets/workshop-circuit-v1.webp",
        }
        self.assertTrue(expected.issubset(self.by_path))

    def test_windows_pupil_client_is_outside_supported_source_manifest(self):
        manifest_text = (self.root / "deployment/public-source-manifest.json").read_text(encoding="utf-8")
        self.assertNotIn("windows-elevhub/client", manifest_text)
        self.assertFalse(any(item["path"].startswith("windows-elevhub/client/") for item in self.items))

    def test_license_and_provenance_document_are_exported(self):
        license_path = self.root / "web/assets/CC-BY-4.0.txt"
        document = self.root / "web/assets/PUBLIC-ILLUSTRATIONS.md"
        self.assertTrue(license_path.is_file())
        self.assertIn("Attribution 4.0 International", license_path.read_text(encoding="utf-8"))
        text = document.read_text(encoding="utf-8")
        self.assertIn("Klostermarksskolen", text)
        self.assertIn("filspecifikke prompt og genereringsdato er ikke bevaret", text)
        self.assertTrue({"web/assets/PUBLIC-ILLUSTRATIONS.md", "web/assets/CC-BY-4.0.txt"}.issubset(
            {item.relative for item in public_source._collect(self.root, self.policy)}
        ))


if __name__ == "__main__":
    unittest.main()
