"""Opret en ny HTTPS-konfiguration fra eksisterende apps og provisionerede filer."""
import argparse
import ipaddress
import json
import os
from pathlib import Path

from elevhub import MAPPE, laes_konfiguration
from makerspace_printers import PrinterAdministration
from sikkerhed import Enhedsregister, lokal_backend, tls_kontekst


def opret(basis, sikkerhed, output, elev_adresse='127.0.0.1', laerer_adresse='127.0.0.1',
          scratch_mappe=None, datatraener_mappe=None, printer_port=None,
          printer_data_dir=None):
    if (printer_port is None) != (printer_data_dir is None):
        raise ValueError('Printerport og printerdatamappe skal angives sammen.')
    if printer_port is not None and (type(printer_port) is not int or not 1 <= printer_port <= 65535):
        raise ValueError('Printerporten skal være et helt tal mellem 1 og 65535.')
    ipaddress.ip_address(elev_adresse)
    ipaddress.ip_address(laerer_adresse)
    konf = laes_konfiguration(basis)
    for id_, mappe in (('scratch', scratch_mappe), ('datatraener', datatraener_mappe)):
        if mappe is None:
            continue
        lokal = Path(mappe).resolve(strict=True)
        if not (lokal / 'index.html').is_file():
            raise ValueError(f'{id_}-mappen skal indeholde index.html fra det lokale build.')
        app = next((a for a in konf['apps'] if a['id'] == id_ and a.get('type') == 'lokal'), None)
        if app is None:
            raise ValueError(f'Basisopsætningen mangler et lokalt {id_}-værksted.')
        app['mappe'] = str(lokal)
    for app in konf['apps']:
        if app.get('type') == 'lokal' and app.get('mappe'):
            app['mappe'] = str((Path(basis).resolve().parent / app['mappe']).resolve())
    if printer_port is not None:
        try:
            printer_data_dir = Path(printer_data_dir).expanduser().resolve(strict=False)
        except (TypeError, ValueError, OSError) as error:
            raise ValueError('Printerdatamappen skal være en gyldig sti.') from error
        printer_config = {
            'backend': f'127.0.0.1:{printer_port}',
            'key_file': str(printer_data_dir / 'makerspace-bridge.key'),
        }
        # Validate the generated reference without opening, creating, or copying
        # the bridge key.  It is created by app.server at runtime.
        PrinterAdministration(printer_config, Path(basis).resolve())
        konf['makerspace_printers'] = printer_config
    sikkerhed = Path(sikkerhed).resolve(strict=True)
    konf.pop('usikker_test', None)
    konf.update(elev_adresse=elev_adresse, laerer_adresse=laerer_adresse,
                tls={'certifikat': str(sikkerhed / 'server.crt'), 'noegle': str(sikkerhed / 'server.key')},
                enhedsregister=str(sikkerhed / 'enheder.json'))
    tls_kontekst(konf, '.')
    Enhedsregister(konf['enhedsregister'])
    if any(not lokal_backend(a['bagved']) for a in konf['apps'] if a.get('type', 'vaerksted') == 'vaerksted'):
        raise ValueError('Værkstedernes backend skal være på loopback på denne server.')
    # Konfigurationen indeholder stier, ingen private nøgler eller tokens.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(konf, f, ensure_ascii=False, indent=2)
        f.write('\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--basis', type=Path, default=MAPPE / 'konfiguration.json')
    p.add_argument('--sikkerhed', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--elev-adresse', default='127.0.0.1')
    p.add_argument('--laerer-adresse', default='127.0.0.1')
    p.add_argument('--scratch-mappe', type=Path, help='Lokalt Scratch-build med index.html')
    p.add_argument('--datatraener-mappe', type=Path, help='Lokalt Datatræner-build med index.html')
    p.add_argument('--printer-port', type=int, help='Printværkstedets lokale port')
    p.add_argument('--printer-data-dir', type=Path, help='Printværkstedets konkrete datamappe')
    a = p.parse_args()
    try:
        opret(a.basis, a.sikkerhed, a.output, a.elev_adresse, a.laerer_adresse,
              a.scratch_mappe, a.datatraener_mappe, a.printer_port,
              a.printer_data_dir)
    except (OSError, ValueError) as fejl:
        p.exit(2, f'Konfigurationen blev ikke oprettet: {fejl}\n')
    print(f'Ny HTTPS-konfiguration: {a.output}. Ingen tjenester er startet.')


if __name__ == '__main__':
    main()
