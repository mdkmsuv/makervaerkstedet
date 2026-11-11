"""Explicit administrator-side TLS and device enrollment helper.

No command is run implicitly. Certificate generation requires the separately
bundled ``cryptography`` dependency in the final package.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import os
import re
import stat
import tempfile
import subprocess
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit


def validate_server_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError(f'Ugyldig serveradresse: {value!r}') from error
    if not isinstance(address, ipaddress.IPv4Address) or address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError('Serverrollen skal bruge en aktiv IPv4-adresse.')
    return str(address)


def validate_server_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Serveren skal være en ren HTTPS-adresse uden sti eller login.')
    if not parsed.hostname:
        raise ValueError('Serveradressen mangler en vært.')
    host = validate_server_ip(parsed.hostname)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError('Serveradressen skal have en gyldig port.') from error
    if port is None or not 1 <= port <= 65535:
        raise ValueError('Serveradressen skal have en gyldig port.')
    return f'https://{host}:{port}'


def _load(path: Path) -> dict:
    if not path.is_file():
        return {"enheder": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("enheder", {}), dict):
        raise ValueError("Ugyldigt enheder.json-format.")
    value.setdefault("enheder", {})
    return value


def enroll(path: Path, name: str) -> tuple[str, str, str]:
    if not name or len(name) > 32 or name[0] not in 'abcdefghijklmnopqrstuvwxyz0123456789' or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
        raise ValueError("Ugyldigt elevcomputer-navn.")
    data = _load(path)
    if name in data['enheder']:
        raise ValueError('Computeren er allerede tilmeldt. Brug et nyt navn eller fjern den gamle tilmelding først.')
    token = secrets.token_urlsafe(32)
    ui_token = secrets.token_urlsafe(32)
    data["enheder"][name] = {"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                              "ui_token_sha256": hashlib.sha256(ui_token.encode()).hexdigest()}
    return json.dumps(data, ensure_ascii=False, indent=2), token, ui_token


def _restrict_private_path(path: Path) -> None:
    """Protect a private TLS file before and after its bytes are published."""
    if os.name == 'nt':
        # The production installer invokes this helper from an elevated
        # administrator console.  Refuse to publish private bytes from an
        # unelevated process; source tests inject an explicit ACL boundary.
        try:
            import ctypes
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            elevated = False
        if not elevated:
            raise PermissionError('TLS-private ACL kræver en forhøjet administratorkonsol.')
        result = subprocess.run([
            'icacls.exe', str(path), '/inheritance:r', '/grant:r',
            '*S-1-5-18:F', '*S-1-5-32-544:F'],
            capture_output=True, check=False)
        if result.returncode:
            raise OSError('TLS-private ACL kunne ikke sikres.')
    else:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    private = path.name in PRIVATE_TLS_PATHS
    if temporary.exists() and private:
        _restrict_private_path(temporary)
    with temporary.open('wb') as output:
        if private:
            _restrict_private_path(temporary)
        output.write(text.encode('utf-8'))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    if private:
        _restrict_private_path(path)


TLS_FILES = ('ca.crt', 'server.crt', 'server.key', 'enheder.json')
TLS_PRIVATE_FILE = 'ca.key'
TLS_ALL_FILES = TLS_FILES + (TLS_PRIVATE_FILE,)
TLS_JOURNAL = 'tls-initialization.json'
TLS_RENEWAL_JOURNAL = 'tls-renewal.json'
TLS_RENEWAL_RECEIPT = 'tls-renewal-receipt.json'
PRIVATE_TLS_PATHS = frozenset((TLS_JOURNAL, TLS_PRIVATE_FILE, TLS_RENEWAL_JOURNAL, TLS_RENEWAL_RECEIPT))
TLS_MAX_FILE_BYTES = 64 * 1024
TLS_MAX_REGISTRY_BYTES = 8 * 1024 * 1024
FIXED_APP = {'__init__.py', 'backup_databases.py', 'backup_guard.py', 'data_contract.py', 'database_migrations.py',
             'generation_activation.py', 'generation_host.py', 'generation_lock.py', 'generation_materialization.py',
             'generation_relocation.py', 'generation_rollback.py', 'generation_runtime.py', 'generation_selection.py',
             'generation_transaction.py', 'installation_backup.py', 'installation_update.py', 'instance.py',
             'release_compatibility.py', 'school_maintenance.py', 'school_maintenance_host.py', 'school_recovery.py', 'school_tls.py'}


def _tls_material(server_ip: str) -> dict[str, str]:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    except ImportError as error:
        raise RuntimeError('Bundled cryptography mangler i runtime.') from error
    server_ip = validate_server_ip(server_ip)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Elevhub local CA')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
          .not_valid_after(now + timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
              key_encipherment=False, data_encipherment=False, key_agreement=False,
              key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, server_ip)])
    server = (x509.CertificateBuilder().subject_name(server_name).issuer_name(ca.subject).public_key(server_key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
              .not_valid_after(now + timedelta(days=825))
              .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(server_ip)),
                  x509.IPAddress(ipaddress.ip_address('127.0.0.1')), x509.DNSName('localhost')]), critical=False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                  key_encipherment=True, data_encipherment=False, key_agreement=False,
                  key_cert_sign=False, crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
              .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
    return {
        'ca.crt': ca.public_bytes(serialization.Encoding.PEM).decode('ascii'),
        'server.crt': server.public_bytes(serialization.Encoding.PEM).decode('ascii'),
        'server.key': server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode('ascii'),
        'ca.key': ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode('ascii'),
        'enheder.json': '{"enheder": {}}\n',
    }


def _tls_digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _tls_read(path: Path, limit: int = TLS_MAX_FILE_BYTES) -> bytes:
    with path.open('rb') as source:
        value = source.read(limit + 1)
    if len(value) > limit:
        raise ValueError('TLS-filen overskrider den tilladte størrelse.')
    return value


def _tls_assert_path(path: Path) -> None:
    for cursor in (path, *path.parents):
        try:
            attributes = cursor.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(attributes.st_mode) or
                getattr(attributes, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('TLS-genoptagelse afviser omdirigerede filstier.')


def _unique_json(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Dubleret nøgle i installationsmetadata.')
        value[key] = item
    return value


def check_initial_install_tree(package: Path, runtime: Path, data: Path, *, uninstall: bool = False) -> None:
    """Prove that rollback will remove only this first install's program bytes.

    A power loss may leave a prefix of a copied file. Unknown paths, modified
    bytes, school data and later generation selections are never reclaimed.
    The supplied package has already passed the installer's full verification.
    This function still verifies every source digest against the owner anchor.
    """
    package, runtime, data = (Path(os.path.abspath(p)) for p in (package, runtime, data))
    for path in (package, runtime, data):
        _tls_assert_path(path)
    owner_path = data / 'installation-owner.json'
    _tls_assert_path(owner_path)
    owner = json.loads(_tls_read(owner_path).decode('utf-8-sig'), object_pairs_hook=_unique_json)
    if (type(owner) is not dict or type(owner.get('schemaVersion')) is not int
            or owner['schemaVersion'] != 1 or owner.get('product') != 'ElevhubServer'
            or owner.get('state') not in (('installed',) if uninstall else ('installing', 'rollbackFailed', 'rolledBack'))
            or owner.get('taskName') != 'ElevhubServer'
            or Path(owner.get('installRoot', '')) != runtime or Path(owner.get('dataRoot', '')) != data):
        raise ValueError('Oprydning kræver en ejet førstegangsinstallation.')
    installation_id = uuid.UUID(owner['ownerId']).hex
    generation = owner.get('startupProtocol') == 'generation-v1'
    if bool(owner.get('generationLayout')) != generation or owner.get('startupProtocol') not in (None, 'generation-v1'):
        raise ValueError('Installationens opstartsprotokol matcher ikke ejeren.')
    if data == runtime or data.is_relative_to(runtime) or runtime.is_relative_to(data):
        raise ValueError('Oprydning kræver adskilte program- og datamapper.')
    manifest_path = package / 'integrity.json'
    _tls_assert_path(manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != owner.get('packageManifestSha256'):
        raise ValueError('Oprydning kræver den oprindelige verificerede pakke.')
    manifest = json.loads(manifest_bytes.decode('utf-8-sig'), object_pairs_hook=_unique_json)
    files = manifest.get('files') if type(manifest) is dict else None
    if not isinstance(manifest, dict) or manifest.get('algorithm') != 'sha256' or not isinstance(files, dict) or not files:
        raise ValueError('Pakkens integritetsmanifest er ugyldigt.')
    sources = {'integrity.json': manifest_path}
    release_path = package / 'makerspace-release.json'
    if release_path.exists():
        _tls_assert_path(release_path)
        if hashlib.sha256(release_path.read_bytes()).hexdigest() != owner.get('releaseManifestSha256'):
            raise ValueError('Oprydning kræver det oprindelige generationsmanifest.')
        sources['makerspace-release.json'] = release_path
    elif generation:
        raise ValueError('Generationsmanifestet mangler; programmerne bevares.')
    for name, digest in files.items():
        parts = name.split('/') if isinstance(name, str) else []
        if (not parts or any(p in ('', '.', '..') or ':' in p or '\\' in p for p in parts)
                or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Pakkens filliste er ugyldig.')
        source = package.joinpath(*parts)
        _tls_assert_path(source)
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Den oprindelige pakke er ændret.')
        sources[name] = source
    expected = {}
    for name, source in sources.items():
        expected[('program/' if generation else '') + name] = source
        if generation and (name.split('/')[0] in ('runtime', 'server', 'deployment')
                           or name in {'app/' + module for module in FIXED_APP}):
            expected[name] = source
    metadata = {'installation.json', 'installation.json.tmp'}
    if generation:
        metadata |= {'control/generation.lock', 'control/selection-transition.json',
                     'control/active-generation.json', 'control/active-generation.json.pending'}
    directories = {str(Path(name).parent).replace('\\', '/') for name in (*expected, *metadata)}
    directories |= {str(parent).replace('\\', '/') for name in (*expected, *metadata) for parent in Path(name).parents}
    directories.discard('.')
    if not runtime.exists():
        return
    if not runtime.is_dir():
        raise ValueError('Programmappen har forkert type.')
    if uninstall and generation:
        if (not (runtime / 'control/active-generation.json').is_file()
                or (runtime / 'control/selection-transition.json').exists()
                or (runtime / 'control/active-generation.json.pending').exists()):
            raise ValueError('Generationsfjernelse kræver et fuldført første generationsvalg.')
    for folder, subfolders, names in os.walk(runtime, followlinks=False):
        for name in (*subfolders, *names):
            path = Path(folder) / name
            _tls_assert_path(path)
            relative = path.relative_to(runtime).as_posix()
            if path.is_dir():
                if relative not in directories:
                    raise ValueError('Ukendt mappe bevares: ' + relative)
                continue
            if not path.is_file():
                raise ValueError('Ukendt filtype bevares: ' + relative)
            if relative in expected:
                # Stream comparison bounds memory even for large bundled files.
                with path.open('rb') as copied, expected[relative].open('rb') as original:
                    while chunk := copied.read(1024 * 1024):
                        if chunk != original.read(len(chunk)):
                            raise ValueError('Ændret programfil bevares: ' + relative)
                    if uninstall and original.read(1):
                        raise ValueError('Ufuldstændig programfil bevares: ' + relative)
            elif relative in metadata:
                raw = _tls_read(path)
                if relative == 'control/generation.lock':
                    if raw not in (b'', b'1'):
                        raise ValueError('Ændret generationslås bevares.')
                    continue
                value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_json)
                if relative.startswith('installation.json'):
                    installation = {'data_root': str(data), 'port': owner.get('printBackendPort', 8765), 'autostart': 'Boot'}
                    package_marker = json.loads((package / 'elevhub-package.json').read_text(encoding='utf-8-sig'), object_pairs_hook=_unique_json)
                    ai_enabled = package_marker.get('localAiIncluded', True)
                    valid = (type(ai_enabled) is bool and
                             value in (installation, dict(installation, ai_enabled=ai_enabled)))
                elif relative == 'control/selection-transition.json':
                    valid = value == {'format': 1, 'operation': 'initial_install', 'installation_id': installation_id}
                else:
                    valid = (value == {'format': 1, 'installation_id': installation_id, 'revision': 1,
                                      'runtime_root': str(runtime), 'program_root': str(runtime / 'program'),
                                      'data_root': str(data), 'manifest_sha256': owner['releaseManifestSha256']})
                if not valid:
                    raise ValueError('Ændret installationsmetadata bevares: ' + relative)
            else:
                raise ValueError('Ukendt fil bevares: ' + relative)


def generation_uninstall_plan(runtime: Path, anchor_data: Path) -> dict:
    """Resolve Windows integration ownership; never authorize generation deletion.

    After updates/restores the original data root is only an installation
    anchor. Integration uses the selected owner and CA. Both owners, all
    archives and all selected/unselected data are retained during uninstall.
    """
    runtime, anchor_data = (Path(os.path.abspath(p)) for p in (runtime, anchor_data))
    def plain(path):
        _tls_assert_path(path)
        if not path.exists():
            raise ValueError('En bevaret generationssti mangler.')
        return path
    def read_json(path, limit=TLS_MAX_FILE_BYTES):
        return json.loads(_tls_read(plain(path), limit).decode('utf-8-sig'), object_pairs_hook=_unique_json)
    plain(runtime); plain(anchor_data)
    anchor_path = anchor_data / 'installation-owner.json'
    anchor = read_json(anchor_path)
    if (type(anchor) is not dict or type(anchor.get('schemaVersion')) is not int or anchor['schemaVersion'] != 1
            or anchor.get('product') != 'ElevhubServer' or anchor.get('state') not in ('installed', 'removed')
            or anchor.get('startupProtocol') != 'generation-v1' or anchor.get('generationLayout') is not True
            or anchor.get('taskName') != 'ElevhubServer' or Path(anchor.get('installRoot', '')) != runtime
            or Path(anchor.get('dataRoot', '')) != anchor_data):
        raise ValueError('Generationsfjernelse kræver den oprindelige installationsejer.')
    installation_id = uuid.UUID(anchor['ownerId']).hex
    if installation_id == '0' * 32:
        raise ValueError('Installationens ejer-id er ugyldigt.')
    control = plain(runtime / 'control')
    if (control / 'selection-transition.json').exists() or (control / 'active-generation.json.pending').exists():
        raise ValueError('En generationsændring skal afsluttes før fjernelse.')
    selected_path = plain(control / 'active-generation.json')
    raw = _tls_read(selected_path)
    selected = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_json)
    fields = {'format', 'installation_id', 'revision', 'runtime_root', 'program_root', 'data_root', 'manifest_sha256'}
    if (type(selected) is not dict or set(selected) != fields or type(selected['format']) is not int or selected['format'] != 1
            or selected['installation_id'] != installation_id or type(selected['revision']) is not int or selected['revision'] < 1
            or Path(selected['runtime_root']) != runtime or not isinstance(selected['manifest_sha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', selected['manifest_sha256'])):
        raise ValueError('Generationsvalget matcher ikke installationen.')
    program, data = (plain(Path(selected[key])) for key in ('program_root', 'data_root'))
    if (not program.is_absolute() or not data.is_absolute() or not program.is_dir() or not data.is_dir()
            or str(program).startswith(('\\\\', '//')) or str(data).startswith(('\\\\', '//'))
            or data == runtime or data.is_relative_to(runtime) or runtime.is_relative_to(data)
            or data == program or data.is_relative_to(program) or program.is_relative_to(data)):
        raise ValueError('Generationsdata skal have et kendt, adskilt lokalt layout.')
    release_path = plain(program / 'makerspace-release.json')
    if hashlib.sha256(release_path.read_bytes()).hexdigest() != selected['manifest_sha256']:
        raise ValueError('Den valgte udgivelsesidentitet er ændret.')
    owner_path = data / 'installation-owner.json'
    owner = read_json(owner_path)
    if (type(owner) is not dict or owner.get('state') not in ('installed', 'removed') or Path(owner.get('dataRoot', '')) != data
            or any(owner.get(key) != anchor.get(key) for key in
                   ('schemaVersion', 'product', 'ownerId', 'taskName', 'installRoot', 'startupProtocol', 'generationLayout',
                    'roleIp', 'roleNetwork', 'studentSubnet', 'teacherAddress', 'teacherPort', 'printBackendPort', 'designBackendPort',
                    'teacherBackendPort', 'externalBackends', 'firewallNames', 'caThumbprint', 'packageManifestSha256', 'releaseManifestSha256'))):
        raise ValueError('Den valgte installationsejer eller Windows-integration er ændret.')
    original = plain(runtime / 'program')
    for name, field in (('integrity.json', 'packageManifestSha256'), ('makerspace-release.json', 'releaseManifestSha256')):
        if hashlib.sha256(plain(original / name).read_bytes()).hexdigest() != anchor.get(field):
            raise ValueError('Den faste installations oprindelige manifest er ændret.')
    manifest = read_json(original / 'integrity.json', 16 * 1024 * 1024)
    files = manifest.get('files') if type(manifest) is dict else None
    if not isinstance(files, dict) or not files or manifest.get('algorithm') != 'sha256':
        raise ValueError('Den oprindelige programfilliste er ugyldig.')
    # Bind every fixed executable/administrative entry point to the original
    # manifest before stopping any task. Unknown files stay untouched.
    for name, digest in files.items():
        parts = name.split('/') if isinstance(name, str) else []
        if (not parts or any(p in ('', '.', '..') or ':' in p or '\\' in p for p in parts)
                or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Den oprindelige programfilliste er ugyldig.')
        fixed = (parts[0] in ('runtime', 'server', 'deployment') or name in {'app/' + p for p in FIXED_APP})
        if fixed:
            with plain(runtime.joinpath(*parts)).open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != digest:
                    raise ValueError('En fast programfil er ændret; Windows-integration bevares.')
    return {'dataRoot': str(data), 'programRoot': str(program), 'revision': selected['revision'],
            'selectionSha256': hashlib.sha256(raw).hexdigest(),
            'anchorOwnerSha256': hashlib.sha256(anchor_path.read_bytes()).hexdigest(),
            'selectedOwnerSha256': hashlib.sha256(owner_path.read_bytes()).hexdigest(),
            'retainGenerations': selected['revision'] != 1 or data != anchor_data or program != runtime / 'program'}


def _check_tls_identity(material: dict[str, str], server_ip: str) -> None:
    """Reject inconsistent legacy identities; never repair by replacing trust."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    ca = x509.load_pem_x509_certificate(material['ca.crt'].encode('ascii'))
    cert = x509.load_pem_x509_certificate(material['server.crt'].encode('ascii'))
    key = serialization.load_pem_private_key(material['server.key'].encode('ascii'), password=None)
    public_format = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca or cert.issuer != ca.subject:
        raise ValueError('TLS-identiteten matcher ikke den bevarede CA.')
    ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm)
    if TLS_PRIVATE_FILE in material:
        ca_key = serialization.load_pem_private_key(material[TLS_PRIVATE_FILE].encode('ascii'), password=None)
        if ca_key.public_key().public_bytes(*public_format) != ca.public_key().public_bytes(*public_format):
            raise ValueError('TLS-CA-nøglen matcher ikke det bevarede CA-certifikat.')
    if key.public_key().public_bytes(*public_format) != cert.public_key().public_bytes(*public_format):
        raise ValueError('TLS-nøglen matcher ikke det bevarede certifikat.')
    if ipaddress.ip_address(server_ip) not in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress):
        raise ValueError('TLS-certifikatet matcher ikke serveradressen.')
    registry = json.loads(material['enheder.json'])
    if not isinstance(registry, dict) or not isinstance(registry.get('enheder'), dict):
        raise ValueError('Ugyldigt bevaret enhedsregister.')


