from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.instance import InstanceLock
from app.server import Application


class RuntimeGuardTests(unittest.TestCase):
    def test_restore_hold_is_checked_before_datadir_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".restore-hold.json").write_text("{}", encoding="utf-8")
            target = root / "new-school"
            with self.assertRaises(RuntimeError):
                Application(target, demo=True)
            self.assertFalse(target.exists())

    def test_init_failure_releases_instance_lock_and_closes_partial_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "school"
            fake_queue = mock.Mock()
            with mock.patch.object(Application, "_initialize", side_effect=RuntimeError("init failed")), \
                 mock.patch("app.server.Queue", return_value=fake_queue):
                with self.assertRaisesRegex(RuntimeError, "init failed"):
                    Application(target, demo=True)
            # A second owner can acquire the same OS lock after failed init.
            lock = InstanceLock(target)
            lock.close()
            fake_queue.close.assert_not_called()

    def test_partial_component_failure_closes_queue_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "school"
            fake_queue = mock.Mock()
            fake_queue.configs = {}
            fake_queue.lock = mock.Mock()
            with mock.patch("app.server.Queue", return_value=fake_queue), \
                 mock.patch("app.server.Slicer", side_effect=RuntimeError("slicer failed")):
                with self.assertRaisesRegex(RuntimeError, "slicer failed"):
                    Application(target, demo=True)
            fake_queue.close.assert_called_once_with()
            lock = InstanceLock(target)
            lock.close()

    def test_shutdown_is_idempotent_after_all_resources_are_closed(self):
        app = object.__new__(Application)
        app._closed = False
        app.instance_lock = mock.Mock()
        instance_lock = app.instance_lock
        app.queue = mock.Mock()
        queue = app.queue
        queue.close = mock.Mock()
        app.worker = app.firmware = app.cameras = None
        app.drivers = {}
        app.shutdown()
        app.shutdown()
        queue.close.assert_called_once_with()
        instance_lock.close.assert_called_once_with()

    def test_incomplete_shutdown_keeps_data_locked_until_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as folder:
            app = object.__new__(Application)
            app._closed = False
            app.instance_lock = InstanceLock(folder)
            app.queue = mock.Mock()
            app.firmware = app.cameras = None
            app.worker = mock.Mock()
            app.worker.stop.side_effect = RuntimeError('still writing')
            app.drivers = {}
            try:
                with mock.patch('app.server.logging.exception'):
                    with self.assertRaisesRegex(RuntimeError, 'datalåsen beholdes'):
                        app.shutdown()
                    with self.assertRaises(RuntimeError):
                        InstanceLock(folder)
                    app.queue.close.assert_not_called()
                    app.worker.stop.side_effect = None
                    app.shutdown()
                InstanceLock(folder).close()
            finally:
                app.instance_lock.close()


if __name__ == "__main__":
    unittest.main()
