"""Durable queue and conservative bed-release state machine, shared by demo and LAN."""
import hashlib
import calendar
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid
import logging

from .stl import inspect_stl
from .profiles import MODELS, QUALITIES
from .queue_storage import cleanup_job_artifacts
from .file_library import FileLibrary
from .submission_stage import write_upload, finish_upload, recover_uploads
from .hardware_workflows import validate_binding
from .printer_config import ConfigConflict

log = logging.getLogger(__name__)

ACTIVE = ("workflow_wait", "preparing", "dispatching", "starting", "printing", "recovering", "attention")
IDLE = {"IDLE", "FINISH"}
BUSY = {"RUNNING", "PREPARE", "PAUSE"}
FRESH_SECONDS = 40
# Bambu Studio's supplied HMS catalog: observe filament extrusion and Continue.
FILAMENT_CONFIRMATION = 0x07FF8007
DEFAULT_PRINT_TRIGGER = 10
PRINT_BLOCK_SECONDS = 3600
CLEANUP_RETRY_SECONDS = 60
CLEANUP_RETRY_BATCH = 16
UPLOAD_INVENTORY_LIMIT = 10000
TRANSFER_ATTEMPT_LIMIT = 3
TERMINAL = frozenset(("done", "failed", "cancelled"))


class SubmissionBlocked(ValueError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def normalize_colour(value):
    value = str(value or "").lstrip("#").upper()
    return "#" + value[:6] if re.fullmatch(r"[0-9A-F]{6}([0-9A-F]{2})?", value) else None


def colour_name(value):
    names = {"Rød": "E84142", "Orange": "FF8B2C", "Gul": "F5D328", "Grøn": "32AA69", "Blå": "3678DC", "Lilla": "925CCC", "Lyserød": "F28AB5", "Hvid": "F4F4F4", "Sort": "20242A", "Grå": "92999F", "Brun": "895634"}
    rgb = tuple(int(value[i:i+2], 16) for i in (1, 3, 5))
    return min(names, key=lambda name: sum((rgb[i] - int(names[name][i*2:i*2+2], 16)) ** 2 for i in range(3)))


class Queue:
    def __init__(self, folder, configs, demo=False, clock=time.time):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "uploads").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.clock = clock
        self.demo = demo
        self.paused = False
        self.configs = {p["id"]: p for p in configs}
        self.db = sqlite3.connect(self.folder / "queue.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            database_version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if database_version not in (0, 1):
                raise ValueError("Uventet printkø-databaseversion.")
        except BaseException:
            self.db.close()
            raise
        try:
            self._initialize_database()
        except BaseException:
            self.db.close()
            raise

    def _initialize_database(self):
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL,
            filename TEXT NOT NULL, colour TEXT, meta TEXT NOT NULL, created REAL NOT NULL,
            updated REAL NOT NULL, status TEXT NOT NULL, printer_id TEXT, slot INTEGER,
            actual_colour TEXT, message TEXT NOT NULL DEFAULT '', progress INTEGER DEFAULT 0,
            remaining INTEGER, remote_name TEXT, profile TEXT, quality TEXT NOT NULL DEFAULT 'standard')""")
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        if 'quality' not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN quality TEXT NOT NULL DEFAULT 'standard'")
        if 'extruder' not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN extruder INTEGER")
        if 'retry_after' not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN retry_after REAL NOT NULL DEFAULT 0")
        if 'source' not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN source TEXT")
        for field in ('transfer_attempts', 'isolated', 'physical_resolved'):
            if field not in columns:
                self.db.execute(f'ALTER TABLE jobs ADD COLUMN {field} INTEGER NOT NULL DEFAULT 0')
        for field, declaration in (
                ('workflow', 'TEXT'), ('workflow_step', 'INTEGER NOT NULL DEFAULT 0'),
                ('workflow_version', 'INTEGER NOT NULL DEFAULT 0'),
                ('workflow_authorized', 'INTEGER NOT NULL DEFAULT 0'),
                ('workflow_complete', 'INTEGER NOT NULL DEFAULT 0')):
            if field not in columns:
                self.db.execute(f'ALTER TABLE jobs ADD COLUMN {field} {declaration}')
        self.db.execute('''CREATE TABLE IF NOT EXISTS workflow_commands (
            actor TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL, response TEXT NOT NULL,
            PRIMARY KEY(actor, request_id))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS workflow_audit (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
            actor TEXT NOT NULL, request_id TEXT NOT NULL, job_id TEXT NOT NULL, step_id TEXT NOT NULL)''')
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_source_created ON jobs(source, created)")
        self.library = FileLibrary(self.db, self.folder)
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        # Replace the former per-hour/concurrent quotas; do not migrate their
        # small values into the abuse trigger.
        self.db.execute("DELETE FROM settings WHERE key='submission_limits'")
        self.db.execute("""CREATE TABLE IF NOT EXISTS submission_access (
            source TEXT PRIMARY KEY, reset_after INTEGER NOT NULL DEFAULT 0,
            blocked_until REAL NOT NULL DEFAULT 0, block_id TEXT,
            blocked_at REAL, trigger_count INTEGER)""")
        row = self.db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        self.paused = row is not None and row[0] == 'true'
        version_row = self.db.execute("SELECT value FROM settings WHERE key='queue_control_version'").fetchone()
        if version_row is None:
            self.queue_control_version = 1
            self.db.execute("INSERT INTO settings VALUES('queue_control_version','1')")
        else:
            try:
                self.queue_control_version = int(version_row[0])
            except (TypeError, ValueError):
                raise ValueError('Ugyldig version for printkøstyring.') from None
            if self.queue_control_version < 1:
                raise ValueError('Ugyldig version for printkøstyring.')
        # Preparing is before the durable send barrier; no start command existed.
        self.db.execute("UPDATE jobs SET status='queued', printer_id=NULL, slot=NULL, extruder=NULL, actual_colour=NULL, profile=NULL, remote_name=NULL, message=? WHERE status='preparing' AND workflow IS NULL", ("Klargøring genoptages efter genstart.",))
        self.db.execute("UPDATE jobs SET status='workflow_wait', message=? WHERE status='preparing' AND workflow IS NOT NULL", ("Klargøring genoptages med jobbets valgte workflow og printer.",))
        # Never replay work on the other side of the barrier. Reconcile telemetry.
        self.db.execute("UPDATE jobs SET status='recovering', message=? WHERE status IN ('dispatching','starting','printing','recovering')", ("Venter på printerens aktuelle job efter genstart.",))
        self.db.execute("PRAGMA user_version=1")
        self.db.commit()
        # Preserve every upload that still exists before terminal cleanup can
        # remove it. Deleted historical uploads cannot be reconstructed.
        self.submission_recovery = recover_uploads(self.folder, self.db, self.library)
        self._next_stage_recovery = self.clock() + CLEANUP_RETRY_SECONDS
        self.library.migrate_jobs(self.folder)
        self.recovery_seen = {}
        # Exact payload paths that were locked when a terminal transition was
        # committed. Startup and the worker reconcile history in small batches.
        self.pending_cleanup_jobs = {}
        self._terminal_cleanup_cursor = ''
        self._terminal_cleanup_complete = False
        self.printers = {p["id"]: {"id": p["id"], "name": p["name"], "state": "OFFLINE", "seen": 0, "state_seen": 0, "tray_seen": 0, "bed_clear": False, "trays": [], "progress": 0, "remaining": None, "error": 0, "task": "", "enabled": p.get("enabled", False), "message": "Venter på forbindelse"} for p in self.configs.values()}
        self.maintenance_snapshot()
        # Retry only exact, queue-owned payloads left locked by a previous
        # process. Terminal rows remain as useful history in SQLite.
        self.cleanup_terminal_jobs()

    def _maintenance_record(self, printer_id, today):
        key = 'maintenance:' + printer_id
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        if row:
            return json.loads(row[0])
        record = {'started_on': today.isoformat(), 'completed_on': None}
        self.db.execute('INSERT INTO settings VALUES(?,?)', (key, json.dumps(record)))
        return record

    def maintenance_snapshot(self):
        # Calendar dates need no scheduler, model process or printer connection.
        today = datetime.fromtimestamp(self.clock()).date()
        with self.lock:
            printers = []
            for pid, config in self.configs.items():
                record = self._maintenance_record(pid, today)
                base = date.fromisoformat(record['completed_on'] or record['started_on'])
                year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
                due = date(year, month, min(base.day, calendar.monthrange(year, month)[1]))
                printers.append({'id': pid, 'name': config['name'], 'completed_on': record['completed_on'],
                                 'next_due': due.isoformat(), 'due': today >= due})
            self.db.commit()
            return {'printers': printers, 'due_count': sum(p['due'] for p in printers)}

    def complete_maintenance(self, printer_id):
        with self.lock:
            if printer_id not in self.configs:
                raise ValueError('Printeren blev ikke fundet.')
            today = datetime.fromtimestamp(self.clock()).date()
            record = self._maintenance_record(printer_id, today)
            record['completed_on'] = today.isoformat()
            self.db.execute('UPDATE settings SET value=? WHERE key=?',
                            (json.dumps(record), 'maintenance:' + printer_id))
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def library_list(self):
        with self.lock:
            return self.library.list()

    def library_page(self, page=1, page_size=25, query=''):
        with self.lock:
            result = self.library.list_page(page, page_size, query=query)
            result['files'] = result.pop('items')
            result['storage'] = self.library.disk_status()
            result['policy'] = self.library.get_retention_policy()
            result['recovery'] = self._recovery_status()
            return result

    def library_storage(self):
        with self.lock:
            return {'storage': self.library.disk_status(),
                    'policy': self.library.get_retention_policy(), 'recovery': self._recovery_status()}

    def _recovery_status(self):
        return {'preserved_count': len(self.submission_recovery.get('preserved', [])),
                'deferred': self.submission_recovery.get('deferred', False)}

    def library_delete(self, digest, request_id, expected_generation, confirmed):
        if confirmed is not True:
            raise ValueError('Bekræft, at modellen og navnene i dens afsluttede historik skal slettes.')
        if not isinstance(expected_generation, str) or not re.fullmatch(r'[a-f0-9]{32}', expected_generation):
            raise ValueError('Hent biblioteket igen før sletning.')
        with self.lock:
            return self.library.request_delete(digest, request_id,
                                               expected_generation=expected_generation)

    def library_set_policy(self, data):
        if (not isinstance(data, dict) or set(data) !=
                {'request_id', 'expected_version', 'retention_days', 'quota_bytes'}
                or type(data['expected_version']) is not int or data['expected_version'] < 0
                or type(data['retention_days']) is not int or not 1 <= data['retention_days'] <= 3650
                or type(data['quota_bytes']) is not int or not 25 * 1024**2 <= data['quota_bytes'] <= 1024**4
                or not isinstance(data['request_id'], str)
                or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', data['request_id'])):
            raise ValueError('Vælg 1–3650 dage og et lagerloft mellem 25 MiB og 1 TiB.')
        with self.lock:
            return {'policy': self.library.set_retention_policy(**data),
                    'storage': self.library.disk_status()}

    def library_download_entry(self, digest):
        with self.lock:
            entry = self.library.get(digest)
            return self.library.download(digest), entry['filename']

    def library_download(self, digest):
        with self.lock:
            return self.library.download(digest)

    def library_reprint(self, digest, request_id, *, source='teacher', colour=None, quality=None):
        """Submit an archived model through normal queue admission."""
        with self.lock:
            model = self.library.get(digest)
            data = self.library.download(digest)
            selected_colour = model['colour'] if colour is None else colour
            selected_quality = model['quality'] if quality is None else quality
        return self.submit(data, model['filename'], selected_colour or 'any', request_id,
                           selected_quality, source=source,
                           library_generation=model['generation'])

    # Verbose aliases keep the public surface readable for HTTP/server code.
    list_library = library_list
    download_library = library_download
    reprint_library = library_reprint

    def _change(self, job_id, **values):
        if values.get('status') in ('cancelled', 'failed'):
            previous = self.get_job(job_id)
            if previous.get('workflow') and not previous['workflow_complete']:
                binding = validate_binding(json.loads(previous['workflow']))
                # Failure closes production, but cannot claim that the plate is clear.
                values.update(workflow_step=len(binding['definition']['steps']) - 1,
                              workflow_version=previous['workflow_version'] + 1, workflow_authorized=0)
        values["updated"] = self.clock()
        keys = list(values)
        self.db.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in keys)} WHERE id=?", [values[k] for k in keys] + [job_id])
        self.db.commit()
        if values.get('status') in TERMINAL:
            self.cleanup_job(job_id)

    def cleanup_job(self, job_id):
        """Clean transient files after the terminal status is durable."""
        # Keep caller errors visible while treating a redirected/junction
        # queue directory like any other scoped filesystem cleanup failure.
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('Ugyldigt job-id til oprydning.')
        try:
            result = cleanup_job_artifacts(self.folder, job_id)
            with self.lock:
                if result.pending:
                    self.pending_cleanup_jobs[job_id] = self.clock() + CLEANUP_RETRY_SECONDS
                else:
                    self.pending_cleanup_jobs.pop(job_id, None)
            return result
        except OSError as error:
            with self.lock:
                self.pending_cleanup_jobs[job_id] = self.clock() + CLEANUP_RETRY_SECONDS
            # A status transition must remain durable even if a file is
            # temporarily locked. The worker retries the exact paths after a
            # bounded delay; startup cleanup also retries them.
            # nothing outside the exact queue path is followed.
            log.warning("Kunne ikke planlægge oprydning for printjob %s: %s", job_id, error)
            return None
        except ValueError as error:
            # A redirected/junction parent is a scope violation, not a
            # transient lock. Leave it for the next startup audit only.
            with self.lock:
                self.pending_cleanup_jobs.pop(job_id, None)
            log.warning("Kunne ikke planlægge oprydning for printjob %s: %s", job_id, error)
            return None

    def cleanup_terminal_jobs(self, limit=CLEANUP_RETRY_BATCH):
        """Reconcile one keyset page of terminal history after a crash."""
        limit = min(CLEANUP_RETRY_BATCH, max(0, limit))
        if not limit:
            return {}
        with self.lock:
            ids = [row[0] for row in self.db.execute(
                "SELECT id FROM jobs WHERE status IN ('done','failed','cancelled') "
                "AND id>? AND length(id)=32 AND id NOT GLOB '*[^a-f0-9]*' ORDER BY id LIMIT ?",
                (self._terminal_cleanup_cursor, limit))]
            if ids:
                self._terminal_cleanup_cursor = ids[-1]
            self._terminal_cleanup_complete = len(ids) < limit
        return {job_id: self.cleanup_job(job_id) for job_id in ids}

    def retry_pending_cleanup(self, limit=CLEANUP_RETRY_BATCH):
        """Retry only due, exact-path cleanup jobs, in a bounded batch."""
        now = self.clock()
        with self.lock:
            ids = [job_id for job_id, due in self.pending_cleanup_jobs.items()
                   if due <= now][:max(0, limit)]
        for job_id in ids:
            self.cleanup_job(job_id)
        if not self._terminal_cleanup_complete:
            self.cleanup_terminal_jobs(limit)
        if now >= self._next_stage_recovery:
            with self.lock:
                if self.submission_recovery.get('preserved') or self.submission_recovery.get('deferred'):
                    self.submission_recovery = recover_uploads(self.folder, self.db, self.library)
                self.library.recover_pending_deletions()
                self._next_stage_recovery = now + CLEANUP_RETRY_SECONDS

    def _upload_bytes(self):
        """Bound the inventory and never follow an upload or parent link."""
        parent = (self.folder / 'uploads').absolute()
        for path in (parent, *parent.parents):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Uploadmappen er omdirigeret. Bed læreren kontrollere lagringen.')
        used = 0
        with os.scandir(parent) as entries:
            for index, entry in enumerate(entries):
                if index >= UPLOAD_INVENTORY_LIMIT:
                    raise ValueError('Uploadmappen kræver oprydning. Bed læreren kontrollere lagringen.')
                if not entry.name.lower().endswith('.stl'):
                    continue
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('En uploadfil er omdirigeret eller ugyldig. Bed læreren kontrollere lagringen.')
                used += info.st_size
                if used > 1024**3:
                    break
        return used

    def get_job(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Printet blev ikke fundet.")
        return dict(row)

    def active(self, printer_id):
        row = self.db.execute(f"SELECT * FROM jobs WHERE printer_id=? AND status IN ({','.join('?' for _ in ACTIVE)}) ORDER BY created LIMIT 1", (printer_id, *ACTIVE)).fetchone()
        return dict(row) if row else None

    def workflow_blocks_bed(self, printer_id, *, exclude=None):
        """A finished/aborted production still needs its physical clear step."""
        return self.db.execute('''SELECT 1 FROM jobs WHERE printer_id=? AND workflow IS NOT NULL
            AND workflow_complete=0 AND status IN ('done','failed','cancelled')
            AND id!=? LIMIT 1''', (printer_id, exclude or '')).fetchone() is not None

    @staticmethod
    def _workflow_public(job):
        binding = validate_binding(json.loads(job['workflow']))
        steps = binding['definition']['steps']
        step = steps[job['workflow_step']] if job['workflow_step'] < len(steps) else None
        return {'id': job['id'], 'printer_id': job['printer_id'], 'filename': job['filename'],
                'status': job['status'], 'version': job['workflow_version'],
                'workflow_id': binding['id'], 'workflow_revision': binding['revision'],
                'workflow_name': binding['definition']['name'], 'step': step,
                'production_authorized': bool(job['workflow_authorized'])}

    def workflow_jobs(self):
        with self.lock:
            rows = self.db.execute('''SELECT * FROM jobs WHERE workflow IS NOT NULL
                AND workflow_complete=0 ORDER BY created,rowid LIMIT 100''').fetchall()
            return [self._workflow_public(dict(row)) for row in rows]

    def advance_workflow(self, actor, envelope):
        """Confirm one pinned step; only the scheduler can cross the send barrier.

        The loopback bridge authenticates the teacher actor. Job mutation,
        receipt and audit share a commit.
        """
        if not isinstance(actor, str) or not re.fullmatch(r'teacher:[a-f0-9]{32}', actor):
            raise PermissionError('Ugyldig aktør.')
        if not isinstance(envelope, dict) or set(envelope) != {'action', 'params', 'expected_version', 'request_id'}:
            raise ValueError('Ugyldig workflowkommando.')
        if envelope['action'] != 'printer.job.advance':
            raise ValueError('Ukendt workflowkommando.')
        params, expected, request_id = envelope['params'], envelope['expected_version'], envelope['request_id']
        if (not isinstance(params, dict) or set(params) != {'id', 'step_id', 'confirmed'}
                or params['confirmed'] is not True or not isinstance(params['id'], str)
                or not re.fullmatch(r'[a-f0-9]{32}', params['id'])
                or not isinstance(params['step_id'], str) or len(params['step_id']) > 40
                or not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9]{32}', request_id)
                or type(expected) is not int or expected < 1):
            raise ValueError('Vælg et aktuelt trin, og bekræft handlingen.')
        digest = hashlib.sha256(json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                                          separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        with self.lock:
            previous = self.db.execute('SELECT digest,response FROM workflow_commands WHERE actor=? AND request_id=?',
                                       (actor, request_id)).fetchone()
            if previous:
                if previous['digest'] != digest:
                    raise ConfigConflict('Request-ID er allerede brugt til en anden ændring.')
                return json.loads(previous['response'])
            job = self.get_job(params['id'])
            if not job['workflow'] or job['workflow_complete']:
                raise ConfigConflict('Printet har ikke et åbent workflow.')
            if expected != job['workflow_version']:
                raise ConfigConflict('Workflowet er ændret. Hent den aktuelle version.')
            binding = validate_binding(json.loads(job['workflow']))
            steps, index = binding['definition']['steps'], job['workflow_step']
            step = steps[index]
            if step['id'] != params['step_id']:
                raise ConfigConflict('Det valgte trin er ikke længere aktuelt.')
            if step['role'] == 'teacher' and not actor.startswith('teacher:'):
                raise PermissionError('Dette trin kræver lærerens egen bekræftelse.')
            production_index = next(i for i, value in enumerate(steps) if value['action'] == 'produce')
            if index <= production_index and job['status'] != 'workflow_wait':
                raise ValueError('Printet kan ikke godkendes i sin aktuelle tilstand.')
            if index > production_index and job['status'] not in TERMINAL:
                raise ValueError('Afvent printets afslutning, før næste trin bekræftes.')
            printer = self.printers.get(job['printer_id'])
            if step['action'] == 'clear' and (printer is None or not self.can_release(printer, workflow_job=job['id'])):
                raise ValueError('Kontrollér, at printeren er online og stoppet uden fejl eller uafklarede print.')
            values = {'workflow_step': index + 1, 'workflow_version': expected + 1,
                      'workflow_complete': int(index + 1 == len(steps)), 'updated': self.clock()}
            if step['action'] == 'clear':
                values['physical_resolved'] = 1
            if step['action'] == 'produce':
                values['workflow_authorized'] = 1
                values['message'] = 'Printstart er godkendt. Afventer en klar printer.'
            elif index < production_index:
                values['message'] = 'Afventer næste workflowbekræftelse i Makerværkstedet.'
            response = {'ok': True, 'action': envelope['action'],
                        'result': self._workflow_public({**job, **values})}
            with self.db:
                keys = list(values)
                self.db.execute(f"UPDATE jobs SET {','.join(key+'=?' for key in keys)} WHERE id=?",
                                [values[key] for key in keys] + [job['id']])
                self.db.execute('INSERT INTO workflow_commands VALUES(?,?,?,?)',
                                (actor, request_id, digest, json.dumps(response, ensure_ascii=False)))
                self.db.execute('INSERT INTO workflow_audit(at,actor,request_id,job_id,step_id) VALUES(?,?,?,?,?)',
                                (self.clock(), actor, request_id, job['id'], step['id']))
            if step['action'] == 'clear':
                # Durable confirmation first. A crash here leaves the bed blocked
                # at restart until a new physical clearance, never auto-cleared.
                if printer['state'] == 'FAILED':
                    printer['acknowledged_stop_task'] = printer['task']
                printer.update(bed_clear=True, bed_source='teacher')
            return response

    def control_queue(self, actor, envelope):
        """Apply a versioned pause/resume with the workflow receipt ledger."""
        if not isinstance(actor, str) or not re.fullmatch(r'teacher:[a-f0-9]{32}', actor):
            raise PermissionError('Ugyldig aktør.')
        if (not isinstance(envelope, dict)
                or set(envelope) != {'action', 'params', 'expected_version', 'request_id'}
                or envelope['action'] != 'printer.queue.pause'):
            raise ValueError('Ugyldig printkøkommando.')
        params = envelope['params']
        expected = envelope['expected_version']
        request_id = envelope['request_id']
        if (not isinstance(params, dict) or set(params) != {'id', 'paused'}
                or params['id'] != 'print' or type(params['paused']) is not bool
                or type(expected) is not int or expected < 1
                or not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9]{32}', request_id)):
            raise ValueError('Ugyldig printkøkommando.')
        digest = hashlib.sha256(json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                                           separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        with self.lock:
            previous = self.db.execute(
                'SELECT digest,response FROM workflow_commands WHERE actor=? AND request_id=?',
                (actor, request_id)).fetchone()
            if previous:
                if previous['digest'] != digest:
                    raise ConfigConflict('Request-ID er allerede brugt til en anden ændring.')
                return json.loads(previous['response'])
            if expected != self.queue_control_version:
                raise ConfigConflict('Printkøens pauseversion er ændret. Hent den aktuelle version.')
            paused = params['paused']
            version = expected + 1
            response = {'ok': True, 'action': envelope['action'],
                        'result': {'id': 'print', 'paused': paused, 'version': version}}
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO settings VALUES('paused',?)",
                                (json.dumps(paused),))
                self.db.execute("INSERT OR REPLACE INTO settings VALUES('queue_control_version',?)",
                                (str(version),))
                self.db.execute('INSERT INTO workflow_commands VALUES(?,?,?,?)',
                                (actor, request_id, digest, json.dumps(response, ensure_ascii=False)))
                self.db.execute(
                    'INSERT INTO workflow_audit(at,actor,request_id,job_id,step_id) VALUES(?,?,?,?,?)',
                    (self.clock(), actor, request_id, 'queue:print',
                     'pause' if paused else 'resume'))
            self.paused = paused
            self.queue_control_version = version
            return response

    def online(self, printer):
        return printer.get('status_confirmed', False) and printer["state"] != "OFFLINE" and self.clock() - printer["state_seen"] < FRESH_SECONDS

    def available_trays(self, printer):
        if not self.online(printer) or self.clock() - printer["tray_seen"] >= FRESH_SECONDS:
            return []
        h2d = self.configs[printer['id']].get('model') == 'H2D'
        return [t for t in printer["trays"] if t.get("colour") and t.get("material") == "PLA" and t.get("profile") and str(t.get("remaining", -1)) != '0' and (not h2d or (t.get('extruder') in (0, 1) and self.clock() - t.get('topology_seen', 0) < FRESH_SECONDS))]

    def ready(self, printer):
        return (printer["enabled"] and self.online(printer) and self.stopped(printer) and not printer["error"] and self.bed_is_clear(printer) and not self.active(printer["id"]))

    @staticmethod
    def acknowledged_stop(printer):
        # A cancelled Bambu job can remain FAILED until another job starts.
        # A teacher may acknowledge that exact task;
        # any subsequent activity, error, identity change or disconnect revokes it.
        return (printer['state'] == 'FAILED' and not printer['error']
                and 'acknowledged_stop_task' in printer
                and printer['acknowledged_stop_task'] == printer['task'])

    def stopped(self, printer):
        return printer['state'] in IDLE or self.acknowledged_stop(printer)

    def can_release(self, printer, *, workflow_job=None):
        active = self.active(printer['id'])
        return (not self.sample_pending(printer) and self.online(printer) and printer['state'] in IDLE | {'FAILED'}
                and not printer['error'] and (not active or active['status'] == 'workflow_wait')
                and not self.workflow_blocks_bed(printer['id'], exclude=workflow_job))

    def camera_idle(self, printer):
        # A stopped, fault-free printer can be inspected before its bed is clear.
        # An unresolved job still blocks plate release.
        return self.stopped(printer) or (printer['state'] == 'FAILED' and self.can_release(printer))

    @staticmethod
    def sample_pending(printer):
        return printer.get('teacher_capture_pending') or printer.get('completion_capture_pending')

    def bed_is_clear(self, printer, *, for_dispatch=False):
        if self.sample_pending(printer) or self.workflow_blocks_bed(printer['id']):
            return False
        # Legacy camera clearance is never accepted after an update.
        return printer['bed_clear'] and printer.get('bed_source') == 'teacher'

    def teacher_cleared(self, printer):
        return (printer.get('bed_source') == 'teacher' and self.bed_is_clear(printer)
                and self.online(printer) and self.stopped(printer) and not printer['error'])

    def fits(self, meta, printer_id):
        size = MODELS[self.configs[printer_id].get("model", "P1S")]["size"]
        return all(a <= b for a, b in zip(meta["size"], size))

    def print_guard_settings(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key='print_guard_trigger'").fetchone()
            return {'trigger_count': int(row[0]) if row else DEFAULT_PRINT_TRIGGER,
                    'window_seconds': 3600, 'block_seconds': PRINT_BLOCK_SECONDS}

    def set_print_guard(self, trigger_count):
        if type(trigger_count) is not int or not 2 <= trigger_count <= 100:
            raise ValueError('Vælg et helt tal fra 2 til 100 for indsendelsen, der udløser en blokering.')
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('print_guard_trigger',?)", (str(trigger_count),))
            self.db.commit()

    def _reset_print_access(self, source):
        # A row watermark also works if unlock and the next print have the
        # same timestamp. The teacher's unlock must grant a fresh start.
        watermark = self.db.execute('SELECT coalesce(max(rowid),0) FROM jobs WHERE source=?', (source,)).fetchone()[0]
        self.db.execute('''UPDATE submission_access SET reset_after=?, blocked_until=0,
                           block_id=NULL, blocked_at=NULL, trigger_count=NULL WHERE source=?''', (watermark, source))
        self.db.commit()

    def print_access(self, source):
        with self.lock:
            now = self.clock()
            row = self.db.execute('SELECT * FROM submission_access WHERE source=?', (source,)).fetchone()
            if row and row['blocked_until'] and row['blocked_until'] <= now:
                self._reset_print_access(source)
                row = self.db.execute('SELECT * FROM submission_access WHERE source=?', (source,)).fetchone()
            until = row['blocked_until'] if row else 0
            retry_after = max(1, math.ceil(until - now)) if until > now else None
            message = ''
            if retry_after:
                computer = source.removeprefix('ip:')
                message = f'Computer {computer} er midlertidigt blokeret efter mange indsendelser. Adgangen åbner automatisk om {math.ceil(retry_after / 60)} min. Læreren kan åbne den tidligere fra lærersiden.'
            return {'allowed': not retry_after, 'blocked_until': until or None,
                    'retry_after': retry_after, 'message': message}

    def print_access_overview(self):
        with self.lock:
            rows = self.db.execute('SELECT * FROM submission_access WHERE blocked_until>? ORDER BY blocked_at', (self.clock(),)).fetchall()
            return {'settings': self.print_guard_settings(), 'blocked': [
                {'id': row['block_id'], 'computer': row['source'].removeprefix('ip:'),
                 'blocked_at': row['blocked_at'], 'blocked_until': row['blocked_until'],
                 'trigger_count': row['trigger_count']} for row in rows]}

    def unblock_print_access(self, block_id):
        if not isinstance(block_id, str) or not re.fullmatch(r'[a-f0-9]{32}', block_id):
            raise ValueError('Vælg en computer fra listen over blokeringer.')
        with self.lock:
            row = self.db.execute('SELECT source FROM submission_access WHERE block_id=?', (block_id,)).fetchone()
            if row:
                self._reset_print_access(row['source'])
            # Repeated clicks are harmless. An old ID cannot unlock a new ban.

    def _check_print_trigger(self, source):
        # Caller holds the queue lock through checking and inserting the job.
        row = self.db.execute('SELECT reset_after FROM submission_access WHERE source=?', (source,)).fetchone()
        reset_after = row[0] if row else 0
        recent = self.db.execute('SELECT count(*) FROM jobs WHERE source=? AND created>? AND rowid>?',
                                 (source, self.clock() - 3600, reset_after)).fetchone()[0]
        trigger = self.print_guard_settings()['trigger_count']
        if recent + 1 >= trigger:
            now = self.clock()
            self.db.execute('''INSERT INTO submission_access(source,reset_after,blocked_until,block_id,blocked_at,trigger_count)
                               VALUES(?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET
                               blocked_until=excluded.blocked_until,block_id=excluded.block_id,
                               blocked_at=excluded.blocked_at,trigger_count=excluded.trigger_count''',
                            (source, reset_after, now + PRINT_BLOCK_SECONDS, uuid.uuid4().hex, now, trigger))
            self.db.commit()
            access = self.print_access(source)
            raise SubmissionBlocked(access['message'], access['retry_after'])

    def submit(self, data, filename, colour, request_id, quality="standard", *, source='local',
               library_generation=None):
        if not isinstance(source, str) or not 1 <= len(source) <= 100:
            raise ValueError('Computeren kunne ikke genkendes. Prøv igen.')
        if quality not in QUALITIES:
            raise ValueError("Vælg Hurtig, Standard eller Smuk.")
        if not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", request_id or ""):
            raise ValueError("Prøv at sende filen igen.")
        if colour != "any" and normalize_colour(colour) != colour:
            raise ValueError("Vælg en farve fra listen.")
        colour = None if colour == "any" else colour
        digest = hashlib.sha256(data).hexdigest()

        def existing_or_conflict():
            """Return an idempotent result, or raise for a reused request id.

            This check is deliberately repeated after STL inspection.  The
            inspection is pure but relatively expensive, so it must not hold
            the queue lock; two callers may therefore pass the first check
            concurrently and only one may win the second check.
            """
            old = self.db.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["digest"] != digest or old["colour"] != colour or old["quality"] != quality or (old['source'] is not None and old['source'] != source):
                    raise ValueError("Denne indsendelse er allerede brugt til en anden fil.")
                return dict(old)
            return None

        with self.lock:
            old = existing_or_conflict()
            if old:
                return dict(old)
            access = self.print_access(source)
            if not access['allowed']:
                raise SubmissionBlocked(access['message'], access['retry_after'])
            if not filename.lower().endswith(".stl"):
                raise ValueError("Vælg en fil, der slutter med .stl.")

        # STL parsing is read-only, but can be costly for a large model.  Do
        # it outside the queue lock so status, telemetry and scheduler work
        # can continue while an upload is inspected.
        meta = inspect_stl(data)

        with self.lock:
            old = existing_or_conflict()
            if old:
                return dict(old)
            # State-dependent checks belong to the commit phase: printers,
            # trays, capacity and disk usage may have changed while parsing.
            if library_generation is not None:
                current = self.db.execute('SELECT generation FROM library_models WHERE digest=?',
                                          (digest,)).fetchone()
                if current is None or current['generation'] != library_generation:
                    raise ConfigConflict('Modellen er slettet eller ændret. Hent biblioteket igen.')
            access = self.print_access(source)
            if not access['allowed']:
                raise SubmissionBlocked(access['message'], access['retry_after'])
            # Admission is based on the teacher-enabled fleet, not on saved
            # disabled configurations.  An enabled printer may be offline at
            # this point; dispatch will wait for its later readiness report.
            enabled_printers = [pid for pid, printer in self.printers.items() if printer["enabled"]]
            if not self.demo and not enabled_printers:
                raise ValueError("En voksen skal tilføje skolens printere først.")
            if enabled_printers and not any(self.fits(meta, pid) for pid in enabled_printers):
                raise ValueError("Modellen er for stor til printerne. Gør den mindre i dit tegneprogram.")
            if colour and not any(t["colour"] == colour for p in self.printers.values() if p["enabled"] for t in self.available_trays(p)):
                raise ValueError("Farven er ikke længere tilgængelig. Vælg en anden farve eller Ligemeget.")
            count = self.db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('done','failed','cancelled')").fetchone()[0]
            if count >= 100:
                raise ValueError("Køen er fuld. Spørg en voksen, og prøv igen senere.")
            used = self._upload_bytes()
            if used + len(data) > 1024**3:
                raise ValueError("Der er ikke mere plads til modeller. Bed en voksen rydde op.")
            self._check_print_trigger(source)
            job_id = uuid.uuid4().hex
            name = Path(filename.replace("\\", "/")).name[:120]
            now = self.clock()
            # This is the durable barrier for the model. It must complete
            # before a terminal transition can ever clean the upload.
            self.library.archive(data, filename=name, colour=colour, quality=quality,
                                 meta=meta, created=now, digest=digest)
            try:
                write_upload(self.folder, job_id, digest, data)
                self.db.execute("INSERT INTO jobs (id,request_id,digest,filename,colour,meta,created,updated,status,quality,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (job_id, request_id, digest, name, colour, json.dumps(meta), now, now, "queued",quality,source))
                self.db.commit()
            except Exception:
                self.db.rollback()
                # A complete model remains accessible in the teacher library.
                # Exact, journal-owned transient files can be compensated now
                # or recovered after a process interruption.
                self.submission_recovery = recover_uploads(self.folder, self.db, self.library)
                raise
            try:
                finish_upload(self.folder, job_id, digest)
            except (OSError, ValueError) as error:
                # The job is already durable; cleanup failure must not turn a
                # successful submission into a false rejection or replay.
                log.warning('Uploadmarkør for accepteret job %s bevares til genforsøg: %s', job_id, error)
                self.submission_recovery['deferred'] = True
            return self.get_job(job_id)

    def update_report(self, printer_id, *, state=None, trays=None, progress=None, remaining=None, error=None, task=None):
        with self.lock:
            p = self.printers[printer_id]
            now = self.clock()
            previous_state = p['state']
            previous_task = p['task']
            previous_teacher_clear = self.teacher_cleared(p)
            previous_acknowledged_stop = self.acknowledged_stop(p)
            # A gap in telemetry invalidates an earlier physical bed confirmation.
            if p["state_seen"] and now - p["state_seen"] >= FRESH_SECONDS:
                p["bed_clear"] = False
                p.pop('acknowledged_stop_task', None)
                p['bed_invalidated'] = now
                p['status_confirmed'] = False
            if state is not None and task is not None and error is not None:
                if not p.get('status_confirmed'):
                    p['bed_clear'] = False
                    p['bed_invalidated'] = now
                p['status_confirmed'] = True
            p["seen"] = now
            if state is not None:
                p.update(state=str(state).upper(), state_seen=now, message="")
                if p['state'] != 'FAILED':
                    p.pop('acknowledged_stop_task', None)
            if trays is not None:
                p.update(trays=trays, tray_seen=now)
            if progress is not None:
                p["progress"] = max(0, min(100, int(progress)))
            if remaining is not None:
                p["remaining"] = max(0, int(remaining))
            if error is not None:
                p["error"] = error
                if error:
                    p.pop('acknowledged_stop_task', None)
            if task is not None:
                new_task = str(task)
                task_cleared_after_stop = (
                    previous_teacher_clear and previous_task and not new_task
                    and previous_state in IDLE | {'FAILED'}
                    and p['state'] in IDLE | {'FAILED'}
                    and not p['error']
                    and not self.active(printer_id)
                )
                if new_task != previous_task and not task_cleared_after_stop:
                    p.pop('acknowledged_stop_task', None)
                    p["bed_clear"] = False
                    p['bed_invalidated'] = now
                elif task_cleared_after_stop and previous_state == p['state'] == 'FAILED' and previous_acknowledged_stop:
                    # Clearing the old stopped task identity is not new
                    # machine activity. Keep the deliberate FAILED
                    # acknowledgement bound to the now-empty identity.
                    p['acknowledged_stop_task'] = new_task
                p["task"] = new_task
                p['task_seen'] = now
            job = self.active(printer_id)
            failed = p['state'] == 'FAILED' and not self.acknowledged_stop(p)
            if p["state"] in BUSY or p['state'] == 'OFFLINE' or p["error"] or (failed and previous_state != 'FAILED'):
                p["bed_clear"] = False
                p['bed_invalidated'] = now
            elif failed:
                # The bed stays blocked until a teacher clears the stopped job.
                p['bed_clear'] = False
            if not job or job['status'] == 'workflow_wait':
                return
            matches = self.task_matches(job, p['task'])
            if p['state'] == 'PAUSE' and p['error'] == FILAMENT_CONFIRMATION and matches and job['status'] != 'preparing':
                message = 'Printeren venter på, at filament ved dysen bekræftes på dens skærm.'
                p['message'] = message
                if state is not None and task is not None and error is not None:
                    self._change(job['id'], status='printing', progress=p['progress'], remaining=p['remaining'], message=message)
                return
            if job['status'] == 'attention':
                return
            if job['status'] == 'recovering':
                if state is not None and p['state'] != 'OFFLINE':
                    self.recovery_seen.setdefault(job['id'], now)
                # A fresh, coherent report, not cached pre-disconnect identity.
                if state is None or task is None or error is None:
                    return
                if not error and matches and p['state'] in BUSY:
                    self._change(job['id'], status='printing', progress=p['progress'], remaining=p['remaining'], message='Printet er sat på pause' if p['state'] == 'PAUSE' else 'Genfundet på printeren efter genstart.')
                elif not error and matches and p['state'] == 'FINISH':
                    self._change(job['id'], status='done', progress=100, remaining=0, message='Printeren bekræftede, at printet er færdigt.')
                else:
                    self._change(job['id'], status='attention', message='Printet kunne ikke afklares sikkert efter genstart. En voksen skal kontrollere printeren.')
                return
            if failed and not p['error'] and job['status'] == 'printing':
                # Bambu reports a user-aborted job as FAILED with error=0.
                # Wait for coherent fresh telemetry; never replay the stopped job.
                if state is None or task is None or error is None:
                    return
                if matches:
                    self._change(job['id'], status='cancelled', remaining=0,
                                 message='Printet er afbrudt. Venter på en ryddet plade.')
                    return
            if p["error"] or failed:
                self._change(job["id"], status="attention", message="Printeren melder en fejl. En voksen skal kigge på den.")
            elif p["state"] in BUSY and job["status"] != "preparing":
                if matches:
                    self._change(job["id"], status="printing", progress=p["progress"], remaining=p["remaining"], message="Printet er sat på pause" if p["state"] == "PAUSE" else "")
                elif p["task"]:
                    self._change(job["id"], status="attention", message="Printeren kører et andet job. Kontrollér printeren.")
            elif state == "FINISH" and task is not None and job["status"] == "printing" and matches:
                self._change(job["id"], status="done", progress=100, remaining=0, message="Dit print er færdigt.")
            elif p["state"] == "IDLE" and job["status"] == "printing":
                self._change(job["id"], status="attention", message="Printet er stoppet uden en færdigmelding. Kontrollér printeren.")

    @staticmethod
    def task_matches(job, task):
        name = (job.get('remote_name') or '').removesuffix('.gcode.3mf')
        if not name:
            return False
        # Require a complete unique name, never a substring of someone else's job.
        identities = re.findall(r'(?<![\w.-])pv_[\w-]+(?:\.gcode\.3mf)?(?![\w.-])', str(task))
        return bool(identities) and all(value.removesuffix('.gcode.3mf') == name for value in identities)

    def offline(self, printer_id, message="Forbindelsen er afbrudt"):
        with self.lock:
            self.printers[printer_id].pop('acknowledged_stop_task', None)
            self.printers[printer_id].update(state="OFFLINE", bed_clear=False, message=message)
            self.printers[printer_id].update(task='', task_seen=0, state_seen=0, tray_seen=0, status_confirmed=False)
            job = self.active(printer_id)
            if job:
                self.recovery_seen.pop(job['id'], None)
                if job['status'] in ('dispatching', 'starting', 'printing'):
                    self._change(job['id'], status='recovering', message='Venter på printerens aktuelle job efter afbrudt forbindelse.')
            self.printers[printer_id]['bed_invalidated'] = self.clock()

    def release_bed(self, printer_id):
        with self.lock:
            p = self.printers[printer_id]
            if not self.can_release(p):
                raise ValueError("Printeren skal være online og stoppet uden fejl eller uafklarede print.")
            # Preserve the historical isolation flag for safe retries while
            # recording the teacher's later physical clearance durably.
            with self.db:
                self.db.execute("UPDATE jobs SET physical_resolved=1 WHERE printer_id=? "
                                "AND isolated=1 AND status IN ('done','failed','cancelled') "
                                "AND (workflow IS NULL OR workflow_complete=1)", (printer_id,))
            if p['state'] == 'FAILED':
                p['acknowledged_stop_task'] = p['task']
            p["bed_clear"] = True
            p['bed_source'] = 'teacher'

    def pause(self, paused):
        with self.lock:
            paused = bool(paused)
            version = self.queue_control_version + 1
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO settings VALUES('paused',?)", (json.dumps(paused),))
                self.db.execute("INSERT OR REPLACE INTO settings VALUES('queue_control_version',?)", (str(version),))
            self.paused = paused
            self.queue_control_version = version

    def resolution_options(self, job):
        if job['status'] not in ('attention', 'recovering'):
            return []
        p = self.printers.get(job['printer_id'])
        if not p or self.sample_pending(p):
            return []
        if not self.online(p):
            # Isolation closes only our queue record. It makes no statement
            # about a disconnected printer's physical activity or empty bed.
            return ['isolated']
        if job['status'] != 'attention':
            return []
        # FAILED is a terminal machine state too. Requiring IDLE here traps
        # an aborted job: that job itself blocks camera/manual bed release.
        if p['state'] == 'FAILED' or (p['state'] in IDLE and p['error']):
            # Closing a stopped job never clears a printer fault or its bed.
            return ['cancelled', 'failed']
        return ['done', 'cancelled', 'failed'] if p['state'] in IDLE else []

    def resolve(self, job_id, result):
        with self.lock:
            job = self.get_job(job_id)
            if result == 'isolated':
                if job['status'] == 'cancelled' and job['isolated']:
                    return  # A lost response must not invalidate a later clear.
                if result not in self.resolution_options(job):
                    raise ValueError('Kun et uafklaret job uden frisk printerforbindelse kan isoleres.')
                p = self.printers[job['printer_id']]
                self._change(job_id, status='cancelled', isolated=1, remaining=0,
                             message='Uafklaret job er isoleret af læreren. Printeren kan stadig køre. Kontrollér den fysisk; pladen er ikke frigivet.')
                p.update(bed_clear=False, bed_invalidated=self.clock())
                p.pop('acknowledged_stop_task', None)
                return
            if job["status"] in ("queued", "workflow_wait") and result == "cancelled":
                self._change(job_id, status="cancelled", message="Printet er fjernet fra køen.")
                return
            if job["status"] != "attention" or result not in {"done", "failed", "cancelled"}:
                raise ValueError("Dette print kan ikke afsluttes her.")
            if result not in self.resolution_options(job):
                raise ValueError("Kontrollér først, at printeren er forbundet og melder, at printet er stoppet.")
            p = self.printers[job['printer_id']]
            # Acknowledging a stopped job is not a claim that its plate is empty.
            p.update(bed_clear=False, bed_invalidated=self.clock())
            p.pop('acknowledged_stop_task', None)
            self._change(job_id, status=result, remaining=0,
                         message='Afbrydelsen er bekræftet af en lærer. Venter på en ryddet plade.'
                         if result == 'cancelled' else 'Afklaret af en voksen. Venter på en ryddet plade.')

    def reserve(self):
        with self.lock:
            now = self.clock()
            for job_id, seen in list(self.recovery_seen.items()):
                job = self.get_job(job_id)
                if job['status'] == 'recovering' and now - seen > 90:
                    self._change(job_id, status='attention', message='Printeren gav ikke en entydig jobstatus. En voksen skal kontrollere printeren.')
            self.db.execute("UPDATE jobs SET status='attention',message='Ingen sikker bekræftelse på printstart. Kontrollér printeren.',updated=? WHERE status IN ('dispatching','starting') AND updated<?", (now, now - 90))
            self.db.commit()
            if self.paused:
                return None
            # These jobs already own a printer and an immutable workflow. Neither
            # a retry nor a restart may move them to an ungoverned printer.
            for row in self.db.execute("SELECT * FROM jobs WHERE status='workflow_wait' AND workflow_authorized=1 AND retry_after<=? ORDER BY created,rowid", (now,)):
                job = dict(row)
                p = self.printers.get(job['printer_id'])
                if not p or not (p['enabled'] and self.online(p) and self.stopped(p)
                                  and not p['error'] and self.bed_is_clear(p)):
                    continue
                for tray in self.available_trays(p):
                    if job['colour'] is None or job['colour'] == tray['colour']:
                        self._change(job['id'], status='preparing', slot=tray['slot'],
                                     extruder=tray.get('extruder', 0), actual_colour=tray['colour'],
                                     profile=tray['profile'], message='Klargør det godkendte print.')
                        return self.get_job(job['id'])
            for row in self.db.execute("SELECT * FROM jobs WHERE status='queued' AND retry_after<=? ORDER BY created, rowid", (now,)):
                job = dict(row)
                # Oldest eligible job first: a red job must not block a free blue printer.
                for p in self.printers.values():
                    if not self.ready(p) or not self.fits(json.loads(job["meta"]), p["id"]):
                        continue
                    for tray in self.available_trays(p):
                        if job["colour"] is None or job["colour"] == tray["colour"]:
                            binding = validate_binding(self.configs[p['id']].get('workflow'))
                            if binding is not None:
                                self._change(job['id'], status='workflow_wait', printer_id=p['id'],
                                    slot=tray['slot'], extruder=tray.get('extruder', 0), actual_colour=tray['colour'],
                                    profile=tray['profile'], remote_name=f"pv_{job['id']}.gcode.3mf",
                                    workflow=json.dumps(binding, ensure_ascii=False, allow_nan=False),
                                    workflow_step=1, workflow_version=1, workflow_authorized=0, workflow_complete=0,
                                    message='Afventer workflowets bekræftelser i Makerværkstedet.')
                                return None
                            self._change(job["id"], status="preparing", printer_id=p["id"], slot=tray["slot"], extruder=tray.get('extruder', 0), actual_colour=tray["colour"], profile=tray["profile"], remote_name=f"pv_{job['id']}.gcode.3mf")
                            return self.get_job(job["id"])
            return None

    def begin_dispatch(self, job_id):
        with self.lock:
            job = self.get_job(job_id)
            if job['status'] != 'preparing':
                return False
            if job['workflow'] and not job['workflow_authorized']:
                self._change(job_id, status='workflow_wait', message='Afventer lærerens godkendelse af printstart.')
                return False
            p = self.printers[job["printer_id"]]
            match = any(t["slot"] == job["slot"] and t.get('extruder', 0) == job['extruder'] and t["colour"] == job["actual_colour"] and t["profile"] == job["profile"] for t in self.available_trays(p))
            if (self.paused or job["status"] != "preparing" or not p["enabled"] or not self.online(p) or not self.stopped(p) or p["error"] or not self.bed_is_clear(p, for_dispatch=True) or not match):
                if job['workflow']:
                    self._change(job_id, status='workflow_wait', message='Afventer den valgte printer og fysisk pladekontrol.')
                else:
                    self._change(job_id, status="queued", printer_id=None, slot=None, extruder=None, actual_colour=None, profile=None, message="Venter på en ledig printer med den valgte farve.")
                return False
            # Commit uncertainty BEFORE making a physical start request.
            p["bed_clear"] = False
            p['bed_invalidated'] = self.clock()
            self._change(job_id, status="dispatching", message="Sender til printeren")
            return True

    def dispatched(self, job_id):
        with self.lock:
            if self.get_job(job_id)["status"] == "dispatching":
                self._change(job_id, status="starting", message="Venter på printerens bekræftelse")

    def fail(self, job_id, message, uncertain=False):
        with self.lock:
            self._change(job_id, status="attention" if uncertain else "failed", message=message)

    def begin_transfer(self, job_id):
        """Charge the attempt before I/O, so a crash cannot reset the limit."""
        with self.lock:
            job = self.get_job(job_id)
            if job['status'] != 'preparing':
                return False
            if job['transfer_attempts'] >= TRANSFER_ATTEMPT_LIMIT:
                self._change(job_id, status='attention', message='Overførselsgrænsen er nået efter tre forsøg. Kontrollér forbindelsen og se fejlrapporten.')
                return False
            self._change(job_id, transfer_attempts=job['transfer_attempts'] + 1)
            return True

    def transfer_succeeded(self, job_id):
        with self.lock:
            job = self.get_job(job_id)
            if job['status'] == 'preparing':
                self._change(job_id, transfer_attempts=0)
                self.printers[job['printer_id']].pop('transfer_error', None)

    def retry_preparation(self, job_id, message=None):
        with self.lock:
            job = self.get_job(job_id)
            if job['status'] == 'preparing':
                if message:
                    self.printers[job['printer_id']]['transfer_error'] = message
                if job['transfer_attempts'] >= TRANSFER_ATTEMPT_LIMIT:
                    self._change(job_id, status='attention', message=(message or 'Overførslen til printeren fejlede.') + ' Tre forsøg er brugt. En lærer skal afklare jobbet.')
                    return
                retry_message = (message + ' Nyt forsøg om 30 sekunder.' if message else None)
                if job['workflow']:
                    self._change(job_id, status='workflow_wait', retry_after=self.clock() + 30,
                                 message=retry_message or 'Afventer den valgte printer. Workflow og godkendelse er bevaret.')
                else:
                    self._change(job_id, status='queued', printer_id=None, slot=None, extruder=None,
                                 actual_colour=None, profile=None, remote_name=None, retry_after=self.clock() + 30,
                                 message=retry_message or 'Venter på forbindelse. Klargøring forsøges igen automatisk.')

    def snapshot(self, admin=False):
        with self.lock:
            printers, colours = [], {}
            for p in self.printers.values():
                active = self.active(p["id"])
                online = self.online(p)
                status = "offline" if not online else "disabled" if not p["enabled"] else "attention" if p["error"] or (p["state"] == "FAILED" and active and not self.acknowledged_stop(p)) or (active and active["status"] == "attention") else "printing" if p["state"] in BUSY else "preparing" if active else "ready" if self.bed_is_clear(p) else "bed"
                trays = self.available_trays(p)
                if p["enabled"]:
                    for t in trays:
                        entry = colours.setdefault(t["colour"], {"hex": t["colour"], "name": colour_name(t["colour"]), "ready": 0, "printers": []})
                        if p["id"] not in entry["printers"]:
                            entry["printers"].append(p["id"])
                            entry["ready"] += int(status == "ready")
                printers.append({"id": p["id"], "name": p["name"], "model": self.configs[p["id"]].get("model", "P1S"), "status": status, "machine_state": p['state'] if online else 'OFFLINE', "progress": p["progress"], "remaining": p["remaining"], "colours": [t["colour"] for t in trays], "bed_clear": self.bed_is_clear(p), "can_release": p['enabled'] and not self.bed_is_clear(p) and self.can_release(p), "message": p["message"] or p.get("transfer_error", "") or ("Pladen afventer afhentning og rydning i arbejdsgangen. Fortsæt i Makerværkstedets workflowoversigt." if self.workflow_blocks_bed(p["id"]) else "") or ("Printeren melder stadig en fejl. Kontrollér beskeden på printerens skærm, før pladen kan frigives." if p["error"] else ""), "error_code": p["error"], "enabled": p["enabled"]})
            rows = self.db.execute("SELECT * FROM jobs ORDER BY CASE WHEN status IN ('done','failed','cancelled') THEN 1 ELSE 0 END, created DESC LIMIT 100").fetchall()
            jobs = [{k: row[k] for k in ("id", "filename", "colour", "actual_colour", "created", "status", "printer_id", "message", "progress", "remaining", "quality", "isolated", "physical_resolved")} for row in rows]
            for job in jobs:
                job["meta"] = json.loads(next(row["meta"] for row in rows if row["id"] == job["id"]))
                job['resolution_options'] = self.resolution_options(job)
            return {"demo": self.demo, "paused": self.paused, "printers": printers, "colours": list(colours.values()), "jobs": jobs, "ready": sum(p["status"] == "ready" for p in printers), "queued": sum(j["status"] == "queued" for j in jobs)}
