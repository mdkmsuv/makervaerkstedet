#!/usr/bin/env python3
"""Local first-boot guide for the public ISO. Never runs from the pupil session."""
from __future__ import annotations

import getpass
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import time

CONFIG = Path('/etc/elevhub-install')
NAME = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?')
OWN_MOUNTS = set()


def load_connection(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Skolefilen er for stor.')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('schemaVersion') != 1 or data.get('kind') != 'elevhub-school-connection':
        raise ValueError('Vælg school-connection.json fra skolens styreenhed.')
    address = ipaddress.IPv4Address(data['server'])
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError('Skolefilen har en ugyldig serveradresse.')
    if data.get('enrollment_port') != 8774 or data.get('theme') not in ('red', 'yellow', 'green'):
        raise ValueError('Skolefilen har en ukendt port eller et ukendt tema.')
    title = data.get('school_name', '')
    if not isinstance(title, str) or not 1 <= len(title) <= 80 or any(ord(c) < 32 for c in title):
        raise ValueError('Skolefilen har et ugyldigt skolenavn.')
    if not isinstance(data.get('admin_public_key'), str) or not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]{68}', data['admin_public_key']):
        raise ValueError('Skolefilen mangler en offentlig administrationsnøgle.')
    ca = data.get('caCertificatePem', '')
    if not isinstance(ca, str) or 'PRIVATE KEY' in ca:
        raise ValueError('Skolefilen må kun indeholde offentlige nøgler.')
    ssl.create_default_context(cadata=ca)
    return data


def enroll(connection, computer, code, installation_id, host_key):
    if not NAME.fullmatch(computer):
        raise ValueError('Brug 1–32 små bogstaver, tal eller bindestreger til navnet.')
    context = ssl.create_default_context(cadata=connection['caCertificatePem'])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    request = json.dumps({'computer': computer, 'code': code,
                          'installation_id': installation_id, 'ssh_host_key': host_key})
    transport = http.client.HTTPSConnection(connection['server'], 8774, timeout=20, context=context)
    try:
        transport.request('POST', '/enroll', body=request, headers={'Content-Type': 'application/json'})
        response = transport.getresponse()
        payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError('Serverens svar er for stort.')
        if response.status != 200:
            raise ValueError('Tilmelding afvist. Kontrollér computernavn, kode og elevnetværk. Koder gælder i 30 minutter.')
        data = json.loads(payload)
    finally:
        transport.close()
    if (data.get('schemaVersion') != 1 or data['settings']['computer'] != computer
            or data['settings']['server'] != connection['server']
            or data['settings'].get('theme') != connection['theme']
            or data['settings'].get('school_name') != connection['school_name']
            or data['caCertificatePem'].strip() != connection['caCertificatePem'].strip()
            or data['admin_public_key'] != connection['admin_public_key']
            or data['settings'].get('admin_sources') != [connection['server']]):
        raise ValueError('Serverens tilmelding passer ikke til skolefilen.')
    return data


def save_profile(data, config=CONFIG, firstboot_path=Path('/usr/local/lib/elevhub-firstboot.py')):
    spec = importlib.util.spec_from_file_location('elevhub_firstboot', firstboot_path)
    firstboot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(firstboot)
    firstboot.validate_profile(data['settings'], data['device'], data['caCertificatePem'], data['admin_public_key'])
    config.mkdir(mode=0o700, parents=True, exist_ok=True)
    config.chmod(0o700)
    files = {'device.json': json.dumps(data['device']), 'ca.crt': data['caCertificatePem'],
             'admin.pub': data['admin_public_key'] + '\n', 'settings.json': json.dumps(data['settings'], ensure_ascii=False)}
    # settings.json is the commit marker and always written last.
    for filename, text in files.items():
        pending = config / (filename + '.pending')
        with pending.open('w', encoding='utf-8') as stream:
            os.chmod(pending, 0o600)
            stream.write(text + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, config / filename)


