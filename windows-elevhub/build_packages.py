"""Build separate, offline Windows pilot packages from explicit source lists."""
from __future__ import annotations
import argparse
import email.parser
import hashlib
import json
import os
import re
import stat
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'windows-elevhub'
VERSION = '0.1.0-pilot'
PYTHON_HASH = 'd297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15'
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '*.before-*', '*-before-*', 'index.before-creative.html')
ARTIFACT_MANIFEST_SCHEMA = 1
ARTIFACT_NAMES = (
    'server.tools', 'server.licenses',
    'client.scratch', 'client.datatraener', 'client.cadoodle',
    # Build inputs are directories with a manifest-bound tree hash.  Keeping
    # them in the same schema-v1 map preserves old manifest readers while
    # making the Windows-only binary/runtime inputs explicit.
    'runtime.python', 'runtime.wheels', 'client.webview2',
    'client.shell', 'client.circuitjs',
)
PRIVATE_SUFFIXES = frozenset(('.key', '.p12', '.pfx', '.kdbx'))
PRIVATE_KEY_MARKERS = (
    b'-----BEGIN PRIVATE KEY-----',
    b'-----BEGIN ENCRYPTED PRIVATE KEY-----',
    b'-----BEGIN RSA PRIVATE KEY-----',
    b'-----BEGIN EC PRIVATE KEY-----',
    b'-----BEGIN DSA PRIVATE KEY-----',
    b'-----BEGIN OPENSSH PRIVATE KEY-----',
)
HASH_RE = re.compile(r'^[0-9a-f]{64}$')
PYTHON_ARCHIVE = 'python-3.14.7-embed-amd64.zip'
# Server + laser station imports are supplied by the same explicit wheel set;
# this replaces the old builder-host installed-package lookup for both roles.
WHEEL_REQUIREMENTS = {
    # (manifest filename prefix, pinned version, native wheel required)
    'paho_mqtt-': ('2.1.0', False),
    'pillow-': ('12.3.0', True),
    'cryptography-': ('50.0.1', True),
    'cffi-': ('2.1.1', True),
    'pycparser-': ('3.0', False),
    'psutil-': ('7.2.2', True),
    'affine-': ('3.0.1', False),
    'qrcode-': ('8.2', False),
    'shapely-': ('2.1.2', True),
    'numpy-': ('2.5.3', True),
    'typing_extensions-': ('4.16.0', False),
    'attrs-': ('26.1.0', False),
    'colorama-': ('0.4.6', False),
}
SERVER_WHEEL_PREFIXES = tuple(WHEEL_REQUIREMENTS)
WEBVIEW2_FILES = (
    'lib/net462/Microsoft.Web.WebView2.Core.dll',
    'lib/net462/Microsoft.Web.WebView2.WinForms.dll',
    'runtimes/win-x64/native/WebView2Loader.dll',
    'MicrosoftEdgeWebView2Setup.exe',
    'LICENSE.txt',
)
SHELL_FILES = ('ElevhubShell.exe', 'ElevhubShell.exe.config')
CIRCUITJS_FILES = ('circuitjs.html',)
# These limits apply before any package output directory is created.  They are
# deliberately finite so a corrupt or zip-bomb input cannot turn preflight
# into an unbounded read; the real Windows runtime and wheel set are well below
# these ceilings.
MAX_ARCHIVE_FILE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_COMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
WINDOWS_DOS_DEVICES = frozenset({
    'con', 'prn', 'aux', 'nul',
    *(f'com{index}' for index in range(1, 10)),
    *(f'lpt{index}' for index in range(1, 10)),
})


# Private development features are never distributable package inputs.
PRIVATE_FEATURE_FILES = frozenset(('bed_ai.py', 'local_ai.py', 'vision.py', 'training_samples.py',
    'laser_vision.py', 'laser_vision_client.py',
    'laser_vision_server.py', 'server_ai_artifact.py', 'plate_camera_client.py',
    'camera-batch.js', 'plate-camera.js'))
MODEL_SUFFIXES = frozenset(('.onnx', '.safetensors', '.gguf', '.ggml', '.tflite', '.ckpt', '.pt'))
FEATURE_COMPONENT = re.compile(r'^(?:ai|ollama|torch|torchvision|torchaudio|transformers|diffusers|onnxruntime|rembg|transparent_background|segment_anything|llama_cpp|timm|safetensors|huggingface_hub|accelerate|tokenizers)(?:$|[-_.])', re.I)


def private_feature_path(relative, educational=False):
    path = Path(relative)
    educational = educational or 'datatraener' in {part.casefold() for part in path.parts}
    return (path.name.casefold() in PRIVATE_FEATURE_FILES
            or (not educational and path.suffix.casefold() in MODEL_SUFFIXES)
            or any(FEATURE_COMPONENT.match(part) for part in path.parts))


def validate_package_tree(root):
    for path in Path(root).rglob('*'):
        if private_feature_path(path.relative_to(root)):
            raise ValueError(f'Package contains excluded private feature: {path.relative_to(root)}')


def source_ignore(directory, names):
    return set(IGNORE(directory, names)) | {name for name in names if private_feature_path(name, educational='datatraener' in {part.casefold() for part in Path(directory).parts})}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _is_reparse(path):
    """Detect symlinks and Windows reparse points without following them."""
    path = Path(path)
    if path.is_symlink():
        return True
    if os.name != 'nt':
        return False
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _assert_no_reparse_ancestors(path):
    """Reject a source reached through a junction/reparse parent as well."""
    path = Path(path)
    current = path
    ancestors = []
    while True:
        ancestors.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for ancestor in reversed(ancestors):
        if _is_reparse(ancestor):
            raise ValueError(f'Artifactstien må ikke gå gennem symlink/junction/reparse point: {ancestor}')


