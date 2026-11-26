from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('model3d_package_gate',source_path('windows-elevhub/build_packages.py', ROOT))
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
class Model3dPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        def write(name,data):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return hashlib.sha256(data).hexdigest()
        shutil.copyfile(ROOT/'REPOSITORY-LAYOUT.json', self.root/'REPOSITORY-LAYOUT.json')
        sources={name:write(name,b'synthetic reviewed input') for name in ('package.json','package-lock.json','scripts/build_model3d_vendor.mjs','apps/3dprint/build.mjs','scripts/repository-layout.mjs','sdk/web/stl-validation.mjs','apps/3dprint/web/model3d.html','apps/3dprint/web/model3d.css','apps/3dprint/web/model3d/app.js')}
        sources['REPOSITORY-LAYOUT.json']=hashlib.sha256((self.root/'REPOSITORY-LAYOUT.json').read_bytes()).hexdigest()
        outputs={name:{'sha256':write('apps/3dprint/web/vendor/'+name,b'synthetic output'),'size':16} for name in ('model3d-app.js','model3d-app.js.LEGAL.txt','model3d-geometry-worker.js','model3d-manifold.wasm')}
        licenses={name:write('apps/3dprint/web/assets/licenses/model3d/'+name,b'synthetic license') for name in ('manifold-3d-LICENSE.txt','three-LICENSE.txt','opentype-LICENSE.txt','Metrophobic-OFL.txt')}
        font=self.root/'apps/3dprint/web/assets/model3d/Metrophobic-Regular.ttf';font.parent.mkdir(parents=True);shutil.copyfile(source_path('apps/3dprint/web/assets/model3d/Metrophobic-Regular.ttf', ROOT),font)
        self.manifest=self.root/'apps/3dprint/web/vendor/model3d-vendor-manifest.json';self.manifest.write_text(json.dumps({'schema':1,'source_sha256':sources,'license_sha256':licenses,'outputs':outputs}))
    def test_complete_receipt_passes_and_modified_js_wasm_or_license_is_rejected(self):
        gate.validate_model3d_assets(self.root,source_root=self.root)
        for name in ('apps/3dprint/web/vendor/model3d-app.js','apps/3dprint/web/vendor/model3d-manifold.wasm','apps/3dprint/web/assets/licenses/model3d/three-LICENSE.txt'):
            path=self.root/name;old=path.read_bytes();path.write_bytes(b'modified')
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'model3d_.*hash_mismatch'):gate.validate_model3d_assets(self.root,source_root=self.root)
            path.write_bytes(old)
    def test_stale_source_or_unrecorded_local_source_cannot_be_shipped(self):
        for name in ('apps/3dprint/web/model3d/app.js', 'sdk/web/stl-validation.mjs'):
            path=self.root/name;path.write_bytes(b'new source not rebuilt')
            with self.subTest(source=name), self.assertRaisesRegex(ValueError,'source_hash_mismatch'):
                gate.validate_model3d_assets(self.root,source_root=self.root)
            path.write_bytes(b'synthetic reviewed input')
        path=self.root/'apps/3dprint/web/model3d/new-worker.js';path.write_bytes(b'new unbuilt worker')
        with self.assertRaisesRegex(ValueError,'source_receipt_incomplete'):gate.validate_model3d_assets(self.root,source_root=self.root)
    def test_missing_bundle_and_missing_font_are_rejected(self):
        path=self.root/'apps/3dprint/web/assets/model3d/Metrophobic-Regular.ttf';path.unlink()
        with self.assertRaisesRegex(ValueError,'font_hash_mismatch'):gate.validate_model3d_assets(self.root)
        self.manifest.unlink()
        with self.assertRaisesRegex(ValueError,'build_receipt_required'):gate.validate_model3d_assets(self.root)

    def test_catalogue_checks_every_offline_font_and_rejects_an_incomplete_receipt(self):
        web = self.manifest.parent.parent
        asset = 'apps/3dprint/web/assets/model3d/'
        provenance = ROOT / (asset + 'font-provenance.json')
        copied = web / 'assets/model3d/font-provenance.json'
        shutil.copyfile(provenance, copied)
        source = json.loads(provenance.read_text(encoding='utf-8'))
        value = json.loads(self.manifest.read_text(encoding='utf-8'))
        value['text'] = {'input_sha256': {asset + copied.name: hashlib.sha256(copied.read_bytes()).hexdigest(),
                                         asset + 'Metrophobic-Regular.ttf': source['font_sha256']},
                         'fonts': {'mkv-sans': {'file': 'Metrophobic-Regular.ttf', 'sha256': source['font_sha256'],
                                               'license': 'Metrophobic-OFL.txt'}}}
        value['license_sha256']['Metrophobic-OFL.txt'] = source['license_sha256']
        shutil.copyfile(ROOT / (asset + 'Metrophobic-OFL.txt'), web / 'assets/licenses/model3d/Metrophobic-OFL.txt')
        for entry in source['catalog']['fonts']:
            filename = entry['source_path'].rsplit('/', 1)[-1]
            license_name = entry['license_file']
            value['text']['input_sha256'][asset + filename] = entry['font_sha256']
            value['text']['fonts'][entry['key']] = {'file': filename, 'sha256': entry['font_sha256'], 'license': license_name}
            value['license_sha256'][license_name] = entry['license_sha256']
            shutil.copyfile(ROOT / (asset + filename), web / 'assets/model3d' / filename)
            shutil.copyfile(ROOT / 'apps/3dprint/web/assets/licenses/model3d' / license_name,
                            web / 'assets/licenses/model3d' / license_name)
        self.manifest.write_text(json.dumps(value), encoding='utf-8')
        gate.validate_model3d_assets(self.root)

        font = value['text']['fonts']['titan-one']['file']
        (web / 'assets/model3d' / font).unlink()
        with self.assertRaisesRegex(ValueError, 'model3d_font_hash_mismatch'):
            gate.validate_model3d_assets(self.root)
        shutil.copyfile(ROOT / (asset + font), web / 'assets/model3d' / font)

        del value['text']['fonts']['titan-one']
        del value['text']['input_sha256'][asset + font]
        self.manifest.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'model3d_font_receipt_incomplete'):
            gate.validate_model3d_assets(self.root)
if __name__=='__main__':unittest.main()
