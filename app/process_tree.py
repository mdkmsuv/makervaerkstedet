"""Own child processes with Windows job objects and optional CPU limits."""
import ctypes
from ctypes import wintypes
import os


class ProcessTree:
    """Windows closes the entire owned process tree if this app exits or crashes."""
    def __init__(self, process, *, cpu_percent=None):
        self.handle = None
        if cpu_percent is not None and not 1 <= cpu_percent <= 100:
            raise ValueError('CPU limit must be between 1 and 100 percent.')
        if os.name != 'nt':
            return

        class Limits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t),
                        ('max_working', ctypes.c_size_t), ('process_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Limits), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            if not self.kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if cpu_percent is not None:
                class CpuRate(ctypes.Structure):
                    _fields_ = [('flags', wintypes.DWORD), ('rate', wintypes.DWORD)]
                # Windows enforces one combined budget for the process and all its descendants.
                # A thread count alone does not limit every model-loading phase.
                rate = CpuRate(0x1 | 0x4, int(cpu_percent * 100))
                if not self.kernel.SetInformationJobObject(handle, 15, ctypes.byref(rate), ctypes.sizeof(rate)):
                    raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.kernel.CloseHandle(handle)
            raise
        self.handle = handle

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
