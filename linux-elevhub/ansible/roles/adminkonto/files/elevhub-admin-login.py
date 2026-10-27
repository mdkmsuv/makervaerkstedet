#!/usr/bin/python3
"""Open the fixed local login console; this never logs in or starts a root shell."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CONFIG = Path('/etc/elevhub/admin-login.json')


def open_login(config=CONFIG, run=subprocess.run):
    if os.geteuid() != 0:
        raise RuntimeError('Admin-login skal startes gennem den installerede genvej.')
    metadata = config.stat()
    if config.is_symlink() or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise RuntimeError('Indstillingerne for admin-login er ikke sikre.')
    username = json.loads(config.read_text(encoding='utf-8')).get('username', '')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', username) or username in ('root', 'elev', 'elevhub'):
        raise RuntimeError('Den lokale administrator er ikke sat op.')
    # Only check whether login is available. Never print the password hash.
    account = run(['/usr/bin/getent', 'shadow', username], capture_output=True, text=True, check=True)
    fields = account.stdout.strip().split(':')
    if len(fields) < 2 or fields[0] != username or not fields[1].startswith('$'):
        raise RuntimeError('Administratorkontoen mangler en adgangskode.')
    run(['/usr/bin/systemctl', 'start', 'getty@tty2.service'], check=True, timeout=15)
    run(['/usr/bin/chvt', '2'], check=True, timeout=5)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 1:
            raise RuntimeError('Admin-login modtager ingen argumenter.')
        open_login()
    except Exception:
        print('Admin-login kunne ikke åbnes. Kontroller opsætningen via SSH.', file=sys.stderr)
        raise SystemExit(1)
