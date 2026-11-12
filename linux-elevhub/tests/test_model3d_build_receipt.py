"""A bundle receipt must bind imported local code and the runtime font."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging'))
from prepare_inputs import MODEL3D_OUTPUTS, validate_model3d_bundle


class Model3dReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.font = 'web/assets/model3d/Metrophobic-Regular.ttf'
        files = {'web/model3d/app.js': b"import {validate} from '../stl-validation.mjs'; validate();",
                 'web/stl-validation.mjs': b'export function validate() { return true; }',
                 self.font: b'original font'}
        for name, data in files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.receipt = {'schema': 1,
                        'source_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                        'text': {'input_sha256': {self.font: hashlib.sha256(files[self.font]).hexdigest()}},
                        'outputs': {}}
        for name in MODEL3D_OUTPUTS:
            target = self.root / 'web/vendor' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'built output')
            self.receipt['outputs'][name] = {'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'size': target.stat().st_size}
        self.write_receipt()

    def write_receipt(self):
        (self.root / 'web/vendor/model3d-vendor-manifest.json').write_text(json.dumps(self.receipt), encoding='utf-8')

    def test_complete_unchanged_receipt_is_accepted(self):
        validate_model3d_bundle(self.root)

    def test_imported_source_omitted_from_receipt_is_rejected(self):
        del self.receipt['source_sha256']['web/stl-validation.mjs']
        (self.root / 'web/stl-validation.mjs').write_text('export function validate() { return false; }')
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, 'model3d_source_receipt_incomplete'):
            validate_model3d_bundle(self.root)

    def test_runtime_font_pin_is_checked_even_when_missing_from_source_hashes(self):
        del self.receipt['source_sha256'][self.font]
        (self.root / self.font).write_bytes(b'changed font')
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, 'model3d_runtime_input_hash_mismatch'):
            validate_model3d_bundle(self.root)


if __name__ == '__main__':
    unittest.main()
