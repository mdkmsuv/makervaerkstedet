import unittest

from makerspace_adapters import laser_machine, printer_machines


class AdapterTests(unittest.TestCase):
    def test_printer_fresh_supported_row(self):
        rows = printer_machines({"status": "ok", "checked_at": 1700000000,
                                 "items": [{"id": "a1", "name": "Front", "model": "A1 mini",
                                            "status": "ready", "online": True, "enabled": True}]})
        self.assertEqual(rows[0]["id"], "bambu:a1")
        self.assertTrue(rows[0]["supported"])
        self.assertTrue(rows[0]["online"])
        self.assertEqual(rows[0]["status"], "ready")

    def test_unknown_collection_is_empty(self):
        self.assertEqual(printer_machines({"status": "unknown", "checked_at": 1, "items": []}), [])

    def test_unknown_model_is_not_supported(self):
        row = printer_machines({"status": "ok", "checked_at": 1,
                                "items": [{"id": "x", "model": "X1 Carbon", "status": "ready",
                                           "online": True, "enabled": True}]})[0]
        self.assertFalse(row["supported"])

    def test_bad_printer_timestamp_is_unknown_offline(self):
        row = printer_machines({"status": "ok", "checked_at": "not-a-time",
                                "items": [{"id": "x", "model": "P1S", "status": "ready",
                                           "online": True, "enabled": True}]})[0]
        self.assertEqual((row["status"], row["online"]), ("unknown", False))

    def test_negative_timestamp_and_malformed_status_are_safe(self):
        rows = printer_machines({"status": "ok", "checked_at": -1,
                                 "items": [{"id": "x", "model": "P1S", "status": {},
                                            "online": True, "enabled": True}]})
        self.assertEqual((rows[0]["status"], rows[0]["online"]), ("unknown", False))

    def test_ready_without_online_is_not_ready(self):
        row = printer_machines({"status": "ok", "checked_at": 1,
                                "items": [{"id": "x", "model": "P1S", "status": "ready",
                                           "online": False, "enabled": True}]})[0]
        self.assertEqual((row["status"], row["online"]), ("offline", False))

    def test_laser_fresh_state_and_secret_payload_are_whitelisted(self):
        state = {"loggedIn": True, "flux": True,
                 "bed": {"model": "fbb2", "machine": "Beambox II"},
                 "directTransfer": {"available": True},
                 "server": "https://secret.invalid", "lastPlate": {"student": "Elevnavn"},
                 "jobs": [{"student": "Elevnavn"}]}
        row = laser_machine("station-1", state, "2026-10-11T10:00:00Z")
        self.assertEqual(row["status"], "unknown")
        self.assertFalse(row["online"])
        self.assertTrue(row["service_available"])
        self.assertTrue(row["connection_ready"])
        self.assertNotIn("server", row)
        self.assertNotIn("jobs", row)
        self.assertNotIn("student", row)

    def test_laser_malformed_is_unknown_offline(self):
        row = laser_machine("station-1", {"bed": {"model": "fbb2"}}, "bad")
        self.assertEqual((row["status"], row["online"]), ("unknown", False))


if __name__ == "__main__":
    unittest.main()
