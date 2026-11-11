"""Bound a WSL test and its Linux descendants before Windows' outer deadline.

Killing wsl.exe on Windows does not reliably kill a process already running in
the Linux VM. This supervisor owns a Linux process group and reaps it on every
normal exit, failure, timeout, or catchable termination signal.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('seconds', type=float)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.seconds <= 0 or not args.command:
        return 125
    child = None

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        child = subprocess.Popen(args.command, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        try:
            return child.wait(timeout=args.seconds)
        except subprocess.TimeoutExpired:
            print('WSL_TEST_TIMEOUT: Linux test process group exceeded its deadline.',
                  file=sys.stderr, flush=True)
            return 124
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f'WSL_TEST_START_ERROR: {error}', file=sys.stderr, flush=True)
        return 125
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
