import tempfile
import unittest

from app.core import Queue
from scripts.smoke_slice import cube


class EnabledPrinterCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, configs, payload_size, *, demo=False):
        queue = Queue(self.temp.name, configs, demo=demo)
        try:
            return queue.submit(
                cube(payload_size),
                "elevfigur.stl",
                "any",
                "enabled-capacity-request-0001",
            )
        finally:
            queue.close()

    def test_disabled_h2d_cannot_admit_model_for_enabled_small_printer(self):
        configs = [
            {"id": "h2d", "name": "H2D", "model": "H2D", "enabled": False},
            {"id": "small", "name": "A1 mini", "model": "A1 mini", "enabled": True},
        ]
        with self.assertRaisesRegex(ValueError, "for stor"):
            self.submit(configs, 200)

    def test_enabled_supported_printer_can_queue_while_temporarily_offline(self):
        configs = [
            {"id": "h2d", "name": "H2D", "model": "H2D", "enabled": False},
            {"id": "small", "name": "A1 mini", "model": "A1 mini", "enabled": True},
        ]
        job = self.submit(configs, 20)
        self.assertEqual(job["status"], "queued")
        self.assertIsNone(job["printer_id"])

    def test_demo_without_printers_remains_open(self):
        job = self.submit([], 20, demo=True)
        self.assertEqual(job["status"], "queued")


if __name__ == "__main__":
    unittest.main()
