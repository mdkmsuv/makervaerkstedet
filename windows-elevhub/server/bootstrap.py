"""Elevhub Windows server supervisor.

Validation is intentionally read-only.  Runtime starts the three local services as
members of Windows job objects so a stopped scheduled task cannot leave children.
"""
from __future__ import annotations

import argparse
import errno
import ipaddress
import json
import os
import signal
import socket
import subprocess
from app.backup_guard import assert_not_restored, write_protocol
from app.instance import InstanceLock
import sys
import time
import threading
from pathlib import Path

sys.dont_write_bytecode = True

# Documentation/template address only. Runtime must use the explicitly chosen
# address or the protected installed configuration, never this build constant.
ROLE_IP = "192.0.2.2"
PORTS = (8765, 8766, 8767, 8770, 8771, 8772, 8773, 8780)
EXTERNAL_BACKENDS = frozenset(("print", "design"))
ROLE_IP_RETRY_SECONDS = 5.0


class BoundedChildLog:
    """Drain binary child output without line buffering or unbounded files.

    Only this reader owns the log handles, so Windows rotation never attempts
    to rename a file inherited by a child. Nonblocking pipe reads also allow
    shutdown when an inherited writer has failed to close its end.
    """
    def __init__(self, pipe, path, max_bytes=2_000_000, backups=3):
        if max_bytes < 1 or backups < 1:
            raise ValueError('Loggrænser skal være positive.')
        self.pipe, self.path = pipe, Path(path)
        self.max_bytes, self.backups = max_bytes, backups
        self.error = None
        self.stopping = threading.Event()
        os.set_blocking(pipe.fileno(), False)
        self.thread = threading.Thread(target=self._drain, name='elevhub-log-' + self.path.stem)
        self.thread.start()

    def _drain(self):
        stream = None
        try:
            # Upgrade an old unbounded log without reading it into memory.
            # Include retained archives when restarting with a smaller limit.
            for index in range(self.backups + 1):
                path = self.path if index == 0 else Path(str(self.path) + '.' + str(index))
                if path.exists() and path.stat().st_size > self.max_bytes:
                    with path.open('rb') as old:
                        old.seek(-self.max_bytes, os.SEEK_END)
                        tail = old.read(self.max_bytes)
                    with path.open('wb') as trimmed:
                        trimmed.write(tail)
            stream = self.path.open('ab', buffering=0)
            size = stream.tell()
            deadline = None
            while True:
                if self.stopping.is_set():
                    deadline = deadline or time.monotonic() + 1
                    if time.monotonic() >= deadline:
                        break
                try:
                    chunk = os.read(self.pipe.fileno(), 64 * 1024)
                except BlockingIOError:
                    if self.stopping.wait(0.05):
                        break
                    continue
                if not chunk:
                    break
                while chunk:
                    if size >= self.max_bytes:
                        stream.close()
                        for index in range(self.backups, 0, -1):
                            source = self.path if index == 1 else Path(str(self.path) + '.' + str(index - 1))
                            target = Path(str(self.path) + '.' + str(index))
                            if source.exists():
                                os.replace(source, target)
                        stream = self.path.open('wb', buffering=0)
                        size = 0
                    amount = min(len(chunk), self.max_bytes - size)
                    stream.write(chunk[:amount])
                    size += amount
                    chunk = chunk[amount:]
        except Exception as error:
            self.error = error
        finally:
            if stream is not None:
                stream.close()
            self.pipe.close()

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError('Tjenestens loglæser kunne ikke stoppes.')


