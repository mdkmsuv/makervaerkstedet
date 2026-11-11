"""Read-only ownership proof for interrupted copies and generation removal."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'provision.py'
spec = importlib.util.spec_from_file_location('initial_install_tree', SOURCE)
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class InitialInstallTreeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        self.runtime = self.root / 'installed'
        self.data = self.root / 'data'
        for path in (self.package, self.runtime, self.data):
            path.mkdir()
        for name, raw in {'runtime/python.exe': b'original runtime bytes',
                          'app/generation_runtime.py': b'original launcher bytes',
                          'server/provision.py': b'original administrator bytes',
                          'elevhub-package.json': b'{"localAiIncluded":false}'}.items():
            path = self.package / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
        files = {p.relative_to(self.package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.package.rglob('*') if p.is_file()}
        (self.package / 'integrity.json').write_text(json.dumps({'algorithm': 'sha256', 'files': files}))
        self.owner = {'schemaVersion': 1, 'product': 'ElevhubServer',
                      'ownerId': '11111111-1111-4111-8111-111111111111', 'state': 'installing',
                      'taskName': 'ElevhubServer', 'installRoot': str(self.runtime),
                      'dataRoot': str(self.data), 'printBackendPort': 18765,
                      'packageManifestSha256': hashlib.sha256((self.package / 'integrity.json').read_bytes()).hexdigest()}
        (self.data / 'private.key').write_bytes(b'preserved private key')
        self.save_owner()

    def tearDown(self):
        self.temp.cleanup()

    def save_owner(self):
        (self.data / 'installation-owner.json').write_text(json.dumps(self.owner))

    def check(self, **kwargs):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        try:
            provision.check_initial_install_tree(self.package, self.runtime, self.data, **kwargs)
        finally:
            after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def generation(self):
        self.owner.update(generationLayout=True, startupProtocol='generation-v1')
        (self.package / 'makerspace-release.json').write_text('{"release":"test-only-anchor"}')
        self.owner['releaseManifestSha256'] = hashlib.sha256((self.package / 'makerspace-release.json').read_bytes()).hexdigest()
        self.save_owner()
        shutil.copytree(self.package, self.runtime / 'program')
        for name in ('runtime/python.exe', 'app/generation_runtime.py', 'server/provision.py'):
            target = self.runtime / name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(self.package / name, target)
        (self.runtime / 'control').mkdir()
        self.selection = {'format': 1, 'installation_id': '11111111111141118111111111111111',
                          'revision': 1, 'runtime_root': str(self.runtime),
                          'program_root': str(self.runtime / 'program'), 'data_root': str(self.data),
                          'manifest_sha256': self.owner['releaseManifestSha256']}
        (self.runtime / 'control/active-generation.json').write_text(json.dumps(self.selection))
        (self.runtime / 'control/generation.lock').write_bytes(b'1')

    def test_each_partial_copy_is_owned_but_unknown_or_modified_bytes_are_retained(self):
        path = self.runtime / 'runtime/python.exe'
        path.parent.mkdir()
        original = (self.package / 'runtime/python.exe').read_bytes()
        for length in range(len(original) + 1):
            path.write_bytes(original[:length])
            self.check()
        for raw in (b'foreign key', original + b'extra'):
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'programfil'):
                self.check()
        path.write_bytes(original)
        (self.runtime / 'private.key').write_bytes(b'do not delete')
        with self.assertRaisesRegex(ValueError, 'Ukendt fil'):
            self.check()

    def test_empty_foreign_data_directory_and_junction_are_rejected(self):
        unknown = self.runtime / 'student-files'
        unknown.mkdir()
        with self.assertRaisesRegex(ValueError, 'Ukendt mappe'):
            self.check()
        unknown.rmdir()
        link = self.runtime / 'runtime'
        try:
            if __import__('os').name == 'nt':
                import subprocess
                result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                                         'New-Item -ItemType Junction -Path $env:LINK -Target $env:TARGET | Out-Null'],
                                        env={**__import__('os').environ, 'LINK': str(link), 'TARGET': str(self.data)},
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                link.symlink_to(self.data, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'omdirigerede'):
                provision.check_initial_install_tree(self.package, self.runtime, self.data)
        finally:
            if link.exists():
                link.unlink() if link.is_symlink() else link.rmdir()

    def test_modified_original_package_or_owner_cannot_authorize_delete(self):
        (self.package / 'runtime/python.exe').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'pakke er ændret'):
            self.check()
        self.owner['installRoot'] = str(self.root / 'wrong')
        self.save_owner()
        with self.assertRaisesRegex(ValueError, 'ejet førstegangsinstallation'):
            self.check()

    def test_complete_first_generation_accepts_selected_ports_and_ai_marker(self):
        self.generation()
        self.owner['state'] = 'installed'
        self.save_owner()
        (self.runtime / 'installation.json').write_text(json.dumps(
            {'data_root': str(self.data), 'port': 18765, 'autostart': 'Boot', 'ai_enabled': False}))
        self.check(uninstall=True)
        (self.runtime / 'installation.json').write_text(json.dumps(
            {'data_root': str(self.data), 'port': 18765, 'autostart': 'Boot', 'ai_enabled': True}))
        with self.assertRaisesRegex(ValueError, 'installationsmetadata'):
            self.check(uninstall=True)

    def test_later_selection_relocated_data_and_foreign_transition_are_retained(self):
        self.generation()
        for bad in ({'revision': 2}, {'data_root': str(self.root / 'retained-data')},
                    {'program_root': str(self.root / 'new-release')}, {'manifest_sha256': 'b' * 64}):
            (self.runtime / 'control/active-generation.json').write_text(json.dumps({**self.selection, **bad}))
            with self.assertRaisesRegex(ValueError, 'installationsmetadata'):
                self.check()
        (self.runtime / 'control/active-generation.json').write_text(json.dumps(self.selection))
        (self.runtime / 'control/selection-transition.json').write_text(json.dumps(
            {'format': 1, 'operation': 'update', 'installation_id': self.selection['installation_id']}))
        with self.assertRaisesRegex(ValueError, 'installationsmetadata'):
            self.check()

    def test_initial_transition_and_pending_selection_are_recoverable_only_for_install(self):
        self.generation()
        (self.runtime / 'control/active-generation.json').rename(self.runtime / 'control/active-generation.json.pending')
        (self.runtime / 'control/selection-transition.json').write_text(json.dumps(
            {'format': 1, 'operation': 'initial_install', 'installation_id': self.selection['installation_id']}))
        self.check()
        self.owner['state'] = 'installed'
        self.save_owner()
        with self.assertRaisesRegex(ValueError, 'fuldført'):
            self.check(uninstall=True)

    def test_torn_control_metadata_does_not_authorize_removal(self):
        self.generation()
        (self.runtime / 'control/active-generation.json').write_bytes(b'{"format":')
        with self.assertRaises(ValueError):
            self.check()


if __name__ == '__main__':
    unittest.main()
