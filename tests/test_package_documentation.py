"""Check real installed guides using a tiny program layout, never build binaries."""
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit
from scripts import package_documentation as docs


class PackageDocumentationTests(unittest.TestCase):
    def test_agent_guides_credit_license_and_installed_links(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / 'hub').mkdir()
            (output / 'hub/makerspace_store.py').write_text('# fixture')
            (output / 'server').mkdir()
            source_server_readme = docs.ROOT / 'windows-elevhub/server/README.md'
            source_before = source_server_readme.read_bytes()
            (output / 'server/README.md').write_bytes(source_before)
            (output / 'web/assets').mkdir(parents=True)
            (output / 'web/assets/PUBLIC-ILLUSTRATIONS.md').write_bytes(
                (docs.ROOT / 'web/assets/PUBLIC-ILLUSTRATIONS.md').read_bytes())
            (output / 'deployment/school-setup').mkdir(parents=True)
            (output / 'deployment/school-setup/Setup-School.ps1').write_text('# fixture')
            docs.copy_documentation(output)
            self.assertIn('../docs/MAKERSPACE-OPDATERING.md',
                          (output / 'server/README.md').read_text(encoding='utf-8'))
            self.assertEqual(source_before, source_server_readme.read_bytes())
            for relative in ('AGENTS.md', 'CREDITS.md', 'LICENSE-SCOPE.md', 'docs/AGENTINDGANG.md', 'README.md'):
                self.assertIn('Klostermarksskolen', (output / relative).read_text(encoding='utf-8'))
            self.assertIn('../hub/makerspace_store.py', (output / 'docs/AGENTINDGANG.md').read_text(encoding='utf-8'))
            self.assertIn('i tilsvarende kildeudgivelse', (output / 'docs/SKOLETILPASNING.md').read_text(encoding='utf-8'))
            for path in output.rglob('*.md'):
                for _label, target in docs.LINK.findall(path.read_text(encoding='utf-8')):
                    parsed = urlsplit(target)
                    if parsed.scheme or not parsed.path:
                        continue
                    self.assertTrue((path.parent / parsed.path).exists(), f'{path.relative_to(output)} -> {target}')
            self.assertEqual((output / 'LICENSE').read_bytes(), (docs.ROOT / 'LICENSE').read_bytes())
            self.assertEqual((output / 'licenses/CC-BY-4.0.txt').read_bytes(),
                             (docs.ROOT / 'web/assets/CC-BY-4.0.txt').read_bytes())

    def test_external_and_source_only_links(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            text = '[API](https://example.test) [source](../tests/test_workflow_unbinding.py)'
            result = docs.render(text, 'docs/AGENTINDGANG.md', output)
            self.assertIn('[API](https://example.test)', result)
            self.assertIn('`tests/test_workflow_unbinding.py`', result)
