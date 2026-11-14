"""Signed, declarative app identities shared by publisher, server and updater.

Parsing grants no filesystem, process or package-manager access. Native actions
must use a verified catalog and preserve its exact artifact binding.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath,Path
import re
from urllib.parse import unquote, urlsplit

try:
    from . import simple_release
except ImportError:  # Also installed next to the standalone updater verifier.
    import importlib.util
    _release_spec=importlib.util.spec_from_file_location('_mkv_catalog_release',Path(__file__).with_name('simple_release.py'))
    simple_release=importlib.util.module_from_spec(_release_spec)
    _release_spec.loader.exec_module(simple_release)

SDK_VERSION = 1
SDK_MIN_CORE = '2.1.0~'
SUPPORTED_CAPABILITIES = frozenset(('projects-v1', 'theme-v1', 'laser-submit-v1', 'print-job-v1','svg-library-v1'))
GATEWAY_CAPABILITIES = frozenset(('laser-submit-v1','print-job-v1','svg-library-v1'))
QUEUE_CAPABILITIES = {'laser-submit': 'laser-submit-v1', 'print-job': 'print-job-v1'}
SYSTEM_PORTS = frozenset((8790, 8791))
RESERVED_IDS = frozenset(('catalog', 'internet', 'projekter', 'start'))
MAX_PROJECT_BYTES = 50 * 1024**2
ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
PACKAGE = re.compile(r'[a-z0-9][a-z0-9+.-]{1,127}\Z')
VERSION = re.compile(r'[0-9][A-Za-z0-9.+:~\-]{0,127}\Z')
CAPABILITY = re.compile(r'[a-z][a-z0-9-]{0,63}-v[1-9][0-9]{0,3}\Z')
EXTENSION = re.compile(r'\.[a-z0-9][a-z0-9.]{0,30}\Z')
APP_FIELDS = frozenset(('schema', 'id', 'navn', 'description', 'category', 'icon',
                        'version', 'package', 'architecture', 'sdk', 'min_core',
                        'start', 'offline', 'aflevering', 'kraever_internet',
                        'licens', 'project', 'capabilities'))
CATALOG_FIELDS = frozenset(('schema', 'kind', 'repository', 'role', 'platform',
                            'component', 'version', 'sequence', 'apps'))
ENTRY_FIELDS = frozenset(('descriptor', 'sequence', 'source_commit', 'artifact'))
ARTIFACT_FIELDS = frozenset(('name', 'size', 'sha256', 'url'))


class CatalogError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise CatalogError(code)


def _text(value, limit):
    return (type(value) is str and bool(value.strip()) and len(value) <= limit
            and not any(ord(char) < 32 or 0x7f <= ord(char) < 0xa0
                        or 0xd800 <= ord(char) <= 0xdfff for char in value))


def _positive(value):
    return type(value) is int and 0 < value < 2**63


def _relative(value):
    return (type(value) is str and 0 < len(value) <= 256
            and re.fullmatch(r'[A-Za-z0-9_./-]+', value) is not None
            and not PurePosixPath(value).is_absolute()
            and not any(part in ('', '.', '..') for part in value.split('/')))


def component_for_app(app_id):
    require(type(app_id) is str and ID.fullmatch(app_id) and app_id not in RESERVED_IDS,
            'invalid_app_id')
    return 'app-' + app_id


def _version_parts(value):
    require(type(value) is str and VERSION.fullmatch(value), 'invalid_app_version')
    if ':' in value:
        epoch, value = value.split(':', 1)
        require(epoch.isdecimal() and bool(value) and ':' not in value, 'invalid_app_version')
        epoch = int(epoch)
    else:
        epoch = 0
    upstream, separator, revision = value.rpartition('-')
    if not separator:
        upstream, revision = value, '0'
    require(bool(upstream) and bool(revision), 'invalid_app_version')
    return epoch, upstream, revision


def _part_compare(left, right):
    def order(char):
        if char == '~': return -1
        if not char: return 0
        return ord(char) if char.isalpha() else ord(char) + 256
    while left or right:
        while (left and not left[0].isdigit()) or (right and not right[0].isdigit()):
            a = left[0] if left and not left[0].isdigit() else ''
            b = right[0] if right and not right[0].isdigit() else ''
            if order(a) != order(b): return -1 if order(a) < order(b) else 1
            if a: left = left[1:]
            if b: right = right[1:]
        a = re.match(r'[0-9]*', left)[0]
        b = re.match(r'[0-9]*', right)[0]
        left, right = left[len(a):], right[len(b):]
        a, b = a.lstrip('0'), b.lstrip('0')
        if len(a) != len(b): return -1 if len(a) < len(b) else 1
        if a != b: return -1 if a < b else 1
    return 0


def compare_versions(left, right):
    """Debian epoch/upstream/revision ordering, usable on the Windows server."""
    a, b = _version_parts(left), _version_parts(right)
    if a[0] != b[0]: return -1 if a[0] < b[0] else 1
    return _part_compare(a[1], b[1]) or _part_compare(a[2], b[2])


def validate_app_descriptor(value):
    require(type(value) is dict and set(value) == APP_FIELDS, 'invalid_app_fields')
    require(type(value['schema']) is int and value['schema'] == 2, 'invalid_app_schema')
    component_for_app(value['id'])
    for key, limit in (('navn', 256), ('description', 2048), ('licens', 1024)):
        require(_text(value[key], limit), 'invalid_app_' + key)
    require(value['category'] in ('byg', 'kode', 'ide'), 'invalid_app_category')
    require(value['icon'] is None or _relative(value['icon']), 'invalid_app_icon')
    _version_parts(value['version']); _version_parts(value['min_core'])
    expected = 'makervaerkstedet-app-' + value['id']
    require(type(value['package']) is str and PACKAGE.fullmatch(value['package'])
            and (value['package'] == expected or
                 (value['id'] == 'scratch' and value['package'] == 'makervaerkstedet-scratch')),
            'invalid_app_package')
    require(value['architecture'] in ('all', 'amd64'), 'invalid_app_architecture')
    require(_positive(value['sdk']), 'invalid_app_sdk')
    require(type(value['offline']) is bool and type(value['kraever_internet']) is bool,
            'invalid_app_boolean')
    start = value['start']
    require(type(start) is str and len(start) <= 2048, 'invalid_app_start')
    try:
        parsed = urlsplit(start)
        port = parsed.port
    except ValueError as error:
        raise CatalogError('invalid_app_start') from error
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
            and parsed.username is None and parsed.password is None
            and type(port) is int and 1024 <= port <= 65535 and port not in SYSTEM_PORTS
            and not parsed.query and not parsed.fragment
            and (parsed.path == '/' or _relative(parsed.path.lstrip('/')))
            and '%' not in parsed.path and '//' not in parsed.path,
            'invalid_app_start')
    capabilities = value['capabilities']
    require(type(capabilities) is dict and set(capabilities) == {'required', 'optional'},
            'invalid_app_capabilities')
    seen = set()
    for group in ('required', 'optional'):
        require(type(capabilities[group]) is list and len(capabilities[group]) <= 32,
                'invalid_app_capabilities')
        for item in capabilities[group]:
            require(type(item) is str and CAPABILITY.fullmatch(item) and item not in seen,
                    'invalid_app_capability')
            seen.add(item)
    require(value['aflevering'] is None or (type(value['aflevering']) is str
            and value['aflevering'] in QUEUE_CAPABILITIES), 'invalid_app_delivery')
    if value['aflevering'] is not None:
        require(QUEUE_CAPABILITIES[value['aflevering']] in seen, 'undeclared_queue_capability')
    project = value['project']
    if project is not None:
        require(type(project) is dict and set(project) == {'kind', 'extension', 'max_bytes'},
                'invalid_app_project')
        require(type(project['kind']) is str and ID.fullmatch(project['kind'])
                and type(project['extension']) is str and EXTENSION.fullmatch(project['extension'])
                and '..' not in project['extension'] and _positive(project['max_bytes'])
                and project['max_bytes'] <= MAX_PROJECT_BYTES
                and 'projects-v1' in capabilities['required'], 'invalid_app_project')
        builtins={'scratch':'.sb3','laser':'.laser.json','folie':'.svg','3dprint':'.mkv3d','microbit':'.microbit.json'}
        reserved=set(builtins.values())|{'.json','.txt','.png','.wav','.mp3','.csv','.piskel','.zip','.stl','.hex','.projekt.svg'}
        # Namespaced JSON is a supported opaque app format. Raw .json remains
        # reserved; machine/media formats and the builtin formats cannot be
        # claimed through a longer suffix such as .cad.stl or .note.laser.json.
        require(project['kind']==value['id'] and (project['extension']==builtins[value['id']] if value['id'] in builtins
                else project['extension'] not in reserved and not any(
                    project_extensions_overlap(project['extension'],extension) for extension in reserved-{'.json'})),
                'app_project_format_conflict')
    return value


def compatibility(descriptor, *, core_version, sdk=SDK_VERSION, capabilities=SUPPORTED_CAPABILITIES):
    validate_app_descriptor(descriptor)
    reasons = []
    if descriptor['sdk'] != sdk: reasons.append('sdk_unsupported')
    if compare_versions(core_version, descriptor['min_core']) < 0 or compare_versions(core_version,SDK_MIN_CORE)<0:
        reasons.append('core_too_old')
    reasons.extend('missing_capability:' + item for item in descriptor['capabilities']['required']
                   if item not in capabilities)
    return {'compatible': not reasons, 'reasons': reasons}


def project_extensions_overlap(left,right):
    """An opaque filename must identify exactly one app, independent of order."""
    return left.endswith(right) or right.endswith(left)


def validate_catalog(value, *, repository):
    require(type(value) is dict and set(value) == CATALOG_FIELDS, 'invalid_catalog_fields')
    require(type(value['schema']) is int and value['schema'] == 2
            and value['kind'] == 'makervaerkstedet-app-catalog', 'invalid_catalog_schema')
    require(repository == simple_release.REPOSITORY and value['repository'] == repository,
            'catalog_repository_mismatch')
    require((value['role'], value['platform'], value['component']) ==
            ('student', 'debian-amd64', 'app-catalog'), 'invalid_catalog_scope')
    _version_parts(value['version'])
    require(_positive(value['sequence']), 'invalid_catalog_sequence')
    require(type(value['apps']) is list and len(value['apps']) <= 128, 'invalid_catalog_apps')
    ids, ports, packages,formats = set(), set(), set(),set()
    for entry in value['apps']:
        require(type(entry) is dict and set(entry) == ENTRY_FIELDS, 'invalid_catalog_entry')
        app = validate_app_descriptor(entry['descriptor'])
        require(_positive(entry['sequence']) and type(entry['source_commit']) is str
                and re.fullmatch(r'[0-9a-f]{40}', entry['source_commit']), 'invalid_app_provenance')
        port = urlsplit(app['start']).port
        require(app['id'] not in ids and port not in ports and app['package'] not in packages,
                'duplicate_catalog_app')
        ids.add(app['id']); ports.add(port); packages.add(app['package'])
        if app['project'] is not None:
            extension=app['project']['extension']
            require(not any(project_extensions_overlap(extension,other) for other in formats),
                    'duplicate_catalog_project_format')
            formats.add(extension)
        artifact = entry['artifact']
        require(type(artifact) is dict and set(artifact) == ARTIFACT_FIELDS, 'invalid_app_artifact')
        name = artifact['name']
        require(type(name) is str and simple_release.BASENAME.fullmatch(name)
                and name == f"{app['package']}_{app['version']}_{app['architecture']}.deb",
                'invalid_app_artifact_name')
        require(type(artifact['sha256']) is str and simple_release.HEX.fullmatch(artifact['sha256'])
                and type(artifact['size']) is int and 0 < artifact['size'] <= simple_release.MAX_ARTIFACT,
                'invalid_app_artifact_identity')
        url = artifact['url']
        require(type(url) is str and len(url) <= 4096, 'invalid_app_artifact_url')
        parsed = urlsplit(url)
        prefix = '/' + repository + '/releases/download/'
        tail = parsed.path[len(prefix):].split('/')
        require(parsed.scheme == 'https' and parsed.netloc == 'github.com'
                and parsed.path.startswith(prefix) and not parsed.query and not parsed.fragment
                and len(tail) == 2 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+~-]{0,127}', tail[0])
                and unquote(parsed.path.rsplit('/', 1)[-1]) == name,
                'invalid_app_artifact_url')
    return value


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_catalog_json_key')
        result[key] = value
    return result


def verify_catalog(raw, signature, *, minisign_exe, minisign_sha256, public_key, repository):
    require(type(raw) is bytes and 0 < len(raw) <= simple_release.MAX_MANIFEST,
            'invalid_catalog_size')
    require(type(signature) is bytes and 0 < len(signature) <= simple_release.MAX_SIGNATURE,
            'invalid_catalog_signature_size')
    simple_release._minisign(raw, signature, minisign_exe, minisign_sha256, public_key)
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CatalogError('invalid_catalog_json') from error
    validate_catalog(value, repository=repository)
    value['_manifest_sha256'] = hashlib.sha256(raw).hexdigest()
    return value


def accept_catalog(catalog, highest_seen):
    """Return durable highwater state; removal never forgets prior app records."""
    digest = catalog.get('_manifest_sha256') if type(catalog) is dict else None
    require(type(digest) is str and simple_release.HEX.fullmatch(digest), 'verified_catalog_required')
    validate_catalog({key: value for key, value in catalog.items() if key != '_manifest_sha256'},
                     repository=simple_release.REPOSITORY)
    require(type(highest_seen) is dict, 'invalid_catalog_highwater')
    require(type(highest_seen.get('apps', {})) is dict, 'invalid_catalog_highwater')
    for identity, old in highest_seen.get('apps', {}).items():
        component_for_app(identity)
        require(type(old) is dict and set(old) == {'sequence', 'sha256','versions'}
                and _positive(old['sequence']) and type(old['sha256']) is str
                and simple_release.HEX.fullmatch(old['sha256']) and type(old['versions']) is dict,
                'invalid_catalog_highwater')
        for version,version_digest in old['versions'].items():
            _version_parts(version)
            require(type(version_digest) is str and simple_release.HEX.fullmatch(version_digest),'invalid_catalog_highwater')
    previous = highest_seen.get('sequence', 0)
    require(type(previous) is int and previous >= 0 and catalog['sequence'] >= previous,
            'catalog_rollback_rejected')
    if previous == catalog['sequence']:
        require(highest_seen.get('manifest_sha256') == digest, 'catalog_sequence_fork')
    apps = dict(highest_seen.get('apps', {}))
    for entry in catalog['apps']:
        identity = entry['descriptor']['id']
        entry_hash = hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False,
                                              separators=(',', ':')).encode()).hexdigest()
        old = apps.get(identity, {'sequence': 0,'versions':{}})
        require(entry['sequence'] >= old['sequence'], 'app_rollback_rejected')
        if entry['sequence'] == old['sequence']:
            require(old.get('sha256') == entry_hash, 'app_sequence_fork')
        bound={'descriptor':entry['descriptor'],'artifact':{key:entry['artifact'][key] for key in ('name','size','sha256')}}
        binding=hashlib.sha256(json.dumps(bound,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        versions=dict(old['versions']);version=entry['descriptor']['version']
        require(version not in versions or versions[version]==binding,'app_version_identity_conflict')
        versions[version]=binding
        apps[identity] = {'sequence': entry['sequence'], 'sha256': entry_hash,'versions':versions}
    return {'sequence': catalog['sequence'], 'manifest_sha256': digest, 'apps': apps}