def _iter_artifact_paths(source):
    """Walk without following a child symlink/junction before inspecting it."""
    pending = [Path(source)]
    found = []
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            raise ValueError(f'Artifactkilden kunne ikke læses: {current}') from error
        for entry in entries:
            path = Path(entry.path)
            found.append(path)
            if _is_reparse(path):
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as error:
                raise ValueError(f'Artifactkilden kunne ikke læses: {path}') from error
            if is_directory:
                pending.append(path)
    return found


def _find_marker(path, markers):
    """Scan in bounded chunks so large model/tool files do not fill memory."""
    if not markers:
        return None
    carry = b''
    width = max(len(marker) for marker in markers)
    with Path(path).open('rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return next((marker for marker in markers if marker in carry), None)
            data = carry + chunk
            marker = next((marker for marker in markers if marker in data), None)
            if marker is not None:
                return marker
            carry = data[-(width - 1):]


def artifact_tree_digest(source):
    """Return a deterministic SHA-256 for a public artifact directory."""
    source = Path(source)
    _assert_no_reparse_ancestors(source)
    if not source.is_dir() or _is_reparse(source):
        raise ValueError(f'Artifactkilden er ikke en almindelig mappe: {source}')
    rows = []
    seen = set()
    for path in sorted(_iter_artifact_paths(source), key=lambda item: item.relative_to(source).as_posix().casefold()):
        relative = path.relative_to(source).as_posix()
        folded = relative.casefold()
        if folded in seen:
            raise ValueError(f'Artifactkilden har kolliderende filnavne: {relative}')
        seen.add(folded)
        if _is_reparse(path):
            raise ValueError(f'Artifactkilden må ikke indeholde symlinks: {relative}')
        if path.is_dir():
            rows.append(f'D\0{relative}\n'.encode('utf-8'))
        elif path.is_file():
            rows.append(f'F\0{relative}\0{path.stat().st_size}\0{digest(path)}\n'.encode('utf-8'))
        else:
            raise ValueError(f'Artifactkilden indeholder en ukendt filtype: {relative}')
    if not rows:
        raise ValueError(f'Artifactkilden er tom: {source}')
    return hashlib.sha256(b''.join(rows)).hexdigest()


def _validate_public_artifact(name, source):
    """Reject concrete private printer/key/queue material before copying."""
    _assert_no_reparse_ancestors(source)
    for path in _iter_artifact_paths(source):
        relative = path.relative_to(source)
        if private_feature_path(relative, educational=name == 'client.datatraener'):
            raise ValueError(f'Artifact {name} contains excluded private feature: {relative}')
        parts = [part.casefold() for part in relative.parts]
        filename = path.name.casefold()
        if _is_reparse(path):
            raise ValueError(f'Artifact {name} indeholder symlink/junction/reparse point: {relative}')
        if 'skolens-printere-privat' in parts or 'skolens-printere-privat' in filename:
            raise ValueError(f'Artifact {name} indeholder privat printerdata: {relative}')
        if path.is_file() and path.suffix.casefold() in PRIVATE_SUFFIXES:
            raise ValueError(f'Artifact {name} indeholder en privat nøglefil: {relative}')
        if not path.is_file():
            continue
        if path.suffix.casefold() in {'.zip', '.whl'} and zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                if any(private_feature_path(member, educational=name == 'client.datatraener') for member in archive.namelist()):
                    raise ValueError(f'Artifact {name} contains excluded private feature in archive: {relative}')
        if 'settings' in parts and path.suffix.casefold() in {'.db', '.sqlite', '.sqlite3'}:
            raise ValueError(f'Artifact {name} indeholder privat settings-/kødatabase: {relative}')
        if filename in {'queue.db', 'queue.sqlite', 'queue.sqlite3', 'queues.db', 'queues.sqlite', 'queues.sqlite3'}:
            raise ValueError(f'Artifact {name} indeholder privat kødatabase: {relative}')
        try:
            connection_marker = _find_marker(path, (b'"access_code"',)) if filename == 'connection.json' else None
        except OSError as error:
            raise ValueError(f'Artifact {name} kunne ikke læses: {relative}') from error
        if connection_marker is not None:
            raise ValueError(f'Artifact {name} indeholder privat printer-/forbindelsesdata: {relative}')
        try:
            private_key_marker = _find_marker(path, PRIVATE_KEY_MARKERS)
        except OSError as error:
            raise ValueError(f'Artifact {name} kunne ikke læses: {relative}') from error
        if private_key_marker is not None:
            raise ValueError(f'Artifact {name} indeholder en privat nøgle: {relative}')


def _reject_duplicate_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'Duplicate JSON key: {key}')
        value[key] = item
    return value


