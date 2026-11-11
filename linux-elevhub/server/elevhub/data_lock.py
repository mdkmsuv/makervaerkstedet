"""Process lock shared by the hub and the backup/restore tooling."""
import os
import stat
from pathlib import Path


class InstanceLock:
    """Hold ``instance.lock`` exclusively for the lifetime of a process."""

    def __init__(self, folder):
        self.path = Path(folder).absolute() / 'instance.lock'
        for item in (self.path, *self.path.parents):
            try: info = item.lstat()
            except FileNotFoundError: continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Låsens sti må ikke indeholde links eller reparse points.')
        self.file = self.path.open('a+b')
        try:
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b'1')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('Elevhub kører allerede med denne datamappe.') from None
        self._closed = False

    def close(self):
        if not self._closed:
            self._closed = True
            self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
