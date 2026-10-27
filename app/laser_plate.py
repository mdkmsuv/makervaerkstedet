"""Plates for the laser station: geometry of prepared deliveries, automatic
placement on the machine bed and a Beam Studio document (.bvg) for FLUX Beambox II.

Only files are produced here. Nothing in this module talks to a machine.
"""
import base64
from io import BytesIO
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from PIL import Image
from app.laser_machines import get_machine

NS = 'http://www.w3.org/2000/svg'
XLINK = 'http://www.w3.org/1999/xlink'
tag = lambda name: f'{{{NS}}}{name}'

PRESETS_FILE = Path(__file__).with_name('laser_presets_fbb2.json')
# Beambox II (fbb2) in Beam Studio: 600 x 375 mm, 10 document units per mm.
BED_WIDTH = 600.0
BED_HEIGHT = 375.0
DPMM = 10
OPERATIONS = ('engrave-fill', 'engrave-line', 'cut')
# Beam Studio runs the layers from the top of its layer list, i.e. the last group in
# the document first. Cutting therefore comes first in the file and runs last.
LAYER_ORDER = ('cut', 'engrave-line', 'engrave-fill')
LAYER_COLOR = {'cut': '#B4121A', 'engrave-line': '#256B93', 'engrave-fill': '#333333'}
LAYER_TITLE = {'cut': 'Skæring', 'engrave-line': 'Gravering, streger', 'engrave-fill': 'Gravering, flader'}
MAX_ITEMS = 60


# --- FLUX presets ------------------------------------------------------------------

def load_presets(path=None, *, model='fbb2'):
    if path is None:
        path = get_machine(model)['presets_file']
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('model') != model:
        raise ValueError('Materialeindstillingerne passer ikke til lasermodellen.')
    return data


def materials(preset_data):
    """Groups FLUX presets into materials: cutting preset plus the material's engraving."""
    presets = preset_data['presets']
    found = []
    engraving_used = set()
    for key, preset in presets.items():
        match = re.fullmatch(r'([a-z_]+?)_(\d+)mm_cutting', key)
        if not match:
            continue
        base, thickness = match[1], int(match[2])
        engrave = next((k for k in (f'{base}_engraving', f'{base}_bw_engraving') if k in presets), None)
        if engrave:
            engraving_used.add(engrave)
        label = preset['name'].split(' - ')[0].strip()
        found.append({'id': f'{base}_{thickness}mm', 'name': f'{label} {thickness} mm', 'sort': (label, thickness),
                      'cut': key, 'engrave': engrave})
    for key, preset in presets.items():
        if key.endswith('_engraving') and key not in engraving_used:
            label = preset['name'].split(' - ')[0].strip()
            found.append({'id': key.removesuffix('_engraving'), 'name': f'{label} (kun gravering)', 'sort': ('~' + label, 0),
                          'cut': None, 'engrave': key})
    found.sort(key=lambda m: m['sort'])
    for item in found:
        del item['sort']
        for role in ('cut', 'engrave'):
            item[role + 'Preset'] = dict(presets[item[role]], key=item[role]) if item[role] else None
    return found


def material_by_id(preset_data, material_id):
    material = next((m for m in materials(preset_data) if m['id'] == material_id), None)
    if not material:
        raise ValueError('Vælg et materiale.')
    return material


# --- geometry ------------------------------------------------------------------------

NUMBER = re.compile(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?')
COMMANDS = set('MmLlHhVvCcSsQqTtAaZz')
ARGS = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0}


def _path_tokens(d):
    """Yields (command, [numbers]) segments. Arc flags may be written without separators."""
    i, n = 0, len(d)
    command = None
    while i < n:
        ch = d[i]
        if ch in ' \t\r\n,':
            i += 1
            continue
        if ch in COMMANDS:
            command = ch
            i += 1
            if command in 'Zz':
                yield command, []
            continue
        if command is None or command in 'Zz':
            raise ValueError('En kontur kunne ikke læses.')
        count = ARGS[command.upper()]
        values = []
        while len(values) < count:
            while i < n and d[i] in ' \t\r\n,':
                i += 1
            if command in 'Aa' and len(values) in (3, 4) and i < n and d[i] in '01':
                values.append(float(d[i]))
                i += 1
                continue
            match = NUMBER.match(d, i)
            if not match:
                raise ValueError('En kontur kunne ikke læses.')
            values.append(float(match[0]))
            i = match.end()
        yield command, values
        if command == 'M':
            command = 'L'
        elif command == 'm':
            command = 'l'