def load_artifact_manifest(path):
    """Load and verify the explicit, versioned external artifact manifest."""
    if path is None:
        raise ValueError('Angiv --artifact-manifest; builderen bruger ingen skjulte lokale artifact-stier.')
    unresolved_manifest = Path(path).expanduser()
    _assert_no_reparse_ancestors(unresolved_manifest)
    manifest_path = unresolved_manifest.resolve()
    if not manifest_path.is_file():
        raise ValueError(f'Artifactmanifestet findes ikke: {manifest_path}')
    try:
        value = json.loads(manifest_path.read_text(encoding='utf-8'), object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f'Artifactmanifestet kunne ikke læses: {manifest_path}') from error
    if (not isinstance(value, dict)
            or type(value.get('schemaVersion')) is not int
            or value.get('schemaVersion') != ARTIFACT_MANIFEST_SCHEMA):
        raise ValueError('Artifactmanifestet skal have schemaVersion=1.')
    artifacts = value.get('artifacts')
    if not isinstance(artifacts, dict):
        raise ValueError('Artifactmanifestet mangler objektet artifacts.')
    unknown = set(artifacts) - set(ARTIFACT_NAMES)
    if unknown:
        raise ValueError(f'Artifactmanifestet indeholder ukendte artefakter: {sorted(unknown)}')
    result = {'manifest_path': manifest_path, 'artifacts': {}}
    for name, entry in artifacts.items():
        if not isinstance(entry, dict) or set(entry) != {'path', 'version', 'sha256'}:
            raise ValueError(f'Artifact {name} skal have præcis path, version og sha256.')
        raw_path, version, expected = entry['path'], entry['version'], entry['sha256']
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f'Artifact {name} har ingen gyldig path.')
        if not isinstance(version, str) or not version.strip() or len(version) > 120:
            raise ValueError(f'Artifact {name} har ingen gyldig version.')
        if not isinstance(expected, str) or not HASH_RE.fullmatch(expected.lower()):
            raise ValueError(f'Artifact {name} har ikke en gyldig SHA-256.')
        unresolved = manifest_path.parent / raw_path if not Path(raw_path).is_absolute() else Path(raw_path)
        _assert_no_reparse_ancestors(unresolved)
        if _is_reparse(unresolved):
            raise ValueError(f'Artifact {name} må ikke pege via symlink: {unresolved}')
        source = unresolved.resolve()
        _assert_no_reparse_ancestors(source)
        if not source.is_dir() or _is_reparse(source):
            raise ValueError(f'Artifact {name} er ikke en eksisterende mappe: {source}')
        if manifest_path.is_relative_to(source):
            raise ValueError(f'Artifactmanifestet må ikke ligge inde i artifact {name}.')
        _validate_public_artifact(name, source)
        actual = artifact_tree_digest(source)
        if actual != expected.lower():
            raise ValueError(f'Artifact {name} matcher ikke SHA-256: forventede {expected.lower()}, fik {actual}.')
        result['artifacts'][name] = {'path': source, 'version': version, 'sha256': actual}
    return result


def require_artifacts(manifest, names, output=None):
    if manifest is None:
        raise ValueError('Artifactmanifest mangler; brug --artifact-manifest med eksplicitte kilder.')
    missing = [name for name in names if name not in manifest['artifacts']]
    if missing:
        raise ValueError(f'Artifactmanifestet mangler: {", ".join(missing)}')
    if output is not None:
        output = Path(output).resolve()
        for name, entry in manifest['artifacts'].items():
            source = entry['path']
            if source == output or output.is_relative_to(source) or source.is_relative_to(output):
                raise ValueError(f'Artifact {name} ligger i build-outputtet; vælg adskilte mapper.')
    return {name: manifest['artifacts'][name] for name in names}


def _required_file(root, relative, label):
    """Return an ordinary input file and fail before a build directory exists."""
    root = Path(root)
    path = root / relative
    _assert_no_reparse_ancestors(path)
    if not path.is_file() or _is_reparse(path):
        raise ValueError(f'{label} mangler den bundne fil: {relative}')
    return path


def _single_wheel(root, prefix):
    matches = sorted(path for path in Path(root).glob('*.whl')
                     if path.name.casefold().startswith(prefix.casefold()))
    if len(matches) != 1:
        raise ValueError(f'runtime.wheels skal indeholde præcis én wheel med præfiks {prefix}')
    wheel = _required_file(root, matches[0].name, 'runtime.wheels')
    expected_version, native = WHEEL_REQUIREMENTS[prefix]
    stem = wheel.name[:-4]
    try:
        distribution_and_version, python_tag, abi_tag, platform_tag = stem.rsplit('-', 3)
        distribution_name, version = distribution_and_version.rsplit('-', 1)
    except ValueError as error:
        raise ValueError(f'runtime.wheels har et ugyldigt wheel-navn: {wheel.name}') from error
    expected_distribution = prefix[:-1].casefold().replace('-', '_')
    if distribution_name.casefold().replace('-', '_') != expected_distribution:
        raise ValueError(f'runtime.wheels har forkert distribution i {wheel.name}')
    if version != expected_version:
        raise ValueError(f'runtime.wheels {wheel.name} har version {version}, forventede {expected_version}')
    if native:
        if platform_tag.casefold() != 'win_amd64' or not python_tag.casefold().startswith('cp'):
            raise ValueError(f'runtime.wheels {wheel.name} skal være cp3xx/Windows win_amd64')
        try:
            python_version = int(python_tag.casefold()[2:])
        except ValueError as error:
            raise ValueError(f'runtime.wheels {wheel.name} har en ugyldig Python-tag') from error
        if abi_tag.casefold() == 'abi3':
            if python_version < 30 or python_version > 314:
                raise ValueError(f'runtime.wheels {wheel.name} skal bruge en Python 3 abi3-tag til og med 3.14')
        elif (python_tag.casefold(), abi_tag.casefold()) != ('cp314', 'cp314'):
            raise ValueError(f'runtime.wheels {wheel.name} skal være cp314-cp314 eller kompatibel abi3')
    elif (python_tag.casefold(), abi_tag.casefold(), platform_tag.casefold()) not in {
            ('py3', 'none', 'any'), ('py2.py3', 'none', 'any')}:
        raise ValueError(f'runtime.wheels {wheel.name} skal være py3-none-any eller py2.py3-none-any')
    _validate_wheel_metadata(wheel, prefix, expected_version, native)
    return wheel


