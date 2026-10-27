"""Boundary checks added after reviewing the source exporter, no publishing."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import public_source as source
from tests import test_public_source_export as fixture


class PublicSourceGuardTests(unittest.TestCase):
    def test_export_requires_both_credits(self):
        with tempfile.TemporaryDirectory() as folder:
            root, policy = fixture._tree(Path(folder))
            (root / 'CREDITS.md').write_text(source.CREDIT, encoding='utf-8')
            with self.assertRaisesRegex(source.ExportError, 'Mathias Kusk Damgaard'):
                source.audit(root, policy)

    def test_public_ui_rejects_school_defaults_but_keeps_attribution(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root, policy = fixture._tree(parent)
            policy['includeExtensions'] += ['.html', '.css', '.js']
            cases = (
                ('page.html', '<title>Klostermarksskolen</title>'),
                ('page.html', '<img alt="Klostermarksskolen">'),
                ('page.html', '<img src="/assets/klostermarksskolen-logo.png">'),
                ('theme.css', ':root { --brand: #b4121a; }'),
                ('theme.js', 'const logo = "/assets/klostermarksskolen-logo.png";'),
            )
            for name, text in cases:
                candidate = root / 'src' / name
                candidate.write_text(text, encoding='utf-8')
                with self.subTest(name=name, text=text), self.assertRaises(source.ExportError):
                    source.export_source(root, policy, str(parent / 'release'))
                self.assertFalse((parent / 'release').exists())
                candidate.unlink()
            (root / 'src' / 'page.html').write_text(
                '<title>Makerværkstedet</title><span data-school-name>Makerværkstedet</span>'
                '<footer>Udviklet af Mathias Kusk Damgaard · Kreditering: Klostermarksskolen.</footer>',
                encoding='utf-8')
            self.assertTrue(source.export_source(root, policy, str(parent / 'release'))['files'])

    def test_school_policy_cannot_replace_or_omit_original_credit(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root, policy = fixture._tree(parent)
            cases = (dict(policy, credit='Kreditering: Eksempelskolen.'),
                     dict(policy, explicitFiles=['AGENTS.md']))
            for modified in cases:
                with self.subTest(policy=modified), self.assertRaisesRegex(source.ExportError, 'Klostermarksskolen'):
                    source.export_source(root, modified, str(parent / 'release'))
                self.assertFalse((parent / 'release').exists())
            (root / 'CREDITS.md').write_text('Kreditering: Eksempelskolen.', encoding='utf-8')
            with self.assertRaisesRegex(source.ExportError, 'Klostermarksskolen'):
                source.audit(root, policy)

    def test_unknown_empty_directory_in_failed_stage_prevents_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root, policy = fixture._tree(parent)
            with patch.object(source, '_publish', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                source.export_source(root, policy, str(parent / 'release'))
            stage = next(parent.glob('.release.public-source-*'))
            personal = stage / 'src' / 'personal-empty-folder'
            personal.mkdir()
            with self.assertRaisesRegex(source.ExportError, 'ukendt mappe'):
                source.cleanup_staging(str(stage), root)
            self.assertTrue(personal.is_dir())
            personal.rmdir()
            self.assertTrue(source.cleanup_staging(str(stage), root)['cleaned'])

    def test_short_or_fixture_looking_printer_credentials_cannot_be_exported(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root, policy = fixture._tree(parent)
            configuration = root / 'src' / 'printer-template.json'
            for field, value in (('access_code', '12345678'), ('access_code', 'fixture1'),
                                 ('access_code', 12345678), ('password', 'abc123'),
                                 ('private_key', 'dummy-key'), ('admin_private_key', False)):
                with self.subTest(field=field, value_type=type(value).__name__):
                    configuration.write_text(json.dumps({'printers': [{field: value}]}), encoding='utf-8')
                    output = parent / 'release'
                    with self.assertRaisesRegex(source.ExportError, 'privat adgangsfelt'):
                        source.export_source(root, policy, str(output))
                    self.assertFalse(output.exists())
            configuration.write_text(json.dumps({'access_code': '', 'password': None}), encoding='utf-8')
            self.assertTrue(source.audit(root, policy)['files'])

    def test_target_created_during_export_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root, policy = fixture._tree(parent)
            output = parent / 'release'
            original = source._publish
            def race(stage, target):
                target.mkdir()
                return original(stage, target)
            with patch.object(source, '_publish', side_effect=race), self.assertRaises(OSError):
                source.export_source(root, policy, str(output))
            self.assertTrue(output.is_dir())
            self.assertEqual(list(output.iterdir()), [])
            stage = next(parent.glob('.release.public-source-*'))
            self.assertTrue(source.cleanup_staging(str(stage), root)['cleaned'])

    def test_unknown_user_file_in_failed_stage_prevents_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root, policy = fixture._tree(parent)
            with patch.object(source, '_publish', side_effect=OSError('interrupted')), self.assertRaises(OSError):
                source.export_source(root, policy, str(parent / 'release'))
            stage = next(parent.glob('.release.public-source-*'))
            personal = stage / 'personal.txt'
            personal.write_text('Preserve me')
            with self.assertRaisesRegex(source.ExportError, 'ukendt fil'):
                source.cleanup_staging(str(stage), root)
            self.assertEqual(personal.read_text(), 'Preserve me')

    def test_changed_sources_after_copy_cannot_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            root, policy = fixture._tree(parent)
            copy = source._copy_checked
            def copy_and_change(item, stage, limit):
                copy(item, stage, limit)
                if item.relative == 'src/main.py':
                    item.path.write_text("print('changed after copy')")
            with patch.object(source, '_copy_checked', side_effect=copy_and_change), self.assertRaisesRegex(source.ExportError, 'ændrede sig'):
                source.export_source(root, policy, str(parent / 'release'))
            self.assertFalse((parent / 'release').exists())

    def test_encrypted_private_key_is_rejected_and_archives_are_pinned(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            key = parent / 'notice.txt'
            with self.assertRaisesRegex(source.ExportError, 'Privat PEM'):
                source._check_content(key, b'-----BEGIN ENCRYPTED PRIVATE KEY-----\n' + b'A1b2C3d4'*12 + b'\n-----END ENCRYPTED PRIVATE KEY-----\n')
            for name in source.SOURCE_ARCHIVES:
                archive = parent / name
                archive.write_bytes(b'\x00changed archive')
                with self.assertRaisesRegex(source.ExportError, 'Debian-pin'):
                    source._read_source(archive, 1024)

    def test_required_source_builders_and_complete_notices_are_selected(self):
        root = source._repo_root()
        paths = {item.relative for item in source._collect(root, source._load_policy(root))}
        for relative in ('LICENSE', 'LICENSE-SCOPE.md', 'requirements-dev.txt',
                         'scripts/school_server_update/update.py',
                         'scripts/school_server_update/Invoke-SchoolUpdate.ps1',
                         'scripts/school_server_update/Repair-SchoolRuntimeAccess.ps1',
                         'windows-elevhub/build_packages.py', 'scripts/package_documentation.py',
                         'third_party/boxes.py/boxes/scripts/boxes_main.py',
                         'linux-elevhub/kiosk-window-guard/matchbox-close-guard.patch',
                         'linux-elevhub/cadoodle/kloster/licenses/source-manifest.json', 'web/assets/Three-MIT.txt'):
            self.assertIn(relative, paths)
        for name in source.SOURCE_ARCHIVES:
            self.assertIn('linux-elevhub/kiosk-window-guard/matchbox-source/' + name, paths)
