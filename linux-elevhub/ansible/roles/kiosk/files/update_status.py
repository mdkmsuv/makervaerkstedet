"""Read-only validation and presentation for the root-owned update heartbeat."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

STATUS_PATH = Path('/run/elevhub-update-status.json')
MAX_BYTES = 16 * 1024
PHASES = {'checking', 'snapshot', 'installing', 'verifying', 'recovering',
          'complete', 'rolled_back', 'failed', 'recovery_required'}
STATUSES = {'staged_awaiting_restart', 'applying', 'recovering', 'complete',
            'failed_rolled_back', 'failed_before_apply', 'recovery_required'}
ACTIVE_PHASES = {'checking', 'snapshot', 'installing', 'verifying', 'recovering'}
ACTIVE_STATUSES = {'applying', 'recovering'}


def current_boot_id(path='/proc/sys/kernel/random/boot_id'):
    try:
        value = Path(path).read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        return None
    return value if 1 <= len(value) <= 128 else None


def _safe_file(path):
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        return None
    if os.name != 'nt' and info.st_mode & 0o022:
        return None
    # Windows has no meaningful st_uid; Linux production files must be root-owned.
    if hasattr(info, 'st_uid') and os.name != 'nt' and info.st_uid != 0:
        return None
    return info


def read_status(path=STATUS_PATH, boot_id=None):
    """Return a bounded, current heartbeat or ``None`` without blocking boot."""
    if boot_id is None:
        boot_id = current_boot_id()
        if boot_id is None:
            return None
    if _safe_file(path) is None:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(value, dict) or value.get('schema') != 1:
        return None
    if value.get('status') not in STATUSES or value.get('phase') not in PHASES:
        return None
    marker = value.get('boot_id')
    if not isinstance(marker, str) or not marker or boot_id is not None and marker != boot_id:
        return None
    if not isinstance(value.get('release', ''), str) or len(value.get('release', '')) > 120:
        return None
    if not isinstance(value.get('updated_at'), (int, float)):
        return None
    return value


def blocks_power(value):
    if not value or value.get('status') == 'staged_awaiting_restart':
        return False
    return bool(value.get('status') in ACTIVE_STATUSES or
                (value.get('phase') in ACTIVE_PHASES and
                 value.get('status') not in {'complete', 'failed_rolled_back', 'failed_before_apply'}))


def message(value):
    """Return a prominent Danish instruction for a validated or test value."""
    if not value:
        return ''
    status, phase = value.get('status'), value.get('phase')
    if status == 'recovery_required' or phase == 'recovery_required':
        return 'OPDATERING FEJLEDE · GENDANNELSE KRÆVER HJÆLP · BED LÆREREN OM HJÆLP'
    if status == 'failed_rolled_back' or phase == 'rolled_back':
        return 'OPDATERING FEJLEDE · TIDLIGERE VERSION ER GENDANNET · BED LÆREREN OM HJÆLP'
    if status == 'failed_before_apply' or phase == 'failed':
        return 'OPDATERINGEN KUNNE IKKE STARTE · BED LÆREREN OM HJÆLP'
    if status == 'staged_awaiting_restart':
        return 'OPDATERING KLAR · GEM ARBEJDET · GENSTART NORMALT EFTER LÆRERENS BESKED'
    if blocks_power(value):
        return 'OPDATERER · LAD COMPUTEREN VÆRE TÆNDT · GENSTART IKKE'
    if status == 'complete' or phase == 'complete':
        return 'OPDATERING FÆRDIG'
    return ''