def _journal_tls_files(journal: dict) -> tuple[str, ...]:
    names = journal.get('sha256') if isinstance(journal, dict) else None
    if not isinstance(names, dict):
        raise ValueError('TLS-journalen mangler filintegritet.')
    keys = set(names)
    if keys == set(TLS_FILES):
        return TLS_FILES
    if keys == set(TLS_ALL_FILES):
        return TLS_ALL_FILES
    raise ValueError('TLS-journalens filliste matcher ikke protokollen.')


def _tls_material_from_files(output_dir: Path, names: tuple[str, ...]) -> dict[str, str]:
    return {name: _tls_read(output_dir / name,
                            TLS_MAX_REGISTRY_BYTES if name == 'enheder.json' else TLS_MAX_FILE_BYTES
                            ).decode('utf-8') for name in names}


def _tls_preflight(output_dir: Path, server_ip: str, owner_id: str) -> dict | None:
    """Read-only recovery check, including every file before publishing any."""
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 128:
        raise ValueError('TLS-genoptagelse kræver installationens ejer-id.')
    _tls_assert_path(output_dir)
    if not output_dir.exists():
        return None
    if not output_dir.is_dir():
        raise ValueError('TLS-mappen er ikke en mappe.')
    # Complete installations may also contain enrollment exports. Inspect only
    # paths that this provisioner reads/writes, leaving unrelated data alone.
    for name in (*TLS_ALL_FILES, TLS_JOURNAL, TLS_RENEWAL_JOURNAL, TLS_RENEWAL_RECEIPT):
        for suffix in ('', '.tmp'):
            path = output_dir / (name + suffix)
            _tls_assert_path(path)
            if path.exists() and not path.is_file():
                raise ValueError('TLS-mappen indeholder en usikker filsti.')
    journal_path = output_dir / TLS_JOURNAL
    if (output_dir / TLS_RENEWAL_JOURNAL).exists():
        raise ValueError('En afbrudt certifikatfornyelse skal genoptages eller kontrolleres særskilt.')
    # A power loss during the atomic journal write has not published identities.
    # Recover a complete temporary journal, but never invent replacements for it.
    source = journal_path if journal_path.exists() else journal_path.with_suffix('.json.tmp')
    if not source.exists():
        if not any(output_dir.iterdir()):
            return None
        if not all((output_dir / name).is_file() for name in TLS_FILES):
            raise ValueError('Ufuldstændig TLS uden ejerbundet journal; ingen data blev overskrevet.')
        names = TLS_ALL_FILES if (output_dir / TLS_PRIVATE_FILE).exists() else TLS_FILES
        _check_tls_identity(_tls_material_from_files(output_dir, names), server_ip)
        return {'state': 'legacy-complete'}
    try:
        journal = json.loads(_tls_read(source).decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A torn FIRST journal temporary file cannot have published a CA or
        # key: publication starts only after the complete journal rename.
        # Regenerate only this unexposed bundle, never a canonical identity.
        if source != journal_path and set(output_dir.iterdir()) == {source}:
            return None
        raise ValueError('TLS-journalen er beskadiget; ingen data blev overskrevet.')
    if (not isinstance(journal, dict) or type(journal.get('schemaVersion')) is not int or journal['schemaVersion'] != 1 or
            journal.get('ownerId') != owner_id or journal.get('serverIp') != server_ip or
            journal.get('state') not in ('staged', 'complete') or
            not isinstance(journal.get('sha256'), dict) or set(journal['sha256']) not in (set(TLS_FILES), set(TLS_ALL_FILES)) or
            any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in journal['sha256'].values())):
        raise ValueError('TLS-journalen matcher ikke installationen; ingen data blev overskrevet.')
    journal_files = _journal_tls_files(journal)
    staged = journal['state'] == 'staged'
    expected_fields = {'schemaVersion', 'ownerId', 'serverIp', 'state', 'sha256'} | ({'material'} if staged else set())
    if set(journal) != expected_fields:
        raise ValueError('TLS-journalens felter matcher ikke protokollen.')
    material = journal.get('material', {})
    if staged:
        if not isinstance(material, dict) or set(material) != set(journal_files):
            raise ValueError('TLS-journalen mangler oprindelige identiteter.')
        if any(not isinstance(material[name], str) or _tls_digest(material[name]) != journal['sha256'][name] for name in journal_files):
            raise ValueError('TLS-journalens integritetskontrol fejlede.')
        _check_tls_identity(material, server_ip)
    for name in journal_files:
        path = output_dir / name
        if not path.exists():
            if not staged:
                raise ValueError('Komplet TLS har mistet en fil; gendannelse kræver backup.')
        elif not (name == 'enheder.json' and not staged) and hashlib.sha256(_tls_read(path, TLS_MAX_REGISTRY_BYTES if name == 'enheder.json' else TLS_MAX_FILE_BYTES)).hexdigest() != journal['sha256'][name]:
            raise ValueError('TLS-filen er ændret; ingen data blev overskrevet.')
    if not staged:
        registry = json.loads(_tls_read(output_dir / 'enheder.json', TLS_MAX_REGISTRY_BYTES).decode('utf-8'))
        if not isinstance(registry, dict) or not isinstance(registry.get('enheder'), dict):
            raise ValueError('Ugyldigt bevaret enhedsregister.')
        if TLS_PRIVATE_FILE in journal_files:
            _check_tls_identity(_tls_material_from_files(output_dir, journal_files), server_ip)
    return journal


