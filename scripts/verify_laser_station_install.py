"""Verify a Windows Station install using only temporary directories.

This is an installer smoke test, rather than a unit test.  It invokes the
packaged PowerShell installer with ``-NoStart -NoShortcut`` so it never starts
the station, creates a desktop shortcut, contacts a VM, or changes firewall
state.  The package itself must already have been built.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def powershell() -> str:
    for name in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("PowerShell was not found; run this verifier on Windows.")


def run_installer(ps: str, package: Path, role: str, install: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    command = [
        ps,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(package / "scripts" / "install-laser.ps1"),
        "-Role",
        role,
        "-InstallPath",
        str(install),
        "-NoStart",
        "-NoShortcut",
        *extra,
    ]
    return subprocess.run(command, text=True, capture_output=True, timeout=120)


def json_file(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object in {path}")
    return value


def assert_ok(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode:
        raise AssertionError(
            f"{label} failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def assert_rejected(result: subprocess.CompletedProcess[str], label: str, expected: str) -> None:
    if result.returncode == 0:
        raise AssertionError(f"{label} unexpectedly succeeded")
    if expected not in result.stdout + result.stderr:
        raise AssertionError(f"{label} failed for another reason: {result.stdout}\n{result.stderr}")


def make_role_fixture(source_package: Path, root: Path, *, tamper: bool = False) -> Path:
    """Make a tiny package fixture; the installer rejects before copying data."""
    fixture = root / ("tampered-fixture" if tamper else "station-only-fixture")
    (fixture / "scripts").mkdir(parents=True)
    installer = fixture / "scripts" / "install-laser.ps1"
    shutil.copy2(source_package / "scripts" / "install-laser.ps1", installer)
    if tamper:
        installer.write_text(installer.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    marker = {
        "format": "laser-workshop-windows-v1",
        "roles": ["station"],
    }
    (fixture / "laser-package.json").write_text(json.dumps(marker), encoding="utf-8")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if tamper:
        digest = "0" * 64
    manifest = {"scripts/install-laser.ps1": digest}
    (fixture / "integrity.json").write_text(json.dumps(manifest), encoding="utf-8")
    return fixture


def verify(package: Path, output: Path) -> dict:
    package = package.resolve()
    output = output.resolve()
    installer = package / "scripts" / "install-laser.ps1"
    if not installer.is_file() or not (package / "laser-package.json").is_file():
        raise FileNotFoundError(f"Not a laser package: {package}")
    ps = powershell()
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="laser install verifier ") as temp_name:
        temp = Path(temp_name)
        # Every path intentionally includes spaces: this exercises PowerShell
        # argument handling and the quoted config path used by the launcher.
        install = temp / "Laser Station Install"
        plates = temp / "Custom Laser Plates"
        ca_source = temp / "School CA cert.pem"
        beam = temp / "Beam Studio.exe"
        flux = temp / "flux_api.exe"
        ca_source.write_text("test CA\n", encoding="ascii")
        beam.write_text("placeholder", encoding="ascii")
        flux.write_text("placeholder", encoding="ascii")

        first = run_installer(
            ps,
            package,
            "Station",
            install,
            "-Server",
            "http://192.0.2.1:8767",
            "-PlatesPath",
            str(plates),
            "-CAFile",
            str(ca_source),
            "-BeamStudioPath",
            str(beam),
            "-FluxApiPath",
            str(flux),
        )
        assert_ok(first, "fresh Station install")
        config_path = install / "laser-installation.json"
        config = json_file(config_path)
        expected_plates = str(plates.resolve())
        expected_beam = str(beam.resolve())
        expected_flux = str(flux.resolve())
        expected_ca = str((install / "school-ca.pem").resolve())
        for key, expected in {
            "role": "Station",
            "server": "http://192.0.2.1:8767",
            "plates": expected_plates,
            "beam_studio": expected_beam,
            "flux_api": expected_flux,
            "ca_file": expected_ca,
        }.items():
            if config.get(key) != expected:
                raise AssertionError(f"fresh config {key!r}: {config.get(key)!r} != {expected!r}")
        if (install / "school-ca.pem").read_bytes() != ca_source.read_bytes():
            raise AssertionError("CA file was not copied")
        sentinel = plates / "sentinel-keep.bvg"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"must survive update")
        checks.append("fresh install writes custom paths and CA without starting or creating a shortcut")

        # Omit all optional station settings to exercise old-config preservation.
        second = run_installer(ps, package, "Station", install)
        assert_ok(second, "Station update")
        updated = json_file(config_path)
        for key, expected in {
            "server": "http://192.0.2.1:8767",
            "plates": expected_plates,
            "beam_studio": expected_beam,
            "flux_api": expected_flux,
            "ca_file": expected_ca,
        }.items():
            if updated.get(key) != expected:
                raise AssertionError(f"update did not preserve {key}: {updated.get(key)!r}")
        if sentinel.read_bytes() != b"must survive update":
            raise AssertionError("existing plate was overwritten")
        backups = list(install.glob("laser-installation.json.backup-*"))
        if not backups:
            raise AssertionError("update did not create a configuration backup")
        checks.append("update preserves server, plates, CA, Beam Studio, Flux, and existing plate data")

        station_fixture = make_role_fixture(package, temp)
        rejected_role = run_installer(ps, station_fixture, "Server", temp / "server role should not install")
        assert_rejected(rejected_role, "Server role against station-only package", "understotter ikke")
        checks.append("station-only package rejects Server role")

        tampered_fixture = make_role_fixture(package, temp, tamper=True)
        rejected_integrity = run_installer(ps, tampered_fixture, "Station", temp / "tampered install")
        assert_rejected(rejected_integrity, "tampered package", "Pakken er beskadiget")
        checks.append("tampered package is rejected by integrity verification")

    output.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": True,
        "package": str(package),
        "checks": checks,
        "desktop_touched": False,
        "firewall_changed": False,
        "vm_contacted": False,
        "station_started": False,
    }
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True, help="Built package folder")
    parser.add_argument("--output", type=Path, required=True, help="Report directory")
    args = parser.parse_args()
    try:
        report = verify(args.package, args.output)
    except Exception as error:
        report = {"passed": False, "package": str(args.package.resolve()), "error": str(error)}
        args.output.resolve().mkdir(parents=True, exist_ok=True)
        (args.output.resolve() / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