def _validate_wheel_metadata(wheel, prefix, expected_version, native):
    """Check METADATA and WHEEL declarations, not only the filename."""
    try:
        with zipfile.ZipFile(wheel) as package:
            entries = _zip_members(package, f'runtime.wheels/{wheel.name}')
            metadata = []
            wheel_metadata = []
            for info, key, directory in entries:
                if directory:
                    continue
                if key.endswith('.dist-info/metadata'):
                    metadata.append(info)
                elif key.endswith('.dist-info/wheel'):
                    wheel_metadata.append(info)
            if len(metadata) != 1 or len(wheel_metadata) != 1:
                raise ValueError(f'runtime.wheels/{wheel.name} mangler entydig METADATA/WHEEL')
            def read_text(info):
                with package.open(info) as stream:
                    data = stream.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                if len(data) > MAX_ARCHIVE_MEMBER_BYTES:
                    raise ValueError(f'runtime.wheels/{wheel.name} har for stor METADATA/WHEEL')
                return data.decode('utf-8')
            metadata_text = read_text(metadata[0])
            wheel_text = read_text(wheel_metadata[0])
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, ValueError) and str(error).startswith('runtime.wheels/'):
            raise
        raise ValueError(f'runtime.wheels/{wheel.name} har ugyldige METADATA/WHEEL-filer') from error
    message = email.parser.Parser().parsestr(metadata_text)
    name = message.get('Name')
    version = message.get('Version')
    expected_name = prefix[:-1].replace('_', '-')
    if not name or re.sub(r'[-_.]+', '-', name).casefold() != re.sub(r'[-_.]+', '-', expected_name).casefold():
        raise ValueError(f'runtime.wheels/{wheel.name} har forkert METADATA Name')
    if version != expected_version:
        raise ValueError(f'runtime.wheels/{wheel.name} har forkert METADATA Version')
    tags = [line.split(':', 1)[1].strip().casefold()
            for line in wheel_text.splitlines()
            if line.casefold().startswith('tag:') and ':' in line]
    if not tags:
        raise ValueError(f'runtime.wheels/{wheel.name} mangler WHEEL Tag')
    pure_python3 = False
    for tag in tags:
        try:
            py_tag, abi_tag, platform_tag = tag.split('-')
        except ValueError as error:
            raise ValueError(f'runtime.wheels/{wheel.name} har ugyldig WHEEL Tag') from error
        if native:
            if platform_tag != 'win_amd64' or not py_tag.startswith('cp'):
                raise ValueError(f'runtime.wheels/{wheel.name} har inkompatibel WHEEL Tag')
            try:
                py_version = int(py_tag[2:])
            except ValueError as error:
                raise ValueError(f'runtime.wheels/{wheel.name} har ugyldig WHEEL Python-tag') from error
            if abi_tag == 'abi3':
                if py_version < 30 or py_version > 314:
                    raise ValueError(f'runtime.wheels/{wheel.name} skal bruge en Python 3 abi3-tag til og med 3.14')
            elif (py_tag, abi_tag) != ('cp314', 'cp314'):
                raise ValueError(f'runtime.wheels/{wheel.name} har inkompatibel WHEEL ABI-tag')
        else:
            if (py_tag, abi_tag, platform_tag) not in {
                    ('py2', 'none', 'any'), ('py3', 'none', 'any'), ('py2.py3', 'none', 'any')}:
                raise ValueError(f'runtime.wheels/{wheel.name} har inkompatibel WHEEL Tag')
            pure_python3 |= 'py3' in py_tag.split('.')
    if not native and not pure_python3:
        raise ValueError(f'runtime.wheels/{wheel.name} har inkompatibel WHEEL Tag; Python 3 kræves')


def _zip_member_key(name, label):
    """Return a collision-safe Windows path key for one ZIP member."""
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError(f'{label} har et ugyldigt ZIP-navn: {name!r}')
    directory = name.endswith('/')
    raw = name[:-1] if directory else name
    if not raw or raw.startswith('/') or re.match(r'^[A-Za-z]:', raw):
        raise ValueError(f'{label} har en absolut ZIP-sti: {name!r}')
    pieces = raw.split('/')
    if any(not piece or piece in {'.', '..'} for piece in pieces):
        raise ValueError(f'{label} har traversal/empty ZIP-sti: {name!r}')
    invalid = set('<>:"|?*')
    for piece in pieces:
        if any(ord(char) < 32 or char in invalid for char in piece):
            raise ValueError(f'{label} har ulovligt Windows-tegn i ZIP-stien: {name!r}')
        if piece.endswith((' ', '.')):
            raise ValueError(f'{label} har et tvetydigt Windows-filnavn: {name!r}')
        if piece.split('.', 1)[0].casefold() in WINDOWS_DOS_DEVICES:
            raise ValueError(f'{label} har et reserveret Windows-filnavn: {name!r}')
    return '/'.join(piece.casefold() for piece in pieces), directory


