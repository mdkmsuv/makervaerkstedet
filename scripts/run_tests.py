"""Run bounded, non-privileged project tests with isolated test processes.

Every test process owns its descendants, and results are reported as each
source file or suite finishes. Some fixtures start isolated headless browsers.
The runner never starts an installer, system service, VM, or elevated process.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import sys
import time


ROOT = Path(__file__).resolve().parents[1]

# These are the maintained Python test entry points.  Keep each invocation
# independent so unittest's import state cannot leak between suites.
SUITES = (
    ("node-cjs", None, None),
    ("windows-shared", "tests", "test_*.py"),
    ("school-setup", "deployment/school-setup", "test_*.py"),
    ("windows-server", "windows-elevhub/server/tests", "test_*.py"),
    ("linux-server", "linux-elevhub/server/elevhub", "test_*.py"),
    ("linux-client", "linux-elevhub/tests", "test_*.py"),
)


def _standard_cjs_tests() -> list[Path]:
    """Return ordinary CJS fixtures, excluding opt-in browser audits."""
    paths = list((ROOT / "tests").glob("*.cjs"))
    paths += list((ROOT / "linux-elevhub" / "tests").glob("*.cjs"))
    paths += list((ROOT / "linux-elevhub" / "server" / "microbit-controller").glob("test-*.cjs"))
    paths += list((ROOT / "scripts").glob("test_*.cjs"))
    return sorted(path for path in paths if path.is_file())


# Ordinary browser journeys now start and close their own disposable services.
# External audits (for example the official MakeCode iframe) are separate
# opt-in scripts; no ordinary pupil journey is silently skipped as a demo.
_CJS_ENVIRONMENT_SKIPS: dict[str, str] = {}


def _decode(value: bytes) -> str:
    for encoding in ("utf-8-sig", "cp850", "cp1252"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace")


def _safe_output(value: str, limit: int = 5000) -> str:
    """Keep reports useful without copying credentials from a failed test."""
    value = re.sub(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", "[redacted certificate/key]", value,
                   flags=re.DOTALL)
    value = re.sub(r"(?i)(password|token|secret|private[_ -]?key)\s*[:=]\s*[^\s,;]+",
                   r"\1=[redacted]", value)
    if len(value) > limit:
        value = value[-limit:]
        value = "[earlier output omitted]\n" + value
    return value


def _unittest_summary(result: dict[str, object]) -> str:
    text = str(result.get("stdout", "")) + "\n" + str(result.get("stderr", ""))
    ran = re.search(r"Ran (\d+) tests? in ([0-9.]+)s", text)
    if not ran:
        return ""
    skipped = re.search(r"skipped[=:] ?(\d+)", text, re.IGNORECASE)
    suffix = f", skipped {skipped.group(1)}" if skipped else ""
    return f"Ran {ran.group(1)} tests in {ran.group(2)}s{suffix}"


def _local_python() -> str:
    candidate = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(candidate) if candidate.is_file() else sys.executable


def _wsl_root() -> str | None:
    if os.name != "nt":
        return None
    drive = ROOT.drive.rstrip(":").lower()
    if not drive:
        return None
    return "/mnt/" + drive + "/" + str(ROOT.relative_to(ROOT.anchor)).replace("\\", "/")


def _command(suite_path: str, pattern: str, *, wsl: bool, timeout: float = 900) -> list[str]:
    args = ["-B", "-m", "unittest", "discover", "-s", suite_path, "-p", pattern, "-q"]
    if wsl:
        return ["wsl.exe", "--cd", _wsl_root() or ".", "--", "python3", "-B",
                "scripts/test_wsl_supervisor.py", str(timeout), "python3", *args]
    return [_local_python(), *args]


class _WindowsTestJob:
    """Contain the waiting supervisor before it can launch a venv redirector."""
    def __init__(self, process: subprocess.Popen):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t),
                        ('max_working', ctypes.c_size_t), ('process_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                         ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateJobObject.restype = wintypes.BOOL
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                           ctypes.c_void_p, wintypes.DWORD,
                                                           ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits),
                                                        ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.handle:
            # Closing a kill-on-close job initiates process termination but
            # can return before a child releases its cwd and output handles.
            # Terminate first, then wait briefly for the kernel's active count
            # to reach zero so the next test can remove its fixture directory.
            import ctypes
            from ctypes import wintypes

            class Accounting(ctypes.Structure):
                _fields_ = [('user_time', ctypes.c_longlong),
                            ('kernel_time', ctypes.c_longlong),
                            ('period_user_time', ctypes.c_longlong),
                            ('period_kernel_time', ctypes.c_longlong),
                            ('page_faults', wintypes.DWORD),
                            ('total_processes', wintypes.DWORD),
                            ('active_processes', wintypes.DWORD),
                            ('terminated_processes', wintypes.DWORD)]

            self.kernel.TerminateJobObject(self.handle, 1)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                count = Accounting()
                if not self.kernel.QueryInformationJobObject(
                        self.handle, 1, ctypes.byref(count), ctypes.sizeof(count), None):
                    break
                if count.active_processes == 0:
                    break
                time.sleep(0.02)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _capture(command: list[str], *, cwd: Path | None, timeout: float,
             env: dict[str, str] | None = None) -> tuple[int | None, bool, bytes, bytes]:
    """Capture a test with a deadline that owns its descendants as well."""
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = None
        job = None
        try:
            if os.name == 'nt':
                supervisor = Path(__file__).with_name('test_process_supervisor.py')
                process = subprocess.Popen([sys._base_executable, '-B', '-u', str(supervisor)],
                                           stdin=subprocess.PIPE, stdout=stdout_file,
                                           stderr=stderr_file, cwd=ROOT,
                                           env=env,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                job = _WindowsTestJob(process)
                request = {'command': command, 'cwd': str(cwd) if cwd is not None else None}
                assert process.stdin is not None
                process.stdin.write((json.dumps(request) + '\n').encode('utf-8'))
                process.stdin.close()
            else:
                process = subprocess.Popen(command, cwd=cwd or ROOT, stdin=subprocess.DEVNULL,
                                           stdout=stdout_file, stderr=stderr_file,
                                           env=env,
                                           start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
                expired = False
            except subprocess.TimeoutExpired:
                code = None
                expired = True
        finally:
            # Closing a Windows job kills every descendant, including the
            # base interpreter behind a venv launcher. POSIX uses one process
            # group for the same lifetime rule.
            if job is not None:
                job.close()
            elif process is not None and os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                if process.poll() is None:
                    process.kill()
                process.wait()
        stdout_file.seek(0)
        stderr_file.seek(0)
        return code, expired, stdout_file.read(), stderr_file.read()


def _run(name: str, path: str, pattern: str, *, timeout: float, wsl: bool,
         workdir: Path | None = None, env: dict[str, str] | None = None) -> dict[str, object]:
    command = _command(path, pattern, wsl=wsl, timeout=timeout)
    started = time.monotonic()
    try:
        code, expired, stdout_bytes, stderr_bytes = _capture(
            command, cwd=None if wsl else (workdir or ROOT),
            timeout=timeout + 30 if wsl else timeout, env=env)
        linux_timeout = wsl and code == 124 and b'WSL_TEST_TIMEOUT:' in stderr_bytes
        return {"name": name, "status": "timeout" if expired or linux_timeout else "passed" if code == 0 else "failed",
                "returncode": code, "seconds": time.monotonic() - started,
                "stdout": _decode(stdout_bytes), "stderr": _decode(stderr_bytes), "command": command}
    except OSError as error:
        return {"name": name, "status": "error", "returncode": None,
                "seconds": time.monotonic() - started, "stdout": "", "stderr": str(error),
                "command": command}


def _run_node(path: Path, *, timeout: float) -> dict[str, object]:
    node = shutil.which("node") or shutil.which("node.exe")
    name = "node:" + path.relative_to(ROOT).as_posix()
    skip_reason = _CJS_ENVIRONMENT_SKIPS.get(path.name)
    if skip_reason:
        return {"name": name, "status": "skipped", "returncode": None, "seconds": 0,
                "stdout": "", "stderr": skip_reason, "command": []}
    if not node:
        return {"name": name, "status": "skipped", "returncode": None, "seconds": 0,
                "stdout": "", "stderr": "Node.js unavailable.", "command": []}
    started = time.monotonic()
    try:
        code, expired, stdout_bytes, stderr_bytes = _capture([node, str(path)], cwd=ROOT, timeout=timeout)
        return {"name": name, "status": "timeout" if expired else "passed" if code == 0 else "failed",
                "returncode": code, "seconds": time.monotonic() - started,
                "stdout": _decode(stdout_bytes), "stderr": _decode(stderr_bytes),
                "command": [node, str(path)]}
    except OSError as error:
        return {"name": name, "status": "error", "returncode": None,
                "seconds": time.monotonic() - started, "stdout": "", "stderr": str(error),
                "command": [node, str(path)]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=900,
                        help="maximum seconds per Python suite/file (default: 900)")
    parser.add_argument("--no-wsl", action="store_true", help="skip Linux suites on Windows")
    parser.add_argument("--suite", action="append", choices=[entry[0] for entry in SUITES],
                        help="run only the named suite; repeat for multiple suites")
    args = parser.parse_args()
    selected = set(args.suite or (entry[0] for entry in SUITES))
    is_windows = platform.system().lower() == "windows"
    wsl_available = bool(is_windows and shutil.which("wsl.exe") and _wsl_root())
    results: list[dict[str, object]] = []

    print("Makerspace test report (non-privileged, no install/service actions)", flush=True)

    def record(result: dict[str, object]) -> None:
        results.append(result)
        print(f"[{result['status'].upper():7}] {result['name']} ({result['seconds']:.1f}s)", flush=True)
        summary = _unittest_summary(result)
        if summary:
            print("  " + summary, flush=True)
        if result['status'] == 'skipped' and result.get('stderr'):
            print("  skip: " + str(result['stderr']), flush=True)
        if result['status'] in {'failed', 'timeout', 'error'}:
            output = _safe_output((str(result.get('stdout', '')) + '\n' + str(result.get('stderr', ''))).strip())
            if output:
                print(output, flush=True)

    for name, path, pattern in SUITES:
        if name not in selected:
            continue
        if name == "node-cjs":
            for test in _standard_cjs_tests():
                print(f"[START  ] node:{test.relative_to(ROOT).as_posix()}", flush=True)
                record(_run_node(test, timeout=min(args.timeout, 180)))
            continue
        if name == "windows-shared":
            # A single 1,000+ case discovery hid the current test for up to
            # fifteen minutes. Each source file now has an independent,
            # bounded process; one slow fixture cannot hide later results.
            for test in sorted((ROOT / path).glob(pattern)):
                if test.is_file():
                    file_name = f"{name}:{test.name}"
                    print(f"[START  ] {file_name}", flush=True)
                    record(_run(file_name, path, test.name,
                                timeout=args.timeout, wsl=False))
            continue
        if name == "linux-client":
            if is_windows and (args.no_wsl or not wsl_available):
                record({"name": name, "status": "skipped", "returncode": None,
                        "seconds": 0, "stdout": "", "stderr": "WSL unavailable or disabled.",
                        "command": []})
                continue
            # Each Xlib/Tk fixture owns a private Xvfb. A fresh interpreter
            # per source file prevents a prior display's Tcl state from
            # disconnecting a later one and obscuring all remaining results.
            for test in sorted((ROOT / path).glob(pattern)):
                if test.is_file():
                    file_name = f"{name}:{test.name}"
                    print(f"[START  ] {file_name}", flush=True)
                    record(_run(file_name, path, test.name,
                                timeout=min(args.timeout, 300), wsl=is_windows))
            continue
        # Keep the Linux server suite in the WSL platform lane on Windows.
        if is_windows and name.startswith("linux-"):
            if args.no_wsl or not wsl_available:
                record({"name": name, "status": "skipped", "returncode": None,
                        "seconds": 0, "stdout": "", "stderr": "WSL unavailable or disabled.",
                        "command": []})
                continue
            print(f"[START  ] {name}", flush=True)
            record(_run(name, path, pattern, timeout=args.timeout, wsl=True))
        else:
            print(f"[START  ] {name}", flush=True)
            record(_run(name, path, pattern, timeout=args.timeout, wsl=False))

    # Keep the aggregate line ASCII so a legacy Windows console cannot
    # corrupt the report after the per-file progress has been emitted.
    failed = [result for result in results if result["status"] not in {"passed", "skipped"}]
    print(f"Checks: {len(results)}; failed/timeouts: {len(failed)}; skipped: "
          f"{sum(result['status'] == 'skipped' for result in results)}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
