"""Bounded STL inspection. Geometry stays in the orientation chosen by the pupil."""
import math
import re
import struct

MAX_BYTES = 25 * 1024 * 1024
MAX_TRIANGLES = 500_000
_VERTEX_RE = re.compile(r"\bvertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", re.I)
_ENDFACET_RE = re.compile(r"\bendfacet\b", re.I)
_ENDSOLID_RE = re.compile(r"endsolid", re.I)


def _ascii_points(source):
    for match in _VERTEX_RE.finditer(source):
        try:
            yield tuple(map(float, match.groups()))
        except ValueError:
            raise ValueError("STL-filen indeholder ugyldige koordinater.") from None


def inspect_stl(data: bytes) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Vælg en STL-fil på højst 25 MB.")
    triangles = struct.unpack_from("<I", data, 80)[0] if len(data) >= 84 else 0
    if triangles and 84 + triangles * 50 == len(data):
        if triangles > MAX_TRIANGLES:
            raise ValueError("Modellen er for detaljeret. Eksportér en enklere STL-fil.")
        def vertices():
            for offset in range(84, len(data), 50):
                values = struct.unpack_from("<12fH", data, offset)
                yield from (values[3:6], values[6:9], values[9:12])
        points = vertices()
    else:
        try:
            source = data.decode("ascii")
        except UnicodeDecodeError:
            raise ValueError("Filen kunne ikke læses som STL. Eksportér modellen som STL igen.") from None
        if source.lstrip()[:5].lower() != "solid" or _ENDSOLID_RE.search(source) is None:
            raise ValueError("Filen er ikke en gyldig STL-fil.")
        points = _ascii_points(source)
        triangles = 0
        vertex_count = 0
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for point in points:
            vertex_count += 1
            if vertex_count > MAX_TRIANGLES * 3:
                raise ValueError("STL-filen har ugyldige eller for mange trekanter.")
            if any(not math.isfinite(v) or abs(v) > 1_000_000 for v in point):
                raise ValueError("Modellen indeholder ugyldige koordinater.")
            for i, value in enumerate(point):
                lo[i] = min(lo[i], value)
                hi[i] = max(hi[i], value)
        if vertex_count < 12 or vertex_count % 3:
            raise ValueError("STL-filen har ugyldige eller for mange trekanter.")
        triangles = vertex_count // 3
        if sum(1 for _ in _ENDFACET_RE.finditer(source)) != triangles:
            raise ValueError("STL-filen er ufuldstændig.")
        size = [hi[i] - lo[i] for i in range(3)]
        if triangles < 4 or min(size) < 0.01:
            raise ValueError("Modellen skal være en rumlig figur med tykkelse.")
        if max(size) > 350:
            raise ValueError("Modellen er for stor til skolens printere. Gør den mindre i dit tegneprogram.")
        return {"size": [round(v, 2) for v in size], "triangles": triangles, "bytes": len(data)}
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for point in points:
        if any(not math.isfinite(v) or abs(v) > 1_000_000 for v in point):
            raise ValueError("Modellen indeholder ugyldige koordinater.")
        for i, value in enumerate(point):
            lo[i] = min(lo[i], value)
            hi[i] = max(hi[i], value)
    size = [hi[i] - lo[i] for i in range(3)]
    if triangles < 4 or min(size) < 0.01:
        raise ValueError("Modellen skal være en rumlig figur med tykkelse.")
    if max(size) > 350:
        raise ValueError("Modellen er for stor til skolens printere. Gør den mindre i dit tegneprogram.")
    return {"size": [round(v, 2) for v in size], "triangles": triangles, "bytes": len(data)}
