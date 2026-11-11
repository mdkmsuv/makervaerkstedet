"""The real school-shell integration must ship its currently referenced images."""
import contextlib
import importlib.util
import io
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('workshop_style', ROOT/'linux-elevhub/server/style-workshop.py')
style = importlib.util.module_from_spec(spec)
spec.loader.exec_module(style)


class WorkshopStyleAssets(unittest.TestCase):
    def test_current_scratch_and_data_images_are_shipped(self):
        assets = ROOT/'linux-elevhub/server/elevhub/web/assets'
        catalog = (assets/'workshops.js').read_text(encoding='utf-8')
        for workshop in ('scratch', 'datatraener'):
            with self.subTest(workshop=workshop), tempfile.TemporaryDirectory(prefix='workshop-style-') as temp:
                build = Path(temp)
                original = '<html lang="en"><head><title>Upstream</title></head><body><main>Editor</main></body></html>'
                (build/'index.html').write_text(original, encoding='utf-8')
                with contextlib.redirect_stdout(io.StringIO()):
                    style.apply(build, workshop)
                image = re.search(r'^'+workshop+r":.*?image:'([^']+)'", catalog, re.MULTILINE).group(1)
                self.assertEqual((build/'creative'/image).read_bytes(), (assets/image).read_bytes())
                self.assertEqual((build/'index.before-creative.html').read_text(), original)
                html = (build/'index.html').read_text(encoding='utf-8')
                self.assertIn('lang="da"', html)
                self.assertIn('Klostermarksskolen', html)
                self.assertIn('creative/creative-app.js', html)
                # Reapplying keeps one integration and the upstream backup.
                with contextlib.redirect_stdout(io.StringIO()):
                    style.apply(build, workshop)
                self.assertEqual((build/'index.html').read_text(encoding='utf-8'), html)


if __name__ == '__main__':
    unittest.main()
