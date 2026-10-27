"""Small, bounded adapter around the vendored Boxes.py ABox generator.

Boxes.py remains the source of the box geometry.  This module only exposes a
small set of classroom-safe values and returns the generated SVG in memory.
The vendored source and its GPL-3.0-or-later licence are documented in
``third_party/BOXES-PY-SOURCE.md``.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
BOXES_SOURCE = ROOT / "third_party" / "boxes.py"
if str(BOXES_SOURCE) not in sys.path:
    sys.path.insert(0, str(BOXES_SOURCE))

MAX_DIMENSION = 300.0
MAX_OUTPUT = 1_500_000
LASER_BED_WIDTH = 600.0
LASER_BED_HEIGHT = 375.0
NS = "http://www.w3.org/2000/svg"


def _number(data: dict, key: str, low: float, high: float) -> float:
    value = data.get(key)
    if isinstance(value, bool):
        raise ValueError(f"{key} skal være et tal.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} skal være et tal.") from None
    if not low <= value <= high:
        raise ValueError(f"{key} skal være mellem {low:g} og {high:g} mm.")
    return value


def _svg_dimensions(source: bytes) -> tuple[float, float]:
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        raise ValueError("Kassegeneratoren lavede en ugyldig SVG.") from None
    if root.tag != f"{{{NS}}}svg":
        raise ValueError("Kassegeneratoren lavede ikke en SVG.")

    def mm(value: str | None) -> float:
        if not value or not value.endswith("mm"):
            raise ValueError("Kasse-SVG'en mangler mål i millimeter.")
        try:
            result = float(value[:-2])
        except ValueError:
            raise ValueError("Kasse-SVG'en har ugyldige mål.") from None
        if not 1 <= result <= 600:
            raise ValueError("Kassen blev større end det tilladte arbejdsområde.")
        return result

    width, height = mm(root.get("width")), mm(root.get("height"))
    if width > LASER_BED_WIDTH or height > LASER_BED_HEIGHT:
        raise ValueError("Kassen er større end Beambox II-pladen på 600 × 375 mm.")
    return width, height


def generate_box_svg(data: dict) -> tuple[str, dict[str, float | str]]:
    """Generate one simple finger-joint box using official Boxes.py code.

    ``x``, ``y`` and ``h`` are inner dimensions.  ``thickness`` is material
    thickness and ``burn`` is Boxes.py's kerf/burn correction (half the measured
    kerf width).  Limits are
    deliberately conservative for a classroom laser bed.
    """
    if not isinstance(data, dict):
        raise ValueError("Ugyldige kasseindstillinger.")
    kind = data.get("kind", "abox")
    if kind != "abox":
        raise ValueError("Vælg den understøttede kassetype.")

    x = _number(data, "x", 20, MAX_DIMENSION)
    y = _number(data, "y", 20, MAX_DIMENSION)
    h = _number(data, "h", 20, MAX_DIMENSION)
    thickness = _number(data, "thickness", 1.5, 10)
    burn = _number(data, "burn", 0, 1)

    # Import lazily so a server that only serves the other workshops can still
    # start and can return a useful error if the optional generator is absent.
    try:
        from boxes.generators.abox import ABox
    except ImportError as error:
        raise RuntimeError(
            "Kassegeneratoren mangler sine Python-afhængigheder."
        ) from error

    box = ABox()
    # The parser is part of Boxes.py's public generator API.  Keep labels and
    # reference marks out of the student's cutting geometry.
    box.parseArgs(
        [
            "--x",
            str(x),
            "--y",
            str(y),
            "--h",
            str(h),
            "--thickness",
            str(thickness),
            "--burn",
            str(burn),
            "--labels",
            "false",
            "--reference",
            "0",
            "--output",
            "box.svg",
        ]
    )
    box.open()
    try:
        box.render()
        result = box.close()
    finally:
        # close() already releases the SVG context.  This is only a guard for
        # generator failures where the context may still be open.
        if box.ctx is not None:
            box.close()
    if not isinstance(result, io.BytesIO):
        raise ValueError("Kassegeneratoren returnerede ikke en SVG.")
    source = result.getvalue()
    if len(source) > MAX_OUTPUT:
        raise ValueError("Kasse-SVG'en blev for stor.")
    width, height = _svg_dimensions(source)
    return source.decode("utf-8"), {
        "width": round(width, 2),
        "height": round(height, 2),
        "generator": "Boxes.py ABox",
    }
