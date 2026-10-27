"""Start one test command only after the runner has contained this process tree.

This helper uses the host Python so a Windows venv redirector cannot spawn its
base interpreter before the runner has assigned the helper to a Job Object.
"""
from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    line = sys.stdin.readline()
    if not line:
        return 125
    try:
        request = json.loads(line)
        if (type(request) is not dict or set(request) != {'command', 'cwd'}
                or type(request['command']) is not list or not request['command']
                or any(type(item) is not str for item in request['command'])
                or request['cwd'] is not None and type(request['cwd']) is not str):
            return 125
        return subprocess.call(request['command'], cwd=request['cwd'], stdin=subprocess.DEVNULL)
    except (OSError, ValueError, TypeError):
        return 125


if __name__ == '__main__':
    raise SystemExit(main())
