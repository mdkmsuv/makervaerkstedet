"""Bounded child work for the Elevhub server benchmark.

The worker never contacts printers or POSTs production queue routes. It writes
one JSON result to the requested benchmark directory and keeps exceptions out
of the result payload.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time


def _cube(path: Path):
    """Create a tiny benchmark-only 20 mm cube STL."""
    triangles = ((0,0,0),(20,0,0),(20,20,0),(0,20,0),(0,0,20),(20,0,20),(20,20,20),(0,20,20))
    faces = ((0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,4,5),(0,5,1),(1,5,6),(1,6,2),(2,6,7),(2,7,3),(3,7,4),(3,4,0))
    with path.open('w', encoding='ascii') as handle:
        handle.write('solid benchmark-cube\n')
        for a,b,c in faces:
            handle.write(' facet normal 0 0 0\n  outer loop\n')
            for point in (triangles[a], triangles[b], triangles[c]): handle.write('   vertex %s %s %s\n' % point)
            handle.write('  endloop\n endfacet\n')
        handle.write('endsolid benchmark-cube\n')


def _slice(install_root: Path, output: Path) -> dict:
    executable = install_root / 'tools' / 'bambu' / 'bambu-studio.exe'
    profiles_root = executable.parent / 'resources' / 'profiles' / 'BBL'
    if not executable.is_file() or not profiles_root.is_dir():
        raise ValueError('Bambu Studio eller printprofiler mangler i serverpakken.')
    import sys
    if str(install_root) not in sys.path:
        sys.path.insert(0, str(install_root))
    from app.slicer import Slicer
    from app.profiles import Profiles
    work = output.parent / 'slicer-work'
    work.mkdir(parents=True, exist_ok=True)
    cube = work / 'cube-20mm.stl'; _cube(cube)
    fixture = next((p for p in (install_root / 'tests').rglob('*.stl')), cube)
    profiles = Profiles(profiles_root)
    filament = profiles.find_filament('A1 mini', 'GFA00')
    if not filament:
        raise ValueError('GFA00 filamentprofilen mangler')
    slicer = Slicer(executable, profiles_root, work)
    config = {'model': 'A1 mini'}
    job = {'id': 'benchmark-cube', 'quality': 'standard', 'profile': filament,
           'actual_colour': '#B4121A', 'remote_name': 'benchmark-cube.gcode.3mf'}
    first = slicer.slice(job, config, cube)
    details = {'available': True, 'cube': str(first.name), 'filament': filament}
    if fixture != cube:
        job['id'] = 'benchmark-existing'; job['remote_name'] = 'benchmark-existing.gcode.3mf'
        details['existing'] = str(slicer.slice(job, config, fixture).name)
    return details


def run(kind: str, output: Path, install_root: Path) -> dict:
    started = time.perf_counter()
    result = {'kind': kind, 'ok': False, 'seconds': None, 'details': {}}
    try:
        if kind == 'slicer':
            result['details'] = _slice(install_root, output)
            result['ok'] = True
        else:
            raise ValueError('unknown benchmark work')
    except Exception as error:  # result files never contain credentials
        result['details'] = {'error_type': type(error).__name__, 'message': str(error)[:240]}
    result['seconds'] = round(time.perf_counter() - started, 4)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=('slicer',), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--install-root', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.kind, args.output, args.install_root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
