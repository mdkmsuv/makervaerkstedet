"""Local administrator requests, scoped to one root-authenticated pupil device.

Package discovery is read-only. Only an explicit, hash-bound request authorizes
download and enqueue; the controller continues it after the local UI closes.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
from update_admin import safe_path, version_key

HEX = re.compile(r'[a-f0-9]{64}')
NONCE = re.compile(r'[a-f0-9]{32}')


class DeviceUpdates:
    def __init__(self, admin, feed, clock=time.time):
        self.admin, self.feed, self.clock = admin, feed, clock
        self.path = safe_path(admin.root / 'device-requests.sqlite3')
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS intents (
              computer TEXT PRIMARY KEY, request_id TEXT NOT NULL,
              version TEXT NOT NULL, package_sha256 TEXT NOT NULL,
              status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        safe_path(self.path)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def target(self, computer):
        releases, targets = self.admin.configuration()
        matches = [t for t in targets.values()
                   if t['role'] == 'linux_student' and t['worker_target'] == computer]
        if len(matches) != 1:
            raise ValueError('Computeren er ikke klargjort til lokale opdateringer.')
        return matches[0], releases

    def offer(self, computer):
        target, releases = self.target(computer)
        choices = []
        for rid in target['release_ids']:
            release = releases.get(rid, {})
            version, sha = release.get('version'), release.get('package_sha256')
            if (release.get('role') == 'linux_student' and version_key(version)
                    and rid == 'github-linux_student-' + version
                    and isinstance(sha, str) and HEX.fullmatch(sha)
                    and self.admin.release_ready(release)):
                choices.append({'version': version, 'package_sha256': sha,
                                'release_id': rid, 'notes': ''})
        feed = self.feed.snapshot()
        latest = feed.get('latest') if feed.get('enabled') else None
        if latest and version_key(latest.get('version')):
            for p in latest.get('packages', []):
                if p.get('role') == 'linux_student' and HEX.fullmatch(p.get('sha256', '')):
                    choices.append({'version': latest['version'], 'package_sha256': p['sha256'],
                                    'release_id': 'github-linux_student-' + latest['version'],
                                    'notes': str(latest.get('notes', ''))[:4096]})
        if not choices:
            return None
        latest_version = max(version_key(c['version']) for c in choices)
        newest = [c for c in choices if version_key(c['version']) == latest_version]
        if len({c['package_sha256'] for c in newest}) != 1:
            raise ValueError('Udgivelsens kontrolsum er ændret. Kontakt administratoren.')
        return newest[-1]

    def job(self, target):
        snapshot = self.admin.snapshot()
        row = next((t for t in snapshot['targets'] if t['id'] == target['id']), None)
        job = row.get('job') if row else None
        return {key: job.get(key) for key in ('status', 'code', 'version', 'release_id', 'paused')} if job else None

    def snapshot(self, computer):
        target, _ = self.target(computer)
        with self.connect() as db:
            row = db.execute('SELECT status,version,package_sha256,error FROM intents WHERE computer=?',
                             (computer,)).fetchone()
        job = self.job(target)
        if (row and job and row['status'] in ('requested', 'waiting_for_package', 'queued')
                and job['release_id'] != 'github-linux_student-' + row['version']):
            job = None
        return {'schema': 1, 'computer': computer, 'available': self.offer(computer),
                'job': job, 'request': dict(row) if row else None,
                'feed_state': self.feed.snapshot()['state']}

    @staticmethod
    def command_id(computer, request, action):
        return hashlib.sha256((computer + '\n' + request + '\n' + action).encode()).hexdigest()[:32]

    def execute(self, computer, body):
        self.target(computer)
        if not isinstance(body, dict) or not isinstance(body.get('request_id'), str) or not NONCE.fullmatch(body['request_id']):
            raise ValueError('Ugyldig opdateringsanmodning.')
        action = body.get('action')
        fields = {'action', 'request_id'} | ({'version', 'package_sha256'} if action in ('update', 'retry') else set())
        if action not in ('check', 'update', 'retry') or set(body) != fields:
            raise ValueError('Ugyldig opdateringshandling.')
        if action == 'check':
            self.feed.execute('local-admin:' + computer, {
                'request_id': self.command_id(computer, body['request_id'], 'check'),
                'action': 'check', 'params': {}})
            return self.snapshot(computer)
        if not version_key(body['version']) or not isinstance(body['package_sha256'], str) or not HEX.fullmatch(body['package_sha256']):
            raise ValueError('Ugyldig version eller kontrolsum.')
        payload = json.dumps({'computer': computer, **body}, sort_keys=True)
        identifier = self.command_id(computer, body['request_id'], 'intent')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT payload FROM commands WHERE id=?', (identifier,)).fetchone()
            if old:
                if old['payload'] != payload:
                    raise ValueError('Anmodnings-ID er allerede brugt.')
            else:
                current = db.execute('SELECT * FROM intents WHERE computer=?', (computer,)).fetchone()
                same = current and all(current[k] == body[k] for k in ('version', 'package_sha256'))
                if action == 'retry':
                    if not same:
                        raise ValueError('Ingen tilsvarende opdatering at prøve igen.')
                else:
                    offer = self.offer(computer)
                    if not offer or any(body[k] != offer[k] for k in ('version', 'package_sha256')):
                        raise ValueError('Den valgte opdatering er ikke længere tilgængelig.')
                active = current and current['status'] in ('requested', 'waiting_for_package', 'queued')
                target, _ = self.target(computer)
                current_job = self.job(target)
                if current_job and current_job['status'] == 'completed':
                    if current_job['release_id'] == 'github-linux_student-' + body['version']:
                        raise ValueError('Denne opdatering er allerede installeret.')
                    if current and current_job['release_id'] == 'github-linux_student-' + current['version']:
                        active = False
                if active:
                    if any(current[k] != body[k] for k in ('version', 'package_sha256')):
                        raise ValueError('En anden opdatering er allerede bestilt.')
                if not active or action == 'retry':
                    db.execute('INSERT OR REPLACE INTO intents VALUES(?,?,?,?,?,?,?)',
                               (computer, body['request_id'], body['version'], body['package_sha256'],
                                'requested', '', self.clock()))
                db.execute('INSERT INTO commands VALUES(?,?)', (identifier, payload))
        return self.snapshot(computer)

    def process_pending(self):
        """Called under the existing controller process lock; never contacts pupils."""
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM intents WHERE status IN ('requested','waiting_for_package')")]
        for row in rows:
            computer = row['computer']
            try:
                target, releases = self.target(computer)
                rid = 'github-linux_student-' + row['version']
                release = releases.get(rid)
                if release:
                    if (release.get('role') != 'linux_student' or rid not in target['release_ids']
                            or release.get('package_sha256') != row['package_sha256']):
                        raise ValueError('changed_release')
                    if not self.admin.release_ready(release):
                        continue
                    job = self.job(target)
                    if job and job['release_id'] == rid and (job['status'] == 'blocked' or job.get('paused')):
                        command = {'action': 'retry', 'params': {'target_id': target['id'], 'version': job['version']}}
                    else:
                        command = {'action': 'enqueue', 'params': {'release_id': rid, 'target_ids': [target['id']]}}
                    command['request_id'] = self.command_id(computer, row['request_id'], 'queue')
                    self.admin.execute('local-admin:' + computer, command)
                    status = 'queued'
                else:
                    feed = self.feed.snapshot()
                    latest = feed.get('latest')
                    if not latest or latest['version'] != row['version']:
                        raise ValueError('release_not_available')
                    package = next((p for p in latest['packages'] if p['role'] == 'linux_student'
                                    and p['sha256'] == row['package_sha256']), None)
                    if not package:
                        raise ValueError('release_not_available')
                    failed = any(d.get('release_id') == latest['release_id']
                                 and d.get('asset') == package['asset']
                                 and d.get('status') in ('failed', 'blocked')
                                 for d in feed.get('downloads', []))
                    if failed and row['status'] == 'waiting_for_package':
                        raise ValueError('download_failed')
                    self.feed.execute('local-admin:' + computer, {
                        'request_id': self.command_id(computer, row['request_id'], 'download'),
                        'action': 'download', 'params': {'release_id': latest['release_id'], 'asset': package['asset']}})
                    status = 'waiting_for_package'
                error = ''
            except (ValueError, OSError, KeyError, TypeError):
                status, error = 'blocked', 'Opdateringen kunne ikke klargøres. Prøv igen eller kontakt administratoren.'
            with self.connect() as db:
                db.execute('UPDATE intents SET status=?,error=?,updated=? WHERE computer=? AND request_id=?',
                           (status, error, self.clock(), computer, row['request_id']))
