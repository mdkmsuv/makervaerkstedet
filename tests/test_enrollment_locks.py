import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from app.instance import InstanceLock

MODULE = Path(__file__).parents[1] / 'deployment' / 'school-setup' / 'enrollment.py'
spec = importlib.util.spec_from_file_location('enrollment_lock_contract', MODULE)
enrollment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enrollment)


class EnrollmentLockTests(unittest.TestCase):
    def test_lifetime_lock_interoperates_with_app_instance_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = enrollment.EnrollmentInstanceLock(root)
            first.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    InstanceLock(root / 'security' / 'enrollment')
            finally:
                first.close()
            other = InstanceLock(root / 'security' / 'enrollment')
            other.close()

    def test_operations_lock_interoperates_and_has_no_growth_on_contention(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            operations = root / 'security' / 'enrollment' / 'operations'
            operations.mkdir(parents=True)
            first = InstanceLock(operations)
            size = (operations / 'instance.lock').stat().st_size
            try:
                with self.assertRaises(RuntimeError):
                    enrollment.EnrollmentInstanceLock(root, enrollment.OPERATIONS_LOCK).acquire()
                self.assertEqual((operations / 'instance.lock').stat().st_size, size)
            finally:
                first.close()

    def test_registry_operations_remain_available_with_lifetime_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'security').mkdir()
            operations = root / 'security' / 'enrollment' / 'operations'
            operations.mkdir(parents=True)
            lifetime = enrollment.EnrollmentInstanceLock(root)
            lifetime.acquire()
            try:
                with enrollment.lock_registry(root):
                    self.assertTrue((root / enrollment.OPERATIONS_LOCK).exists())
            finally:
                lifetime.close()

    def test_hold_appearing_after_lock_acquisition_closes_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = enrollment.EnrollmentInstanceLock(root)
            with mock.patch.object(enrollment, 'assert_not_restored', side_effect=[None, RuntimeError('hold')]):
                with self.assertRaisesRegex(RuntimeError, 'hold'):
                    lock.acquire()
            self.assertIsNone(lock.stream)
            released = enrollment.EnrollmentInstanceLock(root)
            released.acquire()
            released.close()

    def test_restore_hold_blocks_before_any_lock_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.restore-hold.json').write_text('{}', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                enrollment.EnrollmentInstanceLock(root).acquire()
            self.assertFalse((root / 'security').exists())

    def test_linked_lock_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            real = root / 'real'
            real.mkdir()
            linked = root / 'linked'
            try:
                linked.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
            with self.assertRaises(ValueError):
                enrollment.EnrollmentInstanceLock(linked).acquire()

    def test_protocol_type_mismatches_are_repaired_to_integer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            enrollment.write_service_protocol(root)
            target = root / enrollment.SERVICE_PROTOCOL
            for marker in ({'protocol': True, 'role': 'enrollment'},
                           {'protocol': 1.0, 'role': 'enrollment'}):
                target.write_text(json.dumps(marker), encoding='utf-8')
                enrollment.write_service_protocol(root)
                value = enrollment.read_json(target)
                self.assertIs(type(value['protocol']), int)
                self.assertEqual(value, {'protocol': 1, 'role': 'enrollment'})

    def test_preexisting_pending_symlink_never_writes_external_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / 'outside.json'
            outside.write_text('original', encoding='utf-8')
            pending = root / 'value.json.pending'
            try:
                pending.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
            destination = root / 'value.json'
            enrollment.write_json(destination, {'changed': True})
            self.assertEqual(enrollment.read_json(destination), {'changed': True})
            self.assertEqual(outside.read_text(encoding='utf-8'), 'original')
            self.assertTrue(pending.is_symlink())

    def test_destination_symlink_is_rejected_without_external_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / 'outside.json'
            outside.write_text('original', encoding='utf-8')
            destination = root / 'value.json'
            try:
                destination.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
            with self.assertRaises(ValueError):
                enrollment.write_json(destination, {'changed': True})
            self.assertEqual(outside.read_text(encoding='utf-8'), 'original')

    def test_separate_process_holding_operations_lock_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'security').mkdir()
            (root / 'security' / 'enrollment' / 'operations').mkdir(parents=True)
            script = ('from app.instance import InstanceLock; import sys; '
                      'lock=InstanceLock(sys.argv[1]); print("ready", flush=True); sys.stdin.read()')
            process = subprocess.Popen([sys.executable, '-c', script,
                                        str(root / 'security' / 'enrollment' / 'operations')],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                with self.assertRaises(RuntimeError):
                    with enrollment.lock_registry(root):
                        pass
                self.assertFalse((root / 'security' / 'enheder.lock').exists())
            finally:
                if process.stdin:
                    process.stdin.close()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=3)
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()


if __name__ == '__main__':
    unittest.main()
