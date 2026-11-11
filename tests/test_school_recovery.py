"""Replacement host recovery with real held databases and preserved keys.

The final Windows integrations are not executed. Temporary paths stand in
for the newly secured installer directories; release uses actual package,
TLS, copy, inventory and database code.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import unittest
import uuid
from contextlib import closing
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from unittest.mock import patch

from app import school_recovery as recovery, installation_backup as backup
from app.backup_guard import assert_not_restored
from tests import test_generation_install as program_fixture
from tests import test_installation_backup as data_fixture


@unittest.skipUnless(os.name == 'nt' and program_fixture.ARCHIVE.is_file(), 'requires cached Windows embedded runtime')
class SchoolRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.program = program_fixture.GenerationInstallTests()
        self.program.setUp()
        self.addCleanup(self.program.doCleanups)
        self.data = data_fixture.InstallationBackupTests()
        self.data.setUp()
        self.addCleanup(self.data.tearDown)
        self.root = self.program.root
        self.package = self.program.program
        shutil.copy2(program_fixture.ROOT / 'windows-elevhub/server/provision.py', self.package / 'server/provision.py')
        integrity = {'algorithm': 'sha256', 'files': {p.relative_to(self.package).as_posix():
                     hashlib.sha256(p.read_bytes()).hexdigest() for p in self.package.rglob('*')
                     if p.is_file() and p.name not in ('integrity.json', 'makerspace-release.json')}}
        (self.package / 'integrity.json').write_text(json.dumps(integrity), encoding='utf-8')
        manifest_path = self.package / 'makerspace-release.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files'] = {p.relative_to(self.package).as_posix(): {'size': p.stat().st_size,
                             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                             for p in self.package.rglob('*') if p.is_file() and p != manifest_path}
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        owner = {'schemaVersion': 1, 'product': 'ElevhubServer', 'ownerId': str(uuid.UUID(self.program.installation_id)),
                 'taskName': 'ElevhubServer', 'state': 'installed', 'startupProtocol': 'generation-v1',
                 'generationLayout': True, 'installRoot': str(self.program.stable), 'dataRoot': str(self.data.server),
                 'roleIp': '192.0.2.10', 'roleNetwork': '192.0.2.0/24', 'studentSubnet': '192.0.2.0/24',
                 'teacherAddress': '127.0.0.1', 'teacherPort': 8780, 'printBackendPort': 8765,
                 'designBackendPort': 8766, 'teacherBackendPort': 8767, 'externalBackends': [],
                 'firewallNames': ['ElevhubServer-' + str(port) for port in (8770, 8771, 8772, 8773)],
                 'caThumbprint': None, 'packageManifestSha256': backup._hash(self.package / 'integrity.json'),
                 'releaseManifestSha256': backup._hash(manifest_path)}
        (self.data.server / 'installation-owner.json').write_text(json.dumps(owner), encoding='utf-8')
        _, _, provision = recovery._package(self.package, owner)
        for name, content in provision._tls_material(owner['roleIp']).items():
            (self.data.server / 'security' / name).write_text(content, encoding='utf-8')
        school = {'schemaVersion': 1, 'school_name': 'Eksempelskolen', 'role': 'vm', 'theme': 'green',
                  'server': owner['roleIp'], 'student_subnet': owner['studentSubnet']}
        enrollment = self.data.server / 'security/enrollment'
        (enrollment / 'operations').mkdir(parents=True)
        (enrollment / 'backup-protocol.json').write_text('{"protocol":1,"role":"enrollment"}')
        admin = self.data.server / 'admin-ssh'; admin.mkdir()
        ssh = ed25519.Ed25519PrivateKey.generate()
        self.ssh_private = ssh.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
        self.ssh_public = ssh.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        (admin / 'id_ed25519').write_bytes(self.ssh_private)
        (admin / 'id_ed25519.pub').write_bytes(self.ssh_public)
        school['admin_public_key'] = self.ssh_public.decode('ascii')
        (self.data.server / 'school.json').write_text(json.dumps(school), encoding='utf-8')
        plates = self.data.server / 'Laserplader'; plates.mkdir()
        (plates / 'backup-protocol.json').write_text('{"protocol":1,"role":"laser_station"}')
        (self.data.server / 'security/client.key').write_bytes(b'preserved-direct-transfer-key')
        station = self.data.server / 'laserstation'; station.mkdir()
        (station / 'laser-installation.json').write_text(json.dumps({'plates': str(plates),
            'ca_file': str(self.data.server / 'security/ca.crt'),
            'direct_transfer': {'client_key_file': str(self.data.server / 'security/client.key')}}), encoding='utf-8')
        self.saved_key = (self.data.server / 'security/server.key').read_bytes()
        self.source = self.root / 'backup'
        backup.snapshot(self.data.profile, self.source)
        self.runtime, self.target = self.root / 'replacement-program', self.root / 'replacement-data'

    def prepare(self):
        return recovery.prepare(self.source, self.package, self.runtime, self.target)

    def install_copy(self):
        shutil.copytree(self.package, self.runtime / 'program')
        path = self.target / 'installation-owner.json'
        owner = json.loads(path.read_text(encoding='utf-8')); owner['state'] = 'installing'
        path.write_text(json.dumps(owner), encoding='utf-8')

    def test_original_host_gone_preserves_identity_keys_data_and_pauses_queue(self):
        self.data.queue.close(); self.data.queue = None
        self.assertTrue(self.data.server.resolve().is_relative_to(Path(self.data.temp.name).resolve()))
        shutil.rmtree(self.data.server)
        result = self.prepare()
        self.assertEqual(result['state'], 'prepared')
        self.assertEqual(self.prepare(), result)
        self.assertEqual((self.target / 'security/server.key').read_bytes(), self.saved_key)
        self.assertEqual((self.target / 'admin-ssh/id_ed25519').read_bytes(), self.ssh_private)
        owner = json.loads((self.target / 'installation-owner.json').read_text(encoding='utf-8'))
        self.assertEqual(owner['ownerId'].replace('-', ''), self.program.installation_id)
        self.assertEqual(owner['state'], 'recoveryPrepared')
        self.assertEqual(owner['installRoot'], str(self.runtime))
        config = json.loads((self.target / 'konfiguration.json').read_text(encoding='utf-8'))
        self.assertEqual(config['tls']['noegle'], str(self.target / 'security/server.key'))
        self.assertEqual(config['makerspace_printers']['key_file'], str(self.target / 'printvaerksted/makerspace-bridge.key'))
        station = json.loads((self.target / 'laserstation/laser-installation.json').read_text(encoding='utf-8'))
        self.assertEqual(station['plates'], str(self.target / 'Laserplader'))
        self.assertEqual(station['direct_transfer']['client_key_file'], str(self.target / 'security/client.key'))
        self.assertEqual((self.target / 'security/client.key').read_bytes(), b'preserved-direct-transfer-key')
        with closing(sqlite3.connect(self.target / 'printvaerksted/queue.sqlite3')) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
        with self.assertRaises(RuntimeError):
            assert_not_restored(self.target)
        with self.assertRaisesRegex(ValueError, 'ejede installer'):
            recovery.release(self.package, self.runtime, self.target)

    def test_interrupted_copy_is_preserved_and_resume_publishes_fresh_verified_data(self):
        write = backup._write
        def torn_request(path, value):
            if path.name == recovery.REQUEST:
                path.write_text('{"format":', encoding='utf-8')
                raise OSError('initial request interrupted')
            return write(path, value)
        with patch.object(backup, '_write', side_effect=torn_request):
            with self.assertRaisesRegex(OSError, 'initial request interrupted'):
                self.prepare()
        initial_stage = next(self.root.glob('.replacement-data.school-recovery-*'))
        with patch.object(recovery.os, 'rename', side_effect=OSError('before publish')):
            with self.assertRaisesRegex(OSError, 'before publish'):
                self.prepare()
        self.assertFalse(self.target.exists())
        stages = list(self.root.glob('.replacement-data.school-recovery-*'))
        old = [path for stage in stages for path in stage.glob('restore-*')]
        self.assertEqual(len(old), 1)
        self.prepare()
        self.assertTrue(all(path.exists() for path in old))
        self.assertEqual((initial_stage / recovery.REQUEST).read_text(encoding='utf-8'), '{"format":')
        recovery.check(self.package, self.runtime, self.target)
        with self.assertRaises(RuntimeError):
            assert_not_restored(self.target)

    def test_wrong_original_package_fails_before_any_destination(self):
        path = self.package / 'integrity.json'
        path.write_text('changed package')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('.replacement-data.school-recovery-*')), [])

    def test_changed_prepared_job_file_keeps_all_holds(self):
        self.prepare()
        path = self.target / 'printvaerksted/settings.json'
        path.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError, 'data er ændret'):
            recovery.check(self.package, self.runtime, self.target)
        self.assertTrue((self.target / backup.HOLD_FILE).exists())

    def test_unknown_empty_directory_and_fake_instance_lock_are_not_ignored(self):
        self.prepare()
        folder = self.target / 'unknown-pupil-folder'; folder.mkdir()
        with self.assertRaisesRegex(ValueError, 'data er ændret'):
            recovery.check(self.package, self.runtime, self.target)
        (folder / 'instance.lock').write_bytes(b'not a known service lock')
        with self.assertRaisesRegex(ValueError, 'data er ændret'):
            recovery.check(self.package, self.runtime, self.target)
        self.assertTrue((self.target / backup.HOLD_FILE).exists())

    def test_wrong_ssh_identity_is_rejected_even_with_a_consistent_backup_hash(self):
        other = ed25519.Ed25519PrivateKey.generate()
        key_path = self.source / 'roots/server/admin-ssh/id_ed25519'
        key_path.write_bytes(other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()))
        manifest_path = self.source / backup.MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for row in manifest['files']:
            if row['path'] == 'roots/server/admin-ssh/id_ed25519':
                row.update(sha256=backup._hash(key_path), size=key_path.stat().st_size)
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'SSH-nøgle'):
            self.prepare()
        self.assertFalse(self.target.exists())

    def test_interrupted_hold_release_resumes_same_pair_without_unpausing(self):
        self.prepare(); self.install_copy()
        unlink = Path.unlink
        calls = []
        def interrupt(path, *args, **kwargs):
            if path.name == backup.HOLD_FILE:
                calls.append(path)
                if len(calls) == 2:
                    raise OSError('release interrupted')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', side_effect=interrupt, autospec=True):
            with self.assertRaisesRegex(OSError, 'release interrupted'):
                recovery.release(self.package, self.runtime, self.target)
        self.assertEqual(json.loads((self.target / recovery.RECEIPT).read_text(encoding='utf-8'))['phase'], 'releasing')
        self.assertEqual(recovery.release(self.package, self.runtime, self.target)['state'], 'released')
        self.assertEqual(list(self.target.rglob(backup.HOLD_FILE)), [])
        recovery.check(self.package, self.runtime, self.target)
        with closing(sqlite3.connect(self.target / 'printvaerksted/queue.sqlite3')) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')

    def test_readiness_retry_retains_data_accepted_after_the_first_start(self):
        self.prepare(); self.install_copy()
        recovery.release(self.package, self.runtime, self.target)
        recovery.started(self.package, self.runtime, self.target)
        accepted = self.target / 'printvaerksted/new-pupil-project.stl'
        accepted.write_bytes(b'accepted after service start')
        owner_path = self.target / 'installation-owner.json'
        owner = json.loads(owner_path.read_text(encoding='utf-8')); owner['state'] = 'rolledBack'
        owner_path.write_text(json.dumps(owner), encoding='utf-8')
        recovery.check(self.package, self.runtime, self.target)
        self.assertEqual(self.prepare()['state'], 'started')
        owner['state'] = 'installing'; owner_path.write_text(json.dumps(owner), encoding='utf-8')
        recovery.release(self.package, self.runtime, self.target)
        recovery.started(self.package, self.runtime, self.target)
        owner['state'] = 'installed'; owner_path.write_text(json.dumps(owner), encoding='utf-8')
        recovery.complete(self.package, self.runtime, self.target)
        self.assertEqual(self.prepare()['state'], 'complete')
        self.assertEqual(accepted.read_bytes(), b'accepted after service start')


if __name__ == '__main__':
    unittest.main()
