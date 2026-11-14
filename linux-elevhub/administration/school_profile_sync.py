"""Synchronize the administrator-managed school identity over enrolled HTTPS.

The caller supplies the existing authenticated transport. This module never
constructs an origin, installs a trust key, starts a service or restarts a pupil
session. Immutable assets precede the atomic manifest publication; a durable
receipt distinguishes a retry from an unrelated operator edit after a crash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

from template_activation import ActivationError, activate, atomic, plain, protected
from student_appearance import _manifest

API = '/api/device/school-profile'
MAX_METADATA = 128 * 1024
MAX_ASSET = 4 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_ASSETS = 128
HEX = re.compile(r'[a-f0-9]{64}\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*\Z')


class ProfileError(ValueError):
    pass


def require(ok, code='school_profile_metadata_invalid'):
    if not ok:
        raise ProfileError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def integer(value, low=1, high=2**63 - 1):
    return type(value) is int and low <= value <= high


def hexadecimal(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def filename(value):
    require(type(value) is str and 0 < len(value) <= 200 and NAME.fullmatch(value)
            and all(part not in ('.', '..') for part in value.split('/')))
    return value


def strict_json(raw, *, code='school_profile_metadata_invalid'):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_METADATA, code)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, code)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: require(False, code))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProfileError(code) from error


def directory(path, *, private=False):
    """Only create descendants after checking the existing owned ancestor."""
    path = plain(path)
    missing = []
    ancestor = path
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    protected(ancestor, directory=True)
    for item in reversed(missing):
        item.mkdir(mode=0o700 if private and item == path else 0o755)
        protected(item, directory=True)
    return protected(path, directory=True, private=private)


def read_owned(path, limit, *, private=False):
    path = protected(path, private=private)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
            'school_profile_local_file_invalid')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'school_profile_local_file_invalid')
    return raw


def current_hash(path):
    path = plain(path)
    return digest(read_owned(path, MAX_METADATA)) if path.exists() else None


def metadata(raw):
    offer = strict_json(raw)
    require(type(offer) is dict and type(offer.get('schema')) is int and offer['schema'] == 1
            and type(offer.get('enabled')) is bool)
    if not offer['enabled']:
        require(set(offer) == {'schema', 'enabled'})
        return offer, None
    require(set(offer) == {'schema', 'enabled', 'revision', 'manifest', 'assets'}
            and integer(offer['revision']))
    descriptor = offer['manifest']
    require(type(descriptor) is dict and set(descriptor) == {'value', 'size', 'sha256'}
            and type(descriptor['value']) is dict and hexadecimal(descriptor['sha256'])
            and integer(descriptor['size'], high=65536))
    try:
        raw_manifest = canonical(descriptor['value'])
    except (ValueError, TypeError, RecursionError) as error:
        raise ProfileError('school_profile_metadata_invalid') from error
    require(len(raw_manifest) == descriptor['size'] and digest(raw_manifest) == descriptor['sha256'],
            'school_profile_manifest_integrity')
    manifest = descriptor['value']
    require(type(manifest.get('schemaVersion')) is int and manifest['schemaVersion'] == 1)
    require('font' not in manifest or manifest['font'] in ('standard', 'manrope', 'metrophobic'))
    style = manifest.get('style', {})
    require(type(style) is dict)
    references = {}
    def reference(name, sha256):
        filename(name)
        require(hexadecimal(sha256) and (name not in references or references[name] == sha256))
        references[name] = sha256
    for key in ('website_css', 'logo'):
        if key in style:
            reference(style[key], style.get(key + '_sha256'))
    static = style.get('static', {})
    require(type(static) is dict and len(static) <= MAX_ASSETS)
    for item in static.values():
        require(type(item) is dict and set(item) == {'path', 'sha256'})
        reference(item['path'], item['sha256'])
    assets = offer['assets']
    require(type(assets) is list and len(assets) <= MAX_ASSETS)
    names, aliases, total = set(), set(), len(raw_manifest)
    for item in assets:
        require(type(item) is dict and set(item) == {'name', 'sha256', 'size', 'path'})
        name = filename(item['name'])
        suffix = PurePosixPath(name).suffix.lower()
        require(hexadecimal(item['sha256']) and integer(item['size'], low=0, high=MAX_ASSET)
                and name in references and references[name] == item['sha256']
                and name not in names and name.casefold() not in aliases
                and item['path'] == API + '/assets/' + item['sha256'] + suffix)
        names.add(name)
        aliases.add(name.casefold())
        total += item['size']
    require(names == set(references) and total <= MAX_TOTAL)
    # A file cannot also be a directory, including case aliases on FAT exports.
    require(not any('/'.join(name.casefold().split('/')[:i]) in aliases
                    for name in names for i in range(1, len(name.split('/')))))
    return offer, raw_manifest


def ledger_value(path):
    if not plain(path).exists():
        return {'schema': 1, 'highwater': {'revision': 0, 'sha256': None}, 'applied': None, 'pending': None}
    value = strict_json(read_owned(path, MAX_METADATA, private=True), code='school_profile_ledger_invalid')
    code = 'school_profile_ledger_invalid'
    require(type(value) is dict and set(value) == {'schema', 'highwater', 'applied', 'pending'}
            and type(value['schema']) is int and value['schema'] == 1, code)
    highwater = value['highwater']
    require(type(highwater) is dict and set(highwater) == {'revision', 'sha256'}
            and integer(highwater['revision'], low=0)
            and (highwater['sha256'] is None if highwater['revision'] == 0 else hexadecimal(highwater['sha256'])), code)
    for key in ('applied', 'pending'):
        item = value[key]
        if item is None:
            continue
        fields = {'revision', 'sha256'} | ({'phase', 'old_sha256'} if key == 'pending' else set())
        require(type(item) is dict and set(item) == fields and integer(item['revision'])
                and hexadecimal(item['sha256']) and item['revision'] <= highwater['revision'], code)
        if key == 'pending':
            require(item['phase'] in ('prepared', 'published')
                    and (item['old_sha256'] is None or hexadecimal(item['old_sha256'])), code)
    return value


def verify_published(base):
    protected(base, directory=True)
    read_owned(base / 'manifest.json', MAX_METADATA)
    appearance = _manifest(base.parent)
    require(appearance is not None, 'school_profile_asset_conflict')
    for key in ('_website_css', '_logo'):
        if key in appearance:
            read_owned(appearance[key][0], MAX_ASSET)
    for item in appearance.get('_static', {}).values():
        read_owned(item[0], MAX_ASSET)


def finalize(ledger, ledger_path, base, root, activation, result):
    pending = ledger['pending']
    verify_published(base)
    pending['phase'] = 'published'
    atomic(ledger_path, canonical(ledger), 0o600)
    result.update(changed=True, revision=pending['revision'])
    try:
        activation(root)
    except Exception as error:
        raise ProfileError('school_profile_activation_failed') from error
    ledger['applied'] = {'revision': pending['revision'], 'sha256': pending['sha256']}
    ledger['pending'] = None
    atomic(ledger_path, canonical(ledger), 0o600)
    return result


def _synchronize(client, root, activation, result):
    root = protected(plain(root), directory=True)
    state = root / 'var/lib/makervaerkstedet-updater'
    ledger_path = plain(state / 'school-profile.json')
    base = plain(root / 'etc/elevhub/local-appearance')
    target = plain(base / 'manifest.json')
    ledger = ledger_value(ledger_path)
    result['revision'] = ledger['highwater']['revision']
    previous = current_hash(target)
    pending = ledger['pending']
    if pending:
        require(previous in (pending['old_sha256'], pending['sha256']), 'school_profile_conflict')
        if previous == pending['sha256']:
            return finalize(ledger, ledger_path, base, root, activation, result)
    offer, raw_manifest = metadata(client.get(API, MAX_METADATA))
    if not offer['enabled']:
        result['code'] = 'school_profile_disabled'
        return result
    revision, wanted = offer['revision'], offer['manifest']['sha256']
    result['revision'] = revision
    highwater = ledger['highwater']
    require(revision >= highwater['revision'], 'school_profile_stale')
    require(revision != highwater['revision'] or wanted == highwater['sha256'], 'school_profile_revision_changed')
    applied = ledger['applied']
    require(not applied or previous == applied['sha256'], 'school_profile_conflict')
    if applied:
        verify_published(base)
        if applied == {'revision': revision, 'sha256': wanted}:
            return result
    directory(state, private=True)
    if revision > highwater['revision']:
        ledger['highwater'] = {'revision': revision, 'sha256': wanted}
        atomic(ledger_path, canonical(ledger), 0o600)
    # Download and validate the exact canonical reader's view in an owned private
    # directory. No destination profile or asset is written during this phase.
    with tempfile.TemporaryDirectory(prefix='.school-profile-', dir=state) as temporary:
        staged = Path(temporary) / 'local-appearance'
        staged.mkdir(mode=0o700)
        for item in offer['assets']:
            raw = client.get(item['path'], max(1, item['size']))
            require(type(raw) is bytes and len(raw) == item['size'] and digest(raw) == item['sha256'],
                    'school_profile_asset_integrity')
            path = staged / item['name']
            directory(path.parent)
            atomic(path, raw, 0o600)
        atomic(staged / 'manifest.json', raw_manifest, 0o600)
        require(_manifest(Path(temporary)) is not None, 'school_profile_manifest_invalid')
        # Recheck existing destination files without replacing an immutable asset.
        for item in offer['assets']:
            path = plain(base / item['name'])
            if path.exists():
                require(digest(read_owned(path, MAX_ASSET)) == item['sha256'], 'school_profile_asset_conflict')
        require(current_hash(target) == previous, 'school_profile_conflict')
        ledger['highwater'] = {'revision': revision, 'sha256': wanted}
        ledger['pending'] = {'revision': revision, 'sha256': wanted, 'old_sha256': previous, 'phase': 'prepared'}
        atomic(ledger_path, canonical(ledger), 0o600)
        directory(base)
        for item in offer['assets']:
            path = plain(base / item['name'])
            directory(path.parent)
            if not path.exists():
                atomic(path, read_owned(staged / item['name'], MAX_ASSET), 0o644)
            else:
                require(digest(read_owned(path, MAX_ASSET)) == item['sha256'], 'school_profile_asset_conflict')
        require(current_hash(target) == previous, 'school_profile_conflict')
        atomic(target, raw_manifest, 0o644)
        return finalize(ledger, ledger_path, base, root, activation, result)


def sync(client, *, root=Path('/'), activation=None):
    """One bounded tick, returning ``{changed: bool, revision: int, code: str|None}``.

    ``client.get(path, limit)`` must use the enrolled school's authenticated TLS
    connection. Activation defaults to the existing offline template renderer;
    it runs after publication and may safely be retried after an interruption.
    Exceptions at this external boundary become stable, non-sensitive codes.
    """
    result = {'changed': False, 'revision': 0, 'code': None}
    try:
        return _synchronize(client, Path(root), activate if activation is None else activation, result)
    except (ProfileError, ActivationError) as error:
        result['code'] = str(error)
    except Exception:
        result['code'] = 'school_profile_sync_failed'
    return result
