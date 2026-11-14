"""Check a model before the native importer can substitute an error placeholder."""
import math
import re
import struct


def validate_stl(data):
    if not data or len(data) > 25 * 1024 * 1024:
        raise ValueError('Vælg en STL-fil på højst 25 MB.')
    count = struct.unpack_from('<I', data, 80)[0] if len(data) >= 84 else 0
    if count and 84 + count * 50 == len(data):
        if count > 500000:
            raise ValueError('STL-filen har for mange trekanter.')
        points = (struct.unpack_from('<9f', data, offset + 12) for offset in range(84, len(data), 50))
    else:
        try:
            source = data.decode('ascii')
            vertices = re.findall(r'\bvertex\s+(\S+)\s+(\S+)\s+(\S+)', source, re.I)
            if (not source.lstrip().lower().startswith('solid') or 'endsolid' not in source.lower()
                    or not vertices or len(vertices) % 3 or len(vertices) > 1500000
                    or len(re.findall(r'\bendfacet\b', source, re.I)) != len(vertices) // 3):
                raise ValueError()
            points = (tuple(map(float, point)) for point in vertices)
        except (ValueError, UnicodeError):
            raise ValueError('STL-filen kunne ikke læses. Gem modellen igen fra dit tegneprogram.') from None
    try:
        for point in points:
            if any(not math.isfinite(v) or abs(v) > 1000000 for v in point):
                raise ValueError()
    except ValueError:
        raise ValueError('STL-filen indeholder ugyldige koordinater.') from None