def validate_role_ip(value: str) -> str:
    """Return a normalized IPv4 role address.

    The package carries a documentation address; a deployment must
    explicitly select the address assigned to the temporary workstation or a
    future server.  Host names and IPv6 are intentionally not accepted: the
    address is also used for certificate SANs and firewall bindings.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError(f"Ugyldig serveradresse: {value!r}") from error
    if not isinstance(address, ipaddress.IPv4Address):
        raise ValueError("Serverrollen skal bruge en IPv4-adresse")
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError("Serverrollen skal bruge en aktiv, ikke-lokal IPv4-adresse")
    return str(address)


def _role_ip_unavailable(error: OSError) -> bool:
    """Only a not-yet-assigned address is a startup condition to retry."""
    return (getattr(error, 'winerror', None) == 10049
            or error.errno in (errno.EADDRNOTAVAIL, 10049))


def wait_for_role_ip(role_ip: str, stopping: threading.Event,
                     retry_interval: float = ROLE_IP_RETRY_SECONDS) -> bool:
    """Wait until Windows can bind the configured IPv4, or stop was requested.

    Binding an ephemeral port checks local address assignment without opening a
    listener or depending on DNS, remote connectivity, or firewall policy.
    """
    role_ip = validate_role_ip(role_ip)
    started = time.monotonic()
    waiting_logged = False
    while not stopping.is_set():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((role_ip, 0))
        except OSError as error:
            if not _role_ip_unavailable(error):
                raise
            if not waiting_logged:
                print(f'Server IPv4 readiness: waiting for {role_ip}; elapsed=0s', flush=True)
                waiting_logged = True
            if stopping.wait(retry_interval):
                break
            continue
        if stopping.is_set():
            break
        elapsed = time.monotonic() - started
        print(f'Server IPv4 readiness: ready for {role_ip}; elapsed={elapsed:.1f}s', flush=True)
        return True
    elapsed = time.monotonic() - started
    print(f'Server IPv4 readiness: cancelled for {role_ip}; elapsed={elapsed:.1f}s', flush=True)
    return False


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} skal indeholde et JSON-objekt")
    return value


def _under(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _reparse(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def resolve_role_ip(data_root: Path, explicit: str | None = None) -> str:
    """Resolve legacy startup only from retained installation metadata.

    Old scheduled tasks omit --role-ip. Their installed configuration still
    carries the actual address. Conflicting owner/config addresses are refused.
    """
    paths = [data_root, data_root / 'konfiguration.json', data_root / 'installation-owner.json']
    for path in paths:
        if _reparse(path) or any(_reparse(parent) for parent in path.parents):
            raise ValueError('Serveradressens installationsmetadata må ikke være omdirigeret')
    candidates = []
    if paths[1].is_file():
        config = _load(paths[1])
        candidates.append(validate_role_ip(config.get('elev_adresse')))
    if paths[2].is_file():
        owner = _load(paths[2])
        if owner.get('roleIp') is not None:
            candidates.append(validate_role_ip(owner['roleIp']))
    if explicit is not None:
        candidates.append(validate_role_ip(explicit))
    if not candidates or len(set(candidates)) != 1:
        raise ValueError('Serveradressen skal vælges eksplicit eller fremgå entydigt af installationen')
    return candidates[0]


def read_runtime_config(data_root: Path, role_ip: str | None = None,
                        teacher_port: int = 8780,
                        teacher_address: str | None = None) -> dict:
    role_ip = resolve_role_ip(data_root, role_ip)
    expected_teacher_address = role_ip if teacher_address is None else str(ipaddress.ip_address(teacher_address))
    if expected_teacher_address not in (role_ip, "127.0.0.1"):
        raise ValueError("Lærerens adresse skal være serveradressen eller 127.0.0.1")
    if _reparse(data_root):
        raise ValueError("Datamappen må ikke være et reparse point")
    data_root = data_root.resolve(strict=True)
    config_path = data_root / "konfiguration.json"
    if _reparse(config_path):
        raise ValueError("Runtime-konfigurationen må ikke være et reparse point")
    config = _load(config_path)
    if (config.get("elev_adresse") != role_ip or config.get("hub_port") != 8773
            or config.get("laerer_adresse") != expected_teacher_address or config.get("laerer_port") != teacher_port):
        raise ValueError("Runtime-konfigurationen har forkert serverrolle")
    tls = config.get("tls")
    if not isinstance(tls, dict):
        raise ValueError("Runtime-konfigurationen mangler tls")
    required = [tls.get("certifikat"), tls.get("noegle"), tls.get("ca"), config.get("enhedsregister")]
    security = data_root / "security"
    expected = [security / "server.crt", security / "server.key", security / "ca.crt", security / "enheder.json"]
    if _reparse(security):
        raise ValueError("Sikkerhedsmappen må ikke være et reparse point")
    for raw, exact in zip(required, expected):
        if not isinstance(raw, str) or not os.path.isabs(raw):
            raise ValueError("TLS- og registerstier skal være absolutte")
        path = Path(raw)
        if path.resolve(strict=False) != exact.resolve(strict=False) or not _under(path, security) or _reparse(path) or not path.is_file():
            raise ValueError(f"Beskyttet sikkerhedsfil mangler: {path}")
    _validate_backend_ports(config)
    _validate_external_backends(config)
    return config


def _validate_backend_ports(config: dict) -> dict:
    defaults = {"print": 8765, "design": 8766, "teacher": 8767}
    ports = config.get("backend_ports")
    if ports is None:
        return defaults
    if not isinstance(ports, dict):
        raise ValueError("backend_ports skal være et JSON-objekt")
    for name in ("print", "design", "teacher"):
        value = ports.get(name, defaults[name])
        if type(value) is not int or not 1 <= value <= 65535:
            raise ValueError(f"backend_ports.{name} skal være en gyldig port")
        ports[name] = value
    for app in config.get("apps", []):
        if not isinstance(app, dict) or app.get("type", "vaerksted") != "vaerksted":
            continue
        backend = app.get("bagved")
        if not isinstance(backend, str) or ":" not in backend:
            raise ValueError("Værkstedets backend skal være en lokal host:port")
        host, raw_port = backend.rsplit(":", 1)
        try:
            port = int(raw_port)
        except ValueError as error:
            raise ValueError("Værkstedets backendport er ugyldig") from error
        if host not in ("127.0.0.1", "localhost") or port not in (ports["print"], ports["design"]):
            raise ValueError("Værkstedets backend skal matche en konfigureret lokal backendport")
    return ports


def _validate_external_backends(config: dict) -> list[str]:
    """Validate names of backends owned by another process."""
    value = config.get("external_backends", [])
    if type(value) is not list:
        raise ValueError("external_backends skal være en liste")
    if any(type(name) is not str or name not in EXTERNAL_BACKENDS for name in value):
        raise ValueError("external_backends må kun indeholde 'print' og 'design'")
    if len(value) != len(set(value)):
        raise ValueError("external_backends må ikke indeholde dubletter")
    return value


def check(package_root: Path, data_root: Path, installed: bool = False,
          role_ip: str | None = None, teacher_port: int = 8780,
          teacher_address: str | None = None, runtime_root: Path | None = None,
          manifest_sha256: str | None = None) -> dict:
    role_ip = resolve_role_ip(data_root, role_ip) if installed else (validate_role_ip(role_ip) if role_ip is not None else None)
    if _reparse(package_root):
        raise ValueError("Pakkemappen må ikke være et reparse point")
    package_root = package_root.resolve(strict=True)
    data_root = data_root.resolve(strict=True)
    required = (
        "runtime/python.exe", "app", "web", "hub/elevhub.py",
        "hub/konfiguration.windows.json", "server/provision.py",
    )
    missing = [item for item in required if not (package_root / item).exists()]
    if missing:
        raise ValueError("Pakken mangler: " + ", ".join(missing))
    if runtime_root is not None:
        from app.generation_runtime import server_integrity_lease, validate_generation
        if not server_integrity_lease(package_root, runtime_root, manifest_sha256):
            validate_generation(package_root, runtime_root, manifest_sha256)
    elif manifest_sha256 is not None:
        raise ValueError("Et generationsmanifest kræver en fast runtime-rod")
    config = read_runtime_config(data_root, role_ip, teacher_port, teacher_address) if installed else _load(package_root / "hub/konfiguration.windows.json")
    configured_ip = config.get("elev_adresse")
    if installed:
        if configured_ip != role_ip:
            raise ValueError("Runtime-konfigurationen bruger en anden serveradresse")
    elif configured_ip != ROLE_IP:
        raise ValueError(f"Pakkens standardserveradresse skal være {ROLE_IP}")
    ports = [item.get("port") for item in config.get("apps", []) if isinstance(item, dict) and isinstance(item.get("port"), int)]
    ports.extend([config.get("hub_port"), config.get("laerer_port")])
    return {"ok": True, "role": "server", "roleIp": role_ip,
            "ports": ports or list(PORTS)}


def _commands(package_root: Path, data_root: Path, config: dict,
              runtime_root: Path | None = None, manifest_sha256: str | None = None) -> list[tuple[str, list[str]]]:
    python = str(package_root / "runtime" / "python.exe")
    backend = _validate_backend_ports(config)
    external = set(_validate_external_backends(config))
    from app.demo_workshop import demo_root
    simulation = demo_root(data_root)
    if simulation and external:
        raise ValueError('DEMO kræver egne lokale simulerede backends; eksterne tjenester må ikke genbruges.')
    commands = []
    if "print" not in external:
        commands.append(("print", [python, "-B", "-m", "app.background", "--host", "127.0.0.1", "--port", str(backend["print"]),
                                    "--data", str(data_root / "printvaerksted"), "--watchdog"]))
        if simulation:
            commands[-1][1].append('--demo')
    if "design" not in external:
        commands.append(("design", [python, "-B", "-m", "app.laser_server", "--host", "127.0.0.1", "--port", str(backend["design"]),
                                     "--teacher-host", "127.0.0.1", "--teacher-port", str(backend["teacher"]),
                                     "--data", str(data_root / "designvaerksted")]))
    commands.append(("hub", [python, "-B", "hub/elevhub.py", "--konfiguration", str(data_root / "konfiguration.json"),
                              "--data", str(data_root / "hub")]))
    if runtime_root is not None:
        from app.generation_runtime import build_command
        return [(name, build_command(package_root, runtime_root, manifest_sha256, name,
                                     command[3:] if name == 'hub' else command[4:]))
                for name, command in commands]
    return commands


def run(package_root: Path, data_root: Path, role_ip: str | None = None,
        teacher_port: int = 8780, teacher_address: str | None = None,
        runtime_root: Path | None = None, manifest_sha256: str | None = None) -> int:
    if runtime_root is not None:
        # A direct invocation through the old embedded _pth must not turn a
        # validated selection into a supervisor using cached old app modules.
        package_root = package_root.resolve(strict=True)
        runtime_root = runtime_root.resolve(strict=True)
        if (Path(__file__).resolve(strict=True) != package_root / 'server/bootstrap.py'
                or Path(sys.executable).resolve(strict=True) != runtime_root / 'runtime/python.exe'):
            raise ValueError('Start den valgte generation gennem den faste startmekanisme')
        for name, module in tuple(sys.modules.items()):
            if name == 'app' or name.startswith('app.'):
                source = getattr(module, '__file__', None)
                if source is None or not Path(source).resolve(strict=True).is_relative_to(package_root / 'app'):
                    raise ValueError('Serveren har indlæst kode fra en anden programgeneration')
        from app.generation_runtime import assert_generation
        assert_generation(package_root, runtime_root, manifest_sha256)
    assert_not_restored(data_root)
    lock = InstanceLock(data_root)
    children = []
    try:
        assert_not_restored(data_root)
        write_protocol(data_root, 'server')
        config = read_runtime_config(data_root, role_ip, teacher_port, teacher_address)
        check(package_root, data_root, installed=True, role_ip=role_ip, teacher_port=teacher_port,
              teacher_address=teacher_address, runtime_root=runtime_root, manifest_sha256=manifest_sha256)
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        if (package_root / 'school-package.json').is_file():
            os.environ['ELEVHUB_SCHOOL_MODE'] = '1'
        from app.process_tree import ProcessTree  # imported only for runtime; check remains pure

        stopping = threading.Event()

        def request_stop(_signum=None, _frame=None):
            stopping.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        if not wait_for_role_ip(config['elev_adresse'], stopping):
            return 0
        log_dir = data_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        commands = (_commands(package_root, data_root, config, runtime_root, manifest_sha256)
                    if runtime_root is not None else _commands(package_root, data_root, config))
        environment = None
        child_cwd = package_root
        if runtime_root is not None:
            from app.generation_runtime import clean_environment
            environment = clean_environment()
            child_cwd = runtime_root / 'runtime'
        for name, command in commands:
            process = subprocess.Popen(command, cwd=str(child_cwd), env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                tree = ProcessTree(process)
            except Exception:
                process.kill()
                process.wait(timeout=10)
                process.stdout.close()
                raise
            try:
                stream = BoundedChildLog(process.stdout, log_dir / f"{name}.log")
            except Exception:
                try:
                    tree.close()
                finally:
                    if process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=10)
                    finally:
                        process.stdout.close()
                raise
            children.append((name, process, tree, stream))
        while not stopping.is_set():
            for name, process, _tree, log in children:
                if log.error is not None:
                    raise RuntimeError(f'Elevhub-tjenesten {name} kunne ikke skrive sin log') from log.error
                code = process.poll()
                if code is not None:
                    print(f"Elevhub-tjenesten {name} stoppede med kode {code}", file=sys.stderr)
                    return code if code else 1
            stopping.wait(0.5)
        return 0
    finally:
        # Closing each kill-on-close job terminates that complete process tree.
        for _name, _process, tree, _stream in reversed(children):
            try:
                tree.close()
            except Exception:
                pass
        for _name, process, _tree, stream in reversed(children):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                # Killing is asynchronous. Do not release the data lock while
                # a child can still write its files or inherited log stream.
                process.wait()
            stream.close()
        lock.close()


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--role-ip", help='Valgt serveradresse; ældre opgaver læser den bevarede installationskonfiguration.')
    parser.add_argument("--teacher-port", type=int, default=8780)
    parser.add_argument("--teacher-address")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check(args.package_root, args.data_root, args.installed, args.role_ip,
                               args.teacher_port, args.teacher_address, args.runtime_root,
                               args.manifest_sha256), sort_keys=True))
        return 0
    return run(args.package_root.resolve(strict=True), args.data_root.resolve(strict=True), args.role_ip,
               args.teacher_port, args.teacher_address, args.runtime_root, args.manifest_sha256)


if __name__ == "__main__":
    raise SystemExit(main())
