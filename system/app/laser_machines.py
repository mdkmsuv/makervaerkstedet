"""Explicit Beam Studio machine profiles. Never infer a model from a connected device.

For HEXA, the published cutting area is 730 x 410 mm and the published camera
coverage is 730 x 395 mm. Installed Beam Studio 2.5.14 uses a 740 x 410 mm
document for model ``fhexa1``; the extra document width is not offered for
placement. See FLUX's workarea-constants.ts and the HEXA specifications.
"""
from pathlib import Path


_HERE = Path(__file__).resolve().parent

MACHINES = {
    'fbb2': {
        'id': 'fbb2', 'name': 'Beambox II', 'width': 600.0, 'height': 375.0,
        'documentWidth': 600.0, 'documentHeight': 375.0,
        'cameraWidth': None, 'cameraHeight': None,
        'presets_file': _HERE / 'laser_presets_fbb2.json',
        'compilerArgs': '-fbb2 -model fbb2 -acc 8000 -acc-override {"path":{"x":1000,"y":1000}} '
                        '-vsc -vsl 3000 -min-speed 0.5 -fg',
    },
    'fhexa1': {
        'id': 'fhexa1', 'name': 'HEXA', 'width': 730.0, 'height': 410.0,
        'documentWidth': 740.0, 'documentHeight': 410.0,
        'cameraWidth': 730.0, 'cameraHeight': 395.0,
        'presets_file': _HERE / 'laser_presets_fhexa1.json',
        # Beam Studio 2.5.14 svg-laser-parser.ts getExportOpt(): hardware_name
        # hexa, model fhexa1, acc 7500, workarea vector speed 20 mm/s -> 1200
        # mm/min, min speed 0.5. Fast gradient is firmware dependent, so omitted.
        'compilerArgs': '-hexa -model fhexa1 -acc 7500 -vsc -vsl 1200 -min-speed 0.5',
    },
}


def get_machine(model='fbb2'):
    """Return a copy of a trusted profile, or refuse an unsupported model."""
    try:
        profile = MACHINES[model]
    except (KeyError, TypeError):
        raise ValueError('Denne lasermodel er ikke understøttet.') from None
    return dict(profile)
