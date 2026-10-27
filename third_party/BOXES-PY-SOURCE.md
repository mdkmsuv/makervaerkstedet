# Boxes.py source

The laser workshop's Superbruger box addition uses the official
[Boxes.py](https://github.com/florianfesti/boxes) source, pinned to commit
`7a6ccc48b3fdd3680fe90c6769f0ec5cf003e30e` (19 September 2026 in this
workspace). The vendored source is in `third_party/boxes.py` and is licensed
under **GNU GPL-3.0-or-later**; see `third_party/boxes.py/LICENSE.txt`.

The integration calls Boxes.py's public `boxes.generators.abox.ABox` generator
and does not reimplement finger-joint geometry. The server adapter supplies
the dimensions, material thickness and burn correction (half the measured
kerf width), then returns the generated SVG to the existing laser editor for
import and editing. Generation is transient and happens in memory; no server
project or generated SVG is written to the data directory.

The complete upstream project declares more optional generator and web UI
dependencies. This integration's `ABox` path needs `affine`, `qrcode`,
`shapely` and `typing_extensions` in addition to the existing Pillow dependency, so the root
`requirements.txt` keeps the install small.

The public product source omits upstream generated SVG examples and their
golden-file comparison test (`tests/test_svg.py`). `MANIFEST.in` omits the
corresponding examples entry. The generator code and license are unchanged;
Makervaerkstedet tests the integrated ABox output in
[`tests/test_boxes_generator.py`](../tests/test_boxes_generator.py).
