"""Scoped cleanup of transient print job files.

The queue database is the durable history. STL uploads and slicer work are only
working material and may be removed after a job reaches a confirmed terminal
state.  This module deliberately knows only the two queue-owned directories;
it never walks the data root and never removes camera, certificate, training,
or configuration files.
"""
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import shutil

log = logging.getLogger(__name__)
JOB_ID = re.compile(r"^[a-f0-9]{32}$")


@dataclass
class CleanupResult:
    removed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)

    @property
    def complete(self):
        return not self.pending


def _owned_target(parent: Path, name: str) -> Path:
    """Return a target only when its queue-owned parent is a real directory.

    Resolving the parent would make ``uploads`` or ``sliced`` symlinks point
    cleanup at an arbitrary directory.  Refuse those parents instead.  The
    target itself is deliberately *not* resolved: a job symlink is removed as
    a link and never followed.
    """
    parent = Path(parent)
    target = parent / name
    is_junction = getattr(parent, 'is_junction', lambda: False)()
    if target.parent != parent or target.name != name or parent.is_symlink() or is_junction:
        raise ValueError("Ugyldig oprydningssti.")
    return target


def _is_reparse_link(path: Path) -> bool:
    """Recognize symlinks and Windows junctions without resolving either."""
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def cleanup_job_artifacts(folder, job_id) -> CleanupResult:
    """Remove one print job's STL and slicer directory, safely and idempotently.

    ``folder`` is the queue folder.  Only ``uploads/<id>.stl`` and
    ``sliced/<id>/`` are in scope.  A locked Windows file is reported as
    pending so the queue worker can retry it after a bounded delay; the
    durable queue row is intentionally left untouched. Startup also retries
    all terminal rows as a crash-recovery reconciliation.
    """
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("Ugyldigt job-id til oprydning.")
    root = Path(folder).resolve()
    result = CleanupResult()
    targets = (
        (_owned_target(root / "uploads", f"{job_id}.stl"), False),
        (_owned_target(root / "sliced", job_id), True),
    )
    for target, directory in targets:
        if not target.exists() and not _is_reparse_link(target):
            continue
        try:
            if _is_reparse_link(target):
                target.unlink()
            elif directory and target.is_dir():
                shutil.rmtree(target)
            else:
                # A malformed or interrupted slicer run can leave a file at
                # the directory name.  It is still the exact job path, so it
                # is safe to remove without walking anything else.
                target.unlink()
            result.removed.append(str(target))
        except (OSError, PermissionError) as error:
            # Windows can retain a handle briefly after Bambu Studio exits.
            # Keep this bounded to the exact target. Queue-level scheduling
            # retries pending jobs after 60 seconds; startup also retries them.
            result.pending.append(str(target))
            log.warning("Printjob %s venter på filoprydning (%s): %s", job_id, target, error)
    return result
