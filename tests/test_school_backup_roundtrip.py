"""End-to-end preservation contract for an isolated school backup restore.

This deliberately composes the established installation-backup fixture and
checks the data a school would need to recover, rather than repeating the
individual manifest and SQLite unit tests.
"""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

from app.installation_backup import restore, snapshot, verify

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux-elevhub" / "server" / "elevhub"))
from tilstand import Laererkode  # noqa: E402
from makerspace_workflows import templates  # noqa: E402


class SchoolBackupRoundtripTests(unittest.TestCase):
    def setUp(self):
        from tests.test_installation_backup import InstallationBackupTests

        self.fixture = InstallationBackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = Path(self.fixture.temp.name)
        self.teacher_code = "korrekt-laererkode-2026"
        Laererkode(self.fixture.hub).vaelg(self.teacher_code)
        self._add_school_state()

    def _add_school_state(self):
        server = self.fixture.server
        # School identity and Danish teacher-facing text are ordinary source
        # files and must survive relocation byte-for-byte.
        (server / "school.json").write_text(json.dumps({
            "school_name": "Klostermarksskolen",
            "role": "makerspace",
            "server": "203.0.113.10",
            "student_subnet": "203.0.113.0/24",
            "welcome": "Velkommen til værkstedet",
        }, ensure_ascii=False), encoding="utf-8")
        enrollment = server / "security" / "enrollment"
        (enrollment / "operations").mkdir(parents=True)
        (enrollment / "backup-protocol.json").write_text(
            json.dumps({"protocol": 1, "role": "enrollment"}), encoding="utf-8"
        )
        admin = server / "admin-ssh"
        admin.mkdir()
        (admin / "id_ed25519").write_bytes(b"school-admin-ssh-private-key")
        (admin / "known_hosts").write_text("makerspace.example ssh-ed25519 AAAA\n", encoding="utf-8")

        # Retain files that are not represented solely by queue rows.
        (self.fixture.print / "projects").mkdir()
        (self.fixture.print / "projects" / "Øvelse – navneskilt.stl").write_bytes(b"historic-print-file")
        (self.fixture.laser / "history").mkdir()
        (self.fixture.laser / "history" / "skilt-2026.json").write_text(
            '{"status":"completed","elev":"Åse"}\n', encoding="utf-8")

        # Exercise the real workflow API, so the preserved machine/workflow
        # rows are produced by the same path as teacher administration.
        actor = {"id": "teacher:" + "1" * 32, "role": "teacher",
                 "scopes": ["makerspace:read", "machines:write", "workflows:write",
                            "jobs:write", "audit:read"]}
        definition = templates()["manual"]
        self.fixture.service.execute(actor, {"action": "workflow.create",
                                              "params": {"definition": definition},
                                              "expected_version": 0, "request_id": "a" * 32})
        workflow = self.fixture.service.store.snapshot()["workflows"][0]
        self.fixture.service.execute(actor, {"action": "workflow.publish",
                                              "params": {"id": workflow["id"]},
                                              "expected_version": 1, "request_id": "b" * 32})

    def _restored_profile(self, destination):
        profile = json.loads(json.dumps(self.fixture.profile))
        profile["roots"]["server"]["path"] = str(destination / "roots" / "server")
        profile["components"]["hub"]["root"] = "server"
        profile["components"]["print"]["root"] = "server"
        profile["components"]["laser"]["root"] = "server"
        profile["config"] = {"root": "server", "path": "konfiguration.json"}
        return profile

    def test_roundtrip_preserves_school_identity_credentials_keys_workflows_and_files(self):
        backup = self.root / "school-backup"
        destination = self.root / "restored-school"
        with patch("app.installation_backup._private_directory"):
            result = snapshot(self.fixture.profile, backup)
            self.assertTrue(result["ok"])
            self.assertTrue(verify(backup)["ok"])
            report = restore(backup, destination)
        self.assertTrue(report["isolated"])
        self.assertTrue(report["agents_revoked"])
        self.assertTrue(report["print_paused"])

        restored = destination / "roots" / "server"
        self.assertEqual(
            json.loads((restored / "school.json").read_text(encoding="utf-8"))["welcome"],
            "Velkommen til værkstedet",
        )
        self.assertEqual((restored / "admin-ssh/id_ed25519").read_bytes(), b"school-admin-ssh-private-key")
        restored_code = Laererkode(restored / "hub")
        self.assertTrue(restored_code.er_valgt())
        self.assertTrue(restored_code.log_ind(self.teacher_code, "127.0.0.1"))
        with self.assertRaises(PermissionError):
            restored_code.log_ind("forkert-laererkode", "127.0.0.1")
        self.assertEqual(
            (restored / "printvaerksted/projects/Øvelse – navneskilt.stl").read_bytes(),
            b"historic-print-file",
        )
        self.assertEqual(
            (restored / "designvaerksted/history/skilt-2026.json").read_text(encoding="utf-8"),
            '{"status":"completed","elev":"Åse"}\n',
        )
        for relative in (
            "security/server.crt", "security/server.key", "security/ca.crt",
            "printvaerksted/makerspace-bridge.key", "designvaerksted/makerspace-laser.key",
        ):
            source = self.fixture.server / relative
            self.assertEqual((restored / relative).read_bytes(), source.read_bytes())

        with closing(sqlite3.connect(self.fixture.hub / "makerspace.sqlite3")) as source_db:
            source_objects = source_db.execute(
                "SELECT type,id,version,body FROM objects ORDER BY id").fetchall()
            source_workflows = source_db.execute(
                "SELECT id,revision,body FROM workflow_versions ORDER BY id,revision").fetchall()
        with closing(sqlite3.connect(restored / "hub/makerspace.sqlite3")) as db:
            self.assertEqual(db.execute(
                "SELECT type,id,version,body FROM objects ORDER BY id").fetchall(), source_objects)
            self.assertEqual(db.execute(
                "SELECT id,revision,body FROM workflow_versions ORDER BY id,revision").fetchall(), source_workflows)
        with closing(sqlite3.connect(restored / "printvaerksted/queue.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], "true")
            self.assertEqual(db.execute("SELECT status FROM jobs WHERE id=?", (self.fixture.job["id"],)).fetchone()[0], "dispatching")
            self.assertTrue(db.execute("SELECT 1 FROM library_models").fetchone())
        with closing(sqlite3.connect(restored / "designvaerksted/afleveringer.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT station_id, workflow_version, handoff_claimed FROM jobs WHERE id=?",
                                        (self.fixture.laser_job["id"],)).fetchone(), ("laser-fbb2", 4, 1))
            self.assertTrue(db.execute("SELECT 1 FROM laser_workflow_audit").fetchone())

        # Every restored root and component carries a hold. It is enforced by
        # the normal backup entry point, so the isolated copy cannot be reused.
        self.assertTrue((destination / ".restore-hold.json").exists())
        with self.assertRaises(RuntimeError):
            snapshot(self._restored_profile(destination), self.root / "second-backup")

    def test_tamper_is_rejected_before_destination_and_existing_destination_is_refused(self):
        backup = self.root / "tampered-backup"
        with patch("app.installation_backup._private_directory"):
            snapshot(self.fixture.profile, backup)
        manifest = json.loads((backup / "backup-manifest.json").read_text(encoding="utf-8"))
        target = next(row["path"] for row in manifest["files"] if row["path"].endswith("school.json"))
        path = backup / target
        path.write_text(path.read_text(encoding="utf-8") + "\nændret", encoding="utf-8")
        destination = self.root / "tampered-destination"
        with patch("app.installation_backup._private_directory"):
            with self.assertRaises(ValueError):
                restore(backup, destination)
        self.assertFalse(destination.exists(), "tampered input must fail before destination creation")

        clean = self.root / "clean-backup"
        with patch("app.installation_backup._private_directory"):
            snapshot(self.fixture.profile, clean)
        existing = self.root / "already-there"
        existing.mkdir()
        with patch("app.installation_backup._private_directory"):
            with self.assertRaises(FileExistsError):
                restore(clean, existing)
        self.assertEqual(list(existing.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