def _arc_points(x1, y1, rx, ry, angle, large, sweep, x2, y2, steps):
    """Samples an SVG arc by the endpoint-to-centre conversion in SVG 1.1 appendix F.6."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x2, y2)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(angle % 360)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    scale = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if scale > 1:
        rx, ry = rx * math.sqrt(scale), ry * math.sqrt(scale)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    factor = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        factor = -factor
    cxp, cyp = factor * rx * y1p / ry, -factor * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2
    angle_of = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    start = angle_of(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle_of((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    points = []
    for k in range(1, steps + 1):
        t = start + delta * k / steps
        px, py = rx * math.cos(t), ry * math.sin(t)
        points.append((cos_p * px - sin_p * py + cx, sin_p * px + cos_p * py + cy))
    return points


def path_points(d, steps=24):
    """Points along a path, dense enough for a bounding box to within a fraction of a mm."""
    points = []
    x = y = start_x = start_y = 0.0
    last_control = None  # (x, y, kind) for S/T reflection
    for command, v in _path_tokens(d):
        rel = command.islower()
        up = command.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if up == 'Z':
            x, y = start_x, start_y
            last_control = None
            continue
        if up == 'M':
            x, y = ox + v[0], oy + v[1]
            start_x, start_y = x, y
            points.append((x, y))
            last_control = None
        elif up == 'L':
            x, y = ox + v[0], oy + v[1]
            points.append((x, y))
            last_control = None
        elif up == 'H':
            x = (x if rel else 0.0) + v[0]
            points.append((x, y))
            last_control = None
        elif up == 'V':
            y = (y if rel else 0.0) + v[0]
            points.append((x, y))
            last_control = None
        elif up in ('C', 'S'):
            if up == 'C':
                c1 = (ox + v[0], oy + v[1])
                c2, end = (ox + v[2], oy + v[3]), (ox + v[4], oy + v[5])
            else:
                c1 = (2 * x - last_control[0], 2 * y - last_control[1]) if last_control and last_control[2] == 'C' else (x, y)
                c2, end = (ox + v[0], oy + v[1]), (ox + v[2], oy + v[3])
            for k in range(1, steps + 1):
                t = k / steps
                a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                points.append((a * x + b * c1[0] + c * c2[0] + e * end[0], a * y + b * c1[1] + c * c2[1] + e * end[1]))
            last_control = (c2[0], c2[1], 'C')
            x, y = end
        elif up in ('Q', 'T'):
            if up == 'Q':
                c1, end = (ox + v[0], oy + v[1]), (ox + v[2], oy + v[3])
            else:
                c1 = (2 * x - last_control[0], 2 * y - last_control[1]) if last_control and last_control[2] == 'Q' else (x, y)
                end = (ox + v[0], oy + v[1])
            for k in range(1, steps + 1):
                t = k / steps
                a, b, e = (1 - t) ** 2, 2 * (1 - t) * t, t * t
                points.append((a * x + b * c1[0] + e * end[0], a * y + b * c1[1] + e * end[1]))
            last_control = (c1[0], c1[1], 'Q')
            x, y = end
        elif up == 'A':
            end = (ox + v[5], oy + v[6])
            points.extend(_arc_points(x, y, v[0], v[1], v[2], int(v[3]), int(v[4]), end[0], end[1], steps))
            x, y = end
            last_control = None
    return points


def parse_matrix(text):
    if not text:
        return None
    match = re.fullmatch(r'\s*matrix\(([^)]*)\)\s*', text)
    if not match:
        raise ValueError('Kun matrix-placering kan bruges på laserpladen.')
    values = [float(n) for n in re.split(r'[\s,]+', match[1].strip())]
    if len(values) != 6 or not all(math.isfinite(n) for n in values):
        raise ValueError('En placering er ugyldig.')
    return values


def apply_matrix(m, point):
    if not m:
        return point
    a, b, c, d, e, f = m
    x, y = point
    return a * x + c * y + e, b * x + d * y + f


def multiply(m1, m2):
    """m1 · m2 in SVG matrix order: first m2, then m1."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1]


