"""Narrow Windows host restart bridge; never accept executable paths or commands."""
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys


def _invoke(mode, field):
    unavailable = {field: False}
    if sys.platform != 'win32':
        return unavailable
    # These are the installer's protected locations, independent of school data,
    # configuration files, current directory and all browser-supplied values.
    program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
    windows = os.environ.get('SystemRoot', r'C:\Windows')
    if not ntpath.isabs(program_files) or not ntpath.isabs(windows):
        return unavailable
    helper = ntpath.join(program_files, 'ElevhubServer', 'server', 'server-restart.ps1')
    powershell = ntpath.join(windows, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    try:
        if not Path(helper).is_file() or not Path(powershell).is_file():
            return unavailable
        result = subprocess.run(
            [powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', helper, '-Mode', mode],
            capture_output=True, text=True, encoding='utf-8-sig', timeout=8,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0 or len(result.stdout) > 8192:
            return unavailable
        payload = json.loads(result.stdout)
        return {field: isinstance(payload, dict) and payload.get(field) is True}
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        # Never expose command output, usernames or private installation paths.
        return unavailable


def status():
    return _invoke('Status', 'available')


def request():
    return _invoke('Request', 'accepted')