def _tls(output_dir: Path, server_ip: str, owner_id: str = '', *, resume: bool = False, check: bool = False) -> None:
    server_ip = validate_server_ip(server_ip)
    if not owner_id:
        if resume or check:
            raise ValueError('TLS-genoptagelse kræver installationens ejer-id.')
        if output_dir.exists():
            raise FileExistsError('TLS-mappen findes allerede; den overskrives ikke.')
        owner_id = secrets.token_hex(16)
    if not resume and output_dir.exists():
        raise FileExistsError('TLS-mappen findes allerede; den overskrives ikke.')
    journal = _tls_preflight(output_dir, server_ip, owner_id)
    if check or (journal and journal['state'] in ('complete', 'legacy-complete')):
        return
    if journal is None:
        material = _tls_material(server_ip)
        journal = {'schemaVersion': 1, 'ownerId': owner_id, 'serverIp': server_ip,
                   'state': 'staged', 'material': material,
                   'sha256': {name: _tls_digest(material[name]) for name in material}}
    journal_files = _journal_tls_files(journal)
    # No public CA/key/registry exists before the entire identity bundle is
    # durably journaled. A retry always publishes the same identities.
    _atomic_write(output_dir / TLS_JOURNAL, json.dumps(journal))
    for name in journal_files:
        if not (output_dir / name).exists():
            _atomic_write(output_dir / name, journal['material'][name])
    receipt = {name: value for name, value in journal.items() if name != 'material'}
    receipt['state'] = 'complete'
    _atomic_write(output_dir / TLS_JOURNAL, json.dumps(receipt))


