"""Read-only integrity and role verification for an Elevhub server package."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Ugyldig JSON i {path}")
    return value


def _reparse(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def verify(package_root: Path, role_ip: str) -> dict:
    if _reparse(package_root):
        raise ValueError("Pakkemappen må ikke være et reparse point")
    root = package_root.resolve(strict=True)
    role_ip = bootstrap.validate_role_ip(role_ip)
    manifest = _load(root / "elevhub-package.json")
    if manifest.get("schemaVersion") != 1 or manifest.get("product") != "Elevhub":
        raise ValueError("Ugyldigt pakkemanifest")
    # The package manifest records the canonical build target.  An explicit
    # --role-ip is allowed for a temporary workstation or a later VM; the
    # installer rewrites the runtime configuration and creates a matching TLS
    # certificate before starting services.
    expected_server = None if manifest.get('requiresSchoolSetup') is True else bootstrap.ROLE_IP
    if (manifest.get("role") != "server" or manifest.get("server") != expected_server
            or manifest.get("platform") != "windows-x64"):
        raise ValueError("Pakkens rolle eller serveradresse er forkert")
    if manifest.get("containsCredentials") is not False:
        raise ValueError("Pakken må ikke indeholde legitimationsoplysninger")
    external_print_required = manifest.get("externalPrintBackendRequired", False)
    if type(external_print_required) is not bool:
        raise ValueError("externalPrintBackendRequired skal være boolsk")
    role_files = (
        "runtime/python.exe", "app/background.py", "app/laser_server.py", "app/process_tree.py", "app/paths.py",
        "hub/elevhub.py", "hub/konfiguration.windows.json", "server/bootstrap.py", "server/verify.py",
        "server/provision.py", "server/install.ps1", "server/fjern.ps1", "server/launchers/enroll.ps1",
    )
    missing_role_files = [relative for relative in role_files if not (root / relative).is_file()]
    if missing_role_files:
        raise ValueError("Serverpakken mangler rollefil(er): " + ", ".join(missing_role_files))
    integrity = _load(root / "integrity.json")
    if integrity.get("algorithm") != "sha256":
        raise ValueError("Kun SHA-256-integritetsmanifest understøttes")
    expected = integrity.get("files")
    if not isinstance(expected, dict) or not expected or not all(isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for k, v in expected.items()):
        raise ValueError("integrity.json er tom eller ugyldig")
    actual_files: set[str] = set()
    generation_manifest = root / 'makerspace-release.json'
    generation_release = generation_manifest.is_file()
    for path in root.rglob("*"):
        if _reparse(path):
            raise ValueError(f"Reparse point er ikke tilladt: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative != "integrity.json" and not (generation_release and relative == 'makerspace-release.json'):
                actual_files.add(relative)
    if set(expected) != actual_files:
        missing = sorted(set(expected) - actual_files)
        extra = sorted(actual_files - set(expected))
        raise ValueError(f"Pakkens filliste afviger; mangler={missing}, ekstra={extra}")
    # The public school enrollment service is source code shipped with the
    # package. Only its private registry/state files remain forbidden.
    forbidden = ("security/", "server.key", "enheder.json")
    for relative, digest in expected.items():
        lower = relative.lower()
        if lower.startswith(forbidden[0]) or any(token in lower for token in forbidden[1:]) or lower.endswith('enrollment.json'):
            raise ValueError(f"Sikkerhedsdata må ikke ligge i pakken: {relative}")
        with (root / relative).open("rb") as stream:
            calculated = hashlib.file_digest(stream, "sha256").hexdigest()
        if not hmac.compare_digest(calculated.lower(), digest.lower()):
            raise ValueError(f"Integritetsfejl: {relative}")
    if generation_release:
        # The program manifest includes integrity.json; the reverse reference
        # would create a hash cycle. Still verify every file through both maps.
        sys.path.insert(0, str(root))
        from app.release_compatibility import inspect_program
        release = inspect_program(root)
        if release['data_contract'] != {'hub': 1, 'print': 1, 'laser': 1}:
            raise ValueError('Skolepakkens generationsroller er ufuldstændige')
    # The build package is checked against a harmless existing directory only.
    result = bootstrap.check(root, root, installed=False, role_ip=role_ip)
    result["roleIp"] = role_ip
    result["fileCount"] = len(actual_files)
    result["externalPrintBackendRequired"] = external_print_required
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--role-ip", default=bootstrap.ROLE_IP)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.package_root, args.role_ip), sort_keys=True))
        return 0
    except Exception as exc:
        print(f"Pakkeverifikation fejlede: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