def _zip_members(package, label, occupied=None):
    """Validate names, types and declared sizes for an already opened ZIP."""
    try:
        infos = package.infolist()
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ValueError(f'{label} er ikke en gyldig ZIP-fil.') from error
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(f'{label} har for mange ZIP-medlemmer.')
    occupied = set(occupied or ())
    keys = set()
    files = set()
    implied_directories = set()
    uncompressed = compressed = 0
    result = []
    for info in infos:
        key, directory = _zip_member_key(info.filename, label)
        if key in keys or key in occupied:
            raise ValueError(f'{label} har kolliderende ZIP-navn: {info.filename!r}')
        if any('/'.join(key.split('/')[:index]) in occupied
               for index in range(1, len(key.split('/')))):
            raise ValueError(f'{label} blander fil og mappe med eksisterende ZIP-navn: {info.filename!r}')
        if private_feature_path(info.filename):
            raise ValueError(f'{label} contains excluded private feature: {info.filename}')
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK or (info.create_system == 3 and mode not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise ValueError(f'{label} har et symlink/ukendt ZIP-filtype: {info.filename!r}')
        if info.file_size < 0 or info.compress_size < 0:
            raise ValueError(f'{label} har en ugyldig ZIP-størrelse: {info.filename!r}')
        if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError(f'{label} har et for stort ZIP-medlem: {info.filename!r}')
        uncompressed += info.file_size
        compressed += info.compress_size
        if uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError(f'{label} overskrider ZIP-størrelsesgrænsen.')
        if compressed > MAX_ARCHIVE_COMPRESSED_BYTES:
            raise ValueError(f'{label} overskrider ZIP-komprimeringsgrænsen.')
        if directory:
            if info.file_size:
                raise ValueError(f'{label} har en mappe med indholdsstørrelse: {info.filename!r}')
        else:
            if key in implied_directories:
                raise ValueError(f'{label} blander fil og mappe: {info.filename!r}')
            files.add(key)
        parts = key.split('/')
        for index in range(1, len(parts)):
            parent = '/'.join(parts[:index])
            if parent in files:
                raise ValueError(f'{label} blander fil og mappe: {info.filename!r}')
            implied_directories.add(parent)
        keys.add(key)
        result.append((info, key, directory))
    return result


def _hash_stream(stream):
    hasher = hashlib.sha256()
    stream.seek(0)
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            return hasher.hexdigest()
        hasher.update(chunk)


def _validate_zip_file(path, label, expected_hash=None):
    """Read and CRC-check every ZIP member before package output exists."""
    path = Path(path)
    _assert_no_reparse_ancestors(path)
    before = path.stat(follow_symlinks=False)
    if before.st_size > MAX_ARCHIVE_FILE_BYTES:
        raise ValueError(f'{label} er for stor som ZIP-input.')
    try:
        with path.open('rb') as stream:
            first_hash = _hash_stream(stream)
            if expected_hash is not None and first_hash != expected_hash:
                raise ValueError(f'{label} matcher ikke den forventede SHA-256.')
            with zipfile.ZipFile(stream) as package:
                members = _zip_members(package, label)
                for info, _key, directory in members:
                    if directory:
                        continue
                    with package.open(info) as member:
                        remaining = info.file_size
                        while remaining:
                            chunk = member.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError(f'{label} sluttede før ZIP-størrelsen.')
                            remaining -= len(chunk)
            second_hash = _hash_stream(stream)
            after = os.fstat(stream.fileno())
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ValueError(f'{label} kunne ikke valideres som ZIP-input.') from error
    if first_hash != second_hash or before.st_size != after.st_size:
        raise ValueError(f'{label} ændrede sig under preflight.')
    return first_hash


def validate_build_inputs(inputs, role):
    """Validate portable runtime/binary inputs before output creation.

    The input directories are already SHA-256 checked by load_artifact_manifest;
    this second pass checks their expected layout so a local developer cache or
    a partially copied SDK cannot silently become a package.
    """
    required = ['runtime.python']
    if role == 'server':
        required.append('runtime.wheels')
    else:
        required.extend(('client.webview2', 'client.shell', 'client.circuitjs'))
    manifest = inputs if 'artifacts' in inputs else {'artifacts': inputs}
    values = require_artifacts(manifest, required)
    # load_artifact_manifest hashes once.  Rehash the selected input trees here
    # as well, immediately before inspecting archives/files, so a changed
    # developer checkout cannot pass layout checks with stale manifest data.
    for name in required:
        entry = values[name]
        actual = artifact_tree_digest(entry['path'])
        if actual != entry['sha256']:
            raise ValueError(f'Artifact {name} ændrede sig under preflight.')
    archive = _required_file(values['runtime.python']['path'], PYTHON_ARCHIVE, 'runtime.python')
    _validate_zip_file(archive, 'runtime.python', expected_hash=PYTHON_HASH)
    if role == 'server':
        for prefix in SERVER_WHEEL_PREFIXES:
            wheel = _single_wheel(values['runtime.wheels']['path'], prefix)
            _validate_zip_file(wheel, f'runtime.wheels/{wheel.name}')
    else:
        for relative in WEBVIEW2_FILES:
            _required_file(values['client.webview2']['path'], relative, 'client.webview2')
        for relative in SHELL_FILES:
            _required_file(values['client.shell']['path'], relative, 'client.shell')
        for relative in CIRCUITJS_FILES:
            _required_file(values['client.circuitjs']['path'], relative, 'client.circuitjs')
    for name in required:
        actual = artifact_tree_digest(values[name]['path'])
        if actual != values[name]['sha256']:
            raise ValueError(f'Artifact {name} ændrede sig under preflight.')
    return values


def copy_artifact(name, entry, target):
    source = Path(entry['path'])
    target = Path(target)
    if source == target.resolve() or target.resolve().is_relative_to(source) or source.is_relative_to(target.resolve()):
        raise ValueError(f'Artifact {name} ligger i build-outputtet; vælg adskilte mapper.')
    _validate_public_artifact(name, source)
    before = artifact_tree_digest(source)
    if before != entry['sha256']:
        raise ValueError(f'Artifact {name} blev ændret efter manifestvalidering.')
    copy_tree(source, target)
    after = artifact_tree_digest(source)
    if after != before:
        raise ValueError(f'Artifact {name} blev ændret under kopiering.')


def copy_tree(source, target):
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=source_ignore)


