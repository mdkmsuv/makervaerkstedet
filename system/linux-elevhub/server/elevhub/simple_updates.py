"""Small desired-version service. No installer, shell commands or device leases.

Publisher verification is delegated to app.simple_release. Device reports are
self-reports, never proof of a successful install or permission to run a URL.
"""
from contextlib import contextmanager
from collections.abc import Mapping
from datetime import datetime, timezone
import importlib.util
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import ssl
from urllib.parse import unquote, urlsplit
import uuid


class UpdateError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fields(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise UpdateError('invalid_update_fields')


def plain(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise UpdateError('linked_update_path')
    return path


def revoke_device_updates(hub_data: Path, computer: str, *, revoked_token_sha256: str | None) -> None:
    """Local enrollment-admin hook; called under its existing revoke journal/lock.

    No device/registry/queue access. The old root-token hash prevents a teacher
    rebind during the gap between this commit and registry revocation.
    """
    if not isinstance(computer, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', computer):
        raise UpdateError('invalid_device_name')
    if revoked_token_sha256 is not None and (not isinstance(revoked_token_sha256, str)
            or not re.fullmatch('[a-f0-9]{64}', revoked_token_sha256)):
        raise UpdateError('invalid_revoked_identity')
    data = plain(hub_data)
    data.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(plain(data / 'simple-updates.sqlite3'), timeout=10)
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO meta VALUES(1,0)')
        db.execute('CREATE TABLE IF NOT EXISTS revoked_devices(device TEXT PRIMARY KEY, token_sha256 TEXT)')
        previous = db.execute('SELECT token_sha256 FROM revoked_devices WHERE device=?', (computer,)).fetchone()
        # An already-absent repeat must not erase the original identity marker.
        identity = previous[0] if previous and revoked_token_sha256 is None else revoked_token_sha256
        changed = previous is None or previous[0] != identity
        db.execute('INSERT OR REPLACE INTO revoked_devices VALUES(?,?)', (computer, identity))
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('desired', 'bindings'):
            if table in tables:
                changed = bool(db.execute('DELETE FROM ' + table + ' WHERE device=?', (computer,)).rowcount) or changed
        # Keep the last core observation as revocation history. A fresh identity
        # must be explicitly rebound and clears that observation before eligibility.
        for table in ('apps_reports', 'apps_enrolled', 'migrations'):
            if table in tables:
                changed = bool(db.execute('DELETE FROM ' + table + ' WHERE device=?', (computer,)).rowcount) or changed
        if 'apps_jobs' in tables:
            changed = bool(db.execute("UPDATE apps_jobs SET state='superseded',code='device_revoked' WHERE device=? AND state='queued'", (computer,)).rowcount) or changed
        if changed:
            db.execute('UPDATE meta SET revision=revision+1 WHERE id=1')
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def verifier_path():
    source = Path(__file__).absolute()
    if source.parent.name == 'hub':
        candidate = source.parent.parent / 'app/simple_release.py'
    elif (source.parent.name == 'elevhub' and source.parent.parent.name == 'server'
          and source.parent.parent.parent.name == 'linux-elevhub'):
        candidate = source.parents[3] / 'app/simple_release.py'
    else:
        raise UpdateError('unsupported_release_verifier_layout', 503)
    return plain(candidate)


# Load the shared contract only when needed: an empty catalog can operate before
# the optional release verifier is installed, as in the existing Windows flow.
class _DebianPackages(Mapping):
    def _mapping(self):
        spec = importlib.util.spec_from_file_location('_elevhub_package_contract', verifier_path())
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.DEBIAN_COMPONENTS

    def __getitem__(self, key):
        return self._mapping()[key]

    def __iter__(self):
        return iter(self._mapping())

    def __len__(self):
        return len(self._mapping())

DEBIAN_PACKAGES = _DebianPackages()


class SimpleUpdates:
    def __init__(self, data, config, base, registry, *, verifier=None, clock=None):
        self.config, self.base, self.registry = config, Path(base), registry
        self.verifier = verifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.bindings = config['device_bindings']
        if not isinstance(self.bindings, dict) or not isinstance(config['catalog'], list):
            raise UpdateError('invalid_update_configuration')
        for binding in self.bindings.values():
            fields(binding, ('role', 'platform'))
            if (binding['role'], binding['platform']) not in (('student', 'debian-amd64'), ('server', 'windows-amd64'), ('laser_station', 'windows-amd64')):
                raise UpdateError('invalid_device_binding')
        self.path = Path(data) / 'simple-updates.sqlite3'
        self.catalog_path = self.path.parent / 'simple-update-catalog.json'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if config['apt_cache'] != 'public-apt-cache':
            raise UpdateError('apt_cache_must_be_public_cache')
        self.cache = plain(self.base / 'public-apt-cache')
        if self.cache == plain(self.path.parent) or self.cache in plain(self.path.parent).parents or self.cache == plain(self.base):
            raise UpdateError('apt_cache_must_be_dedicated')
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO meta VALUES(1,0);
                CREATE TABLE IF NOT EXISTS highwater(scope TEXT PRIMARY KEY, sequence INTEGER NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, body TEXT NOT NULL, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS desired(device TEXT NOT NULL, component TEXT NOT NULL, hash TEXT NOT NULL,
                    revision INTEGER NOT NULL, rollback INTEGER NOT NULL, PRIMARY KEY(device,component));
                CREATE TABLE IF NOT EXISTS reports(device TEXT PRIMARY KEY, installed TEXT NOT NULL, result TEXT, seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bindings(device TEXT PRIMARY KEY, role TEXT NOT NULL, platform TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revoked_devices(device TEXT PRIMARY KEY, token_sha256 TEXT);
                CREATE TABLE IF NOT EXISTS migrations(device TEXT NOT NULL,core_hash TEXT NOT NULL,revision INTEGER NOT NULL,
                    identity TEXT NOT NULL,catalog_hash TEXT NOT NULL,core_version TEXT NOT NULL,apps TEXT NOT NULL,
                    ack INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(device,core_hash,revision));
            ''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def release_verifier(self):
        if self.verifier is None:
            try:
                # Fixed sibling product path: also works with the legacy CLI's
                # script-directory sys.path, without importing another checkout.
                spec = importlib.util.spec_from_file_location('_elevhub_simple_release', verifier_path())
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.verifier = module.verify_manifest
            except (ImportError, OSError):
                raise UpdateError('release_verifier_unavailable', 503) from None
        return self.verifier

    def catalog(self, db):
        entries = self.config['catalog']
        if self.catalog_path.exists():
            from release_feed import strict_json
            path = plain(self.catalog_path)
            if path.stat().st_size > 2 * 1024**2:
                raise UpdateError('imported_catalog_too_large', 503)
            document = strict_json(path.read_bytes())
            fields(document, ('schema', 'sequence', 'descriptor_sha256', 'catalog'))
            if document['schema'] != 1 or not isinstance(document['catalog'], list):
                raise UpdateError('invalid_imported_catalog', 503)
            entries = document['catalog']
        if not entries:
            return {}, []
        verifier = self.release_verifier()
        verified, errors = {}, []
        for entry in entries:
            if isinstance(entry, dict) and entry.get('component') == 'app-catalog':
                continue  # Schema2 is independently verified by AppAdmin.
            try:
                manifest = plain(self.base / entry['manifest'])
                signature = plain(self.base / entry['signature'])
                if manifest.stat().st_size > 1024 * 1024 or signature.stat().st_size > 16384:
                    raise UpdateError('release_metadata_too_large')
                raw = manifest.read_bytes()
                result = verifier(raw, signature.read_bytes(),
                    minisign_exe=plain(self.base / self.config['minisign_exe']),
                    minisign_sha256=self.config['minisign_sha256'], public_key=self.config['public_key'],
                    repository=self.config['repository'], role=entry['role'], platform=entry['platform'],
                    component=entry['component'], now=self.clock())
                # Recheck binding even with an injected publisher used in tests.
                for name in ('repository', 'role', 'platform', 'component'):
                    expected = self.config[name] if name == 'repository' else entry[name]
                    if result[name] != expected:
                        raise UpdateError('release_binding_mismatch')
                digest = result['_manifest_sha256']
                if not re.fullmatch('[a-f0-9]{64}', digest) or type(result['sequence']) is not int or result['sequence'] < 1:
                    raise UpdateError('invalid_release_identity')
                if hashlib.sha256(raw).hexdigest() != digest or manifest != plain(self.cache / 'manifests' / (digest + '.json')) or signature != plain(self.cache / 'manifests' / (digest + '.minisig')):
                    raise UpdateError('release_cache_hash_binding_mismatch')
                if result['platform'] == 'debian-amd64':
                    allowed = DEBIAN_PACKAGES.get(result['component'])
                    if not result['artifacts'] or allowed is None:
                        raise UpdateError('component_packages_not_configured')
                    for artifact in result['artifacts']:
                        if (artifact['package'], artifact['architecture']) != allowed:
                            raise UpdateError('component_package_not_allowed')
                        if artifact['version'] != result['version']:
                            raise UpdateError('component_package_version_mismatch')
                scope = canonical([result[k] for k in ('repository', 'role', 'platform', 'component')])
                old = db.execute('SELECT * FROM highwater WHERE scope=?', (scope,)).fetchone()
                if old and old['sequence'] == result['sequence'] and old['hash'] != digest:
                    raise UpdateError('release_sequence_collision')
                if not old or result['sequence'] > old['sequence']:
                    db.execute('INSERT OR REPLACE INTO highwater VALUES(?,?,?)', (scope, result['sequence'], digest))
                verified[digest] = dict(result, _scope=scope)
                verified[digest]['orderable'] = result['platform'] == 'debian-amd64'
            except (ValueError, OSError, KeyError, TypeError, OverflowError) as error:
                errors.append({'component': entry.get('component'), 'code': getattr(error, 'code', 'release_unavailable')})
        # Evaluate only after every entry, independent of configuration order.
        for item in verified.values():
            highest = db.execute('SELECT sequence FROM highwater WHERE scope=?', (item['_scope'],)).fetchone()[0]
            item['rollback_required'] = item['sequence'] < highest
        return verified, errors

    def devices(self, db):
        registered = set(self.registry.inventar() or []) if self.registry is not None else set()
        configured = set(self.bindings) | {row[0] for row in db.execute('SELECT device FROM bindings')}
        revoked = {row[0] for row in db.execute('SELECT device FROM revoked_devices')}
        return (registered & configured) - revoked

    def binding(self, device, db):
        # An explicit teacher choice survives restart/config edits. Config is
        # only the bootstrap fallback, never an implicit rebind instruction.
        row = db.execute('SELECT role,platform FROM bindings WHERE device=?', (device,)).fetchone()
        return dict(row) if row else self.bindings[device]

    def eligible(self, device, manifest, db):
        binding = self.binding(device, db)
        return all(manifest[key] == binding[key] for key in ('role', 'platform'))

    def _apps(self,db):
        admin=getattr(self,'app_admin',None)
        if admin is not None:return admin
        admin=getattr(self,'_migration_admin_cache',None)
        if admin is None:
            from app_admin import AppAdmin
            admin=self._migration_admin_cache=AppAdmin(self,db=db)
        else:
            # Initialization may have been rolled back with a rejected core
            # order. Repair it on this connection while retaining verified file
            # stamps, rather than hashing large app archives on every overview.
            admin._initialize(db)
        return admin

    def _at_least(self,version,minimum):
        if not isinstance(version,str):return False
        from app_admin import contract
        if not hasattr(self,'_migration_contract'):self._migration_contract=contract()
        try:return self._migration_contract.compare_versions(version,minimum)>=0
        except ValueError:return False

    def _observed(self,db,device):
        report=db.execute('SELECT installed FROM reports WHERE device=?',(device,)).fetchone()
        return {item['component']:item['version'] for item in json.loads(report[0])} if report else {}

    def migration_active(self,db,device):
        return db.execute('SELECT 1 FROM migrations WHERE device=? AND completed=0',(device,)).fetchone() is not None

    def _prerequisite(self,db,device,item,*,row=None):
        observed=self._observed(db,device)
        core=observed.get('studentcore')
        if item['component']=='scratch' and (self.migration_active(db,device) or self._at_least(core,'2.1.0~')):
            return 'legacy_scratch_suppressed'
        if item['component']=='updater' and self._at_least(item['version'],'1.1.0~'):
            if not self._at_least(core,'2.0.0~20261114.2'):return 'legacy_core_baseline_required'
        if item['component']=='studentcore' and self._at_least(item['version'],'2.1.0~'):
            if not self._at_least(observed.get('updater'),'1.1.0~'):return 'updater_bridge_required'
            if not self._at_least(core,'2.0.0~20261114.2'):return 'legacy_core_baseline_required'
            if row is not None:
                migration=db.execute('SELECT * FROM migrations WHERE device=? AND completed=0',(device,)).fetchone()
                if migration is not None:
                    if migration['core_hash']!=row['hash'] or migration['revision']!=row['revision'] or migration['identity']!=self.registry.identitet(device):
                        return 'migration_binding_required'
                elif not self._at_least(core,'2.1.0~'):return 'migration_binding_required'
        return None

    def _bind_migration(self,db,device,item,digest,revision,admin,catalog):
        if self.migration_active(db,device):raise UpdateError('migration_in_progress',409)
        if not catalog:raise UpdateError('migration_catalog_required',409)
        entries={entry['descriptor']['id']:entry['descriptor'] for entry in catalog['apps']}
        builtin={'laser','folie','3dprint','microbit','scratch'}
        if not builtin<=set(entries):raise UpdateError('migration_catalog_incomplete',409)
        if not all(admin.contract.compatibility(entries[name],core_version=item['version'])['compatible'] for name in builtin):
            raise UpdateError('migration_catalog_incompatible',409)
        admin._artifacts(catalog)
        if db.execute("SELECT 1 FROM apps_jobs WHERE device=? AND state='queued'",(device,)).fetchone():
            raise UpdateError('migration_pending_apps',409)
        selected=builtin if 'scratch' in self._observed(db,device) else builtin-{'scratch'}
        identity=self.registry.identitet(device)
        if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{64}',identity):raise UpdateError('unknown_update_device',403)
        db.execute('INSERT INTO migrations(device,core_hash,revision,identity,catalog_hash,core_version,apps) VALUES(?,?,?,?,?,?,?)',
            (device,digest,revision,identity,catalog['_manifest_sha256'],item['version'],canonical(sorted(selected))))
        db.execute('INSERT OR REPLACE INTO apps_enrolled VALUES(?,1)',(device,))

    def migration(self,device,digest,revision):
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or type(revision) is not int or not 1<=revision<=2**63-1:
            raise UpdateError('migration_not_found',404)
        with self.transaction() as db:
            if device not in self.devices(db) or self.binding(device,db)!={'role':'student','platform':'debian-amd64'}:
                raise UpdateError('unknown_update_device',403)
            row=db.execute('SELECT * FROM migrations WHERE device=? AND core_hash=? AND revision=?',(device,digest,revision)).fetchone()
            identity=self.registry.identitet(device)
            if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{64}',identity):raise UpdateError('migration_state_unavailable',503)
            if row is None or row['identity']!=identity:raise UpdateError('migration_not_found',404)
            if type(row['completed']) is not int or row['completed'] not in (0,1):raise UpdateError('migration_state_unavailable',503)
            return {'schema':1,'core_manifest_sha256':digest,'revision':revision,
                    'catalog_sha256':row['catalog_hash'],'apps':json.loads(row['apps']),'completed':bool(row['completed'])}

    def complete_migration(self,db,device,*,admin=None):
        row=db.execute('SELECT * FROM migrations WHERE device=? AND completed=0',(device,)).fetchone()
        if row is None or row['ack']!=1 or device not in self.devices(db):return False
        if self.binding(device,db)!={'role':'student','platform':'debian-amd64'} or row['identity']!=self.registry.identitet(device):return False
        if self._observed(db,device).get('studentcore')!=row['core_version']:return False
        admin=admin or self._apps(db)
        report,_=admin._report(db,device)
        if not admin._capable(db,device,report):return False
        try:
            # Artifact bytes were authenticated before authorization and by the
            # native transaction. Completion compares signed metadata to current
            # inventory; it must not depend on retaining unselected old .deb files.
            catalog=admin._verify(row['catalog_hash'])
        except (ValueError,OSError):return False
        selected=set(json.loads(row['apps']))
        expected={entry['descriptor']['id']:entry['descriptor'] for entry in catalog['apps']}
        actual={item['id']:item for item in report['installed']}
        if any(issue['app_id'] in selected for issue in report['issues']):return False
        for name in selected:
            item=actual.get(name);want=expected.get(name)
            if item is None or want is None or item['state']!='installed' or (item['package'],item['version'])!=(want['package'],want['version']):
                return False
        db.execute('UPDATE migrations SET completed=1 WHERE device=? AND core_hash=? AND revision=?',(device,row['core_hash'],row['revision']))
        db.execute("DELETE FROM desired WHERE device=? AND component='studentcore' AND hash=? AND revision=?",(device,row['core_hash'],row['revision']))
        return True

    def offer(self, row, catalog, db):
        item = catalog.get(row['hash'])
        if item is None or not item['orderable'] or not self.eligible(row['device'], item, db) or (item['rollback_required'] and not row['rollback']) or self._prerequisite(db,row['device'],item,row=row):
            return None
        return {'component': row['component'], 'version': item['version'], 'revision': row['revision'],
                'manifest_sha256': row['hash'], 'rollback': bool(row['rollback']),
                'manifest_path': '/apt/manifests/' + row['hash'] + '.json',
                'signature_path': '/apt/manifests/' + row['hash'] + '.minisig',
                'packages': item['artifacts']}

    def overview(self):
        with self.transaction() as db:
            catalog, errors = self.catalog(db)
            if any(item['component']=='studentcore' and self._at_least(item['version'],'2.1.0~') for item in catalog.values()):
                self._apps(db).catalog(db)
            devices = []
            for device in sorted(self.devices(db)):
                report = db.execute('SELECT * FROM reports WHERE device=?', (device,)).fetchone()
                desired = [dict(row, available=self.offer(row, catalog, db) is not None)
                           for row in db.execute('SELECT * FROM desired WHERE device=?', (device,))]
                migration=db.execute('SELECT core_hash AS core_manifest_sha256,revision FROM migrations WHERE device=? AND completed=0',(device,)).fetchone()
                devices.append({'device': device, **self.binding(device, db), 'desired': desired,
                    'migration':dict(migration) if migration is not None else None,
                    'installed': json.loads(report['installed']) if report else [],
                    'result': json.loads(report['result']) if report and report['result'] else None,
                    'last_seen': report['seen'] if report else None, 'installation_evidence': 'device_report' if report else 'unknown'})
            return {'schema': 1, 'revision': db.execute('SELECT revision FROM meta').fetchone()[0],
                    'catalog': [{k: v for k, v in item.items() if not k.startswith('_')} | {'manifest_sha256': digest}
                                for digest, item in catalog.items()], 'catalog_errors': errors, 'devices': devices,
                    'unbound_devices': sorted(set(self.registry.inventar() or []) - self.devices(db)) if self.registry is not None else []}

    @staticmethod
    def command_id(value):
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise UpdateError('invalid_command_id') from None

    def bind(self, body):
        fields(body, ('command_id', 'expected_revision', 'device', 'role', 'platform'))
        self.command_id(body['command_id'])
        if type(body['expected_revision']) is not int or not isinstance(body['device'], str):
            raise UpdateError('invalid_device_binding')
        if (body['role'], body['platform']) not in (('student', 'debian-amd64'), ('server', 'windows-amd64'), ('laser_station', 'windows-amd64')):
            raise UpdateError('invalid_device_binding')
        payload = canonical(body)
        with self.transaction() as db:
            if self.registry is None or body['device'] not in (self.registry.inventar() or []):
                raise UpdateError('unknown_update_device', 403)
            revoked = db.execute('SELECT token_sha256 FROM revoked_devices WHERE device=?', (body['device'],)).fetchone()
            if revoked:
                identity = self.registry.identitet(body['device'])
                if identity is None or identity == revoked['token_sha256']:
                    raise UpdateError('fresh_enrollment_required', 409)
            previous = db.execute('SELECT * FROM commands WHERE id=?', (body['command_id'],)).fetchone()
            if previous:
                if previous['body'] != payload:
                    raise UpdateError('command_id_conflict', 409)
                return json.loads(previous['receipt'])
            revision = db.execute('SELECT revision FROM meta').fetchone()[0]
            if revision != body['expected_revision']:
                raise UpdateError('update_revision_conflict', 409)
            old_binding = db.execute('SELECT role,platform FROM bindings WHERE device=?', (body['device'],)).fetchone()
            old_binding = dict(old_binding) if old_binding else self.bindings.get(body['device'])
            db.execute('INSERT OR REPLACE INTO bindings VALUES(?,?,?)', (body['device'], body['role'], body['platform']))
            db.execute('DELETE FROM revoked_devices WHERE device=?', (body['device'],))
            # A rebind must not inherit a previous role's desired operation.
            old_identity=db.execute('SELECT identity FROM migrations WHERE device=? LIMIT 1',(body['device'],)).fetchone()
            identity_changed=old_identity is not None and old_identity['identity']!=self.registry.identitet(body['device'])
            if old_binding != {'role': body['role'], 'platform': body['platform']} or identity_changed or revoked is not None:
                db.execute('DELETE FROM desired WHERE device=?', (body['device'],))
                for table in ('migrations','reports','apps_reports','apps_enrolled'):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                        db.execute('DELETE FROM '+table+' WHERE device=?',(body['device'],))
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apps_jobs'").fetchone():
                    db.execute("UPDATE apps_jobs SET state='superseded',code='device_rebound' WHERE device=? AND state='queued'",(body['device'],))
            db.execute('UPDATE meta SET revision=?', (revision + 1,))
            response = {'schema': 1, 'revision': revision + 1, 'command_id': body['command_id']}
            db.execute('INSERT INTO commands VALUES(?,?,?)', (body['command_id'], payload, canonical(response)))
            return response

    def cancel_migration(self,body):
        """Stop retry authority; never kill apt, undo packages or erase reports."""
        fields(body,('command_id','expected_revision','device','core_manifest_sha256','migration_revision'))
        self.command_id(body['command_id'])
        if (type(body['expected_revision']) is not int or body['expected_revision']<0
                or type(body['migration_revision']) is not int or not 1<=body['migration_revision']<2**63
                or not isinstance(body['device'],str) or not isinstance(body['core_manifest_sha256'],str)
                or not re.fullmatch('[a-f0-9]{64}',body['core_manifest_sha256'])):
            raise UpdateError('invalid_migration_cancellation')
        payload=canonical(body)
        with self.transaction() as db:
            if body['device'] not in self.devices(db) or self.binding(body['device'],db)!={'role':'student','platform':'debian-amd64'}:
                raise UpdateError('unknown_update_device',403)
            old=db.execute('SELECT * FROM commands WHERE id=?',(body['command_id'],)).fetchone()
            if old:
                if old['body']!=payload:raise UpdateError('command_id_conflict',409)
                return json.loads(old['receipt'])
            revision=db.execute('SELECT revision FROM meta').fetchone()[0]
            if revision!=body['expected_revision']:raise UpdateError('update_revision_conflict',409)
            row=db.execute('SELECT * FROM migrations WHERE device=? AND core_hash=? AND revision=? AND completed=0',
                (body['device'],body['core_manifest_sha256'],body['migration_revision'])).fetchone()
            if row is None:raise UpdateError('migration_not_pending',409)
            db.execute('DELETE FROM desired WHERE device=? AND component=? AND hash=? AND revision=?',
                (body['device'],'studentcore',row['core_hash'],row['revision']))
            db.execute('DELETE FROM migrations WHERE device=? AND core_hash=? AND revision=?',
                (body['device'],row['core_hash'],row['revision']))
            db.execute('UPDATE meta SET revision=?',(revision+1,))
            receipt={'schema':1,'revision':revision+1,'command_id':body['command_id']}
            db.execute('INSERT INTO commands VALUES(?,?,?)',(body['command_id'],payload,canonical(receipt)))
            return receipt

    def order(self, body):
        fields(body, ('command_id', 'expected_revision', 'devices', 'component', 'manifest_sha256', 'rollback_manifest_sha256'))
        self.command_id(body['command_id'])
        if not isinstance(body['component'], str):
            raise UpdateError('invalid_update_order')
        for key in ('manifest_sha256', 'rollback_manifest_sha256'):
            if key == 'rollback_manifest_sha256' and body[key] is None:
                continue
            if not isinstance(body[key], str) or not re.fullmatch('[a-f0-9]{64}', body[key]):
                raise UpdateError('invalid_update_order')
        if type(body['expected_revision']) is not int or not isinstance(body['devices'], list) or not body['devices'] or any(not isinstance(d, str) for d in body['devices']) or len(set(body['devices'])) != len(body['devices']):
            raise UpdateError('invalid_update_order')
        payload = canonical(body)
        with self.transaction() as db:
            if not set(body['devices']) <= self.devices(db):
                raise UpdateError('unknown_update_device', 403)
            receipt = db.execute('SELECT * FROM commands WHERE id=?', (body['command_id'],)).fetchone()
            if receipt:
                if receipt['body'] != payload:
                    raise UpdateError('command_id_conflict', 409)
                return json.loads(receipt['receipt'])
            catalog, _ = self.catalog(db)
            item = catalog.get(body['manifest_sha256'])
            admin=app_catalog=None
            if item is not None and item['component']=='studentcore' and self._at_least(item['version'],'2.1.0~'):
                admin=self._apps(db);app_catalog,_=admin.catalog(db)
            revision = db.execute('SELECT revision FROM meta').fetchone()[0]
            if revision != body['expected_revision']:
                raise UpdateError('update_revision_conflict', 409)
            if item is None or item['component'] != body['component']:
                raise UpdateError('unknown_release', 409)
            if not item['orderable']:
                raise UpdateError('windows_update_requires_role_installer', 409)
            rollback = body['rollback_manifest_sha256'] == body['manifest_sha256']
            if body['rollback_manifest_sha256'] is not None and not rollback:
                raise UpdateError('rollback_hash_mismatch')
            if item['rollback_required'] and not rollback:
                raise UpdateError('explicit_rollback_required', 409)
            for device in body['devices']:
                if not self.eligible(device, item, db):
                    raise UpdateError('device_release_binding_mismatch', 403)
                error=self._prerequisite(db,device,item)
                if error:raise UpdateError(error,409)
                if item['component']=='studentcore':
                    if self.migration_active(db,device):raise UpdateError('migration_in_progress',409)
                    if self._at_least(item['version'],'2.1.0~') and not self._at_least(self._observed(db,device).get('studentcore'),'2.1.0~'):
                        self._bind_migration(db,device,item,body['manifest_sha256'],revision+1,admin,app_catalog)
                db.execute('INSERT OR REPLACE INTO desired VALUES(?,?,?,?,?)', (device, item['component'], body['manifest_sha256'], revision + 1, int(rollback)))
            db.execute('UPDATE meta SET revision=?', (revision + 1,))
            response = {'schema': 1, 'revision': revision + 1, 'command_id': body['command_id']}
            db.execute('INSERT INTO commands VALUES(?,?,?)', (body['command_id'], payload, canonical(response)))
            return response

    def poll(self, device, body):
        fields(body, ('schema', 'installed', 'result'))
        if body['schema'] != 1 or type(body['schema']) is not int or not isinstance(body['installed'], list) or len(body['installed']) > 32:
            raise UpdateError('invalid_update_report')
        components = set()
        for installed in body['installed']:
            fields(installed, ('component', 'version'))
            if not isinstance(installed['component'], str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}', installed['component']) or installed['component'] in components:
                raise UpdateError('invalid_update_report')
            if not isinstance(installed['version'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:+_~\-]{0,127}', installed['version']):
                raise UpdateError('invalid_update_report')
            components.add(installed['component'])
        result = body['result']
        if result is not None:
            fields(result, ('revision', 'state', 'code'))
            if type(result['revision']) is not int or result['revision'] < 1 or result['state'] not in ('succeeded', 'failed'):
                raise UpdateError('invalid_update_result')
            if result['code'] is not None and (not isinstance(result['code'], str) or not re.fullmatch('[a-z][a-z0-9_]{0,79}', result['code'])):
                raise UpdateError('invalid_update_result')
        with self.transaction() as db:
            if device not in self.devices(db):
                raise UpdateError('unknown_update_device', 403)
            catalog, _ = self.catalog(db)
            db.execute('INSERT OR REPLACE INTO reports VALUES(?,?,?,?)',
                       (device, canonical(body['installed']), canonical(result), self.clock().isoformat()))
            if result is not None:
                db.execute('UPDATE migrations SET ack=? WHERE device=? AND revision=? AND completed=0',
                    (int(result['state']=='succeeded'),device,result['revision']))
            self.complete_migration(db,device)
            if result is not None and result['state'] == 'succeeded':
                installed = {item['component']: item['version'] for item in body['installed']}
                for row in db.execute('SELECT * FROM desired WHERE device=? AND revision=?', (device, result['revision'])):
                    if db.execute('SELECT 1 FROM migrations WHERE device=? AND core_hash=? AND revision=?',(device,row['hash'],row['revision'])).fetchone():continue
                    expected = self.offer(row, catalog, db)
                    # A receipt alone does not complete an operation. Match the
                    # actual report to the still-verified authorized version.
                    if expected is not None and installed.get(row['component']) == expected['version']:
                        db.execute('DELETE FROM desired WHERE device=? AND component=?', (device, row['component']))
            desired = [self.offer(row, catalog, db) for row in db.execute('SELECT * FROM desired WHERE device=?', (device,))]
            return {'schema': 1, 'revision': db.execute('SELECT revision FROM meta').fetchone()[0],
                    'poll_after_seconds': 60, 'desired': [item for item in desired if item is not None]}

    def apt_file(self, raw):
        path = unquote(urlsplit(raw).path)
        if not path.startswith('/apt/') or '\\' in path or '\x00' in path:
            raise UpdateError('unsafe_apt_path')
        relative = path[5:]
        # Native APT uses /apt/./<index> for a flat repository with Suites: ./ .
        # Accept that single fixed prefix; all remaining segments stay strict.
        if relative.startswith('./'):
            relative = relative[2:]
        if not relative or str(PurePosixPath(relative)) != relative or any(part in ('', '.', '..') or ':' in part for part in relative.split('/')):
            raise UpdateError('unsafe_apt_path')
        file = plain(self.cache / relative)
        if self.cache not in file.parents or not file.is_file():
            raise UpdateError('apt_file_not_found', 404)
        return file


def handle(handler, path, body=None, length=0):
    """Routes shared by teacher and pupil HTTPS listeners; no new auth system."""
    raw_path = urlsplit(handler.path).path
    service = getattr(handler.server, 'simple_updates', None)
    if service is not None and (raw_path.startswith('/api/updates/') or path == '/api/enhed/opdatering'):
        handler.close_connection = True
        handler.svar(410, {'error': 'legacy_updates_disabled'})
        return True
    if path not in ('/api/admin/updates', '/api/admin/updates/bind', '/api/admin/updates/source', '/api/admin/updates/refresh', '/api/admin/updates/cancel-migration', '/api/device/updates') and not raw_path.startswith('/apt/'):
        return False
    try:
        if service is None:
            raise UpdateError('simple_updates_unavailable', 503)
        if raw_path.startswith('/apt/'):
            if handler.command not in ('GET', 'HEAD'):
                raise UpdateError('method_not_allowed', 405)
            file = service.apt_file(handler.path)
            with file.open('rb') as source:
                size = os.fstat(source.fileno()).st_size
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/octet-stream')
                handler.send_header('Content-Length', str(size))
                handler.send_header('X-Content-Type-Options', 'nosniff')
                handler.end_headers()
                if handler.command == 'GET':
                    while chunk := source.read(64 * 1024):
                        handler.wfile.write(chunk)
            return True
        if path in ('/api/admin/updates', '/api/admin/updates/bind', '/api/admin/updates/source', '/api/admin/updates/refresh', '/api/admin/updates/cancel-migration'):
            if not hasattr(handler, 'logget_ind') or not handler.logget_ind():
                raise UpdateError('teacher_login_required', 401)
            if handler.command in ('GET', 'HEAD'):
                if path.endswith(('/bind', '/refresh', '/cancel-migration')):
                    raise UpdateError('method_not_allowed', 405)
                if path.endswith('/source'):
                    worker = getattr(service, 'source_worker', None)
                    if worker is None:
                        raise UpdateError('update_source_unavailable', 503)
                    response = worker.status()
                else:
                    response = service.overview()
            elif handler.command == 'POST':
                origins, hosts = handler.headers.get_all('Origin', []), handler.headers.get_all('Host', [])
                scheme = 'https://' if isinstance(handler.connection, ssl.SSLSocket) else 'http://'
                if len(origins) != 1 or len(hosts) != 1 or origins[0] != scheme + hosts[0]:
                    raise UpdateError('teacher_origin_required', 403)
                if path.endswith('/refresh'):
                    worker = getattr(service, 'source_worker', None)
                    if worker is None:
                        raise UpdateError('update_source_unavailable', 503)
                    response = worker.request_refresh(body)
                elif path.endswith('/source'):
                    raise UpdateError('method_not_allowed', 405)
                else:
                    response = service.cancel_migration(body) if path.endswith('/cancel-migration') else service.bind(body) if path.endswith('/bind') else service.order(body)
            else:
                raise UpdateError('method_not_allowed', 405)
        else:
            if handler.command != 'POST':
                raise UpdateError('method_not_allowed', 405)
            if handler.headers.get('Origin') is not None or not hasattr(handler, 'enhed'):
                raise UpdateError('native_device_required', 403)
            device = handler.enhed('root')
            if device is None:
                return True
            if handler.headers.get_content_type() != 'application/json':
                raise UpdateError('json_required', 415)
            response = service.poll(device, handler.laes_json(length) if body is None else body)
        handler.svar(202 if path == '/api/admin/updates/refresh' and handler.command == 'POST' else 200, response)
    except UpdateError as error:
        handler.close_connection = True
        handler.svar(error.status, {'error': error.code})
    except (ValueError, TypeError, KeyError):
        handler.close_connection = True
        handler.svar(400, {'error': 'invalid_update_request'})
    except (OSError, sqlite3.Error):
        handler.close_connection = True
        handler.svar(503, {'error': 'update_persistence_unavailable'})
    return True
