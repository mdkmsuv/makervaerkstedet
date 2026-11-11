"""OS-held lock, released even when the process crashes."""
import os
import stat
from pathlib import Path


class InstanceLock:
    def __init__(self, folder, *, filename='instance.lock'):
        if (not isinstance(filename, str) or filename in ('', '.', '..')
                or any(c in filename for c in '\\/:*?"<>|') or any(not c.isprintable() for c in filename)
                or filename.endswith((' ', '.'))):
            raise ValueError('Låsefilen skal være et enkelt lokalt filnavn.')
        path = Path(folder).absolute() / filename
        for item in (path, *path.parents):
            try: info = item.lstat()
            except FileNotFoundError: continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Låsens sti må ikke indeholde links eller reparse points.')
        self.file = path.open('a+b')
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
            raise RuntimeError('En tjeneste kører allerede med denne datamappe.') from None

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