def _copy_verified_file(source, target, label):
    """Copy one manifest-bound binary while checking its source is stable."""
    source = Path(source)
    target = Path(target)
    _assert_no_reparse_ancestors(source)
    _assert_no_reparse_ancestors(target.parent)
    if not source.is_file() or _is_reparse(source):
        raise ValueError(f'{label} mangler eller er ikke en almindelig fil.')
    if _is_reparse(target):
        raise ValueError(f'{label} mål er en reparse/symlink.')
    if target.exists():
        raise ValueError(f'{label} målfilen findes allerede; vælg en frisk outputmappe.')
    before = source.stat(follow_symlinks=False)
    hasher = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open('rb') as source_stream, target.open('wb') as destination:
            while True:
                chunk = source_stream.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                destination.write(chunk)
        after = source.stat(follow_symlinks=False)
        if (before.st_size, before.st_mtime_ns, getattr(before, 'st_ino', 0)) != (
                after.st_size, after.st_mtime_ns, getattr(after, 'st_ino', 0)):
            raise ValueError(f'{label} ændrede sig under kopiering.')
        if digest(target) != hasher.hexdigest():
            raise ValueError(f'{label} mål matcher ikke kilden.')
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def _safe_extract(package, target, label='archive', occupied=None):
    """Extract a verified archive without aliases, overwrites or traversal."""
    target = Path(target).resolve()
    entries = _zip_members(package, label, occupied=occupied)
    occupied = occupied if occupied is not None else set()
    for member, key, directory in entries:
        name = member.filename[:-1] if directory else member.filename
        candidate = (target / name).resolve()
        if candidate != target and not candidate.is_relative_to(target):
            raise ValueError(f'{label} går uden for runtime-mappen: {member.filename}')
        _assert_no_reparse_ancestors(candidate.parent)
        if directory:
            if candidate.exists() and not candidate.is_dir():
                raise ValueError(f'{label} vil overskrive en fil: {member.filename}')
            candidate.mkdir(parents=True, exist_ok=True)
            continue
        if candidate.exists() or _is_reparse(candidate):
            raise ValueError(f'{label} vil overskrive en eksisterende fil: {member.filename}')
        candidate.parent.mkdir(parents=True, exist_ok=True)
        with package.open(member) as source, candidate.open('xb') as destination:
            remaining = member.file_size
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError(f'{label} sluttede før ZIP-størrelsen: {member.filename}')
                destination.write(chunk)
                remaining -= len(chunk)
        occupied.add(key)


def runtime(target, server=False, inputs=None):
    if inputs is None:
        raise ValueError('Windows runtime kræver manifest-bundne runtime.python/runtime.wheels inputs.')
    target = Path(target)
    _assert_no_reparse_ancestors(target)
    if target.exists() and not target.is_dir():
        raise ValueError(f'Runtime-outputtet er ikke en mappe: {target}')
    required = ['runtime.python'] + (['runtime.wheels'] if server else [])
    for name in required:
        actual = artifact_tree_digest(inputs[name]['path'])
        if actual != inputs[name]['sha256']:
            raise ValueError(f'Artifact {name} ændrede sig før runtime-ekstraktion.')
    archive = _required_file(inputs['runtime.python']['path'], PYTHON_ARCHIVE, 'runtime.python')
    _validate_zip_file(archive, 'runtime.python', expected_hash=PYTHON_HASH)
    wheels = []
    if server:
        wheel_root = inputs['runtime.wheels']['path']
        for prefix in SERVER_WHEEL_PREFIXES:
            wheel = _single_wheel(wheel_root, prefix)
            _validate_zip_file(wheel, f'runtime.wheels/{wheel.name}')
            wheels.append(wheel)
    runtime_root = Path(target) / 'runtime'
    if runtime_root.exists() or _is_reparse(runtime_root):
        raise ValueError('Eksisterende runtime-mappe afvises; vælg en frisk outputmappe.')
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_occupied = set()
    with zipfile.ZipFile(archive) as package:
        _safe_extract(package, runtime_root, label='runtime.python', occupied=runtime_occupied)
    (target / 'runtime/python314._pth').write_text(
        'python314.zip\n.\n..\n../hub\n../server\n../client/agent\n../projekter\nLib/site-packages\n', encoding='ascii')
    site = target / 'runtime/Lib/site-packages'
    if server:
        site.mkdir(parents=True, exist_ok=True)
        prefix = 'lib/site-packages/'
        occupied = {key[len(prefix):] for key in runtime_occupied if key.startswith(prefix)}
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as package:
                _safe_extract(package, site, label=f'runtime.wheels/{wheel.name}', occupied=occupied)
    for name in required:
        actual = artifact_tree_digest(inputs[name]['path'])
        if actual != inputs[name]['sha256']:
            raise ValueError(f'Artifact {name} ændrede sig under runtime-ekstraktion.')


