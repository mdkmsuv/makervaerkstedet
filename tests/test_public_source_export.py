"""Regression tests for the fail-closed public source exporter.

These tests use small temporary source trees.  They exercise the same
preflight, copy, and receipt paths as a real export without producing a
repository-sized release artifact.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import public_source


def _policy() -> dict:
    return {
        "schemaVersion": 1,
        "project": "Makerværkstedet",
        "credit": "Kreditering: Klostermarksskolen.",
        "sourceRoots": ["src"],
        "explicitFiles": ["AGENTS.md", "CREDITS.md"],
        "includeExtensions": [".py", ".json", ".md", ".txt"],
        "includeNames": [],
        "excludeDirectoryNames": [".git", "staging", "output"],
        "excludeGlobs": [],
        "requiredDocs": ["AGENTS.md"],
        "limits": {"maxFileBytes": 1024 * 1024, "maxTotalBytes": 4 * 1024 * 1024},
    }


def _tree(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Agentinstruktioner\n", encoding="utf-8")
    (root / "CREDITS.md").write_text(public_source.AUTHOR_CREDIT + "\n" + public_source.CREDIT + "\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("print('source')\n", encoding="utf-8")
    return root, _policy()


class PublicSourceExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp_path = Path(self.temp.name)

    def test_small_export_is_deterministic_and_never_overwrites(self):
        root, policy = _tree(self.tmp_path)
        output = self.tmp_path / "release"

        result = public_source.export_source(root, policy, str(output))
        self.assertEqual(result["files"], 3)
        manifest = json.loads((output / public_source.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual([item["path"] for item in manifest["files"]], ["AGENTS.md", "CREDITS.md", "src/main.py"])
        self.assertTrue(all(len(item["sha256"]) == 64 and len(item["mode"]) == 4 for item in manifest["files"]))
        self.assertEqual(len(manifest["sourcePolicySha256"]), 64)
        self.assertEqual((output / "src" / "main.py").read_text(encoding="utf-8"), "print('source')\n")
        with self.assertRaisesRegex(public_source.ExportError, "overwrite"):
            public_source.export_source(root, policy, str(output))


    def test_preflight_failure_does_not_create_output_parent(self):
        root, policy = _tree(self.tmp_path)
        (root / "src" / "secret.bin").write_bytes(b"binary")
        target_parent = self.tmp_path / "not-created"
        with self.assertRaisesRegex(public_source.ExportError, "Ukendt valgt kildefil"):
            public_source.export_source(root, policy, str(target_parent / "release"))
        self.assertFalse(target_parent.exists())


    def test_traversal_and_hardlink_are_rejected(self):
        root, policy = _tree(self.tmp_path)
        traversal = dict(policy, explicitFiles=["../outside.py"])
        with self.assertRaisesRegex(public_source.ExportError, "Ugyldig relativ"):
            public_source.audit(root, traversal)

        linked = root / "src" / "linked.py"
        try:
            os.link(root / "src" / "main.py", linked)
        except (AttributeError, OSError) as error:
            self.skipTest(f"Hardlinks are unavailable in this test environment: {error}")
        with self.assertRaisesRegex(public_source.ExportError, "Hardlink"):
            public_source.audit(root, policy)


    def test_symlink_is_never_followed(self):
        root, policy = _tree(self.tmp_path)
        outside = self.tmp_path / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        try:
            (root / "src" / "linked.py").symlink_to(outside)
        except (AttributeError, OSError) as error:
            self.skipTest(f"Symlinks are unavailable in this test environment: {error}")
        with self.assertRaisesRegex(public_source.ExportError, "Symlink/reparsepunkt"):
            public_source.audit(root, policy)

    def test_output_symlink_parent_is_rejected_before_mutation(self):
        root, policy = _tree(self.tmp_path)
        external = self.tmp_path / "external"
        external.mkdir()
        redirected = self.tmp_path / "redirected"
        try:
            redirected.symlink_to(external, target_is_directory=True)
        except (AttributeError, OSError) as error:
            self.skipTest(f"Symlinks are unavailable in this test environment: {error}")
        with self.assertRaisesRegex(public_source.ExportError, "Symlink/reparsepunkt"):
            public_source.export_source(root, policy, str(redirected / "release"))
        self.assertFalse((external / "release").exists())


    def test_secret_lookalikes_are_allowed_but_real_material_is_rejected(self):
        root, policy = _tree(self.tmp_path)
        (root / "src" / "fixture.py").write_text(
        "# -----BEGIN PRIVATE KEY----- (synthetic fixture marker)\n", encoding="utf-8"
        )
        (root / "src" / "fixture.json").write_text(
        json.dumps({"token": "fixture-token-value-for-tests"}), encoding="utf-8"
        )
        self.assertEqual(public_source.audit(root, policy)["files"], 5)

        (root / "src" / "real.json").write_text(
        json.dumps({"token": "AbCdEf0123456789AbCdEf0123456789"}), encoding="utf-8"
        )
        with self.assertRaisesRegex(public_source.ExportError, "hemmelighed"):
            public_source.audit(root, policy)

        (root / "src" / "real.json").unlink()
        (root / "src" / "private.txt").write_text(
        "-----BEGIN PRIVATE KEY-----\n"
        "AbCdEf0123456789AbCdEf0123456789AbCdEf0123456789\n"
        "-----END PRIVATE KEY-----\n",
        encoding="ascii",
        )
        with self.assertRaisesRegex(public_source.ExportError, "Privat PEM"):
            public_source.audit(root, policy)


    def test_size_limit_is_checked_during_preflight(self):
        root, policy = _tree(self.tmp_path)
        policy["limits"]["maxFileBytes"] = 4
        with self.assertRaisesRegex(public_source.ExportError, "filgrænsen"):
            public_source.audit(root, policy)

    def test_markdown_local_links_and_anchors_are_checked(self):
        root, policy = _tree(self.tmp_path)
        readme = root / "src" / "readme.md"
        readme.write_text("# Guide\n\n[program](main.py)\n\n## Kørsel\n", encoding="utf-8")
        self.assertEqual(public_source.audit(root, policy)["files"], 4)
        readme.write_text("[mangler](missing.py)\n", encoding="utf-8")
        with self.assertRaisesRegex(public_source.ExportError, "eksporteret fil"):
            public_source.audit(root, policy)
        readme.write_text("[ankerløs](readme.md#forkert)\n", encoding="utf-8")
        with self.assertRaisesRegex(public_source.ExportError, "anker"):
            public_source.audit(root, policy)


    def test_changed_source_leaves_owned_receipt_and_staging_for_cleanup(self):
        root, policy = _tree(self.tmp_path)
        output = self.tmp_path / "release"
        original = public_source._copy_checked
        changed = False

        def change_before_copy(item, stage, limit):
            nonlocal changed
            if not changed:
                changed = True
                item.path.write_text("changed after preflight\n", encoding="utf-8")
            return original(item, stage, limit)

        with patch.object(public_source, "_copy_checked", change_before_copy):
            with self.assertRaisesRegex(public_source.ExportError, "ændrede sig"):
                public_source.export_source(root, policy, str(output))
        self.assertFalse(output.exists())
        stages = list(self.tmp_path.glob(".release.public-source-*"))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / public_source.RECEIPT_NAME).is_file())
        self.assertTrue(public_source.cleanup_staging(str(stages[0]), root)["cleaned"])
        self.assertFalse(stages[0].exists())

    def test_repository_allowlist_collects_third_party_license_without_build_outputs(self):
        root = public_source._repo_root()
        policy = public_source._load_policy(root)
        files = public_source._collect(root, policy)
        included = {item.relative for item in files}
        self.assertIn("third_party/boxes.py/LICENSE.txt", included)
        self.assertNotIn("third_party/boxes.py/documentation/src/static", included)
        self.assertFalse(any("/build/" in path or "/output/" in path for path in included))

