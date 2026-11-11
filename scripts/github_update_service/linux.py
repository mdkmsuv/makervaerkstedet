"""Configured transport for the existing boot-only Linux update transaction.

The installer and remote helper are local controller code, never imported from
an update package. Device names, keys and networks come only from local profiles.
Kreditering: Klostermarksskolen.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import tarfile
from pathlib import PurePosixPath

try:
    from .common import digest, plain, read, write, require_upgrade
except ImportError:
    from common import digest, plain, read, write, require_upgrade

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}")


def verify_payload(path, version):
    actual, metadata, seen = {}, {}, set()
    total = 0
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            name = member.name.rstrip("/")
            relative = PurePosixPath(name)
            if (not name or relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name
                    or str(relative) != name or name in seen):
                raise ValueError("unsafe_linux_payload_path")
            seen.add(name)
            if len(seen) > 100000:
                raise ValueError("linux_payload_too_many_files")
            if member.isdir():
                continue
            total += member.size
            if not member.isfile() or member.size > 4 * 1024**3 or total > 8 * 1024**3:
                raise ValueError("unsafe_linux_payload_member")
            is_metadata = name in ("manifest.json", "linux-elevhub/server/elevhub/release.json")
            if is_metadata and member.size > 4 * 1024**2:
                raise ValueError("linux_payload_metadata_too_large")
            content = bytearray() if is_metadata else None
            sha = hashlib.sha256()
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(block)
                    if content is not None:
                        content.extend(block)
            actual[name] = sha.hexdigest()
            if content is not None:
                metadata[name] = json.loads(content)
    manifest = metadata.get("manifest.json", {})
    if (manifest.get("version") != version
            or metadata.get("linux-elevhub/server/elevhub/release.json", {}).get("release_id") != version):
        raise ValueError("linux_payload_version_mismatch")
    files = manifest.get("files", {})
    if not isinstance(files, dict) or set(actual) != set(files) | {"manifest.json"}:
        raise ValueError("linux_payload_inventory_mismatch")
    if any(actual[name] != value for name, value in files.items()):
        raise ValueError("linux_payload_hash_mismatch")
    required_admin = {
        "linux-elevhub/ansible/roles/adminkonto/tasks/update-interface.yml",
        "linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update.py",
        "linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update-gui.py",
        "linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-xsession.sh",
        "linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-session.sh",
        "linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-return.py",
        "linux-elevhub/ansible/roles/adminkonto/templates/admin-bash-profile.j2",
    }
    if not required_admin.issubset(actual):
        raise ValueError("linux_payload_admin_interface_incomplete")
    return manifest


def bind_installer(source, version, operation, names, package_sha256=None):
    """Bind the installer to one reviewed outer package and target scope."""
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(?:\.\d+)?", version):
        raise ValueError("invalid_linux_version")
    if not re.fullmatch(r"github-linux-[a-f0-9]{24}", operation):
        raise ValueError("invalid_linux_operation")
    if not isinstance(package_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", package_sha256):
        raise ValueError("invalid_linux_package_sha256")
    if operation != "github-linux-" + package_sha256[:24]:
        raise ValueError("linux_operation_package_mismatch")
    if not names or any(not NAME.fullmatch(n) for n in names) or len(set(names)) != len(names):
        raise ValueError("invalid_linux_targets")
    replacements = {
        "RELEASE='UNBOUND_RELEASE'": "RELEASE=" + repr(version),
        "OPERATION='UNBOUND_OPERATION'": "OPERATION=" + repr(operation),
        "NAMES=[] # BOUND_LOCAL_TARGETS": "NAMES=" + repr(names),
        "PACKAGE_SHA='UNBOUND_PACKAGE_SHA'": "PACKAGE_SHA=" + repr(package_sha256),
        "STAGE_RE=re.compile(r'UNBOUND_STAGE_PATTERN')":
            "STAGE_RE=re.compile(" + repr("/home/elevhub/" + operation + "-[A-Za-z0-9]{6}") + ")",
    }
    for before, after in replacements.items():
        if source.count(before) != 1:
            raise ValueError("unsupported_linux_installer_template")
        source = source.replace(before, after)
    compile(source, "<configured-linux-installer>", "exec")
    return source


def prepare(root, package, config, code_root):
    source_root = code_root / "scripts/github_update_service/templates"
    operation = "github-linux-" + config["package_sha256"][:24]
    source = bind_installer((source_root / "linux_install.py").read_text(encoding="utf-8"),
                            config["version"], operation, [p["worker_target"] for p in config["profiles"]],
                            config["package_sha256"])
    payload = plain(package / "payload.tar.gz")
    verify_payload(payload, config["version"])
    helper = (source_root / "linux_remote.py").read_text(encoding="utf-8")
    helper = helper.replace("@@PAYLOAD_SHA@@", digest(payload)).replace(
        "PAYLOAD_SIZE=0 # @@PAYLOAD_SIZE@@", "PAYLOAD_SIZE=" + str(payload.stat().st_size)).replace(
        "@@INSTALLER_B64@@", base64.b64encode(source.encode()).decode())
    if "@@" in helper:
        raise ValueError("unbound_linux_helper")
    # Completion must bind to this payload, in addition to the installer's
    # verified installed-file inventory and preservation record.
    compile(helper, "<configured-linux-helper>", "exec")
    (root / "remote.py").write_text(helper, encoding="utf-8")
    config.update(operation=operation, payload=str(payload), payload_sha256=digest(payload),
                  remote_sha256=digest(root / "remote.py"))
    return config


def options(profile, known, bootstrap=False):
    null = "NUL" if os.name == "nt" else "/dev/null"
    return ["-F", null, "-i", profile["ssh_key"], "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2", "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", "GlobalKnownHostsFile=" + null, "-o", "HashKnownHosts=no",
            "-o", "HostKeyAlias=" + profile["host_key_alias"], "-o", "UserKnownHostsFile=" + str(known),
            "-o", "StrictHostKeyChecking=" + ("accept-new" if bootstrap else "yes")]


def address(profile, registry, now):
    row = registry.get(profile["worker_target"], {})
    if not isinstance(row.get("sidst_set"), (int, float)) or not 0 <= now - row["sidst_set"] <= 180:
        return None
    try:
        ip = ipaddress.ip_address(row.get("ip"))
    except ValueError:
        return None
    networks = [ipaddress.ip_network(n, strict=True) for n in profile["allowed_networks"]]
    if any(ip in net and ip not in (net.network_address, net.broadcast_address) for net in networks):
        return str(ip)
    return None


def command(argv, source=None, timeout=90, invoke=subprocess.run):
    result = invoke(argv, input=source, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise OSError("linux_remote_command_failed")
    return result.stdout


def probe(root, config, profile, ip, invoke=subprocess.run):
    ssh = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/OpenSSH/ssh.exe")
    name = profile["worker_target"]
    known = plain(profile["known_hosts"])
    candidate = plain(root / (name + ".candidate"))
    candidate.write_text(known.read_text(encoding="ascii") if known.exists() else "", encoding="ascii")
    bootstrap = not known.exists() and profile.get("host_key_policy") == "enrollment_proof"
    nonce = secrets.token_hex(32)
    helper = (root / "remote.py").read_text(encoding="utf-8")
    response = json.loads(command([ssh, *options(profile, candidate, bootstrap), "elevhub@" + ip,
                                  "sudo -n python3 - probe " + name + " " + nonce], helper, invoke=invoke))
    lines = [line.split() for line in candidate.read_text(encoding="ascii").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    alias = profile["host_key_alias"]
    matched = [line for line in lines if len(line) == 3 and alias in line[0].split(",")]
    if len(matched) != 1:
        raise ValueError("linux_host_key_evidence_missing")
    public = " ".join(matched[0][1:])
    if (not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]{68}", public)
            or response.get("publickey") != public or response.get("hostname") != name):
        raise ValueError("linux_host_identity_mismatch")
    expected = hmac.new(bytes.fromhex(profile["identity_sha256"]),
                        (nonce + "\n" + name + "\n" + public).encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, response.get("proof", "")):
        raise ValueError("linux_enrollment_proof_rejected")
    if known.exists() and known.read_bytes() != candidate.read_bytes():
        raise ValueError("linux_pinned_host_changed")
    known.parent.mkdir(parents=True, exist_ok=True)
    os.replace(candidate, known)
    response.pop("proof", None)
    response.pop("publickey", None)
    return response, known


def tick(root, config, profile, admin, invoke=subprocess.run):
    release, name = config["release_id"], profile["worker_target"]
    if not admin.worker_allowed(release, name):
        return
    state_path = root / (name + ".status.json")
    row = read(state_path) if state_path.exists() else {}
    try:
        if digest(root / "remote.py") != config["remote_sha256"]:
            raise ValueError("linux_payload_changed")
        ip = address(profile, read(profile["registry"]), time.time())
        if ip is None:
            admin.report(release, name, "waiting_for_computer")
            return
        admin.report(release, name, "checking")
        result, known = probe(root, config, profile, ip, invoke)
        if result.get("operation") != config["operation"]:
            raise ValueError("linux_operation_mismatch")
        status = result.get("update_status")
        if status in (None, "stage_retry", "staged_awaiting_restart") and (
                result.get("package_sha256") != config.get("package_sha256") or
                result.get("local_admin_approved") is not True):
            admin.report(release, name, "blocked", code="local_admin_required")
            row.update(status="blocked", code="local_admin_required", checked_at=time.time())
            write(state_path, row)
            return
        if status in (None, "stage_retry"):
            try:
                require_upgrade(result.get("installed_release"), config["version"], profile.get("baseline_version"))
            except ValueError as error:
                code = "version_unknown" if str(error) == "installed_version_unknown" else "version_not_older"
                admin.report(release, name, "blocked", code=code)
                row.update(status="blocked", code="installed_version_requires_review")
                write(state_path, row)
                return
            ssh = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/OpenSSH/ssh.exe")
            scp = str(Path(ssh).with_name("scp.exe"))
            stage = row.get("stage")
            pattern = "/home/elevhub/" + config["operation"] + "-[A-Za-z0-9]{6}"
            if stage is None:
                stage = command([ssh, *options(profile, known), "elevhub@" + ip,
                                 "mktemp -d /home/elevhub/" + config["operation"] + "-XXXXXX"], invoke=invoke).strip()
            if not re.fullmatch(pattern, stage):
                raise ValueError("invalid_linux_staging_path")
            row["stage"] = stage
            write(state_path, row)
            admin.report(release, name, "transferring")
            if digest(config["payload"]) != config["payload_sha256"]:
                raise ValueError("linux_payload_changed")
            command([scp, *options(profile, known), config["payload"], "elevhub@" + ip + ":" + stage + "/payload.tar.gz"],
                    timeout=3600, invoke=invoke)
            result = json.loads(command([ssh, *options(profile, known), "elevhub@" + ip,
                                         "sudo -n python3 - stage " + name + " " + stage],
                                        (root / "remote.py").read_text(encoding="utf-8"), timeout=5400, invoke=invoke))
            status = result.get("status")
            row.pop("stage", None)
        if status == "complete":
            # Stage cannot provide completion proof; read the root receipt and
            # installed hashes via a second authenticated probe after any apply.
            result, _ = probe(root, config, profile, ip, invoke)
            if (result.get("update_status") != "complete" or result.get("release") != config["version"]
                    or result.get("payload_sha256") != config["payload_sha256"]):
                raise ValueError("linux_completion_not_verified")
            admin.report(release, name, "complete", role_verified=True, installed_version=config["version"],
                         package_sha256=config["package_sha256"])
        elif status in ("staged_awaiting_restart", "applying", "failed_rolled_back", "recovery_required", "profile_missing"):
            admin.report(release, name, status)
        else:
            raise ValueError("linux_result_not_verified")
        row.update(status=status, checked_at=time.time())
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        admin.report(release, name, "retry", code="remote_result_unconfirmed")
        row.update(status="retry", checked_at=time.time())
    write(state_path, row)