def build_server(target, reuse_print_backend=False, artifact_manifest=None):
    # Direct CLI execution needs the project's helper/documentation modules.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    artifact_manifest = load_artifact_manifest(artifact_manifest)
    required = ['server.licenses'] if reuse_print_backend else ['server.tools', 'server.licenses']
    artifacts = require_artifacts(artifact_manifest, required, target)
    inputs = require_artifacts(artifact_manifest, ('runtime.python', 'runtime.wheels'), target)
    validate_build_inputs(artifact_manifest, 'server')
    runtime(target, server=True, inputs=inputs)
    for folder in ('app', 'web'):
        copy_tree(ROOT / folder, target / folder)
    # The ABox adapter imports this vendored source at runtime. Include the
    # corresponding source and licence with the Windows distribution as well.
    copy_tree(ROOT / 'third_party/boxes.py/boxes', target / 'third_party/boxes.py/boxes')
    shutil.copy2(ROOT / 'third_party/boxes.py/LICENSE.txt', target / 'third_party/boxes.py/LICENSE.txt')
    shutil.copy2(ROOT / 'third_party/BOXES-PY-SOURCE.md', target / 'third_party/BOXES-PY-SOURCE.md')
    copy_tree(ROOT / 'linux-elevhub/server/elevhub', target / 'hub')
    shutil.copy2(ROOT / 'linux-elevhub/LICENSE', target / 'LICENSE')
    shutil.copy2(ROOT / 'CREDITS.md', target / 'CREDITS.md')
    copy_tree(SOURCE / 'server', target / 'server')
    shutil.copy2(SOURCE / 'server/hub/konfiguration.windows.json', target / 'hub/konfiguration.windows.json')
    # Explicit public/runtime allow-list from the caller's verified manifest:
    # never copy saved printer connections, school data or user queues.
    if not reuse_print_backend:
        copy_artifact('server.tools', artifacts['server.tools'], target / 'tools')
    copy_artifact('server.licenses', artifacts['server.licenses'], target / 'licenses')
    shutil.copy2(ROOT / 'linux-elevhub/LICENSE', target / 'licenses/Elevhub-LICENSE.txt')
    from scripts.package_documentation import copy_documentation
    copy_documentation(target)
    backend_artifact = artifacts['server.licenses'] if reuse_print_backend else artifacts['server.tools']
    (target / 'printvaerksted-package.json').write_text(json.dumps({
        'app':'printvaerksted', 'version':backend_artifact['version'], 'platform':'windows-x64',
        'printer_settings_included':False}), encoding='utf-8')
    for filename in ('START.cmd', 'Fjern server.cmd', 'Tilmeld elevcomputer.cmd'):
        path = SOURCE / 'server' / filename
        if path.is_file():
            shutil.copy2(path, target / filename)
    for filename, action in (('Fjern server.cmd','remove'),('Tilmeld elevcomputer.cmd','enroll')):
        (target / filename).write_text('@echo off\nsetlocal\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0server\\launchers\\start-action.ps1" -Action ' + action + '\nset "result=%errorlevel%"\npause\nexit /b %result%\n', encoding='ascii')
    executable = SOURCE / 'server/ElevhubServerSetup.exe'
    if executable.is_file():
        shutil.copy2(executable, target / 'ElevhubServerSetup.exe')


def build_client(target, artifact_manifest=None):
    raise ValueError(
        'Windows-elevklienten er historisk og er ikke en understøttet produktplatform. '
        'Elevcomputere understøttes kun på Linux.'
    )
    artifact_manifest = load_artifact_manifest(artifact_manifest)
    artifacts = require_artifacts(artifact_manifest,
                                   ('client.scratch', 'client.datatraener', 'client.cadoodle'), target)
    inputs = require_artifacts(artifact_manifest,
                               ('runtime.python', 'client.webview2', 'client.shell', 'client.circuitjs'), target)
    validate_build_inputs(artifact_manifest, 'client')
    runtime(target, inputs=inputs)
    from scripts.package_documentation import copy_client_documentation
    copy_client_documentation(target)
    for part in ('web', 'scripts', 'agent', 'admin'):
        copy_tree(SOURCE / 'client' / part, target / 'client' / part)
    (target / 'client/native').mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / 'client/native/README.md', target / 'client/native/README.md')
    copy_tree(SOURCE / 'client/web', target / 'web')
    copy_tree(ROOT / 'linux-elevhub/projekter', target / 'projekter')
    for name in ('scratch', 'datatraener'):
        copy_artifact(f'client.{name}', artifacts[f'client.{name}'], target / 'apps' / name)
    copy_artifact('client.circuitjs', inputs['client.circuitjs'], target / 'apps' / 'kredsloeb')
    shutil.copy2(target / 'apps/kredsloeb/circuitjs.html', target / 'apps/kredsloeb/index.html')
    # The shell and WebView2 SDK are separate, manifest-bound portable inputs;
    # the builder never reaches into a developer checkout or machine cache.
    webview = inputs['client.webview2']['path']
    shell = inputs['client.shell']['path']
    for relative in WEBVIEW2_FILES[:3]:
        _copy_verified_file(webview / relative, target / Path(relative).name,
                            f'client.webview2/{relative}')
    _copy_verified_file(shell / 'ElevhubShell.exe', target / 'ElevhubShell.exe',
                        'client.shell/ElevhubShell.exe')
    _copy_verified_file(shell / 'ElevhubShell.exe.config', target / 'ElevhubShell.exe.config',
                        'client.shell/ElevhubShell.exe.config')
    copy_artifact('client.cadoodle', artifacts['client.cadoodle'], target / 'native-apps/cadoodle')
    launchers = {
        'Fjern elevinstallation.cmd': 'client\\admin\\remove-client.ps1',
        'Kontroller computer.cmd': 'client\\admin\\check-computer.ps1',
        'Registrer Word.cmd': 'client\\scripts\\register-apps.ps1',
    }
    for filename, script in launchers.items():
        (target / filename).write_text('@echo off\nsetlocal\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0' + script + '"\nset "result=%errorlevel%"\npause\nexit /b %result%\n', encoding='ascii')
    (target / 'prerequisites').mkdir(exist_ok=True)
    _copy_verified_file(webview / 'MicrosoftEdgeWebView2Setup.exe',
                        target / 'prerequisites/MicrosoftEdgeWebView2Setup.exe',
                        'client.webview2/MicrosoftEdgeWebView2Setup.exe')
    copy_tree(ROOT / 'linux-elevhub/cadoodle/kloster', target / 'licenses/CaDoodle-overlay-source')
    shutil.copy2(SOURCE / 'client/cadoodle/build.py', target / 'licenses/CaDoodle-Windows-build.py')
    shutil.copy2(ROOT / 'linux-elevhub/LICENSE', target / 'licenses/Elevhub-LICENSE')
    _copy_verified_file(webview / 'LICENSE.txt', target / 'licenses/WebView2-LICENSE.txt',
                        'client.webview2/LICENSE.txt')
    from scripts.package_documentation import render_installed_markdown
    render_installed_markdown(target)
    (target / 'START.cmd').write_text('@echo off\ncall "%~dp0client\\scripts\\start.cmd"\n', encoding='ascii')
    for name in ('runtime.python', 'client.webview2', 'client.shell', 'client.circuitjs'):
        actual = artifact_tree_digest(inputs[name]['path'])
        if actual != inputs[name]['sha256']:
            raise ValueError(f'Artifact {name} ændrede sig under klientkopiering.')