def job_geometry(svg):
    """Parts and bounding box (mm) of a prepared delivery from the teacher queue."""
    root = ET.fromstring(svg)
    if root.tag != tag('svg') or root.get('data-laservaerksted') != '1':
        raise ValueError('Afleveringen er ikke en tegning fra Laserværkstedet.')
    parts, images, points = [], [], []
    for group in root:
        if group.tag != tag('g'):
            continue
        group_op = group.get('data-operation')
        if group_op not in OPERATIONS:
            raise ValueError('Afleveringen har en ukendt handling.')
        for element in group:
            op = element.get('data-operation', group_op)
            if op not in OPERATIONS:
                raise ValueError('Afleveringen har en ukendt handling.')
            matrix = parse_matrix(element.get('transform'))
            if element.tag == tag('path'):
                d = element.get('d', '')
                sampled = [apply_matrix(matrix, p) for p in path_points(d)]
                if not sampled:
                    continue
                points.extend(sampled)
                parts.append({'op': op, 'd': d, 'fillRule': 'evenodd' if element.get('fill-rule') == 'evenodd' else 'nonzero',
                              'transform': matrix})
            elif element.tag == tag('image'):
                href = element.get('href') or element.get(f'{{{XLINK}}}href') or ''
                if not re.fullmatch(r'data:image/png;base64,[A-Za-z0-9+/]+={0,2}', href):
                    raise ValueError('Et billede i afleveringen kan ikke bruges.')
                width, height = float(element.get('width', '0')), float(element.get('height', '0'))
                if width <= 0 or height <= 0:
                    raise ValueError('Et billede i afleveringen har ingen størrelse.')
                points.extend(apply_matrix(matrix, p) for p in ((0, 0), (width, 0), (0, height), (width, height)))
                images.append({'op': 'engrave-fill', 'href': href, 'width': width, 'height': height, 'transform': matrix})
            else:
                raise ValueError('Afleveringen indeholder ukendte dele.')
    if not points:
        raise ValueError('Afleveringen er tom.')
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    operations = sorted({p['op'] for p in parts} | {i['op'] for i in images}, key=OPERATIONS.index)
    return {'bbox': [round(v, 3) for v in bbox], 'width': round(bbox[2] - bbox[0], 2), 'height': round(bbox[3] - bbox[1], 2),
            'operations': operations, 'parts': parts, 'images': images}


# --- placement -------------------------------------------------------------------------

def rotated_size(width, height, rotation):
    return (height, width) if rotation in (90, 270) else (width, height)


def placement_matrix(bbox, x, y, rotation):
    """Moves the content's bounding box to (x, y) on the bed, turned clockwise in steps of 90°."""
    bx, by, bx2, by2 = bbox
    bw, bh = bx2 - bx, by2 - by
    if rotation == 0:
        return [1, 0, 0, 1, x - bx, y - by]
    if rotation == 90:
        return [0, 1, -1, 0, x + bh + by, y - bx]
    if rotation == 180:
        return [-1, 0, 0, -1, x + bw + bx, y + bh + by]
    if rotation == 270:
        return [0, -1, 1, 0, x - by, y + bw + bx]
    raise ValueError('Drej kun i trin på 90°.')


def auto_place(items, bed=(BED_WIDTH, BED_HEIGHT), margin=5.0, gap=4.0):
    """MaxRects placement from the top-left corner (the machine origin).

    items: [{'id', 'width', 'height'}]. Returns (placements, unplaced ids). Items are kept
    at least `gap` mm apart and `margin` mm from the edge of the bed.
    """
    usable_w, usable_h = bed[0] - 2 * margin + gap, bed[1] - 2 * margin + gap
    free = [(0.0, 0.0, usable_w, usable_h)]
    placements, unplaced = [], []
    ordered = sorted(items, key=lambda i: (-max(i['width'], i['height']), -i['width'] * i['height'], i['id']))
    for item in ordered:
        best = None
        for rotation in (0, 90):
            w, h = rotated_size(item['width'], item['height'], rotation)
            w, h = w + gap, h + gap
            for fx, fy, fw, fh in free:
                if w <= fw + 1e-9 and h <= fh + 1e-9:
                    # Top first, then left: the used area stays in one corner of the sheet.
                    score = (round(fy + h, 6), round(fx, 6), rotation)
                    if best is None or score < best[0]:
                        best = (score, fx, fy, w, h, rotation)
        if best is None:
            unplaced.append(item['id'])
            continue
        _, px, py, pw, ph, rotation = best
        placements.append({'id': item['id'], 'x': round(px + margin, 2), 'y': round(py + margin, 2), 'rotation': rotation})
        used = (px, py, pw, ph)
        next_free = []
        for rect in free:
            next_free.extend(_split(rect, used))
        free = [r for r in next_free if not any(r is not o and _contains(o, r) for o in next_free)]
        # Remove exact duplicates that survive the containment test.
        unique = []
        for r in free:
            if r not in unique:
                unique.append(r)
        free = unique
    return placements, unplaced


