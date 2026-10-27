"""Headless supervisor. Task Scheduler owns this process; it owns the server tree."""
import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import subprocess
import sys
import time

from .instance import InstanceLock
from .process_tree import ProcessTree
from .paths import ROOT, data_root, service_port
from .backup_guard import assert_not_restored
from .generation_runtime import child_command, child_cwd


def supervise(folder, port, host, demo=False, retry_seconds=2, server_command=None, watchdog=False):
    assert_not_restored(folder)
    folder.mkdir(parents=True, exist_ok=True)
    owner_folder = folder / 'supervisor'
    owner_folder.mkdir(exist_ok=True)
    try:
        lock = InstanceLock(owner_folder)
    except RuntimeError:
        return 0
    failures = 0
    stopped = folder / 'stopped-by-teacher'
    try:
        assert_not_restored(folder)
        command = child_command('app.server', ['--data', str(folder), '--port', str(port), '--host', host])
        if demo:
            command.append('--demo')
        if server_command is not None:
            command = server_command
        # Both Windows tasks take this lock before checking/changing stop intent.
        # Boot/desktop starts explicitly resume the service; the watchdog respects stop.
        if watchdog and stopped.exists():
            return 0
        stopped.unlink(missing_ok=True)
        while True:
            tree = None
            process = None
            started = time.monotonic()
            try:
                with (folder / 'startup.log').open('ab') as output:
                    process = subprocess.Popen(command, cwd=child_cwd(ROOT), stdin=subprocess.DEVNULL,
                        stdout=output, stderr=output, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                tree = ProcessTree(process)
                logging.info('Server started pid=%s', process.pid)
                code = process.wait()
                # An authenticated, intentional stop lasts until next boot or shortcut.
                if code == 0:
                    stopped.write_text('Service stopped deliberately. Queue pause is stored separately.\n', encoding='utf-8')
                    logging.info('Server stopped by request')
                    return 0
                logging.error('Server exited with code %s; restarting', code)
            except OSError:
                logging.exception('Server could not start; retrying')
            finally:
                if tree:
                    tree.close()
                if process and process.poll() is None:
                    process.kill()
                    process.wait()
            failures = 0 if time.monotonic() - started > 60 else failures + 1
            time.sleep(min(30, retry_seconds * 2 ** min(failures - 1, 4)))
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path)
    parser.add_argument('--port', type=int, default=service_port())
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--watchdog', action='store_true')
    args = parser.parse_args()
    folder = args.data or data_root() / ('demo' if args.demo else 'school')
    assert_not_restored(folder)
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / 'background.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(handlers=[handler], level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    sys.exit(supervise(folder, args.port, args.host, args.demo, watchdog=args.watchdog))


if __name__ == '__main__':
    main()