def manifest(target, role, reuse_print_backend=False, artifact_manifest=None):
    validate_package_tree(target)
    # Windows PowerShell 5.1 needs a BOM for non-ASCII Danish messages.
    for script in target.rglob('*.ps1'):
        script.write_text(script.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
    package_manifest = {
        'schemaVersion':1, 'product':'Elevhub', 'version':VERSION, 'role':role,
        'platform':'windows-x64', 'server':'192.0.2.2',
        'containsCredentials':False}
    if artifact_manifest is not None:
        verified = load_artifact_manifest(artifact_manifest)
        package_manifest['artifactManifestSha256'] = digest(verified['manifest_path'])
        package_manifest['artifactSources'] = {
            name: {'version': entry['version'], 'sha256': entry['sha256']}
            for name, entry in sorted(verified['artifacts'].items())
        }
    if reuse_print_backend:
        package_manifest['externalPrintBackendRequired'] = True
    (target / 'elevhub-package.json').write_text(json.dumps(package_manifest, indent=2), encoding='utf-8')
    files = {p.relative_to(target).as_posix():digest(p)
             for p in sorted(target.rglob('*')) if p.is_file() and p.name != 'integrity.json'}
    (target / 'integrity.json').write_text(json.dumps({'algorithm':'sha256', 'files':files}, indent=2), encoding='utf-8')
    return {'files':len(files), 'bytes':sum(p.stat().st_size for p in target.rglob('*') if p.is_file())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('server',), required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/windows-elevhub-pilot')
    parser.add_argument('--artifact-manifest', type=Path, required=True,
                        help='JSON-manifest med version og SHA-256 for eksterne public artifacts')
    parser.add_argument('--zip', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--reuse-print-backend', action='store_true',
                        help='server only: omit bundled Printvaerksted runtime assets and require an existing print backend')
    args = parser.parse_args()
    # Validate external inputs before creating even an empty output directory.
    verified_artifacts = load_artifact_manifest(args.artifact_manifest)
    if args.reuse_print_backend and args.role != 'server':
        parser.error('--reuse-print-backend kræver --role server')
    name = f'Elevhub-{args.role}-{VERSION}'
    target = args.output / name
    required = ['server.licenses'] if args.reuse_print_backend else ['server.tools', 'server.licenses']
    require_artifacts(verified_artifacts, required, target)
    validate_build_inputs(verified_artifacts, args.role)
    if target.exists() and not args.refresh:
        raise SystemExit('Choose an empty output directory to avoid mixing releases.')
    if args.refresh:
        marker = json.loads((target / 'elevhub-package.json').read_text(encoding='utf-8'))
        if marker.get('role') != args.role or marker.get('version') != VERSION:
            raise SystemExit('Refresh requires the same staged role/version.')
        if bool(marker.get('externalPrintBackendRequired', False)) != args.reuse_print_backend:
            raise SystemExit('Refresh requires the same --reuse-print-backend setting.')
        validate_package_tree(target)
    target.mkdir(parents=True, exist_ok=True)
    build_server(target, reuse_print_backend=args.reuse_print_backend,
                 artifact_manifest=args.artifact_manifest)
    info = manifest(target, args.role, reuse_print_backend=args.reuse_print_backend,
                    artifact_manifest=args.artifact_manifest)
    if args.zip:
        archive = Path(str(target) + '.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as package:
            for path in target.rglob('*'):
                if path.is_file():
                    package.write(path, name + '/' + path.relative_to(target).as_posix())
        info.update(archive=str(archive), sha256=digest(archive), zipBytes=archive.stat().st_size)
    print(json.dumps({'role':args.role, 'directory':str(target), **info}, indent=2))


if __name__ == '__main__':
    main()
