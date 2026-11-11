"""Crash-safe staging for queue submission payloads.

The queue commits its durable library row before it commits the job row.  A
small, fsynced marker records the transient upload while that transaction is
in flight.  Recovery is deliberately conservative: only paths named by a
validated marker can be inspected or removed, and changed links or files are
left in place for an operator to inspect.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


log = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(("done", "failed", "cancelled"))
MARKER_VERSION = 1
MAX_MARKER_BYTES = 16 * 1024
MAX_STAGED_BYTES = 25 * 1024 ** 2
MAX_RECOVERY_MARKERS = 100
_JOB_RE = re.compile(r"^[a-f0-9]{32}$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_TEMP_RE = re.compile(r"^\.[a-f0-9]{32}\.[a-f0-9]{32}\.upload\.tmp$")


class SubmissionStageError(ValueError):
    """A staging operation failed closed without deleting untrusted data."""


def _is_reparse(path: Path) -> bool:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _assert_safe_ancestors(path: Path, *, include_self: bool = True) -> None:
    """Reject symlink/junction ancestors without resolving through them."""
    current = path if include_self else path.parent
    while True:
        if _is_reparse(current):
            raise SubmissionStageError(f"Reparse-link is not allowed: {current}")
        if current.parent == current:
            break
        current = current.parent


def _root(folder) -> Path:
    root = Path(folder).absolute()
    if not root.exists() or not root.is_dir() or _is_reparse(root):
        raise SubmissionStageError("Staging root must be an existing ordinary directory.")
    _assert_safe_ancestors(root)
    return root


def _ensure_dir(path: Path) -> None:
    _assert_safe_ancestors(path, include_self=False)
    if path.exists():
        if not path.is_dir() or _is_reparse(path):
            raise SubmissionStageError(f"Staging directory is unsafe: {path}")
    else:
        path.mkdir()
    _assert_safe_ancestors(path)


def _paths(folder):
    root = _root(folder)
    uploads = root / "uploads"
    stages = root / "submission-stages"
    _ensure_dir(uploads)
    _ensure_dir(stages)
    return root, uploads, stages


def _validate_ids(job_id, digest) -> tuple[str, str]:
    if not isinstance(job_id, str) or _JOB_RE.fullmatch(job_id) is None:
        raise SubmissionStageError("job_id must be exactly 32 lowercase hexadecimal characters")
    if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
        raise SubmissionStageError("digest must be exactly 64 lowercase hexadecimal characters")
    return job_id, digest


def _safe_regular(path: Path, *, missing_ok: bool = True) -> bool:
    _assert_safe_ancestors(path, include_self=False)
    if not path.exists():
        if missing_ok:
            return False
        raise SubmissionStageError(f"Missing staging file: {path}")
    if _is_reparse(path) or not path.is_file():
        raise SubmissionStageError(f"Staging file is unsafe: {path}")
    return True


def _regular_identity(path: Path, *, missing_ok: bool = True):
    """Check one exact regular file and return its lstat identity."""
    _assert_safe_ancestors(path, include_self=False)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise SubmissionStageError(f"Missing staging file: {path}")
    except OSError as exc:
        raise SubmissionStageError(f"Staging file could not be inspected: {path}") from exc
    if (path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or not stat.S_ISREG(info.st_mode)):
        raise SubmissionStageError(f"Staging file is unsafe: {path}")
    return info.st_dev, info.st_ino


def _fsync_directory(path: Path) -> None:
    # Windows does not permit opening directories for fsync.  The file fsyncs
    # still provide the required ordering there; POSIX gets a directory fsync.
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _fsync_bytes(path: Path, data: bytes, *, truncate: bool = True) -> None:
    flags = os.O_WRONLY | os.O_CREAT
    if truncate:
        flags |= os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def _write_marker(stages: Path, marker_path: Path, marker: dict) -> None:
    encoded = json.dumps(marker, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MARKER_BYTES:
        raise SubmissionStageError("Submission marker is too large")
    _assert_safe_ancestors(marker_path, include_self=False)
    if marker_path.exists() and _is_reparse(marker_path):
        raise SubmissionStageError("Submission marker is a link")
    fd, temp_name = tempfile.mkstemp(prefix=".marker-", suffix=".json.tmp", dir=stages)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, marker_path)
        _fsync_directory(stages)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _strict_object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate marker key")
        result[key] = value
    return result


def _read_marker(marker_path: Path) -> tuple[dict, tuple[int, int]]:
    _safe_regular(marker_path, missing_ok=False)
    try:
        stat_before = marker_path.stat()
        if stat_before.st_size > MAX_MARKER_BYTES:
            raise SubmissionStageError("Submission marker is too large")
        raw = marker_path.read_bytes()
        marker = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SubmissionStageError(f"Invalid submission marker: {marker_path}") from exc
    if not isinstance(marker, dict):
        raise SubmissionStageError("Submission marker must be an object")
    required = {"schemaVersion", "job_id", "digest", "size", "upload_name", "temp_name"}
    if set(marker) != required:
        raise SubmissionStageError("Submission marker has an unexpected schema")
    if type(marker["schemaVersion"]) is not int or marker["schemaVersion"] != MARKER_VERSION:
        raise SubmissionStageError("Unsupported submission marker version")
    job_id, digest = _validate_ids(marker["job_id"], marker["digest"])
    size = marker["size"]
    if type(size) is not int or size < 0 or size > MAX_STAGED_BYTES:
        raise SubmissionStageError("Invalid staged payload size")
    if marker["upload_name"] != f"{job_id}.stl":
        raise SubmissionStageError("Submission marker upload name is invalid")
    temp_name = marker["temp_name"]
    if not isinstance(temp_name, str) or _TEMP_RE.fullmatch(temp_name) is None:
        raise SubmissionStageError("Submission marker temp name is invalid")
    if not temp_name.startswith(f".{job_id}."):
        raise SubmissionStageError("Submission marker temp belongs to another job")
    stat_after = marker_path.stat()
    if (stat_before.st_dev, stat_before.st_ino) != (stat_after.st_dev, stat_after.st_ino):
        raise SubmissionStageError("Submission marker changed while being read")
    return marker, (stat_after.st_dev, stat_after.st_ino)


def _marker_paths(uploads: Path, marker: dict):
    # The marker contains names, never paths.  The exact patterns above make
    # these joins incapable of escaping the fixed uploads directory.
    upload = uploads / marker["upload_name"]
    temp = uploads / marker["temp_name"]
    _assert_safe_ancestors(upload, include_self=False)
    _assert_safe_ancestors(temp, include_self=False)
    return upload, temp


def _sha256(path: Path, *, max_bytes=MAX_STAGED_BYTES) -> tuple[str | None, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                return None, size
            digest.update(chunk)
    return digest.hexdigest(), size


def _payload_matches(path: Path, digest: str, size: int) -> bool:
    if not _safe_regular(path):
        return False
    try:
        if size > MAX_STAGED_BYTES or path.stat().st_size != size:
            return False
        identity = _regular_identity(path, missing_ok=False)
    except (OSError, SubmissionStageError):
        return False
    actual, actual_size = _sha256(path)
    try:
        unchanged = _regular_identity(path, missing_ok=False) == identity
    except SubmissionStageError:
        return False
    return unchanged and actual is not None and actual == digest and actual_size == size


def _new_temp_name(job_id: str) -> str:
    return f".{job_id}.{uuid.uuid4().hex}.upload.tmp"


def _write_payload(uploads: Path, final: Path, temp: Path, data: bytes, digest: str) -> None:
    if len(data) > MAX_STAGED_BYTES:
        raise SubmissionStageError("Staged payload is too large")
    if final.exists():
        if _is_reparse(final):
            raise SubmissionStageError("Existing upload is a link")
        if _payload_matches(final, digest, len(data)):
            return
        raise SubmissionStageError("Existing upload does not match the staged digest")
    _assert_safe_ancestors(temp, include_self=False)
    if temp.exists():
        if _is_reparse(temp) or not temp.is_file():
            raise SubmissionStageError("Existing staging temp is unsafe")
        # A marker-created empty reservation is safe to fill.  A partial file
        # is preserved and a caller must choose a new temp name.
        if temp.stat().st_size != 0:
            raise SubmissionStageError("Existing staging temp was changed")
        mode = "wb"
    else:
        mode = "xb"
    with temp.open(mode) as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, final)
    _fsync_directory(uploads)
    if not _payload_matches(final, digest, len(data)):
        raise SubmissionStageError("Staged upload failed integrity verification")


def write_upload(folder, job_id, digest, data):
    """Journal and atomically materialize one queue-owned transient upload."""
    job_id, digest = _validate_ids(job_id, digest)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise SubmissionStageError("Upload data must be bytes-like")
    payload = bytes(data)
    if hashlib.sha256(payload).hexdigest() != digest:
        raise SubmissionStageError("Upload data does not match digest")
    _root_path, uploads, stages = _paths(folder)
    marker_path = stages / f"{job_id}.json"
    marker = None
    if marker_path.exists():
        marker, _identity = _read_marker(marker_path)
        if marker["digest"] != digest or marker["size"] != len(payload):
            raise SubmissionStageError("Existing marker does not match upload")
    else:
        temp_name = _new_temp_name(job_id)
        temp = uploads / temp_name
        final = uploads / f"{job_id}.stl"
        # If a prior committed operation already left the exact payload, no
        # new payload write is needed.  Still record a marker for the caller's
        # jobs transaction, but do not reserve an unnecessary temp file.
        reserve_temp = True
        if final.exists():
            if _is_reparse(final) or not _payload_matches(final, digest, len(payload)):
                raise SubmissionStageError("Existing upload does not match the staged digest")
            reserve_temp = False
        if reserve_temp:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        marker = {"schemaVersion": MARKER_VERSION, "job_id": job_id, "digest": digest,
                  "size": len(payload), "upload_name": f"{job_id}.stl", "temp_name": temp_name}
        try:
            _write_marker(stages, marker_path, marker)
        except BaseException:
            if reserve_temp:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
            raise
    final, temp = _marker_paths(uploads, marker)
    _write_payload(uploads, final, temp, payload, digest)
    return final


def finish_upload(folder, job_id, digest):
    """Remove exactly the matching marker after the jobs transaction commits."""
    job_id, digest = _validate_ids(job_id, digest)
    _root_path, uploads, stages = _paths(folder)
    marker_path = stages / f"{job_id}.json"
    if not marker_path.exists():
        return False
    marker, identity = _read_marker(marker_path)
    if marker["digest"] != digest:
        raise SubmissionStageError("Submission marker digest does not match")
    final, _temp = _marker_paths(uploads, marker)
    if not _payload_matches(final, digest, marker["size"]):
        raise SubmissionStageError("Cannot finish a missing or corrupt upload")
    current = marker_path.stat()
    if (current.st_dev, current.st_ino) != identity:
        raise SubmissionStageError("Submission marker changed before removal")
    marker_path.unlink()
    _fsync_directory(stages)
    return True


def _row_value(row, key, index):
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return row[index]


def _job_row(db, job_id):
    row = db.execute("SELECT id, digest, status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    return (_row_value(row, "id", 0), _row_value(row, "digest", 1), _row_value(row, "status", 2))


def _library_payload(library, digest):
    payload = library.download(digest)
    if not isinstance(payload, bytes):
        payload = bytes(payload)
    if len(payload) > MAX_STAGED_BYTES or hashlib.sha256(payload).hexdigest() != digest:
        raise SubmissionStageError("Library copy failed digest verification")
    return payload


def _remove_owned(path: Path, digest: str, size: int) -> bool:
    identity = _regular_identity(path)
    if identity is None:
        return True
    if not _payload_matches(path, digest, size):
        return False
    try:
        if _regular_identity(path, missing_ok=False) != identity:
            return False
    except SubmissionStageError:
        return False
    path.unlink()
    return True


def _remove_marker_if_unchanged(marker_path: Path, identity) -> None:
    current = _regular_identity(marker_path, missing_ok=False)
    if current != identity:
        raise SubmissionStageError("Submission marker changed during recovery")
    marker_path.unlink()


def _remove_regular_if_unchanged(path: Path, identity) -> None:
    current = _regular_identity(path, missing_ok=False)
    if current != identity:
        raise SubmissionStageError("Owned staging file changed during recovery")
    path.unlink()


def recover_uploads(folder, db, library, *, limit=MAX_RECOVERY_MARKERS):
    """Recover staged uploads before queue migration/terminal cleanup.

    The return value is a small diagnostic summary; failures are logged and
    leave their marker and any files in place for a later retry.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_RECOVERY_MARKERS:
        raise ValueError("Submission recovery limit is invalid")
    result = {"repaired": [], "cleaned": [], "preserved": [], "deferred": False}
    try:
        _root_path, uploads, stages = _paths(folder)
    except SubmissionStageError as exc:
        log.warning("Submission recovery skipped unsafe staging tree: %s", exc)
        return result
    def process_marker(marker_path):
        job_id = marker_path.stem
        try:
            marker, identity = _read_marker(marker_path)
            if marker["job_id"] != job_id:
                raise SubmissionStageError("Marker filename and job id differ")
            final, temp = _marker_paths(uploads, marker)
            row = _job_row(db, job_id)
            if row is None:
                payload = _library_payload(library, marker["digest"])
                if len(payload) != marker["size"]:
                    raise SubmissionStageError("Marker size differs from library copy")
                if not _remove_owned(final, marker["digest"], marker["size"]):
                    raise SubmissionStageError("Changed or unsafe upload was preserved")
                temp_identity = _regular_identity(temp)
                if temp_identity is not None:
                    _remove_regular_if_unchanged(temp, temp_identity)
                _remove_marker_if_unchanged(marker_path, identity)
                result["cleaned"].append(job_id)
                return
            row_id, row_digest, status = row
            if row_id != job_id or row_digest != marker["digest"]:
                raise SubmissionStageError("Job row differs from submission marker")
            if status in TERMINAL_STATUSES:
                _remove_marker_if_unchanged(marker_path, identity)
                result["cleaned"].append(job_id)
                return
            payload = _library_payload(library, marker["digest"])
            if len(payload) != marker["size"]:
                raise SubmissionStageError("Marker size differs from library copy")
            if final.exists():
                if not _payload_matches(final, marker["digest"], marker["size"]):
                    raise SubmissionStageError("Changed or unsafe upload was preserved")
            else:
                if temp.exists() and (temp.stat().st_size != 0 or _is_reparse(temp)):
                    new_temp_name = _new_temp_name(job_id)
                    marker["temp_name"] = new_temp_name
                    _write_marker(stages, marker_path, marker)
                    marker, identity = _read_marker(marker_path)
                    final, temp = _marker_paths(uploads, marker)
                if not temp.exists():
                    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.close(fd)
                _write_payload(uploads, final, temp, payload, marker["digest"])
                result["repaired"].append(job_id)
            _remove_marker_if_unchanged(marker_path, identity)
            if job_id not in result["repaired"]:
                result["cleaned"].append(job_id)
        except (SubmissionStageError, OSError, ValueError) as exc:
            log.warning("Submission marker %s preserved: %s", marker_path.name, exc)
            result["preserved"].append(marker_path.name)

    # Accepted, non-terminal jobs are the recovery priority.  The query is
    # bounded and the marker path is constructed only from a validated UUID.
    priority_rows = db.execute(
        "SELECT id FROM jobs WHERE status NOT IN ('done','failed','cancelled') "
        "AND length(id)=32 AND id NOT GLOB '*[^0-9a-f]*' ORDER BY created LIMIT ?",
        (limit + 1,),
    ).fetchall()
    priority_ids = [row[0] for row in priority_rows[:limit]]
    if len(priority_rows) > limit:
        result["deferred"] = True
    priority_names = {job_id + ".json" for job_id in priority_ids}
    for job_id in priority_ids:
        marker_path = stages / f"{job_id}.json"
        if marker_path.exists():
            process_marker(marker_path)

    # Inspect only a bounded number of other directory entries.  This keeps a
    # flood of unrelated files from making startup scan the whole directory.
    try:
        entries = os.scandir(stages)
    except OSError as exc:
        log.warning("Submission recovery cannot enumerate markers: %s", exc)
        return result
    other_entries = 0
    try:
        for entry in entries:
            if entry.name in priority_names:
                continue
            if other_entries >= limit:
                result["deferred"] = True
                break
            other_entries += 1
            if not entry.name.endswith(".json"):
                continue
            process_marker(Path(entry.path))
    finally:
        entries.close()
    return result

