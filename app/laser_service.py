"""Windows service wrapper for the classroom laser inbox.

The wrapper owns the queue supervisor lock and the laser-server process tree.
It deliberately contains no Beam Studio, printer, or AI imports.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from app.instance import InstanceLock

FIELDS = ('data', 'host', 'port', 'teacher_host', 'teacher_port')


def load_config(path):
    path = Path(path)
    config = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or any(key not in config for key in FIELDS):
        raise ValueError('Laser-service-konfigurationen mangler felter.')
    if not all(isinstance(config[key], str) and config[key].strip() for key in ('data', 'host', 'teacher_host')):
        raise ValueError('Laser-service-konfigurationen har ugyldige værter eller data.')
    if type(config['port']) is not int or not 1 <= config['port'] <= 65535:
        raise ValueError('Laser-service-konfigurationen har en ugyldig port.')
    if type(config['teacher_port']) is not int or not 1 <= config['teacher_port'] <= 65535:
        raise ValueError('Laser-service-konfigurationen har en ugyldig lærerport.')
    return config


def command(config):
    return [sys.executable, '-m', 'app.laser_server', '--data', config['data'], '--host', config['host'],
            '--port', str(config['port']), '--teacher-host', config['teacher_host'],
            '--teacher-port', str(config['teacher_port'])]


class ProcessTree:
    """Own a child process and kill its descendants when the service exits."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt' or not hasattr(process, '_handle'):
            return
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        class Limits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
                        ('process_limit', wintypes.DWORD), ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Limits), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended(); limits.basic.flags = 0x2000
        try:
            if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle, self.kernel = handle, kernel
        except Exception:
            kernel.CloseHandle(handle)
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def supervise(config, *, popen=subprocess.Popen, sleep=time.sleep, stop_event=None, retry_seconds=2):
    data = Path(config['data']); data.mkdir(parents=True, exist_ok=True)
    service = data / 'service'; service.mkdir(parents=True, exist_ok=True)
    try:
        lock = InstanceLock(service)
    except RuntimeError:
        return 0
    stop_event = stop_event or _Event()
    output = (service / 'laser-server.log').open('ab')
    try:
        failures = 0
        while not stop_event.is_set():
            process = tree = None
            started = time.monotonic()
            try:
                process = popen(command(config), cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=output, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                tree = ProcessTree(process)
                while process.poll() is None and not stop_event.is_set():
                    stop_event.wait(0.25)
                if stop_event.is_set() and process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
                    return 0
                if process.returncode == 0:
                    return 0
                logging.warning('Laser-server exited with code %s; restarting', process.returncode)
            except OSError:
                logging.exception('Laser-server could not start; retrying')
            finally:
                if tree: tree.close()
                if process and process.poll() is None: process.kill(); process.wait()
            if time.monotonic() - started > 60:
                failures = 0
            else:
                failures += 1
            stop_event.wait(min(30, retry_seconds * 2 ** min(max(0, failures - 1), 4)))
        return 0
    finally:
        output.close(); lock.close()


class _Event:
    def __init__(self): self._stopped = False
    def is_set(self): return self._stopped
    def set(self): self._stopped = True
    def wait(self, seconds): time.sleep(seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    service = Path(config['data']) / 'service'; service.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(service / 'service.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(handlers=[handler], level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    stop = _Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try: return supervise(config, stop_event=stop)
    finally: handler.close()


if __name__ == '__main__':
    raise SystemExit(main())
