"""Small signed-release wire contract. Cryptography is delegated to pinned minisign."""
from __future__ import annotations
import base64
from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote

REPOSITORY = 'mdkmsuv/makervaerkstedet'
ROLES = frozenset(('student', 'server', 'laser_station'))
PLATFORMS = frozenset(('windows-amd64', 'debian-amd64'))
DEBIAN_COMPONENTS = {
    'studentcore': ('makervaerkstedet-elev', 'all'),
    'scratch': ('makervaerkstedet-scratch', 'all'),
    'matchbox': ('makervaerkstedet-matchbox', 'amd64'),
    'updater': ('makervaerkstedet-updater', 'amd64'),
}
MAX_MANIFEST = 1024 * 1024
MAX_SIGNATURE = 16384
MAX_VERIFIER = 32 * 1024 * 1024
MAX_ARTIFACT = 2 * 1024**3 - 1
FIELDS = frozenset(('schema', 'repository', 'role', 'platform', 'component', 'version', 'sequence', 'artifacts'))
ARTIFACT_FIELDS = frozenset(('name', 'package', 'version', 'architecture', 'sha256', 'size'))
HEX = re.compile(r'[a-f0-9]{64}')
COMPONENT = re.compile(r'[a-z][a-z0-9_-]{0,63}')
BASENAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+~-]{0,199}')
VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9.:+_~\-]{0,127}')
PACKAGE = re.compile(r'[a-z0-9][a-z0-9+.-]{1,127}')

class ReleaseVerificationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

def require(condition, code):
    if not condition: raise ReleaseVerificationError(code)

def _duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_json_key')
        require(not key.startswith('_'), 'reserved_field')
        result[key] = value
    return result

def _basename(value):
    require(isinstance(value, str) and BASENAME.fullmatch(value) is not None
            and not value.endswith('.') and '..' not in value
            and re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', value) is None,
            'unsafe_artifact_name')

def _wire(raw, repository, role, platform, component, now):
    try:
        manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_duplicates,
                              parse_constant=lambda _: (_ for _ in ()).throw(ReleaseVerificationError('invalid_json_number')))
    except ReleaseVerificationError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ReleaseVerificationError('invalid_manifest_json') from error
    require(type(manifest) is dict and set(manifest) == FIELDS, 'invalid_manifest_fields')
    require(type(manifest['schema']) is int and manifest['schema'] == 1, 'unsupported_schema')
    require(repository == REPOSITORY and manifest['repository'] == repository, 'repository_mismatch')
    require(role in ROLES and manifest['role'] == role, 'role_mismatch')
    require(platform in PLATFORMS and manifest['platform'] == platform, 'platform_mismatch')
    require(isinstance(component, str) and COMPONENT.fullmatch(component) is not None
            and manifest['component'] == component, 'component_mismatch')
    require((role == 'student' and platform == 'debian-amd64')
            or (role in ('server', 'laser_station') and platform == 'windows-amd64'), 'role_platform_mismatch')
    require(isinstance(manifest['version'], str) and VERSION.fullmatch(manifest['version']) is not None, 'invalid_version')
    require(type(manifest['sequence']) is int and 0 < manifest['sequence'] <= 2**63 - 1, 'invalid_sequence')
    # now is retained for API compatibility; project releases do not expire.
    if role == 'student':
        require(component in DEBIAN_COMPONENTS, 'component_not_allowed')
    else:
        require(component == role, 'component_not_allowed')
    artifacts = manifest['artifacts']
    require(type(artifacts) is list and 0 < len(artifacts) <= 128, 'invalid_artifact_list')
    names = set(); packages = set()
    for artifact in artifacts:
        require(type(artifact) is dict and set(artifact) == ARTIFACT_FIELDS, 'invalid_artifact_fields')
        _basename(artifact['name']); name = artifact['name'].casefold()
        require(name not in names, 'duplicate_artifact_name'); names.add(name)
        require(isinstance(artifact['sha256'], str) and HEX.fullmatch(artifact['sha256']) is not None, 'invalid_artifact_hash')
        require(type(artifact['size']) is int and 0 < artifact['size'] <= MAX_ARTIFACT, 'invalid_artifact_size')
        require(isinstance(artifact['version'], str) and VERSION.fullmatch(artifact['version']) is not None, 'invalid_artifact_version')
        if platform == 'debian-amd64':
            require(artifact['version'] == manifest['version'], 'artifact_version_mismatch')
            require(isinstance(artifact['package'], str) and PACKAGE.fullmatch(artifact['package']) is not None, 'invalid_debian_package')
            require(artifact['architecture'] in ('all', 'amd64') and artifact['name'].endswith('.deb'), 'invalid_debian_architecture')
            require((artifact['package'], artifact['architecture']) == DEBIAN_COMPONENTS[component], 'package_component_mismatch')
            package = (artifact['package'], artifact['architecture'])
            require(package not in packages, 'duplicate_debian_package'); packages.add(package)
        else:
            require(artifact['package'] is None and artifact['architecture'] == 'amd64'
                    and artifact['name'].lower().endswith('.exe'), 'invalid_windows_artifact')
    return manifest

