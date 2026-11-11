#!/usr/bin/env python3
"""Provision local TLS material and per-computer credentials for Elevhub.

The program deliberately delegates all public-key operations to the system
``openssl`` executable.  It never prints a key or token.  ``init`` only works
with two new directories; device credentials are added or rotated explicitly
by separate subcommands.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows refuses secret operations.
    fcntl = None


class Sikkerhedsfejl(Exception):
    """An expected, user-facing provisioning error."""


DEVICE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


def _require_linux() -> None:
    # Windows ACLs differ by installation and are easy to get wrong.  Refuse
    # to create secrets there; WSL's Linux home has well-defined mode bits.
    if os.name == "nt":
        raise Sikkerhedsfejl(
            "Sikker filoprettelse er ikke aktiveret på Windows; kør dette i Debian WSL "
            "i en Linux-home og kopiér kun efter en manuel sikkerhedskontrol."
        )


def _require_openssl() -> str:
    executable = shutil.which("openssl")
    if executable is None:
        raise Sikkerhedsfejl("openssl blev ikke fundet; installér openssl i Debian WSL.")
    return executable


def _validate_name(name: str, label: str = "enhedsnavn") -> str:
    if not isinstance(name, str) or not DEVICE_RE.fullmatch(name):
        raise Sikkerhedsfejl(
            f"Ugyldigt {label}: brug kun små ASCII-bogstaver, tal og bindestreg "
            "(1-63 tegn; bindestreg må ikke stå først eller sidst)."
        )
    return name


def _validate_sans(values: Iterable[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw)
        if not value or value != value.strip() or value in seen:
            raise Sikkerhedsfejl("SAN skal være unikke, ikke-tomme DNS-navne eller IP-adresser.")
        seen.add(value)
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if len(value) > 253 or ".." in value or not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value
            ):
                raise Sikkerhedsfejl(f"Ugyldig DNS-SAN: {value!r}") from None
            labels = value.split(".")
            if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
                raise Sikkerhedsfejl(f"Ugyldig DNS-SAN: {value!r}")
            result.append(("DNS", value))
        else:
            result.append(("IP", value))
    if not result:
        raise Sikkerhedsfejl("Mindst én --san DNS/IP-adresse er påkrævet.")
    return result


def _mode(path: Path, mode: int, *, kind: str = "file") -> None:
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            raise Sikkerhedsfejl(f"Symlinks afvises: {path}.")
        if kind == "dir" and not stat.S_ISDIR(info.st_mode):
            raise Sikkerhedsfejl(f"Forventede en mappe: {path}.")
        if kind == "file" and not stat.S_ISREG(info.st_mode):
            raise Sikkerhedsfejl(f"Forventede en almindelig fil: {path}.")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise Sikkerhedsfejl(f"Filen ejes ikke af den aktuelle Linux-bruger: {path}.")
        os.chmod(path, mode, follow_symlinks=False)
        checked = os.lstat(path)
        if stat.S_IMODE(checked.st_mode) != mode or checked.st_uid != info.st_uid:
            raise Sikkerhedsfejl(f"Linux-rettigheder {oct(mode)} kunne ikke håndhæves på {path}.")
    except Sikkerhedsfejl:
        raise
    except OSError as exc:
        raise Sikkerhedsfejl(f"Kunne ikke sætte sikre rettigheder på {path}.") from exc


def _new_dir(path: Path, *, label: str) -> None:
    if os.path.lexists(path):
        raise Sikkerhedsfejl(f"{label} findes allerede; overskrivning er nægtet.")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise Sikkerhedsfejl(f"{label} findes allerede; overskrivning er nægtet.") from exc
    except OSError as exc:
        raise Sikkerhedsfejl(f"Kunne ikke oprette {label}.") from exc
    _mode(path, 0o700, kind="dir")


def _ensure_separate(first: Path, second: Path) -> None:
    first_abs, second_abs = first.resolve(), second.resolve()
    if first_abs == second_abs or first_abs in second_abs.parents or second_abs in first_abs.parents:
        raise Sikkerhedsfejl("output-dir og issuer-dir skal være separate mapper.")


def _run_openssl(openssl: str, args: list[str], *, cwd: Path | None = None) -> None:
    try:
        completed = subprocess.run(
            [openssl, *args],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except OSError as exc:
        raise Sikkerhedsfejl("Kunne ikke køre openssl.") from exc
    if completed.returncode != 0:
        # Never relay openssl diagnostics: a future command/configuration must
        # not accidentally put sensitive values into terminal logs.
        raise Sikkerhedsfejl("openssl kunne ikke gennemføre sikkerhedsoperationen.")


def _write_private(path: Path, content: str) -> None:
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        _mode(path, 0o600)
    except FileExistsError as exc:
        raise Sikkerhedsfejl(f"Filen {path} findes allerede; overskrivning er nægtet.") from exc
    except OSError as exc:
        raise Sikkerhedsfejl(f"Kunne ikke skrive {path}.") from exc


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    fd: int | None = None
    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        temporary = Path(temporary_name)
        _mode(temporary, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _mode(path, mode)
    except OSError as exc:
        raise Sikkerhedsfejl(f"Kunne ikke skrive atomisk til {path}.") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise Sikkerhedsfejl(f"Kunne ikke læse registryfilen {path}.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("enheder"), dict):
        raise Sikkerhedsfejl("Registryfilen har et ugyldigt format.")
    used_hashes: set[str] = set()
    for name, entry in value["enheder"].items():
        _validate_name(name)
        if (
            not isinstance(entry, dict)
            or set(entry) != {"token_sha256", "ui_token_sha256"}
            or any(not isinstance(entry[key], str) or not re.fullmatch(r"[0-9a-f]{64}", entry[key]) for key in entry)
        ):
            raise Sikkerhedsfejl("Registryfilen har et ugyldigt credential-format.")
        if any(entry[key] in used_hashes for key in ("token_sha256", "ui_token_sha256")):
            raise Sikkerhedsfejl("Registryfilen har genbrugte credentials.")
        used_hashes.update(entry[key] for key in ("token_sha256", "ui_token_sha256"))
    return value


def _registry_content(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _tokens(registry: dict[str, Any]) -> tuple[str, str]:
    used = {v[key] for entry in registry["enheder"].values() for v in [entry] for key in ("token_sha256", "ui_token_sha256")}
    for _ in range(100):
        token, ui_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        if token != ui_token and _hash(token) not in used and _hash(ui_token) not in used:
            return token, ui_token
    raise Sikkerhedsfejl("Kunne ikke generere unikke credentials.")


def _client_content(name: str, token: str, ui_token: str) -> str:
    return json.dumps({"computer": name, "token": token, "ui_token": ui_token}, ensure_ascii=False, indent=2) + "\n"


def _layout(output: Path) -> tuple[Path, dict[str, Any]]:
    if not output.is_dir():
        raise Sikkerhedsfejl(f"Outputmappen findes ikke: {output}")
    _mode(output, 0o700, kind="dir")
    devices = output / "enheder"
    if not devices.is_dir():
        raise Sikkerhedsfejl(f"Mangler enhedsmappe: {devices}")
    _mode(devices, 0o700, kind="dir")
    registry_path = output / "enheder.json"
    if not registry_path.is_file():
        raise Sikkerhedsfejl(f"Mangler registryfil: {registry_path}")
    _mode(registry_path, 0o600)
    return registry_path, _json(registry_path)


@contextmanager
def _registry_lock(output: Path):
    """Serialize credential updates, waiting at most five seconds."""
    if fcntl is None:
        raise Sikkerhedsfejl("Linux flock er påkrævet til credential-operationer.")
    lock_path = output / ".enheder.lock"
    try:
        if os.path.lexists(lock_path) and stat.S_ISLNK(os.lstat(lock_path).st_mode):
            raise Sikkerhedsfejl(f"Symlink afvises: {lock_path}.")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
        lock_file = os.fdopen(fd, "r+", encoding="ascii")
        try:
            _mode(lock_path, 0o600)
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise Sikkerhedsfejl("Enhedsregistry er optaget; prøv igen.")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
    except Sikkerhedsfejl:
        raise
    except OSError as exc:
        raise Sikkerhedsfejl("Kunne ikke låse enhedsregistry sikkert.") from exc


def init_security(output_dir: Path, issuer_dir: Path, sans: Iterable[str], common_name: str | None = None) -> list[Path]:
    """Create a complete fresh bundle and return only the created paths."""
    _require_linux()
    openssl = _require_openssl()
    output, issuer = Path(output_dir), Path(issuer_dir)
    _ensure_separate(output, issuer)
    san_pairs = _validate_sans(sans)
    cn = common_name or san_pairs[0][1]
    if not cn or any(ch in cn for ch in "\r\n"):
        raise Sikkerhedsfejl("Ugyldigt common-name.")
    # A CN is informational here (SAN is authoritative), but still validate it
    # as one ordinary DNS/IP value so it cannot alter OpenSSL's subject syntax.
    _validate_sans([cn])
    # Check both targets before creating either one, so a pre-existing issuer
    # cannot leave a surprising half-created runtime directory behind.
    if output.exists() or issuer.exists():
        raise Sikkerhedsfejl("output-dir og issuer-dir skal begge være nye mapper; overskrivning er nægtet.")
    _new_dir(output, label="output-dir")
    try:
        _new_dir(issuer, label="issuer-dir")
        devices = output / "enheder"
        _new_dir(devices, label="enhedsmappen")
        ca_key = issuer / "ca.key"
        ca_crt = output / "ca.crt"
        server_key = output / "server.key"
        server_crt = output / "server.crt"
        registry = output / "enheder.json"
        _run_openssl(
            openssl,
            [
                "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-batch",
                "-keyout", str(ca_key), "-out", str(ca_crt), "-days", "3650",
                "-sha256", "-subj", "/CN=Elevhub local CA",
                "-addext", "basicConstraints=critical,CA:TRUE,pathlen:1",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            ],
        )
        _mode(ca_key, 0o600)
        _mode(ca_crt, 0o644)
        with tempfile.TemporaryDirectory(dir=str(issuer), prefix=".udstedelse-") as work_name:
            work = Path(work_name)
            csr, ext, serial = work / "server.csr", work / "server.ext", work / "ca.srl"
            alt_lines = ["subjectAltName = @alt_names", "basicConstraints = critical,CA:FALSE", "keyUsage = critical,digitalSignature,keyEncipherment", "extendedKeyUsage = serverAuth", "", "[alt_names]"]
            alt_lines.extend(f"{kind}.{index} = {value}" for index, (kind, value) in enumerate(san_pairs, 1))
            _write_private(ext, "\n".join(alt_lines) + "\n")
            _run_openssl(openssl, ["req", "-new", "-newkey", "rsa:3072", "-nodes", "-batch", "-keyout", str(server_key), "-out", str(csr), "-subj", f"/CN={cn}"])
            _run_openssl(openssl, ["x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial", "-CAserial", str(serial), "-out", str(server_crt), "-days", "825", "-sha256", "-extfile", str(ext)])
            _run_openssl(openssl, ["verify", "-CAfile", str(ca_crt), str(server_crt)])
        _mode(server_key, 0o600)
        _mode(server_crt, 0o644)
        _atomic_write(registry, _registry_content({"enheder": {}}), 0o600)
        return [ca_crt, server_crt, server_key, registry, ca_key]
    except Exception:
        # Only newly-created directories are ours.  Keep them for diagnosis;
        # never remove a pre-existing user directory.
        raise


def add_device(output_dir: Path, name: str) -> list[Path]:
    _require_linux()
    _validate_name(name)
    output = Path(output_dir)
    _layout(output)  # Validate the directory before opening its lock file.
    with _registry_lock(output):
        registry_path, registry = _layout(output)
        client = output / "enheder" / f"{name}.json"
        if name in registry["enheder"] or os.path.lexists(client):
            raise Sikkerhedsfejl("Enheden findes allerede; brug rotate-device med --confirm for rotation.")
        token, ui_token = _tokens(registry)
        staged = False
        old_registry = registry_path.read_text(encoding="utf-8")
        try:
            _atomic_write(client, _client_content(name, token, ui_token), 0o600)
            staged = True
            registry["enheder"][name] = {"token_sha256": _hash(token), "ui_token_sha256": _hash(ui_token)}
            _atomic_write(registry_path, _registry_content(registry), 0o600)
        except Exception:
            # Restore the old registry even if replace() succeeded before a
            # post-write mode check failed, then remove the new client file.
            try:
                _atomic_write(registry_path, old_registry, 0o600)
            except Exception as restore_error:
                raise Sikkerhedsfejl("Kunne ikke genoprette registry efter fejl; credentials er ikke godkendt.") from restore_error
            if staged:
                try:
                    client.unlink()
                except OSError as exc:
                    raise Sikkerhedsfejl("Kunne ikke rydde nye credentials efter fejl.") from exc
            raise
        return [client, registry_path]


def rotate_device(output_dir: Path, name: str, confirm: bool = False) -> list[Path]:
    _require_linux()
    _validate_name(name)
    if not confirm:
        raise Sikkerhedsfejl("Rotation kræver eksplicit --confirm.")
    output = Path(output_dir)
    _layout(output)
    with _registry_lock(output):
        registry_path, registry = _layout(output)
        client = output / "enheder" / f"{name}.json"
        if name not in registry["enheder"] or not client.is_file():
            raise Sikkerhedsfejl("Enheden findes ikke; rotation nægtes.")
        _mode(client, 0o600)
        old_client = client.read_text(encoding="utf-8")
        old_registry = registry_path.read_text(encoding="utf-8")
        token, ui_token = _tokens(registry)
        registry["enheder"][name] = {"token_sha256": _hash(token), "ui_token_sha256": _hash(ui_token)}
        try:
            _atomic_write(client, _client_content(name, token, ui_token), 0o600)
            _atomic_write(registry_path, _registry_content(registry), 0o600)
        except Exception:
            try:
                _atomic_write(registry_path, old_registry, 0o600)
                _atomic_write(client, old_client, 0o600)
            except Exception as restore_error:
                raise Sikkerhedsfejl("Kunne ikke genoprette gamle credentials efter rotationsfejl.") from restore_error
            raise
        return [client, registry_path]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opret Elevhub lokal CA, servercertifikat og enhedscredentials.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="opret en ny sikkerhedsmappe")
    init.add_argument("--output-dir", type=Path, required=True)
    init.add_argument("--issuer-dir", type=Path, required=True)
    init.add_argument("--san", action="append", required=True, help="DNS-navn eller IP; gentag for flere SAN")
    init.add_argument("--common-name")
    add = commands.add_parser("add-device", help="opret credentials for en ny enhed")
    add.add_argument("--output-dir", type=Path, required=True)
    add.add_argument("--name", "--computer", dest="name", required=True)
    rotate = commands.add_parser("rotate-device", help="rotér credentials for en eksisterende enhed")
    rotate.add_argument("--output-dir", type=Path, required=True)
    rotate.add_argument("--name", "--computer", dest="name", required=True)
    rotate.add_argument("--confirm", action="store_true", help="bekræft eksplicit rotation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            paths = init_security(args.output_dir, args.issuer_dir, args.san, args.common_name)
        elif args.command == "add-device":
            paths = add_device(args.output_dir, args.name)
        else:
            paths = rotate_device(args.output_dir, args.name, args.confirm)
    except (Sikkerhedsfejl, ValueError, OSError) as exc:
        print(f"Fejl: {exc}", file=os.sys.stderr)
        return 2
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
