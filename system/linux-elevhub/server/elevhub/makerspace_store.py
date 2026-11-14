"""Transactional configuration and manual work orders, never a hardware queue.

Existing printer/laser services remain authoritative for their physical jobs.
One SQLite transaction covers configuration, deduplication and successful audit.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager

from makerspace_workflows import validate_definition


class Conflict(ValueError):
    pass


ACTIONS = {
    'machine.create': ('machines:write', 'Registrér manuel arbejdsstation'),
    'machine.update': ('machines:write', 'Ret manuel arbejdsstation'),
    'workflow.create': ('workflows:write', 'Opret workflowkladde'),
    'workflow.edit': ('workflows:write', 'Ret workflowkladde'),
    'workflow.publish': ('workflows:write', 'Aktivér workflowversion'),
    'workflow.rollback': ('workflows:write', 'Vælg tidligere workflowversion'),
    'job.create': ('jobs:write', 'Opret opgave ved manuel station'),
    'job.advance': ('jobs:write', 'Bekræft opgavens aktuelle trin'),
    'printer.create': ('machines:write', 'Registrér Bambu-printer i printertjenesten'),
    'printer.update': ('machines:write', 'Ret Bambu-printer i printertjenesten'),
    'printer.workflow.assign': ('machines:write', 'Knyt en publiceret workflowrevision til en printer'),
    'printer.job.advance': ('jobs:write', 'Bekræft næste trin for et printerjob'),
    'printer.queue.pause': ('queues:write', 'Sæt printkøen på pause eller genoptag den'),
    'printer.certificate.confirm': ('machines:write', 'Bekræft et observeret printercertifikat'),
    'printer.certificate.probe': ('machines:write', 'Kontrollér printerens TLS-certifikater'),
    'printer.certificate.cancel': ('machines:write', 'Afslut et gammelt printercertifikatforsøg'),
    'laser.station.create': ('machines:write', 'Registrér laserstation'),
    'laser.station.update': ('machines:write', 'Ret laserstation'),
    'laser.workflow.assign': ('machines:write', 'Knyt en publiceret laserworkflowrevision til en station'),
    'laser.job.bind': ('jobs:write', 'Knyt et laserjob til en station'),
    'laser.job.advance': ('jobs:write', 'Bekræft næste trin for et laserjob'),
    'laser.job.finish': ('jobs:write', 'Bekræft færdigt laserjob'),
    'laser.job.cancel': ('jobs:write', 'Annullér laserjob'),
}
ID = re.compile(r'^[a-f0-9]{32}$')
PRINTER_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')


def text(value, maximum=80, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or
            (not empty and not value.strip()) or any(not c.isprintable() for c in value)):
        raise ValueError('Ugyldigt tekstfelt.')
    return value.strip()


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        raise ValueError('Handlingen har manglende eller ukendte felter.')


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Ugyldigt ID.')
    return value


def printer_identifier(value):
    if not isinstance(value, str) or not PRINTER_ID.fullmatch(value):
        raise ValueError('Ugyldigt printer-ID.')
    return value


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def public_printer_job(value):
    """Whitelist the backend job contract; never expose backend metadata."""
    if not isinstance(value, dict):
        raise ValueError('Ugyldigt printerjob.')
    required = ('id', 'printer_id', 'filename', 'status', 'version', 'workflow_id',
                'workflow_revision', 'workflow_name', 'step', 'production_authorized')
    if any(key not in value for key in required):
        raise ValueError('Ugyldigt printerjob.')
    if any(not isinstance(value[key], str) or not value[key].strip()
           for key in ('id', 'printer_id', 'filename', 'status')):
        raise ValueError('Ugyldigt printerjob.')
    if type(value['version']) is not int or value['version'] < 1:
        raise ValueError('Ugyldig printerjobversion.')
    if value['workflow_id'] is not None and not isinstance(value['workflow_id'], str):
        raise ValueError('Ugyldigt workflow.')
    if value['workflow_name'] is not None and not isinstance(value['workflow_name'], str):
        raise ValueError('Ugyldigt workflow.')
    if value['workflow_revision'] is not None and (type(value['workflow_revision']) is not int or value['workflow_revision'] < 1):
        raise ValueError('Ugyldig workflowrevision.')
    if type(value['production_authorized']) is not bool:
        raise ValueError('Ugyldig produktionsstatus.')
    step = value['step']
    if step is not None:
        if not isinstance(step, dict) or set(step) != {'id', 'label', 'role', 'action'} or any(not isinstance(step[k], str) for k in step):
            raise ValueError('Ugyldigt printerjobtrin.')
        step = {k: step[k] for k in ('id', 'label', 'role', 'action')}
    return {key: value[key] for key in required[:-2]} | {'step': step,
            'production_authorized': value['production_authorized']}


def public_laser_job(value):
    required = ('id', 'project', 'status', 'station_id', 'version', 'workflow_id', 'workflow_revision',
                'workflow_name', 'step', 'production_authorized', 'physical_done', 'handoff_claimed')
    if not isinstance(value, dict) or any(k not in value for k in required):
        raise ValueError('Ugyldigt laserjob.')
    if any(not isinstance(value[k], str) for k in ('id', 'project', 'status')) or type(value['version']) is not int or value['version'] < 0:
        raise ValueError('Ugyldigt laserjob.')
    if value['station_id'] is not None and not isinstance(value['station_id'], str): raise ValueError('Ugyldig station.')
    if value['workflow_id'] is not None and not isinstance(value['workflow_id'], str): raise ValueError('Ugyldigt workflow.')
    if value['workflow_revision'] is not None and (type(value['workflow_revision']) is not int or value['workflow_revision'] < 1): raise ValueError('Ugyldigt workflow.')
    if value['workflow_name'] is not None and not isinstance(value['workflow_name'], str): raise ValueError('Ugyldigt workflow.')
    if any(type(value[k]) is not bool for k in ('production_authorized', 'physical_done', 'handoff_claimed')): raise ValueError('Ugyldig laserjobstatus.')
    step = value['step']
    if step is not None:
        if not isinstance(step, dict) or set(step) != {'id', 'label', 'role', 'action'} or any(not isinstance(step[k], str) for k in step): raise ValueError('Ugyldigt laserjobtrin.')
        step = {k: step[k] for k in ('id', 'label', 'role', 'action')}
    return {k: value[k] for k in required[:-1]} | {'handoff_claimed': value['handoff_claimed']}


def public_queue(value):
    if (not isinstance(value, dict) or value.get('id') != 'print'
            or type(value.get('paused')) is not bool
            or type(value.get('version')) is not int or value['version'] < 1):
        raise ValueError('Ugyldig printkøstatus.')
    return {key: value[key] for key in ('id', 'paused', 'version')}


class MakerspaceStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError('Databasen må ikke være et symbolsk link.')
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('Ukendt makerspace-databaseversion.')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS objects (
                    type TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(type,id));
                CREATE TABLE IF NOT EXISTS workflow_versions (
                    id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS requests (
                    actor TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
                    response TEXT NOT NULL, PRIMARY KEY(actor,request_id));
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT,
                    request_id TEXT NOT NULL, outcome TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _get(db, kind, key):
        identifier(key)
        row = db.execute('SELECT body FROM objects WHERE type=? AND id=?', (kind, key)).fetchone()
        if row is None:
            raise ValueError('Objektet findes ikke.')
        return json.loads(row['body'])

    @staticmethod
    def _put(db, kind, value):
        db.execute('INSERT OR REPLACE INTO objects(type,id,version,body) VALUES(?,?,?,?)',
                   (kind, value['id'], value['version'], encode(value)))

    def snapshot(self):
        result = {'machines': [], 'workflows': [], 'jobs': []}
        with self.connection() as db:
            # One read transaction, so objects and revision lists share a view.
            db.execute('BEGIN')
            for row in db.execute('SELECT type,body FROM objects ORDER BY id'):
                value = json.loads(row['body'])
                if row['type'] == 'workflow':
                    revisions = [r[0] for r in db.execute(
                        'SELECT revision FROM workflow_versions WHERE id=? ORDER BY revision', (value['id'],))]
                    value['revisions'] = revisions
                    # The panel must be able to compare the editable draft with
                    # the definition that is actually active.  Keep this
                    # read-only metadata beside the legacy revision list so
                    # older clients remain compatible.
                    active = value.get('active_revision')
                    if type(active) is int and active in revisions:
                        active_row = db.execute(
                            'SELECT body FROM workflow_versions WHERE id=? AND revision=?',
                            (value['id'], active)).fetchone()
                        active_definition = json.loads(active_row['body']) if active_row else {}
                        value['active_steps'] = active_definition.get('steps', [])
                        value['has_unpublished_changes'] = value.get('draft') != active_definition
                    else:
                        value['active_steps'] = []
                        value['has_unpublished_changes'] = bool(value.get('draft'))
                result[row['type'] + 's'].append(value)
        return result

    def audit(self, limit=100):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY sequence DESC LIMIT ?', (limit,))]

    def execute(self, actor, envelope, external=None):
        fields(envelope, ('action', 'params', 'request_id', 'expected_version'), ('confirmed',))
        action = envelope['action']
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError('Ukendt handling.')
        if 'confirmed' in envelope and action != 'printer.certificate.confirm':
            raise ValueError('Ugyldig eksplicit bekræftelse.')
        request_id = identifier(envelope['request_id'])
        expected = envelope['expected_version']
        if type(expected) is not int or expected < 0:
            raise ValueError('expected_version skal være et ikke-negativt heltal.')
        # Actors are constructed by authentication, never by the request body.
        actor_id = actor['id']
        if not isinstance(actor_id, str) or not re.fullmatch(r'teacher:[a-f0-9]{32}', actor_id):
            raise PermissionError('Ugyldig aktør.')
        try:
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                if actor.get('role') != 'teacher':
                    raise PermissionError('Kun lærerens session må ændre makerspace.')
                if ACTIONS[action][0] not in actor['scopes']:
                    raise PermissionError('Adgangen omfatter ikke denne handling.')
                hardware = action.startswith(('printer.', 'laser.'))
                if hardware and external is None:
                    raise OSError('Printeradministrationen er ikke konfigureret.')
                digest = (external.fingerprint(envelope) if hardware else
                          hashlib.sha256(encode(envelope).encode()).hexdigest())
                existing = db.execute('SELECT digest,response FROM requests WHERE actor=? AND request_id=?',
                                      (actor_id, request_id)).fetchone()
                if existing:
                    if existing['digest'] != digest:
                        raise Conflict('Request-ID er allerede brugt til en anden ændring.')
                    return json.loads(existing['response'])
                dispatch = envelope
                if action == 'printer.workflow.assign':
                    dispatch = dict(envelope, params=self._printer_workflow(db, envelope['params']))
                elif action == 'laser.workflow.assign':
                    dispatch = dict(envelope, params=self._laser_workflow(db, envelope['params']))
                elif action == 'printer.job.advance':
                    fields(envelope['params'], ('id', 'step_id', 'confirmed'))
                    text(envelope['params']['id'], 100)
                    text(envelope['params']['step_id'], 40)
                    if envelope['params']['confirmed'] is not True or expected < 1:
                        raise ValueError('Printerjobbet kræver bekræftet trin og positiv kontrolversion.')
                elif action == 'printer.queue.pause':
                    fields(envelope['params'], ('id', 'paused'))
                    if (envelope['params']['id'] != 'print'
                            or type(envelope['params']['paused']) is not bool or expected < 1):
                        raise ValueError('Vælg printkøens pause og aktuelle kontrolversion.')
                elif action == 'printer.certificate.confirm':
                    if 'confirmed' in envelope and envelope['confirmed'] is not True:
                        raise ValueError('Certifikatbekræftelsen kræver lærerens eksplicitte bekræftelse.')
                    fields(envelope['params'], ('id', 'channel', 'fingerprint'), ('repair',))
                    printer_identifier(envelope['params']['id'])
                    if (envelope['params']['channel'] not in ('mqtt', 'ftp')
                            or not isinstance(envelope['params']['fingerprint'], str)
                            or not re.fullmatch(r'[0-9a-fA-F]{64}', envelope['params']['fingerprint'])
                            or ('repair' in envelope['params'] and type(envelope['params']['repair']) is not bool)
                            or expected < 1):
                        raise ValueError('Vælg et aktuelt observeret printercertifikat.')
                elif action == 'printer.certificate.probe':
                    fields(envelope['params'], ('id',))
                    printer_identifier(envelope['params']['id'])
                    if expected < 1:
                        raise ValueError('Vælg en aktuel printeropsætning til certifikatprobe.')
                elif action == 'printer.certificate.cancel':
                    fields(envelope['params'], ('id', 'operation_request_id'), ('operation_actor',))
                    printer_identifier(envelope['params']['id'])
                    identifier(envelope['params']['operation_request_id'])
                    if ('operation_actor' in envelope['params'] and
                            (not isinstance(envelope['params']['operation_actor'], str) or
                             not re.fullmatch(r'teacher:[a-f0-9]{32}', envelope['params']['operation_actor']))):
                        raise ValueError('Den oprindelige lærerhandling er ugyldig.')
                    if expected < 1:
                        raise ValueError('Vælg en aktuel printeropsætning til annullering.')
                elif action.startswith('laser.'):
                    self._validate_laser_command(action, envelope['params'], expected)
                if hardware:
                    # Backend persists configuration and its own receipt atomically.
                    # If this transaction fails after the remote commit, retrying
                    # the same ID recovers the receipt without reconfiguring again.
                    response = external.execute(actor_id, dispatch)
                    value = response['result']
                    if action == 'printer.job.advance':
                        value = public_printer_job(value)
                        response = {'ok': True, 'action': action, 'result': value}
                    elif action == 'printer.queue.pause':
                        value = public_queue(value)
                        response = {'ok': True, 'action': action, 'result': value}
                    elif action.startswith('laser.job.'):
                        value = public_laser_job(value)
                        response = {'ok': True, 'action': action, 'result': value}
                else:
                    value = self._apply(db, actor, action, envelope['params'], expected)
                    response = {'ok': True, 'action': action, 'result': value}
                db.execute('INSERT INTO requests VALUES(?,?,?,?)', (actor_id, request_id, digest, encode(response)))
                db.execute('INSERT INTO audit(at,actor,action,target,request_id,outcome) VALUES(?,?,?,?,?,?)',
                           (self.clock(), actor_id, action, value['id'], request_id, 'applied'))
                return response
        except (ValueError, PermissionError) as error:
            # Never persist submitted values, error text, credentials or student names.
            with self.connection() as db:
                db.execute('INSERT INTO audit(at,actor,action,target,request_id,outcome) VALUES(?,?,?,?,?,?)',
                           (self.clock(), actor_id, action, None, request_id,
                            'conflict' if isinstance(error, Conflict) else 'denied' if isinstance(error, PermissionError) else 'invalid'))
            raise

    def _printer_workflow(self, db, params):
        fields(params, ('id', 'workflow_id', 'revision'))
        text(params['id'], 100)
        workflow_id, revision = params['workflow_id'], params['revision']
        if workflow_id is None or revision is None:
            if workflow_id is not None or revision is not None:
                raise ValueError('workflow_id og revision skal sættes samlet.')
            return {'id': params['id'], 'workflow': None}
        identifier(workflow_id)
        if type(revision) is not int or revision < 1:
            raise ValueError('Workflowrevisionen skal være positiv.')
        workflow = self._get(db, 'workflow', workflow_id)
        if workflow.get('kind') != '3d_print':
            raise ValueError('Printeren kræver et 3D-printworkflow.')
        row = db.execute('SELECT body FROM workflow_versions WHERE id=? AND revision=?',
                         (workflow_id, revision)).fetchone()
        if row is None:
            raise ValueError('Den publicerede workflowrevision findes ikke.')
        return {'id': params['id'], 'workflow': {'id': workflow_id, 'revision': revision,
                'definition': json.loads(row['body'])}}

    def _laser_workflow(self, db, params):
        fields(params, ('id', 'workflow_id', 'revision'))
        text(params['id'], 40)
        workflow_id, revision = params['workflow_id'], params['revision']
        if workflow_id is None or revision is None:
            if workflow_id is not None or revision is not None: raise ValueError('workflow_id og revision skal sættes samlet.')
            return {'id': params['id'], 'workflow': None}
        identifier(workflow_id)
        if type(revision) is not int or revision < 1: raise ValueError('Workflowrevisionen skal være positiv.')
        workflow = self._get(db, 'workflow', workflow_id)
        if workflow.get('kind') != 'laser': raise ValueError('Laserstationen kræver et laserworkflow.')
        row = db.execute('SELECT body FROM workflow_versions WHERE id=? AND revision=?', (workflow_id, revision)).fetchone()
        if row is None: raise ValueError('Den publicerede workflowrevision findes ikke.')
        return {'id': params['id'], 'workflow': {'id': workflow_id, 'revision': revision, 'definition': json.loads(row['body'])}}

    @staticmethod
    def _validate_laser_command(action, params, expected):
        if action in ('laser.station.create', 'laser.station.update'):
            fields(params, ('id', 'name', 'location', 'model', 'enabled'))
            text(params['id'], 40); text(params['name'], 100); text(params['location'], 100, empty=True); text(params['model'], 40)
            if type(params['enabled']) is not bool: raise ValueError('enabled skal være true eller false.')
            if action.endswith('create') and expected != 0: raise Conflict('En ny laserstation kræver expected_version 0.')
            if action.endswith('update') and expected < 1: raise Conflict('Laserstationens version er ugyldig.')
        elif action == 'laser.job.bind':
            fields(params, ('id', 'station_id')); text(params['id'], 100); text(params['station_id'], 40); 
            if expected != 0: raise Conflict('Et nyt laserjob kræver expected_version 0.')
        elif action == 'laser.workflow.assign':
            pass
        elif action in ('laser.job.advance', 'laser.job.finish', 'laser.job.cancel'):
            fields(params, ('id', 'step_id', 'confirmed')) if action.endswith('advance') else fields(params, ('id', 'confirmed'))
            text(params['id'], 100)
            if action.endswith('advance'): text(params['step_id'], 40)
            if params['confirmed'] is not True or expected < 1: raise ValueError('Laserjobbet kræver bekræftelse og en gyldig version.')

    def _apply(self, db, actor, action, params, expected):
        kind, operation = action.split('.')
        creates = operation == 'create'
        if creates:
            if expected != 0:
                raise Conflict('Et nyt objekt kræver expected_version 0.')
            value = {'id': uuid.uuid4().hex, 'version': 1}
        else:
            if not isinstance(params, dict):
                raise ValueError('params skal være et objekt.')
            value = self._get(db, kind, params.get('id'))
            if value['version'] != expected:
                raise Conflict('Objektet er ændret. Hent den aktuelle version igen.')
            value['version'] += 1
        if kind == 'machine':
            fields(params, ('name', 'location', 'enabled', 'workflow_id') + (() if creates else ('id',)))
            if type(params['enabled']) is not bool:
                raise ValueError('enabled skal være true eller false.')
            workflow_id = params['workflow_id']
            if workflow_id is not None:
                workflow = self._get(db, 'workflow', workflow_id)
                if workflow['kind'] != 'manual':
                    raise ValueError('En manuel station kræver et manuelt workflow.')
            name, location = text(params['name']), text(params['location'], empty=True)
            for row in db.execute("SELECT body FROM objects WHERE type='machine' AND id!=?", (value['id'],)):
                other = json.loads(row['body'])
                if (other['name'].casefold(), other['location'].casefold()) == (name.casefold(), location.casefold()):
                    raise Conflict('Stationens navn og placering er allerede registreret.')
            value.update(name=name, location=location,
                         enabled=params['enabled'], workflow_id=workflow_id, kind='manual', adapter='manual')
        elif kind == 'workflow':
            if operation in ('create', 'edit'):
                fields(params, ('definition',) + (() if creates else ('id',)))
                definition = validate_definition(params['definition'])
                if not creates and definition['kind'] != value['kind']:
                    raise ValueError('Workflowtypen kan ikke ændres; opret en kopi.')
                value.update(name=definition['name'], kind=definition['kind'], draft=definition)
                if creates:
                    value['active_revision'] = None
            elif operation == 'publish':
                fields(params, ('id',))
                definition = validate_definition(value['draft'])
                revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM workflow_versions WHERE id=?', (value['id'],)).fetchone()[0]
                db.execute('INSERT INTO workflow_versions VALUES(?,?,?)', (value['id'], revision, encode(definition)))
                value['active_revision'] = revision
            else:
                fields(params, ('id', 'revision'))
                revision = params['revision']
                if type(revision) is not int or not db.execute('SELECT 1 FROM workflow_versions WHERE id=? AND revision=?', (value['id'], revision)).fetchone():
                    raise ValueError('Workflowversionen findes ikke.')
                value['active_revision'] = revision
        elif kind == 'job':
            if creates:
                fields(params, ('machine_id', 'title'))
                machine = self._get(db, 'machine', params['machine_id'])
                if not machine['enabled'] or machine['workflow_id'] is None:
                    raise ValueError('Stationen skal være aktiv og have et workflow.')
                workflow = self._get(db, 'workflow', machine['workflow_id'])
                row = db.execute('SELECT body FROM workflow_versions WHERE id=? AND revision=?',
                                 (workflow['id'], workflow['active_revision'])).fetchone()
                if row is None:
                    raise ValueError('Workflowet har ingen aktiv version.')
                value.update(title=text(params['title']), machine_id=machine['id'], workflow_id=workflow['id'],
                             workflow_revision=workflow['active_revision'], definition=json.loads(row['body']),
                             step_index=1, status='active', created_at=self.clock())
            else:
                fields(params, ('id', 'step_id', 'confirmed'))
                if value['status'] != 'active':
                    raise Conflict('Opgaven er allerede afsluttet.')
                step = value['definition']['steps'][value['step_index']]
                if params['step_id'] != step['id']:
                    raise Conflict('Trinnet er ændret. Hent opgaven igen.')
                if step['role'] == 'teacher' and actor.get('role') != 'teacher':
                    raise PermissionError('Dette trin kræver lærerens egen bekræftelse.')
                if params['confirmed'] is not True:
                    raise ValueError('Bekræft at det aktuelle trin er udført.')
                value['step_index'] += 1
                if value['step_index'] == len(value['definition']['steps']):
                    value['status'] = 'done'
        self._put(db, kind, value)
        return value
