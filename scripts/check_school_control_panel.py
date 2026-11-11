"""Run the bundled Elevhub control-panel integration against a staged package."""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from smoke_windows_school import package_command


class QaFailure(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class EmptyPrintBackend(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        body = b'{"printers":[],"jobs":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def https_call(port: int, context: ssl.SSLContext, method: str, route: str, body: dict | None = None,
               headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], dict | bytes]:
    connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=5)
    try:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        connection.request(method, route, payload, request_headers)
        response = connection.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type", "")
        if "json" in content_type:
            try:
                value: dict | bytes = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise QaFailure(f"Invalid JSON from {route}") from None
        else:
            value = raw
        return response.status, dict(response.getheaders()), value
    finally:
        connection.close()


def run(package: Path, scratch_parent: Path) -> dict:
    package = package.resolve(strict=True)
    release = json.loads((package / 'hub/release.json').read_text(encoding='utf-8'))
    server_release = release['release_id']
    client_release = release['expected_client_release']
    python = package / "runtime" / "python.exe"
    config_template = package / "hub" / "konfiguration.windows.json"
    integrity = package / "integrity.json"
    for required in (python, config_template, integrity):
        if not required.is_file():
            raise QaFailure(f"Staged file missing: {required.name}")
    integrity_before = sha256(integrity)
    openssl = shutil.which("openssl")
    if not openssl:
        candidate = Path(r"C:\Program Files\Git\usr\bin\openssl.exe")
        openssl = str(candidate) if candidate.is_file() else None
    if not openssl:
        raise QaFailure("openssl.exe is required for the temporary TLS fixture")
    scratch_parent = scratch_parent.resolve()
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="control-panel-qa-", dir=scratch_parent) as temporary:
        root = Path(temporary)
        security, hub_data = root / "security", root / "hub-data"
        security.mkdir(); hub_data.mkdir()
        cert, key, ca = security / "server.crt", security / "server.key", security / "ca.crt"
        subprocess.run([
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert),
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
        shutil.copy2(cert, ca)

        config = json.loads(config_template.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise QaFailure("Bundled hub configuration is not an object")
        ports = {"hub": free_port(), "teacher": free_port(), "print_entry": free_port(),
                 "laser_entry": free_port(), "folie_entry": free_port(), "backend": free_port()}
        config["elev_adresse"] = "127.0.0.1"
        config["laerer_adresse"] = "127.0.0.1"
        config["hub_port"] = ports["hub"]
        config["laerer_port"] = ports["teacher"]
        config["tls"] = {"certifikat": "security/server.crt", "noegle": "security/server.key", "ca": "security/ca.crt"}
        config["enhedsregister"] = "security/enheder.json"
        config["backend_ports"] = {"print": ports["backend"], "design": free_port(), "teacher": free_port()}
        apps = copy.deepcopy(config.get("apps", []))
        for app in apps:
            if app.get("id") == "3dprint":
                app["port"] = ports["print_entry"]
                app["bagved"] = f"127.0.0.1:{ports['backend']}"
            elif app.get("id") == "laser":
                app["port"] = ports["laser_entry"]
                app["bagved"] = f"127.0.0.1:{config['backend_ports']['design']}"
            elif app.get("id") == "folie":
                app["port"] = ports["folie_entry"]
                app["bagved"] = f"127.0.0.1:{config['backend_ports']['design']}"
        config["apps"] = apps

        root_token = secrets.token_urlsafe(32)
        ui_token = secrets.token_urlsafe(32)
        register = {"enheder": {"pc1": {
            "token_sha256": hashlib.sha256(root_token.encode("ascii")).hexdigest(),
            "ui_token_sha256": hashlib.sha256(ui_token.encode("ascii")).hexdigest(),
        }}}
        (security / "enheder.json").write_text(json.dumps(register), encoding="utf-8")
        config_path = root / "konfiguration.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "school.json").write_text(json.dumps({"school_name": "Eksempel Skole", "theme": "green"}), encoding="utf-8")

        backend = ThreadingHTTPServer(("127.0.0.1", ports["backend"]), EmptyPrintBackend)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        env = dict(os.environ)
        env.update({"ELEVHUB_SCHOOL_MODE": "1", "TEMP": str(root / "tmp"), "TMP": str(root / "tmp"),
                    "LOCALAPPDATA": str(root / "localappdata"), "HOME": str(root / "home"),
                    "USERPROFILE": str(root / "home")})
        for name in ("tmp", "localappdata", "home"):
            (root / name).mkdir()
        process = None
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log = (root / "elevhub.log").open("wb")
        try:
            process = subprocess.Popen(package_command(package, 'hub', [
                "--konfiguration", str(config_path), "--data", str(hub_data)
            ]), cwd=package, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            context = ssl.create_default_context(cafile=str(cert))
            for _ in range(450):
                if process.poll() is not None:
                    raise QaFailure(f"Bundled Elevhub exited: {process.returncode}")
                try:
                    status, _, _ = https_call(ports["teacher"], context, "GET", "/api/laerer/session")
                    if status == 200:
                        break
                except (OSError, QaFailure):
                    pass
                time.sleep(0.2)
            else:
                raise QaFailure("Bundled teacher panel did not answer")

            teacher_pass = "teacher-pass-2026"
            status, _, _ = https_call(ports["teacher"], context, "POST", "/api/laerer/opsaet",
                                      {"kode": teacher_pass, "kode_igen": teacher_pass},
                                      {"Origin": f"https://127.0.0.1:{ports['teacher']}"})
            if status != 200:
                raise QaFailure("Teacher password setup failed")
            status, headers, _ = https_call(ports["teacher"], context, "POST", "/api/laerer/login",
                                             {"kode": teacher_pass},
                                             {"Origin": f"https://127.0.0.1:{ports['teacher']}"})
            if status != 200 or "Set-Cookie" not in headers:
                raise QaFailure("Teacher login did not return a cookie")
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            status, _, _ = https_call(ports["teacher"], context, "GET", "/api/laerer/system")
            if status != 401:
                raise QaFailure("Unauthenticated system overview was not rejected")

            heartbeat_headers = {"Authorization": f"Bearer {root_token}", "X-Elevhub-Computer": "pc1"}
            status, _, _ = https_call(ports["hub"], context, "POST", "/api/enhed/status",
                                      {"computer": "pc1", "version": 1, "software_version": client_release}, heartbeat_headers)
            if status != 200:
                raise QaFailure("Current root heartbeat was rejected")
            panel_headers = {"Cookie": cookie}
            status, _, overview = https_call(ports["teacher"], context, "GET", "/api/laerer/system", headers=panel_headers)
            if status != 200 or overview.get("server", {}).get("release_id") != server_release:
                raise QaFailure("Panel did not expose the packaged release")
            computer = next((row for row in overview.get("computers", []) if row.get("navn") == "pc1"), None)
            if not computer or computer.get("software_version") != client_release or computer.get("version_status") != "current":
                raise QaFailure("Current software heartbeat was not reflected in the panel")
            if overview.get("printers", {}).get("status") != "ok" or overview.get("printers", {}).get("items") != []:
                raise QaFailure("Empty isolated print backend was not reported cleanly")

            for software_version, expected_status in ((client_release + ".qa-different", "different"), (None, "unknown")):
                body = {"computer": "pc1", "version": 1}
                if software_version is not None:
                    body["software_version"] = software_version
                status, _, _ = https_call(ports["hub"], context, "POST", "/api/enhed/status", body, heartbeat_headers)
                if status != 200:
                    raise QaFailure("Version heartbeat was rejected")
                status, _, overview = https_call(ports["teacher"], context, "GET", "/api/laerer/system", headers=panel_headers)
                computer = next(row for row in overview.get("computers", []) if row.get("navn") == "pc1")
                if computer.get("version_status") != expected_status:
                    raise QaFailure(f"Panel version status was not {expected_status}")

            for route, marker in (("/kontrolpanel", b"system-overview.js"), ("/system-overview.js", b"systemGeneration"), ("/system-overview.css", b"system-") , ("/LICENSE", b"MIT") ):
                status, headers, payload = https_call(ports["teacher"], context, "GET", route)
                if status != 200 or not isinstance(payload, bytes) or marker not in payload:
                    raise QaFailure(f"Teacher asset failed: {route}")
                if route == "/LICENSE" and "text/plain" not in headers.get("Content-Type", ""):
                    raise QaFailure("License fallback has the wrong content type")
            if sha256(integrity) != integrity_before:
                raise QaFailure("Stage integrity manifest changed during QA")
            return {"ok": True, "embedded_runtime": True, "tls": True, "loopback_only": True,
                    "teacher_cookie_auth": True, "release": server_release, "heartbeat_current": True,
                    "heartbeat_different": True, "heartbeat_unknown": True, "printer_probe_empty": True,
                    "control_panel_assets": True, "license_fallback": True, "stage_unchanged": True}
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=10)
            backend.shutdown(); backend.server_close(); log.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--scratch-parent", type=Path, default=Path(r"D:\Elevhub-QA"))
    parser.add_argument("--report", type=Path, default=Path(__file__).with_name("system-overview-qa-report.json"))
    args = parser.parse_args()
    try:
        result = run(args.package, args.scratch_parent)
    except Exception as error:
        result = {"ok": False, "error": str(error)[:240]}
    report = args.report.resolve(); report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result.get("ok", False), "report": str(report)}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
