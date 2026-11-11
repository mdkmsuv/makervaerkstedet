"""Chromium proxy contract shared by installation checks and update sources."""
import argparse
import json
import os
import stat
from pathlib import Path

MAX_POLICY_BYTES = 1024 * 1024


def normalized_bypass(value, server):
    if not isinstance(server, str) or not server or len(server) > 2048 or any(c in server for c in ',;\r\n\x00'):
        raise ValueError('Skoleserverens proxyundtagelse er ugyldig.')
    if isinstance(value, str):
        entries = value.replace(';', ',').split(',')
    elif isinstance(value, list):
        entries = value
    elif value is None:
        entries = []
    else:
        raise ValueError('ProxyBypassList skal være en tekststreng.')
    if len(entries) > 256:
        raise ValueError('Der er for mange proxyundtagelser.')
    result = []
    for item in [*entries, server, '127.0.0.1', 'localhost']:
        if not isinstance(item, str) or len(item) > 2048 or any(c in item for c in ',;\r\n\x00'):
            raise ValueError('En proxyundtagelse er ugyldig.')
        item = item.strip()
        if item and item.casefold() not in {entry.casefold() for entry in result}:
            result.append(item)
    return ','.join(result)


def validate_policy(policy, server):
    if not isinstance(policy, dict):
        raise ValueError('Browserpolitikken skal være et JSON-objekt.')
    settings = policy.get('ProxySettings', policy)
    if not isinstance(settings, dict) or settings.get('ProxyMode') != 'fixed_servers':
        raise ValueError('Browserpolitikken mangler den faste skoleproxy.')
    if settings.get('ProxyServer') != 'http=127.0.0.1:3128;https=127.0.0.1:3128':
        raise ValueError('Browserpolitikken bruger en anden proxy.')
    bypass = settings.get('ProxyBypassList')
    if not isinstance(bypass, str) or len(bypass) > MAX_POLICY_BYTES:
        raise ValueError('ProxyBypassList skal være en tekststreng.')
    normalized_bypass(bypass, server)
    current = {entry.strip().casefold() for entry in bypass.replace(';', ',').split(',')}
    if not {server.casefold(), '127.0.0.1', 'localhost'} <= current:
        raise ValueError('Browserpolitikken mangler undtagelse for skoleserver eller lokale værksteder.')
    return True


def read_policy(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Browserpolitikfilen må ikke bruge symbolske links.')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_POLICY_BYTES:
        raise ValueError('Browserpolitikfilen er usikker eller for stor.')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('Browserpolitikfilen blev ændret under kontrollen.')
        raw = source.read(MAX_POLICY_BYTES + 1)
    if len(raw) > MAX_POLICY_BYTES:
        raise ValueError('Browserpolitikfilen er for stor.')
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--server', required=True)
    args = parser.parse_args()
    validate_policy(read_policy(args.policy), args.server)
    print('Skoleproxy og lokale undtagelser er kontrolleret.')


if __name__ == '__main__':
    main()
