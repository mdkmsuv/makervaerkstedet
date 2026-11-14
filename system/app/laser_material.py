"""Conservative placement inside teacher-approved camera material outlines.

Coordinates and dimensions are millimetres. This module only computes and checks
geometry; it never controls the laser or approves a camera detection.
"""

import math

from shapely.geometry import GeometryCollection, Polygon, box
from shapely.ops import unary_union


DEFAULT_BED = (600.0, 375.0)
MAX_REGIONS = 80
MAX_POINTS_PER_RING = 256
MAX_TOTAL_POINTS = 4096
MAX_ITEMS = 60
EPS = 1e-7


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} skal være et endeligt tal.')
    return float(value)


def _bed(bed):
    if not isinstance(bed, (list, tuple)) or len(bed) != 2:
        raise ValueError('Laserens område er ugyldigt.')
    width, height = (_number(v, 'Laserens størrelse') for v in bed)
    if width <= 0 or height <= 0:
        raise ValueError('Laserens område er ugyldigt.')
    return width, height


def _nonnegative(value, label):
    result = _number(value, label)
    if result < 0:
        raise ValueError(f'{label} må ikke være negativ.')
    return result


def _ring(raw, bed, label):
    if not isinstance(raw, list) or not 3 <= len(raw) <= MAX_POINTS_PER_RING + 1:
        raise ValueError(f'{label} skal have mellem 3 og {MAX_POINTS_PER_RING} punkter.')
    points = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f'{label} har et ugyldigt punkt.')
        x, y = (_number(v, 'Koordinat') for v in point)
        if x < 0 or y < 0 or x > bed[0] or y > bed[1]:
            raise ValueError(f'{label} ligger uden for laserens område.')
        points.append([x, y])
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3 or len(points) > MAX_POINTS_PER_RING or len({tuple(p) for p in points}) != len(points):
        raise ValueError(f'{label} har gentagne eller for få punkter.')
    if not Polygon(points).is_valid or Polygon(points).area <= EPS:
        raise ValueError(f'{label} har en ugyldig eller selvkrydsende kant.')
    return points


def normalize_regions(regions, bed=DEFAULT_BED):
    """Validate teacher-drawn polygons without repairing their boundaries."""
    bed = _bed(bed)
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise ValueError(f'Angiv højst {MAX_REGIONS} materialeområder.')
    normalized, seen, point_count = [], set(), 0
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            raise ValueError('Et materialeområde er ugyldigt.')
        region_id = region.get('id', f'region-{index + 1}')
        if not isinstance(region_id, str) or not region_id or len(region_id) > 80 or region_id in seen:
            raise ValueError('Materialeområder skal have forskellige, korte id’er.')
        seen.add(region_id)
        points = _ring(region.get('points'), bed, f'Materialeområde {region_id}')
        raw_holes = region.get('holes', [])
        if not isinstance(raw_holes, list) or len(raw_holes) > MAX_REGIONS:
            raise ValueError(f'Materialeområde {region_id} har for mange huller.')
        holes = [_ring(raw, bed, f'Hul i {region_id}') for raw in raw_holes]
        point_count += len(points) + sum(len(hole) for hole in holes)
        if point_count > MAX_TOTAL_POINTS:
            raise ValueError('Materialekanterne har for mange punkter.')
        polygon = Polygon(points, holes)
        if not polygon.is_valid or polygon.area <= EPS:
            raise ValueError(f'Materialeområde {region_id} har ugyldige eller overlappende huller.')
        normalized.append({'id': region_id, 'points': points, 'holes': holes, 'source': 'manual'})
    return normalized


def material_geometry(regions, margin=3, bed=DEFAULT_BED):
    """Return the safe Shapely area, inset from every edge and hole."""
    bed = _bed(bed)
    margin = _nonnegative(margin, 'Sikkerhedsafstand')
    normalized = normalize_regions(regions, bed)
    if not normalized:
        return GeometryCollection()
    # A cutout is forbidden for the whole proposed sheet, even when another
    # overlapping outline happens to cover it.
    material = unary_union([Polygon(r['points']) for r in normalized])
    holes = [Polygon(hole) for r in normalized for hole in r['holes']]
    if holes:
        material = material.difference(unary_union(holes))
    # Inset the bed independently: a region touching the machine edge still
    # needs the requested clearance from that edge.
    safe_bed = box(0, 0, *bed).buffer(-margin)
    return material.buffer(-margin).intersection(safe_bed)


def _size(item):
    if not isinstance(item, dict):
        raise ValueError('En aflevering er ugyldig.')
    geometry = item.get('geometry', item)
    if not isinstance(geometry, dict):
        raise ValueError('En afleverings størrelse er ugyldig.')
    width = _number(geometry.get('width'), 'Afleveringens bredde')
    height = _number(geometry.get('height'), 'Afleveringens højde')
    if width <= 0 or height <= 0:
        raise ValueError('En afleverings størrelse skal være positiv.')
    return width, height


