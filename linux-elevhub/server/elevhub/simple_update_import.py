"""One fixed GitHub source -> verified public APT cache. Never installs code.

The publisher signs the complete snapshot descriptor with minisign. APT clients
still check the native GPG repository signature; Windows needs no GPG runtime.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
import time
from urllib.parse import quote
import uuid
import zipfile

from release_feed import open_github, strict_json
from simple_updates import UpdateError, canonical, fields, plain, verifier_path

REPOSITORY = 'mdkmsuv/makervaerkstedet'
DESCRIPTOR = 'makervaerkstedet-updates.json'
SNAPSHOT = 'makervaerkstedet-apt-repository.zip'
MAX_TOTAL = 4 * 1024**3
MAX_FILES = 10000
ROOT_FILES = {'Packages', 'Packages.gz', 'Release', 'InRelease', 'Release.gpg'}
PUBLIC_METADATA = ROOT_FILES | {'makervaerkstedet.gpg'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_file(source, destination, *, expected_sha256=None, expected_size=None):
    destination = plain(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.import-' + uuid.uuid4().hex)
    try:
        with source.open('rb') as src, temporary.open('xb') as dst:
            shutil.copyfileobj(src, dst, 65536)
            dst.flush(); os.fsync(dst.fileno())
        expected = expected_sha256 if expected_sha256 is not None else sha(source)
        if sha(temporary) != expected or (expected_size is not None and temporary.stat().st_size != expected_size):
            raise UpdateError('published_copy_hash_mismatch')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def member_name(name):
    if not isinstance(name, str) or not name or str(PurePosixPath(name)) != name or name.startswith('/') or any(c in name for c in '\\:*?"<>|') or any(ord(c) < 32 for c in name):
        raise UpdateError('unsafe_snapshot_path')
    for part in name.split('/'):
        if part in ('.', '..') or part.endswith((' ', '.')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part):
            raise UpdateError('unsafe_snapshot_path')
    allowed = (name in PUBLIC_METADATA or name == 'snapshot-inventory.json'
               or re.fullmatch(r'pool/[A-Za-z0-9][A-Za-z0-9._+~-]*\.deb', name)
               or re.fullmatch(r'manifests/[a-f0-9]{64}\.(?:json|minisig)', name)
               or re.fullmatch(r'by-hash/SHA256/[a-f0-9]{64}', name))
    if not allowed or 'cadoodle' in name.casefold():
        raise UpdateError('unsupported_snapshot_member')
    return name


class UpdateImporter:
    def __init__(self, service, *, opener=open_github, feed_verifier=None, publication_hook=None):
        self.service, self.opener, self.feed_verifier = service, opener, feed_verifier
        self.publication_hook = publication_hook or (lambda name: None)
        self.stop_event, self.wake, self.run_lock = threading.Event(), threading.Event(), threading.Lock()
        self.thread = None
        self.backup = plain(service.path.parent / 'simple-update-publication')
        config = service.config.get('source', {'enabled': False, 'refresh_seconds': 86400})
        fields(config, ('enabled', 'refresh_seconds'))
        if type(config['enabled']) is not bool or type(config['refresh_seconds']) is not int or not 60 <= config['refresh_seconds'] <= 604800:
            raise UpdateError('invalid_update_source_configuration')
        self.enabled, self.interval = config['enabled'], config['refresh_seconds']
        if service.config['repository'] != REPOSITORY:
            raise UpdateError('update_source_repository_mismatch')
        with service.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS import_state(id INTEGER PRIMARY KEY CHECK(id=1),
                    seen_sequence INTEGER NOT NULL, seen_hash TEXT, active_sequence INTEGER NOT NULL,
                    state TEXT NOT NULL, last_attempt TEXT, last_success TEXT, code TEXT);
                INSERT OR IGNORE INTO import_state VALUES(1,0,NULL,0,'idle',NULL,NULL,NULL);
                CREATE TABLE IF NOT EXISTS import_commands(id TEXT PRIMARY KEY, receipt TEXT NOT NULL);
            ''')
        self._recover_publication()

    def status(self):
        with self.service.transaction() as db:
            row = dict(db.execute('SELECT * FROM import_state').fetchone())
        return {'schema': 1, 'enabled': self.enabled, **{k: v for k, v in row.items() if k not in ('id', 'seen_hash')}}

    def request_refresh(self, body):
        fields(body, ('command_id',))
        self.service.command_id(body['command_id'])
        if not self.enabled:
            raise UpdateError('update_source_disabled', 503)
        with self.service.transaction() as db:
            previous = db.execute('SELECT receipt FROM import_commands WHERE id=?', (body['command_id'],)).fetchone()
            if previous:
                return json.loads(previous[0])
            response = {'schema': 1, 'command_id': body['command_id'], 'accepted': True}
            db.execute('INSERT INTO import_commands VALUES(?,?)', (body['command_id'], canonical(response)))
        self.wake.set()
        return response

    def start(self):
        if self.enabled and self.thread is None:
            self.wake.set()
            self.thread = threading.Thread(target=self._loop, name='simple-update-import', daemon=False)
            self.thread.start()
        return self

    def stop(self):
        self.stop_event.set(); self.wake.set()
        if self.thread is not None:
            self.thread.join()

    def _loop(self):
        while not self.stop_event.is_set():
            self.wake.wait(self.interval); self.wake.clear()
            if not self.stop_event.is_set():
                self.refresh()

    def _download(self, url, maximum, *, destination=None, size=None, digest=None):
        # The opener supplies only fixed GitHub HTTP headers, never school/device
        # credentials. No URL is accepted from browser requests or ZIP contents.
        count, checksum, chunks, started = 0, hashlib.sha256(), [], time.monotonic()
        stream = destination.open('xb') if destination is not None else None
        try:
            with self.opener(url, REPOSITORY) as response:
                while True:
                    if self.stop_event.is_set() or time.monotonic() - started > 300:
                        raise UpdateError('update_download_interrupted')
                    block = response.read(65536)
                    if not block:
                        break
                    count += len(block)
                    if count > maximum:
                        raise UpdateError('update_download_too_large')
                    checksum.update(block)
                    if stream is not None:
                        stream.write(block)
                    else:
                        chunks.append(block)
            if size is not None and count != size:
                raise UpdateError('snapshot_size_mismatch')
            if digest is not None and checksum.hexdigest() != digest:
                raise UpdateError('snapshot_hash_mismatch')
            if stream is not None:
                stream.flush(); os.fsync(stream.fileno())
                return destination
            return b''.join(chunks)
        finally:
            if stream is not None:
                stream.close()

    def _verify_feed(self, raw, signature, highest, previous):
        if self.feed_verifier is None:
            spec = importlib.util.spec_from_file_location('_elevhub_simple_feed', verifier_path().with_name('simple_feed.py'))
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            self.feed_verifier = module.verify_descriptor
        config = self.service.config
        return self.feed_verifier(raw, signature, minisign_exe=plain(self.service.base / config['minisign_exe']),
            minisign_sha256=config['minisign_sha256'], public_key=config['public_key'], now=self.service.clock(),
            highest_sequence=highest, highest_sha256=previous)

    def _unpack(self, archive_path, destination):
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_FILES:
                raise UpdateError('snapshot_file_limit')
            files, folded, total = {}, set(), 0
            for entry in entries:
                name = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
                mode = entry.external_attr >> 16
                if entry.flag_bits & 1 or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise UpdateError('unsafe_snapshot_entry')
                # Directory records have no authority and are not required.
                if entry.is_dir():
                    if name not in ('pool', 'manifests', 'by-hash', 'by-hash/SHA256'):
                        raise UpdateError('unsafe_snapshot_path')
                    continue
                member_name(name)
                if (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or name.casefold() in folded:
                    raise UpdateError('unsafe_snapshot_entry')
                folded.add(name.casefold()); files[name] = entry; total += entry.file_size
                if total > MAX_TOTAL:
                    raise UpdateError('snapshot_unpacked_limit')
            if 'snapshot-inventory.json' not in files or files['snapshot-inventory.json'].file_size > 2 * 1024**2:
                raise UpdateError('snapshot_inventory_missing')
            inventory = strict_json(archive.read(files['snapshot-inventory.json']))
            fields(inventory, ('schema', 'files'))
            if type(inventory['schema']) is not int or inventory['schema'] != 1 or not isinstance(inventory['files'], dict) or set(files) != set(inventory['files']) | {'snapshot-inventory.json'}:
                raise UpdateError('snapshot_inventory_mismatch')
            if not ROOT_FILES <= set(inventory['files']):
                raise UpdateError('incomplete_apt_snapshot')
            for name, expected in inventory['files'].items():
                fields(expected, ('sha256', 'size'))
                if not isinstance(expected['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', expected['sha256']) or type(expected['size']) is not int or expected['size'] != files[name].file_size or expected['size'] < 1:
                    raise UpdateError('invalid_snapshot_inventory')
                if name.startswith('by-hash/') and expected['sha256'] != PurePosixPath(name).name:
                    raise UpdateError('by_hash_name_mismatch')
                target = destination / name; target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(files[name]) as src, target.open('xb') as dst:
                    checksum, count = hashlib.sha256(), 0
                    while block := src.read(65536):
                        count += len(block)
                        if count > expected['size']:
                            raise UpdateError('snapshot_member_size_mismatch')
                        checksum.update(block); dst.write(block)
                if count != expected['size'] or checksum.hexdigest() != expected['sha256']:
                    raise UpdateError('snapshot_member_hash_mismatch')
            return inventory['files']

    def _components(self, stage, files):
        verify = self.service.release_verifier()
        descriptors = []
        manifests = sorted(name for name in files if re.fullmatch(r'manifests/[a-f0-9]{64}\.json', name))
        if not manifests or len(manifests) > 128:
            raise UpdateError('component_manifests_missing')
        signatures = {name for name in files if name.startswith('manifests/') and name.endswith('.minisig')}
        if signatures != {name[:-5] + '.minisig' for name in manifests}:
            raise UpdateError('component_signature_set_mismatch')
        config = self.service.config
        for name in manifests:
            if self.stop_event.is_set():
                raise UpdateError('update_download_interrupted')
            raw = (stage / name).read_bytes()
            if len(raw) > 1024**2 or files[name[:-5] + '.minisig']['size'] > 16384:
                raise UpdateError('release_metadata_too_large')
            value = strict_json(raw)
            verified = verify(raw, (stage / (name[:-5] + '.minisig')).read_bytes(),
                minisign_exe=plain(self.service.base / config['minisign_exe']), minisign_sha256=config['minisign_sha256'],
                public_key=config['public_key'], repository=REPOSITORY, role=value.get('role'),
                platform=value.get('platform'), component=value.get('component'), now=self.service.clock())
            if verified['_manifest_sha256'] != PurePosixPath(name).stem:
                raise UpdateError('release_cache_hash_binding_mismatch')
            if verified['role'] != 'student' or verified['platform'] != 'debian-amd64':
                raise UpdateError('non_apt_component_in_snapshot')
            for artifact in verified['artifacts']:
                if verified['platform'] == 'debian-amd64':
                    if files.get('pool/' + artifact['name']) != {'sha256': artifact['sha256'], 'size': artifact['size']}:
                        raise UpdateError('component_artifact_not_in_snapshot')
            descriptors.append({'manifest': 'public-apt-cache/' + name, 'signature': 'public-apt-cache/' + name[:-5] + '.minisig',
                                **{key: verified[key] for key in ('role', 'platform', 'component')}})
        return descriptors

    def _journal(self, value):
        self.backup.mkdir(exist_ok=True)
        temporary = self.backup / ('journal-' + uuid.uuid4().hex)
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.backup / 'journal.json')
        finally:
            temporary.unlink(missing_ok=True)

    def _clear_backup(self):
        # Fixed flat APT metadata backup only; never a recursive data deletion.
        allowed = PUBLIC_METADATA | {'catalog-before.json', 'journal.json'}
        for path in self.backup.iterdir():
            if path.name not in allowed or not plain(path).is_file():
                raise UpdateError('unsafe_publication_backup')
        for path in self.backup.iterdir():
            path.unlink()
        self.backup.rmdir()

    def _recover_publication(self):
        if not self.backup.exists():
            return
        marker = self.backup / 'journal.json'
        if not marker.exists():
            # No journal means no public mutation had permission to start.
            self._clear_backup(); return
        value = strict_json(plain(marker).read_bytes())
        fields(value, ('committed', 'metadata', 'catalog'))
        if type(value['committed']) is not bool or type(value['catalog']) is not bool or not isinstance(value['metadata'], dict) or not set(value['metadata']) <= PUBLIC_METADATA or any(type(v) is not bool for v in value['metadata'].values()):
            raise UpdateError('invalid_publication_journal')
        if not value['committed']:
            # Restore non-InRelease metadata first; old InRelease is the last
            # public commit on recovery too. Immutable objects are never removed.
            for name in sorted(set(value['metadata']) - {'InRelease'}) + (['InRelease'] if 'InRelease' in value['metadata'] else []):
                target = plain(self.service.cache / name)
                if value['metadata'][name]:
                    atomic_file(plain(self.backup / name), target)
                else:
                    target.unlink(missing_ok=True)
            if value['catalog']:
                atomic_file(plain(self.backup / 'catalog-before.json'), self.service.catalog_path)
            else:
                plain(self.service.catalog_path).unlink(missing_ok=True)
        self._clear_backup()

    def _publish(self, stage, files, descriptors, feed):
        cache = self.service.cache
        self._recover_publication()
        immutable = [name for name in files if name.startswith(('pool/', 'manifests/', 'by-hash/'))]
        for name in immutable:
            target = plain(cache / name)
            if target.exists() and (target.stat().st_size != files[name]['size'] or sha(target) != files[name]['sha256']):
                raise UpdateError('immutable_cache_collision')
        mutable = set(files) - set(immutable)
        self.backup.mkdir()
        before = {}
        for name in mutable:
            target = plain(cache / name); before[name] = target.exists()
            if before[name]:
                atomic_file(target, self.backup / name)
        had_catalog = self.service.catalog_path.exists()
        if had_catalog:
            atomic_file(plain(self.service.catalog_path), self.backup / 'catalog-before.json')
        journal = {'committed': False, 'metadata': before, 'catalog': had_catalog}
        self._journal(journal)
        try:
            for name in sorted(immutable) + sorted(mutable - {'InRelease'}) + ['InRelease']:
                if self.stop_event.is_set():
                    raise UpdateError('update_publication_interrupted')
                atomic_file(stage / name, cache / name, expected_sha256=files[name]['sha256'], expected_size=files[name]['size'])
                self.publication_hook(name)
            document = {'schema': 1, 'sequence': feed['sequence'], 'descriptor_sha256': feed['_descriptor_sha256'], 'catalog': descriptors}
            private = stage / 'catalog-current.json'; private.write_text(canonical(document), encoding='utf-8')
            atomic_file(private, self.service.catalog_path)
            self._journal(dict(journal, committed=True))
        except BaseException:
            self._recover_publication()
            raise
        self._clear_backup()

    def refresh(self):
        if not self.enabled or not self.run_lock.acquire(blocking=False):
            return False
        try:
            with self.service.transaction() as db:
                previous = db.execute('SELECT * FROM import_state').fetchone()
                high, old_hash = previous['seen_sequence'], previous['seen_hash']
                db.execute("UPDATE import_state SET state='refreshing',last_attempt=?,code=NULL", (self.service.clock().isoformat(),))
            metadata = strict_json(self._download('https://api.github.com/repos/' + REPOSITORY + '/releases/latest', 2 * 1024**2))
            tag = metadata.get('tag_name')
            if not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+~\-]{0,127}', tag) or '..' in tag:
                raise UpdateError('invalid_release_tag')
            prefix = 'https://github.com/' + REPOSITORY + '/releases/download/' + quote(tag, safe='') + '/'
            feed_raw = self._download(prefix + DESCRIPTOR, 65536)
            feed_signature = self._download(prefix + DESCRIPTOR + '.minisig', 16384)
            feed = self._verify_feed(feed_raw, feed_signature, high, old_hash)
            if feed['release_tag'] != tag or feed['repository'] != REPOSITORY or feed['_snapshot_url'] != prefix + SNAPSHOT:
                raise UpdateError('feed_release_binding_mismatch')
            # Seeing authenticated newer metadata advances replay protection even
            # if its payload download later fails. Existing catalog stays intact.
            with self.service.transaction() as db:
                db.execute('UPDATE import_state SET seen_sequence=?,seen_hash=?', (feed['sequence'], feed['_descriptor_sha256']))
            with tempfile.TemporaryDirectory(prefix='simple-import-', dir=self.service.path.parent) as temporary:
                root = Path(temporary); archive = root / 'snapshot.zip'; stage = root / 'public'; stage.mkdir()
                item = feed['snapshot']
                self._download(feed['_snapshot_url'], item['size'], destination=archive, size=item['size'], digest=item['sha256'])
                files = self._unpack(archive, stage)
                descriptors = self._components(stage, files)
                # Revalidate the authenticated feed identity at publication
                # after the potentially long payload transfer.
                feed = self._verify_feed(feed_raw, feed_signature, feed['sequence'], feed['_descriptor_sha256'])
                self._publish(stage, files, descriptors, feed)
            with self.service.transaction() as db:
                db.execute("UPDATE import_state SET state='ready',active_sequence=?,last_success=?,code=NULL", (feed['sequence'], self.service.clock().isoformat()))
            return True
        except Exception as error:
            with self.service.transaction() as db:
                db.execute("UPDATE import_state SET state='failed',code=?", (getattr(error, 'code', 'update_import_failed'),))
            return False
        finally:
            self.run_lock.release()
