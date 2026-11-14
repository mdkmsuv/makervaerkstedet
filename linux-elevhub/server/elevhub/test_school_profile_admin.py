"""Synthetic profile publication tests, including the real public reader."""
import base64
import builtins
from contextlib import contextmanager
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zlib

from PIL import Image, PngImagePlugin

SPEC = importlib.util.spec_from_file_location(
    'canonical_profile_reader', Path(__file__).resolve().parents[3] / 'app/school_appearance.py')
READER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READER)


def png(colour=(255, 0, 0, 120), size=(4, 3)):
    stream = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('private', 'synthetic metadata to remove')
    Image.new('RGBA', size, colour).save(stream, format='PNG', pnginfo=metadata)
    return base64.b64encode(stream.getvalue()).decode('ascii')


class ProfileTests(unittest.TestCase):
    def setUp(self):
        import school_profile_admin
        self.module = school_profile_admin
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.admin = self.module.SchoolProfileAdmin(self.root)

    def body(self, revision=0, **changes):
        profile = dict(school_name='Eksempelskolen', colour='#eeee00', font='standard',
                       logo_mode='replace', logo_png=png())
        profile.update(changes)
        return dict(command_id=str(uuid.uuid4()), expected_revision=revision, profile=profile)

    def test_selected_font_is_declarative_for_offline_start_page(self):
        self.admin.save(self.body(font='manrope'))
        self.assertEqual(READER.school_appearance(self.root).get('font'), 'manrope')

    def manifest(self):
        return (self.root / 'local-appearance/manifest.json').read_bytes()

    def assert_error(self, body, code, status=400):
        with self.assertRaises(self.module.SchoolProfileError) as caught:
            self.admin.save(body)
        self.assertEqual((caught.exception.code, caught.exception.status), (code, status))

    def test_save_is_readable_by_public_reader_and_strips_logo_metadata(self):
        receipt = self.admin.save(self.body())
        self.assertEqual(receipt['revision'], 1)
        appearance = READER.school_appearance(self.root)
        self.assertEqual(appearance['school_name'], 'Eksempelskolen')
        self.assertNotEqual(appearance['brand'], '#eeee00')
        self.assertNotEqual(appearance['brand_dark'], appearance['brand'])
        self.assertEqual(self.admin.overview()['profile']['colour'], '#eeee00')
        css, mime = READER.school_appearance_asset(self.root, 'website.css')
        self.assertEqual(mime, 'text/css; charset=utf-8')
        self.assertIn(b'--school-accent:#eeee00', css)
        logo, mime = READER.school_appearance_asset(self.root, 'logo')
        with Image.open(io.BytesIO(logo)) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 120))
            self.assertNotIn('private', image.info)
        self.assertEqual(mime, 'image/png')
        self.assertTrue(self.admin.overview()['managed'])

    def test_retry_after_lost_response_is_idempotent_before_revision_check(self):
        body = self.body()
        first = self.admin.save(body)
        self.admin.save(self.body(1, school_name='Nyt navn'))
        self.assertEqual(self.module.SchoolProfileAdmin(self.root).save(body), first)
        body['profile']['school_name'] = 'Ændret kommando'
        self.assert_error(body, 'command_id_reused', 409)
        self.assertEqual(self.admin.overview()['revision'], 2)

    def test_stale_revision_and_strict_fields_preserve_existing_profile(self):
        self.admin.save(self.body())
        before = self.manifest()
        self.assert_error(self.body(), 'stale_profile_revision', 409)
        wrong = self.body(1)
        wrong['arbitrary'] = 'ignored?'
        self.assert_error(wrong, 'invalid_profile_fields')
        self.assertEqual(self.manifest(), before)

    def test_invalid_values_never_change_previous_profile(self):
        self.admin.save(self.body())
        before = self.manifest()
        for change, code in [
                ({'school_name': ''}, 'invalid_school_name'),
                ({'school_name': 'x' * 121}, 'invalid_school_name'),
                ({'school_name': 'Skole\nnavn'}, 'invalid_school_name'),
                ({'school_name': 'Skole\u0085navn'}, 'invalid_school_name'),
                ({'colour': 'red'}, 'invalid_profile_colour'),
                ({'font': 'url(https://example.invalid)'}, 'invalid_profile_font'),
                ({'logo_png': base64.b64encode(b'not PNG').decode()}, 'invalid_logo_png'),
                ({'logo_png': '%%%%'}, 'invalid_logo_png'),
                ({'logo_png': png(size=(2049, 1))}, 'logo_dimensions_exceeded'),
                ({'logo_mode': 'keep'}, 'invalid_logo_mode'),
                ({'logo_mode': 'remove'}, 'invalid_logo_mode')]:
            with self.subTest(change=list(change)):
                self.assert_error(self.body(1, **change), code)
                self.assertEqual(self.manifest(), before)

    def test_logo_keep_remove_and_replace(self):
        self.admin.save(self.body())
        original = READER.school_appearance_asset(self.root, 'logo')[0]
        self.admin.save(self.body(1, logo_mode='keep', logo_png=None))
        self.assertEqual(READER.school_appearance_asset(self.root, 'logo')[0], original)
        self.admin.save(self.body(2, logo_mode='remove', logo_png=None))
        self.assertIsNone(READER.school_appearance_asset(self.root, 'logo'))
        self.admin.save(self.body(3, logo_png=png((0, 0, 255, 255))))
        self.assertNotEqual(READER.school_appearance_asset(self.root, 'logo')[0], original)

    def test_bundled_fonts_are_hash_verified_assets_and_default_does_not_override_fonts(self):
        self.admin.save(self.body(font='manrope'))
        css = READER.school_appearance_asset(self.root, 'website.css')[0]
        self.assertIn(b'@font-face', css)
        self.assertIn(b'--f-body', css)
        manifest = json.loads(self.manifest())
        self.assertEqual(len(manifest['style']['static']), 1)
        font_url = next(iter(manifest['style']['static']))
        self.assertTrue(READER.school_appearance_asset(self.root, font_url)[0])
        self.admin.save(self.body(1, font='standard'))
        self.assertNotIn(b'font-family', READER.school_appearance_asset(self.root, 'website.css')[0])
        self.assertEqual(json.loads(self.manifest())['style'].get('static', {}), {})

    def test_missing_font_does_not_fetch_or_change_profile(self):
        self.admin = self.module.SchoolProfileAdmin(self.root, font_files={})
        self.assert_error(self.body(font='manrope'), 'profile_font_unavailable')
        self.assertFalse((self.root / 'local-appearance/manifest.json').exists())

    def test_missing_image_decoder_has_a_bounded_unavailable_error(self):
        body = self.body()
        original = builtins.__import__
        def missing_pillow(name, *args, **kwargs):
            if name == 'PIL':
                raise ModuleNotFoundError('synthetic missing optional decoder')
            return original(name, *args, **kwargs)
        with patch.object(builtins, '__import__', missing_pillow):
            self.assert_error(body, 'logo_processing_unavailable', 503)
        self.assertFalse((self.root / 'local-appearance/manifest.json').exists())

    def test_animated_png_and_oversized_upload_are_rejected(self):
        stream = io.BytesIO()
        Image.new('RGBA', (2, 2), 'red').save(
            stream, format='PNG', save_all=True,
            append_images=[Image.new('RGBA', (2, 2), 'blue')], duration=20, loop=0)
        self.assert_error(self.body(logo_png=base64.b64encode(stream.getvalue()).decode()),
                          'invalid_logo_png')
        # APNG's animation declaration is rejected even with just one frame.
        raw = base64.b64decode(png(size=(2, 2)))
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        animation = chunk(b'acTL', struct.pack('>II', 1, 0))
        animation += chunk(b'fcTL', struct.pack('>IIIIIHHBB', 0, 2, 2, 0, 0, 20, 1000, 0, 0))
        one_frame_apng = raw[:33] + animation + raw[33:]
        self.assert_error(self.body(logo_png=base64.b64encode(one_frame_apng).decode()),
                          'invalid_logo_png')
        self.assert_error(self.body(logo_png='A' * (3 * 1024 * 1024)), 'logo_upload_too_large')

    def test_interrupted_asset_write_can_retry_without_corrupting_addressed_asset(self):
        self.admin.save(self.body())
        before = self.manifest()
        body = self.body(1, colour='#123456')
        with patch.object(self.module.os, 'fsync', side_effect=OSError('synthetic disk error')):
            self.assert_error(body, 'profile_write_failed', 500)
        self.assertEqual(self.manifest(), before)
        self.assertEqual(self.admin.save(body)['revision'], 2)
        self.assertEqual(READER.school_appearance(self.root)['brand'], '#123456')

    def test_partial_asset_write_never_occupies_its_content_address(self):
        self.admin.save(self.body())
        before = self.manifest()
        body = self.body(1, colour='#123456')
        original = Path.open
        @contextmanager
        def partial_write(path, *args, **kwargs):
            with original(path, *args, **kwargs) as stream:
                writing_asset = args and args[0] == 'xb' and (
                    path.suffix == '.css' or path.name.startswith('.asset-'))
                if writing_asset:
                    class PartialStream:
                        def write(self, content):
                            stream.write(content[:len(content) // 2])
                            stream.flush()
                            raise OSError('synthetic interruption after partial write')
                    yield PartialStream()
                else:
                    yield stream
        with patch.object(Path, 'open', partial_write):
            self.assert_error(body, 'profile_write_failed', 500)
        self.assertEqual(self.manifest(), before)
        self.assertEqual(self.admin.save(body)['revision'], 2)
        self.assertIsNotNone(READER.school_appearance_asset(self.root, 'website.css'))

    def test_external_manifest_edit_invalidates_an_already_seen_revision(self):
        self.admin.save(self.body())
        external = json.loads(self.manifest())
        external['school_name'] = 'Ekstern administrators ændring'
        (self.root / 'local-appearance/manifest.json').write_text(json.dumps(external), encoding='utf-8')
        before = self.manifest()
        self.assert_error(self.body(1), 'stale_profile_revision', 409)
        self.assertEqual(self.manifest(), before)
        state = self.admin.overview()
        self.assertEqual(state['revision'], 2)
        self.assertFalse(state['managed'])
        self.assertEqual(state['profile']['school_name'], 'Ekstern administrators ændring')

    def test_linked_or_irregular_font_source_is_unavailable(self):
        self.admin = self.module.SchoolProfileAdmin(self.root, font_files={'manrope': self.root})
        self.assert_error(self.body(font='manrope'), 'profile_font_unavailable')
        other = self.root / 'linked.ttf'
        try:
            other.symlink_to(Path(self.module.__file__).parent / 'web/assets/Manrope.ttf')
        except OSError:
            self.skipTest('Host cannot create symlinks')
        self.admin = self.module.SchoolProfileAdmin(self.root, font_files={'manrope': other})
        self.assert_error(self.body(font='manrope'), 'profile_font_unavailable')

    def test_recovery_does_not_overwrite_an_unrelated_external_profile(self):
        self.admin.save(self.body())
        original = self.module.os.replace
        def crash_before_replace(source, target):
            if Path(target).name == 'manifest.json':
                raise SystemExit('synthetic process crash before publish')
            return original(source, target)
        with patch.object(self.module.os, 'replace', side_effect=crash_before_replace):
            with self.assertRaises(SystemExit):
                self.admin.save(self.body(1, school_name='Ikke publiceret'))
        external = json.loads(self.manifest())
        external['school_name'] = 'Ekstern profil'
        (self.root / 'local-appearance/manifest.json').write_text(json.dumps(external), encoding='utf-8')
        before = self.manifest()
        self.assert_error(self.body(1), 'profile_recovery_conflict', 409)
        self.assertEqual(self.manifest(), before)

    def test_legacy_assets_remain_until_successful_explicit_save(self):
        base = self.root / 'local-appearance'
        base.mkdir()
        (base / 'legacy.css').write_bytes(b'body{color:purple}')
        import hashlib
        legacy = dict(schemaVersion=1, school_name='Gammel skole', theme='green',
                      style=dict(website_css='legacy.css', website_css_sha256=hashlib.sha256(
                          b'body{color:purple}').hexdigest()))
        (base / 'manifest.json').write_text(json.dumps(legacy), encoding='utf-8')
        before = self.manifest()
        state = self.admin.overview()
        self.assertEqual(state['state'], 'legacy')
        self.assertEqual(state['profile']['school_name'], 'Gammel skole')
        self.assert_error(self.body(school_name=''), 'invalid_school_name')
        self.assertEqual(self.manifest(), before)
        self.admin.save(self.body(logo_mode='keep', logo_png=None))
        self.assertEqual((base / 'legacy.css').read_bytes(), b'body{color:purple}')

    def test_write_failure_before_publish_preserves_old_bytes_and_can_retry(self):
        self.admin.save(self.body())
        before = self.manifest()
        body = self.body(1, school_name='Nyt navn')
        original = self.module.os.replace
        def fail_manifest(source, target):
            if Path(target).name == 'manifest.json':
                raise OSError('synthetic full disk')
            return original(source, target)
        with patch.object(self.module.os, 'replace', side_effect=fail_manifest):
            self.assert_error(body, 'profile_write_failed', 500)
        self.assertEqual(self.manifest(), before)
        reopened = self.module.SchoolProfileAdmin(self.root)
        self.assertEqual(reopened.overview()['revision'], 1)
        self.assertEqual(reopened.save(body)['revision'], 2)

    def test_crash_after_manifest_publish_recovers_receipt_without_second_revision(self):
        self.admin.save(self.body())
        body = self.body(1, school_name='Efter afbrudt svar')
        original = self.module.os.replace
        def crash_after_replace(source, target):
            result = original(source, target)
            if Path(target).name == 'manifest.json':
                raise SystemExit('synthetic process crash')
            return result
        with patch.object(self.module.os, 'replace', side_effect=crash_after_replace):
            with self.assertRaises(SystemExit):
                self.admin.save(body)
        reopened = self.module.SchoolProfileAdmin(self.root)
        self.assertEqual(reopened.overview()['revision'], 2)
        self.assertEqual(reopened.save(body)['revision'], 2)
        self.assertEqual(READER.school_appearance(self.root)['school_name'], 'Efter afbrudt svar')

    def test_linked_profile_directory_is_rejected(self):
        other = self.root / 'other'
        other.mkdir()
        try:
            (self.root / 'local-appearance').symlink_to(other, target_is_directory=True)
        except OSError:
            self.skipTest('Host cannot create symlinks')
        self.assert_error(self.body(), 'linked_profile_path')
        self.assertEqual(list(other.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
