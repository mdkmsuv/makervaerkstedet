"""Safe, bounded project thumbnails.

The project shelf must never execute or hand arbitrary project bytes to a
browser just to draw a card.  This module therefore only accepts a small set
of image representations and emits either a validated image or a generated
SVG.  Scratch scene composition is intentionally partial: the first stored
preview/costume is useful and safe, while a full renderer would be a second
runtime for untrusted project files.
"""

from __future__ import annotations

import io
import json
import math
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath


MAX_INPUT = 50 * 1024 * 1024
MAX_SVG = 2 * 1024 * 1024
MAX_PNG = 16 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRY = 8 * 1024 * 1024
MAX_STL_TRIANGLES = 10_000
MAX_SKETCH_MARKS = 300
MAX_SKETCH_POINTS = 8_000
MAX_JSON = 4 * 1024 * 1024
MAX_SVG_NODES = 10_000
MAX_SVG_DEPTH = 64

_PNG = b"\x89PNG\r\n\x1a\n"
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_PATH = re.compile(r"^[0-9eE+.,\- \t\r\nMmZzLlHhVvCcSsQqTtAa]*$")
_TRANSFORM = re.compile(r"^(?:matrix\([^()]+\)|translate\([^()]+\)|rotate\([^()]+\)|scale\([^()]+\))(?:\s+(?:matrix\([^()]+\)|translate\([^()]+\)|rotate\([^()]+\)|scale\([^()]+\)))*$")
_COLOR = re.compile(r"^(?:none|currentColor|#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla)\([^()]{1,80}\)|[A-Za-z]{1,30})$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _finite(value: object, *, absolute: float = 1_000_000.0) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and abs(number) <= absolute


def _safe_number(value: str, *, absolute: float = 1_000_000.0) -> bool:
    return bool(_NUMBER.fullmatch(value.strip())) and _finite(value, absolute=absolute)


def _safe_png(data: bytes) -> bytes | None:
    """Validate PNG framing and dimensions without decoding image pixels."""
    if not isinstance(data, bytes) or len(data) > MAX_PNG or not data.startswith(_PNG):
        return None
    pos = len(_PNG)
    seen_header = seen_data = seen_end = False
    width = height = 0
    while pos + 12 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        if length > MAX_PNG or pos + 12 + length > len(data):
            return None
        kind = data[pos + 4:pos + 8]
        payload_start = pos + 8
        payload_end = payload_start + length
        payload = data[payload_start:payload_end]
        crc = struct.unpack_from(">I", data, payload_end)[0]
        import zlib
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            return None
        if kind == b"IHDR":
            if seen_header or length != 13:
                return None
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            valid_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if not width or not height or width > 4096 or height > 4096 or width * height > 16_000_000:
                return None
            if colour not in valid_depths or depth not in valid_depths[colour] or compression or filtering or interlace not in (0, 1):
                return None
            seen_header = True
        elif kind == b"IDAT":
            if not seen_header or seen_end:
                return None
            seen_data = True
        elif kind == b"IEND":
            if length or not seen_header or not seen_data or seen_end or payload_end + 4 != len(data):
                return None
            seen_end = True
            break
        pos = payload_end + 4
    return data if seen_header and seen_data and seen_end else None


_SVG_ELEMENTS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan"}
_SVG_NUMERIC = {"x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "dx", "dy", "width", "height", "stroke-width", "opacity", "fill-opacity", "stroke-opacity", "font-size"}
_SVG_ATTRS = _SVG_NUMERIC | {"viewBox", "d", "points", "fill", "stroke", "fill-rule", "clip-rule", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "transform", "preserveAspectRatio", "text-anchor", "font-family", "font-weight", "style"}
_SVG_SAFE_STYLE = {"fill", "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity", "fill-rule", "stroke-linecap", "stroke-linejoin"}


def _safe_svg(data: bytes) -> bytes | None:
    if not isinstance(data, bytes) or not data or len(data) > MAX_SVG:
        return None
    lowered = data.lower()
    if any(token in lowered for token in (b"<!doctype", b"<!entity", b"<script", b"foreignobject", b"javascript:", b"data:", b"onload=", b"onerror=", b"onclick=", b"xlink:href", b"href=")):
        return None
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError, UnicodeError, RecursionError):
        return None
    if _local(root.tag) != "svg":
        return None
    if len(list(root)) > 5000:
        return None
    attrs: dict[str, str] = {}
    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if view_box:
        values = view_box.replace(",", " ").split()
        if len(values) != 4 or not all(_safe_number(v) for v in values) or float(values[2]) <= 0 or float(values[3]) <= 0:
            return None
        attrs["viewBox"] = " ".join(values)
    else:
        width, height = root.attrib.get("width"), root.attrib.get("height")
        if not width or not height or not _safe_number(width.rstrip("px")) or not _safe_number(height.rstrip("px")):
            return None
        attrs["viewBox"] = f"0 0 {float(width.rstrip('px')):g} {float(height.rstrip('px')):g}"
    attrs["xmlns"], attrs["width"], attrs["height"] = "http://www.w3.org/2000/svg", "256", "256"

    def safe_style(value: str) -> str | None:
        if len(value) > 2000 or any(token in value.lower() for token in ("url(", "expression", "@import", "javascript")):
            return None
        kept = []
        for item in value.split(";"):
            if not item.strip():
                continue
            if ":" not in item:
                return None
            key, raw = (part.strip() for part in item.split(":", 1))
            if key not in _SVG_SAFE_STYLE:
                continue
            if key in {"fill", "stroke"} and not _COLOR.fullmatch(raw):
                return None
            if key in {"stroke-width", "opacity", "fill-opacity", "stroke-opacity"} and not _safe_number(raw):
                return None
            if key in {"fill-rule"} and raw not in {"nonzero", "evenodd"}:
                return None
            if key in {"stroke-linecap", "stroke-linejoin"} and raw not in {"round", "butt", "square", "miter", "bevel"}:
                return None
            kept.append(f"{key}:{raw}")
        return ";".join(kept)

    def safe_root_attr(key: str, value: str) -> bool:
        if key not in _SVG_ATTRS or key in {"viewBox", "width", "height", "xmlns"}:
            return True
        if key == "style":
            return safe_style(value) is not None
        if key in _SVG_NUMERIC:
            return _safe_number(value)
        if key in {"fill", "stroke"}:
            return bool(_COLOR.fullmatch(value))
        if key == "transform":
            return bool(_TRANSFORM.fullmatch(value)) and all(_safe_number(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value))
        return True

    for key, value in root.attrib.items():
        key = _local(key)
        value = str(value).strip()
        if key in {"fill", "stroke", "style", "transform", "opacity", "fill-opacity", "stroke-opacity"}:
            if not safe_root_attr(key, value):
                return None
            attrs[key] = safe_style(value) if key == "style" else value

    node_count = 0

    def clean(node: ET.Element, output: ET.Element, depth: int = 1) -> bool:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_SVG_NODES or depth > MAX_SVG_DEPTH:
            return False
        name = _local(node.tag)
        if name not in _SVG_ELEMENTS:
            return True
        if node.text:
            if len(node.text) > 100_000 or any(ord(char) < 32 and char not in "\t\r\n" for char in node.text):
                return False
            if name in {"text", "tspan"}:
                output.text = node.text
        for key, value in node.attrib.items():
            key = _local(key)
            if key not in _SVG_ATTRS:
                continue
            value = str(value).strip()
            if len(value) > 300_000 or any(c in value for c in "<>\x00"):
                return False
            if key in _SVG_NUMERIC:
                if not _safe_number(value):
                    return False
            elif key == "viewBox":
                continue
            elif key == "d":
                if len(value) > 500_000 or not _PATH.fullmatch(value):
                    return False
            elif key == "points":
                if not re.fullmatch(r"[0-9eE+.,\- \t\r\n]+", value):
                    return False
            elif key == "transform":
                if not _TRANSFORM.fullmatch(value) or any(not _safe_number(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value)):
                    return False
            elif key == "style":
                value = safe_style(value)
                if value is None:
                    return False
            elif key in {"fill", "stroke"} and not _COLOR.fullmatch(value):
                return False
            elif key in {"fill-rule", "clip-rule"} and value not in {"nonzero", "evenodd"}:
                return False
            elif key in {"stroke-linecap", "stroke-linejoin", "preserveAspectRatio"} and (len(value) > 80 or "url" in value.lower()):
                return False
            output.set(key, value)
        for child in list(node):
            if _local(child.tag) not in _SVG_ELEMENTS:
                continue  # metadata/title/desc and other non-visual nodes
            child_output = ET.SubElement(output, _local(child.tag))
            if not clean(child, child_output, depth + 1):
                return False
            if not list(child_output) and not child_output.attrib and not (child_output.text or "").strip() and _local(child.tag) not in {"text", "tspan"}:
                output.remove(child_output)
        return True

    cleaned = ET.Element("svg", attrs)
    for child in list(root):
        if _local(child.tag) not in _SVG_ELEMENTS:
            continue
        child_output = ET.SubElement(cleaned, _local(child.tag))
        if not clean(child, child_output):
            return None
        if not list(child_output) and not child_output.attrib and not (child_output.text or "").strip() and _local(child.tag) not in {"text", "tspan"}:
            cleaned.remove(child_output)
    result = ET.tostring(cleaned, encoding="utf-8", xml_declaration=True)
    return result if len(result) <= MAX_SVG else None


def _svg_preview(polygons: list[list[tuple[float, float]]], *, fill: str = "#2563eb") -> bytes | None:
    points = [point for polygon in polygons for point in polygon]
    if not points:
        return None
    lo_x, hi_x = min(p[0] for p in points), max(p[0] for p in points)
    lo_y, hi_y = min(p[1] for p in points), max(p[1] for p in points)
    if hi_x <= lo_x or hi_y <= lo_y:
        return None
    margin = max(1.0, (hi_x - lo_x + hi_y - lo_y) * 0.04)
    view = f"{lo_x-margin:.6g} {lo_y-margin:.6g} {hi_x-lo_x+2*margin:.6g} {hi_y-lo_y+2*margin:.6g}"
    body = "".join('<polygon points="' + " ".join(f"{x:.6g},{y:.6g}" for x, y in polygon) + f'" fill="{fill}" stroke="#12304a" stroke-width="{max((hi_x-lo_x+hi_y-lo_y)/500, 0.15):.6g}" stroke-linejoin="round"/>' for polygon in polygons)
    result = f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="{view}" preserveAspectRatio="xMidYMid meet"><rect width="100%" height="100%" fill="#f8fafc"/>{body}</svg>'.encode()
    return result if len(result) <= MAX_SVG else None


def _stl(data: bytes) -> bytes | None:
    triangles: list[list[tuple[float, float, float]]] = []
    binary = len(data) >= 84 and struct.unpack_from("<I", data, 80)[0] and 84 + struct.unpack_from("<I", data, 80)[0] * 50 == len(data)
    if binary:
        count = struct.unpack_from("<I", data, 80)[0]
        if count > MAX_STL_TRIANGLES:
            return None
        for offset in range(84, len(data), 50):
            values = struct.unpack_from("<9f", data, offset + 12)
            if not all(math.isfinite(v) and abs(v) <= 1_000_000 for v in values):
                return None
            triangles.append([tuple(values[index:index + 3]) for index in (0, 3, 6)])
    else:
        if len(data) > MAX_INPUT:
            return None
        try:
            text = data.decode("ascii")
        except UnicodeDecodeError:
            return None
        if not text.lstrip().lower().startswith("solid") or "endsolid" not in text.lower():
            return None
        matches = list(re.finditer(r"\bvertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", text, re.I))
        if not matches or len(matches) // 3 > MAX_STL_TRIANGLES or len(matches) % 3:
            return None
        for start in range(0, len(matches), 3):
            tri = []
            for match in matches[start:start + 3]:
                try:
                    values = tuple(float(match.group(i)) for i in (1, 2, 3))
                except (TypeError, ValueError, OverflowError):
                    return None
                if not all(math.isfinite(v) and abs(v) <= 1_000_000 for v in values):
                    return None
                tri.append(values)
            triangles.append(tri)
    if not triangles:
        return None
    projected = []
    # Draw farther faces first so the tiny SVG thumbnail has a stable painter's
    # order without needing a 3D runtime or a browser-side script.
    triangles.sort(key=lambda tri: sum(point[2] for point in tri) / 3.0)
    for triangle in triangles:
        polygon = [(x + z * 0.45, -y - z * 0.35) for x, y, z in triangle]
        if len(set(polygon)) >= 3:
            projected.append(polygon)
    return _svg_preview(projected, fill="#63a4d8")


def _sketch(data: bytes) -> bytes | None:
    if len(data) > MAX_JSON:
        return None
    try:
        drawing = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(drawing, dict) or drawing.get("version") != 1:
        return None
    marks = drawing.get("marks")
    height = drawing.get("height", 3)
    if not isinstance(marks, list) or len(marks) > MAX_SKETCH_MARKS or not _finite(height, absolute=180) or not 0.4 <= float(height) <= 180:
        return None
    shapes = []
    count = 0
    for mark in marks:
        if not isinstance(mark, dict) or mark.get("tool") not in {"Rektangel", "Ellipse", "Polygon", "Frihånd", "Viskelæder"}:
            return None
        points = mark.get("points")
        width = mark.get("width", 3)
        if not isinstance(points, list) or len(points) > MAX_SKETCH_POINTS or not _finite(width, absolute=30) or not 0.4 <= float(width) <= 30:
            return None
        count += len(points)
        if count > MAX_SKETCH_POINTS:
            return None
        parsed = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or not all(_finite(v, absolute=180) and 0 <= float(v) <= 180 for v in point):
                return None
            parsed.append((float(point[0]), float(point[1])))
        if len(parsed) < 1:
            continue
        tool = mark["tool"]
        if tool in {"Rektangel", "Ellipse"} and len(parsed) >= 2:
            x, y = min(parsed[0][0], parsed[-1][0]), min(parsed[0][1], parsed[-1][1])
            w, h = abs(parsed[-1][0] - parsed[0][0]), abs(parsed[-1][1] - parsed[0][1])
            if w and h:
                shapes.append(("rectangle" if tool == "Rektangel" else "ellipse", [(x, y), (x + w, y + h)], width, mark.get("cut", False)))
        elif len(parsed) >= 2:
            shapes.append(("polygon" if tool == "Polygon" else "polyline", parsed, width, mark.get("cut", False)))
        else:
            x, y = parsed[0]
            shapes.append(("circle", [(x, y)], width, mark.get("cut", False)))
    if not shapes:
        return None
    body = ['<rect width="180" height="180" fill="#f8fafc"/>']
    for tool, points, width, cut in shapes:
        colour = "#f8fafc" if cut or tool == "circle" and cut else "#2563eb"
        if tool == "rectangle":
            (x1, y1), (x2, y2) = points
            body.append(f'<rect x="{x1:g}" y="{y1:g}" width="{x2-x1:g}" height="{y2-y1:g}" fill="{colour}" stroke="#12304a" stroke-width="{width:g}"/>')
        elif tool == "ellipse":
            (x1, y1), (x2, y2) = points
            body.append(f'<ellipse cx="{(x1+x2)/2:g}" cy="{(y1+y2)/2:g}" rx="{(x2-x1)/2:g}" ry="{(y2-y1)/2:g}" fill="{colour}" stroke="#12304a" stroke-width="{width:g}"/>')
        elif tool == "circle":
            x, y = points[0]
            body.append(f'<circle cx="{x:g}" cy="{y:g}" r="{width/2:g}" fill="{colour}"/>')
        else:
            tag = "polygon" if tool == "polygon" else "polyline"
            body.append(f'<{tag} points="{" ".join(f"{x:g},{y:g}" for x,y in points)}" fill="{colour if tag == "polygon" else "none"}" stroke="#12304a" stroke-width="{width:g}" stroke-linecap="round" stroke-linejoin="round"/>')
    result = f'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 180 180" preserveAspectRatio="xMidYMid meet">{"".join(body)}</svg>'.encode()
    return result if len(result) <= MAX_SVG else None


def _zip_preview(data: bytes, *, scratch: bool = False) -> tuple[bytes, str] | None:
    if len(data) > MAX_INPUT:
        return None
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    with archive:
        try:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES or sum(max(0, info.file_size) for info in infos) > MAX_ZIP_BYTES:
                return None
        except (OSError, RuntimeError, ValueError, OverflowError, zipfile.BadZipFile):
            return None
        safe: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = info.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or info.file_size > MAX_ZIP_ENTRY or (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue
            safe[name] = info
        names = sorted(safe, key=lambda n: (0 if PurePosixPath(n).name.casefold() in {"snapshot.png", "preview.png", "thumbnail.png"} else 1, n.casefold()))
        for name in names:
            if PurePosixPath(name).name.casefold() not in {"snapshot.png", "preview.png", "thumbnail.png"}:
                continue
            try:
                image = _safe_png(archive.read(safe[name]))
            except (OSError, RuntimeError, ValueError, EOFError, KeyError, NotImplementedError, zipfile.BadZipFile):
                image = None
            if image is not None:
                return image, "image/png"
        if scratch:
            project = safe.get("project.json")
            if project and project.file_size <= MAX_JSON:
                try:
                    obj = json.loads(archive.read(project))
                except (OSError, ValueError, UnicodeDecodeError, EOFError, KeyError, NotImplementedError, zipfile.BadZipFile):
                    obj = None
                candidates = []
                if isinstance(obj, dict):
                    targets = obj.get("targets", [])
                    if not isinstance(targets, list):
                        return None
                    for target in targets:
                        if not isinstance(target, dict):
                            continue
                        costumes = target.get("costumes", [])
                        if isinstance(costumes, list):
                            for costume in costumes:
                                if not isinstance(costume, dict):
                                    continue
                                asset = str(costume.get("assetId", ""))
                                fmt = str(costume.get("dataFormat", "")).lower()
                                if re.fullmatch(r"[0-9a-fA-F]{32}", asset) and fmt in {"png", "svg", "jpg", "jpeg"}:
                                    candidates.append(asset + "." + fmt)
                for candidate in candidates:
                    info = safe.get(candidate)
                    if not info:
                        continue
                    try:
                        raw = archive.read(info)
                    except (OSError, RuntimeError, ValueError, EOFError, KeyError, NotImplementedError, zipfile.BadZipFile):
                        continue
                    if candidate.endswith(".png"):
                        image = _safe_png(raw)
                        if image is not None:
                            return image, "image/png"
                    elif candidate.endswith(".svg"):
                        image = _safe_svg(raw)
                        if image is not None:
                            return image, "image/svg+xml"
    return None


def build_preview(meta, data: bytes) -> tuple[bytes, str] | None:
    """Return ``(bytes, mime)`` for a safe thumbnail, or ``None``."""
    if not isinstance(meta, dict) or not isinstance(data, bytes) or not 0 < len(data) <= MAX_INPUT:
        return None
    filename = str(meta.get("filename", "")).casefold()
    kind = str(meta.get("kind", "")).casefold()
    if filename.endswith(".png"):
        image = _safe_png(data)
        return (image, "image/png") if image is not None else None
    if filename.endswith(".svg"):
        image = _safe_svg(data)
        return (image, "image/svg+xml") if image is not None else None
    if filename.endswith(".stl"):
        image = _stl(data)
        return (image, "image/svg+xml") if image is not None else None
    if filename.endswith(".klosterskitse"):
        image = _sketch(data)
        return (image, "image/svg+xml") if image is not None else None
    if filename.endswith(".sb3") or kind == "scratch":
        return _zip_preview(data, scratch=True)
    if filename.endswith(".zip") or kind == "cadoodle":
        return _zip_preview(data)
    return None

