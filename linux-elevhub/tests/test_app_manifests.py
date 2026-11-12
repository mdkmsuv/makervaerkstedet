import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from app_manifests import load_manifests, validate_manifest

def manifest(id='laser'):
    return {'schema': 1, 'id': id, 'navn': 'Laser', 'version': '2.0.0', 'start': 'http://127.0.0.1:8795/', 'offline': True, 'aflevering': 'laser-submit', 'kraever_internet': False, 'licens': 'copyright'}

class ManifestValidation(unittest.TestCase):
    def test_valid_and_expected_directory(self):
        self.assertEqual(validate_manifest(manifest(), 'laser'), manifest())
        with self.assertRaises(ValueError): validate_manifest(manifest(), 'folie')
        for value in (None, 'laser-submit', 'print-job'):
            self.assertEqual(validate_manifest(manifest() | {'aflevering': value})['aflevering'], value)

    def test_fields_types_and_lengths(self):
        for field, value in [('schema', True), ('schema', 2), ('id', 'A/b'), ('navn', ''), ('navn', 'x' * 257), ('version', 1), ('offline', 1), ('kraever_internet', 0), ('licens', None), ('aflevering', 'other')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): validate_manifest(manifest() | {field: value})
        with self.assertRaises(ValueError): validate_manifest(manifest() | {'unknown': 1})
        value = manifest(); del value['navn']
        with self.assertRaises(ValueError): validate_manifest(value)

    def test_start_url_security(self):
        for url in ('https://127.0.0.1:8795/', 'http://localhost:8795/', 'http://example.com:8795/', 'http://127.0.0.1/', 'http://127.0.0.1:80/', 'http://127.0.0.1:65536/', 'http://user@127.0.0.1:8795/', 'http://127.0.0.1:8795/?x=1', 'http://127.0.0.1:8795/#x', 'http://127.0.0.1:8795/a\n'):
            with self.subTest(url=url), self.assertRaises(ValueError): validate_manifest(manifest() | {'start': url})
        for url in ('http://127.0.0.1:1024/', 'http://127.0.0.1:65535/editor.html'):
            self.assertEqual(validate_manifest(manifest() | {'start': url})['start'], url)

class ManifestLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'apps'; self.root.mkdir()
    def put(self, id, content):
        folder = self.root / id; folder.mkdir(exist_ok=True)
        (folder / 'app.json').write_text(content, encoding='utf-8')
    def test_sorted_reload_after_removal_and_missing_root(self):
        self.put('laser', json.dumps(manifest('laser'))); self.put('folie', json.dumps(manifest('folie')))
        self.assertEqual([a['id'] for a in load_manifests(self.root)], ['folie', 'laser'])
        (self.root / 'laser/app.json').unlink()
        self.assertEqual([a['id'] for a in load_manifests(self.root)], ['folie'])
        self.assertEqual(load_manifests(self.root / 'missing'), [])
    def test_bad_json_duplicate_keys_oversize_or_id_mismatch(self):
        for value in ('{', '{"id":"laser","id":"folie"}', ' ' * 16385, json.dumps(manifest('wrong'))):
            self.put('laser', value)
            with self.subTest(value=value[:50]), self.assertRaises(ValueError): load_manifests(self.root)
    def test_symlinks_are_rejected(self):
        self.put('laser', json.dumps(manifest())); (self.root / 'laser/app.json').unlink()
        (self.root / 'laser/app.json').symlink_to(Path(self.tmp.name) / 'outside')
        with self.assertRaises(ValueError): load_manifests(self.root)
        (self.root / 'laser/app.json').unlink(); (self.root / 'laser').rmdir()
        (self.root / 'laser').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): load_manifests(self.root)
        link = Path(self.tmp.name) / 'linked'; link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): load_manifests(link)

if __name__ == '__main__': unittest.main()
