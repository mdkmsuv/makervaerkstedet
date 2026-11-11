#!/usr/bin/python3 -I
"""Root-owned, narrowly permitted shutdown helper; never execute a shell."""
import os
import subprocess
import sys
sys.path.insert(0, '/usr/local/lib/elevhub')
from update_status import blocks_power, message, read_status


def power_command(args):
    if len(args) != 1 or args[0] not in ('poweroff', 'reboot'):
        raise ValueError('Brug poweroff eller reboot')
    # Normal systemd shutdown flushes filesystems and stops services in order.
    # Never --force, skip inhibitors, clear projects or kill applications here.
    return ['/usr/bin/systemctl', '--no-block', args[0]]


def main(args):
    command = power_command(args)
    if os.geteuid() != 0:
        raise PermissionError('Kræver den godkendte systemgenvej')
    status = read_status()
    if blocks_power(status):
        raise RuntimeError(message(status) or 'Opdateringen kører. Vent med at slukke eller genstarte.')
    subprocess.run(command, check=True, timeout=12,
                   env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (ValueError, PermissionError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
