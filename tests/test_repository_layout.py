"""Physical source ownership is independent from installed URLs and paths."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
layout=__import__('repository') if importlib.util.find_spec('repository') else None


class RepositoryLayoutTests(unittest.TestCase):
    def test_main_and_apps_have_one_physical_owner(self):
        self.assertIsNotNone(layout,'The source layout resolver is missing')
        self.assertEqual(layout.repo_root(__file__),ROOT)
        for name in ('app','linux-elevhub','mkv2','windows','windows-elevhub','web','third_party'):
            self.assertTrue((ROOT/'system'/name).is_dir(),name)
            self.assertFalse((ROOT/name).exists(),'Old parallel source directory: '+name)
        for app in ('laser','folie','3dprint','scratch','microbit'):
            folder=ROOT/'apps'/app
            files=json.loads((folder/'files.json').read_text())['files']
            for item in files:
                self.assertTrue(item['source'].startswith('apps/'+app+'/'),item['source'])
                self.assertTrue((ROOT/item['source']).exists(),item['source'])
        self.assertFalse((ROOT/'system/web/laser.html').exists())
        self.assertFalse((ROOT/'system/web/model3d.html').exists())

    def test_build_aliases_preserve_installation_relative_names(self):
        self.assertIsNotNone(layout,'The source layout resolver is missing')
        expected={'app/app_catalog.py':'system/app/app_catalog.py',
                  'web/school-theme.js':'system/web/school-theme.js',
                  'web/laser.html':'apps/folie/web/laser.html',
                  'web/model3d/app.js':'apps/3dprint/web/model3d/app.js',
                  'web/stl-validation.mjs':'sdk/web/stl-validation.mjs',
                  'linux-elevhub/server/microbit-controller/app.js':'apps/microbit/web/app.js'}
        for logical,actual in expected.items():
            self.assertEqual(layout.source_path(logical,ROOT),ROOT/actual)
        for unsafe in ('../identity.key','/etc/passwd','C:/data','apps/../app','web\\server.js'):
            with self.assertRaises(ValueError):layout.source_path(unsafe,ROOT)


if __name__=='__main__':unittest.main()
