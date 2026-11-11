"""Read-only HEXA identity check and optional local Laserstation configuration.

Example (on the maker computer, under the SAME Windows user as Beam Studio):
  python scripts/probe_laser_direct.py --config station.json --model fhexa1 \
    --uuid <HEXA-UUID> \
    --flux-api "C:\\Program Files\\Beam Studio\\resources\\backend\\flux_api\\flux_api.exe"

Only authentication, deviceinfo and play report are issued. No file is sent.
The Beam Studio key is read in memory from its existing Electron Store and is
never written to this config or printed. --enable must be specified explicitly
after a successful read-only probe; that still does not validate upload.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.laser_machines import get_machine
from app.laser_transfer import FluxTransfer, TransferError


def load_config(path):
    if not path.exists():
        return {}
    if path.stat().st_size > 64 * 1024:
        raise ValueError('Stationens konfigurationsfil er for stor.')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Stationens konfiguration skal være et JSON-objekt.')
    return value


def candidate_config(existing, *, model, device_uuid, flux_api, beam_store=None, enable=False):
    get_machine(model)
    if not isinstance(device_uuid, str) or not re.fullmatch(r'[a-f0-9]{32}', device_uuid):
        raise ValueError('Angiv præcis UUID med 32 små hexadecimale tegn.')
    if existing.get('machine', model) != model:
        raise ValueError('Stationens maskinmodel er en anden; kontrollér konfigurationen manuelt.')
    if not Path(flux_api).is_file():
        raise ValueError('FLUX-programmet blev ikke fundet.')
    direct = {'enabled': bool(enable), 'commissioned': bool(enable), 'managed_ghost': True,
              'flux_api': str(Path(flux_api).resolve()), 'client_key_source': 'beam_studio',
              'device_uuid': device_uuid, 'model': model}
    if beam_store is not None:
        direct['beam_studio_store'] = str(Path(beam_store).resolve())
    return dict(existing, machine=model, direct_transfer=direct)


def save_config(path, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + '.bak-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path, help='Stationens lokale JSON-konfiguration')
    parser.add_argument('--model', required=True, choices=('fhexa1', 'fbb2'))
    parser.add_argument('--uuid', required=True, help='Forventet UUID fra den parrede maskine')
    parser.add_argument('--flux-api', required=True, type=Path, help='Beam Studios installerede flux_api.exe')
    parser.add_argument('--beam-store', type=Path, help='Valgfri placering af Beam Studios eksisterende config.json')
    parser.add_argument('--enable', action='store_true', help='Aktivér først efter vellykket identitetskontrol')
    args = parser.parse_args(argv)
    try:
        existing = load_config(args.config)
        candidate = candidate_config(existing, model=args.model, device_uuid=args.uuid,
                                     flux_api=args.flux_api, beam_store=args.beam_store, enable=args.enable)
        result = FluxTransfer(candidate['direct_transfer']).probe()
        if result.get('model') != args.model or result.get('uuid') != args.uuid:
            raise TransferError('Laseren matcher ikke den angivne model og UUID; konfigurationen blev ikke ændret.')
        if not result['idle']:
            raise TransferError('Laseren er ikke i tomgang; konfigurationen blev ikke ændret.')
        save_config(args.config, candidate)
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f'Kontrol mislykkedes: {error}\n')
    print(f'Læsekontrol lykkedes: {result["model"]}, UUID {result["uuid"]}, tomgang. '
          f'Konfiguration gemt; direkte overførsel er {"aktiveret" if args.enable else "fortsat slået fra"}. '
          'Filoverførsel og fysisk start er ikke testet.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
