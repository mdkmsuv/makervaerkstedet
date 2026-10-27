#!/usr/bin/env python3
"""Build a student payload and a personal or shared Debian installation USB.

The payload contains software only. Device credentials are supplied separately
from the existing school provisioning and never include a server/private SSH key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from firstboot import validate_profile, verify_bundle

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEB_HASH = '6ee1ed049127a25a07331e8a9f61035e98130310cb7991f48bf9fcfb0880520a'
CADoodle17_JAR_HASH = 'bc0f2121fc0da194c08be044f4b5d9f0c673b0522e70124987e510611dc7a983'
IGNORED = {'.git', '__pycache__', 'node_modules', '.DS_Store'}


def sha(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def add_path(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        raise ValueError('Pakken må ikke indeholde symbolske links: ' + str(source))
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            if child.name not in IGNORED and child.suffix not in {'.pyc', '.pyo'}:
                add_path(child, destination / child.name)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix in {'.sh', '.py', '.yml', '.j2', '.service', '.cfg', '.templates'} or source.name == 'elevhub-cadoodle':
            destination.write_bytes(source.read_bytes().replace(b'\r\n', b'\n'))
        else:
            shutil.copyfile(source, destination)
    else:
        raise FileNotFoundError(source)


def design_paths():
    # Read the existing explicit role allowlist instead of copying the project
    # root, which also contains school credentials and unrelated applications.
    tasks = (ROOT / 'linux-elevhub/ansible/roles/kiosk/tasks/main.yml').read_text(encoding='utf-8')
    section = tasks.split('    kiosk_lokal_design_filer:\n', 1)[1].split('  block:\n', 1)[0]
    paths = re.findall(r'^      - ([a-zA-Z0-9_./-]+)$', section, re.MULTILINE)
    if not paths or any(Path(path).is_absolute() or '..' in Path(path).parts for path in paths):
        raise ValueError('Designværkstedets filoversigt kunne ikke læses sikkert.')
    return paths


def bundle(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError('Outputmappen findes allerede. Vælg en ny versionsmappe.')
    required = [args.deb, args.bootstrap, args.kloster, args.scratch / 'index.html', args.datatraener / 'index.html']
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    if sha(args.deb) != DEB_HASH:
        raise ValueError('Den officielle CaDoodle-pakkes kontrolsum passer ikke.')
    if sha(args.kloster) != CADoodle17_JAR_HASH:
        raise ValueError('CaDoodle-Kloster.jar er ikke den verificerede project-finish-20261009-udgave.')
    # Stage beside the output on disk; /tmp can be a small RAM disk in WSL.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='elevhub-bundle-', dir=output.parent) as temp:
        stage = Path(temp) / 'payload'
        stage.mkdir()
        paths = [
            'CREDITS.md',
            'linux-elevhub/ansible/roles', 'linux-elevhub/ansible/site.yml',
            'linux-elevhub/ansible/ansible.cfg', 'linux-elevhub/ansible/requirements.yml',
            'linux-elevhub/projekter', 'linux-elevhub/server/kredsloeb',
            'linux-elevhub/server/microbit-controller',
            'linux-elevhub/server/elevhub/release.json',
            'linux-elevhub/server/elevhub/web/assets',
            'linux-elevhub/cadoodle/elevhub-cadoodle', 'linux-elevhub/cadoodle/klargoer.py',
            'linux-elevhub/cadoodle/fresh_session.py',
            'linux-elevhub/cadoodle/bootstrap.py', 'linux-elevhub/cadoodle/kloster/deploy.py',
            'linux-elevhub/kiosk-window-guard/bin/matchbox-window-manager',
            'linux-elevhub/kiosk-window-guard/matchbox-close-guard.patch',
            'linux-elevhub/kiosk-window-guard/README.md',
            'linux-elevhub/kiosk-window-guard/COPYING',
            'linux-elevhub/kiosk-window-guard/debian-copyright',
            'linux-elevhub/kiosk-window-guard/build.sh',
            'linux-elevhub/kiosk-window-guard/matchbox-source/matchbox-window-manager_1.2.2+git20200512.orig.tar.xz',
            'linux-elevhub/kiosk-window-guard/matchbox-source/matchbox-window-manager_1.2.2+git20200512-2.debian.tar.xz',
            'linux-elevhub/kiosk-window-guard/matchbox-source/matchbox-window-manager_1.2.2+git20200512-2.dsc',
            'linux-elevhub/LICENSE', 'linux-elevhub/THIRD_PARTY.md',
        ] + design_paths()
        for relative in paths:
            add_path(ROOT / relative, stage / relative)
        for source, name in [(args.deb, 'CaDoodle.deb'), (args.bootstrap, 'CaDoodle-ApplicationInstall.zip'),
                             (args.kloster, 'CaDoodle-Kloster.jar'), (args.scratch, 'scratch'),
                             (args.datatraener, 'datatraener')]:
            print('Pakker ' + name, flush=True)
            add_path(source, stage / 'assets' / name)
        manifest = {'version': args.version, 'files': {}}
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                manifest['files'][path.relative_to(stage).as_posix()] = sha(path)
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        package = Path(temp) / 'payload.tar.gz'
        def normalize(info):
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            info.mode = 0o755 if info.isdir() else 0o644
            info.mtime = 0
            return info
        print('Komprimerer den samlede softwarepakke', flush=True)
        with tarfile.open(package, 'w:gz', compresslevel=1) as archive:
            for path in sorted(stage.iterdir()):
                archive.add(path, arcname=path.name, filter=normalize)
        output.mkdir()
        shutil.move(package, output / 'payload.tar.gz')
        shutil.copyfile(stage / 'manifest.json', output / 'manifest.json')
        (output / 'payload.sha256').write_text(sha(output / 'payload.tar.gz') + '  payload.tar.gz\n')
    print('Softwarepakken er klar: ' + str(output), flush=True)


def wifi_preseed(wifi):
    """Optional private installer-only Wi-Fi settings; never log their values."""
    if wifi is None:
        return ''
    if not isinstance(wifi, dict):
        raise ValueError('Wi-Fi-profilen skal indeholde ssid og password.')
    ssid, password = wifi.get('ssid'), wifi.get('password')
    # Reject line/control injection and ambiguous leading/trailing whitespace.
    # Backslashes are interpreted by cdebconf's line protocol.
    for value in (ssid, password):
        if (not isinstance(value, str) or not value or value != value.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or '\\' in value):
            raise ValueError('Wi-Fi-felter skal være enkeltlinjer uden kontroltegn eller kantmellemrum.')
    if len(ssid.encode('utf-8')) > 32:
        raise ValueError('Wi-Fi-netværksnavnet må højst fylde 32 byte.')
    if not ((8 <= len(password) <= 63 and password.isascii())
            or re.fullmatch('[0-9a-fA-F]{64}', password)):
        raise ValueError('WPA-koden skal have 8–63 ASCII-tegn eller være en 64-cifret hex-nøgle.')
    return ('\n### Privat Wi-Fi til denne installation\n'
            f'd-i netcfg/wireless_essid string {ssid}\n'
            'd-i netcfg/wireless_security_type select wpa\n'
            f'd-i netcfg/wireless_wpa string {password}\n')


def prepare_preseed(template, computer, key, payload_hash, wifi=None, reusable=False):
    text = template.replace('@@NAVN@@', computer).replace('@@NOEGLE@@', key.strip())
    disk_marker = 'd-i partman-auto/disk string /dev/sda'
    if text.count(disk_marker) != 1 or 'd-i partman/early_command' in text:
        raise ValueError('Ukendt diskopsætning i USB-svarfilen.')
    # Selection is performed by the first init.d hook embedded in initrd.
    # Unlike preseed_command, partman's init loop propagates a hook failure
    # to main-menu before starting parted or any partition commit.
    text = text.replace(disk_marker,
                        'd-i partman-auto/disk string /dev/elevhub-unselected')
    text = text.replace('### Disk: hele den første disk slettes og bruges til Debian (UEFI, én partition + swap)',
                        '### Disk: kun én entydig intern disk må vælges; USB-mediet udelukkes.')
    text = text.replace('### OBS: kontrollér selv, at /dev/sda er den rigtige disk på den fysiske\n'
                        '### maskine, hvis den har mere end én disk. Testmiljøets VM\'er har kun én.',
                        '### Flere interne diske stopper installationen før partitionering.')
    text = text.replace('openssh-server sudo python3 python3-apt',
                        'openssh-server sudo python3 python3-apt ansible')
    marker = '    in-target systemctl enable ssh.service'
    if text.count(marker) != 1 or not re.fullmatch('[a-f0-9]{64}', payload_hash):
        raise ValueError('Ukendt SSH-svarfil eller ugyldig kontrolsum.')
    commands = [
        "echo '" + payload_hash + "  /cdrom/elevhub/payload.tar.gz' | sha256sum -c -",
        'in-target install -d -m 700 /opt/elevhub-install /etc/elevhub-install',
        'tar -xzf /cdrom/elevhub/payload.tar.gz -C /target/opt/elevhub-install',
        ('sh /cdrom/elevhub/copy-selected-profile.sh' if reusable else
         'cp /cdrom/elevhub/profile/settings.json /cdrom/elevhub/profile/device.json /cdrom/elevhub/profile/ca.crt /cdrom/elevhub/profile/admin.pub /target/etc/elevhub-install/'),
        'chmod 600 /target/etc/elevhub-install/*',
        'cp /cdrom/elevhub/firstboot.py /target/usr/local/lib/elevhub-firstboot.py',
        'chmod 644 /target/usr/local/lib/elevhub-firstboot.py',
        'cp /cdrom/elevhub/apply-install-identity.py /target/usr/local/lib/elevhub-apply-install-identity.py',
        'in-target python3 /usr/local/lib/elevhub-apply-install-identity.py',
        'cp /cdrom/elevhub/elevhub-firstboot.service /target/etc/systemd/system/elevhub-firstboot.service',
        'chmod 644 /target/etc/systemd/system/elevhub-firstboot.service',
        'in-target systemctl enable elevhub-firstboot.service',
    ]
    text = text.replace(marker, marker + ' && \\\n    ' + ' && \\\n    '.join(commands))
    if '@@' in text:
        raise ValueError('Installationssvarfilen har uudfyldte felter.')
    # Append after template substitution so credential characters are never
    # mistaken for template markers or interpolated into a shell command.
    text += wifi_preseed(wifi)
    return text


def prepare_generic_preseed(template, computer, payload_hash):
    """Prepare a credential-free installer; onboarding supplies identity later."""
    text = template.replace('@@NAVN@@', computer)
    text = text.replace('@@NOEGLE@@', '')
    disk_marker = 'd-i partman-auto/disk string /dev/sda'
    if text.count(disk_marker) != 1 or 'd-i partman/early_command' in text:
        raise ValueError('Ukendt diskopsætning i USB-svarfilen.')
    text = text.replace(disk_marker, 'd-i partman-auto/disk string /dev/elevhub-unselected')
    text = text.replace('### Disk: hele den første disk slettes og bruges til Debian (UEFI, én partition + swap)',
                        '### Disk: kun én entydig intern disk må vælges; USB-mediet udelukkes.')
    text = text.replace('### OBS: kontrollér selv, at /dev/sda er den rigtige disk på den fysiske\n'
                        '### maskine, hvis den har mere end én disk. Testmiljøets VM\'er har kun én.',
                        '### Flere interne diske stopper installationen før partitionering.')
    text = text.replace('openssh-server sudo python3 python3-apt',
                        'openssh-server sudo python3 python3-apt ansible')
    marker = 'd-i preseed/late_command string \\'
    if text.count(marker) != 1 or not re.fullmatch('[a-f0-9]{64}', payload_hash):
        raise ValueError('Ukendt SSH-svarfil eller ugyldig kontrolsum.')
    start = text.index(marker)
    end = text.index('### Afslut:', start)
    commands = [
        'in-target passwd -l elevhub',
        "in-target install -d -m 755 /etc/ssh/sshd_config.d && for line in 'PermitRootLogin no' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PubkeyAuthentication yes' 'PermitEmptyPasswords no' 'AllowUsers elevhub' 'AllowAgentForwarding no' 'X11Forwarding no' 'PermitTunnel no'; do echo \"$line\"; done > /target/etc/ssh/sshd_config.d/10-elevhub-bootstrap.conf",
        'in-target chmod 644 /etc/ssh/sshd_config.d/10-elevhub-bootstrap.conf',
        "echo '" + payload_hash + "  /cdrom/elevhub/payload.tar.gz' | sha256sum -c -",
        'in-target install -d -m 700 /opt/elevhub-install /etc/elevhub-install',
        'tar -xzf /cdrom/elevhub/payload.tar.gz -C /target/opt/elevhub-install',
        'cp /cdrom/elevhub/onboarding.py /target/usr/local/lib/elevhub-onboarding.py',
        'chmod 755 /target/usr/local/lib/elevhub-onboarding.py',
        'cp /cdrom/elevhub/elevhub-onboarding.service /target/etc/systemd/system/elevhub-onboarding.service',
        'chmod 644 /target/etc/systemd/system/elevhub-onboarding.service',
        'cp /cdrom/elevhub/firstboot.py /target/usr/local/lib/elevhub-firstboot.py',
        'chmod 644 /target/usr/local/lib/elevhub-firstboot.py',
        'cp /cdrom/elevhub/elevhub-firstboot.service /target/etc/systemd/system/elevhub-firstboot.service',
        'chmod 644 /target/etc/systemd/system/elevhub-firstboot.service',
        'install -d -m 755 /target/etc/systemd/system/elevhub-firstboot.service.d',
        'cp /cdrom/elevhub/elevhub-firstboot-onboarding.conf /target/etc/systemd/system/elevhub-firstboot.service.d/10-onboarding.conf',
        'chmod 644 /target/etc/systemd/system/elevhub-firstboot.service.d/10-onboarding.conf',
        'in-target systemctl disable getty@tty1.service',
        'in-target systemctl enable elevhub-onboarding.service',
        'in-target systemctl enable elevhub-firstboot.service',
    ]
    late = marker + '\n    ' + ' && \\\n    '.join(commands)
    text = text[:start] + late + '\n\n' + text[end:]
    if '@@' in text:
        raise ValueError('Installationssvarfilen har uudfyldte felter.')
    return text


def installation_profiles(profile, directory):
    """Validate each independent device identity before building any medium."""
    if ('device_file' in profile) == ('device_files' in profile):
        raise ValueError('Vælg enten device_file eller device_files i profilen.')
    reusable = 'device_files' in profile
    files = profile.get('device_files') if reusable else [profile['device_file']]
    if (not isinstance(files, list) or not 1 <= len(files) <= 200
            or any(not isinstance(path, str) or not path for path in files)):
        raise ValueError('device_files skal være en liste med 1–200 enhedsfiler.')
    ca = (directory / profile['ca_file']).read_text()
    key = (directory / profile['admin_public_key']).read_text().strip()
    selected, tokens = {}, set()
    for path in files:
        device = json.loads((directory / path).read_text())
        settings = {'computer': device.get('computer'), 'server': profile['server']}
        if 'local_admin' in profile:
            settings['local_admin'] = profile['local_admin']
        validate_profile(settings, device, ca, key)
        name = settings['computer']
        if name in selected or any(device[field] in tokens for field in ('token', 'ui_token')):
            raise ValueError('Computernavne og adgangsværdier må ikke genbruges på USB-mediet.')
        tokens.update(device[field] for field in ('token', 'ui_token'))
        selected[name] = (settings, device)
    return reusable, selected, ca, key


def iso(args):
    if os.name != 'posix':
        raise ValueError('ISO-bygning køres i Linux/WSL.')
    generic = args.generic
    profile_path = args.profile.resolve() if args.profile else None
    profile = json.loads(profile_path.read_text()) if profile_path else {}
    if generic and profile_path:
        raise ValueError('--generic kan ikke kombineres med --profile.')
    wifi_preseed(profile.get('wifi'))  # Validate before copying the large payload.
    def relative(key):
        return (profile_path.parent / profile[key]).resolve()
    if generic:
        reusable, profiles, ca, key = False, {}, '', ''
        computer = 'elevhub-setup'
    else:
        reusable, profiles, ca, key = installation_profiles(profile, profile_path.parent)
        computer = 'vaerksted-installation' if reusable else next(iter(profiles))
    # With 12 personal ISOs, the file name is what the admin trusts when writing
    # a stick. It must name the identity inside, e.g. vaerksted-06-r8.iso.
    stem = args.output.name.removesuffix('.iso')
    if not generic and not reusable and stem != computer and not stem.startswith(computer + '-'):
        raise ValueError('ISO-navnet skal begynde med computerens navn fra profilen ('
                         + computer + '), så to maskiners medier ikke forveksles.')
    # Parse the certificate as well as checking the PEM marker. Generic media
    # deliberately has no CA until onboarding enrollment completes.
    if not generic:
        subprocess.run(['openssl', 'x509', '-in', str(relative('ca_file')), '-noout'], check=True)
    payload = args.bundle.resolve() / 'payload.tar.gz'
    payload_hash = sha(payload)
    recorded_hash = (args.bundle / 'payload.sha256').read_text().split()[0]
    if payload_hash != recorded_hash:
        raise ValueError('Softwarepakken er ændret siden bygningen.')
    output = args.output.resolve()
    if output.exists():
        raise ValueError('ISO-filen findes allerede; vælg et nyt navn.')
    # The ISO contains one device identity. Keep the build in a private Linux
    # directory rather than silently writing credentials to a shared NTFS tree.
    if str(output).startswith('/mnt/'):
        raise ValueError('Byg den personlige ISO i en privat Linux/WSL-mappe, ikke under /mnt/.')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.parent.stat().st_mode & 0o077:
        raise ValueError('ISO-outputmappen skal have rettigheden 700.')
    with tempfile.TemporaryDirectory(prefix='elevhub-iso-profile-', dir=output.parent) as temp:
        extra = Path(temp) / 'elevhub'
        for name, (settings, device) in profiles.items():
            config = extra / 'profiles' / name if reusable else extra / 'profile'
            config.mkdir(parents=True, mode=0o700)
            for filename, data in [('settings.json', settings), ('device.json', device)]:
                (config / filename).write_text(json.dumps(data, indent=2) + '\n')
                (config / filename).chmod(0o600)
            (config / 'ca.crt').write_text(ca)
            (config / 'admin.pub').write_text(key + '\n')
        if reusable:
            (extra / 'profiles/names.txt').write_text('\n'.join(sorted(profiles)) + '\n')
            for filename in ('select-computer.sh', 'computer-selection.templates', 'copy-selected-profile.sh'):
                add_path(HERE / filename, extra / filename)
        for name in ['firstboot.py', 'elevhub-firstboot.service', 'select-install-disk.sh',
                     'partman-disk-guard.sh', 'disk-selection.templates', 'apply-install-identity.py']:
            add_path(HERE / name, extra / name)
        if generic:
            for name in ('onboarding.py', 'elevhub-onboarding.service',
                         'elevhub-firstboot-onboarding.conf'):
                add_path(HERE / name, extra / name)
        shutil.copyfile(payload, extra / 'payload.tar.gz')
        template = (ROOT / 'linux-elevhub/testmiljoe/vm/preseed-usb.skabelon.cfg').read_text()
        preseed = Path(temp) / 'preseed.cfg'
        preseed_text = (prepare_generic_preseed(template, computer, payload_hash) if generic else
                        prepare_preseed(template, computer, key, payload_hash,
                                        profile.get('wifi'), reusable=reusable))
        preseed.write_text(preseed_text, encoding='utf-8')
        preseed.chmod(0o600)
        builder = ROOT / 'linux-elevhub/testmiljoe/vaert/byg-usb-iso.sh'
        pending = output.with_suffix('.iso.part')
        subprocess.run(['bash', str(builder), str(args.base_iso.resolve()), str(preseed),
                        str(pending), 'generic USB' if generic else ('faelles USB' if reusable else computer), str(extra)], check=True)
        pending.chmod(0o600)
        os.replace(pending, output)
    output.with_suffix('.iso.sha256').write_text(sha(output) + '  ' + output.name + '\n')
    print('Samlet elevinstallation klar: ' + str(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    pack = commands.add_parser('bundle', help='Pak alle elevprogrammer uden enhedscredentials')
    for name in ['output', 'deb', 'bootstrap', 'kloster', 'scratch', 'datatraener']:
        pack.add_argument('--' + name, type=Path, required=True)
    pack.add_argument('--version', default='2026.10.13')
    make_iso = commands.add_parser('iso', help='Byg ISO med skoleidentitet eller generic onboarding')
    for name in ['output', 'bundle', 'base-iso']:
        make_iso.add_argument('--' + name, type=Path, required=True)
    make_iso.add_argument('--profile', type=Path)
    make_iso.add_argument('--generic', action='store_true', help='Byg credential-fri ISO med lokal onboarding')
    args = parser.parse_args()
    if args.action == 'iso' and bool(args.generic) == bool(args.profile is not None):
        parser.error('iso kræver præcis én af --profile eller --generic')
    (bundle if args.action == 'bundle' else iso)(args)


if __name__ == '__main__':
    main()
