import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from scripts import build_simple_feed, build_update_manifest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('windows_package_builder',ROOT/'windows-elevhub/build_packages.py')
windows=importlib.util.module_from_spec(spec);spec.loader.exec_module(windows)

class RetiredWorkshopPayloadTests(unittest.TestCase):
    def test_windows_recursive_copy_excludes_removed_workshops_and_preserves_shared_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';target=Path(tmp)/'target'
            for name in ('skolegpt','kredsloeb','datatraener','circuitjs1','scratch','microbit','matchbox','model3d'):
                path=source/name/'index.html';path.parent.mkdir(parents=True);path.write_text('synthetic payload')
            windows.copy_tree(source,target)
            self.assertEqual({p.name for p in target.iterdir()}, {'scratch','microbit','matchbox','model3d'})
            windows.validate_package_tree(target)
            path=target/'datatraener'/'model.onnx';path.parent.mkdir();path.write_bytes(b'excluded')
            with self.assertRaisesRegex(ValueError,'excluded private feature'):
                windows.validate_package_tree(target)

    def test_windows_default_catalog_has_no_retired_workshops(self):
        import json
        config=json.loads((ROOT/'windows-elevhub/server/hub/konfiguration.windows.json').read_text(encoding='utf-8'))
        ids={app['id'] for app in config['apps']}
        self.assertFalse({'datatraener','skolegpt','kredsloeb'} & ids)
        self.assertTrue({'scratch','microbit','internet'} <= ids)

    def test_feed_rejects_removed_or_unknown_debian_package(self):
        for name in ('datatraener','skolegpt','kredsloeb','unknown'):
            self.assertFalse(build_simple_feed.allowed('pool/makervaerkstedet-'+name+'_1.0_all.deb'))
        for name,arch in (('elev','all'),('scratch','all'),('matchbox','amd64'),('updater','amd64')):
            self.assertTrue(build_simple_feed.allowed('pool/makervaerkstedet-'+name+'_1.0_'+arch+'.deb'))

    def test_update_archive_rejects_retired_runtime_but_keeps_scratch_microbit_matchbox(self):
        for name in ('skolegpt/index.html','apps/kredsloeb/index.html','assets/datatraener/index.html','vendor/circuitjs1/circuitjs.html'):
            with self.assertRaisesRegex(ValueError,'removed_workshop_in_package'):
                build_update_manifest.reject_removed_runtime(name)
        for name in ('apps/scratch/index.html','apps/microbit/index.html','bin/matchbox-window-manager'):
            build_update_manifest.reject_removed_runtime(name)

if __name__=='__main__':unittest.main()