def _atomic_write_unique(path: Path, text: str) -> None:
    """Fsync a uniquely named temporary file before replacing *path*."""
    _tls_assert_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _tls_assert_path(path.parent)
    fd, temporary_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary_name)
    private = path.name in PRIVATE_TLS_PATHS
    try:
        if private:
            _restrict_private_path(temporary)
        with os.fdopen(fd, 'wb') as output:
            output.write(text.encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if private:
            _restrict_private_path(path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _renewal_material(output_dir: Path, server_ip: str) -> dict[str, object]:
    """Validate the existing CA/leaf pair without changing any file."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    server_ip = validate_server_ip(server_ip)
    names = TLS_FILES + (TLS_PRIVATE_FILE,)
    for name in names:
        _tls_assert_path(output_dir / name)
        if not (output_dir / name).is_file():
            if name == TLS_PRIVATE_FILE:
                raise ValueError('TLS-fornyelse kræver den bevarede admin-CA-nøgle; ingen ny CA blev oprettet.')
            raise ValueError(f'TLS-filen mangler: {name}')
    material = _tls_material_from_files(output_dir, names)
    ca = x509.load_pem_x509_certificate(material['ca.crt'].encode('ascii'))
    old_cert = x509.load_pem_x509_certificate(material['server.crt'].encode('ascii'))
    ca_key = serialization.load_pem_private_key(material[TLS_PRIVATE_FILE].encode('ascii'), password=None)
    server_key = serialization.load_pem_private_key(material['server.key'].encode('ascii'), password=None)
    now = datetime.now(timezone.utc)
    if ca.not_valid_before_utc > now or ca.not_valid_after_utc <= now:
        raise ValueError('Den bevarede CA er uden for sin gyldighed; fornyelse afviser ny trust.')
    if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError('Det bevarede certifikat er ikke en CA.')
    public_format = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if ca_key.public_key().public_bytes(*public_format) != ca.public_key().public_bytes(*public_format):
        raise ValueError('Den bevarede CA-private key matcher ikke CA-certifikatet.')
    ca.public_key().verify(ca.signature, ca.tbs_certificate_bytes, padding.PKCS1v15(), ca.signature_hash_algorithm)
    if old_cert.issuer != ca.subject:
        raise ValueError('Det eksisterende servercertifikat tilhører ikke den bevarede CA.')
    ca.public_key().verify(old_cert.signature, old_cert.tbs_certificate_bytes, padding.PKCS1v15(), old_cert.signature_hash_algorithm)
    if server_key.public_key().public_bytes(*public_format) != old_cert.public_key().public_bytes(*public_format):
        raise ValueError('server.key matcher ikke det eksisterende servercertifikat.')
    try:
        san = old_cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound as error:
        raise ValueError('Det eksisterende servercertifikat mangler SAN-identitet.') from error
    ips = {str(value) for value in san.get_values_for_type(x509.IPAddress)}
    dns = {value.lower() for value in san.get_values_for_type(x509.DNSName)}
    if server_ip not in ips or '127.0.0.1' not in ips or 'localhost' not in dns:
        raise ValueError('Servercertifikatets SAN matcher ikke serveradresse og localhost.')
    return {'ca': ca, 'ca_key': ca_key, 'server_key': server_key, 'old_cert': old_cert,
            'material': material, 'server_ip': server_ip}


def _renewed_leaf(material: dict[str, object]) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID

    ca = material['ca']
    ca_key = material['ca_key']
    server_key = material['server_key']
    old_cert = material['old_cert']
    server_ip = material['server_ip']
    now = datetime.now(timezone.utc)
    # A leaf may never outlive the CA that signs it.  Keep a small margin for
    # clock skew and fail closed if the retained CA is already too close to
    # expiry to issue a usable certificate.
    ca_not_after = ca.not_valid_after_utc if hasattr(ca, 'not_valid_after_utc') else ca.not_valid_after.replace(tzinfo=timezone.utc)
    leaf_not_after = min(now + timedelta(days=825), ca_not_after - timedelta(minutes=1))
    if leaf_not_after <= now:
        raise ValueError('Den bevarede CA har ikke nok gyldighed til en ny servercertifikatperiode.')
    certificate = (x509.CertificateBuilder().subject_name(old_cert.subject).issuer_name(ca.subject)
        .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(leaf_not_after)
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address(server_ip)),
            x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
            x509.DNSName('localhost')]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
            key_encipherment=True, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256()))
    return certificate.public_bytes(serialization.Encoding.PEM).decode('ascii')


def _read_renewal_journal(path: Path) -> dict:
    _tls_assert_path(path)
    raw = _tls_read(path)
    try:
        journal = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_json)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('TLS-fornyelsesjournalen er beskadiget.') from error
    fields = {'schemaVersion', 'state', 'serverIp', 'oldServerSha256', 'newServerSha256',
              'caSha256', 'keySha256', 'certificate'}
    optional_operation = isinstance(journal, dict) and set(journal) == fields | {'operationId'}
    if (not isinstance(journal, dict) or set(journal) not in (fields, fields | {'operationId'})
            or (optional_operation and (not isinstance(journal.get('operationId'), str)
                                        or not re.fullmatch(r'[0-9a-f]{32}', journal['operationId'])))
            or journal.get('schemaVersion') != 1
            or journal.get('state') != 'staged' or not isinstance(journal.get('serverIp'), str)
            or any(not isinstance(journal.get(name), str) or not re.fullmatch(r'[0-9a-f]{64}', journal[name])
                   for name in ('oldServerSha256', 'newServerSha256', 'caSha256', 'keySha256'))
             or not isinstance(journal.get('certificate'), str)
             or len(journal['certificate'].encode('utf-8')) > TLS_MAX_FILE_BYTES
             or _tls_digest(journal['certificate']) != journal.get('newServerSha256')):
        raise ValueError('TLS-fornyelsesjournalen matcher ikke protokollen.')
    return journal


def _read_renewal_receipt(path: Path) -> dict:
    _tls_assert_path(path)
    try:
        value = json.loads(_tls_read(path).decode('utf-8'), object_pairs_hook=_unique_json)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('TLS-fornyelsesreceipt er beskadiget.') from error
    fields = {'schemaVersion', 'state', 'operationId', 'serverIp', 'oldServerSha256',
              'newServerSha256', 'caSha256', 'keySha256'}
    if (not isinstance(value, dict) or set(value) != fields or value.get('schemaVersion') != 1
            or value.get('state') != 'complete'
            or not isinstance(value.get('operationId'), str)
            or not re.fullmatch(r'[0-9a-f]{32}', value['operationId'])
            or not isinstance(value.get('serverIp'), str)
            or any(not isinstance(value.get(name), str) or not re.fullmatch(r'[0-9a-f]{64}', value[name])
                   for name in ('oldServerSha256', 'newServerSha256', 'caSha256', 'keySha256'))):
        raise ValueError('TLS-fornyelsesreceipt matcher ikke protokollen.')
    return value


def _update_tls_receipt_after_renewal(output_dir: Path, server_sha256: str) -> None:
    path = output_dir / TLS_JOURNAL
    if not path.exists():
        return
    _tls_assert_path(path)
    journal = json.loads(_tls_read(path).decode('utf-8'), object_pairs_hook=_unique_json)
    if (not isinstance(journal, dict) or journal.get('state') != 'complete'
            or set(journal) != {'schemaVersion', 'ownerId', 'serverIp', 'state', 'sha256'}):
        raise ValueError('TLS-installationsjournalen er ikke en afsluttet receipt.')
    _journal_tls_files(journal)
    updated = dict(journal)
    updated['sha256'] = dict(journal['sha256'])
    updated['sha256']['server.crt'] = server_sha256
    _atomic_write_unique(path, json.dumps(updated, sort_keys=True))


def _verify_renewal_receipt(output_dir: Path, server_ip: str,
                            allowed_server_hashes: set[str] | None = None) -> None:
    """Verify the completed initial-install receipt before a leaf mutation."""
    path = output_dir / TLS_JOURNAL
    _tls_assert_path(path)
    if not path.is_file():
        raise ValueError('TLS-fornyelse kræver en afsluttet installationsreceipt.')
    try:
        receipt = json.loads(_tls_read(path).decode('utf-8'), object_pairs_hook=_unique_json)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError('TLS-installationsjournalen er beskadiget; ingen data blev overskrevet.') from error
    if (not isinstance(receipt, dict)
            or set(receipt) != {'schemaVersion', 'ownerId', 'serverIp', 'state', 'sha256'}
            or type(receipt.get('schemaVersion')) is not int or receipt['schemaVersion'] != 1
            or receipt.get('state') != 'complete'
            or receipt.get('serverIp') != validate_server_ip(server_ip)
            or not isinstance(receipt.get('ownerId'), str) or not receipt['ownerId']
            or not isinstance(receipt.get('sha256'), dict)):
        raise ValueError('TLS-installationsjournalen er ikke en afsluttet receipt.')
    names = _journal_tls_files(receipt)
    material = _tls_material_from_files(output_dir, names)
    allowed_server_hashes = set(allowed_server_hashes or ())
    expected_server_hashes = {receipt['sha256']['server.crt']} | allowed_server_hashes
    for name in names:
        # The enrollment registry is intentionally mutable after initial
        # installation. Its JSON shape is checked by _check_tls_identity, but
        # its initial byte hash is not a renewal precondition.
        if name == 'enheder.json':
            continue
        expected = expected_server_hashes if name == 'server.crt' else {receipt['sha256'][name]}
        if _tls_digest(material[name]) not in expected:
            raise ValueError('TLS-installationsjournalens integritet fejlede; ingen data blev overskrevet.')
    _check_tls_identity(material, server_ip)


def _renew_tls(output_dir: Path, server_ip: str, operation_id: str | None = None) -> dict:
    """Renew only ``server.crt`` using the retained admin-only CA key."""
    output_dir = Path(output_dir).absolute()
    _tls_assert_path(output_dir)
    if not output_dir.is_dir():
        raise ValueError('TLS-mappen findes ikke.')
    renewal_path = output_dir / TLS_RENEWAL_JOURNAL
    completion_path = output_dir / TLS_RENEWAL_RECEIPT
    marker = _read_renewal_journal(renewal_path) if renewal_path.exists() else None
    # A crash after publishing server.crt but before updating the initial
    # receipt leaves the old receipt beside a durable staged marker. Permit
    # only that marker's old/new leaf hashes; CA, CA key, server key and all
    # other immutable receipt bindings remain strict.
    allowed = ({marker['oldServerSha256'], marker['newServerSha256']}
               if marker is not None else set())
    _verify_renewal_receipt(output_dir, server_ip, allowed)
    if operation_id is None:
        if marker is not None:
            operation_id = marker.get('operationId') or uuid.uuid4().hex
        elif completion_path.exists():
            operation_id = _read_renewal_receipt(completion_path)['operationId']
        else:
            operation_id = uuid.uuid4().hex
    if not re.fullmatch(r'[0-9a-f]{32}', operation_id):
        raise ValueError('TLS-fornyelsens operation-id er ugyldigt.')
    if completion_path.exists() and not renewal_path.exists():
        completed = _read_renewal_receipt(completion_path)
        current = _tls_digest(_tls_read(output_dir / 'server.crt').decode('utf-8'))
        if completed['operationId'] == operation_id:
            if completed['serverIp'] != validate_server_ip(server_ip) or current != completed['newServerSha256']:
                raise ValueError('TLS-fornyelsens færdige receipt matcher ikke den aktuelle leaf.')
            return completed
    if marker is not None:
        journal = marker
        marker_operation = journal.get('operationId')
        if marker_operation is not None and marker_operation != operation_id:
            raise ValueError('TLS-fornyelsen tilhører en anden operation.')
        if journal['serverIp'] != validate_server_ip(server_ip):
            raise ValueError('TLS-fornyelsen matcher ikke serveradressen.')
        state = _renewal_material(output_dir, server_ip)
        material = state['material']
        if (_tls_digest(material['ca.crt']) != journal['caSha256'] or
                _tls_digest(material['server.key']) != journal['keySha256']):
            raise ValueError('CA eller server.key blev ændret under TLS-fornyelsen.')
        certificate = journal['certificate']
        renewed_material = dict(material)
        renewed_material['server.crt'] = certificate
        _check_tls_identity(renewed_material, server_ip)
        current = _tls_digest(_tls_read(output_dir / 'server.crt').decode('utf-8'))
        if current == journal['oldServerSha256']:
            _atomic_write_unique(output_dir / 'server.crt', certificate)
            current = journal['newServerSha256']
        elif current != journal['newServerSha256']:
            raise ValueError('Servercertifikatet blev ændret under TLS-fornyelsen.')
        completion = {'schemaVersion': 1, 'state': 'complete', 'operationId': operation_id,
                      'serverIp': journal['serverIp'], 'oldServerSha256': journal['oldServerSha256'],
                      'newServerSha256': journal['newServerSha256'], 'caSha256': journal['caSha256'],
                      'keySha256': journal['keySha256']}
        _update_tls_receipt_after_renewal(output_dir, current)
        _atomic_write_unique(completion_path, json.dumps(completion, sort_keys=True))
        renewal_path.unlink()
        return completion
    state = _renewal_material(output_dir, server_ip)
    old_text = state['material']['server.crt']
    new_text = _renewed_leaf(state)
    journal = {'schemaVersion': 1, 'state': 'staged', 'operationId': operation_id,
               'serverIp': state['server_ip'],
               'oldServerSha256': _tls_digest(old_text), 'newServerSha256': _tls_digest(new_text),
               'caSha256': _tls_digest(state['material']['ca.crt']),
               'keySha256': _tls_digest(state['material']['server.key']), 'certificate': new_text}
    _atomic_write_unique(renewal_path, json.dumps(journal, sort_keys=True))
    _atomic_write_unique(output_dir / 'server.crt', new_text)
    _update_tls_receipt_after_renewal(output_dir, journal['newServerSha256'])
    completion = {'schemaVersion': 1, 'state': 'complete', 'operationId': operation_id,
                  'serverIp': journal['serverIp'], 'oldServerSha256': journal['oldServerSha256'],
                  'newServerSha256': journal['newServerSha256'], 'caSha256': journal['caSha256'],
                  'keySha256': journal['keySha256']}
    _atomic_write_unique(completion_path, json.dumps(completion, sort_keys=True))
    renewal_path.unlink()
    return completion


@contextmanager
def registry_lock(path):
    """Serialize admin enrollment and benchmark changes without lost updates."""
    with path.with_suffix('.lock').open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def renew_tls(output_dir: Path, server_ip: str, operation_id: str | None = None,
              *, lock_held: bool = False) -> dict:
    """Renew under the enrollment lock used by device registration."""
    output_dir = Path(output_dir)
    # Fail closed before creating the lock sidecar: a legacy four-file
    # installation must remain byte-for-byte untouched and must never receive
    # a new CA as a fallback.
    renewal_path = output_dir / TLS_RENEWAL_JOURNAL
    if renewal_path.exists():
        _read_renewal_journal(renewal_path)
        _renewal_material(output_dir, server_ip)
    else:
        _renewal_material(output_dir, server_ip)
    if lock_held:
        return _renew_tls(output_dir, server_ip, operation_id)
    with registry_lock(output_dir / 'enheder.json'):
        return _renew_tls(output_dir, server_ip, operation_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    enroll_parser = sub.add_parser("enroll")
    enroll_parser.add_argument("--registry", type=Path, required=True)
    enroll_parser.add_argument("--name", required=True)
    enroll_parser.add_argument("--server", required=True)
    enroll_parser.add_argument("--ca-certificate", type=Path, required=True)
    enroll_parser.add_argument("--output", type=Path, required=True)
    enroll_parser.add_argument("--write", action="store_true", help="Skriv den valgte registry-fil atomisk.")
    tls_parser = sub.add_parser("init-tls")
    tls_parser.add_argument("--output-dir", type=Path, required=True)
    tls_parser.add_argument("--server-ip", required=True)
    tls_parser.add_argument("--owner-id", default='')
    tls_parser.add_argument("--resume", action='store_true')
    tls_parser.add_argument("--check", action='store_true', help='Kontrollér genoptagelse uden at skrive data.')
    renew_parser = sub.add_parser('renew-tls', help='Forny kun servercertifikatet med den bevarede CA.')
    renew_parser.add_argument('--output-dir', type=Path, required=True)
    renew_parser.add_argument('--server-ip', required=True)
    renew_parser.add_argument('--operation-id', default=None)
    renew_parser.add_argument('--lock-held', action='store_true',
                              help='Bruges kun af skoleoperatoren med offline datalocks.')
    tree_parser = sub.add_parser('check-initial-install-tree')
    tree_parser.add_argument('--package-root', type=Path, required=True)
    tree_parser.add_argument('--runtime-root', type=Path, required=True)
    tree_parser.add_argument('--data-root', type=Path, required=True)
    tree_parser.add_argument('--uninstall', action='store_true')
    removal_parser = sub.add_parser('generation-uninstall-plan')
    removal_parser.add_argument('--runtime-root', type=Path, required=True)
    removal_parser.add_argument('--anchor-data-root', type=Path, required=True)
    sub.add_parser("help-cert", help="Dokumenterer, at cryptography skal bundles; opretter intet.")
    args = parser.parse_args()
    if args.action == "help-cert":
        print("TLS oprettes ved en eksplicit administratorhandling med bundled cryptography; ingen secrets blev oprettet.")
        return 0
    if args.action == 'init-tls':
        _tls(args.output_dir, args.server_ip, args.owner_id, resume=args.resume, check=args.check)
        return 0
    if args.action == 'renew-tls':
        print(json.dumps(renew_tls(args.output_dir, args.server_ip, args.operation_id,
                                   lock_held=args.lock_held), sort_keys=True))
        return 0
    if args.action == 'check-initial-install-tree':
        check_initial_install_tree(args.package_root, args.runtime_root, args.data_root, uninstall=args.uninstall)
        return 0
    if args.action == 'generation-uninstall-plan':
        print(json.dumps(generation_uninstall_plan(args.runtime_root, args.anchor_data_root), ensure_ascii=True))
        return 0
    args.server = validate_server_url(args.server)
    if not args.write:
        print('Ingen tilmelding skrevet. Brug --write for at oprette en beskyttet tilmeldingsfil.')
        return 0
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('Tilmeldingsfilen findes allerede; den overskrives ikke.')
    with registry_lock(args.registry):
        rendered, token, ui_token = enroll(args.registry, args.name)
        enrollment = {'schemaVersion': 1, 'server': args.server, 'computer': args.name,
                      'token': token, 'ui_token': ui_token,
                      'caCertificatePem': args.ca_certificate.read_text(encoding='utf-8')}
        # Write enrollment before registry; a failed export never invalidates a device.
        with args.output.open('x', encoding='utf-8') as output:
            output.write(json.dumps(enrollment, ensure_ascii=False, indent=2) + '\n')
        _atomic_write(args.registry, rendered)
    print('Tilmeldingsfilen er oprettet. Opbevar den hos administratoren.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
