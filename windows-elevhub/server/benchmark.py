"""Post-install Windows Elevhub benchmark; GET/status traffic only.

Normal runs require an explicitly selected server role IP. ``--check-only`` validates
the package without contacting a server or changing files outside the selected
data root. No ``/api/jobs`` or printer route is ever called.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import random
import shutil
import ssl
import statistics
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, HTTPRedirectHandler
from urllib.parse import urlsplit, urlunsplit

try:
    import psutil
except ImportError:  # check-only reports a useful failure
    psutil = None

try:
    # Share the administrator enrollment lock so benchmark fixtures cannot
    # race a real enrollment or lose an unrelated device entry.
    from provision import registry_lock as _provision_registry_lock
except ImportError:  # source-only unit tests can still exercise the fixture
    _provision_registry_lock = None

POLL_SECONDS = 3


def quantiles(values):
    values = sorted(float(v) for v in values)
    if not values:
        return {'count': 0, 'p50_ms': None, 'p95_ms': None, 'max_ms': None}
    def pick(q): return values[min(len(values) - 1, max(0, int((len(values) - 1) * q)))] * 1000
    return {'count': len(values), 'p50_ms': round(pick(.50), 2), 'p95_ms': round(pick(.95), 2), 'max_ms': round(values[-1] * 1000, 2)}


def memory_recommendation(samples, incomplete=False):
    """Return the measured VM peak recommendation, never an invented result."""
    if incomplete:
        return None
    used = [float(s['ram_used']) for s in samples if s.get('ram_used') is not None]
    if not used:
        return None
    import math
    return max(4, math.ceil(max(used) * 1.3 / (1024 ** 3)))


@contextmanager
def registry_lock(path: Path):
    """Small cross-platform lock for benchmark-only registry mutation."""
    lock = path.with_suffix(path.suffix + '.benchmark.lock')
    handle = lock.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0); handle.write(b'0'); handle.flush(); msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == 'nt':
                try:
                    handle.seek(0); import msvcrt; msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except PermissionError:
                    pass
            else:
                import fcntl; fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            lock.unlink(missing_ok=True)


def device_fixture(registry: Path, count=24):
    """Add uniquely named hashed devices and return clear tokens in memory only."""
    original = json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {'enheder': {}}
    original.setdefault('enheder', {})
    stamp = hashlib.sha256(f'{time.time_ns()}-{random.random()}'.encode()).hexdigest()[:10]
    entries, tokens = {}, {}
    for index in range(count):
        name = f'bench-{stamp}-{index:02d}'
        token = os.urandom(32).hex()
        ui = os.urandom(32).hex()
        entries[name] = {'token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'ui_token_sha256': hashlib.sha256(ui.encode()).hexdigest()}
        tokens[name] = (token, ui)
    with (_provision_registry_lock(registry) if _provision_registry_lock else registry_lock(registry)):
        current = json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {'enheder': {}}
        current.setdefault('enheder', {}).update(entries)
        registry.parent.mkdir(parents=True, exist_ok=True)
        temp = registry.with_suffix('.benchmark.tmp'); temp.write_text(json.dumps(current, indent=2), encoding='utf-8'); temp.replace(registry)
    return set(entries), tokens, entries


def cleanup_fixture(registry: Path, names, expected=None):
    with (_provision_registry_lock(registry) if _provision_registry_lock else registry_lock(registry)):
        if not registry.exists(): return
        current = json.loads(registry.read_text(encoding='utf-8'))
        devices = current.get('enheder', {})
        for name in names:
            if expected is None or devices.get(name) == expected.get(name):
                devices.pop(name, None)
        temp = registry.with_suffix('.benchmark.tmp'); temp.write_text(json.dumps(current, indent=2), encoding='utf-8'); temp.replace(registry)


class Sampler:
    def __init__(self, root: Path, install_root: Path | None = None):
        self.root, self.install_root, self.samples, self.stop = root, install_root or root, [], threading.Event()
        self._cpu = {}
        self.thread = threading.Thread(target=self._run, daemon=True)
    def start(self): self.thread.start()
    def _run(self):
        while not self.stop.wait(1):
            sample = {'time': time.time()}
            if psutil:
                vm, swap = psutil.virtual_memory(), psutil.swap_memory()
                sample.update(cpu_percent=psutil.cpu_percent(None), ram_used=vm.used, ram_available=vm.available, swap_used=swap.used,
                              disk_io=dict(psutil.disk_io_counters()._asdict()) if psutil.disk_io_counters() else {})
                try:
                    usage = shutil.disk_usage(self.root); sample.update(disk_used=usage.used, disk_free=usage.free, disk_total=usage.total)
                except OSError: pass
                try:
                    roots = [str(self.install_root.resolve()).lower(), str(self.root.resolve()).lower()]
                    processes = []
                    for proc in psutil.process_iter(['pid', 'exe', 'cmdline']):
                        if proc.pid == os.getpid():
                            continue
                        try:
                            text = ' '.join(proc.info.get('cmdline') or []) + ' ' + (proc.info.get('exe') or '')
                            if any(root in text.lower() for root in roots):
                                processes.append(proc)
                        except (psutil.Error, OSError, UnicodeError):
                            continue
                    rss = private = cpu = 0.0; pids = []
                    for proc in processes:
                        try:
                            info = proc.memory_info(); rss += info.rss
                            private += float(getattr(info, 'private', 0) or 0)
                            if proc.pid not in self._cpu:
                                proc.cpu_percent(None); self._cpu[proc.pid] = proc
                            else:
                                cpu += self._cpu[proc.pid].cpu_percent(None)
                            pids.append(proc.pid)
                        except (psutil.Error, OSError):
                            continue
                    logical = psutil.cpu_count(logical=True) or 1
                    sample.update(process_rss=rss, process_private=private,
                                  process_cpu_percent=cpu / logical,
                                  process_count=len(processes), process_pids=pids)
                except (psutil.Error, OSError):
                    pass
                for label, path in (('program', self.install_root), ('data', self.root)):
                    try:
                        total = used = 0
                        for item in path.rglob('*'):
                            if item.is_file():
                                try: used += item.stat().st_size
                                except OSError: pass
                        sample[f'{label}_bytes'] = used
                    except OSError:
                        sample[f'{label}_bytes'] = None
            self.samples.append(sample)
    def finish(self):
        self.stop.set(); self.thread.join(2)
        return self.samples


def request(url, headers=None, body=None, context=None, timeout=10, expect_json=False):
    started = time.perf_counter(); error = None; status = None
    try:
        req = Request(url, data=body, headers=headers or {}, method='POST' if body is not None else 'GET')
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl): return None
        import urllib.request
        handlers = [urllib.request.ProxyHandler({}), NoRedirect()]
        if context is not None: handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)
        with opener.open(req, timeout=min(timeout, 4)) as response:
            status = response.status; payload = response.read(2 * 1024 * 1024)
            if expect_json:
                parsed = json.loads(payload.decode('utf-8'))
                if not isinstance(parsed, dict): raise ValueError('invalid_json_object')
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        error = type(exc).__name__
        if isinstance(exc, HTTPError):
            status = exc.code
            exc.close()
    return time.perf_counter() - started, status, error


def run_worker(kind, report_dir, install_root, sampler):
    """Run heavy work with a hard timeout and VM pressure circuit breaker."""
    output = report_dir / f'{kind}.json'
    command = [sys.executable, str(Path(__file__).with_name('benchmark_worker.py')), '--kind', kind,
               '--output', str(output), '--install-root', str(install_root)]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic(); low_memory = 0; high_cpu = 0; stopped = None
    while process.poll() is None:
        if time.monotonic() - started >= 300:
            stopped = 'timeout_300s'; break
        recent = sampler.samples[-3:]
        if len(recent) == 3 and all(s.get('ram_available', 2**60) < 512 * 1024 * 1024 for s in recent):
            stopped = 'memory_pressure'; break
        latest = sampler.samples[-1] if sampler.samples else {}
        if latest.get('cpu_percent', 0) > 95:
            high_cpu += 1
        else:
            high_cpu = 0
        if high_cpu >= 20:
            stopped = 'cpu_pressure'; break
        time.sleep(1)
    if stopped:
        try:
            if psutil:
                try:
                    tree = psutil.Process(process.pid)
                    children = tree.children(recursive=True)
                    for child in children: child.terminate()
                    tree.terminate()
                except psutil.Error:
                    process.terminate()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        return {'ok': False, 'incomplete': True, 'stop_reason': stopped}
    try:
        return json.loads(output.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'ok': False, 'incomplete': True, 'returncode': process.returncode}


def phase(base, ca, names, tokens, duration, users=12, queue_port=8770, laser_port=8771):
    records, errors = [], []
    end = time.monotonic() + duration
    parsed = urlsplit(base)
    def endpoint(port, path):
        return urlunsplit((parsed.scheme, f'{parsed.hostname}:{port}', path, '', ''))
    def loop(name):
        root, ui = tokens[name]
        headers = {'Authorization': 'Bearer ' + root, 'X-Elevhub-Computer': name, 'Accept': 'application/json'}
        ui_headers = {'Authorization': 'Bearer ' + ui, 'X-Elevhub-Computer': name, 'Content-Type': 'application/json'}
        while time.monotonic() < end:
            payload = json.dumps({'computer': name, 'kilde': 'benchmark', 'version': 1}).encode()
            for url, hdr, body in ((endpoint(queue_port, '/api/state'), headers, None),
                                   (endpoint(laser_port, '/api/laser/health'), headers, None),
                                   (base + '/api/enhed/politik', headers, None),
                                   (base + '/api/status', ui_headers, payload),
                                   (base + '/api/enhed/status', {**headers, 'Content-Type': 'application/json'}, payload)):
                latency, status, error = request(url, hdr, body, ca, expect_json=url.endswith('/api/enhed/politik'))
                records.append(latency)
                if error or (status and status >= 400): errors.append(error or str(status))
            time.sleep(POLL_SECONDS)
    threads = [threading.Thread(target=loop, args=(name,), daemon=True) for name in list(names)[:users]]
    [t.start() for t in threads]; [t.join() for t in threads]
    return {'latency': quantiles(records), 'errors': len(errors), 'requests': len(records)}


def phase_resources(samples, start, end):
    selected = samples[start:end] or samples[-1:]
    def peak(key):
        values = [s[key] for s in selected if s.get(key) is not None]
        return max(values) if values else None
    return {'samples': len(selected), 'peak_ram_used': peak('ram_used'), 'peak_cpu_percent': peak('cpu_percent'),
            'peak_process_rss': peak('process_rss'), 'peak_process_private': peak('process_private'),
            'peak_disk_used': peak('disk_used'), 'peak_program_bytes': peak('program_bytes'), 'peak_data_bytes': peak('data_bytes')}


def role_ip_assigned(role_ip):
    if psutil is None: return False
    try:
        return any(any(getattr(address, 'address', '').split('%')[0] == role_ip for address in addresses)
                   for addresses in psutil.net_if_addrs().values())
    except (psutil.Error, OSError):
        return False


def check(args):
    if not args.install_root.is_dir() or not args.data_root.is_dir(): raise ValueError('Install/data-root mangler.')
    try:
        address = ipaddress.ip_address(args.role_ip)
        if not isinstance(address, ipaddress.IPv4Address) or address.is_unspecified or address.is_multicast or address.is_loopback:
            raise ValueError
        args.role_ip = str(address)
    except ValueError as error:
        raise ValueError(f'Ugyldig serveradresse: {args.role_ip!r}') from error
    if not args.check_only and (psutil is None or not role_ip_assigned(args.role_ip)):
        raise ValueError(f'Rolle-IP {args.role_ip} er ikke tildelt denne server.')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--install-root', type=Path, required=True); parser.add_argument('--data-root', type=Path, required=True); parser.add_argument('--quick', action='store_true'); parser.add_argument('--check-only', action='store_true'); parser.add_argument('--role-ip', required=True)
    args = parser.parse_args(); check(args)
    if args.check_only: print(json.dumps({'ok': True, 'role_ip': args.role_ip, 'psutil': getattr(psutil, '__version__', None)})); return 0
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); report_dir = args.data_root / 'benchmarks' / stamp; report_dir.mkdir(parents=True)
    base = f'https://{args.role_ip}:8773'; ca_file = args.data_root / 'security' / 'ca.crt'
    if not ca_file.exists():
        raise ValueError('CA-certifikat mangler under data-root/security/ca.crt.')
    context = ssl.create_default_context(cafile=str(ca_file))
    registry = args.data_root / 'security' / 'enheder.json'; names, tokens, entries = device_fixture(registry)
    sampler = Sampler(args.data_root, args.install_root); sampler.start(); phases = {}
    heavy = {}
    try:
        idle = 3 if args.quick else 30; start = len(sampler.samples); time.sleep(idle); phases['idle'] = {'seconds': idle, 'resources': phase_resources(sampler.samples, start, len(sampler.samples))}
        duration = 5 if args.quick else 90
        start = len(sampler.samples); phases['load12'] = phase(base, context, names, tokens, duration, 12); phases['load12']['resources'] = phase_resources(sampler.samples, start, len(sampler.samples))
        start = len(sampler.samples); phases['load24'] = phase(base, context, names, tokens, duration, 24); phases['load24']['resources'] = phase_resources(sampler.samples, start, len(sampler.samples))
        if not args.quick:
            for kind in ('slicer',):
                start = len(sampler.samples)
                heavy[kind] = run_worker(kind, report_dir, args.install_root, sampler)
                heavy[kind]['resources'] = phase_resources(sampler.samples, start, len(sampler.samples))
    finally:
        samples = sampler.finish(); cleanup_fixture(registry, names, entries)
    result = {'generated_utc': stamp, 'role_ip': args.role_ip, 'phases': phases, 'heavy': heavy, 'samples': samples,
              'hardware': {'logical_cpus': psutil.cpu_count(logical=True) if psutil else None, 'physical_cpus': psutil.cpu_count(logical=False) if psutil else None, 'total_ram': psutil.virtual_memory().total if psutil else None,
                           'disk': (dict(zip(('total', 'used', 'free'), shutil.disk_usage(args.data_root))) if args.data_root.exists() else None)},
              'recommended_ram_gb': (None if args.quick or any(not item.get('ok', False) or item.get('incomplete') for item in heavy.values()) or any(p.get('errors', 0) for p in phases.values() if isinstance(p, dict)) else memory_recommendation(samples)),
              'limitations': ['Ingen fysisk 12-PC end-to-end-test.', 'Ingen GPU/browserklientmåling.', 'Syntetiske benchmarkmodeller er ikke største produktionsmodeller.', 'Proces-RSS kan overlappe delt hukommelse og summeres derfor konservativt.']}
    (report_dir / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = '\n'.join(f'| {name} | {phase.get("latency", {}).get("p50_ms", "-")} | {phase.get("latency", {}).get("p95_ms", "-")} | {phase.get("latency", {}).get("max_ms", "-")} | {phase.get("errors", "-")} |' for name, phase in phases.items() if isinstance(phase, dict))
    md = ('# Elevhub serverbenchmark\n\n'
          f'Genereret {stamp} UTC på rolle-IP **{args.role_ip}**. Målingerne er hentet fra VM’en og gemt i `report.json`.\n\n'
          '## Resultat\n\n'
          f'- Anbefalet VM-RAM: **{result["recommended_ram_gb"] or "ukendt"} GB** (observeret peak × 1,3, minimum 4 GB).\n'
          '\n| Fase | p50 ms | p95 ms | maks ms | fejl |\n|---|---:|---:|---:|---:|\n' + rows + '\n\n'
          f'- Hardware: {result["hardware"]}.\n'
          '- Driftskriterium: 12-elev p95 under 1 sekund og nul fejl.\n\n'
          '## Begrænsninger\n\n'
          '\n'.join(f'- {item}' for item in result['limitations']) + '\n')
    (report_dir / 'REPORT.md').write_text(md, encoding='utf-8')
    import html
    html_doc = '<!doctype html><meta charset="utf-8"><title>Elevhub benchmark</title><style>body{font:14px Arial;color:#421b25}table{border-collapse:collapse}td,th{border:1px solid #bda8ad;padding:5px}</style><h1>Elevhub serverbenchmark</h1><table><tr><th>Fase</th><th>p50 ms</th><th>p95 ms</th><th>maks ms</th><th>fejl</th></tr>'
    for name, phase in phases.items():
        if isinstance(phase, dict):
            q = phase.get('latency', {})
            html_doc += '<tr>' + ''.join(f'<td>{html.escape(str(value))}</td>' for value in (name, q.get('p50_ms','-'), q.get('p95_ms','-'), q.get('max_ms','-'), phase.get('errors','-'))) + '</tr>'
    html_doc += '</table><h2>Hardware og måledata</h2><pre>' + html.escape(json.dumps({'hardware': result['hardware'], 'phases': phases, 'limitations': result['limitations']}, ensure_ascii=False, indent=2)) + '</pre>'
    (report_dir / 'REPORT.html').write_text(html_doc, encoding='utf-8')
    print(json.dumps({'report': str(report_dir), 'phases': phases}, ensure_ascii=False)); return 0


if __name__ == '__main__': raise SystemExit(main())