def _item_id(item):
    value = item.get('id') if isinstance(item, dict) else None
    if not isinstance(value, str) or not value or len(value) > 120:
        raise ValueError('En aflevering har et ugyldigt id.')
    return value


def _candidates(safe, used, dimension, axis, bed_size):
    values = {0.0, max(0.0, bed_size - dimension)}
    # Geometry vertices catch narrow ledges and corners missed by the grid.
    boundaries = list(safe.geoms) if hasattr(safe, 'geoms') else [safe]
    for polygon in boundaries:
        if not isinstance(polygon, Polygon):
            continue
        rings = [polygon.exterior, *polygon.interiors]
        for ring in rings:
            for point in ring.coords:
                coordinate = point[axis]
                values.update((coordinate, coordinate - dimension))
    for previous in used:
        low, high = previous.bounds[axis], previous.bounds[axis + 2]
        values.update((low, high, low - dimension, high - dimension))
    values.update(float(i * 5) for i in range(int(bed_size // 5) + 1))
    return sorted(round(v, 4) for v in values if -EPS <= v <= bed_size - dimension + EPS)


def place_on_material(items, regions, margin=3, gap=4, bed=DEFAULT_BED):
    """Place bounding rectangles; deterministic and bounded, without nesting."""
    bed = _bed(bed)
    gap = _nonnegative(gap, 'Afstand mellem afleveringer')
    safe = material_geometry(regions, margin, bed)
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError(f'Angiv højst {MAX_ITEMS} afleveringer.')
    seen, ordered = set(), []
    for item in items:
        item_id = _item_id(item)
        if item_id in seen:
            raise ValueError('En aflevering er angivet flere gange.')
        seen.add(item_id)
        width, height = _size(item)
        ordered.append((item_id, width, height))
    ordered.sort(key=lambda row: (-max(row[1:]), -row[1] * row[2], row[0]))
    placed, unplaced, used = [], [], []
    for item_id, width, height in ordered:
        choice = None
        for rotation, w, h in ((0, width, height), (90, height, width)):
            if w > bed[0] or h > bed[1] or safe.is_empty:
                continue
            xs = _candidates(safe, used, w, 0, bed[0])
            ys = _candidates(safe, used, h, 1, bed[1])
            # Search cap prevents pathological polygons from blocking the UI.
            attempts = 0
            for y in ys:
                if choice is not None and y > choice[0]:
                    break
                for x in xs:
                    attempts += 1
                    if attempts > 30000:
                        break
                    x, y = round(x, 3), round(y, 3)
                    rectangle = box(x, y, x + w, y + h)
                    if safe.covers(rectangle) and all(rectangle.distance(other) >= gap - EPS for other in used):
                        candidate = (y, x, rotation, rectangle)
                        if choice is None or candidate[:3] < choice[:3]:
                            choice = candidate
                        break
                if attempts > 30000 or choice is not None:
                    break
        if choice is None:
            unplaced.append(item_id)
        else:
            y, x, rotation, rectangle = choice
            placed.append({'id': item_id, 'x': x, 'y': y, 'rotation': rotation})
            used.append(rectangle)
    return placed, unplaced


def check_material(entries, regions, margin=3, gap=4, bed=DEFAULT_BED):
    """Return Danish placement problems; an empty approval never permits a job."""
    bed = _bed(bed)
    gap = _nonnegative(gap, 'Afstand mellem afleveringer')
    safe = material_geometry(regions, margin, bed)
    if not isinstance(entries, list) or len(entries) > MAX_ITEMS:
        raise ValueError(f'Angiv højst {MAX_ITEMS} afleveringer.')
    problems, boxes = [], []
    if safe.is_empty and entries:
        problems.append('Der er ikke godkendt et materialeområde med plads til afleveringerne.')
    for entry in entries:
        try:
            item_id = _item_id(entry)
            width, height = _size(entry)
            x = _number(entry.get('x'), 'Placeringens x')
            y = _number(entry.get('y'), 'Placeringens y')
            rotation = entry.get('rotation')
            if isinstance(rotation, bool) or rotation not in (0, 90, 180, 270):
                raise ValueError('Drej kun i trin på 90°.')
        except ValueError:
            problems.append('En aflevering har en ugyldig placering eller størrelse.')
            continue
        name = entry.get('name') if isinstance(entry.get('name'), str) and entry['name'] else item_id
        w, h = (height, width) if rotation in (90, 270) else (width, height)
        rectangle = box(x, y, x + w, y + h)
        if not safe.covers(rectangle):
            problems.append(f'{name} ligger uden for det godkendte materiale eller for tæt på en kant eller et hul.')
        for other_name, other in boxes:
            if rectangle.distance(other) < gap - EPS:
                problems.append(f'{name} ligger for tæt på {other_name}.')
        boxes.append((name, rectangle))
    return problems