def _split(rect, used):
    fx, fy, fw, fh = rect
    ux, uy, uw, uh = used
    if ux >= fx + fw or ux + uw <= fx or uy >= fy + fh or uy + uh <= fy:
        return [rect]
    result = []
    if ux > fx:
        result.append((fx, fy, ux - fx, fh))
    if ux + uw < fx + fw:
        result.append((ux + uw, fy, fx + fw - ux - uw, fh))
    if uy > fy:
        result.append((fx, fy, fw, uy - fy))
    if uy + uh < fy + fh:
        result.append((fx, uy + uh, fw, fy + fh - uy - uh))
    return [r for r in result if r[2] > 1e-6 and r[3] > 1e-6]


def _contains(outer, inner):
    return (inner[0] >= outer[0] - 1e-9 and inner[1] >= outer[1] - 1e-9 and
            inner[0] + inner[2] <= outer[0] + outer[2] + 1e-9 and inner[1] + inner[3] <= outer[1] + outer[3] + 1e-9)


def check_plate(entries, bed=(BED_WIDTH, BED_HEIGHT)):
    """entries: [{'id', 'name', 'geometry', 'x', 'y', 'rotation'}]. Returns Danish problems."""
    problems = []
    boxes = []
    for entry in entries:
        w, h = rotated_size(entry['geometry']['width'], entry['geometry']['height'], entry['rotation'])
        box = (entry['x'], entry['y'], entry['x'] + w, entry['y'] + h)
        if box[0] < -0.01 or box[1] < -0.01 or box[2] > bed[0] + 0.01 or box[3] > bed[1] + 0.01:
            problems.append(f'{entry["name"]} ligger uden for laserens område.')
        for other_name, other in boxes:
            if box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]:
                problems.append(f'{entry["name"]} overlapper {other_name}.')
        boxes.append((entry['name'], box))
    return problems


def read_placements(data, known_ids):
    """Validates placements sent from the station page."""
    items = data.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f'Læg mellem 1 og {MAX_ITEMS} afleveringer på pladen.')
    seen, result = set(), []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Ugyldig placering.')
        job_id, x, y, rotation = item.get('id'), item.get('x'), item.get('y'), item.get('rotation')
        if not isinstance(job_id, str) or job_id not in known_ids or job_id in seen:
            raise ValueError('En aflevering på pladen findes ikke i køen.')
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)):
            raise ValueError('Ugyldig placering.')
        if rotation not in (0, 90, 180, 270):
            raise ValueError('Drej kun i trin på 90°.')
        seen.add(job_id)
        result.append({'id': job_id, 'x': float(x), 'y': float(y), 'rotation': rotation})
    return result


# --- Beam Studio document ---------------------------------------------------------------

