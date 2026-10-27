"""Lokale projektfiler med uforanderlige versioner. Ingen personlige elevkonti.

SQLite (Python standardbibliotek) holder metadata/transaktioner; filer skrives og
fsync'es før metadata offentliggøres. Historik slettes aldrig via elev-API'et.
Dette er versionshistorik på elevcomputeren, ikke en separat katastrofebackup.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

MAX_FILE = 50 * 1024 * 1024
MAX_TOTAL = 5 * 1024 * 1024 * 1024
RETENTION_DAYS = 30
RETENTION_SECONDS = RETENTION_DAYS * 24 * 60 * 60
ID = re.compile(r'^[a-f0-9]{32}$')
EXTENSIONS = ('.projekt.svg', '.klosterskitse', '.sb3', '.svg', '.hex', '.json',
              '.stl', '.zip', '.csv', '.txt', '.piskel', '.wav', '.mp3', '.png')
TYPES = {'laser', 'folie', 'scratch', 'microbit', 'datatraener', 'cadoodle', 'kredsloeb', 'andet'}


class Konflikt(ValueError):
    pass


def tekst(value, maximum, field):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{field} skal have 1–{maximum} tegn.')
    if any(ord(c) < 32 for c in value):
        raise ValueError(f'{field} indeholder ugyldige tegn.')
    return value.strip()


class Projektarkiv:
    def __init__(self, root, max_total=MAX_TOTAL, retention_days=RETENTION_DAYS):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.files = self.root / 'versioner'
        self.files.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.max_total = max_total
        if type(retention_days) is not int or retention_days <= 0:
            raise ValueError('Ugyldig opbevaringsperiode.')
        self.retention_days = retention_days
        self.retention_seconds = retention_days * 24 * 60 * 60
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT NOT NULL,
                  kind TEXT NOT NULL, filename TEXT NOT NULL, revision INTEGER NOT NULL,
                  modified REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS versions (
                  project TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL,
                  size INTEGER NOT NULL, created REAL NOT NULL, source TEXT NOT NULL,
                  PRIMARY KEY(project, revision));
                CREATE TABLE IF NOT EXISTS retention_pending (
                  project TEXT PRIMARY KEY, source_record TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'projekter.sqlite3', timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self, query='', archived=False):
        if not isinstance(query, str) or len(query) > 120:
            raise ValueError('Søgningen er for lang.')
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT * FROM projects WHERE archived=? ORDER BY modified DESC LIMIT 2000',
                              (int(archived),)).fetchall()
        q = query.casefold()
        return [self._public_meta(dict(r)) for r in rows
                if q in (r['name']+' '+r['folder']+' '+r['kind']).casefold()]

    def meta(self, project):
        self.valid_id(project)
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM projects WHERE id=?', (project,)).fetchone()
            if row is None:
                raise KeyError('Projektet findes ikke.')
            result = self._public_meta(dict(row))
            result['versions'] = [dict(v) for v in db.execute(
                'SELECT revision,size,created,source FROM versions WHERE project=? ORDER BY revision DESC', (project,))]
            return result

    def _public_meta(self, result):
        result['retention_days'] = self.retention_days
        result['expires_at'] = result['modified'] + self.retention_seconds
        return result

    @staticmethod
    def valid_id(project):
        if not isinstance(project, str) or not ID.fullmatch(project):
            raise ValueError('Ugyldigt projekt-id.')

    def save(self, data, *, project, name, folder, kind, filename, expected, source='browser'):
        self.valid_id(project)
        name, folder = tekst(name, 100, 'Projektnavn'), tekst(folder, 100, 'Forløb / gruppe')
        filename = tekst(filename, 180, 'Filnavn')
        if '/' in filename or '\\' in filename or ':' in filename or filename in ('.', '..'):
            raise ValueError('Ugyldigt filnavn.')
        if not filename.lower().endswith(EXTENSIONS) or kind not in TYPES:
            raise ValueError('Denne projekttype understøttes ikke.')
        if type(expected) is not int or expected < 0:
            raise ValueError('Ugyldig versionsangivelse.')
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE:
            raise ValueError('Filen skal være mellem 1 byte og 50 MB.')
        source = tekst(source, 80, 'Kilde')
        digest = hashlib.sha256(data).hexdigest()
        with self.lock, self.connect() as db:
            old = db.execute('SELECT * FROM projects WHERE id=?', (project,)).fetchone()
            revision = old['revision'] if old else 0
            if old:
                if old['kind'] != kind or old['filename'] != filename:
                    raise ValueError('Filtype og filnavn kan ikke skiftes på et eksisterende projekt. Gem en kopi.')
                latest = db.execute('SELECT digest FROM versions WHERE project=? AND revision=?', (project, revision)).fetchone()
                if latest['digest'] == digest and old['name'] == name and old['folder'] == folder and not old['archived']:
                    return self.meta(project)  # safe retry, no duplicate version
            if expected != revision:
                raise Konflikt('Der er gemt en nyere version. Åbn den, eller gem dit arbejde som en ny kopi.')
            if old and old['archived']:
                raise Konflikt('Projektet er arkiveret. Gem dit arbejde som en ny kopi.')
            # Count actual immutable blobs, including an orphan after a failed write.
            total = sum(p.stat().st_size for p in self.files.glob('*/*.bin'))
            if total + len(data) > self.max_total:
                raise OSError('Projektarkivet er fyldt. Bed læreren om hjælp; tidligere versioner bevares.')
            directory = self.files / project
            directory.mkdir(exist_ok=True)
            revision += 1
            final = directory / f'{revision}.bin'
            temp = directory / f'{uuid.uuid4().hex}.tmp'
            try:
                with temp.open('xb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, final)
                now = time.time()
                db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?)', (project, revision, digest, len(data), now, source))
                # A saved edit supersedes an interrupted expiry attempt. Its
                # new source inventory must be used when it eventually expires.
                db.execute('DELETE FROM retention_pending WHERE project=?', (project,))
                db.execute('''INSERT INTO projects VALUES (?,?,?,?,?,?,?,0)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name, folder=excluded.folder,
                    revision=excluded.revision, modified=excluded.modified''',
                    (project, name, folder, kind, filename, revision, now))
                db.commit()
            finally:
                temp.unlink(missing_ok=True)
            return self.meta(project)

    def read(self, project, revision=None):
        with self.lock:
            meta = self.meta(project)
            revision = meta['revision'] if revision is None else revision
            if type(revision) is not int or revision <= 0:
                raise ValueError('Ugyldig version.')
            with self.connect() as db:
                row = db.execute('SELECT * FROM versions WHERE project=? AND revision=?', (project, revision)).fetchone()
            if row is None:
                raise KeyError('Versionen findes ikke.')
            data = (self.files / project / f'{revision}.bin').read_bytes()
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['digest']:
                raise OSError('Filens kontrolsum stemmer ikke. Kontakt læreren.')
            return meta, data

    def archive(self, project, archived):
        self.meta(project)
        if type(archived) is not bool:
            raise ValueError('Ugyldigt arkivvalg.')
        with self.lock, self.connect() as db:
            db.execute('UPDATE projects SET archived=? WHERE id=?', (int(archived), project))
        return self.meta(project)

    def cleanup(self, originals=None, *, projects_root=None, now=None):
        """Remove expired projects and verified originals.

        ``originals`` is an optional mapping from project id to scanner records.
        Records are deliberately supplied by the scanner: the archive never
        guesses which pupil files belong to a project.  A record must contain
        ``type`` (``file`` or ``cadoodle``), ``relative`` and a stat/digest
        captured after two stable scans. Unverifiable scanned originals defer
        expiry of both the source and its history. Known missing sources are safe.
        """
        originals = originals or {}
        root = Path(projects_root).resolve() if projects_root is not None else None
        current = time.time() if now is None else float(now)
        removed, failed = [], []
        with self.lock:
            with self.connect() as db:
                rows = db.execute(
                    'SELECT * FROM projects WHERE modified<=? ORDER BY modified ASC',
                    (current - self.retention_seconds,)).fetchall()
            for row in rows:
                project = row['id']
                try:
                    # Validate every archive path before touching anything.  The
                    # project id is constrained, but keep the check explicit so
                    # future schema/import changes cannot widen the delete scope.
                    directory = self._archive_directory(project)
                    versions = self._version_paths(project, directory)
                    record = originals.get(project)
                    with self.connect() as db:
                        pending = db.execute(
                            'SELECT source_record FROM retention_pending WHERE project=?',
                            (project,)).fetchone()
                    if pending:
                        try:
                            record = json.loads(pending['source_record'])
                        except (TypeError, ValueError):
                            raise OSError('Ugyldig oprydningsrecord.')
                    with self.connect() as db:
                        source = db.execute(
                            'SELECT source FROM versions WHERE project=? ORDER BY revision DESC LIMIT 1',
                            (project,)).fetchone()
                    scanner_source = source and source['source'] == 'Projekter-mappen'
                    if scanner_source and not pending and (record is None or root is None):
                        raise OSError('Originalen er ikke sikkert identificeret.')
                    source_paths = self._verified_original_paths(
                        record, root, forbidden_root=self.root,
                        allow_missing=bool(pending) or bool(isinstance(record, dict) and record.get('missing')))
                    if scanner_source and root is None:
                        raise OSError('Originalens rodmappe mangler.')
                    if scanner_source and source_paths is None:
                        raise OSError('Originalen er ændret eller kan ikke verificeres.')
                    if source_paths is None:
                        source_paths = []
                    if scanner_source and not pending and source_paths:
                        with self.connect() as db:
                            db.execute(
                                'INSERT INTO retention_pending(project,source_record) VALUES(?,?) '
                                'ON CONFLICT(project) DO UPDATE SET source_record=excluded.source_record',
                                (project, json.dumps(record, ensure_ascii=False)))
                    self._delete_paths(source_paths)
                    self._delete_paths(versions)
                    try:
                        directory.rmdir()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        # An unrelated/untracked file is a reason to leave it
                        # alone; all referenced version blobs were still removed.
                        pass
                    with self.connect() as db:
                        db.execute('DELETE FROM versions WHERE project=?', (project,))
                        db.execute('DELETE FROM projects WHERE id=?', (project,))
                        db.execute('DELETE FROM retention_pending WHERE project=?', (project,))
                    removed.append(project)
                except (OSError, ValueError, KeyError) as error:
                    failed.append({'id': project, 'error': str(error)})
        return {'deleted': removed, 'failed': failed}

    def _archive_directory(self, project):
        self.valid_id(project)
        directory = (self.files / project)
        files_root = self.files.resolve()
        resolved = directory.resolve(strict=False)
        if directory.is_symlink() or resolved.parent != files_root or resolved != directory:
            raise OSError('Ugyldig versionssti.')
        if directory.exists() and not directory.is_dir():
            raise OSError('Ugyldig versionsmappe.')
        return directory

    def _version_paths(self, project, directory):
        with self.connect() as db:
            rows = db.execute('SELECT revision FROM versions WHERE project=?', (project,)).fetchall()
        paths = []
        for row in rows:
            revision = row['revision']
            if type(revision) is not int or revision <= 0:
                raise OSError('Ugyldig versionsnummer.')
            path = directory / f'{revision}.bin'
            resolved = path.resolve(strict=False)
            if path.is_symlink() or resolved.parent != directory.resolve() or resolved != path:
                raise OSError('Ugyldig versionsfil.')
            if path.exists() and not path.is_file():
                raise OSError('En versionsfil mangler.')
            paths.append(path)
        return paths

    @staticmethod
    def _verified_original_paths(record, root, forbidden_root=None, allow_missing=False):
        if not record or root is None:
            return None
        if not isinstance(record, dict) or not isinstance(record.get('relative'), str):
            return None
        base = root.resolve()
        forbidden = Path(forbidden_root).resolve() if forbidden_root is not None else None
        missing_expected = record.get('missing') is True
        if record.get('type') == 'file':
            entries = [{'relative': record['relative'], 'stat': record.get('stat'),
                        'digest': record.get('digest')}]
        elif record.get('type') == 'cadoodle':
            entries = record.get('files')
            if not isinstance(entries, list) or not entries:
                return None
            directory_value = record.get('directory')
            if not isinstance(directory_value, str):
                return None
            directory_path = Path(directory_value)
            if directory_path.is_absolute():
                return None
            directory = base / directory_path
            directory_resolved = directory.resolve(strict=False)
            if (directory.is_symlink() or not directory_resolved.is_relative_to(base) or
                    directory_resolved != directory or (forbidden and directory_resolved.is_relative_to(forbidden))):
                return None
        else:
            return None
        result = []
        captured = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('relative'), str):
                return None
            captured.add(entry['relative'])
            entry_path = Path(entry['relative'])
            if entry_path.is_absolute():
                return None
            path = base / entry_path
            resolved = path.resolve(strict=False)
            if (path.is_symlink() or not resolved.is_relative_to(base) or
                    resolved != path or (forbidden and resolved.is_relative_to(forbidden))):
                return None
            try:
                stat = path.stat()
            except FileNotFoundError:
                if allow_missing and not path.is_symlink():
                    continue
                return None
            except OSError:
                return None
            if missing_expected:
                return None
            expected = entry.get('stat')
            if not isinstance(expected, list) or len(expected) != 2 or [stat.st_mtime_ns, stat.st_size] != expected:
                return None
            digest = entry.get('digest')
            try:
                current_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return None
            if not isinstance(digest, str) or current_digest != digest:
                return None
            result.append(path)
        if record.get('type') == 'cadoodle':
            current = set()
            try:
                for parent, dirs, names in os.walk(directory, followlinks=False):
                    dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(parent) / d).is_symlink()]
                    for name in names:
                        path = Path(parent) / name
                        if not name.startswith('.') and not path.is_symlink() and path.is_file():
                            current.add(path.relative_to(base).as_posix())
            except OSError:
                return None
            if missing_expected:
                return [] if not result and not current else None
            expected_inventory = {path.relative_to(base).as_posix() for path in result} if allow_missing else captured
            if current != expected_inventory:
                return None
        return result

    @staticmethod
    def _delete_paths(paths):
        for path in paths:
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                raise OSError('Original eller versionsfil er ikke en almindelig fil.')
            path.unlink()
