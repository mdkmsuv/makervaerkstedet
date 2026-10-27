#!/usr/bin/python3
"""Return from the dedicated local admin tty to the pupil kiosk tty."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CONFIG = Path('/etc/elevhub/admin-login.json')


def return_to_pupil(config=CONFIG, run=subprocess.run, euid=None, environ=None):
    if euid is None:
        euid = os.geteuid()
    if environ is None:
        environ = os.environ
    if euid != 0:
        raise RuntimeError('Skærmskift skal startes gennem den faste sudo-genvej.')
    info = os.lstat(config)
    if (config.is_symlink() or
            os.name != 'nt' and (info.st_mode & 0o022 or info.st_uid != 0)):
        raise RuntimeError('Indstillingerne for admin-login er ikke sikre.')
    username = json.loads(config.read_text(encoding='utf-8')).get('username', '')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', username) or username in ('root', 'elev', 'elevhub'):
        raise RuntimeError('Den lokale administrator er ikke sat op.')
    if environ.get('SUDO_USER') != username:
        raise RuntimeError('Kun den lokale administrator må afslutte admin-sessionen.')
    run(['/usr/bin/chvt', '1'], check=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print('Denne genvej modtager ikke argumenter.')
        return 2
    try:
        return_to_pupil()
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
