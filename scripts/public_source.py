"""Audit and export the deliberately allowlisted public source tree.

The command is intentionally source-only.  It never reads runtime data as a
template, follows a link, overwrites an output directory, or publishes an
archive before its complete deterministic manifest has been written.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import fnmatch
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tokenize
import unicodedata
import base64
import zlib
from urllib.parse import unquote, urlsplit


SCHEMA_VERSION = 1
GENERATOR_VERSION = 3
MANIFEST_NAME = "PUBLIC-SOURCE-MANIFEST.json"
RECEIPT_NAME = ".public-source-receipt.json"
CREDIT = "Kreditering: Klostermarksskolen."
AUTHOR_CREDIT = "Udviklet af Mathias Kusk Damgaard."
# Original Debian *source* archives used by the maintained offline build.sh.
# These two immutable inputs are the only compressed binary-format exception.
SOURCE_ARCHIVES = {
    'matchbox-window-manager_1.2.2+git20200512.orig.tar.xz': 'a0d869dd182b8e31cd67bd4425db48f66ada4f4163e57b9b796304f5a022abcd',
    'matchbox-window-manager_1.2.2+git20200512-2.debian.tar.xz': '2f91a887236c0697a1d4bd5519c83481310d61b24949ce4060b19e97b84337d5',
}
PRIVATE_PEM = re.compile(
    rb"-----BEGIN (?:RSA |EC |DSA |ENCRYPTED |OPENSSH )?PRIVATE KEY-----[\r\n]+"
    rb"[A-Za-z0-9+/=\r\n: ,.-]{32,}"
    rb"-----END (?:RSA |EC |DSA |ENCRYPTED |OPENSSH )?PRIVATE KEY-----"
)
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
IPV4 = re.compile(r'(?<![A-Za-z0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9.])')
IPV6 = re.compile(r'(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]*(?:%[\w.~-]+)?(?![\w:])')
MAC = re.compile(r'(?i)(?<![\da-f])(?:[\da-f]{2}[:-]){5,7}[\da-f]{2}(?![\da-f])')
MAC_DOTTED = re.compile(r'(?i)(?<![\da-f.])(?:[\da-f]{4}\.){2}[\da-f]{4}(?![\da-f.])')
EMAIL = re.compile(r'(?i)(?<![\w.+-])[\w.+-]+@[a-z\d.-]+\.[a-z]{2,}(?![\w.-])')
LOCAL_HOST = re.compile(r'(?i)\b[a-z\d][a-z\d.-]*\.(?:local|lan|internal|home|localdomain|corp)(?![a-z\d_.-])')
PERSONAL_HOME = re.compile(r'(?i)(?:\b[a-z]:[\\/]+Users[\\/]+|/(?:(?:home|Users)/))([^\s\\/"\'<>`]+)')
LITERAL_CREDENTIAL = re.compile(r'''(?ix)\b(?P<field>access_code|password|password_hash|passphrase|private_key|admin_private_key|enrollment_token|ui_token|api_key|deployment_key|token|secret)\b["']?\s*[:=]\s*(?P<quote>["'])(?P<value>[^"'\r\n]+)(?P=quote)''')
PROSE_CREDENTIAL = re.compile(r'(?i)\b(?:password|passphrase|adgangskode|kodeord|wi-?fi[- ]?(?:kode|password)|rdp[- ]?(?:kode|password))\b')
ENCODED_LITERAL = re.compile(r'''(?ix)(?:b64decode|FromBase64String|atob)\s*\(\s*["']([A-Za-z0-9+/=\r\n]{4,})["']''')
DOCUMENTARY = tuple(ipaddress.ip_network(n) for n in ('192.0.2.0/24','198.51.100.0/24','203.0.113.0/24','2001:db8::/32'))
PRIVACY_DESCRIPTIONS = {'secret.pem':'Privat PEM-materiale', 'secret.json':'Mulig skolehemmelighed', 'credential.json':'Udfyldt privat adgangsfelt', 'credential.literal':'Mulig skolehemmelighed', 'credential.prose':'Mulig skolehemmelighed'}


def _decode_text(data: bytes) -> str:
    """Read wide Windows text as text, never silently skip unrecognised bytes."""
    for bom, encoding in ((b'\xff\xfe\x00\x00','utf-32'), (b'\x00\x00\xfe\xff','utf-32'),
                          (b'\xff\xfe','utf-16'), (b'\xfe\xff','utf-16'), (b'\xef\xbb\xbf','utf-8-sig')):
        if data.startswith(bom):
            try: return data.decode(encoding)
            except UnicodeError as error: raise ExportError('Ugyldig tekstkodning.') from error
    if b'\0' in data:
        # Also cover BOM-less UTF-16/32 source, not just BOM-tagged PS files.
        for encoding in ('utf-32-le','utf-32-be','utf-16-le','utf-16-be'):
            try: text=data.decode(encoding)
            except UnicodeError: continue
            if '\0' not in text and text and sum(c.isprintable() or c.isspace() for c in text)/len(text) > .95:
                return text
        raise ExportError('Ukendt binær kildefil er ikke tilladt.')
    try: return data.decode('utf-8')
    except UnicodeError:
        # ASCII network/credential syntax remains visible in legacy 8-bit notices.
        return data.decode('latin-1')


def _placeholder(value: object) -> bool:
    return value in (None, '') or (isinstance(value,str) and (
        re.fullmatch(r'<[^<>\r\n]{1,100}>',value) is not None or
        value in {'REPLACE_ME','CHANGE_ME','SERIAL','PRINTER_SERIAL','BRUGER','USERNAME'}))


def _privacy_settings(policy: dict | None) -> dict:
    settings=(policy or {}).get('privacyRules',{})
    if not isinstance(settings,dict) or settings.get('schemaVersion',1)!=1:
        raise ExportError('Ukendt privacyRules-schema.')
    for name in ('genericHomeUsers','publicAttributionEmails','publicAttributionEmailSha256','reviewedExceptions','forbiddenIdentifierSha256'):
        if not isinstance(settings.get(name,[]),list): raise ExportError('Ugyldigt privacyRules-felt: '+name)
    if settings.get('publicAttributionEmails'): raise ExportError('Offentlige upstreamkontakter kræver et begrundet hashreview, ikke en rå email-allowlist.')
    for item in settings.get('reviewedExceptions',[]):
        if (not isinstance(item,dict) or set(item)!={'path','lineSha256','category','reason'} or
                not isinstance(item['lineSha256'],str) or not re.fullmatch('[a-f0-9]{64}',item['lineSha256']) or
                not isinstance(item['category'],str) or not isinstance(item['reason'],str) or len(item['reason'])<16):
            raise ExportError('Ugyldig, ikke begrundet privacyRules-undtagelse.')
        _relative(item['path'])
        if item['category'] not in {'network.private-ip','network.public-ip','network.local-hostname','inventory.mac','personal.local-home','personal.email','credential.literal','inventory.populated-json','encoded.opaque-content','secret.password-hash'}:
            raise ExportError('Hemmeligheder og kendte skoleidentiteter må ikke undtages fra indholdskontrollen.')
        if item['category']=='secret.password-hash' and not any(part.startswith('test') for part in PurePosixPath(item['path']).parts):
            raise ExportError('Et syntetisk password-hash-review kræver en konkret testfil.')
    for value in settings.get('forbiddenIdentifierSha256',[]):
        if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value): raise ExportError('Ugyldig identitetsfingeraftryk.')
    for item in settings.get('publicAttributionEmailSha256',[]):
        if (not isinstance(item,dict) or set(item)!={'sha256','reason'} or not re.fullmatch('[a-f0-9]{64}',str(item['sha256']))
                or not isinstance(item['reason'],str) or len(item['reason'])<16): raise ExportError('Ugyldig public attribution-email review.')
    return settings


def privacy_findings(relative: str, data: bytes, policy: dict | None = None) -> list[dict]:
    """Return paths/line numbers/categories only, never matched private values.

    This guard recognises concrete network/inventory evidence, personal paths,
    explicit credentials, and common encodings. It does not claim to establish
    whether arbitrarily computed/obfuscated program output is private.
    """
    settings=_privacy_settings(policy)
    generic={x.casefold() for x in settings.get('genericHomeUsers',['elev','elevhub','kiosk','root','BRUGER','USER','USERNAME','example','user','Default','Public'])}
    public_emails={x.casefold() for x in settings.get('publicAttributionEmails',[])}
    public_email_hashes={x['sha256'] for x in settings.get('publicAttributionEmailSha256',[])}
    forbidden=set(settings.get('forbiddenIdentifierSha256',[]))
    findings=set()
    text=_decode_text(data)
    lines=text.splitlines()
    python_text_regions=None
    if Path(relative).suffix.lower()=='.py':
        try:
            python_text_regions=[]
            kinds={tokenize.STRING,tokenize.COMMENT}
            kinds.update(getattr(tokenize,name,-999) for name in ('FSTRING_START','FSTRING_MIDDLE','FSTRING_END'))
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type in kinds: python_text_regions.append((token.start,token.end))
        except (tokenize.TokenError,SyntaxError,IndentationError): python_text_regions=None
    def add(line,category): findings.add((line,category))
    def scan(fragment,source_line=None,depth=0):
        fragment=unicodedata.normalize('NFKC',fragment)
        pending_credential=False
        for number,line in enumerate(fragment.splitlines(),1):
            at=source_line or number
            line=html.unescape(line)
            line=re.sub(r'\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2}))',lambda m:chr(int(m.group(1) or m.group(2),16)) if not 0xd800<=int(m.group(1) or m.group(2),16)<=0xdfff else m.group(),line)
            # Human instructions often put an actual credential in backticks,
            # including on the next line. Do not depend on a JSON/key syntax.
            if Path(relative).suffix.lower() in {'.md','.rst','.txt'}:
                labelled=list(PROSE_CREDENTIAL.finditer(line))
                values=[]
                for label in labelled:
                    tail=line[label.end():]
                    direct=re.match(r'\s*(?::|=|\ber\b|\bis\b)?\s*`([^`\r\n]+)`',tail,re.I)
                    if direct: values.append(direct.group(1))
                if pending_credential:
                    following=re.match(r'\s*`([^`\r\n]+)`',line)
                    if following: values.append(following.group(1))
                if values:
                    for value in values:
                        if not _placeholder(value) and not re.fullmatch(r'(?:fixture|synthetic|example|dummy)(?:[-_:][a-zA-Z0-9._:-]+)+',value):
                            add(at,'credential.prose')
                pending_credential=bool(labelled and re.fullmatch(r'\s*[:=]?\s*',line[labelled[-1].end():]))
            for match in (*IPV4.finditer(line),*IPV6.finditer(line)):
                candidate=match.group().split('%',1)[0]
                try: address=ipaddress.ip_address(candidate)
                except ValueError: continue
                if getattr(address,'ipv4_mapped',None): address=address.ipv4_mapped
                if address.is_loopback or address.is_unspecified or str(address)=='255.255.255.255' or any(address.version==n.version and address in n for n in DOCUMENTARY): continue
                # Version metadata has the same four-number spelling but is not
                # an address. Keep this syntactically limited, not any 'version' comment.
                if address.version==4 and re.search(r'(?:Assembly(?:File|Informational)?Version\s*\(|<(?:(?:File|Product|Assembly)Version|Version)>)["\s]*'+re.escape(candidate),line): continue
                add(at,'network.private-ip' if address.is_private or address.is_link_local else 'network.public-ip')
            for match in (*MAC.finditer(line),*MAC_DOTTED.finditer(line)):
                compact=re.sub(r'[:.\-]','',match.group()).lower()
                if set(compact) not in ({'0'},{'f'}): add(at,'inventory.mac')
            for match in LOCAL_HOST.finditer(line):
                token=match.group().casefold()
                # Known programming properties are not machine names. This is
                # deliberately an exact semantic list, not a '.home' exemption.
                if token=='duser.home' and '-Duser.home' in line: continue
                if token=='user.home' and any(name in line for name in ('getProperty(','setProperty(','clearProperty(')): continue
                if token in {'path.home','pathlib.path.home','threading.local'} and line[match.end():].lstrip().startswith('('): continue
                if token=='process.env.home' and '://' not in line: continue
                if python_text_regions is not None and source_line is None and line==lines[number-1] and not any(
                        start<=(number,match.start())<end for start,end in python_text_regions): continue
                add(at,'network.local-hostname')
            for match in PERSONAL_HOME.finditer(line):
                if match.group(1).casefold() not in generic and not match.group(1).startswith(('$','%','{')): add(at,'personal.local-home')
            for match in EMAIL.finditer(line):
                value=match.group().casefold();domain=value.rsplit('@',1)[-1]
                unit=re.fullmatch(r'(?:serial-)?getty@[A-Za-z0-9_-]+\.service',value)
                if not unit and value not in public_emails and hashlib.sha256(value.encode()).hexdigest() not in public_email_hashes and domain not in {'example.com','example.net','example.org','example.invalid'} and not domain.endswith(('.invalid','.test','.example')): add(at,'personal.email')
            for match in LITERAL_CREDENTIAL.finditer(line):
                value=match.group('value')
                if re.fullmatch(r'\{\{\s*[A-Za-z_][\w.]*\s*\}\}',value): continue
                if match.group('field').casefold()=='password' and value in {'!','!!','*'} and Path(relative).suffix.lower() in {'.yml','.yaml','.j2'}: continue
                if not _placeholder(value) and not re.fullmatch(r'(?:fixture|synthetic|example|dummy)(?:[-_:][a-zA-Z0-9._:-]+)+',value): add(at,'credential.literal')
            if re.search(r'(?:(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}\b)|ssh-(?:rsa|ed25519)\s+[A-Za-z0-9+/]{32,})',line): add(at,'secret.token-or-ssh-key')
            if re.search(r'\$(?:[156]|2[aby]|argon2(?:id|i|d))\$[^\s\"\'`<>]{8,}',line): add(at,'secret.password-hash')
            if re.search(r'(?<![\w])SHA256:[A-Za-z0-9+/]{43}=?(?![A-Za-z0-9+/])|(?i:MD5:)(?:[0-9a-fA-F]{2}:){15}[0-9a-fA-F]{2}',line): add(at,'inventory.ssh-fingerprint')
            # Preserve punctuation-bearing credential/SSID tokens for a private
            # audit policy as well as individual hostname/account words.
            if forbidden:
                tokens=set(re.findall(r'[A-Za-z][A-Za-z0-9_.-]{1,253}',line))
                tokens.update(re.findall(r'[^\s`"\'<>]+',line))
                tokens.update(re.findall(r'[`"\']([^`"\'\r\n]+)[`"\']',line))
                for token in tokens:
                    if hashlib.sha256(token.casefold().encode(errors='surrogatepass')).hexdigest() in forbidden: add(at,'inventory.school-identifier')
            if depth<3:
                for encoded in ENCODED_LITERAL.finditer(line):
                    try:
                        decoded=base64.b64decode(encoded.group(1),validate=False)
                        if decoded.startswith((b'\x1f\x8b',b'x\x9c',b'x\xda')):
                            decoder=zlib.decompressobj(31 if decoded.startswith(b'\x1f\x8b') else 15)
                            decoded=decoder.decompress(decoded,1024*1024+1)
                            if len(decoded)>1024*1024 or decoder.unconsumed_tail: add(at,'encoded.unbounded-content');continue
                        try: decoded_text=_decode_text(decoded)
                        except ExportError: add(at,'encoded.opaque-content');continue
                        scan(decoded_text,at,depth+1)
                    except (ValueError,UnicodeError,zlib.error): add(at,'encoded.invalid-content')
            elif ENCODED_LITERAL.search(line): add(at,'encoded.nesting-limit')
        if PRIVATE_PEM.search(fragment.encode('utf-8',errors='surrogatepass')): add(source_line or 1,'secret.pem')
    scan(text)
    # JSON escape sequences may hide the same text from a raw regex scan.
    try: parsed=json.loads(text)
    except ValueError: parsed=None
    if parsed is not None:
        try: list(_walk_json(parsed))
        except ExportError as error: add(1,'credential.json' if 'privat adgangsfelt' in str(error) else 'secret.json')
        def strings(value):
            if isinstance(value,dict):
                for key,child in value.items():
                    if str(key).casefold() in {'mac','mac_address','serial','serial_number','machine_identity','enrollment_key'} and not _placeholder(child): add(1,'inventory.populated-json')
                    strings(child)
            elif isinstance(value,list):
                for child in value: strings(child)
            elif isinstance(value,str) and ('\\u' in text or '\\x' in text): scan(value,1)
        strings(parsed)
    # Filenames themselves are published too. Scan only the relative name,
    # never the local checkout's absolute Windows/user path.
    prior=set(findings);scan(relative,1)
    findings={pair if pair in prior else (0,pair[1]) for pair in findings}
    exceptions={(x['path'],x['lineSha256'],x['category']) for x in settings.get('reviewedExceptions',[])}
    result=[]
    for number,category in sorted(findings):
        line=lines[number-1] if 0<number<=len(lines) else relative
        if (relative,hashlib.sha256(line.encode()).hexdigest(),category) in exceptions: continue
        result.append({'file':relative,'line':number,'category':category})
    return result


class ExportError(ValueError):
    """A fail-closed audit or export error."""


@dataclass(frozen=True)
class SourceFile:
    relative: str
    path: Path
    size: int
    sha256: str
    mode: int


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _posix(value: str | Path) -> str:
    """Return a policy-relative spelling without hiding traversal markers.

    The old ``lstrip('./')`` form was unsafe because it silently changed
    ``../outside.py`` into ``outside.py`` before the path was validated.
    Keeping this conversion lossless lets the validator reject traversal.
    """
    text = str(value).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _relative(value: str | Path, *, field: str = "sti") -> str:
    text = _posix(value)
    parsed = PurePosixPath(text)
    drive_path = bool(re.match(r"^[A-Za-z]:/", text))
    if not text or parsed.is_absolute() or text.startswith("//") or drive_path or ':' in text or any(ord(c)<32 or ord(c)==127 for c in text) or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ExportError(f"Ugyldig relativ {field}: {value}")
    return text


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _manifest_path(root: Path) -> Path:
    return root / "deployment" / "public-source-manifest.json"


def _load_policy(root: Path) -> dict:
    path = _manifest_path(root)
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExportError(f"Manifesten kan ikke læses: {path}") from error
    if not isinstance(policy, dict) or policy.get("schemaVersion") != SCHEMA_VERSION:
        raise ExportError("Ukendt public-source-manifest schema.")
    for key in ("sourceRoots", "explicitFiles", "includeExtensions", "requiredDocs"):
        if not isinstance(policy.get(key), list):
            raise ExportError(f"Manifestfeltet {key} skal være en liste.")
    return policy


def _windows_reparse(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        get_attributes = ctypes.windll.kernel32.GetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        value = get_attributes(str(path))
        return value != 0xFFFFFFFF and bool(value & 0x400)
    except (AttributeError, OSError):
        return False


def _lstat_checked(path: Path, *, reject_hardlink: bool = False) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise ExportError(f"Kildestien kan ikke læses: {path}") from error
    if stat.S_ISLNK(info.st_mode) or _windows_reparse(path):
        raise ExportError(f"Symlink/reparsepunkt er ikke tilladt i kildeudgivelsen: {path}")
    if reject_hardlink and stat.S_ISREG(info.st_mode) and getattr(info, "st_nlink", 1) != 1:
        raise ExportError(f"Hardlink er ikke tilladt i kildeudgivelsen: {path}")
    return info


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as error:
        raise ExportError(f"Kildestien kan ikke læses: {path}") from error
    return stat.S_ISLNK(info.st_mode) or _windows_reparse(path)


def _check_parent_chain(path: Path, root: Path) -> None:
    current = root
    _lstat_checked(current)
    try:
        parts = path.relative_to(root).parts
    except ValueError as error:
        raise ExportError(f"Sti ligger uden for kilde-roden: {path}") from error
    for part in parts[:-1]:
        current /= part
        _lstat_checked(current)


def _is_excluded(relative: str, policy: dict) -> bool:
    rel = PurePosixPath(relative)
    if any(part in policy.get("excludeDirectoryNames", []) for part in rel.parts):
        return True
    for pattern in policy.get("excludeGlobs", []):
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, pattern.removeprefix("**/")):
            return True
    return False


def _read_source(path: Path, limit: int, public_image_sha256: str | None = None) -> tuple[int, str, bytes]:
    info = _lstat_checked(path, reject_hardlink=True)
    if not stat.S_ISREG(info.st_mode):
        raise ExportError(f"Kildeelementet er ikke en almindelig fil: {path}")
    if info.st_size > limit:
        raise ExportError(f"Filen overskrider filgrænsen på {limit} byte: {path}")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ExportError(f"Filen overskrider filgrænsen på {limit} byte: {path}")
                digest.update(chunk)
                chunks.append(chunk)
    except OSError as error:
        raise ExportError(f"Kilde kan ikke læses: {path}") from error
    data = b"".join(chunks)
    if path.suffix.lower()=='.ttf':
        if (public_image_sha256 is None or digest.hexdigest()!=public_image_sha256 or
                data[:4] not in (b'\x00\x01\x00\x00',b'OTTO',b'true',b'ttcf')):
            raise ExportError(f'Fonten mangler en verificeret publicFonts-pin: {path}')
    is_public_image = False
    if path.suffix.lower() in {'.png', '.webp', '.jpg', '.jpeg', '.gif', '.ico'}:
        if public_image_sha256 is None or digest.hexdigest() != public_image_sha256:
            raise ExportError(f'Billedet mangler en godkendt publicImages-kontrolsum: {path}')
        png = path.suffix.lower() == '.png' and data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR'
        webp = (path.suffix.lower() == '.webp' and data[:4] == b'RIFF'
                and data[8:12] == b'WEBP' and len(data) >= 12
                and int.from_bytes(data[4:8], 'little') == len(data) - 8)
        if not (png or webp):
            raise ExportError(f'Godkendt illustration har ugyldigt PNG/WebP-format: {path}')
        is_public_image = True
    is_source_archive = path.name in SOURCE_ARCHIVES
    if is_source_archive and digest.hexdigest() != SOURCE_ARCHIVES[path.name]:
        raise ExportError(f'Original kildearkiv matcher ikke den verificerede Debian-pin: {path}')
    if path.suffix.lower()!='.ttf' and not is_source_archive and not is_public_image:
        try: _decode_text(data)
        except ExportError as error: raise ExportError(f'Ukendt binær kildefil er ikke tilladt: {path}') from error
    return total, digest.hexdigest(), data


def _looks_real_secret(value: object) -> bool:
    if not isinstance(value, str) or len(value) < 24:
        return False
    lower = value.lower()
    if re.fullmatch(r'(?:test|fixture|synthetic|example|dummy)(?:[-_:][a-z0-9._:-]+)+',lower):
        return False
    if len(set(value)) <= 3 or lower.startswith(("192.0.2.", "198.51.100.", "203.0.113.")):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/=_:-]{24,}", value))


def _walk_json(value: object, path: str = "$"):
    if isinstance(value, dict):
        for key, child in value.items():
            key_lower = str(key).lower()
            # Printer LAN codes and passwords can be short and low entropy.
            # These configuration fields must be empty in a public template;
            # token-length heuristics and fixture-looking substrings cannot
            # establish that a populated credential is safe to publish.
            if (key_lower in {"access_code", "password", "private_key", "admin_private_key"}
                    and child is not None and child != ""):
                raise ExportError(f"Udfyldt privat adgangsfelt i JSON-filen ({path}.{key}).")
            if key_lower in {
                "token", "ui_token", "password", "secret", "private_key",
                "admin_private_key", "installation_id", "enrollment_token",
                "token_sha256", "ui_token_sha256", "access_code"
            } and _looks_real_secret(child):
                raise ExportError(f"Mulig skolehemmelighed i JSON-filen ({path}.{key}).")
            yield from _walk_json(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, f"{path}[{index}]")


def _check_public_branding(relative: str, data: bytes) -> None:
    """Reject the school's identity as a public UI default, retaining credits.

    Local-appearance data is never exported. Compatibility code may still
    recognise historical names; this guard inspects actual UI source.
    """
    path = PurePosixPath(relative)
    if any(part.startswith('test') for part in path.parts) or 'third_party' in path.parts:
        return
    if path.suffix.lower() not in {'.html', '.j2', '.css', '.js', '.svg'}:
        return
    text = _decode_text(data)
    if re.search(r'klostermarksskolen[-_ ]*logo', text, re.I):
        raise ExportError(f'Lokalt skolelogo må ikke være offentlig UI-standard: {relative}')
    if path.suffix.lower() in {'.html', '.j2'}:
        class IdentityCheck(HTMLParser):
            def handle_data(self, value):
                if ('klostermarksskolen' in value.casefold()
                        and not re.search(r'kreditering\s*:|ophav|copyright', value, re.I)):
                    raise ExportError(f'Fast skolenavn må ikke være offentlig UI-standard: {relative}')
            def handle_starttag(self, tag, attributes):
                for name, value in attributes:
                    if name in {'alt','title','content','aria-label'} and value and 'klostermarksskolen' in value.casefold():
                        if not re.search(r'kreditering\s*:|ophav|copyright', value, re.I):
                            raise ExportError(f'Fast skoleidentitet i offentlig UI-attribut: {relative}')
        IdentityCheck().feed(text)
    if path.suffix.lower() == '.css' and re.search(r'#(?:b4121a|920008)\b', text, re.I):
        raise ExportError(f'Lokalt skoletema må ikke være offentlig CSS-standard: {relative}')


def _check_content(path: Path, data: bytes, policy: dict | None = None, *, relative: str | None = None) -> None:
    if path.suffix.lower() in {'.png','.webp','.ttf'} or path.name in SOURCE_ARCHIVES:
        return  # Only immutable, hash-pinned public artwork/source inputs.
    findings=privacy_findings(relative or path.name,data,policy)
    if findings:
        first=findings[0]
        description=PRIVACY_DESCRIPTIONS.get(first['category'],'Privat person-/driftsoplysning')
        raise ExportError(f"{description}: {first['file']}:{first['line']} [{first['category']}]")
    _check_public_branding(relative or path.name, data)


def _explicit_set(policy: dict) -> set[str]:
    return {_relative(value, field="manifeststi") for value in policy["explicitFiles"]}


def _public_image_map(policy: dict) -> dict[str, str]:
    images = policy.get('publicImages', [])
    if not isinstance(images, list) or len(images) > 128:
        raise ExportError('publicImages skal være en afgrænset liste.')
    explicit = _explicit_set(policy)
    result = {}
    folded = set()
    roots = ('web/assets/', 'linux-elevhub/server/elevhub/web/assets/',
             'windows-elevhub/client/web/assets/', 'linux-elevhub/server/kredsloeb/assets/')
    for item in images:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'provenance'}:
            raise ExportError('Ugyldig publicImages-post.')
        relative = _relative(item['path'], field='illustrationssti')
        if (relative not in explicit or relative.casefold() in folded
                or not relative.startswith(roots) or '/brand/' in relative.casefold()
                or 'logo' in PurePosixPath(relative).name.casefold()
                or PurePosixPath(relative).suffix.lower() not in {'.png', '.webp'}):
            raise ExportError(f'Illustrationen er ikke eksplicit godkendt eller er et skolelogo: {relative}')
        if (not isinstance(item['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])
                or not isinstance(item['provenance'], str) or not 1 <= len(item['provenance']) <= 1024):
            raise ExportError(f'Illustrationen mangler kontrolsum eller provenance: {relative}')
        result[relative] = item['sha256']
        folded.add(relative.casefold())
    return result


def _public_font_map(policy: dict) -> dict[str,str]:
    result={};explicit=_explicit_set(policy)
    fonts=policy.get('publicFonts',[])
    if not isinstance(fonts,list) or len(fonts)>64: raise ExportError('Ugyldig publicFonts-liste.')
    for item in fonts:
        if (not isinstance(item,dict) or set(item)!={'path','sha256','provenance'} or
                not isinstance(item['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',item['sha256']) or
                not isinstance(item['provenance'],str) or len(item['provenance'])<16): raise ExportError('Ugyldig publicFonts-post.')
        name=_relative(item['path'])
        if name not in explicit or Path(name).suffix.lower()!='.ttf' or name in result: raise ExportError('Public font er ikke entydigt eksplicit valgt.')
        result[name]=item['sha256']
    return result


def _collect(root: Path, policy: dict, *, content_checks: bool = True) -> list[SourceFile]:
    root = root.resolve()
    explicit = _explicit_set(policy)
    public_images = _public_image_map(policy)
    public_fonts = _public_font_map(policy)
    include_ext = {str(value).lower() for value in policy["includeExtensions"]}
    include_names = {str(value) for value in policy.get("includeNames", [])}
    limits = policy.get("limits", {})
    max_file = int(limits.get("maxFileBytes", 64 * 1024 * 1024))
    max_total = int(limits.get("maxTotalBytes", 512 * 1024 * 1024))
    files: dict[str, SourceFile] = {}

    def add(relative: str, path: Path) -> None:
        relative = _relative(relative)
        if relative in files:
            return
        _check_parent_chain(path, root)
        size, digest, data = _read_source(path, max_file, public_images.get(relative) or public_fonts.get(relative))
        if content_checks: _check_content(path, data,policy,relative=relative)
        mode = stat.S_IMODE(path.lstat().st_mode)
        files[relative] = SourceFile(relative, path, size, digest, mode)

    for relative in sorted(explicit):
        path = root / Path(relative)
        if not path.is_file():
            raise ExportError(f"Explicit kildefil mangler: {relative}")
        if _is_excluded(relative, policy) and relative not in explicit:
            raise ExportError(f"Explicit kildefil er uventet ekskluderet: {relative}")
        add(relative, path)

    for source_root in policy["sourceRoots"]:
        root_path = root / Path(str(source_root))
        root_relative = _relative(source_root, field="kilderod")
        if not root_path.is_dir():
            raise ExportError(f"Kilderoden mangler: {root_relative}")
        _check_parent_chain(root_path, root)
        for current, dirnames, filenames in os.walk(root_path, topdown=True, followlinks=False):
            current_path = Path(current)
            dirnames.sort()
            filenames.sort()
            kept_dirs = []
            for name in dirnames:
                child = current_path / name
                relative = _posix(child.relative_to(root))
                if _is_link_or_reparse(child):
                    if _is_excluded(relative, policy):
                        continue
                    raise ExportError(f"Symlink/reparsepunkt er ikke tilladt i kildeudgivelsen: {child}")
                _lstat_checked(child)
                if not _is_excluded(relative, policy):
                    kept_dirs.append(name)
            dirnames[:] = kept_dirs
            for name in filenames:
                path = current_path / name
                relative = _posix(path.relative_to(root))
                if _is_link_or_reparse(path):
                    if _is_excluded(relative, policy) and relative not in explicit:
                        continue
                    raise ExportError(f"Symlink/reparsepunkt er ikke tilladt i kildeudgivelsen: {path}")
                _lstat_checked(path, reject_hardlink=True)
                if relative in explicit or _is_excluded(relative, policy):
                    continue
                suffix = path.suffix.lower()
                if path.name not in include_names and suffix not in include_ext:
                    raise ExportError(f"Ukendt valgt kildefil; tilføj den eksplicit eller ekskludér den: {relative}")
                add(relative, path)

    total = sum(item.size for item in files.values())
    if total > max_total:
        raise ExportError(f"Kildetræet overskrider totalgrænsen på {max_total} byte.")
    if not files:
        raise ExportError("Allowlisten valgte ingen kildefiler.")
    return [files[key] for key in sorted(files)]


def _slug(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value).strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    return re.sub(r"\s+", "-", value)


def _anchors(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    return {_slug(match.group(1)) for match in HEADING.finditer(text)} | set(
        re.findall(r"\bid=[\"']([^\"']+)[\"']", text)
    )


def _check_links(root: Path, files: list[SourceFile]) -> None:
    included = {item.relative for item in files}
    errors = []
    for item in files:
        if Path(item.relative).suffix.lower() != ".md":
            continue
        try:
            text = item.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ExportError(f"Markdown kan ikke læses: {item.relative}") from error
        for raw in LINK.findall(text):
            target = raw.strip().split(" ", 1)[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("//"):
                continue
            fragment = unquote(parsed.fragment)
            relative_target = unquote(parsed.path)
            if relative_target:
                candidate = (item.path.parent / Path(relative_target)).resolve()
                if not _within(candidate, root):
                    raise ExportError(f"Markdownlink går uden for eksporten: {item.relative} -> {target}")
                relative = _posix(candidate.relative_to(root))
                if relative not in included and not (candidate.is_dir() and any(
                        name.startswith(relative + '/') for name in included)):
                    errors.append(f"Markdownlink peger ikke på en eksporteret fil: {item.relative} -> {target}")
                    continue
                anchor_path = candidate
            else:
                anchor_path = item.path
            if fragment and fragment not in _anchors(anchor_path):
                errors.append(f"Uopløst Markdown-anker: {item.relative} -> {target}")
    if errors:
        raise ExportError('\n'.join(errors))


def _validate_policy(root: Path, policy: dict, files: list[SourceFile]) -> None:
    root = root.resolve()
    included = {item.relative for item in files}
    if policy.get("credit") != CREDIT or "CREDITS.md" not in included:
        raise ExportError("Kildeudgivelsen skal bevare den obligatoriske Klostermarksskolen-kreditering og CREDITS.md.")
    for required in policy["requiredDocs"]:
        required = _relative(required, field="påkrævet dokument")
        if required not in included:
            raise ExportError(f"Påkrævet dokument er ikke med i eksporten: {required}")
    credits = (root / "CREDITS.md").read_text(encoding="utf-8")
    if CREDIT not in credits:
        raise ExportError("CREDITS.md mangler den obligatoriske Klostermarksskolen-kreditering.")
    if AUTHOR_CREDIT not in credits:
        raise ExportError("CREDITS.md mangler den obligatoriske kreditering af Mathias Kusk Damgaard.")
    _check_links(root, files)


def _safe_output(path_value: str, root: Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    # Validate the lexical parent chain before resolving it.  Otherwise a
    # pre-existing junction/symlink could redirect an apparently new output
    # into an unrelated tree between the safety check and mkdir/replace.
    lexical = path.absolute()
    lexical_parent = lexical.parent
    while not lexical_parent.exists():
        if lexical_parent == lexical_parent.parent:
            raise ExportError("Outputforælderen kan ikke findes.")
        lexical_parent = lexical_parent.parent
    _check_parent_chain(lexical_parent, Path(lexical.anchor or lexical_parent))
    _lstat_checked(lexical_parent)
    if lexical.is_symlink() or _windows_reparse(lexical):
        raise ExportError("Outputstien må ikke være et symlink/reparsepunkt.")
    path = lexical.resolve(strict=False)
    if _within(path, root) or path == root:
        raise ExportError("Eksportens output skal ligge uden for projektroden.")
    existing_parent = path.parent
    while not existing_parent.exists():
        if existing_parent == existing_parent.parent:
            raise ExportError("Outputforælderen kan ikke findes.")
        existing_parent = existing_parent.parent
    _check_parent_chain(existing_parent, Path(existing_parent.anchor or existing_parent))
    if path.exists() or path.is_symlink():
        raise ExportError("Outputmappen findes allerede; overwrite er ikke tilladt.")
    return path


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _publish(stage: Path, output: Path) -> None:
    """Atomic no-replace publish, including a target created after preflight."""
    _check_parent_chain(output, Path(output.anchor))
    _lstat_checked(output.parent)
    _check_parent_chain(stage, Path(stage.anchor))
    _lstat_checked(stage)
    if os.name == 'nt':
        os.rename(stage, output)  # Windows rename refuses an existing target.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise ExportError('Platformen mangler atomisk publicering uden overskrivning.')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(output), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(output))


def _receipt_path(stage: Path) -> Path:
    return stage / RECEIPT_NAME


def _write_receipt(stage: Path, value: dict) -> None:
    _write_bytes(_receipt_path(stage), (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def _copy_checked(item: SourceFile, stage: Path, limit: int) -> None:
    # Re-read and hash the source after preflight. A concurrent source edit is
    # an audit failure, never a reason to publish mixed source generations.
    image_pin = item.sha256 if item.path.suffix.lower() in {'.png', '.webp','.ttf'} else None
    size, digest, _ = _read_source(item.path, limit, image_pin)
    if size != item.size or digest != item.sha256:
        raise ExportError(f"Kilden ændrede sig efter preflight: {item.relative}")
    destination = stage / Path(item.relative)
    temp = destination.with_name(destination.name + ".tmp")
    temp.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    written = 0
    with item.path.open("rb") as source, temp.open("wb") as target:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            hasher.update(chunk)
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    if written != item.size or hasher.hexdigest() != item.sha256:
        temp.unlink(missing_ok=True)
        raise ExportError(f"Hashkontrollen fejlede under kopiering: {item.relative}")
    os.replace(temp, destination)
    try:
        os.chmod(destination, item.mode)
    except OSError as error:
        raise ExportError(f"Filrettigheder kunne ikke bevares: {item.relative}") from error
    _, copied_digest, _ = _read_source(destination, limit, image_pin)
    if copied_digest != item.sha256:
        raise ExportError(f"Hashkontrollen fejlede efter kopiering: {item.relative}")


def export_source(root: Path, policy: dict, output_value: str) -> dict:
    # This call deliberately happens before creating output or staging paths.
    files = _collect(root, policy)
    _validate_policy(root, policy, files)
    output = _safe_output(output_value, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    # mkdir may have raced with creation of a redirecting ancestor. Validate
    # the lexical path again before writing even a staging receipt.
    output = _safe_output(output_value, root)
    _check_parent_chain(output, Path(output.anchor))
    _lstat_checked(output.parent)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.public-source-", dir=output.parent))
    _check_parent_chain(stage, Path(stage.anchor))
    _lstat_checked(stage)
    policy_digest = hashlib.sha256(
        (json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).hexdigest()
    receipt = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "public-source-export",
        "state": "staging",
        "target": str(output),
        "stagingRoot": str(stage),
        "files": [],
        "plannedFiles": [item.relative for item in files],
    }
    _write_receipt(stage, receipt)
    try:
        max_file = int(policy.get("limits", {}).get("maxFileBytes", 64 * 1024 * 1024))
        for item in files:
            _copy_checked(item, stage, max_file)
            receipt["files"].append(item.relative)
            _write_receipt(stage, receipt)
        manifest = {
            "schemaVersion": SCHEMA_VERSION,
            "project": policy["project"],
            "credit": policy["credit"],
            "generator": {"path": "scripts/public_source.py", "version": GENERATOR_VERSION},
            "sourcePolicy": "deployment/public-source-manifest.json",
            "sourcePolicySha256": policy_digest,
            "files": [
                {
                    "path": item.relative,
                    "size": item.size,
                    "sha256": item.sha256,
                    "mode": format(item.mode, "04o"),
                }
                for item in files
            ],
        }
        _write_bytes(stage / MANIFEST_NAME, (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        receipt["state"] = "ready"
        _write_receipt(stage, receipt)
        if _collect(root, policy) != files:
            raise ExportError('Kildetræet ændrede sig under eksporten.')
        # The receipt is staging metadata only and is never published.
        _receipt_path(stage).unlink()
        _publish(stage, output)
        return {"output": str(output), "files": len(files), "bytes": sum(item.size for item in files)}
    except Exception:
        # Preserve the exact owned staging directory and receipt for a later
        # explicit cleanup; never remove a path guessed from user input.
        try:
            receipt["state"] = "failed"
            _write_receipt(stage, receipt)
        except OSError:
            pass
        raise


def cleanup_staging(path_value: str, root: Path) -> dict:
    requested = Path(path_value).expanduser()
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    requested = requested.absolute()
    requested_parent = requested.parent
    while not requested_parent.exists():
        if requested_parent == requested_parent.parent:
            raise ExportError("Stagingforælderen kan ikke findes.")
        requested_parent = requested_parent.parent
    _check_parent_chain(requested_parent, Path(requested.anchor or requested_parent))
    _lstat_checked(requested_parent)
    if requested.is_symlink() or _windows_reparse(requested):
        raise ExportError("Stagingmappen må ikke være et symlink/reparsepunkt.")
    stage = requested.resolve(strict=True)
    if _within(stage, root) or stage == root or not stage.is_dir():
        raise ExportError("Stagingmappen skal være en ekstern mappe.")
    _check_parent_chain(stage, Path(stage.anchor or stage))
    receipt_path = stage / RECEIPT_NAME
    _lstat_checked(receipt_path, reject_hardlink=True)
    if not receipt_path.is_file():
        raise ExportError("Stagingmappen mangler sin ejerskabskvittering.")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExportError("Stagingkvitteringen kan ikke læses.") from error
    if receipt.get("kind") != "public-source-export" or Path(receipt.get("stagingRoot", "")).resolve() != stage:
        raise ExportError("Stagingkvitteringen ejer ikke denne mappe.")
    target = Path(receipt.get('target', '')).absolute()
    if (receipt.get('schemaVersion') != SCHEMA_VERSION or receipt.get('state') not in {'staging', 'failed', 'ready'}
            or target.parent != stage.parent or not stage.name.startswith('.' + target.name + '.public-source-')):
        raise ExportError('Stagingkvitteringen har ikke en sikker målafgrænsning.')
    # Refuse deletion of unknown user additions or redirected descendants.
    planned = receipt.get('plannedFiles')
    if type(planned) is not list or len(planned) > 200_000 or not planned:
        raise ExportError('Stagingkvitteringen mangler en afgrænset filliste.')
    allowed = {_relative(name, field='stagingfil') for name in planned}
    allowed_directories = {parent.as_posix() for name in allowed
                           for parent in PurePosixPath(name).parents if parent != PurePosixPath('.')}
    for current, directories, filenames in os.walk(stage, followlinks=False):
        for name in [*directories, *filenames]:
            child = Path(current) / name
            _lstat_checked(child, reject_hardlink=True)
            if child.is_dir() and child.relative_to(stage).as_posix() not in allowed_directories:
                raise ExportError('Stagingmappen indeholder en ukendt mappe; oprydning afvist.')
            if child.is_file():
                relative = child.relative_to(stage).as_posix()
                if relative not in {RECEIPT_NAME, MANIFEST_NAME} and relative not in allowed and not (
                        relative.endswith('.tmp') and relative[:-4] in allowed):
                    raise ExportError('Stagingmappen indeholder en ukendt fil; oprydning afvist.')
    shutil.rmtree(stage)
    return {"cleaned": str(stage)}


def audit(root: Path, policy: dict) -> dict:
    files = _collect(root, policy)
    _validate_policy(root, policy, files)
    return {"files": len(files), "bytes": sum(item.size for item in files), "credit": policy["credit"]}


def privacy_report(root: Path, policy: dict) -> dict:
    """Exhaustive diagnostic report; never an alternate export path."""
    files=_collect(root,policy,content_checks=False)
    findings=[]
    for item in files:
        if item.path.suffix.lower() in {'.png','.webp','.ttf'} or item.path.name in SOURCE_ARCHIVES: continue
        findings.extend(privacy_findings(item.relative,item.path.read_bytes(),policy))
    return {'schemaVersion':1,'selectedFiles':len(files),'findings':findings,'clean':not findings}


def _check_generated_manifest(path: Path, policy: dict, data: bytes | None = None) -> None:
    if data is None:
        _lstat_checked(path,reject_hardlink=True)
        data=path.read_bytes()
    if len(data)>32*1024*1024: raise ExportError('Public-source-manifesten er for stor.')
    try: value=json.loads(_decode_text(data))
    except ValueError as error: raise ExportError('Ugyldig public-source-manifest JSON.') from error
    expected={'schemaVersion','project','credit','generator','sourcePolicy','sourcePolicySha256','files'}
    if (not isinstance(value,dict) or set(value)!=expected or value['schemaVersion']!=SCHEMA_VERSION or
            value['project']!=policy['project'] or value['credit']!=CREDIT or
            value['sourcePolicy']!='deployment/public-source-manifest.json' or
            not re.fullmatch('[a-f0-9]{64}',str(value['sourcePolicySha256'])) or
            not isinstance(value['generator'],dict) or set(value['generator'])!={'path','version'} or
            value['generator']['path']!='scripts/public_source.py' or type(value['generator']['version']) is not int or value['generator']['version']<1 or
            not isinstance(value['files'],list) or len(value['files'])>200_000):
        raise ExportError('Ugyldigt public-source-manifest schema.')
    seen=set();explicit=_explicit_set(policy)
    for item in value['files']:
        if (not isinstance(item,dict) or set(item)!={'path','size','sha256','mode'} or type(item['size']) is not int or item['size']<0 or
                not re.fullmatch('[a-f0-9]{64}',str(item['sha256'])) or not re.fullmatch('[0-7]{4}',str(item['mode']))):
            raise ExportError('Ugyldig public-source-manifest filpost.')
        relative=_relative(item['path'])
        if relative.casefold() in seen: raise ExportError('Dubletsti i public-source-manifest.')
        seen.add(relative.casefold())
        if relative not in explicit and (_is_excluded(relative,policy) or not any(relative.startswith(_relative(r)+'/') for r in policy['sourceRoots'])):
            raise ExportError('Public-source-manifest indeholder en sti uden for public allowlisten.')
    _check_content(path,data,policy,relative=MANIFEST_NAME)
    # Deliberately do not compare historical content hashes with current source:
    # a legitimate subsequent Git commit updates source without rewriting a
    # release snapshot. The current tracked working files are audited below.


def _git_index_contents(root: Path, policy: dict) -> list[tuple[str,bytes]]:
    """Read exact staged blobs in two bounded batches; no path interpolation."""
    def git(arguments,stdin=None):
        try: result=subprocess.run(['git','-C',str(root),*arguments],input=stdin,capture_output=True,timeout=60,check=False)
        except (OSError,subprocess.TimeoutExpired) as error: raise ExportError('Git-indekset kan ikke læses.') from error
        if result.returncode: raise ExportError('Repository-kontrollen kræver et læsbart Git-indeks.')
        return result.stdout
    records=[]
    for raw in git(['ls-files','--cached','--stage','-z']).split(b'\0'):
        if not raw: continue
        try: metadata,name=raw.split(b'\t',1);mode,oid,stage=metadata.split()
        except ValueError as error: raise ExportError('Ugyldig Git-indekspost.') from error
        if mode not in (b'100644',b'100755') or stage!=b'0' or not re.fullmatch(rb'[a-f0-9]{40}(?:[a-f0-9]{24})?',oid):
            raise ExportError('Git-indekset indeholder link, submodule eller uafklaret merge.')
        records.append((_relative(os.fsdecode(name)),oid))
    if not records: raise ExportError('Git-indekset er tomt.')
    request=b''.join(oid+b'\n' for _,oid in records)
    checks=git(['cat-file','--batch-check=%(objectname) %(objecttype) %(objectsize)'],request).splitlines()
    if len(checks)!=len(records): raise ExportError('Git-indeksets objekter er ufuldstændige.')
    sizes=[]
    max_file=int(policy.get('limits',{}).get('maxFileBytes',64*1024*1024))
    for check,(_,oid) in zip(checks,records):
        try: actual,kind,size=check.split();size=int(size)
        except ValueError as error: raise ExportError('Ugyldigt Git-objekt.') from error
        if actual!=oid or kind!=b'blob' or not 0<=size<=max_file: raise ExportError('Git-objektets type/størrelse er ikke tilladt.')
        sizes.append(size)
    if sum(sizes)>int(policy.get('limits',{}).get('maxTotalBytes',512*1024*1024)): raise ExportError('Git-indekset overskrider public totalgrænsen.')
    content=git(['cat-file','--batch'],request);offset=0;result=[]
    for (name,oid),size in zip(records,sizes):
        end=content.find(b'\n',offset)
        if end<0 or content[offset:end]!=oid+b' blob '+str(size).encode(): raise ExportError('Git-objektsvaret er ugyldigt.')
        start=end+1;data=content[start:start+size];offset=start+size+1
        if len(data)!=size or content[offset-1:offset]!=b'\n': raise ExportError('Git-objektets data er afbrudt.')
        result.append((name,data))
    if offset!=len(content): raise ExportError('Uventede ekstra Git-objektdata.')
    return result


def audit_repository(root: Path, policy: dict) -> dict:
    """Every tracked file must be both selected AND content-audited."""
    root=root.resolve()
    index=_git_index_contents(root,policy)
    names=[name for name,_ in index]
    files=_collect(root,policy)
    selected={item.relative for item in files}
    seen=set()
    for name in names:
        relative=_relative(name,field='Git-sti')
        if relative.casefold() in seen: raise ExportError('Flertydig/dubleret Git-sti.')
        seen.add(relative.casefold())
        if relative!=MANIFEST_NAME and relative not in selected:
            # Do not echo an accidentally staged secret's sensitive filename.
            raise ExportError('Git-indekset indeholder en fil uden for public allowlisten.')
        _check_parent_chain(root/relative,root)
        _lstat_checked(root/relative,reject_hardlink=True)
    if MANIFEST_NAME in names: _check_generated_manifest(root/MANIFEST_NAME,policy)
    images=_public_image_map(policy)
    fonts=_public_font_map(policy)
    for name,data in index:
        if name==MANIFEST_NAME: _check_generated_manifest(root/name,policy,data)
        elif Path(name).suffix.lower() in {'.png','.webp'}:
            if hashlib.sha256(data).hexdigest()!=images.get(name): raise ExportError('Git-indeksets illustration matcher ikke public pin.')
        elif Path(name).suffix.lower()=='.ttf':
            if hashlib.sha256(data).hexdigest()!=fonts.get(name): raise ExportError('Git-indeksets font matcher ikke public pin.')
        elif Path(name).name in SOURCE_ARCHIVES:
            if hashlib.sha256(data).hexdigest()!=SOURCE_ARCHIVES[Path(name).name]: raise ExportError('Git-indeksets kildearkiv matcher ikke public pin.')
        else: _check_content(Path(name),data,policy,relative=name)
    _validate_policy(root,policy,files)
    return {'trackedFiles':len(names),'auditedFiles':len(files),'credit':policy['credit']}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="audit allowlist, secrets, credits and links without writing")
    parser.add_argument('--check-repository',action='store_true',help='require every Git-tracked file to pass the public allowlist/content audit')
    parser.add_argument('--privacy-report',metavar='PATH',help='write only file/line/category findings; does not export or publish')
    parser.add_argument("--output", help="new output directory outside the repository")
    parser.add_argument("--cleanup-staging", metavar="PATH", help="remove only a staging directory with its own receipt")
    args = parser.parse_args(argv)
    try:
        root = _repo_root()
        policy = _load_policy(root)
        if sum(bool(value) for value in (args.check, args.check_repository,args.privacy_report,args.output, args.cleanup_staging)) != 1:
            parser.error("Vælg præcis én kontrol, rapport, eksport eller stagingoprydning.")
        if args.privacy_report:
            result=privacy_report(root,policy)
            destination=Path(args.privacy_report).absolute()
            if _within(destination,root) and not _is_excluded(destination.relative_to(root).as_posix(),policy):
                raise ExportError('Privatdiagnostik må ikke skrives til en public kildesti.')
            if destination.exists(): raise ExportError('Rapportfilen findes allerede; overskrivning er afvist.')
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'clean':result['clean'],'selectedFiles':result['selectedFiles'],'findings':len(result['findings'])}))
            return 0 if result['clean'] else 2
        result = audit_repository(root,policy) if args.check_repository else audit(root, policy) if args.check else (
            cleanup_staging(args.cleanup_staging, root) if args.cleanup_staging
            else export_source(root, policy, args.output)
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ExportError as error:
        print(f"public-source: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"public-source: filesystemfejl: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
