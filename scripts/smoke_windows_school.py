"""Isolated smoke QA for a staged Windows school package.

The package is run with its embedded Python only.  All services bind to
loopback and all mutable test data lives in a fresh temporary directory on D:.
The script writes a small result JSON and never prints teacher codes or API
payloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class QaFailure(RuntimeError):
    pass


def package_command(package: Path, entry: str, arguments: list[str]) -> list[str]:
    python = str(package / 'runtime/python.exe')
    manifest = package / 'makerspace-release.json'
    marker = json.loads((package / 'elevhub-package.json').read_text(encoding='utf-8'))
    if marker.get('requiresGenerationLayout'):
        with manifest.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        return [python, '-B', '-I', str(package / 'app/generation_runtime.py'),
                '--runtime-root', str(package), '--package-root', str(package),
                '--manifest-sha256', digest, '--entry', entry, '--', *arguments]
    modules = {'print-server': 'app.server', 'design': 'app.laser_server', 'laser-station': 'app.laser_station'}
    return ([python, '-B', str(package / 'hub/elevhub.py')] if entry == 'hub'
            else [python, '-B', '-m', modules[entry]]) + arguments


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_bytes(url: str, *, body: bytes | None = None, headers: dict[str, str] | None = None,
                  method: str | None = None) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        # Keep errors route/status based; response bodies can contain secrets.
        raise QaFailure(f"HTTP {error.code} for {url.rsplit('/', 1)[-1]}") from None
    except (OSError, URLError) as error:
        raise QaFailure(f"No response from {url.rsplit('/', 1)[-1]}: {type(error).__name__}") from None


def request_json(url: str, *, body: dict | None = None, headers: dict[str, str] | None = None,
                 method: str | None = None) -> tuple[int, dict[str, str], dict]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Content-Type": "application/json"} if body is not None else {}
    request_headers.update(headers or {})
    status, response_headers, raw = request_bytes(url, body=payload, headers=request_headers, method=method)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise QaFailure(f"Non-JSON response from {url.rsplit('/', 1)[-1]}") from None
    if not isinstance(value, dict):
        raise QaFailure(f"Unexpected JSON response from {url.rsplit('/', 1)[-1]}")
    return status, response_headers, value


def expect_unauthorized(url: str) -> None:
    request = Request(url)
    try:
        with urlopen(request, timeout=3) as response:
            raise QaFailure(f"Unauthenticated request unexpectedly returned {response.status}")
    except HTTPError as error:
        if error.code != 401:
            raise QaFailure(f"Unauthenticated request returned HTTP {error.code}") from None


def wait_json(port: int, route: str, process: subprocess.Popen) -> dict:
    for _ in range(450):
        if process.poll() is not None:
            raise QaFailure(f"Process exited before {route}: {process.returncode}")
        try:
            status, _, value = request_json(f"http://127.0.0.1:{port}{route}")
            if status == 200:
                return value
        except QaFailure:
            time.sleep(0.2)
    raise QaFailure(f"No healthy response at {route}")


def run(package: Path, scratch_parent: Path) -> dict:
    package = package.resolve(strict=True)
    python = package / "runtime" / "python.exe"
    if not python.is_file():
        raise QaFailure("Embedded runtime/python.exe is missing")
    scratch_parent = scratch_parent.resolve()
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="windows-qa-", dir=scratch_parent) as temporary:
        root = Path(temporary)
        print_data, laser_data, plates = root / "print", root / "laser", root / "plates"
        local_appdata, temp_dir, home = root / "localappdata", root / "tmp", root / "home"
        for path in (print_data, laser_data, plates, local_appdata, temp_dir, home):
            path.mkdir(parents=True)
        (print_data / "settings.json").write_text(
            json.dumps({"studio_path": str(root / "missing-studio.exe"), "printers": []}), encoding="utf-8"
        )
        ports = [free_port() for _ in range(4)]
        print_port, design_port, teacher_port, station_port = ports
        env = dict(os.environ)
        env.update({
            "ELEVHUB_SCHOOL_MODE": "1",
            "LOCALAPPDATA": str(local_appdata),
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "USERPROFILE": str(home),
            "HOME": str(home),
        })
        commands = [
            package_command(package, 'print-server', ["--host", "127.0.0.1", "--port", str(print_port), "--data", str(print_data)]),
            package_command(package, 'design', ["--host", "127.0.0.1", "--port", str(design_port), "--teacher-host", "127.0.0.1", "--teacher-port", str(teacher_port), "--data", str(laser_data)]),
            package_command(package, 'laser-station', ["--server", f"http://127.0.0.1:{teacher_port}", "--port", str(station_port), "--plates", str(plates)]),
        ]
        if any("0.0.0.0" in argument or "::" in argument for command in commands for argument in command):
            raise QaFailure("A QA service was configured away from loopback")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        processes: list[subprocess.Popen] = []
        handles = []
        try:
            for index, command in enumerate(commands):
                log = (root / f"process-{index}.log").open("wb")
                handles.append(log)
                processes.append(subprocess.Popen(command, cwd=package, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags))

            print_health = wait_json(print_port, "/api/health", processes[0])
            if print_health.get("ok") is not True:
                raise QaFailure("Print health did not report ok")
            design_student = wait_json(design_port, "/api/laser/health", processes[1])
            design_teacher = wait_json(teacher_port, "/api/laser/health", processes[1])
            if design_student.get("surface") != "student" or design_teacher.get("surface") != "teacher":
                raise QaFailure("Laser student/teacher health surfaces are incorrect")

            status, _, javascript = request_bytes(f"http://127.0.0.1:{print_port}/printer-settings.js")
            if status != 200 or b"teacherPrinterSettings" not in javascript:
                raise QaFailure("Printer settings JavaScript was not served")
            status, _, stylesheet = request_bytes(f"http://127.0.0.1:{print_port}/printer-settings.css")
            if status != 200 or b"printer-settings" not in stylesheet:
                raise QaFailure("Printer settings CSS was not served")

            expect_unauthorized(f"http://127.0.0.1:{print_port}/api/settings")
            teacher_code = (print_data / "laererkode.txt").read_text(encoding="ascii").strip()
            if len(teacher_code) < 8 or teacher_code in {"kloster9000", "changeme", "password"}:
                raise QaFailure("Print teacher code was not freshly generated")
            status, response_headers, _ = request_json(
                f"http://127.0.0.1:{print_port}/api/auth/login", body={"code": teacher_code}
            )
            if status != 200 or "Set-Cookie" not in response_headers:
                raise QaFailure("Print teacher login did not return a cookie")
            cookie = response_headers["Set-Cookie"].split(";", 1)[0]
            status, _, settings = request_json(f"http://127.0.0.1:{print_port}/api/settings", headers={"Cookie": cookie})
            if status != 200 or settings.get("printers") != [] or "models" not in settings:
                raise QaFailure("Authenticated settings response is incomplete")
            if "access_code" in json.dumps(settings):
                raise QaFailure("Printer access code leaked through settings API")

            box_check = subprocess.run(
                [str(python), "-B", "-c", (
                    "from app.boxes_generator import generate_box_svg; "
                    "svg,details=generate_box_svg({'kind':'abox','x':40,'y':40,'h':40,'thickness':3,'burn':0.1,'level':3}); "
                    "import xml.etree.ElementTree as ET; "
                    "assert ET.fromstring(svg).tag=='{http://www.w3.org/2000/svg}svg' and details['generator']=='Boxes.py ABox'"
                )],
                cwd=root, env={**env, "PYTHONPATH": str(package)},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            )
            if box_check.returncode != 0:
                raise QaFailure("Embedded Boxes.py ABox generation failed")

            station_seen = False
            for _ in range(450):
                try:
                    status, _, _ = request_bytes(f"http://127.0.0.1:{station_port}/")
                    if status == 200:
                        station_seen = True
                        break
                except QaFailure:
                    time.sleep(0.2)
            if not station_seen:
                raise QaFailure("Laser station UI did not answer")
            state_status, _, state = request_json(f"http://127.0.0.1:{station_port}/api/station/state")
            if state_status != 200 or not isinstance(state, dict):
                raise QaFailure("Laser station state is unavailable")
            laser_code = (laser_data / "laererkode.txt").read_text(encoding="ascii").strip()
            status, _, _ = request_json(
                f"http://127.0.0.1:{station_port}/api/station/login",
                body={"code": laser_code},
                headers={"Origin": f"http://127.0.0.1:{station_port}"},
                method="POST",
            )
            if status != 200:
                raise QaFailure("Laser station login failed")
            jobs_status, _, jobs = request_json(f"http://127.0.0.1:{station_port}/api/station/jobs")
            if jobs_status != 200 or jobs.get("jobs") != []:
                raise QaFailure("Laser station queue is not empty")
            return {
                "ok": True,
                "embedded_runtime": True,
                "loopback_only": True,
                "fresh_d_temp": True,
                "print_health": True,
                "print_cookie_settings": True,
                "printer_settings_assets": True,
                "boxes_generator": True,
                "laser_health": True,
                "laser_station": True,
                "printers": 0,
            }
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            for handle in handles:
                handle.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--scratch-parent", type=Path, default=Path(r"D:\Elevhub-QA"))
    parser.add_argument("--report", type=Path, default=Path(__file__).with_name("windows-qa-report.json"))
    args = parser.parse_args()
    result: dict
    try:
        result = run(args.package, args.scratch_parent)
    except Exception as error:
        result = {"ok": False, "error": str(error)[:240]}
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result.get("ok", False), "report": str(report)}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
