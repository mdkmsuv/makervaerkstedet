import importlib.util
import time
import unittest
from pathlib import Path


FILE = Path(__file__).parents[1] / 'ansible/roles/kiosk/files/cadoodle_vaerksted.py'
SPEC = importlib.util.spec_from_file_location('cadoodle_vaerksted', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CadoodleOfflineTest(unittest.TestCase):
    def make(self, status, app=None):
        bar = type('Bar', (), {'server_status': status})()
        workshop = MODULE.Vaerksted.__new__(MODULE.Vaerksted)
        workshop.bjaelke = bar
        workshop.app = app
        workshop.sidst = time.monotonic()
        return workshop

    def test_cold_start_or_network_loss_opens_local_cadoodle(self):
        self.assertTrue(self.make('offline').aaben())

    def test_valid_server_policy_still_controls_cadoodle(self):
        closed = self.make('online', {'id': '3dprint', 'aaben': False, 'klar': True})
        self.assertFalse(closed.aaben())
        open_app = self.make('online', {'id': '3dprint', 'aaben': True, 'klar': True})
        self.assertTrue(open_app.aaben())

    def test_reconnect_without_valid_policy_invalidates_stale_app(self):
        workshop = self.make('online', {'id': '3dprint', 'aaben': True, 'klar': True})
        workshop.opdater(None)
        self.assertFalse(workshop.aaben())


if __name__ == '__main__':
    unittest.main()
