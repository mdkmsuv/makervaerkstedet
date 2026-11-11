import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('model3d_package_gate',ROOT/'windows-elevhub/build_packages.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
class Model3dPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        def write(name,data):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return hashlib.sha256(data).hexdigest()
        sources={name:write(name,b'synthetic reviewed input') for name in ('package.json','package-lock.json','scripts/build_model3d_vendor.mjs','web/model3d.html','web/model3d.css','web/model3d/app.js')}
        outputs={name:{'sha256':write('web/vendor/'+name,b'synthetic output'),'size':16} for name in ('model3d-app.js','model3d-app.js.LEGAL.txt','model3d-geometry-worker.js','model3d-manifold.wasm')}
        licenses={name:write('web/assets/licenses/model3d/'+name,b'synthetic license') for name in ('manifold-3d-LICENSE.txt','three-LICENSE.txt','opentype-LICENSE.txt','Metrophobic-OFL.txt')}
        font=self.root/'web/assets/model3d/Metrophobic-Regular.ttf';font.parent.mkdir(parents=True);shutil.copyfile(ROOT/'web/assets/model3d/Metrophobic-Regular.ttf',font)
        self.manifest=self.root/'web/vendor/model3d-vendor-manifest.json';self.manifest.write_text(json.dumps({'schema':1,'source_sha256':sources,'license_sha256':licenses,'outputs':outputs}))
    def test_complete_receipt_passes_and_modified_js_wasm_or_license_is_rejected(self):
        gate.validate_model3d_assets(self.root,source_root=self.root)
        for name in ('web/vendor/model3d-app.js','web/vendor/model3d-manifold.wasm','web/assets/licenses/model3d/three-LICENSE.txt'):
            path=self.root/name;old=path.read_bytes();path.write_bytes(b'modified')
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'model3d_.*hash_mismatch'):gate.validate_model3d_assets(self.root,source_root=self.root)
            path.write_bytes(old)
    def test_stale_source_or_unrecorded_local_source_cannot_be_shipped(self):
        path=self.root/'web/model3d/app.js';path.write_bytes(b'new source not rebuilt')
        with self.assertRaisesRegex(ValueError,'source_hash_mismatch'):gate.validate_model3d_assets(self.root,source_root=self.root)
        path.write_bytes(b'synthetic reviewed input');(path.parent/'new-worker.js').write_bytes(b'new unbuilt worker')
        with self.assertRaisesRegex(ValueError,'source_receipt_incomplete'):gate.validate_model3d_assets(self.root,source_root=self.root)
    def test_missing_bundle_and_missing_font_are_rejected(self):
        path=self.root/'web/assets/model3d/Metrophobic-Regular.ttf';path.unlink()
        with self.assertRaisesRegex(ValueError,'font_hash_mismatch'):gate.validate_model3d_assets(self.root)
        self.manifest.unlink()
        with self.assertRaisesRegex(ValueError,'build_receipt_required'):gate.validate_model3d_assets(self.root)
if __name__=='__main__':unittest.main()
