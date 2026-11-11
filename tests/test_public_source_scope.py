"""Keep working notes out of the product repository and installation guides."""
from pathlib import Path
import tempfile
import unittest

from scripts import package_documentation
from scripts import public_source


class PublicSourceScopeTests(unittest.TestCase):
    def test_product_selection_excludes_internal_documents(self):
        root = public_source._repo_root()
        policy = public_source._load_policy(root)
        selected = {item.relative for item in public_source._collect(
            root, policy, content_checks=False)}
        self.assertFalse(any(p.startswith("third_party/boxes.py/examples/") for p in selected))
        self.assertNotIn("third_party/boxes.py/tests/test_svg.py", selected)
        for path in (
            "LASER-KAMERA-AI-RESEARCH.md",
            "LASER-RESEARCH.md",
            "CADOODLE-VISUELT-LOEFT.md",
            "docs/MODNINGSPLAN.md",
            "docs/TESTOVERBLIK.md",
            "docs/KRITISK-GENNEMGANG-2026-10-19.md",
            "linux-elevhub/CLAUDE.md",
            "linux-elevhub/LANCERINGSRAPPORT.md",
            "linux-elevhub/cadoodle/kloster/research/cad-learning-sources.md",
            "scripts/start-local-makerspace.ps1",
            "scripts/prepare_windows_qa_storage.ps1",
            "scripts/megavaerkstedet_update.py",
            "scripts/update-megavaerkstedet.ps1",
            "linux-elevhub/NETVAERK.md",
            "linux-elevhub/laererpanel/README.md",
        ):
            self.assertNotIn(path, selected)
        for path in (
            "app/laser_server.py", "app/school_appearance.py",
            "scripts/build_github_release.py",
            "scripts/school_server_update/update.py",
            "AGENTS.md", "LICENSE", "CREDITS.md", "LICENSE-SCOPE.md",
            "docs/KOM-GODT-IGANG.md", "docs/GITHUB-OPDATERINGER.md",
        ):
            self.assertIn(path, selected)

    def test_new_work_documents_are_not_automatically_selected(self):
        policy = public_source._load_policy(public_source._repo_root())
        # Exercise traversal, not only an assertion about the configured globs.
        # First-party docs must be individually reviewed even inside source roots.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = dict(policy, explicitFiles=[], publicImages=[], publicFonts=[])
            for name in policy["sourceRoots"]:
                base = root / name
                base.mkdir(parents=True, exist_ok=True)
                for filename in ("new-research.md", "notes.txt", "report.rst", "experiment.ipynb"):
                    (base / filename).write_text("Working material", encoding="utf-8")
            program = root / policy["sourceRoots"][0] / "scope_fixture.py"
            program.write_text("pass\n", encoding="utf-8")
            selected = public_source._collect(root, policy, content_checks=False)
            self.assertEqual([item.relative for item in selected],
                             [program.relative_to(root).as_posix()])

    def test_installed_guides_keep_operations_without_internal_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            package_documentation.copy_documentation(output)
            self.assertFalse((output / "docs/MODNINGSPLAN.md").exists())
            self.assertFalse((output / "docs/PUBLIC-RELEASE.md").exists())
            for name in ("GITHUB-OPDATERINGER.md", "OPDATERINGSKOE.md", "KOM-GODT-IGANG.md"):
                self.assertTrue((output / "docs" / name).is_file())
            self.assertEqual(
                (output / "LICENSE").read_bytes(),
                (package_documentation.ROOT / "LICENSE").read_bytes())


if __name__ == "__main__":
    unittest.main()
