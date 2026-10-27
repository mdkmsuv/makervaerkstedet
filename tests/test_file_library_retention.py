import errno
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.file_library import (
    ARCHIVE_FREE_HEADROOM,
    DISK_STATUS_CACHE_SECONDS,
    FileLibrary,
    MAX_MODEL_BYTES,
    LibraryCorrupt,
    LibraryDeletionPending,
    LibraryInUse,
    LibraryStorageError,
)


class FileLibraryRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(self.temp.name + "/library.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY, digest TEXT, filename TEXT, status TEXT,
            workflow TEXT, workflow_complete INTEGER, created REAL,
            quality TEXT DEFAULT 'standard', colour TEXT
        )""")
        self.library = FileLibrary(self.db, self.temp.name)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def archive(self, data=b"model", name="model.stl", created=100):
        return self.library.archive(data, filename=name, colour=None, quality="standard",
                                    meta={"size": [1, 1, 1]}, created=created)

    def job(self, digest, *, status="done", filename="elevnavn.stl", workflow=None, complete=1):
        self.db.execute(
            "INSERT INTO jobs(id,digest,filename,status,workflow,workflow_complete,created) VALUES(?,?,?,?,?,?,?)",
            ("job-" + digest[:8] + str(self.db.total_changes), digest, filename, status, workflow, complete, 1),
        )
        self.db.commit()

    def test_listing_is_bounded_and_disk_status_exposes_warning(self):
        self.archive(b"one", "one.stl", 1)
        self.archive(b"two", "two.stl", 2)
        self.archive(b"three", "three.stl", 3)
        page = self.library.list_page(1, 2, include_jobs=False)
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["total"], 3)
        self.library.set_retention_policy(quota_bytes=5, warning_ratio=.8)
        with patch("app.file_library.shutil.disk_usage", return_value=SimpleNamespace(total=100, used=80, free=20)):
            status = self.library.disk_status()
        self.assertEqual(status["used_bytes"], 11)
        self.assertEqual(status["quota_bytes"], 5)
        self.assertTrue(status["warning"])

    def test_policy_update_is_version_bound_and_receipted(self):
        current = self.library.get_retention_policy()
        updated = self.library.set_retention_policy(
            retention_days=30, quota_bytes=100, expected_version=current["version"],
            request_id="policy-request-0001")
        self.assertEqual(updated["version"], current["version"] + 1)
        self.assertEqual(self.library.set_retention_policy(
            retention_days=30, quota_bytes=100, expected_version=0,
            request_id="policy-request-0001"), updated)
        with self.assertRaises(ValueError):
            self.library.set_retention_policy(retention_days=999, quota_bytes=999,
                expected_version=0, request_id="policy-request-0001")
        with self.assertRaises(ValueError):
            self.library.set_retention_policy(retention_days=31, quota_bytes=101, expected_version=current["version"])

    def test_listing_searches_aliases_and_caps_history_per_item(self):
        digest = self.archive(b"history", "første-navn.stl")
        self.archive(b"history", "historik-11.stl")
        for index in range(12):
            self.job(digest, filename="historik-%02d.stl" % index)
        self.archive(b"other", "andet.stl")
        page = self.library.list_page(1, 25, query="historik-11", include_jobs=True)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["digest"], digest)
        self.assertEqual(page["items"][0]["job_total"], 12)
        self.assertLessEqual(len(page["items"][0]["jobs"]), 10)
        alias_page = self.library.list_page(1, 25, query="første-navn", include_jobs=False)
        self.assertEqual(alias_page["total"], 1)

    def test_listing_does_not_hash_every_object(self):
        self.archive(b"poll")
        with patch.object(self.library, "_verify_path", side_effect=AssertionError("hash in listing")):
            page = self.library.list_page(1, 25, include_jobs=False)
        self.assertEqual(page["items"][0]["integrity"], "present")

    def test_active_jobs_and_incomplete_workflows_block_delete(self):
        digest = self.archive()
        self.job(digest, status="queued")
        with self.assertRaises(LibraryInUse):
            self.library.delete(digest)
        self.db.execute("UPDATE jobs SET status='done', workflow=?, workflow_complete=0", (json.dumps({"id": "w"}),))
        self.db.commit()
        with self.assertRaises(LibraryInUse):
            self.library.delete(digest)

    def test_isolated_terminal_job_remains_protected(self):
        self.db.execute("ALTER TABLE jobs ADD COLUMN isolated INTEGER NOT NULL DEFAULT 0")
        self.library._job_columns.add("isolated")
        digest = self.archive(b"isolated")
        self.job(digest, status="cancelled")
        self.db.execute("UPDATE jobs SET isolated=1 WHERE digest=?", (digest,))
        self.db.commit()
        with self.assertRaises(LibraryInUse):
            self.library.delete(digest)

    def test_terminal_job_with_pending_upload_cleanup_remains_protected(self):
        digest = self.archive(b"cleanup")
        self.job(digest, status="done")
        job_id = self.db.execute("SELECT id FROM jobs WHERE digest=?", (digest,)).fetchone()[0]
        uploads = Path(self.temp.name) / "uploads"
        uploads.mkdir()
        (uploads / (job_id + ".stl")).write_bytes(b"cleanup")
        with self.assertRaises(LibraryInUse):
            self.library.delete(digest)
        (uploads / (job_id + ".stl")).unlink()
        self.assertTrue(self.library.delete(digest))

    def test_delete_anonymizes_terminal_history_and_is_idempotent(self):
        digest = self.archive(name="klasse/elevnavn.stl")
        self.job(digest, filename="elevnavn.stl")
        self.db.execute("UPDATE library_models SET aliases=? WHERE digest=?", (json.dumps(["hemmeligt-navn.stl"]), digest))
        self.db.commit()
        self.assertTrue(self.library.delete(digest))
        self.assertFalse((Path(self.temp.name) / "library" / (digest + ".stl")).exists())
        self.assertIsNone(self.db.execute("SELECT 1 FROM library_models WHERE digest=?", (digest,)).fetchone())
        filename = self.db.execute("SELECT filename FROM jobs WHERE digest=?", (digest,)).fetchone()[0]
        self.assertEqual(filename, "slettet-model-" + digest[:12] + ".stl")
        self.assertEqual(self.db.execute("SELECT count(*) FROM library_deletion_journal").fetchone()[0], 0)
        self.assertFalse(self.library.delete(digest))

    def test_unlink_failure_leaves_journal_and_recovery_finishes(self):
        digest = self.archive()
        self.job(digest)
        with patch.object(self.library, "_unlink_object", side_effect=LibraryDeletionPending("retry")):
            with self.assertRaises(LibraryDeletionPending):
                self.library.delete(digest)
        self.assertIsNone(self.db.execute("SELECT 1 FROM library_models WHERE digest=?", (digest,)).fetchone())
        self.assertEqual(self.db.execute("SELECT state FROM library_deletion_journal").fetchone()[0], "db_committed")
        self.assertTrue((Path(self.temp.name) / "library" / (digest + ".stl")).exists())
        self.db.close()
        reopened_db = sqlite3.connect(self.temp.name + "/library.sqlite3")
        reopened_db.row_factory = sqlite3.Row
        reopened = FileLibrary(reopened_db, self.temp.name)
        self.assertFalse((Path(self.temp.name) / "library" / (digest + ".stl")).exists())
        self.assertEqual(reopened_db.execute("SELECT count(*) FROM library_deletion_journal").fetchone()[0], 0)
        reopened_db.close()
        self.db = sqlite3.connect(self.temp.name + "/library.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def test_migration_does_not_resurrect_explicitly_deleted_generation(self):
        self.db.execute("ALTER TABLE jobs ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'")
        digest = self.archive(b"deleted")
        self.job(digest, status="done")
        job_id = self.db.execute("SELECT id FROM jobs WHERE digest=?", (digest,)).fetchone()[0]
        self.assertTrue(self.library.delete(digest))
        uploads = Path(self.temp.name) / "uploads"
        uploads.mkdir()
        (uploads / (job_id + ".stl")).write_bytes(b"deleted")
        self.library.migrate_jobs(self.temp.name)
        self.assertEqual(self.library.list(), [])

    def test_request_delete_receipt_is_idempotent_and_bound_to_digest(self):
        digest = self.archive()
        self.job(digest)
        first = self.library.request_delete(digest, "delete-request-0001")
        second = self.library.request_delete(digest, "delete-request-0001")
        self.assertEqual(first, second)
        self.assertEqual(second["status"], "completed")
        with self.assertRaises(ValueError):
            self.library.request_delete("0" * 64, "delete-request-0001")

    def test_old_delete_receipt_cannot_delete_rearchived_generation(self):
        digest = self.archive(b"generation")
        self.job(digest)
        old_generation = self.library.get(digest)["generation"]
        self.assertTrue(self.library.request_delete(digest, "delete-generation-0001")["deleted"])
        new_digest = self.archive(b"generation", "rearchived.stl")
        self.assertEqual(new_digest, digest)
        new_generation = self.library.get(digest)["generation"]
        self.assertNotEqual(old_generation, new_generation)
        receipt = self.library.request_delete(digest, "delete-generation-0001")
        self.assertEqual(receipt["status"], "completed")
        self.assertTrue((Path(self.temp.name) / "library" / (digest + ".stl")).exists())
        with self.assertRaises(ValueError):
            self.library.request_delete(digest, "delete-generation-0002", expected_generation=old_generation)

    def test_prune_only_deletes_explicit_selection_and_reports_blocked(self):
        removable = self.archive(b"removable")
        blocked = self.archive(b"blocked")
        self.job(removable)
        self.job(blocked, status="printing")
        result = self.library.prune([removable, blocked], request_id="prune-request-0001")
        self.assertEqual(result["deleted"], [removable])
        self.assertEqual([item["digest"] for item in result["blocked"]], [blocked])
        self.assertTrue((Path(self.temp.name) / "library" / (blocked + ".stl")).exists())

    def test_corrupt_hash_and_object_link_fail_closed(self):
        digest = self.archive()
        path = Path(self.temp.name) / "library" / (digest + ".stl")
        path.write_bytes(b"changed")
        with self.assertRaises(LibraryCorrupt):
            self.library.download(digest)
        with self.assertRaises(LibraryCorrupt):
            self.library.delete(digest)
        path.write_bytes(b"model")
        self.db.execute("UPDATE library_models SET object_name='outside.stl' WHERE digest=?", (digest,))
        self.db.commit()
        with self.assertRaises(LibraryCorrupt):
            self.library.get(digest)

    def test_full_disk_archive_cleans_temp_and_preserves_metadata(self):
        with patch("app.file_library.os.replace", side_effect=OSError(errno.ENOSPC, "full")):
            with self.assertRaises(LibraryStorageError):
                self.archive(b"full", "full.stl")
        self.assertEqual(self.db.execute("SELECT count(*) FROM library_models").fetchone()[0], 0)
        self.assertEqual(list((Path(self.temp.name) / "library").glob("*.tmp")), [])

    def test_configured_quota_is_enforced_before_new_object_write(self):
        self.library.set_retention_policy(quota_bytes=3)
        self.archive(b"one")
        with self.assertRaises(LibraryStorageError):
            self.archive(b"two")
        # A duplicate is still accepted because it consumes no new object.
        self.assertEqual(self.archive(b"one"), self.library.list()[0]["digest"])

    def test_retention_candidates_are_advisory_and_skip_active_jobs(self):
        old = self.archive(b"old", "old.stl", created=0)
        active = self.archive(b"active", "active.stl", created=0)
        self.job(active, status="printing")
        self.library.set_retention_policy(retention_days=1)
        candidates = self.library.retention_candidates(now=2 * 86400)
        self.assertEqual([item["digest"] for item in candidates], [old])
        self.assertTrue((Path(self.temp.name) / "library" / (old + ".stl")).exists())

    def test_concurrent_duplicate_archive_is_serialized(self):
        errors = []

        def worker(name):
            try:
                self.archive(b"same", name)
            except Exception as error:  # pragma: no cover - failure is asserted below
                errors.append(error)

        threads = [threading.Thread(target=worker, args=("one.stl",)),
                   threading.Thread(target=worker, args=("two.stl",))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.library.list()), 1)
        self.assertEqual(self.library.list()[0]["aliases"], ["two.stl"])

    def test_archive_commit_failure_compensates_only_uncommitted_new_object(self):
        original_commit = self.db.commit

        def before_commit_failure():
            raise sqlite3.OperationalError("commit failed before durable commit")

        with patch.object(self.library, "_commit", side_effect=before_commit_failure):
            with self.assertRaises(sqlite3.OperationalError):
                self.archive(b"before", "before.stl")
        digest = self.library.digest(b"before")
        self.assertFalse((Path(self.temp.name) / "library" / (digest + ".stl")).exists())
        self.assertIsNone(self.db.execute("SELECT 1 FROM library_models WHERE digest=?", (digest,)).fetchone())

        def after_commit_failure():
            original_commit()
            raise sqlite3.OperationalError("commit reported failure after durable commit")

        with patch.object(self.library, "_commit", side_effect=after_commit_failure):
            with self.assertRaises(sqlite3.OperationalError):
                self.archive(b"after", "after.stl")
        digest = self.library.digest(b"after")
        self.assertTrue((Path(self.temp.name) / "library" / (digest + ".stl")).exists())
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM library_models WHERE digest=?", (digest,)).fetchone())

    def test_legacy_orphan_is_registered_neutral_and_corrupt_one_is_explicitly_deletable(self):
        data = b"legacy orphan"
        digest = self.library.digest(data)
        path = Path(self.temp.name) / "library" / (digest + ".stl")
        path.write_bytes(data)
        self.library._reconcile_orphans()
        item = self.library.list_page(1, 25, include_jobs=False)["items"][0]
        self.assertEqual(item["filename"], "uidentificeret-model-" + digest[:12] + ".stl")
        self.assertEqual(item["integrity"], "present")

        corrupt_digest = "a" * 64
        corrupt_path = Path(self.temp.name) / "library" / (corrupt_digest + ".stl")
        corrupt_path.write_bytes(b"wrong bytes")
        self.library._reconcile_orphans()
        corrupt = next(item for item in self.library.list_page(1, 25, include_jobs=False)["items"]
                       if item["digest"] == corrupt_digest)
        with self.assertRaises(LibraryCorrupt):
            self.library.download(corrupt_digest)
        corrupt_path.unlink()
        corrupt = next(item for item in self.library.list_page(1, 25, include_jobs=False)["items"]
                       if item["digest"] == corrupt_digest)
        self.assertEqual(corrupt["integrity"], "corrupt")
        self.assertTrue(self.library.request_delete(corrupt_digest, "orphan-delete-0001")["deleted"])
        self.assertFalse(corrupt_path.exists())

    def test_migration_rejects_invalid_job_ids_and_upload_links(self):
        self.db.execute(
            "INSERT INTO jobs(id,digest,filename,status,workflow,workflow_complete,created) VALUES(?,?,?,?,?,?,?)",
            ("../outside", self.library.digest(b"outside"), "outside.stl", "done", None, 1, 1),
        )
        self.db.commit()
        outside = Path(self.temp.name).parent / "outside.stl"
        outside.write_bytes(b"outside")
        try:
            self.library.migrate_jobs(self.temp.name)
            self.assertEqual(self.library.list(), [])
        finally:
            outside.unlink(missing_ok=True)

    def test_migration_rejects_upload_directory_and_file_symlinks(self):
        job_id = uuid.uuid4().hex
        data = b"outside-link"
        digest = self.library.digest(data)
        self.db.execute(
            "INSERT INTO jobs(id,digest,filename,status,workflow,workflow_complete,created) VALUES(?,?,?,?,?,?,?)",
            (job_id, digest, "link.stl", "done", None, 1, 1),
        )
        self.db.commit()
        uploads = Path(self.temp.name) / "uploads"
        outside = Path(self.temp.name).parent / ("uploads-" + uuid.uuid4().hex)
        outside.mkdir()
        (outside / (job_id + ".stl")).write_bytes(data)
        try:
            try:
                uploads.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Symlinks are unavailable in this test environment")
            self.library.migrate_jobs(self.temp.name)
            self.assertEqual(self.library.list(), [])
            uploads.unlink()
            uploads.mkdir()
            (outside / (job_id + ".stl")).unlink()
            target = Path(self.temp.name).parent / ("outside-" + uuid.uuid4().hex + ".stl")
            target.write_bytes(data)
            (uploads / (job_id + ".stl")).symlink_to(target)
            self.library.migrate_jobs(self.temp.name)
            self.assertEqual(self.library.list(), [])
        finally:
            if uploads.is_symlink():
                uploads.unlink()
            elif uploads.exists():
                for item in uploads.iterdir():
                    item.unlink()
                uploads.rmdir()
            for item in (outside,):
                if item.exists():
                    for child in item.iterdir():
                        child.unlink()
                    item.rmdir()
            target = locals().get("target")
            if target is not None:
                target.unlink(missing_ok=True)

    def test_pending_deletion_is_visible_and_retryable_after_new_instance(self):
        digest = self.archive(b"pending")
        self.job(digest)
        with patch.object(self.library, "_unlink_object",
                          side_effect=LibraryDeletionPending("locked")):
            with self.assertRaises(LibraryDeletionPending):
                self.library.request_delete(digest, "pending-delete-0001")
        status = self.library.disk_status()
        self.assertEqual(status["pending_deletions_count"], 1)
        self.assertEqual(status["pending_deletions"][0]["request_id"], "pending-delete-0001")
        self.db.close()
        reopened_db = sqlite3.connect(self.temp.name + "/library.sqlite3", check_same_thread=False)
        reopened_db.row_factory = sqlite3.Row
        reopened = FileLibrary(reopened_db, self.temp.name)
        try:
            self.assertEqual(reopened.request_delete(digest, "pending-delete-0001")["status"], "completed")
            self.assertEqual(reopened.disk_status()["pending_deletions_count"], 0)
        finally:
            reopened_db.close()
        self.db = sqlite3.connect(self.temp.name + "/library.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def test_legacy_null_generation_pending_journal_gets_retryable_generation(self):
        digest = self.archive(b"legacy-generation")
        self.job(digest)
        object_name = digest + ".stl"
        self.db.execute(
            "INSERT INTO library_deletion_journal(operation_id,digest,object_name,state,generation,created) VALUES(?,?,?,?,?,?)",
            ("legacy-journal-0001", digest, object_name, "db_committed", None, 1),
        )
        self.db.execute(
            "INSERT INTO library_delete_receipts(request_id,digest,state,created,completed,generation) VALUES(?,?,?,?,?,?)",
            ("legacy-pending-0001", digest, "pending", 1, None, None),
        )
        self.db.commit()
        self.db.close()

        original_unlink = FileLibrary._unlink_object
        with patch.object(FileLibrary, "_unlink_object",
                          side_effect=LibraryDeletionPending("locked legacy object")):
            reopened_db = sqlite3.connect(self.temp.name + "/library.sqlite3", check_same_thread=False)
            reopened_db.row_factory = sqlite3.Row
            reopened = FileLibrary(reopened_db, self.temp.name)
        try:
            status = reopened.disk_status()
            self.assertEqual(status["pending_deletions_count"], 1)
            generation = status["pending_deletions"][0]["generation"]
            self.assertRegex(generation, r"^[a-f0-9]{32}$")
            receipt = reopened.db.execute(
                "SELECT generation FROM library_delete_receipts WHERE request_id=?",
                ("legacy-pending-0001",)).fetchone()
            self.assertEqual(receipt["generation"], generation)
            with patch.object(reopened, "_unlink_object", wraps=original_unlink.__get__(reopened, FileLibrary)):
                result = reopened.request_delete(
                    digest, "legacy-pending-0001", expected_generation=generation)
            self.assertTrue(result["deleted"])
            self.assertEqual(reopened.disk_status()["pending_deletions_count"], 0)
        finally:
            reopened_db.close()
        self.db = sqlite3.connect(self.temp.name + "/library.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def test_locked_deletion_does_not_block_unrelated_delete(self):
        first = self.archive(b"locked-first")
        second = self.archive(b"free-second")
        self.job(first)
        self.job(second)
        original_unlink = self.library._unlink_object

        def lock_first(path):
            if path.name == first + ".stl":
                raise LibraryDeletionPending("first is locked")
            return original_unlink(path)

        with patch.object(self.library, "_unlink_object", side_effect=lock_first):
            with self.assertRaises(LibraryDeletionPending):
                self.library.request_delete(first, "locked-delete-0001")
            self.assertTrue(self.library.request_delete(second, "free-delete-0001")["deleted"])
        self.assertEqual(self.library.disk_status()["pending_deletions_count"], 1)
        self.assertTrue((Path(self.temp.name) / "library" / (first + ".stl")).exists())

    def test_disk_status_is_cached_and_archive_invalidates_it(self):
        self.archive(b"cached")
        with patch.object(self.library, "_object_bytes", wraps=self.library._object_bytes) as measured:
            first = self.library.disk_status()
            second = self.library.disk_status()
            self.assertEqual(first["used_bytes"], second["used_bytes"])
            self.assertEqual(measured.call_count, 1)
            before_archive = measured.call_count
            self.archive(b"cache-invalidated")
            self.library.disk_status()
            self.assertEqual(measured.call_count, before_archive + 2)

    def test_archive_requires_sqlite_headroom(self):
        data = b"headroom"
        usage = type("Usage", (), {"free": ARCHIVE_FREE_HEADROOM + len(data) - 1,
                                    "total": 10**9, "used": 1})()
        with patch("app.file_library.shutil.disk_usage", return_value=usage):
            with self.assertRaises(LibraryStorageError):
                self.archive(data, "headroom.stl")

    def test_existing_object_size_is_rejected_before_unbounded_read(self):
        data = b"small-existing-payload"
        digest = self.library.digest(data)
        path = Path(self.temp.name) / "library" / (digest + ".stl")
        with path.open("wb") as handle:
            handle.truncate(MAX_MODEL_BYTES + 1)
        with self.assertRaises(LibraryCorrupt):
            self.library.archive(data, filename="existing.stl", colour=None,
                                 quality="standard", meta={}, created=1)

    def test_object_scan_has_bounded_file_count(self):
        class FakeObject:
            suffix = ".stl"

            def is_file(self):
                return True

            def stat(self):
                return type("Stat", (), {"st_size": 1})()

        fake = FakeObject()
        with patch("pathlib.Path.iterdir", return_value=[fake] * 10001):
            with patch.object(self.library, "_is_reparse", return_value=False):
                with self.assertRaises(LibraryStorageError):
                    self.library._object_bytes()

    def test_recovery_processes_bounded_journal_batch_and_reports_deferred_rows(self):
        rows = []
        for index in range(101):
            digest = format(index + 1, "064x")
            rows.append(("batch-journal-%03d" % index, digest, digest + ".stl",
                         "db_committed", uuid.uuid4().hex, index))
        self.db.executemany(
            "INSERT INTO library_deletion_journal(operation_id,digest,object_name,state,generation,created) VALUES(?,?,?,?,?,?)",
            rows)
        self.db.commit()
        completed = self.library.recover_pending_deletions()
        self.assertEqual(len(completed), 100)
        self.assertEqual(self.db.execute("SELECT count(*) FROM library_deletion_journal").fetchone()[0], 1)
        status = self.library.disk_status()
        self.assertEqual(status["pending_deletions_count"], 1)
        self.assertEqual(status["pending_deletions_deferred"], 0)

    def test_legacy_generation_backfill_and_ui_are_bounded(self):
        rows = []
        for index in range(101):
            digest = format(10_000 + index, "064x")
            rows.append(("legacy-bounded-%03d" % index, digest, digest + ".stl",
                         "db_committed", "", index))
        self.db.executemany(
            "INSERT INTO library_deletion_journal(operation_id,digest,object_name,state,generation,created) "
            "VALUES(?,?,?,?,?,?)", rows)
        self.db.commit()

        # The migration itself touches one bounded batch, rather than loading
        # all historical journal rows into a Python collection.
        self.assertEqual(self.library._backfill_deletion_generations(), 100)
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM library_deletion_journal WHERE generation=''"
        ).fetchone()[0], 1)
        self.db.commit()

        # Public status normalizes another bounded batch and never exposes a
        # NULL/invalid generation. The remaining row is explicitly deferred.
        status = self.library.disk_status()
        self.assertEqual(status["pending_deletions_count"], 101)
        self.assertEqual(len(status["pending_deletions"]), 100)
        self.assertEqual(status["pending_deletions_deferred"], 1)
        self.assertTrue(all(self.library._valid_generation(item["generation"])
                            for item in status["pending_deletions"]))

    def test_pending_normalization_does_not_wait_behind_old_model_history(self):
        models = []
        for index in range(101):
            digest = format(20_000 + index, "064x")
            models.append((digest, "legacy-%03d.stl" % index, None, "standard", "{}",
                           index, digest + ".stl", "[]", index, ""))
        self.db.executemany(
            "INSERT INTO library_models(digest,filename,colour,quality,meta,created,object_name,aliases,last_used,generation) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)", models)
        pending_digest = "e" * 64
        self.db.execute(
            "INSERT INTO library_deletion_journal(operation_id,digest,object_name,state,generation,created) "
            "VALUES(?,?,?,?,?,?)",
            ("pending-priority-0001", pending_digest, pending_digest + ".stl", "db_committed", "", 1),
        )
        self.db.commit()

        self.assertEqual(self.library._normalize_pending_generations(), 1)
        pending_generation = self.db.execute(
            "SELECT generation FROM library_deletion_journal WHERE operation_id=?",
            ("pending-priority-0001",)).fetchone()[0]
        self.assertTrue(self.library._valid_generation(pending_generation))
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM library_models WHERE generation=''"
        ).fetchone()[0], 101)


if __name__ == "__main__":
    unittest.main()
