"""Small, strict request schemas for the control-plane HTTP boundary."""
import hashlib
import json
import re
import uuid


class APIError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


COMPONENTS = frozenset(('server', 'laser', 'student', 'updater'))
PHASES = frozenset(('queued', 'downloading', 'staged', 'waiting_for_idle',
                    'waiting_for_reboot', 'applying', 'verifying', 'local_complete',
                    'blocked', 'failed_rolled_back', 'recovery_required'))
HEALTH = frozenset(('healthy', 'unhealthy', 'unknown'))
TERMINAL = frozenset(('local_complete', 'failed_rolled_back', 'recovery_required'))


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        raise APIError('invalid_fields')
    return value


def identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise APIError('invalid_uuid') from None
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise APIError('invalid_sha256')
    return value


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise APIError('invalid_integer')
    return value


def text(value, maximum=128):
    if not isinstance(value, str) or not 0 < len(value) <= maximum or not value.isprintable():
        raise APIError('invalid_text')
    return value


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise APIError('duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(APIError('invalid_json_number')))
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, APIError):
            raise
        raise APIError('invalid_json') from None


def poll_request(value):
    fields(value, ('protocol_version', 'poll_id', 'instance_id', 'updater_version',
                   'os_baseline_id', 'architecture', 'supported_protocols', 'components',
                   'pending_operation', 'previous_challenge'))
    if value['protocol_version'] != 1 or type(value['protocol_version']) is not int:
        raise APIError('unsupported_protocol', 409)
    identifier(value['poll_id']); identifier(value['instance_id'])
    text(value['updater_version']); text(value['os_baseline_id']); text(value['architecture'])
    if not isinstance(value['supported_protocols'], list) or 1 not in value['supported_protocols'] or any(type(x) is not int for x in value['supported_protocols']):
        raise APIError('unsupported_protocol', 409)
    if value['previous_challenge'] is not None:
        identifier(value['previous_challenge'])
    if not isinstance(value['components'], list) or len(value['components']) > 4:
        raise APIError('invalid_components')
    seen = set()
    for item in value['components']:
        fields(item, ('component_id', 'version', 'release_manifest_sha256', 'data_schema', 'health'))
        if item['component_id'] not in COMPONENTS or item['component_id'] in seen or item['health'] not in HEALTH:
            raise APIError('invalid_components')
        seen.add(item['component_id'])
        text(item['version']); digest(item['release_manifest_sha256'])
        if not isinstance(item['data_schema'], dict):
            raise APIError('invalid_data_schema')
        for name, version in item['data_schema'].items():
            text(name); integer(version)
    pending = value['pending_operation']
    if pending is not None:
        fields(pending, ('operation_id', 'desired_revision', 'lease_epoch', 'phase'), ('control_revision',))
        identifier(pending['operation_id']); integer(pending['desired_revision'], 1); integer(pending['lease_epoch'], 1)
        if pending['phase'] not in PHASES:
            raise APIError('invalid_phase')
        if 'control_revision' in pending:
            integer(pending['control_revision'], 1)
    return value


def report_request(value):
    fields(value, ('protocol_version', 'report_id', 'operation_id', 'desired_revision',
                   'lease_epoch', 'instance_id', 'sequence', 'phase', 'reason_code',
                   'active_version', 'active_manifest_sha256', 'data_schema', 'health', 'reboot_required'))
    if type(value['protocol_version']) is not int or value['protocol_version'] != 1:
        raise APIError('unsupported_protocol', 409)
    for key in ('report_id', 'operation_id', 'instance_id'):
        identifier(value[key])
    for key in ('desired_revision', 'lease_epoch', 'sequence'):
        integer(value[key], 1)
    if value['phase'] not in PHASES or value['health'] not in HEALTH or type(value['reboot_required']) is not bool:
        raise APIError('invalid_report')
    if value['reason_code'] is not None:
        if not isinstance(value['reason_code'], str) or not re.fullmatch('[a-z][a-z0-9_]{0,79}', value['reason_code']):
            raise APIError('invalid_reason_code')
    text(value['active_version']); digest(value['active_manifest_sha256'])
    if not isinstance(value['data_schema'], dict):
        raise APIError('invalid_data_schema')
    for name, version in value['data_schema'].items():
        text(name); integer(version)
    return value
