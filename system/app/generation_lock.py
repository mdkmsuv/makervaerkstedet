"""Small OS-backed shared/exclusive lock for an existing generation marker.

The lock file is deliberately never created, modified, or removed.  Its
contents are an administrator-owned marker; only the operating-system lock is
used for coordination.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def _reject_links(path: Path) -> None:
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise FileNotFoundError(str(current)) from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("generation lock path may not contain links or reparse points")
        if current.parent == current:
            break
        current = current.parent


def _identity(path: Path, descriptor: int):
    path_info = path.stat()
    handle_info = os.fstat(descriptor)
    return (path_info.st_dev, path_info.st_ino, stat.S_IFMT(path_info.st_mode)), (
        handle_info.st_dev, handle_info.st_ino, stat.S_IFMT(handle_info.st_mode)
    )


class GenerationLock:
    """Acquire a non-blocking shared or exclusive lock on an existing file."""

    def __init__(self, path, *, exclusive=False):
        raw = Path(path)
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        if os.name == "nt" and str(raw).startswith(("\\\\", "//")):
            raise ValueError("generation lock path must be local")
        _reject_links(raw)
        raw = raw.resolve(strict=True)
        _reject_links(raw)
        self.path = raw
        self.exclusive = bool(exclusive)
        self._fd = None
        self._handle = None
        _reject_links(raw)
        if not stat.S_ISREG(raw.stat().st_mode):
            raise ValueError("generation lock must be a regular file")
        try:
            if os.name == "nt":
                self._open_windows()
            else:
                self._fd = os.open(str(raw), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
                before = _identity(raw, self._fd)
                if before[0] != before[1]:
                    raise RuntimeError("generation lock path changed")
                import fcntl
                fcntl.flock(self._fd, (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                _reject_links(raw)
                after = _identity(raw, self._fd)
                if after[0] != after[1] or after != before:
                    raise RuntimeError("generation lock path changed")
        except BlockingIOError:
            self.close()
            raise RuntimeError("generation lock is already held") from None
        except OSError as exc:
            self.close()
            if getattr(exc, "errno", None) in (11, 33):
                raise RuntimeError("generation lock is already held") from None
            raise
        except BaseException:
            self.close()
            raise

    def _open_windows(self):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        INVALID = wintypes.HANDLE(-1).value
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        access = 0x80000000 | (0x40000000 if self.exclusive else 0)
        share = 0x00000001 | 0x00000002  # Never allow replacement of a leased lock file.
        handle = kernel.CreateFileW(str(self.path), access, share, None, 3, 0x80, None)
        if handle == INVALID:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        msvcrt = __import__("msvcrt")
        try:
            msvcrt_fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        except BaseException:
            kernel.CloseHandle(handle)
            self._handle = None
            raise
        self._fd = msvcrt_fd
        self._handle = None  # open_osfhandle transferred ownership to the fd.
        kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        kernel.SetHandleInformation.restype = wintypes.BOOL
        if not kernel.SetHandleInformation(wintypes.HANDLE(msvcrt.get_osfhandle(msvcrt_fd)), 1, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        before = _identity(self.path, msvcrt_fd)
        _reject_links(self.path)
        if before[0] != before[1]:
            raise RuntimeError("generation lock path changed")
        class OVERLAPPED(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                        ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]
        self._overlapped = OVERLAPPED()
        kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
        kernel.LockFileEx.restype = wintypes.BOOL
        flags = 0x00000001 | (0x00000002 if self.exclusive else 0)
        if not kernel.LockFileEx(wintypes.HANDLE(msvcrt.get_osfhandle(msvcrt_fd)), flags, 0, 1, 0, ctypes.byref(self._overlapped)):
            error = ctypes.get_last_error()
            if error in (32, 33):
                raise RuntimeError("generation lock is already held")
            raise ctypes.WinError(error)
        after = _identity(self.path, msvcrt_fd)
        _reject_links(self.path)
        if after[0] != after[1] or after != before:
            raise RuntimeError("generation lock path changed")

    def close(self):
        fd, self._fd = self._fd, None
        if fd is None:
            handle, self._handle = self._handle, None
            if handle:
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle.restype = wintypes.BOOL
                kernel.CloseHandle(handle)
            return
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD, ctypes.c_void_p]
            kernel.UnlockFileEx.restype = wintypes.BOOL
            # Unlock failure during process teardown is not actionable.
            if getattr(self, "_overlapped", None) is not None:
                kernel.UnlockFileEx(wintypes.HANDLE(__import__("msvcrt").get_osfhandle(fd)), 0, 1, 0,
                                    ctypes.byref(self._overlapped))
        os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
