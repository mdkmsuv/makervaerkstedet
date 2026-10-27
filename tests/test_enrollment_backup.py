import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import queue
import threading

from app.installation_backup import offline_profile, restore, snapshot
from app.instance import InstanceLock


ROOT = Path(__file__).resolve().parents[1]
ENROLLMENT_MODULE = ROOT / 'deployment' / 'school-setup' / 'enrollment.py'
spec = importlib.util.spec_from_file_location('enrollment_backup_contract', ENROLLMENT_MODULE)
enrollment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enrollment)


class EnrollmentBackupTests(unittest.TestCase):
    def setUp(self):
        from tests.test_installation_backup import InstallationBackupTests
        self.fixture = InstallationBackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        root = self.fixture.server
        (root / 'school.json').write_text('{"schemaVersion": 1, "school": "fixture"}\n', encoding='utf-8')
        enrollment.write_service_protocol(root)
        (root / 'security' / 'enrollment' / 'operations').mkdir(parents=True, exist_ok=True)
        (root / 'security' / 'enrollment.json').write_text('{"ticket": "fixture-ticket"}\n', encoding='utf-8')
        (root / 'security' / 'ssh-pins.json').write_text('{"pin": "fixture-pin"}\n', encoding='utf-8')
        self.backup = Path(self.fixture.temp.name) / 'backup'
        self.restored = Path(self.fixture.temp.name) / 'restored'

    def test_service_or_operations_lock_rejects_snapshot_before_destination(self):
        for relative in (enrollment.SERVICE_LOCK, enrollment.OPERATIONS_LOCK):
            destination = Path(self.fixture.temp.name) / ('blocked-' + relative.replace('/', '-'))
            owner = enrollment.EnrollmentInstanceLock(self.fixture.server, relative).acquire()
            try:
                with self.assertRaises(RuntimeError):
                    snapshot(self.fixture.profile, destination)
                self.assertFalse(destination.exists())
            finally:
                owner.close()

    def test_offline_profile_holds_both_enrollment_locks(self):
        with offline_profile(self.fixture.profile):
            with self.assertRaises(RuntimeError):
                enrollment.EnrollmentInstanceLock(self.fixture.server).acquire()
            with self.assertRaises(RuntimeError):
                enrollment.EnrollmentInstanceLock(self.fixture.server, enrollment.OPERATIONS_LOCK).acquire()

    def test_live_enrollment_registry_lock_rejects_snapshot_before_destination(self):
        script = ('import sys; from app.instance import InstanceLock; '
                  'lock=InstanceLock(sys.argv[1], filename="enheder.lock"); '
                  'print("ready", flush=True); sys.stdin.read(); lock.close()')
        owner = subprocess.Popen([sys.executable, '-c', script, str(self.fixture.server / 'security')],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        ready = queue.Queue(maxsize=1)
        reader = threading.Thread(target=lambda: ready.put(owner.stdout.readline()), daemon=True)
        reader.start()
        try:
            self.assertEqual(ready.get(timeout=5).strip(), 'ready')
            destination = Path(self.fixture.temp.name) / 'registry-blocked'
            with self.assertRaises((OSError, RuntimeError)):
                snapshot(self.fixture.profile, destination)
            self.assertFalse(destination.exists())
        finally:
            try:
                owner.stdin.close()
            except OSError:
                pass
            try:
                owner.wait(timeout=2)
            except subprocess.TimeoutExpired:
                owner.terminate()
                try:
                    owner.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    owner.kill()
                    owner.wait(timeout=2)
            owner.stdout.close()
            reader.join(timeout=2)

    def test_offline_profile_holds_configured_registry_lock(self):
        with offline_profile(self.fixture.profile):
            contender = subprocess.run(
                [sys.executable, '-c',
                 'exec("import sys\\nfrom app.instance import InstanceLock\\ntry:\\n '
                 'InstanceLock(sys.argv[1], filename=\\"enheder.lock\\")\\nexcept RuntimeError:\\n '
                 'raise SystemExit(0)\\nraise SystemExit(1)")',
                 str(self.fixture.server / 'security')], capture_output=True, text=True, timeout=5)
            self.assertEqual(contender.returncode, 0, contender.stderr)

    def test_snapshot_ignores_only_registry_lock_and_keeps_private_lock_payload(self):
        private = self.fixture.server / 'security' / 'private.lock'
        private.write_bytes(b'private-payload')
        registry = self.fixture.server / 'security' / 'enheder.lock'
        registry.write_bytes(b'lock-marker')
        result = snapshot(self.fixture.profile, self.backup)
        self.assertTrue(result['ok'])
        manifest = json.loads((self.backup / 'backup-manifest.json').read_text(encoding='utf-8'))
        paths = {entry['path'] for entry in manifest['files']}
        self.assertNotIn('roots/server/security/enheder.lock', paths)
        self.assertIn('roots/server/security/private.lock', paths)

    def test_school_profile_without_enrollment_protocol_is_rejected(self):
        protocol = self.fixture.server / enrollment.SERVICE_PROTOCOL
        protocol.unlink()
        with self.assertRaises((OSError, ValueError)):
            snapshot(self.fixture.profile, self.backup)
        self.assertFalse(self.backup.exists())

    def test_snapshot_restore_preserves_enrollment_records_and_restore_hold(self):
        result = snapshot(self.fixture.profile, self.backup)
        self.assertTrue(result['ok'])
        restore(self.backup, self.restored)
        candidate = self.restored / 'roots' / 'server'
        self.assertEqual((candidate / 'security' / 'enrollment.json').read_text(encoding='utf-8'),
                         '{"ticket": "fixture-ticket"}\n')
        self.assertEqual((candidate / 'security' / 'ssh-pins.json').read_text(encoding='utf-8'),
                         '{"pin": "fixture-pin"}\n')
        self.assertTrue((self.restored / '.restore-hold.json').exists())
        with self.assertRaises(RuntimeError):
            with enrollment.lock_registry(candidate):
                pass


if __name__ == '__main__':
    unittest.main()