def _rgba_png(href):
    """FLUXGhost composes images as RGBA, as Beam Studio sends them. Grayscale PNGs fail."""
    raw = base64.b64decode(href.split(',', 1)[1], validate=True)
    with Image.open(BytesIO(raw)) as image:
        image.load()
        rgba = image.convert('RGBA')
    output = BytesIO()
    rgba.save(output, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


def _fmt(values):
    return ','.join(f'{v:.6f}'.rstrip('0').rstrip('.') if v != int(v) else str(int(v)) for v in values)


def build_bvg(entries, material, title='Laserplade', *, model='fbb2', bed=None):
    """Beam Studio document for a trusted machine with FLUX's own presets.

    entries: [{'id', 'name', 'geometry', 'x', 'y', 'rotation'}]; coordinates in mm.
    """
    machine = get_machine(model)
    if bed is not None and tuple(bed) != (machine['width'], machine['height']):
        raise ValueError('Pladens mål passer ikke til lasermodellen.')
    used = {op for entry in entries for op in entry['geometry']['operations']}
    if 'cut' in used and not material['cut']:
        raise ValueError(f'{material["name"]} kan kun graveres. Fjern skæringer eller vælg et andet materiale.')
    if used & {'engrave-line', 'engrave-fill'} and not material['engrave']:
        raise ValueError(f'{material["name"]} har ingen FLUX-indstilling til gravering.')
    ET.register_namespace('', NS)
    ET.register_namespace('xlink', XLINK)
    root = ET.Element(tag('svg'), {
        'id': 'svgcontent', 'width': str(int(machine['documentWidth'] * DPMM)),
        'height': str(int(machine['documentHeight'] * DPMM)),
        'viewBox': f'0 0 {int(machine["documentWidth"] * DPMM)} {int(machine["documentHeight"] * DPMM)}',
        'data-workarea': model,
        'data-en_af': 'false', 'data-en_diode': 'false', 'data-rotary_mode': '0', 'data-engrave_dpi': 'medium',
    })
    counter = 0
    for op in LAYER_ORDER:
        if op not in used:
            continue
        preset = material['cutPreset'] if op == 'cut' else material['engravePreset']
        layer = ET.SubElement(root, tag('g'), {
            'class': 'layer', 'data-color': LAYER_COLOR[op], 'data-speed': _fmt([preset['speed']]),
            'data-strength': _fmt([preset['power']]), 'data-repeat': str(preset['repeat']), 'data-minPower': '0',
            'data-height': '-3', 'data-zstep': '0', 'data-diode': '0', 'data-configName': preset['key'],
            'data-module': '15', 'data-backlash': '0', 'data-focus': '-2', 'data-focusStep': '-2', 'display': 'inline',
        })
        ET.SubElement(layer, tag('title')).text = f'{LAYER_TITLE[op]} – {material["name"]}'
        for entry in entries:
            geometry = entry['geometry']
            place = [v * DPMM for v in placement_matrix(geometry['bbox'], entry['x'], entry['y'], entry['rotation'])]
            for part in geometry['parts']:
                if part['op'] != op:
                    continue
                counter += 1
                matrix = multiply(place, part['transform']) if part['transform'] else place
                attrs = {'id': f'svg_{counter}', 'd': part['d'], 'transform': f'matrix({_fmt(matrix)})',
                         'fill-rule': part['fillRule'], 'vector-effect': 'non-scaling-stroke', 'data-elev': entry['name'][:80]}
                if op == 'engrave-fill':
                    attrs.update({'fill': LAYER_COLOR[op], 'fill-opacity': '1', 'stroke': 'none'})
                else:
                    attrs.update({'fill': 'none', 'fill-opacity': '0', 'stroke': LAYER_COLOR[op], 'stroke-width': '1'})
                ET.SubElement(layer, tag('path'), attrs)
            for image in geometry['images']:
                if image['op'] != op:
                    continue
                counter += 1
                matrix = multiply(place, image['transform']) if image['transform'] else place
                href = _rgba_png(image['href'])
                ET.SubElement(layer, tag('image'), {
                    'id': f'svg_{counter}', 'x': '0', 'y': '0', 'width': _fmt([image['width']]), 'height': _fmt([image['height']]),
                    f'{{{XLINK}}}href': href, 'origImage': href, 'data-shading': 'true',
                    'data-threshold': '254', 'data-ratiofixed': 'true', 'preserveAspectRatio': 'none',
                    'transform': f'matrix({_fmt(matrix)})', 'style': 'image-rendering:optimizeQuality',
                    'data-elev': entry['name'][:80],
                })
    return ET.tostring(root, encoding='unicode')


def expected_extent(entries):
    """Bounding box (mm) of everything on the plate, for comparison with FLUX's own result."""
    boxes = []
    for entry in entries:
        w, h = rotated_size(entry['geometry']['width'], entry['geometry']['height'], entry['rotation'])
        boxes.append((entry['x'], entry['y'], entry['x'] + w, entry['y'] + h))
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
