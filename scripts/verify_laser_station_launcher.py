"""Smoke-test the packaged Windows station launcher without a VM or browser.

The test copies the package to a temporary path, writes a Station config with
an unreachable loopback queue address, and invokes ``open-laserstation.ps1
-NoBrowser``.  The launcher must start the local service, while the owned
Windows job makes cleanup safe when the PowerShell parent exits.
"""

from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.laser_service import ProcessTree


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def powershell() -> str:
    for name in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("PowerShell was not found; run this verifier on Windows.")


def copy_package(package: Path, target: Path) -> None:
    for name in ("app", "runtime", "scripts", "web"):
        shutil.copytree(package / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def request_state(port: int) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/api/station/state")
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    if response.status != 200:
        raise RuntimeError(f"station state returned HTTP {response.status}")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("station state was not a JSON object")
    return value


def cleanup_owned(process, tree) -> None:
    """Close the kill-on-job-close handle, then reap only our PowerShell."""
    if tree is not None:
        tree.close()
    if process is not None and process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def verify(package: Path, output: Path) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("launcher smoke test requires Windows process-job cleanup")
    package = package.resolve()
    if not (package / "runtime" / "python.exe").is_file():
        raise FileNotFoundError(f"Packaged runtime not found: {package}")
    if not (package / "scripts" / "open-laserstation.ps1").is_file():
        raise FileNotFoundError(f"Packaged launcher not found: {package}")
    output = output.resolve()
    checks: list[str] = []
    process = tree = None
    with tempfile.TemporaryDirectory(prefix="laser launcher verifier ") as temp_name:
        temp = Path(temp_name)
        copied = temp / "Laserstation package copy"
        copied.mkdir()
        copy_package(package, copied)
        station_port = free_port()
        queue_port = free_port()
        while queue_port == station_port:
            queue_port = free_port()
        plates = temp / "Laser plates path"
        config = {
            "role": "Station",
            "server": f"http://127.0.0.1:{queue_port}",
            "plates": str(plates),
            "port": station_port,
        }
        config_path = copied / "laser-installation.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        command = [
            powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(copied / "scripts" / "open-laserstation.ps1"), "-NoBrowser",
        ]
        process = subprocess.Popen(
            command,
            cwd=str(copied),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            tree = ProcessTree(process)
            if not tree.handle:
                raise RuntimeError("could not create a Windows job for launcher cleanup")
            deadline = time.monotonic() + 20
            state = None
            while time.monotonic() < deadline:
                try:
                    state = request_state(station_port)
                    break
                except Exception:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=2)
                        raise RuntimeError(f"launcher exited {process.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}")
                    time.sleep(0.2)
            if state is None:
                raise TimeoutError("station did not become ready")
            if state.get("server") != config["server"]:
                raise AssertionError(f"unexpected queue server: {state.get('server')!r}")
            if state.get("loggedIn") is not False:
                raise AssertionError(f"station unexpectedly logged in: {state.get('loggedIn')!r}")
            connection = http.client.HTTPConnection("127.0.0.1", station_port, timeout=2)
            try:
                connection.request("GET", "/")
                page = connection.getresponse()
                body = page.read()
                if page.status != 200 or b'Laserstation' not in body:
                    raise AssertionError('Station page did not return the expected HTML')
            finally:
                connection.close()
            checks.append("local station HTML page returns HTTP 200 while VM is disconnected")
            # The script polls the local endpoint after spawning the station;
            # allow that short hand-off to finish before checking its output.
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                raise AssertionError("open-laserstation.ps1 did not finish after local readiness") from error
            if process.returncode != 0:
                stdout, stderr = process.communicate(timeout=2)
                raise AssertionError(f"launcher exited {process.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}")
            checks.append("launcher starts local station and reports disconnected queue without browser")
            tree.close()
            tree = None
            # The job's kill-on-close limit should have ended the station child.
            for _ in range(20):
                try:
                    request_state(station_port)
                except Exception:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("station child survived owned job cleanup")
            # Confirm redirected log handles are no longer locked before the
            # temporary tree is removed on Windows.
            logs = copied / "logs"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                locked = False
                for log in logs.glob("*") if logs.is_dir() else ():
                    probe = log.with_name(log.name + ".close-probe")
                    try:
                        log.rename(probe)
                        probe.rename(log)
                    except PermissionError:
                        locked = True
                        break
                if not locked:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("station log handles remained locked after process cleanup")
            checks.append("owned process tree is cleaned up before temporary files are removed")
        finally:
            cleanup_owned(process, tree)
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": True,
        "package": str(package),
        "checks": checks,
        "browser_opened": False,
        "desktop_touched": False,
        "vm_contacted": False,
        "physical_laser_started": False,
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
