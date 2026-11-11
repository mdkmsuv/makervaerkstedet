"""Persistent, content-addressed storage for validated STL models.

The print queue owns transient upload files. This module owns the durable
copy used by the teacher library. Deletion is explicit, journalled, and
resumable after a process interruption.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid


TERMINAL_STATUSES = frozenset(("done", "failed", "cancelled"))
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
DEFAULT_WARNING_RATIO = 0.80
DEFAULT_RETENTION_DAYS = 90
DEFAULT_QUOTA_BYTES = 1024 ** 3
ARCHIVE_FREE_HEADROOM = 32 * 1024 ** 2
DISK_STATUS_CACHE_SECONDS = 30.0
MAX_OBJECT_FILES = 10_000
MAX_ORPHAN_RECONCILE = 100
MAX_MODEL_BYTES = 25 * 1024 ** 2
MAX_STORED_ALIASES = 100
MAX_RECOVERY_BATCH = 100
MAX_RELATED_JOBS = 1000


class LibraryError(ValueError):
    """A safe, user-facing library operation error."""


class LibraryCorrupt(LibraryError):
    """A metadata row or content-addressed object failed integrity checks."""


class LibraryInUse(LibraryError):
    """A model is still referenced by an active queue/workflow job."""


class LibraryDeletionPending(LibraryError):
    """The metadata transition succeeded but object unlink needs a retry."""


class LibraryStorageError(LibraryError):
    """The library could not write or remove a filesystem object."""


class LibraryConflict(LibraryError):
    """A receipt or library revision conflicts with the requested change."""


class FileLibrary:
    """SQLite metadata plus immutable SHA-256 addressed STL objects."""

    def __init__(self, db, folder):
        self.db = db
        self.folder = Path(folder)
        self._assert_safe_directory(self.folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._assert_safe_directory(self.folder)
        self.objects = self.folder / "library"
        if self._is_reparse(self.objects):
            self._assert_safe_directory(self.objects)
        elif not self.objects.exists():
            self.objects.mkdir(parents=True, exist_ok=True)
        self._assert_safe_directory(self.objects)
        self._lock = threading.RLock()
        self._disk_status_cache = None
        self._disk_status_cache_at = 0.0
        self.db.create_function('library_fold', 1, lambda value: str(value or '').casefold(), deterministic=True)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS library_models (
                digest TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                colour TEXT,
                quality TEXT NOT NULL DEFAULT 'standard',
                meta TEXT NOT NULL,
                created REAL NOT NULL,
                object_name TEXT NOT NULL,
                aliases TEXT NOT NULL DEFAULT '[]',
                last_used REAL NOT NULL DEFAULT 0,
                generation TEXT NOT NULL
            )"""
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(library_models)")}
        if "aliases" not in columns:
            self.db.execute("ALTER TABLE library_models ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'")
        if "last_used" not in columns:
            self.db.execute("ALTER TABLE library_models ADD COLUMN last_used REAL NOT NULL DEFAULT 0")
        if "generation" not in columns:
            self.db.execute("ALTER TABLE library_models ADD COLUMN generation TEXT")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS library_deletion_journal (
                operation_id TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                object_name TEXT NOT NULL,
                state TEXT NOT NULL,
                generation TEXT,
                created REAL NOT NULL
            )"""
        )
        self.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS library_deletion_digest "
            "ON library_deletion_journal(digest)"
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS library_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS library_delete_receipts (
                request_id TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                state TEXT NOT NULL,
                created REAL NOT NULL,
                completed REAL,
                generation TEXT
            )"""
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS library_policy_receipts (
                request_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                policy TEXT NOT NULL,
                created REAL NOT NULL
            )"""
        )
        receipt_columns = {row[1] for row in self.db.execute("PRAGMA table_info(library_delete_receipts)")}
        if "generation" not in receipt_columns:
            self.db.execute("ALTER TABLE library_delete_receipts ADD COLUMN generation TEXT")
        journal_columns = {row[1] for row in self.db.execute("PRAGMA table_info(library_deletion_journal)")}
        if "generation" not in journal_columns:
            self.db.execute("ALTER TABLE library_deletion_journal ADD COLUMN generation TEXT")
        self._backfill_deletion_generations()
        # Old rows have no use timestamp. Their archive time is the safest
        # conservative estimate and never triggers deletion by itself.
        self.db.execute("UPDATE library_models SET last_used=created WHERE last_used=0")
        self._job_columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
        self.db.execute('CREATE INDEX IF NOT EXISTS library_jobs_digest_created ON jobs(digest,created)')
        self.db.commit()
        # A locked or full filesystem must not prevent the queue from
        # starting. The journal remains available for a later teacher retry.
        try:
            self.recover_pending_deletions()
        except LibraryDeletionPending:
            pass
        # A process crash can leave a durable object after its metadata
        # transaction failed. Preserve such legacy objects and expose them as
        # neutral teacher-deletable entries; never remove them implicitly.
        self._reconcile_orphans()

    @staticmethod
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _valid_digest(digest):
        return (isinstance(digest, str) and len(digest) == 64 and
                all(c in "0123456789abcdef" for c in digest))

    @staticmethod
    def _is_reparse(path):
        try:
            stat = path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)

    @classmethod
    def _assert_safe_directory(cls, path):
        parts = list(path.parents)[::-1] + [path]
        for candidate in parts:
            if cls._is_reparse(candidate):
                raise LibraryCorrupt("Bibliotekets mappe indeholder et usikkert link.")

    def _invalidate_disk_status(self):
        self._disk_status_cache = None
        self._disk_status_cache_at = 0.0

    @staticmethod
    def _valid_generation(value):
        return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{32}', value) is not None

    def _backfill_deletion_generations(self, limit=MAX_RECOVERY_BATCH):
        """Give legacy journals/receipts stable generation identities.

        A pending journal binds to the surviving model generation when one is
        available; otherwise it receives a new durable identity. Completed
        receipts never bind to a newly re-archived model merely because their
        old generation was absent. The migration is deliberately bounded so
        startup cost cannot grow with historical job volume.
        """
        if type(limit) is not int or limit < 1:
            return 0
        invalid = "(generation IS NULL OR length(generation) <> 32 OR generation GLOB '*[^0-9a-f]*')"
        changed = 0
        for row in self.db.execute(
                'SELECT digest FROM library_models WHERE ' + invalid + ' ORDER BY digest LIMIT ?',
                (limit,)).fetchall():
            self.db.execute('UPDATE library_models SET generation=? WHERE digest=?',
                            (uuid.uuid4().hex, row['digest']))
            changed += 1
        for row in self.db.execute(
                'SELECT operation_id,digest,state,generation FROM library_deletion_journal '
                'WHERE ' + invalid + ' ORDER BY created,operation_id LIMIT ?',
                (limit,)).fetchall():
            generation = row['generation']
            if not self._valid_generation(generation):
                model = self.db.execute(
                    'SELECT generation FROM library_models WHERE digest=? LIMIT 1',
                    (row['digest'],)).fetchone()
                generation = model['generation'] if model and self._valid_generation(model['generation']) else uuid.uuid4().hex
                self.db.execute('UPDATE library_deletion_journal SET generation=? WHERE operation_id=?',
                                (generation, row['operation_id']))
                changed += 1
        for row in self.db.execute(
                'SELECT request_id,digest,state,generation FROM library_delete_receipts '
                'WHERE ' + invalid + ' ORDER BY created,request_id LIMIT ?',
                (limit,)).fetchall():
            if self._valid_generation(row['generation']):
                continue
            if row['state'] == 'pending':
                journal = self.db.execute(
                    "SELECT generation FROM library_deletion_journal "
                    "WHERE digest=? AND " + invalid + " = 0 ORDER BY created,operation_id LIMIT 1",
                    (row['digest'],)).fetchone()
                model = self.db.execute(
                    'SELECT generation FROM library_models WHERE digest=? LIMIT 1',
                    (row['digest'],)).fetchone()
                generation = (journal['generation'] if journal else None)
                if not self._valid_generation(generation) and model:
                    generation = model['generation']
                if not self._valid_generation(generation):
                    generation = uuid.uuid4().hex
            else:
                # A completed legacy receipt is historical. Keep it distinct
                # from any model archived later under the same digest.
                generation = uuid.uuid4().hex
            self.db.execute('UPDATE library_delete_receipts SET generation=? WHERE request_id=?',
                            (generation, row['request_id']))
            changed += 1
        return changed

    def _normalize_pending_generations(self, digest=None):
        """Normalize only rows needed by recovery/status before returning."""
        invalid = "(generation IS NULL OR length(generation) <> 32 OR generation GLOB '*[^0-9a-f]*')"
        digest_clause = ""
        digest_params = ()
        if digest is not None:
            digest_clause = "digest=? AND "
            digest_params = (digest,)
        changed = 0
        for row in self.db.execute(
                'SELECT operation_id,digest FROM library_deletion_journal '
                'WHERE ' + digest_clause + invalid + ' ORDER BY created,operation_id LIMIT ?',
                digest_params + (MAX_RECOVERY_BATCH,)).fetchall():
            model = self.db.execute(
                'SELECT generation FROM library_models WHERE digest=? LIMIT 1',
                (row['digest'],)).fetchone()
            generation = model['generation'] if model and self._valid_generation(model['generation']) else uuid.uuid4().hex
            self.db.execute('UPDATE library_deletion_journal SET generation=? WHERE operation_id=?',
                            (generation, row['operation_id']))
            changed += 1
        for row in self.db.execute(
                'SELECT request_id,digest FROM library_delete_receipts '
                "WHERE state='pending' AND " + digest_clause + invalid + " ORDER BY created,request_id LIMIT ?",
                digest_params + (MAX_RECOVERY_BATCH,)).fetchall():
            journal = self.db.execute(
                "SELECT generation FROM library_deletion_journal "
                "WHERE digest=? AND " + invalid + " = 0 ORDER BY created,operation_id LIMIT 1",
                (row['digest'],)).fetchone()
            model = self.db.execute(
                'SELECT generation FROM library_models WHERE digest=? LIMIT 1',
                (row['digest'],)).fetchone()
            generation = journal['generation'] if journal else None
            if not self._valid_generation(generation) and model:
                generation = model['generation']
            if not self._valid_generation(generation):
                generation = uuid.uuid4().hex
            self.db.execute('UPDATE library_delete_receipts SET generation=? WHERE request_id=?',
                            (generation, row['request_id']))
            changed += 1
        if changed:
            self.db.commit()
        return changed

    def _commit(self):
        self.db.commit()

    def _pending_digests(self):
        return {row[0] for row in self.db.execute(
            "SELECT digest FROM library_deletion_journal").fetchall()}

    def _reconcile_orphans(self):
        """Register at most a bounded batch of safe, digest-named orphans.

        An orphan is evidence of an older interrupted archive, so it is
        retained for teacher review.  Reparse points and files with names that
        cannot be content-addressed stay untouched and are never followed.
        Pending deletion journals are deliberately left to recovery.
        """
        with self._lock:
            self._assert_safe_directory(self.objects)
            pending = self._pending_digests()
            try:
                candidates = sorted(self.objects.iterdir(), key=lambda item: item.name)
            except OSError:
                return
            registered = 0
            for path in candidates:
                if registered >= MAX_ORPHAN_RECONCILE:
                    break
                if path.suffix.lower() != ".stl":
                    continue
                if self._is_reparse(path) or not path.is_file():
                    # Preserve but reject links/non-files. A later explicit
                    # operator action can remove them after review.
                    continue
                stem = path.name[:-4]
                if not self._valid_digest(stem) or path.name != stem + ".stl":
                    continue
                if stem in pending:
                    continue
                if self.db.execute("SELECT 1 FROM library_models WHERE digest=?", (stem,)).fetchone():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                neutral = "uidentificeret-model-" + stem[:12] + ".stl"
                self.db.execute(
                    "INSERT INTO library_models(digest,filename,colour,quality,meta,created,object_name,aliases,last_used,generation) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (stem, neutral, None, "standard", "{}", stat.st_mtime,
                     path.name, "[]", stat.st_mtime, uuid.uuid4().hex),
                )
                registered += 1
            if registered:
                self._commit()

    def _path(self, digest):
        if not self._valid_digest(digest):
            raise LibraryError("Modellen blev ikke fundet.")
        return self.objects / (digest + ".stl")

    def _row_path(self, row):
        self._assert_safe_directory(self.objects)
        path = self._path(row["digest"])
        if row["object_name"] != path.name:
            raise LibraryCorrupt("Bibliotekets objektlink er beskadiget.")
        try:
            if path.resolve(strict=False).parent != self.objects.resolve(strict=False):
                raise LibraryCorrupt("Bibliotekets objektlink peger uden for biblioteket.")
        except OSError as error:
            raise LibraryCorrupt("Bibliotekets objektlink kunne ikke kontrolleres.") from error
        if self._is_reparse(path) or not path.is_file():
            raise LibraryCorrupt("Den arkiverede model mangler eller er ikke en almindelig fil.")
        return path

    @staticmethod
    def _json_value(value, fallback):
        try:
            result = json.loads(value or "")
        except (TypeError, ValueError):
            raise LibraryCorrupt("Bibliotekets metadata er beskadiget.") from None
        if not isinstance(result, type(fallback)):
            raise LibraryCorrupt("Bibliotekets metadata er beskadiget.")
        return result

    def _touch(self, digest, now=None):
        now = time.time() if now is None else float(now)
        self.db.execute("UPDATE library_models SET last_used=? WHERE digest=?", (now, digest))
        self.db.commit()

    def _verify_path(self, row):
        path = self._row_path(row)
        actual, _size = self._stream_digest(path)
        if actual != row["digest"]:
            raise LibraryCorrupt("Det arkiverede model er beskadiget.")
        return path

    @staticmethod
    def _stream_digest(path):
        try:
            if path.stat().st_size > MAX_MODEL_BYTES:
                raise LibraryCorrupt('Modellen er for stor til at blive hentet. Vælg sletning i biblioteket.')
            digest, size = hashlib.sha256(), 0
            with path.open('rb') as source:
                for chunk in iter(lambda: source.read(64 * 1024), b''):
                    size += len(chunk)
                    if size > MAX_MODEL_BYTES:
                        raise LibraryCorrupt('Modellen er for stor til at blive hentet. Vælg sletning i biblioteket.')
                    digest.update(chunk)
            return digest.hexdigest(), size
        except OSError as error:
            raise LibraryCorrupt("Den arkiverede model kunne ikke læses.") from error

    def archive(self, data, *, filename, colour, quality, meta, created, digest=None,
                enforce_quota=True):
        """Atomically persist *data* and metadata, deduplicated by digest."""
        actual_digest = self.digest(data)
        if digest is not None and digest != actual_digest:
            raise LibraryCorrupt("Det arkiverede model har en forkert hash.")
        digest = actual_digest
        path = self._path(digest)
        with self._lock:
            self._assert_safe_directory(self.objects)
            created_new_object = False
            if path.exists():
                if self._is_reparse(path) or not path.is_file():
                    raise LibraryCorrupt("Det arkiverede model er beskadiget.")
                try:
                    actual, _size = self._stream_digest(path)
                    if actual != digest:
                        raise LibraryCorrupt("Det arkiverede model er beskadiget.")
                except OSError as error:
                    raise LibraryCorrupt("Det arkiverede model kunne ikke læses.") from error
            else:
                if enforce_quota:
                    policy = self.get_retention_policy()
                    try:
                        used = self._object_bytes()
                    except LibraryStorageError:
                        self._invalidate_disk_status()
                        raise
                    quota = policy["quota_bytes"]
                    if quota is not None and used + len(data) > quota:
                        self._invalidate_disk_status()
                        raise LibraryStorageError("Biblioteksloftet er nået. Vælg en model, der skal slettes først.")
                    try:
                        if shutil.disk_usage(self.folder).free < len(data) + ARCHIVE_FREE_HEADROOM:
                            self._invalidate_disk_status()
                            raise LibraryStorageError("Disken er fuld. Vælg en model, der skal slettes først.")
                    except OSError as error:
                        self._invalidate_disk_status()
                        raise LibraryStorageError("Bibliotekets diskplads kunne ikke kontrolleres.") from error
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                            mode="wb", dir=self.objects, prefix=digest + ".", suffix=".tmp", delete=False) as stream:
                        temp_path = Path(stream.name)
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp_path, path)
                    created_new_object = True
                except OSError as error:
                    if temp_path is not None:
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass
                    raise LibraryStorageError("Biblioteket kunne ikke gemme modellen. Kontrollér diskpladsen.") from error
            try:
                row = self.db.execute("SELECT * FROM library_models WHERE digest=?", (digest,)).fetchone()
                if row is None:
                    self.db.execute(
                        "INSERT INTO library_models(digest,filename,colour,quality,meta,created,object_name,aliases,last_used,generation) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (digest, str(filename), colour, quality, json.dumps(meta), created, path.name, "[]", created, uuid.uuid4().hex),
                    )
                else:
                    aliases = self._json_value(row["aliases"], [])
                    last_used = max(float(row["last_used"] or 0), float(created))
                    if filename != row["filename"] and filename not in aliases:
                        aliases.append(str(filename))
                    aliases = aliases[-MAX_STORED_ALIASES:]
                    self.db.execute("UPDATE library_models SET aliases=?, last_used=? WHERE digest=?",
                                    (json.dumps(aliases), last_used, digest))
                self._commit()
            except Exception:
                # If SQLite did not commit the metadata, remove only the
                # object created by this call. Existing/legacy objects must
                # remain available for reconciliation and teacher review.
                self.db.rollback()
                committed = self.db.execute(
                    "SELECT 1 FROM library_models WHERE digest=?", (digest,)
                ).fetchone()
                if created_new_object and committed is None:
                    try:
                        self._unlink_object(path)
                    except (LibraryDeletionPending, LibraryCorrupt):
                        pass
                raise
            self._invalidate_disk_status()
            return digest

    def migrate_jobs(self, jobs_folder):
        """Archive surviving upload files for every historical queue row."""
        base = Path(jobs_folder)
        self._assert_safe_directory(base)
        uploads = base / "uploads"
        if not uploads.exists() or self._is_reparse(uploads) or not uploads.is_dir():
            return
        self._assert_safe_directory(uploads)
        for row in self.db.execute("SELECT * FROM jobs").fetchall():
            job_id = row["id"]
            if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
                continue
            source = uploads / (job_id + ".stl")
            if self._is_reparse(source) or not source.is_file():
                continue
            try:
                if source.resolve(strict=True).parent != uploads.resolve(strict=True):
                    continue
            except OSError:
                continue
            data = source.read_bytes()
            digest = self.digest(data)
            if digest != row["digest"]:
                continue
            existing = self.db.execute("SELECT 1 FROM library_models WHERE digest=?", (digest,)).fetchone()
            deleted = self.db.execute(
                "SELECT 1 FROM library_delete_receipts WHERE digest=? AND state='completed' LIMIT 1", (digest,)
            ).fetchone()
            # A surviving transient upload from before an explicit teacher
            # deletion must not resurrect that deleted library generation.
            if existing is None and deleted is not None:
                continue
            self.archive(data, filename=row["filename"], colour=row["colour"],
                         quality=row["quality"], meta=json.loads(row["meta"]),
                         created=row["created"], digest=digest, enforce_quota=False)

    @staticmethod
    def _page_args(page, page_size):
        if type(page) is not int or not 1 <= page <= 1_000_000:
            raise ValueError("Bibliotekssiden er ugyldig.")
        if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError("Bibliotekssiden er ugyldig.")
        return page, page_size

    def _item(self, row, *, include_jobs=True, job_limit=10):
        item = dict(row)
        item["meta"] = self._json_value(item["meta"], {})
        item["aliases"] = self._json_value(item.get("aliases") or "[]", [])
        item['alias_total'] = len(item['aliases'])
        item['aliases'] = item['aliases'][-20:]
        policy = self.get_retention_policy()
        item['expired'] = (policy['retention_days'] is not None and
                           float(item['last_used']) <= time.time() - policy['retention_days'] * 86400)
        item['in_use'] = self._has_active_jobs(item['digest'])
        path = self._path(item["digest"])
        try:
            self._assert_safe_directory(self.objects)
            if row["object_name"] != path.name or self._is_reparse(path) or not path.is_file():
                raise LibraryCorrupt("Bibliotekets objektlink er beskadiget.")
            size = path.stat().st_size
        except (LibraryCorrupt, OSError):
            item["size"] = 0
            item["integrity"] = "corrupt"
        else:
            item["size"] = size
            # Listing is intentionally bounded: full SHA-256 verification is
            # reserved for download, reprint and explicit deletion.
            item["integrity"] = "present"
        if include_jobs:
            jobs = self.db.execute(
                "SELECT id,filename,status,quality,colour FROM jobs WHERE digest=? ORDER BY created DESC, rowid DESC LIMIT ?",
                (item["digest"], job_limit),
            ).fetchall()
            item["job_total"] = self.db.execute(
                "SELECT count(*) FROM jobs WHERE digest=?", (item["digest"],)
            ).fetchone()[0] if self._job_columns else 0
            item["jobs"] = [dict(job) for job in jobs]
            item["latest_job_id"] = jobs[0]["id"] if jobs else None
            item["latest_status"] = jobs[0]["status"] if jobs else None
        return item

    @staticmethod
    def _search_pattern(query):
        if not isinstance(query, str) or len(query) > 120:
            raise ValueError("Bibliotekssøgningen er ugyldig.")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "%" + escaped.casefold() + "%"

    def list_page(self, page=1, page_size=DEFAULT_PAGE_SIZE, *, include_jobs=True, query=""):
        """Return one bounded library page and continuation metadata."""
        page, page_size = self._page_args(page, page_size)
        pattern = self._search_pattern(query)
        offset = (page - 1) * page_size
        with self._lock:
            where = ""
            params = []
            if query:
                where = ("WHERE library_fold(filename) LIKE ? ESCAPE '\\' OR EXISTS "
                         "(SELECT 1 FROM json_each(library_models.aliases) "
                         "WHERE library_fold(value) LIKE ? ESCAPE '\\')")
                params.extend((pattern, pattern))
            rows = self.db.execute(
                "SELECT * FROM library_models " + where + " ORDER BY created DESC, digest LIMIT ? OFFSET ?",
                (*params, page_size + 1, offset),
            ).fetchall()
            has_more = len(rows) > page_size
            items = [self._item(row, include_jobs=include_jobs) for row in rows[:page_size]]
            total = self.db.execute("SELECT count(*) FROM library_models " + where, tuple(params)).fetchone()[0]
        return {"items": items, "page": page, "page_size": page_size,
                "has_more": has_more, "total": total, "query": query}

    def list(self, page=1, page_size=DEFAULT_PAGE_SIZE, *, query=""):
        """Compatibility list, bounded to one page; use list_page for metadata."""
        return self.list_page(page, page_size, query=query)["items"]

    def get(self, digest):
        with self._lock:
            row = self.db.execute("SELECT * FROM library_models WHERE digest=?", (digest,)).fetchone()
            if row is None:
                raise LibraryError("Modellen blev ikke fundet.")
            self._verify_path(row)
            self._json_value(row["meta"], {})
            self._json_value(row["aliases"] or "[]", [])
            self._touch(digest)
            return dict(row)

    def download(self, digest):
        with self._lock:
            row = self.db.execute("SELECT * FROM library_models WHERE digest=?", (digest,)).fetchone()
            if row is None:
                raise LibraryError("Modellen blev ikke fundet.")
            path = self._verify_path(row)
            try:
                with path.open('rb') as source:
                    data = source.read(MAX_MODEL_BYTES + 1)
            except OSError as error:
                raise LibraryCorrupt("Det arkiverede model kunne ikke læses.") from error
            if len(data) > MAX_MODEL_BYTES or self.digest(data) != digest:
                raise LibraryCorrupt("Det arkiverede model er beskadiget.")
            self._touch(digest)
            return data

    def get_retention_policy(self):
        tables = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('settings','library_settings')")}
        row = None
        if "settings" in tables:
            row = self.db.execute("SELECT value FROM settings WHERE key='library_retention_policy'").fetchone()
        if row is None and "library_settings" in tables:
            row = self.db.execute("SELECT value FROM library_settings WHERE key='retention_policy'").fetchone()
        if row is None:
            return {"retention_days": DEFAULT_RETENTION_DAYS, "quota_bytes": DEFAULT_QUOTA_BYTES,
                    "warning_ratio": DEFAULT_WARNING_RATIO, "version": 0}
        try:
            policy = json.loads(row[0])
        except (TypeError, ValueError):
            raise LibraryCorrupt("Bibliotekets opbevaringspolitik er beskadiget.") from None
        if not isinstance(policy, dict):
            raise LibraryCorrupt("Bibliotekets opbevaringspolitik er beskadiget.")
        default = {"retention_days": DEFAULT_RETENTION_DAYS, "quota_bytes": DEFAULT_QUOTA_BYTES,
                   "warning_ratio": DEFAULT_WARNING_RATIO, "version": 0}
        default.update(policy)
        if (set(default) != {'retention_days', 'quota_bytes', 'warning_ratio', 'version'}
                or type(default['version']) is not int or default['version'] < 0
                or (default['retention_days'] is not None and
                    (type(default['retention_days']) is not int or default['retention_days'] < 0))
                or (default['quota_bytes'] is not None and
                    (type(default['quota_bytes']) is not int or default['quota_bytes'] <= 0))
                or type(default['warning_ratio']) not in (int, float)
                or not 0 < default['warning_ratio'] <= 1):
            raise LibraryCorrupt('Bibliotekets opbevaringspolitik er beskadiget.')
        return default

    def set_retention_policy(self, *, retention_days=None, quota_bytes=None,
                             warning_ratio=DEFAULT_WARNING_RATIO, expected_version=None,
                             request_id=None):
        if retention_days is not None and (type(retention_days) is not int or retention_days < 0):
            raise ValueError("Opbevaringsperioden er ugyldig.")
        if quota_bytes is not None and (type(quota_bytes) is not int or quota_bytes <= 0):
            raise ValueError("Biblioteksloftet er ugyldigt.")
        if type(warning_ratio) not in (int, float) or not 0 < warning_ratio <= 1:
            raise ValueError("Advarselsgrænsen er ugyldig.")
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise ValueError("Bibliotekspolitikkens version er ugyldig.")
        if request_id is not None:
            request_id = self._request_id(request_id)
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                current = self.get_retention_policy()
                if request_id is not None:
                    receipt = self.db.execute(
                        "SELECT policy FROM library_policy_receipts WHERE request_id=?", (request_id,)
                    ).fetchone()
                    if receipt is not None:
                        try:
                            policy = json.loads(receipt["policy"])
                        except (TypeError, ValueError):
                            raise LibraryCorrupt("Bibliotekets politik-kvittering er beskadiget.") from None
                        if (policy.get('retention_days') != retention_days
                                or policy.get('quota_bytes') != quota_bytes
                                or policy.get('warning_ratio') != float(warning_ratio)
                                or (expected_version is not None and
                                    policy.get('version') != expected_version + 1)):
                            raise LibraryConflict('Request-ID er brugt til en anden opbevaringsregel.')
                        self.db.rollback()
                        return policy
                if expected_version is not None and expected_version != current["version"]:
                    raise LibraryConflict("Bibliotekspolitikken er ændret. Hent den igen.")
                policy = {"retention_days": retention_days, "quota_bytes": quota_bytes,
                          "warning_ratio": float(warning_ratio), "version": current["version"] + 1}
                value = json.dumps(policy)
                tables = {row[0] for row in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('settings','library_settings')")}
                if "settings" in tables:
                    self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('library_retention_policy',?)",
                                    (value,))
                else:
                    self.db.execute("INSERT OR REPLACE INTO library_settings(key,value) VALUES('retention_policy',?)",
                                    (value,))
                if request_id is not None:
                    self.db.execute(
                        "INSERT INTO library_policy_receipts(request_id,version,policy,created) VALUES(?,?,?,?)",
                        (request_id, policy["version"], value, time.time()),
                    )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self._invalidate_disk_status()
            return policy

    def disk_status(self):
        with self._lock:
            # Normalize only a bounded batch before exposing generations to
            # the teacher UI. Remaining legacy rows are reported as deferred.
            self._normalize_pending_generations()
            now = time.monotonic()
            if (self._disk_status_cache is not None and
                    now - self._disk_status_cache_at < DISK_STATUS_CACHE_SECONDS):
                return dict(self._disk_status_cache,
                            pending_deletions=list(self._disk_status_cache["pending_deletions"]))
            policy = self.get_retention_policy()
            used, count = self._object_bytes(with_count=True)
            disk = shutil.disk_usage(self.folder)
            quota = policy["quota_bytes"] or disk.total
            ratio = (float(used) / quota) if quota else 0.0
            pending = self.db.execute(
                """SELECT j.digest,j.generation,
                          COALESCE(r.request_id,j.operation_id) AS request_id
                     FROM library_deletion_journal AS j
                LEFT JOIN library_delete_receipts AS r
                       ON r.digest=j.digest AND r.generation=j.generation
                      AND r.state='pending'
                    WHERE length(j.generation)=32
                      AND j.generation NOT GLOB '*[^0-9a-f]*'
                 ORDER BY j.created,j.operation_id LIMIT ?""", (MAX_ORPHAN_RECONCILE,)
            ).fetchall()
            pending_items = [{"digest": row["digest"], "generation": row["generation"],
                              "request_id": row["request_id"]} for row in pending]
            pending_count = self.db.execute(
                "SELECT count(*) FROM library_deletion_journal"
            ).fetchone()[0]
            result = {"used_bytes": used, "object_count": count, "free_bytes": disk.free,
                      "disk_total_bytes": disk.total, "quota_bytes": quota,
                      "quota_used_ratio": ratio, "warning": ratio >= policy["warning_ratio"],
                      "pending_deletions": pending_items,
                      "pending_deletions_count": pending_count,
                      "pending_deletions_deferred": max(0, pending_count - len(pending_items))}
            self._disk_status_cache = dict(result)
            self._disk_status_cache_at = now
            return dict(result, pending_deletions=list(pending_items))

    def _object_bytes(self, *, with_count=False):
        used = 0
        count = 0
        self._assert_safe_directory(self.objects)
        for path in self.objects.iterdir():
            if path.suffix.lower() != ".stl":
                continue
            if self._is_reparse(path) or not path.is_file():
                continue
            count += 1
            if count > MAX_OBJECT_FILES:
                raise LibraryStorageError("Biblioteket indeholder for mange modeller til sikker lagerkontrol.")
            try:
                used += path.stat().st_size
            except OSError:
                continue
        return (used, count) if with_count else used

    def _has_active_jobs(self, digest):
        if not self._job_columns:
            return False
        conditions = ["status NOT IN ('done','failed','cancelled')",
                      '(workflow IS NOT NULL AND workflow_complete=0)']
        if 'isolated' in self._job_columns:
            conditions.append('(isolated<>0 AND physical_resolved=0)' if 'physical_resolved' in self._job_columns
                              else 'isolated<>0')
        if self.db.execute('SELECT 1 FROM jobs WHERE digest=? AND (' + ' OR '.join(conditions) + ') LIMIT 1',
                           (digest,)).fetchone():
            return True
        # Inspect only upload paths owned by this digest. Never enumerate the
        # whole upload directory: unrelated files may be operator evidence or
        # an attacker-controlled link. Excessive history fails closed.
        uploads = self.folder / 'uploads'
        if not uploads.exists():
            return False
        self._assert_safe_directory(uploads)
        rows = self.db.execute(
            'SELECT id FROM jobs WHERE digest=? ORDER BY created LIMIT ?',
            (digest, MAX_RELATED_JOBS + 1)).fetchall()
        if len(rows) > MAX_RELATED_JOBS:
            return True
        for row in rows:
            job_id = row['id']
            if not isinstance(job_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', job_id) is None:
                # Never construct a path from traversal-like legacy ids;
                # fail closed because ownership of a possible upload is
                # unknowable.
                return True
            path = uploads / (job_id + '.stl')
            try:
                if self._is_reparse(path):
                    return True
                # lstat is deliberately no-follow. Any present owned upload
                # protects the model; inspecting type or size is unnecessary.
                path.lstat()
                return True
            except FileNotFoundError:
                continue
            except OSError:
                return True
        return False

    def retention_candidates(self, *, now=None, limit=DEFAULT_PAGE_SIZE):
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("Retentionlisten er ugyldig.")
        days = self.get_retention_policy()["retention_days"]
        if days is None:
            return []
        now = time.time() if now is None else float(now)
        cutoff = now - days * 86400
        rows = self.db.execute(
            "SELECT * FROM library_models WHERE last_used <= ? ORDER BY last_used, digest LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        return [self._item(row, include_jobs=False) for row in rows if not self._has_active_jobs(row["digest"])]

    def _unlink_object(self, path):
        self._assert_safe_directory(self.objects)
        if self._is_reparse(path):
            raise LibraryDeletionPending("Bibliotekets objektlink er ændret. Sletningen skal vurderes igen.")
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as error:
            raise LibraryDeletionPending("Bibliotekets sletning skal prøves igen.") from error

    def _finish_journal(self, operation_id):
        self.db.execute("DELETE FROM library_deletion_journal WHERE operation_id=?", (operation_id,))
        self.db.commit()

    def recover_pending_deletions(self, digest=None):
        """Resume DB-complete deletions; safe to call repeatedly.

        Recovery is independently bounded by journal rows. A locked object
        remains pending while unrelated deletions continue, so one bad file
        cannot block the whole teacher library.
        """
        with self._lock:
            if digest is not None and not self._valid_digest(digest):
                raise LibraryError("Modellen blev ikke fundet.")
            self._normalize_pending_generations(digest=digest)
            if digest is None:
                rows = self.db.execute(
                    "SELECT * FROM library_deletion_journal ORDER BY created, operation_id LIMIT ?",
                    (MAX_RECOVERY_BATCH,)).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM library_deletion_journal WHERE digest=? ORDER BY created, operation_id LIMIT ?",
                    (digest, MAX_RECOVERY_BATCH)).fetchall()
            completed = []
            pending_error = None
            for journal in rows:
                try:
                    row = self.db.execute("SELECT * FROM library_models WHERE digest=?", (journal["digest"],)).fetchone()
                    if journal["state"] == "prepared":
                        if row is not None:
                            # A prepared journal with a surviving row never
                            # crossed the DB deletion barrier; discard only
                            # the journal, never the object.
                            self.db.execute("DELETE FROM library_deletion_journal WHERE operation_id=?",
                                            (journal["operation_id"],))
                        else:
                            # Normalize an interrupted legacy journal before
                            # any filesystem mutation.
                            self.db.execute("UPDATE library_deletion_journal SET state='db_committed' WHERE operation_id=?",
                                            (journal["operation_id"],))
                        self.db.commit()
                        if row is not None:
                            self._invalidate_disk_status()
                            continue
                        journal = self.db.execute(
                            "SELECT * FROM library_deletion_journal WHERE operation_id=?",
                            (journal["operation_id"],)
                        ).fetchone()
                    # The same bytes may be archived again after a completed
                    # DB phase. Never let an old journal unlink a new incarnation.
                    if row is not None and (journal["generation"] is None or journal["generation"] != row["generation"]):
                        self._finish_journal(journal["operation_id"])
                        self._invalidate_disk_status()
                        continue
                    self._unlink_object(self._path(journal["digest"]))
                    self._finish_journal(journal["operation_id"])
                    self._invalidate_disk_status()
                    completed.append(journal["digest"])
                except LibraryDeletionPending as error:
                    if pending_error is None:
                        pending_error = error
                    continue
            if pending_error is not None:
                raise pending_error
            return completed

    def delete(self, digest, *, expected_generation=None, allow_corrupt=False):
        """Explicitly remove one unused model and anonymize terminal history."""
        if not self._valid_digest(digest):
            raise LibraryError("Modellen blev ikke fundet.")
        with self._lock:
            # Do not let an unrelated locked deletion block this model.
            self.recover_pending_deletions(digest=digest)
            row = self.db.execute("SELECT * FROM library_models WHERE digest=?", (digest,)).fetchone()
            if row is None:
                return False
            if expected_generation is not None and row["generation"] != expected_generation:
                raise LibraryError("Biblioteksversionen er ændret. Hent biblioteket igen.")
            if allow_corrupt:
                # An explicit teacher delete can remove a missing/corrupt
                # object, but only at the exact owned name inside this folder.
                self._assert_safe_directory(self.objects)
                path = self._path(digest)
                if (row['object_name'] != path.name or self._is_reparse(path)
                        or (path.exists() and not path.is_file())):
                    raise LibraryCorrupt('Bibliotekets objektlink er beskadiget.')
            else:
                path = self._verify_path(row)
            if self._has_active_jobs(digest):
                raise LibraryInUse("Modellen bruges stadig af printkøen eller et workflow.")
            existing = self.db.execute(
                "SELECT operation_id FROM library_deletion_journal WHERE digest=?", (digest,)
            ).fetchone()
            operation_id = existing["operation_id"] if existing else uuid.uuid4().hex
            if existing is None:
                self.db.execute(
                    "INSERT INTO library_deletion_journal(operation_id,digest,object_name,state,generation,created) VALUES(?,?,?,?,?,?)",
                    (operation_id, digest, path.name, "prepared", row["generation"], time.time()),
                )
                self.db.commit()
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute("SELECT * FROM library_models WHERE digest=?", (digest,)).fetchone()
                if row is None:
                    self.db.rollback()
                    self.recover_pending_deletions(digest=digest)
                    return False
                if expected_generation is not None and row["generation"] != expected_generation:
                    raise LibraryError("Biblioteksversionen er ændret. Hent biblioteket igen.")
                if self._has_active_jobs(digest):
                    raise LibraryInUse("Modellen bruges stadig af printkøen eller et workflow.")
                anonymous = "slettet-model-" + digest[:12] + ".stl"
                self.db.execute(
                    "UPDATE jobs SET filename=? WHERE digest=? AND status IN ('done','failed','cancelled')",
                    (anonymous, digest),
                )
                self.db.execute("DELETE FROM library_models WHERE digest=?", (digest,))
                self.db.execute(
                    "UPDATE library_deletion_journal SET state='db_committed' WHERE operation_id=?",
                    (operation_id,),
                )
                # Low-level callers without an HTTP request id still need a
                # durable tombstone so startup migration cannot resurrect a
                # deliberately removed model from a stale upload file.
                if self.db.execute(
                        "SELECT 1 FROM library_delete_receipts WHERE digest=? AND generation=?",
                        (digest, row["generation"])).fetchone() is None:
                    completed = time.time()
                    self.db.execute(
                        "INSERT INTO library_delete_receipts(request_id,digest,state,created,completed,generation) VALUES(?,?,?,?,?,?)",
                        ("delete-" + operation_id, digest, "completed", completed, completed, row["generation"]),
                    )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            # The durable journal/row transition changes storage state even
            # when the physical unlink below needs a later retry.
            self._invalidate_disk_status()
            self._unlink_object(path)
            self._finish_journal(operation_id)
            self._invalidate_disk_status()
            return True

    @staticmethod
    def _request_id(request_id):
        if not isinstance(request_id, str) or not 16 <= len(request_id) <= 80:
            raise ValueError("Sletningskvitteringen er ugyldig.")
        if not all(char.isalnum() or char == '-' for char in request_id):
            raise ValueError("Sletningskvitteringen er ugyldig.")
        return request_id

    def request_delete(self, digest, request_id, *, expected_generation=None):
        """Idempotent teacher delete with a durable request receipt."""
        if not self._valid_digest(digest):
            raise LibraryError("Modellen blev ikke fundet.")
        request_id = self._request_id(request_id)
        with self._lock:
            current = self.db.execute("SELECT generation FROM library_models WHERE digest=?", (digest,)).fetchone()
            receipt = self.db.execute(
                "SELECT * FROM library_delete_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            if receipt is not None:
                if receipt["digest"] != digest:
                    raise LibraryConflict("Sletningskvitteringen er allerede brugt til en anden model.")
                if expected_generation is not None and receipt['generation'] != expected_generation:
                    raise LibraryConflict('Request-ID er brugt til en anden modelversion.')
                if receipt["state"] == "completed":
                    return {"request_id": request_id, "digest": digest, "status": "completed",
                            "deleted": receipt['generation'] is not None}
                if current is not None and receipt["generation"] not in (None, current["generation"]):
                    raise LibraryError("Sletningskvitteringen tilhører en tidligere modelversion.")
                generation = receipt["generation"]
            else:
                if current is None:
                    journal = self.db.execute(
                        "SELECT generation FROM library_deletion_journal WHERE digest=?",
                        (digest,)).fetchone()
                    if journal is None:
                        self.db.execute(
                            "INSERT INTO library_delete_receipts(request_id,digest,state,created,generation) VALUES(?,?,?,?,?)",
                            (request_id, digest, "completed", time.time(), None),
                        )
                        self.db.commit()
                        return {"request_id": request_id, "digest": digest,
                                "status": "completed", "deleted": False}
                    generation = journal["generation"]
                    if expected_generation is not None and expected_generation != generation:
                        raise LibraryConflict("Biblioteksversionen er ændret. Hent biblioteket igen.")
                else:
                    generation = expected_generation or current["generation"]
                    if generation != current["generation"]:
                        raise LibraryConflict("Biblioteksversionen er ændret. Hent biblioteket igen.")
                self.db.execute(
                    "INSERT INTO library_delete_receipts(request_id,digest,state,created,generation) VALUES(?,?,?,?,?)",
                    (request_id, digest, "pending", time.time(), generation),
                )
                self.db.commit()
            try:
                if current is None:
                    # The DB phase already removed the model. Recover only
                    # this digest so another locked deletion cannot interfere
                    # with the request receipt.
                    self.recover_pending_deletions(digest=digest)
                    deleted = self.db.execute(
                        "SELECT 1 FROM library_deletion_journal WHERE digest=?", (digest,)
                    ).fetchone() is None
                else:
                    deleted = self.delete(digest, expected_generation=generation, allow_corrupt=True)
            except Exception:
                # Pending receipts intentionally survive an in-use or
                # interrupted operation so the same request can be retried.
                raise
            self.db.execute(
                "UPDATE library_delete_receipts SET state='completed', completed=? WHERE request_id=?",
                (time.time(), request_id),
            )
            self.db.commit()
            return {"request_id": request_id, "digest": digest, "status": "completed",
                    "deleted": generation is not None or deleted}

    def prune(self, digests, *, request_id=None):
        """Explicitly delete a teacher-selected bounded set of digests.

        This method never selects candidates itself; automatic retention is
        deliberately absent. Each selected digest is independently
        idempotent, and blocked/corrupt entries are reported for review while
        other selected entries can complete.
        """
        if not isinstance(digests, (list, tuple)) or not 1 <= len(digests) <= MAX_PAGE_SIZE:
            raise ValueError("Sletningslisten er ugyldig.")
        if len(set(digests)) != len(digests) or any(not self._valid_digest(digest) for digest in digests):
            raise ValueError("Sletningslisten er ugyldig.")
        if request_id is not None:
            request_id = self._request_id(request_id)
            if len(request_id) > 77:
                raise ValueError("Sletningskvitteringen er ugyldig.")
        result = {"deleted": [], "already_deleted": [], "blocked": [], "pending": []}
        for index, digest in enumerate(digests):
            item_request = None if request_id is None else request_id + "-" + str(index)
            try:
                outcome = self.request_delete(digest, item_request) if item_request else self.delete(digest)
                if isinstance(outcome, dict) and not outcome["deleted"]:
                    result["already_deleted"].append(digest)
                elif outcome:
                    result["deleted"].append(digest)
                else:
                    result["already_deleted"].append(digest)
            except LibraryDeletionPending:
                result["pending"].append(digest)
            except (LibraryInUse, LibraryCorrupt) as error:
                result["blocked"].append({"digest": digest, "error": str(error)})
        return result