def _minisign(raw, signature, executable, expected_hash, public_key):
    require(isinstance(expected_hash, str) and HEX.fullmatch(expected_hash) is not None, 'verifier_pin_required')
    try:
        key = base64.b64decode(public_key, validate=True)
    except (ValueError, TypeError) as error: raise ReleaseVerificationError('invalid_public_key') from error
    require(len(key) == 42 and key[:2] == b'Ed', 'invalid_public_key')
    try:
        executable = Path(executable)
        for parent in (executable, *executable.parents):
            require(not parent.is_symlink() and not parent.is_junction(), 'linked_verifier_rejected')
        require(executable.is_file() and executable.stat().st_size <= MAX_VERIFIER, 'verifier_unavailable')
        with executable.open('rb') as stream: binary = stream.read(MAX_VERIFIER + 1)
        require(len(binary) <= MAX_VERIFIER and hashlib.sha256(binary).hexdigest() == expected_hash, 'verifier_hash_mismatch')
        # Execute an exclusive private copy of the exact pinned bytes, so replacing
        # the input path after hashing cannot substitute another CLI. A standalone
        # verifier is required; no third-party loader path/environment is inherited.
        with tempfile.TemporaryDirectory(prefix='mkv-minisign-') as directory:
            root = Path(directory); tool = root / ('minisign.exe' if os.name == 'nt' else 'minisign')
            tool.write_bytes(binary); tool.chmod(0o500)
            message = root / 'manifest.json'; message.write_bytes(raw)
            sig = root / 'manifest.minisig'; sig.write_bytes(signature)
            environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'SystemDrive', 'TEMP', 'TMP') if key in os.environ}
            environment['PATH'] = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32') if os.name == 'nt' else '/usr/bin:/bin'
            result = subprocess.run([str(tool), '-V', '-m', str(message), '-x', str(sig), '-P', public_key],
                cwd=root, env=environment, shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
            require(result.returncode == 0, 'invalid_signature')
    except ReleaseVerificationError: raise
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseVerificationError('verifier_unavailable') from error

def verify_manifest(raw: bytes, signature: bytes, *, minisign_exe: Path, minisign_sha256: str,
                    public_key: str, repository: str, role: str, platform: str,
                    component: str, now: datetime) -> dict:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_MANIFEST, 'invalid_manifest_size')
    require(type(signature) is bytes and 0 < len(signature) <= MAX_SIGNATURE, 'invalid_signature_size')
    _minisign(raw, signature, minisign_exe, minisign_sha256, public_key)
    manifest = _wire(raw, repository, role, platform, component, now)
    manifest['_manifest_sha256'] = hashlib.sha256(raw).hexdigest()
    return manifest

def release_scope(manifest: dict) -> tuple[str, str, str, str]:
    require(isinstance(manifest, dict) and isinstance(manifest.get('_manifest_sha256'), str)
            and HEX.fullmatch(manifest['_manifest_sha256']) is not None, 'verified_manifest_required')
    return tuple(manifest[key] for key in ('repository', 'role', 'platform', 'component'))

def accept_sequence(manifest: dict, highest_seen: Mapping[tuple[str, str, str, str], int], *,
                    rollback_manifest_sha256: str | None = None) -> int:
    scope = release_scope(manifest); highest = highest_seen.get(scope, 0)
    require(type(highest) is int and highest >= 0, 'invalid_high_water')
    sequence = manifest['sequence']
    require(type(sequence) is int and sequence > 0, 'invalid_sequence')
    if rollback_manifest_sha256 is not None:
        require(rollback_manifest_sha256 == manifest['_manifest_sha256'], 'rollback_hash_mismatch')
    require(sequence >= highest or rollback_manifest_sha256 == manifest['_manifest_sha256'], 'rollback_authorization_required')
    # Caller durably persists this value under release_scope(), including rollback.
    return max(sequence, highest)

def verify_artifact(path: Path, artifact: dict) -> None:
    require(type(artifact) is dict and isinstance(artifact.get('sha256'), str)
            and HEX.fullmatch(artifact['sha256']) is not None, 'invalid_artifact_hash')
    require(type(artifact.get('size')) is int and 0 < artifact['size'] <= MAX_ARTIFACT, 'invalid_artifact_size')
    try:
        path = Path(path)
        for parent in (path, *path.parents):
            require(not parent.is_symlink() and not parent.is_junction(), 'linked_artifact_rejected')
        with path.open('rb') as stream:
            observed = 0; digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                observed += len(block); require(observed <= artifact['size'], 'artifact_size_mismatch'); digest.update(block)
        require(observed == artifact['size'], 'artifact_size_mismatch')
        require(digest.hexdigest() == artifact['sha256'], 'artifact_hash_mismatch')
    except ReleaseVerificationError: raise
    except OSError as error: raise ReleaseVerificationError('artifact_unavailable') from error

def artifact_url(manifest: dict, artifact: dict) -> str:
    release_scope(manifest)
    require(manifest['repository'] == REPOSITORY and artifact in manifest['artifacts'], 'artifact_not_in_manifest')
    _basename(artifact['name'])
    return 'https://github.com/' + REPOSITORY + '/releases/download/' + quote('v' + manifest['version'], safe='') + '/' + quote(artifact['name'], safe='')