def discover_connections():
    """Mount removable USB partitions read-only, without running their contents."""
    result = subprocess.run(['lsblk', '-J', '-o', 'PATH,TYPE,TRAN,RM,FSTYPE,MOUNTPOINT'],
                            check=True, capture_output=True, text=True)
    roots = [Path('/media'), Path('/mnt'), Path('/run/media')]
    index = 0

    def visit(item, usb=False):
        nonlocal index
        usb = usb or item.get('tran') == 'usb' or bool(item.get('rm'))
        if usb and item.get('type') == 'part' and item.get('fstype') in ('vfat', 'exfat', 'ntfs', 'ext4'):
            if item.get('mountpoint'):
                roots.append(Path(item['mountpoint']))
            else:
                target = Path('/run/elevhub-setup-usb') / str(index)
                index += 1
                target.mkdir(parents=True, exist_ok=True)
                mount = subprocess.run(['mount', '-o', 'ro,nosuid,nodev,noexec', item['path'], str(target)],
                                       capture_output=True)
                if mount.returncode == 0:
                    roots.append(target)
                    OWN_MOUNTS.add(target)
        for child in item.get('children', []):
            visit(child, usb)

    for item in json.loads(result.stdout)['blockdevices']:
        visit(item)
    found = set()
    for root in roots:
        for pattern in ('school-connection.json', '*/school-connection.json', '*/*/school-connection.json'):
            found.update(path for path in root.glob(pattern)
                         if path.is_file() and not path.is_symlink()
                         and path.resolve().is_relative_to(root.resolve()))
    return sorted(found)


def release_mounts():
    for target in list(OWN_MOUNTS):
        result = subprocess.run(['umount', str(target)], capture_output=True)
        if result.returncode == 0:
            OWN_MOUNTS.remove(target)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Første opsætning kræver systemtjenesten.')
    if (CONFIG / 'settings.json').exists():
        return
    CONFIG.mkdir(mode=0o700, parents=True, exist_ok=True)
    identifier = CONFIG / 'installation-id'
    if not identifier.exists():
        identifier.write_text(secrets.token_hex(32), encoding='ascii')
        identifier.chmod(0o600)
    installation_id = identifier.read_text(encoding='ascii').strip()
    if not re.fullmatch('[0-9a-f]{64}', installation_id):
        raise ValueError('Installationens lokale identitet er beskadiget.')
    subprocess.run(['ssh-keygen', '-A'], check=True)
    host_key = ' '.join(Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()[:2])
    while True:
        print('\nVELKOMMEN TIL ELEVVÆRKSTEDET\n', flush=True)
        print('1. Skole og netværk\nSæt USB-nøglen med skolens school-connection.json i computeren.\n'
              'Filen oprettes af Windows-opsætningen. Netværket blev valgt under Debian-installationen.\n'
              'Brug netværkskabel, hvis skolens trådløse netværk ikke er tilgængeligt.')
        try:
            found = discover_connections()
            for index, path in enumerate(found, 1):
                print(f'  {index}. {path}')
            choice = input('Vælg filnummer eller skriv filens fulde sti (Enter søger igen): ').strip()
            if not choice:
                continue
            path = found[int(choice) - 1] if choice.isdecimal() and 1 <= int(choice) <= len(found) else Path(choice)
            connection = load_connection(path)
            release_mounts()
            print(f"\nSkole: {connection['school_name']}\nStyreenhed: {connection['server']}\n"
                  f"Tema: {dict(red='Rød', yellow='Gul', green='Grøn')[connection['theme']]}")
            print('Brug kun skolefilen, som læreren har givet dig.')
            print('\n2. Computerens navn og tilmelding\nOpret navnet og en engangskode i styreenhedens administrationsmenu.')
            computer = input('Computerens navn: ').strip().lower()
            code = getpass.getpass('Engangskode (vises ikke): ').strip()
            if input(f'Tilmeld {computer} til {connection["school_name"]}? Skriv JA: ').strip().upper() != 'JA':
                continue
            data = enroll(connection, computer, code, installation_id, host_key)
            save_profile(data)
            print('\n3. Installerer værksteder\nCaDoodle og værkstederne gøres nu klar. Det kan tage lidt tid.\n'
                  'Elevfiler gemmes på denne computer. Køer gemmes på styreenheden.', flush=True)
            return
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, http.client.HTTPException) as error:
            print('\nOpsætningen er ikke gennemført: ' + str(error), flush=True)
            input('Tryk Enter for at prøve igen. Ingen elevfiler er ændret.')
        finally:
            release_mounts()


if __name__ == '__main__':
    main()
