"""Production entrypoints read private component data and unwind invalid config."""
from pathlib import Path
import logging
import sys
import tempfile
import unittest
from unittest import mock

from app.instance import InstanceLock


class ProjectOriginStartupTests(unittest.TestCase):
    def test_print_main_remote_private_config_rejected_before_bind_closes_application(self):
        import app.server as module
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'print-project-origins.json').write_text('{"allowed_origins":["http://evil.test:8791"]}')
            # Real application cleanup and OS lifetime lock; runtime services are
            # disposable mocks so no printer or existing installation is touched.
            app = module.Application.__new__(module.Application)
            app.folder, app.instance_lock = root, InstanceLock(root)
            app.queue = mock.Mock()
            app.worker = app.firmware = app.cameras = None
            app.drivers, app.log_handler, app._closed = {}, None, False
            level = logging.getLogger().level
            try:
                with mock.patch.object(module, 'Application', return_value=app), \
                     mock.patch.object(module.ThreadingHTTPServer, 'server_bind') as bind, \
                     mock.patch.object(sys, 'argv', ['print', '--data', folder]):
                    with self.assertRaises(ValueError):
                        module.main()
                    bind.assert_not_called()
                self.assertTrue(app._closed)
                app.queue.close.assert_called_once()
                self.assertNotIn(app.log_handler, logging.getLogger().handlers)
                InstanceLock(root).close()
            finally:
                logging.getLogger().setLevel(level)
                if not app._closed:
                    app.instance_lock.close()

    def test_laser_main_remote_private_config_rejected_before_bind_releases_lock(self):
        import app.laser_server as module
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'laser-project-origins.json').write_text('{"allowed_origins":["http://evil.test:8791"]}')
            with mock.patch.object(module.ThreadingHTTPServer, 'server_bind') as bind, \
                 mock.patch.object(module, 'Inbox') as inbox:
                with self.assertRaises(ValueError):
                    module.main(['--data', folder, '--port', '0', '--teacher-port', '0'])
                bind.assert_not_called()
                inbox.assert_not_called()
            InstanceLock(root).close()
