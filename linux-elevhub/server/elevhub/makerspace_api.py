"""Teacher-only facade for Makerværkstedet, independent of pupil clients.

Configuration and workflow approvals use the existing printer and laser queues.
Physical observations remain separate; laser commands never start the machine.
"""
import ipaddress
import hashlib
import re
import sqlite3
import ssl

from makerspace_adapters import printer_machines
from makerspace_printers import PrinterAdministration
from makerspace_laser_admin import LaserAdministration
from makerspace_laser import LaserSnapshots
from makerspace_store import ACTIONS, Conflict, MakerspaceStore, fields
from makerspace_workflows import WorkflowValidationError, templates, validate_definition

SCOPES = ['makerspace:read', 'machines:write', 'workflows:write', 'jobs:write', 'queues:write', 'audit:read']
CREDIT = 'Kreditering: Klostermarksskolen.'

# Data, not executable expressions. Shared by the teacher-panel contract.
PARAMETERS = {
    'machine.create': ['name', 'location', 'enabled', 'workflow_id'],
    'machine.update': ['id', 'name', 'location', 'enabled', 'workflow_id'],
    'workflow.create': ['definition'], 'workflow.edit': ['id', 'definition'],
    'workflow.publish': ['id'], 'workflow.rollback': ['id', 'revision'],
    'job.create': ['machine_id', 'title'], 'job.advance': ['id', 'step_id', 'confirmed'],
    'printer.create': ['name', 'location', 'model', 'host', 'serial', 'access_code', 'enabled', 'bed_type'],
    'printer.update': ['id', 'name', 'location', 'model', 'host', 'serial', 'access_code', 'enabled', 'bed_type'],
    'printer.workflow.assign': ['id', 'workflow_id', 'revision'],
    'printer.job.advance': ['id', 'step_id', 'confirmed'],
    'printer.queue.pause': ['id', 'paused'],
    'printer.certificate.confirm': ['id', 'channel', 'fingerprint', 'repair'],
    'printer.certificate.probe': ['id'],
    'printer.certificate.cancel': ['id', 'operation_request_id', 'operation_actor'],
    'laser.station.create': ['id', 'name', 'location', 'model', 'enabled'],
    'laser.station.update': ['id', 'name', 'location', 'model', 'enabled'],
    'laser.workflow.assign': ['id', 'workflow_id', 'revision'],
    'laser.job.bind': ['id', 'station_id'],
    'laser.job.advance': ['id', 'step_id', 'confirmed'],
    'laser.job.finish': ['id', 'confirmed'],
    'laser.job.cancel': ['id', 'confirmed'],
}


class MakerspaceService:
    def __init__(self, path, overview=None, printer_administration=None, laser_stations=None, laser_administration=None):
        self.store = MakerspaceStore(path)
        self.overview = overview or (lambda: {})
        self.printers = printer_administration or PrinterAdministration()
        self.lasers = laser_stations or LaserSnapshots([])
        self.laser_admin = laser_administration or LaserAdministration()

    def execute(self, actor, envelope):
        action = envelope.get('action') if isinstance(envelope, dict) else ''
        external = self.laser_admin if isinstance(action, str) and action.startswith('laser.') else self.printers
        return self.store.execute(actor, envelope, external=external)

    @staticmethod
    def require(actor, scope):
        if (not isinstance(actor, dict) or actor.get('role') != 'teacher'
                or not isinstance(actor.get('id'), str)
                or not re.fullmatch(r'teacher:[a-f0-9]{32}', actor['id'])):
            raise PermissionError('Kun lærerens kontrolpanelsession må bruge Makerværkstedet.')
        if scope not in actor['scopes']:
            raise PermissionError('Adgangen omfatter ikke denne funktion.')

    def snapshot(self, actor):
        self.require(actor, 'makerspace:read')
        result = self.store.snapshot()
        overview = self.overview()
        print_state = overview.get('printers', {'status': 'unknown'})
        laser_state = self.lasers.snapshot()
        printer_admin = self.printers.snapshot()
        laser_admin = self.laser_admin.snapshot()
        observed = printer_machines(print_state) + laser_state['items']
        inventory = [{**row, 'id': 'manual:' + row['id'], 'source_id': row['id']} for row in result['machines']]
        printer_observations = {row['id']: row for row in observed if row['adapter'] == 'bambu'}
        for printer in printer_admin['printers']:
            identifier = 'bambu:' + printer['id']
            observation = printer_observations.pop(identifier, {})
            inventory.append({**printer, **observation, 'id': identifier, 'source_id': printer['id'],
                              'status': observation.get('status', 'unknown'), 'online': observation.get('online', False)})
        inventory += list(printer_observations.values())
        laser_observations = {row['source_id']: row for row in laser_state['items']}
        for station in laser_admin['stations']:
            observation = laser_observations.pop(station['id'], {})
            inventory.append({**observation, **station,
                              'id': 'flux:' + station['id'], 'source_id': station['id'],
                              'kind': 'laser', 'adapter': 'flux', 'supported': True,
                              'status': observation.get('status', 'unknown'),
                              'online': observation.get('online', False),
                              'observed_at': observation.get('observed_at'),
                              'observed_model': observation.get('model'),
                              'capabilities': {'read': ['status', 'model', 'connection'],
                                               'write': ['configuration', 'workflow']}})
        inventory += list(laser_observations.values())
        result.update(product='Makerværkstedet', credits=CREDIT, api_version=1,
                      templates=templates(), observed_machines=observed, inventory=inventory,
                      printer_administration=printer_admin,
                      laser_administration=laser_admin,
                      sources={'printers': print_state.get('status', 'unknown'), 'laser': laser_state['status']},
                      server=overview.get('server', {}), computers=overview.get('computers', []),
                      computers_status=overview.get('computers_status', 'unknown'))
        return result

    def actions(self, actor):
        self.require(actor, 'makerspace:read')
        return {'api_version': 1, 'product': 'Makerværkstedet', 'credits': CREDIT,
                'envelope': {'action': 'action name', 'params': 'object',
                             'expected_version': '0 for creation; current object.version otherwise',
                             'request_id': '32 lowercase hexadecimal characters; retain on retry'},
                'actions': [{'action': key, 'title': value[1], 'scope': value[0],
                             'allowed': value[0] in actor['scopes'] and
                             (not key.startswith('printer.') or self.printers.backend is not None) and
                             (not key.startswith('laser.') or self.laser_admin.backend is not None), 'parameters': PARAMETERS[key]}
                            for key, value in ACTIONS.items()],
                'constraints': ['Printer- og laserworkflower styrer godkendelser; fysisk status forbliver en separat observation.',
                                'A newly registered printer must have enabled=false; activate only after setup checks.',
                                'Manual work orders retain their published workflow definition.',
                                'Queue pause holds future dispatch; it does not stop a physical print. Resuming retains all printer and workflow checks.',
                                'Teacher steps require the teacher session.'],
                'outcomes': {'400': 'invalid request', '401': 'not authenticated', '403': 'not permitted',
                             '409': 'version or request-id conflict', '503': 'temporarily unavailable'}}


