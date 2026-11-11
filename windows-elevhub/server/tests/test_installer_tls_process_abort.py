"""Hard process termination during real Windows TLS provisioning."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "provision.py"
PROJECT_ROOT = SOURCE.parents[2]
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
SERVER_IP = "192.0.2.2"
OWNER_ID = "process-abort-tls-test-owner"


# The child calls provision.main() with the real init-tls command.  The only
# child-side test hook is a bounded pause after a durable atomic write, giving
# the parent a precise point at which to terminate this one owned process.
CHILD = textwrap.dedent(
    r"""
    import importlib.util
    import json
    import os
    from pathlib import Path
    import sys
    import time

    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    server_ip = sys.argv[3]
    owner_id = sys.argv[4]
    mode = sys.argv[5]
    marker = Path(sys.argv[6])

    spec = importlib.util.spec_from_file_location("process_abort_provision", source)
    provision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provision)
    # The child is deliberately non-admin in this source-level process test;
    # inject the ACL boundary while retaining real atomic writes and crypto.
    provision._restrict_private_path = lambda path: None
    real_atomic_write = provision._atomic_write

    def pause_after_checkpoint(path, text):
        real_atomic_write(path, text)
        should_pause = (
            (mode == "staged-journal" and path.name == provision.TLS_JOURNAL)
            or (mode == "first-public-ca" and path.name == "ca.crt")
            or (mode == "server-key" and path.name == "server.key")
            or (mode == "complete" and path.name == provision.TLS_JOURNAL
                and json.loads(text).get("state") == "complete")
        )
        if should_pause:
            marker_tmp = marker.with_suffix(marker.suffix + ".tmp")
            marker_tmp.write_text(json.dumps({
                "pid": os.getpid(),
                "executable": sys.executable,
                "base_executable": getattr(sys, "_base_executable", ""),
                "source": str(source),
            }), encoding="ascii")
            marker_tmp.replace(marker)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                time.sleep(0.02)
            raise RuntimeError("parent did not terminate the checkpointed child")

    provision._atomic_write = pause_after_checkpoint
    sys.argv = [
        str(source), "init-tls", "--output-dir", str(output),
        "--server-ip", server_ip, "--owner-id", owner_id,
    ]
    raise SystemExit(provision.main())
    """
)


@unittest.skipUnless(os.name == "nt", "these tests exercise Windows process termination")
class InstallerTlsProcessAbortTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        if not VENV_PYTHON.is_file():
            self.skipTest(f"project virtualenv is missing: {VENV_PYTHON}")

    @staticmethod
    def snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def wait_for_checkpoint(self, process: subprocess.Popen, marker: Path) -> None:
        deadline = time.monotonic() + 30
        while not marker.exists():
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=5)
                self.fail(
                    "TLS child exited before its checkpoint "
                    f"(returncode={process.returncode}, stderr={stderr!r})"
                )
            if time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=10)
                self.fail("TLS child did not reach its bounded checkpoint")
            time.sleep(0.02)

    @staticmethod
    def open_process_handle(pid: int, access: int):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(access, 0, pid)
        if not handle:
            error = ctypes.get_last_error()
            raise OSError(error, f"OpenProcess({pid}) failed")
        return kernel32, handle

    @staticmethod
    def parent_pid(kernel32, handle) -> int:
        class ProcessBasicInformation(ctypes.Structure):
            _fields_ = [
                ("reserved1", ctypes.c_void_p),
                ("peb_base_address", ctypes.c_void_p),
                ("reserved2", ctypes.c_void_p * 2),
                ("unique_process_id", ctypes.c_void_p),
                ("inherited_from_unique_process_id", ctypes.c_void_p),
            ]

        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtQueryInformationProcess.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        ]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long
        information = ProcessBasicInformation()
        returned = ctypes.c_ulong()
        status = ntdll.NtQueryInformationProcess(
            handle, 0, ctypes.byref(information), ctypes.sizeof(information),
            ctypes.byref(returned),
        )
        if status != 0:
            raise OSError(f"NtQueryInformationProcess failed: 0x{status & 0xffffffff:08x}")
        return int(information.inherited_from_unique_process_id)

    @staticmethod
    def terminate_process(kernel32, handle) -> None:
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateProcess.restype = ctypes.c_int
        if not kernel32.TerminateProcess(handle, 1):
            error = ctypes.get_last_error()
            raise OSError(error, "TerminateProcess failed")

    @staticmethod
    def wait_for_process_handle(kernel32, handle, timeout_ms: int) -> None:
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        result = kernel32.WaitForSingleObject(handle, timeout_ms)
        if result != 0:  # WAIT_OBJECT_0
            raise AssertionError(f"owned process handle did not signal: {result}")

    @staticmethod
    def close_process_handle(kernel32, handle) -> None:
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)

    def run_aborted_creation(self, mode: str) -> None:
        with tempfile.TemporaryDirectory(prefix="elevhub-tls-process-abort-") as tmp:
            root = Path(tmp)
            security = root / "security"
            marker = root / "checkpoint.ready"
            queue = root / "queue.json"
            queue.write_bytes(b"preserved queue")
            process = subprocess.Popen(
                [
                    str(VENV_PYTHON), "-B", "-c", CHILD,
                    str(SOURCE), str(security), SERVER_IP, OWNER_ID, mode,
                    str(marker),
                ],
                cwd=str(SOURCE.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            kernel32 = None
            process_handle = None
            try:
                self.wait_for_checkpoint(process, marker)
                checkpoint = json.loads(marker.read_text(encoding="ascii"))
                actual_pid = checkpoint["pid"]
                self.assertIsInstance(actual_pid, int)
                self.assertGreater(actual_pid, 0)
                self.assertEqual(Path(checkpoint["source"]).resolve(), SOURCE.resolve())
                self.assertEqual(Path(checkpoint["executable"]).resolve(), VENV_PYTHON.resolve())
                kernel32, process_handle = self.open_process_handle(
                    actual_pid, 0x00100000 | 0x00001000 | 0x00000001,
                )  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION | TERMINATE
                if actual_pid != process.pid:
                    # The venv executable can be a launcher.  The marker gives
                    # us the real provision.py process; prove it is the direct
                    # child of this Popen before terminating that PID.
                    self.assertEqual(self.parent_pid(kernel32, process_handle), process.pid)
                    self.terminate_process(kernel32, process_handle)
                    if process.poll() is None:
                        process.kill()
                else:
                    # In runtimes without a launcher, Popen owns this exact
                    # provision.py PID and its kill is sufficient.
                    process.kill()
                process.wait(timeout=10)
                self.wait_for_process_handle(kernel32, process_handle, 10_000)
                stdout, stderr = process.communicate(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                if kernel32 is not None and process_handle is not None:
                    self.close_process_handle(kernel32, process_handle)
                process.stdout.close()
                process.stderr.close()

            self.assertTrue(marker.is_file())
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "")

            journal_path = security / "tls-initialization.json"
            self.assertTrue(journal_path.is_file())
            journal_after_kill = json.loads(journal_path.read_text(encoding="utf-8"))
            before_resume = self.snapshot(root)
            material = journal_after_kill.get("material")

            if mode == "complete":
                self.assertEqual(journal_after_kill["state"], "complete")
                self.assertNotIn("material", journal_after_kill)
                identity_before_resume = {
                    name: (security / name).read_bytes()
                    for name in ("ca.crt", "server.crt", "server.key")
                }
            else:
                self.assertEqual(journal_after_kill["state"], "staged")
                self.assertIsInstance(material, dict)
                for name, value in material.items():
                    path = security / name
                    if path.exists():
                        self.assertEqual(path.read_bytes(), value.encode("ascii"))

            resume_code = textwrap.dedent(
                """
                import importlib.util, sys
                source = sys.argv[1]
                spec = importlib.util.spec_from_file_location('resume_provision', source)
                provision = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(provision)
                provision._restrict_private_path = lambda path: None
                sys.argv = [source] + sys.argv[2:]
                raise SystemExit(provision.main())
                """
            )
            resume = subprocess.run(
                [str(VENV_PYTHON), "-B", "-c", resume_code, str(SOURCE), "init-tls",
                 "--output-dir", str(security), "--server-ip", SERVER_IP,
                 "--owner-id", OWNER_ID, "--resume"],
                cwd=str(SOURCE.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            self.assertEqual(resume.returncode, 0, resume.stderr)
            self.assertEqual(resume.stdout, "")
            self.assertEqual(resume.stderr, "")

            after_resume = self.snapshot(root)
            self.assertEqual(before_resume["queue.json"], b"preserved queue")
            self.assertEqual(after_resume["queue.json"], b"preserved queue")
            for name in ("ca.crt", "server.crt", "server.key", "enheder.json"):
                path = security / name
                self.assertTrue(path.is_file(), name)
                if material is not None:
                    self.assertEqual(path.read_bytes(), material[name].encode("ascii"), name)
            if mode == "complete":
                self.assertEqual(
                    {name: (security / name).read_bytes() for name in identity_before_resume},
                    identity_before_resume,
                )

            completed = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertEqual(completed["state"], "complete")
            self.assertNotIn("material", completed)

    def test_kill_after_staged_journal_then_resume_keeps_identity(self):
        self.run_aborted_creation("staged-journal")

    def test_kill_after_first_public_ca_then_resume_keeps_identity(self):
        self.run_aborted_creation("first-public-ca")

    def test_kill_after_server_key_before_complete_then_resume_keeps_identity(self):
        self.run_aborted_creation("server-key")

    def test_kill_after_complete_receipt_then_resume_is_idempotent(self):
        self.run_aborted_creation("complete")


if __name__ == "__main__":
    unittest.main()
