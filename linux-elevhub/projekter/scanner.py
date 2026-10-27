"""Snapshot stable project files and expire verified projects after 30 days."""
import hashlib
import io
import json
import logging
import os
import threading
import time
import uuid
import zipfile
from pathlib import Path
from projektarkiv import EXTENSIONS, MAX_FILE
from filtyper import project_kind


def laesbar(tekst):
    """Filenames from old Windows USB media are often cp1252, not UTF-8.

    Python exposes such bytes as surrogates, which cannot be stored in the
    index or the archive. Show the intended name instead of losing the file.
    Valid UTF-8 names are returned unchanged, so existing project ids stay.
    """
    try:
        tekst.encode('utf-8')
        return tekst
    except UnicodeEncodeError:
        return os.fsencode(tekst).decode('cp1252', 'replace')


def kind_for(path, data=None):
    name = str(path).lower()
    for word, kind in [('scratch', 'scratch'), ('folie', 'folie'), ('laser', 'laser'),
                       ('datatræner', 'datatraener'), ('datatraener', 'datatraener'),
                       ('kredsløb', 'kredsloeb'), ('kredsloeb', 'kredsloeb'),
                       ('cadoodle', 'cadoodle'), ('microbit', 'microbit')]:
        if word in name:
            return project_kind(path.name, kind, data)
    return project_kind(path.name, 'andet', data)