def _actor(handler, service):
    # Teacher cookies must not travel unencrypted across a LAN. Agent bearer
    # credentials are intentionally no longer an authentication path.
    if not isinstance(handler.connection, ssl.SSLSocket) and not ipaddress.ip_address(handler.client_address[0]).is_loopback:
        raise PermissionError('Administration over netværket kræver HTTPS.')
    if handler.headers.get_all('Authorization', []):
        raise PermissionError('Makerværkstedet kræver lærerens kontrolpanelsession.')
    if not handler.logget_ind():
        return None
    identity = hashlib.sha256(handler.token().encode()).hexdigest()[:32]
    return {'id': 'teacher:' + identity, 'name': 'Lærer', 'role': 'teacher', 'scopes': SCOPES}


def handle(handler, path, body=None):
    """Handle only this namespace, after the host server's body/origin checks."""
    if not path.startswith('/api/makerspace/'):
        return False
    service = getattr(handler.server, 'makerspace', None)
    if service is None:
        handler.svar(503, {'error': 'Makerværkstedets administration er ikke konfigureret.'})
        return True
    try:
        actor = _actor(handler, service)
        if actor is None:
            handler.svar(401, {'error': 'Log ind på kontrolpanelet.'})
            return True
        if handler.command in ('GET', 'HEAD'):
            if path == '/api/makerspace/state':
                result = service.snapshot(actor)
            elif path == '/api/makerspace/actions':
                result = service.actions(actor)
            elif path == '/api/makerspace/audit':
                service.require(actor, 'audit:read')
                result = {'events': service.store.audit()}
            else:
                handler.svar(404, {'error': 'Ukendt makerspace-funktion.'})
                return True
        elif path == '/api/makerspace/commands':
            result = service.execute(actor, body)
        elif path == '/api/makerspace/workflows/validate':
            service.require(actor, 'workflows:write')
            fields(body, ('definition',))
            result = {'valid': True, 'definition': validate_definition(body['definition'])}
        else:
            handler.svar(404, {'error': 'Ukendt makerspace-funktion.'})
            return True
        handler.svar(200, result)
    except Conflict as error:
        handler.svar(409, {'error': str(error)})
    except PermissionError as error:
        handler.svar(403, {'error': str(error)})
    except WorkflowValidationError as error:
        handler.svar(400, {'error': str(error)})
    except (ValueError, TypeError, KeyError):
        handler.svar(400, {'error': 'Ugyldig ændring. Kontrollér felter og workflowets trin.'})
    except (sqlite3.Error, OSError):
        handler.svar(503, {'error': 'Ændringen kunne ikke bekræftes. Genbrug samme request-ID ved genforsøg.'})
    return True
