"""Persistent server-owned laser update worker, controlled by teacher queue jobs."""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def lease(root):
    with (root / "worker.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def validate(root, config):
    if (config.get("schema") != 1
            or config.get("role") != "windows_laser_teacher"
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}", config.get("release_id", ""))
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}", config.get("target", ""))
            or not re.fullmatch(r"[A-Za-z0-9._-]+", config.get("user", ""))
            or not re.fullmatch(r"[A-Za-z0-9.-]+", config.get("host_key_alias", ""))
            or not config.get("hosts")
            or any(not re.fullmatch(r"[A-Za-z0-9.-]+", host) for host in config["hosts"])):
        raise ValueError("invalid_worker_configuration")
    for name, key in (("station-code.zip", "archive_sha256"), ("remote_runner.py", "runner_sha256")):
        if not re.fullmatch(r"[a-f0-9]{64}", config.get(key, "")) or digest(root / name) != config[key]:
            raise ValueError("worker_payload_changed")
    for name in ("ssh_key", "known_hosts"):
        path = Path(config[name])
        if not path.is_absolute() or not path.is_file():
            raise ValueError("missing_ssh_credentials")
    install_path = config.get("install_path")
    policy = config.get("install_path_policy", "explicit")
    if policy not in ("explicit", "user_default"):
        raise ValueError("invalid_installation_path_policy")
    if policy == "user_default":
        if install_path is not None: raise ValueError("ambiguous_installation_path")
    elif (not isinstance(install_path, str) or not re.fullmatch(r"[A-Za-z]:[\\/].+", install_path)
            or any(c in install_path for c in '\r\n\x00') or len(install_path)>1000):
        raise ValueError("station_installation_path_required")
    return config


def options(config):
    return ["-F", "NUL" if os.name == "nt" else "/dev/null",
            "-i", config["ssh_key"], "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", "HostKeyAlias=" + config["host_key_alias"],
            "-o", "UserKnownHostsFile=" + config["known_hosts"],
            "-o", "GlobalKnownHostsFile=" + ("NUL" if os.name == "nt" else "/dev/null"),
            "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2"]


def ps_command(source):
    return "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " + \
        base64.b64encode(source.encode("utf-16le")).decode("ascii")


def deliver(root, config, operation, invoke=subprocess.run):
    if not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise ValueError("invalid_operation")
    ssh = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/OpenSSH/ssh.exe")
    scp = str(Path(ssh).with_name("scp.exe"))
    opts = options(config)
    account = None
    for host in config["hosts"]:
        target = config["user"] + "@" + host
        try:
            probe = invoke([ssh, *opts, target, "echo maker-update-ready"], capture_output=True, timeout=20)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if probe.returncode == 0 and b"maker-update-ready" in probe.stdout:
            account = target
            break
    if account is None:
        return {"status": "waiting_for_computer"}
    stem = "maker-laser-update-" + operation
    for local, extension in (("station-code.zip", ".zip"), ("remote_runner.py", ".py")):
        try:
            result = invoke([scp, *opts, str(root / local), account + ":./" + stem + extension], capture_output=True, timeout=180)
        except (subprocess.TimeoutExpired, OSError):
            return {"status":"retry", "code":"transfer_failed"}
        if result.returncode:
            return {"status": "retry", "code": "transfer_failed"}
    # Trusted fixed script and hex bindings; no user-supplied command/path interpolation.
    if config.get("install_path_policy") == "user_default":
        root_assignment = "$root=Join-Path $env:LOCALAPPDATA 'Laserstation'"
    else:
        encoded = base64.b64encode(config["install_path"].encode("utf-8")).decode("ascii")
        root_assignment = "$root=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))"
    script = """
$ErrorActionPreference='Stop'
INSTALLASSIGN
$python=Join-Path $root 'runtime\\python.exe'
$runner=Join-Path $env:USERPROFILE 'STEM.py'
$archive=Join-Path $env:USERPROFILE 'STEM.zip'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  Write-Output '{"status":"blocked","code":"station_installation_missing"}'; exit 1
}
if ((Get-FileHash -LiteralPath $runner -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'RUNNERHASH') {
  Write-Output '{"status":"blocked","code":"runner_hash_mismatch"}'; exit 1
}
& $python -B $runner --archive $archive --sha256 'ARCHIVEHASH' --install-path $root --operation-id 'OPERATION'
exit $LASTEXITCODE
""".replace("STEM", stem).replace("INSTALLASSIGN", root_assignment).replace("RUNNERHASH", config["runner_sha256"]) \
       .replace("ARCHIVEHASH", config["archive_sha256"]).replace("OPERATION", operation)
    if config.get("require_known_version") is True:
        arguments = " --require-known-version"
        baseline = config.get("baseline_version")
        if baseline is not None:
            if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?", baseline):
                raise ValueError("invalid_verified_baseline")
            arguments += " --baseline-version '" + baseline + "'"
        script = script.replace("--operation-id '" + operation + "'", "--operation-id '" + operation + "'" + arguments)
    try:
        result = invoke([ssh, *opts, account, ps_command(script)], capture_output=True, timeout=300)
    except (subprocess.TimeoutExpired, OSError):
        return {"status":"retry", "code":"remote_result_unconfirmed"}
    try:
        response = json.loads(result.stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        return {"status": "blocked", "code": "station_update_failed"}
    if response.get("status") not in {
            "complete", "waiting_for_idle", "blocked", "failed_rolled_back"}:
        return {"status": "blocked", "code": "station_update_failed"}
    if result.returncode and response["status"] == "complete":
        return {"status": "blocked", "code": "station_update_failed"}
    return response


def tick(root, config, service, state, invoke=subprocess.run):
    release, target = config["release_id"], config["target"]
    service.heartbeat(release)
    if not service.worker_allowed(release, target):
        return
    try:
        job = service.worker_job(release, target)
        state["last_attempt"] = time.time()
        state["status"] = "checking"
        atomic_json(root / "status.json", state)
        service.report(release, target, "checking")
        # Reporting is nonterminal for checking; worker job lease remains bound.
        response = deliver(root, config, job["id"], invoke)
        status = response["status"]
        service.report(release, target, status, role_verified=status == "complete", code=response.get("code"))
        state.update(status=status, release=config["release"], checked_at=time.time())
        state["code"] = response.get("code", "")
        atomic_json(root / "status.json", state)
    except Exception:
        service.report(release, target, "blocked")
        state.update(status="blocked", code="station_update_failed", checked_at=time.time())
        atomic_json(root / "status.json", state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = args.root.absolute()
    config = validate(root, json.loads((root / "config.json").read_text(encoding="utf-8")))
    module_path = Path(config["admin_control"]["module_path"])
    if not module_path.is_absolute(): raise ValueError("admin_module_path_must_be_absolute")
    spec = importlib.util.spec_from_file_location("laser_update_administration", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = module.UpdateAdministration(config["admin_control"]["data_root"])
    state_path = root / "status.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
        "release": config["release"], "status": "pending"}
    with lease(root):
        while True:
            tick(root, config, service, state)
            if args.once:
                return
            time.sleep(30)


if __name__ == "__main__":
    main()
