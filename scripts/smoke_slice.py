"""Exercise installed Bambu Studio without contacting a printer."""
import argparse
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.slicer import Slicer


def cube(size=20):
    vertices = [(0,0,0),(size,0,0),(size,size,0),(0,size,0),(0,0,size),(size,0,size),(size,size,size),(0,size,size)]
    faces = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    data = b'Printvaerksted test cube'.ljust(80,b'\0') + struct.pack('<I', len(faces))
    for face in faces:
        data += struct.pack('<12fH', 0,0,0, *(v for i in face for v in vertices[i]), 0)
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='A1 mini')
    parser.add_argument('--quality', default='standard')
    parser.add_argument('--extruder', type=int, choices=(0, 1), default=1, help='Test only: 0 left, 1 right on H2D')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    work = root / '.runtime' / 'slice-tests'
    work.mkdir(parents=True, exist_ok=True)
    stl = work / 'test-cube.stl'
    stl.write_bytes(cube())
    studio = Path(r'C:\Program Files\Bambu Studio\bambu-studio.exe')
    slicer = Slicer(studio, studio.parent / 'resources' / 'profiles' / 'BBL', work)
    name = slicer.profiles.find_filament(args.model, 'GFA00')
    job = {'id': args.model.replace(' ', '-').lower() + '-' + args.quality, 'quality': args.quality, 'profile': name, 'actual_colour': '#3678DC', 'remote_name': 'test.gcode.3mf'}
    result = slicer.slice(job, {'model': args.model, 'extruder': args.extruder}, stl)
    print(json.dumps({'model': args.model, 'quality': args.quality, 'output': str(result), 'bytes': result.stat().st_size}))
