"""The real school-shell integration must ship its currently referenced images."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import contextlib
import importlib.util
import io
from pathlib import Path
import re
import tempfile
import unittest

ROOT = repo_root(__file__)
spec = importlib.util.spec_from_file_location('workshop_style', source_path('linux-elevhub/server/style-workshop.py', ROOT))
style = importlib.util.module_from_spec(spec)
spec.loader.exec_module(style)


class WorkshopStyleAssets(unittest.TestCase):
    def test_current_scratch_images_are_shipped(self):
        assets = source_path('linux-elevhub/server/elevhub/web/assets', ROOT)
        catalog = (assets/'workshops.js').read_text(encoding='utf-8')
        for workshop in ('scratch',):
            with self.subTest(workshop=workshop), tempfile.TemporaryDirectory(prefix='workshop-style-') as temp:
                build = Path(temp)
                original = '<html lang="en"><head><title>Upstream</title></head><body><main>Editor</main></body></html>'
                (build/'index.html').write_text(original, encoding='utf-8')
                with contextlib.redirect_stdout(io.StringIO()):
                    style.apply(build, workshop)
                image = re.search(r'^'+workshop+r":.*?image:'([^']+)'", catalog, re.MULTILINE).group(1)
                self.assertEqual((build/'creative'/image).read_bytes(), (assets/image).read_bytes())
                self.assertFalse((build/'creative/klostermarksskolen-logo.png').exists())
                self.assertEqual((build/'index.before-creative.html').read_text(), original)
                html = (build/'index.html').read_text(encoding='utf-8')
                self.assertIn('lang="da"', html)
                self.assertIn('<title>Scratch · Makerværkstedet</title>', html)
                self.assertIn('creative/creative-app.js', html)
                self.assertEqual((build/'creative/creative-app.js').read_bytes(),
                    source_path('linux-elevhub/server/elevhub/web/assets/creative-app.js', ROOT).read_bytes())
                # Reapplying keeps one integration and the upstream backup.
                with contextlib.redirect_stdout(io.StringIO()):
                    style.apply(build, workshop)
                self.assertEqual((build/'index.html').read_text(encoding='utf-8'), html)

    def test_retired_workshops_cannot_be_styled(self):
        with tempfile.TemporaryDirectory(prefix='workshop-style-retired-') as temp:
            build = Path(temp)
            original = '<html><body>Upstream</body></html>'
            (build/'index.html').write_text(original, encoding='utf-8')
            for workshop in ('cadoodle', 'skolegpt', 'kredsloeb', 'datatraener'):
                with self.subTest(workshop=workshop), self.assertRaises(ValueError):
                    style.apply(build, workshop)
            self.assertEqual((build/'index.html').read_text(encoding='utf-8'), original)
            self.assertFalse((build/'creative').exists())


if __name__ == '__main__':
    unittest.main()
