#!/usr/bin/env python3
"""Idempotent installation of the workshop entry into an existing test kiosk.

Run as root after installing the official Debian package. Pass the directory
containing elevhub-cadoodle and cadoodle_vaerksted.py as the only argument.
Does not replace the VM's network policy or restart any server.
"""
import os
from pathlib import Path
import pwd
import shutil
import sys


def hook(text):
    if 'self.cadoodle = tilfoej' in text:
        return text
    changes = [
        ("        self.tekst.pack(side='left')", "        self.tekst.pack(side='left')\n\n        from cadoodle_vaerksted import tilfoej\n        self.cadoodle = tilfoej(self, ramme)"),
        ('    def til_start(self):\n', '    def til_start(self):\n        if self.cadoodle:\n            self.cadoodle.skjul()\n'),
    ]
    for before, after in changes:
        if text.count(before) != 1:
            raise ValueError('Ukendt elevbjælke; ingen filer er ændret.')
        text = text.replace(before, after, 1)
    marker = "            self.rod.after(0, self._sendt_hjem)"
    if text.count(marker) != 1:
        raise ValueError('Ukendt statuskontrol; ingen filer er ændret.')
    text = text.replace(marker, marker + '\n        if self.cadoodle:\n            self.rod.after(0, lambda: self.cadoodle.opdater(svar))', 1)
    compile(text, 'elevbjaelke.py', 'exec')
    return text


def prepare_project_paths(home, uid, gid):
    """Create the project destination before the first GUI launch, preserving data."""
    projects = home / 'Projekter/3D-print'
    target = home / 'Documents/CaDoodle-workspace/MyCaDoodleProjects'
    link = projects / 'CaDoodle'

    def directory(path):
        missing = []
        current = path
        while not current.exists():
            missing.append(current)
            current = current.parent
        path.mkdir(parents=True, exist_ok=True)
        for created in missing:
            os.chown(created, uid, gid)

    directory(projects / 'STL')
    if link.is_symlink() and link.resolve() == target.resolve():
        link.unlink()
    if target.is_dir() and not target.is_symlink() and not link.exists():
        target.rename(link)
    elif not target.exists() and not target.is_symlink() and not link.exists():
        directory(link)
    # Do not merge or replace two existing project directories.
    if link.is_dir() and not target.exists() and not target.is_symlink():
        directory(target.parent)
        target.symlink_to(link, target_is_directory=True)
        os.lchown(target, uid, gid)


def main():
    src = Path(sys.argv[1]).resolve()
    bar = Path('/usr/local/lib/elevhub/elevbjaelke.py')
    original = bar.read_text()
    patched = hook(original)
    backup = bar.with_name('elevbjaelke.foer-cadoodle.py')
    if not backup.exists():
        backup.write_text(original)
    # Ansible restarts the pupil's kiosk only when this reports a change, so
    # an unchanged machine must not report one.
    changed = patched != original
    for name, dest, mode in [
        ('elevhub-cadoodle', Path('/usr/local/bin/elevhub-cadoodle'), 0o755),
        ('cadoodle_vaerksted.py', bar.with_name('cadoodle_vaerksted.py'), 0o644)]:
        data = (src / name).read_bytes()
        if not dest.exists() or dest.read_bytes() != data or (dest.stat().st_mode & 0o7777) != mode:
            shutil.copyfile(src / name, dest)
            dest.chmod(mode)
            changed = True
    if patched != original:
        bar.write_text(patched)
    account = pwd.getpwnam('elev')
    home = Path(account.pw_dir)
    prepare_project_paths(home, account.pw_uid, account.pw_gid)
    if changed:
        print('CaDoodle-værksted installeret. Genstart kun elevbjælken for at aktivere knappen.')
    else:
        print('CaDoodle-værkstedet var allerede klar.')


if __name__ == '__main__':
    main()
