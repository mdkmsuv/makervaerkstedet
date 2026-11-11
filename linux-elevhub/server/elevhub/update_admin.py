"""Teacher update queues. Only protected, reviewed server-side releases are runnable.

Workers run on the main Windows server. This module never constructs SSH commands
from browser input. SQLite transactions coordinate UI requests and external workers.
Kreditering: Klostermarksskolen.
"""
from __future__ import annotations
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

ROLES = ('linux_student', 'windows_laser_teacher', 'windows_server')
ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}')
HEX = re.compile(r'[a-f0-9]{64}')
STATUS = {'pending':'pending', 'waiting_for_computer':'offline', 'offline':'offline',
          'checking':'checking', 'transferring':'transferring', 'applying':'applying',
          'staged_awaiting_restart':'staged_awaiting_restart', 'complete':'completed',
          'completed':'completed', 'waiting_for_idle':'waiting_for_idle', 'station_update_failed':'blocked', 'profile_missing':'blocked', 'failed_rolled_back':'blocked',
          'recovery_required':'blocked', 'blocked':'blocked', 'retry':'pending'}
ACTIVE = ('checking', 'transferring', 'applying')

class Conflict(ValueError):
    pass


def safe_path(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        try: info = item.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Opdateringsdata må ikke ligge gennem links.')
    return path


def load_json(path):
    path = safe_path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Opdateringskonfigurationen er for stor.')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def version_key(value):
    """Order release dates numerically, including the optional build number."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?', value):
        return None
    try: datetime.datetime.strptime(value[:10], '%Y.%m.%d')
    except ValueError: return None
    parts = tuple(map(int, value.split('.')))
    return parts if len(parts) == 4 else (*parts, 0)


def target_binding(target):
    # Adding another approved release must not erase installation evidence.
    fields = ('id', 'role', 'worker_target', 'identity_file', 'identity_sha256',
              'identity_name', 'ssh_key', 'known_hosts', 'host_key_alias',
              'host_key_policy', 'transport', 'controller_config', 'controller_sha256')
    return hashlib.sha256(json.dumps({key: target.get(key) for key in fields},
                                    sort_keys=True).encode()).hexdigest()


class UpdateAdministration:
    def __init__(self, data_root, clock=time.time):
        self.root = safe_path(Path(data_root) / 'updates')
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt': self.root.chmod(0o700)
        self.path = safe_path(self.root / 'queue.sqlite3')
        self.clock = clock
        self.claims = {}
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, target_id TEXT NOT NULL UNIQUE, release_id TEXT NOT NULL,
              status TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
              version INTEGER NOT NULL DEFAULT 1, attempt INTEGER NOT NULL DEFAULT 0,
              lease TEXT, lease_until REAL NOT NULL DEFAULT 0, updated REAL NOT NULL,
              code TEXT NOT NULL DEFAULT '', role_verified INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (sequence INTEGER PRIMARY KEY, at REAL, actor TEXT, action TEXT, target TEXT, release TEXT);
            CREATE TABLE IF NOT EXISTS workers (release_id TEXT PRIMARY KEY, checked REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS credentials (target_id TEXT PRIMARY KEY, config_hash TEXT NOT NULL, checked REAL NOT NULL, ready INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS releases (release_id TEXT PRIMARY KEY, catalog_hash TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, checked REAL NOT NULL, ready INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS installations (
              target_id TEXT PRIMARY KEY, target_binding TEXT NOT NULL,
              release_id TEXT NOT NULL, installed_version TEXT NOT NULL,
              manifest_sha256 TEXT NOT NULL, verified_at REAL NOT NULL);
            ''')
        if os.name != 'nt': self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        safe_path(self.path)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def configuration(self):
        if not (self.root / 'catalog.json').exists() and not (self.root / 'targets.json').exists():
            return {}, {}
        catalog = load_json(self.root / 'catalog.json')
        target_data = load_json(self.root / 'targets.json')
        if catalog.get('schema') != 1 or target_data.get('schema') != 1:
            raise ValueError('Ukendt opdateringskonfiguration.')
        releases, targets = {}, {}
        for row in catalog['releases']:
            if not ID.fullmatch(row.get('id', '')) or row['id'] in releases or row.get('role') not in ROLES:
                raise ValueError('Ugyldig udgivelse.')
            if not isinstance(row.get('version'), str) or len(row['version']) > 120:
                raise ValueError('Ugyldig version.')
            releases[row['id']] = row
        for row in target_data['targets']:
            if not ID.fullmatch(row.get('id', '')) or row['id'] in targets or row.get('role') not in ROLES:
                raise ValueError('Ugyldigt opdateringsmål.')
            if not ID.fullmatch(row.get('worker_target', '')):
                raise ValueError('Ugyldigt målnavn.')
            if not isinstance(row.get('name'), str) or len(row['name']) > 160:
                raise ValueError('Ugyldigt visningsnavn.')
            if any(r not in releases or releases[r]['role'] != row['role'] for r in row['release_ids']):
                raise ValueError('Udgivelse og computerrolle stemmer ikke.')
            targets[row['id']] = row
        return releases, targets

    def direct_release_ready(self, release):
        try:
            if release.get('reviewed') is not True or not HEX.fullmatch(release.get('manifest_sha256', '')):
                return False
            root = safe_path(release['worker_root'])
            config = safe_path(root / 'config.json')
            actual = hashlib.sha256(config.read_bytes()).hexdigest()
            return actual == release['manifest_sha256']
        except (OSError, ValueError, KeyError): return False

    def release_ready(self, release):
        if self.direct_release_ready(release): return True
        signature = hashlib.sha256(json.dumps(release,sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            row = db.execute('SELECT * FROM releases WHERE release_id=?',(release['id'],)).fetchone()
        return bool(row and row['ready'] and row['catalog_hash']==signature
                    and row['manifest_sha256']==release.get('manifest_sha256')
                    and self.clock()-row['checked']<86400)

    def direct_credentials(self, target):
        try:
            if target['role'] == 'windows_server':
                if target.get('transport') != 'local_server' or not HEX.fullmatch(target.get('controller_sha256', '')):
                    return False
                config = safe_path(target['controller_config'])
                return config.is_file() and hashlib.sha256(config.read_bytes()).hexdigest() == target['controller_sha256']
            key, known = safe_path(target['ssh_key']), safe_path(target['known_hosts'])
            if not key.is_file() or key.stat().st_size == 0:
                return False
            if (target['role'] == 'linux_student' and target.get('host_key_policy') == 'enrollment_proof'
                    and self.enrolled(target)):
                # Reviewed Linux transport verifies enrolled root HMAC before
                # pinning a first key or sending any payload.
                return True
            if not known.is_file(): return False
            lines = [line.split() for line in known.read_text(encoding='utf-8-sig').splitlines()
                     if line.strip() and not line.lstrip().startswith('#')]
            alias = target.get('host_key_alias', target['worker_target'])
            # No wildcard/CA/trust-on-first-use policy in a reviewed target pin.
            return any(len(line) >= 3 and alias in line[0].split(',') and line[1] == 'ssh-ed25519'
                       and '*' not in line[0] and '?' not in line[0] for line in lines)
        except (OSError, ValueError, KeyError, UnicodeError): return False

    def credentials(self, target):
        if self.direct_credentials(target): return True
        # SYSTEM worker attests readiness without granting the HTTP service
        # access to private key bytes or even its protected file attributes.
        signature = hashlib.sha256(json.dumps(target,sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            row = db.execute('SELECT * FROM credentials WHERE target_id=?',(target['id'],)).fetchone()
        return bool(row and row['ready'] and row['config_hash']==signature and self.clock()-row['checked']<86400)

    def heartbeat(self, release_id):
        releases, targets = self.configuration()
        if release_id not in releases: return
        now = self.clock()
        ready = []
        for target in targets.values():
            if release_id in target['release_ids']:
                signature = hashlib.sha256(json.dumps(target,sort_keys=True).encode()).hexdigest()
                ready.append((target['id'],signature,now,int(self.direct_credentials(target))))
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO workers VALUES(?,?)',(release_id,now))
            release = releases[release_id]
            signature = hashlib.sha256(json.dumps(release,sort_keys=True).encode()).hexdigest()
            db.execute('INSERT OR REPLACE INTO releases VALUES(?,?,?,?,?)',
                (release_id,signature,release.get('manifest_sha256',''),now,int(self.direct_release_ready(release))))
            db.executemany('INSERT OR REPLACE INTO credentials VALUES(?,?,?,?)',ready)

    def enrolled(self, target):
        if target['role'] in ('windows_laser_teacher', 'windows_server'):
            # Pinned host is configured, but physical station role is proved by
            # the remote updater before applying; never fabricate enrollment.
            return self.credentials(target)
        try:
            expected = target['identity_sha256']
            if not HEX.fullmatch(expected): return False
            row = load_json(target['identity_file'])['enheder'][target.get('identity_name', target['worker_target'])]
            actual = row.get('token_sha256', row.get('token_digest', ''))
            return isinstance(actual, str) and secrets.compare_digest(expected, actual)
        except (OSError, ValueError, KeyError, TypeError): return False

    def snapshot(self):
        releases, targets = self.configuration()
        with self.connect() as db:
            jobs = {r['target_id']: dict(r) for r in db.execute('SELECT * FROM jobs')}
            workers = {r['release_id']: r['checked'] for r in db.execute('SELECT * FROM workers')}
            audit = [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY sequence DESC LIMIT 40')]
            installations = {r['target_id']: dict(r) for r in db.execute('SELECT * FROM installations')}
        result = []
        for identifier, target in targets.items():
            job = jobs.get(identifier)
            release = releases.get(job['release_id']) if job else None
            configured = self.credentials(target)
            enrolled = self.enrolled(target)
            installed = installations.get(identifier)
            if installed and installed['target_binding'] != target_binding(target):
                installed = None
            result.append({'id':identifier, 'name':target['name'], 'role':target['role'],
                'release_ids':target['release_ids'], 'credentials_ready':configured, 'enrolled':enrolled,
                'credentials_mode':('local_server' if target['role']=='windows_server' else 'enrollment_proof' if target['role']=='linux_student' and target.get('host_key_policy')=='enrollment_proof' else 'pinned') if configured else 'missing',
                'role_verified':bool(job and job['role_verified']) if target['role'] in ('windows_laser_teacher','windows_server') else enrolled,
                'job': ({k:job[k] for k in ('id','release_id','status','paused','version','attempt','updated','code')} if job else None),
                'desired_version': release['version'] if release else None,
                'last_verified_version': installed['installed_version'] if installed else None,
                'last_verified_at': installed['verified_at'] if installed else None,
                'last_verified_release': installed['release_id'] if installed else None})
        return {'schema':1, 'targets':result, 'audit':audit,
                'releases':[{'id':r['id'], 'role':r['role'], 'version':r['version'],
                    'ready':self.release_ready(r), 'worker_last_seen':workers.get(r['id']),
                    'worker_online':self.clock()-workers.get(r['id'],0) < 180} for r in releases.values()],
                'configured':bool(releases), 'ssh_origin':'main_server'}

    def execute(self, actor, body):
        if not isinstance(body, dict) or set(body) != {'request_id','action','params'}:
            raise ValueError('Ugyldig opdateringshandling.')
        request, action, params = body['request_id'], body['action'], body['params']
        if not isinstance(request, str) or not re.fullmatch('[a-f0-9]{32}', request):
            raise ValueError('Ugyldigt request-ID.')
        expected = {'enqueue':{'release_id','target_ids'}, 'pause':{'target_id','version'},
                    'resume':{'target_id','version'}, 'retry':{'target_id','version'}}
        if action not in expected or not isinstance(params, dict) or set(params) != expected[action]:
            raise ValueError('Handlingen eller felterne er ikke understøttet.')
        payload = json.dumps({'actor':actor,'action':action,'params':params}, sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM requests WHERE id=?',(request,)).fetchone()
            if previous:
                if previous['payload'] != payload: raise Conflict('Request-ID er allerede brugt til en anden handling.')
                return json.loads(previous['result'])
            releases, targets = self.configuration()
            now = self.clock()
            affected = []
            if action == 'enqueue':
                release = releases.get(params['release_id'])
                ids = params['target_ids']
                if not release or not self.release_ready(release): raise ValueError('Udgivelsen er ikke kontrolleret og klar på hovedserveren.')
                if not isinstance(ids, list) or not ids or len(ids) > 256 or any(not isinstance(i,str) for i in ids) or len(set(ids)) != len(ids):
                    raise ValueError('Vælg mindst én computer.')
                for identifier in ids:
                    target = targets.get(identifier)
                    if not target or release['id'] not in target['release_ids'] or release['role'] != target['role']:
                        raise ValueError('Computeren er ikke godkendt til denne udgivelse.')
                    if not self.enrolled(target) or not self.credentials(target):
                        raise ValueError('Serverens SSH-opsætning eller computeridentitet mangler.')
                    old = db.execute('SELECT * FROM jobs WHERE target_id=?',(identifier,)).fetchone()
                    if old and old['release_id'] == release['id']:
                        continue
                    if old and old['status'] != 'completed':
                        raise Conflict('Computeren har allerede en uafsluttet opdatering.')
                    installed = db.execute('SELECT * FROM installations WHERE target_id=?', (identifier,)).fetchone()
                    if installed and installed['target_binding'] == target_binding(target):
                        previous_version, next_version = version_key(installed['installed_version']), version_key(release['version'])
                        if previous_version is not None and next_version is not None and next_version <= previous_version:
                            raise Conflict('Computeren har allerede denne eller en nyere bekræftet version. Gendannelse er en særskilt handling.')
                    job_id = secrets.token_hex(16)
                    db.execute('DELETE FROM jobs WHERE target_id=?',(identifier,))
                    db.execute('INSERT INTO jobs(id,target_id,release_id,status,updated) VALUES(?,?,?,?,?)',
                               (job_id,identifier,release['id'],'pending',now))
                    affected.append(identifier)
            else:
                identifier = params['target_id']
                row = db.execute('SELECT * FROM jobs WHERE target_id=?',(identifier,)).fetchone()
                if not row or type(params['version']) is not int or row['version'] != params['version']:
                    raise Conflict('Status er ændret. Hent den aktuelle status.')
                if row['status'] == 'completed': raise Conflict('Opdateringen er afsluttet.')
                if action == 'retry' and row['lease_until'] > now:
                    raise Conflict('Et forsøg er i gang. Vent på resultatet.')
                if action == 'retry' and row['status'] == 'staged_awaiting_restart':
                    raise Conflict('Opdateringen afventer genstart på elevcomputeren.')
                if action == 'retry':
                    db.execute("UPDATE jobs SET status='pending',paused=0,lease=NULL,lease_until=0,code='',version=version+1,updated=? WHERE target_id=?",(now,identifier))
                else:
                    db.execute('UPDATE jobs SET paused=?,version=version+1,updated=? WHERE target_id=?',(int(action=='pause'),now,identifier))
                affected.append(identifier)
            for identifier in affected:
                row = db.execute('SELECT release_id FROM jobs WHERE target_id=?',(identifier,)).fetchone()
                db.execute('INSERT INTO audit(at,actor,action,target,release) VALUES(?,?,?,?,?)',
                           (now,actor,action,identifier,row['release_id']))
            result = {'ok':True, 'affected':affected}
            db.execute('INSERT INTO requests VALUES(?,?,?)',(request,payload,json.dumps(result)))
        return result

    def worker_allowed(self, release_id, worker_target):
        releases, targets = self.configuration()
        release = releases.get(release_id)
        target = next((t for t in targets.values() if t['worker_target'] == worker_target and release_id in t['release_ids']),None)
        if not release or not target: return False
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR REPLACE INTO workers VALUES(?,?)',(release_id,now))
            row = db.execute('SELECT * FROM jobs WHERE target_id=? AND release_id=?',(target['id'],release_id)).fetchone()
            if not row or row['paused'] or row['status'] in ('completed','blocked'):
                return False
            owned = self.claims.get((release_id,worker_target)) == (row['id'],row['lease'])
            if row['lease_until'] > now and not owned:
                return False
            if not self.direct_release_ready(release) or not self.enrolled(target) or not self.direct_credentials(target):
                db.execute("UPDATE jobs SET status='blocked',code='configuration_changed',version=version+1,updated=? WHERE id=?",(now,row['id']))
                return False
            if owned and row['lease_until'] > now:
                return True
            # A long interruption expires even this process's claim. Reclaim
            # atomically with a fresh token before accepting any new reports.
            token = secrets.token_hex(24)
            db.execute('UPDATE jobs SET lease=?,lease_until=?,attempt=attempt+1,updated=? WHERE id=?',
                       (token,now+10800,now,row['id']))
            self.claims[(release_id,worker_target)] = (row['id'],token)
        return True

    def worker_job(self, release_id, worker_target):
        claim = self.claims.get((release_id,worker_target))
        return {'id':claim[0]} if claim else None

    def report(self, release_id, worker_target, status, *, role_verified=False, code=None, installed_version=None, package_sha256=None):
        if status not in STATUS: raise ValueError('Ukendt opdateringsstatus.')
        claim = self.claims.get((release_id,worker_target))
        if not claim: return
        job_id, token = claim
        if code not in (None,'station_installation_missing','runner_hash_mismatch','station_update_failed','recovery_required','transfer_failed','remote_result_unconfirmed','version_unknown','version_not_older','local_admin_required','windows_long_paths_disabled'):
            code = None
        current = STATUS[status]
        final = current not in ACTIVE
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=? AND lease=?',(job_id,token)).fetchone()
            if not row or row['lease_until'] <= now: return
            releases, targets = self.configuration()
            target = targets.get(row['target_id'])
            release = releases.get(release_id)
            if not target or not release: return
            if current in ('completed','staged_awaiting_restart') and not role_verified:
                if target['role'] in ('windows_laser_teacher', 'windows_server'):
                    current, status = 'blocked', 'station_role_unverified'
            if current == 'completed':
                if (not self.direct_release_ready(release) or not self.enrolled(target) or not self.direct_credentials(target)
                        or (installed_version is not None and installed_version != release['version'])
                        or (package_sha256 is not None and package_sha256 != release.get('package_sha256'))):
                    current, status = 'blocked', 'installed_version_unconfirmed'
                else:
                    db.execute('INSERT OR REPLACE INTO installations VALUES(?,?,?,?,?,?)',
                               (target['id'], target_binding(target), release_id,
                                release['version'], release['manifest_sha256'], now))
            db.execute('UPDATE jobs SET status=?,code=?,role_verified=MAX(role_verified,?),version=version+1,updated=?,lease=?,lease_until=? WHERE id=?',
                (current,code or status,int(role_verified),now,None if final else token,0 if final else now+10800,job_id))
            if current != row['status']:
                db.execute('INSERT INTO audit(at,actor,action,target,release) VALUES(?,?,?,?,?)',
                    (now,'main-server-worker',current,row['target_id'],release_id))
        if final: self.claims.pop((release_id,worker_target),None)


def handle(handler, path, body=None):
    from makerspace_api import _actor
    if not path.startswith('/api/updates/'): return False
    try:
        actor = _actor(handler, None)
        if actor is None:
            handler.svar(401, {'error':'Log ind på kontrolpanelet.'}); return True
        service = getattr(handler.server, 'update_administration', None)
        if service is None:
            handler.svar(503, {'error':'Opdateringsadministration er ikke tilgængelig.'}); return True
        if handler.command in ('GET','HEAD') and path == '/api/updates/state': result = service.snapshot()
        elif handler.command == 'POST' and path == '/api/updates/commands': result = service.execute(actor['id'],body)
        else:
            handler.svar(404, {'error':'Ukendt opdateringsfunktion.'}); return True
        handler.svar(200,result)
    except Conflict as error: handler.svar(409, {'error':str(error)})
    except PermissionError as error: handler.svar(403, {'error':str(error)})
    except (ValueError,TypeError,KeyError): handler.svar(400, {'error':'Ugyldig opdatering eller serveropsætning. Kontrollér udgivelse og computeridentitet.'})
    except (OSError,sqlite3.Error): handler.svar(503, {'error':'Opdateringsstatus kunne ikke bekræftes. Genbrug samme request-ID ved genforsøg.'})
    return True