class Scanner:
    def __init__(self, projects, store):
        self.projects, self.store = Path(projects).resolve(), store
        self.projects.mkdir(parents=True, exist_ok=True)
        self.index = store.root / 'filindeks.json'
        try:
            self.saved = json.loads(self.index.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.saved = {}
        self.seen, self.error = {}, ''
        self.lock = threading.RLock()

    def cadoodle(self, directory, doodle, observed):
        """Snapshot a complete native project, including imported STL dependencies."""
        relative = doodle.relative_to(self.projects)
        key = laesbar(relative.as_posix())
        files = []
        for parent, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(parent)/d).is_symlink()]
            for name in sorted(names):
                path = Path(parent)/name
                if not name.startswith('.') and not path.is_symlink() and path.is_file():
                    files.append(path)
        files.sort()
        signature = [[p.relative_to(directory).as_posix(), p.stat().st_mtime_ns, p.stat().st_size] for p in files]
        if len(files) > 5000 or sum(item[2] for item in signature) > MAX_FILE:
            raise ValueError('3D-projektet er for stort til projektarkivet.')
        observed[key] = signature
        prior = self.saved.get(key, {})
        if self.seen.get(key) != signature:
            return False
        if prior.get('stat') == signature:
            if not prior.get('source'):
                source = self._cadoodle_source(relative, signature, files)
                if source:
                    prior['source'] = source
                    self.saved[key] = prior
                    return True
            return False
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                # Fixed timestamps make unchanged project snapshots byte-identical.
                entry = zipfile.ZipInfo(laesbar(path.relative_to(directory).as_posix()))
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, path.read_bytes())
        if signature != [[p.relative_to(directory).as_posix(), p.stat().st_mtime_ns, p.stat().st_size] for p in files]:
            return False
        project = uuid.uuid5(uuid.NAMESPACE_URL, 'elevhub-local:'+key).hex
        name = laesbar(doodle.stem)[:100]
        try:
            native_name = json.loads(doodle.read_text(encoding='utf-8')).get('projectName')
            if isinstance(native_name, str) and native_name.strip() and not any(ord(c) < 32 for c in native_name):
                name = native_name.strip()[:100]
        except (ValueError, AttributeError, UnicodeError):
            pass
        try:
            expected = self.store.meta(project)['revision']
        except KeyError:
            expected = 0
        result = self.store.save(buffer.getvalue(), project=project, name=name,
            folder=laesbar(str(relative.parent.parent))[:100] or '3D-værkstedet', kind='cadoodle',
            filename=laesbar(doodle.stem)[:170]+'.zip', expected=expected, source='Projekter-mappen')
        self.saved[key] = {'stat': signature, 'id': project, 'revision': result['revision'],
                           'source': self._cadoodle_source(relative, signature, files)}
        return True

    def _cadoodle_source(self, relative, signature, files):
        entries = []
        for path, item in zip(files, signature):
            try:
                relative_path = path.relative_to(self.projects).as_posix()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, UnicodeEncodeError):
                return None
            entries.append({'relative': relative_path, 'stat': item[1:], 'digest': digest})
        try:
            relative.as_posix().encode('utf-8')
        except UnicodeEncodeError:
            return None
        return {'type': 'cadoodle', 'relative': relative.as_posix(),
                'directory': relative.parent.as_posix(), 'files': entries}

    def scan(self):
        with self.lock:
            self._scan()

    def _scan(self):
        observed, changed = {}, False
        self.error = ''
        walk_errors = []
        for directory, dirs, names in os.walk(self.projects, onerror=walk_errors.append, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and
                       not (Path(directory)/d).is_symlink() and
                       not (Path(directory)/d).resolve().is_relative_to(self.store.root.resolve())]
            doodles = [Path(directory)/n for n in names if n.lower().endswith('.doodle') and not (Path(directory)/n).is_symlink()]
            if len(doodles) == 1:
                try:
                    changed = self.cadoodle(Path(directory), doodles[0], observed) or changed
                except (OSError, ValueError):
                    self.error = 'Et 3D-projekt kunne ikke gemmes i oversigten. Originalen er bevaret.'
                dirs[:] = []
                continue
            for name in names:
                p = Path(directory) / name
                if name.startswith('.') or not name.lower().endswith(EXTENSIONS) or p.is_symlink():
                    continue
                try:
                    resolved = p.resolve()
                    if not resolved.is_relative_to(self.projects) or resolved.is_relative_to(self.store.root.resolve()):
                        continue
                    relative = p.relative_to(self.projects)
                    key = laesbar(relative.as_posix())
                    stat = p.stat()
                    signature = [stat.st_mtime_ns, stat.st_size]
                    observed[key] = signature
                    if not 0 < stat.st_size <= MAX_FILE or self.seen.get(key) != signature:
                        continue  # two consecutive polls before reading an in-progress write
                    prior = self.saved.get(key, {})
                    if prior.get('stat') == signature and prior.get('source'):
                        continue
                    data = p.read_bytes()
                    after = p.stat()
                    if [after.st_mtime_ns, after.st_size] != signature:
                        continue
                    if prior.get('stat') == signature:
                        if not prior.get('source'):
                            migrated = self._file_source(relative, signature, data)
                            if migrated:
                                prior['source'] = migrated
                                self.saved[key] = prior
                                changed = True
                        continue
                    project = uuid.uuid5(uuid.NAMESPACE_URL, 'elevhub-local:'+key).hex
                    kind = kind_for(relative, data)
                    try:
                        existing = self.store.meta(project)
                        expected, kind = existing['revision'], existing['kind']
                    except KeyError:
                        expected = 0
                    folder = laesbar(str(relative.parent)) if relative.parent != Path('.') else 'Mine projekter'
                    result = self.store.save(data, project=project, name=laesbar(p.stem)[:100],
                        folder=folder[:100], kind=kind, filename=laesbar(name),
                        expected=expected, source='Projekter-mappen')
                    self.saved[key] = {
                        'stat': signature, 'id': project, 'revision': result['revision'],
                        'source': self._file_source(relative, signature, data),
                    }
                    changed = True
                except (OSError, ValueError) as e:
                    self.error = 'Nogle filer kunne ikke få en version. Originalerne ligger stadig i Projekter.'
                    logging.warning('Lokalt projektindeks: %s', e)
        self.seen = observed
        if walk_errors:
            self.error = 'Projektoversigten kunne ikke gennemgå alle mapper. Originalerne er bevaret.'
        if changed:
            self._write_index()

    def _write_index(self):
        temporary = self.index.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.saved, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.index)

    def _file_source(self, relative, signature, data):
        try:
            relative.as_posix().encode('utf-8')
        except UnicodeEncodeError:
            return None
        return {'type': 'file', 'relative': relative.as_posix(),
                'stat': signature, 'digest': hashlib.sha256(data).hexdigest()}

    def cleanup(self, now=None):
        with self.lock:
            records = {}
            for key, saved in self.saved.items():
                source = saved.get('source')
                project = saved.get('id')
                # Only pass records observed in this completed scan.  This
                # protects a file that was changed, removed, or only partly
                # visited while scanning from retention deletion.
                if not source or not project:
                    continue
                if self.seen.get(key) == saved.get('stat'):
                    records[project] = source
                elif key not in self.seen and not self.error:
                    # A complete scan saw that the user removed the source.
                    # This is safe to expire; an incomplete scan never enters
                    # this branch because os.walk/per-file errors set error.
                    records[project] = {**source, 'missing': True}
            result = self.store.cleanup(records, projects_root=self.projects, now=now)
            deleted = set(result['deleted'])
            if deleted:
                for key in list(self.saved):
                    if self.saved[key].get('id') in deleted:
                        self.saved.pop(key)
                        self.seen.pop(key, None)
                self._write_index()
            if result['failed']:
                self.error = 'Nogle udløbne projekter kunne ikke ryddes op endnu.'
            return result

    def run(self, stop):
        last_cleanup = 0.0
        scans = 0
        while not stop.is_set():
            try:
                self.scan()
                scans += 1
                if scans >= 2 and (not last_cleanup or time.monotonic() - last_cleanup >= 3600):
                    self.cleanup()
                    last_cleanup = time.monotonic()
            except OSError:
                self.error = 'Projektoversigten kunne ikke opdateres. Originalerne er bevaret.'
                logging.exception('Lokalt projektindeks')
            stop.wait(5)
