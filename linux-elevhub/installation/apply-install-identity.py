#!/usr/bin/env python3
"""Validate the copied profile and persist its hostname in the installed OS."""
import importlib.util
import json
import os
from pathlib import Path
import re


def apply_identity(root=Path('/'), firstboot_path=Path('/usr/local/lib/elevhub-firstboot.py')):
    spec = importlib.util.spec_from_file_location('elevhub_firstboot', firstboot_path)
    firstboot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(firstboot)
    config = root / 'etc/elevhub-install'
    settings = json.loads((config / 'settings.json').read_text())
    device = json.loads((config / 'device.json').read_text())
    firstboot.validate_profile(settings, device, (config / 'ca.crt').read_text(),
                               (config / 'admin.pub').read_text())
    name = settings['computer']
    hosts_path = root / 'etc/hosts'
    hosts = hosts_path.read_text()
    local = '127.0.1.1\t' + name
    if re.search(r'^127\.0\.1\.1\s.*$', hosts, re.MULTILINE):
        hosts = re.sub(r'^127\.0\.1\.1\s.*$', local, hosts, flags=re.MULTILINE)
    else:
        hosts = hosts.rstrip() + '\n' + local + '\n'
    for path, content in ((root / 'etc/hostname', name + '\n'), (hosts_path, hosts)):
        pending = path.with_name(path.name + '.elevhub-pending')
        pending.write_text(content)
        pending.chmod(0o644)
        os.replace(pending, path)


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Installationsidentitet kræver root.')
    apply_identity()
