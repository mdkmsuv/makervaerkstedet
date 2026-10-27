import io
import json
import struct
import sys
import unittest
import zlib
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "projekter"))
from previews import MAX_STL_TRIANGLES, build_preview


def png(width=2, height=2, payload=None):
    # Each RGBA row needs one filter byte plus four bytes per pixel.  Keeping
    # real pixels here catches browser failures that a signature-only fixture
    # would hide.
    if payload is None:
        colours = ((255, 50, 50, 255), (40, 120, 220, 255))
        payload = b"".join(b"\x00" + bytes(colours[row % len(colours)]) * width for row in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(payload)) + chunk(b"IEND", b"")


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as value:
        for name, content in entries.items():
            value.writestr(name, content)
    return stream.getvalue()


class ProjectPreviews(unittest.TestCase):
    def test_svg_is_allowlisted_and_rejects_active_or_external_content(self):
        safe = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><g><rect width="5" height="5" fill="#1683c5"/></g></svg>'
        data, mime = build_preview({"filename": "shape.svg"}, safe)
        self.assertEqual(mime, "image/svg+xml")
        self.assertIn(b"rect", data)
        self.assertNotIn(b"script", data)
        self.assertIsNone(build_preview({"filename": "bad.svg"}, safe.replace(b"<g>", b"<script>bad</script><g>")))
        self.assertIsNone(build_preview({"filename": "bad.svg"}, safe.replace(b"<g>", b'<image href="https://example.invalid/x"/><g>')))

    def test_png_is_validated_and_bad_signature_is_rejected(self):
        image = png()
        self.assertEqual(build_preview({"filename": "scene.png"}, image), (image, "image/png"))
        self.assertIsNone(build_preview({"filename": "scene.png"}, image[:-1] + b"x"))
        self.assertIsNone(build_preview({"filename": "scene.png"}, b"\x89PNG" + b"x" * 30))

    def test_binary_and_ascii_stl_make_bounded_svg(self):
        def triangle(a, b, c):
            return struct.pack("<3f", 0, 0, 1) + struct.pack("<9f", *(a + b + c)) + b"\0\0"
        binary = b"fixture".ljust(80, b" ") + struct.pack("<I", 1) + triangle((0, 0, 0), (10, 0, 0), (0, 10, 0))
        result = build_preview({"filename": "model.stl"}, binary)
        self.assertEqual(result[1], "image/svg+xml")
        self.assertIn(b"polygon", result[0])
        ascii_stl = b"solid fixture\n facet normal 0 0 1\n  outer loop\n   vertex 0 0 0\n   vertex 10 0 0\n   vertex 0 10 0\n  endloop\n endfacet\nendsolid fixture\n"
        self.assertEqual(build_preview({"filename": "model.stl"}, ascii_stl)[1], "image/svg+xml")
        invalid = b"solid fixture\nvertex nan 0 0\nvertex 1 0 0\nvertex 0 1 0\nendsolid fixture"
        self.assertIsNone(build_preview({"filename": "model.stl"}, invalid))

    def test_stl_triangle_limit_does_not_allow_unbounded_preview(self):
        triangle = struct.pack("<3f", 0, 0, 1) + struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0) + b"\0\0"
        data = b"fixture".ljust(80, b" ") + struct.pack("<I", MAX_STL_TRIANGLES + 1) + triangle * (MAX_STL_TRIANGLES + 1)
        self.assertIsNone(build_preview({"filename": "too-large.stl"}, data))

    def test_cadoodle_zip_uses_existing_preview_png(self):
        image = png()
        data = archive({"model.doodle": b'{"projectName":"Test"}', "snapshot.png": image, "part.stl": b"ignored"})
        self.assertEqual(build_preview({"filename": "project.zip", "kind": "cadoodle"}, data), (image, "image/png"))

    def test_scratch_uses_referenced_costume_as_partial_scene_preview(self):
        costume = b'<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="orange"/></svg>'
        project = {"targets": [{"costumes": [{"assetId": "a" * 32, "dataFormat": "svg"}]}]}
        data = archive({"project.json": json.dumps(project), "" + "a" * 32 + ".svg": costume})
        result = build_preview({"filename": "game.sb3", "kind": "scratch"}, data)
        self.assertEqual(result[1], "image/svg+xml")
        self.assertIn(b"circle", result[0])

    def test_klosterskitse_is_rendered_only_for_small_valid_geometry(self):
        drawing = {"version": 1, "height": 3, "marks": [{"tool": "Rektangel", "cut": False, "width": 2, "points": [[10, 10], [90, 60]]}]}
        result = build_preview({"filename": "skitse.klosterskitse", "kind": "cadoodle"}, json.dumps(drawing).encode())
        self.assertEqual(result[1], "image/svg+xml")
        self.assertIn(b"rect", result[0])
        self.assertIsNone(build_preview({"filename": "skitse.klosterskitse"}, b'{"version":1,"height":3,"marks":[{"tool":"Nope","points":[]}] }'))

    def test_unsupported_and_malformed_project_content_have_no_preview(self):
        self.assertIsNone(build_preview({"filename": "notes.txt"}, b"hello"))
        self.assertIsNone(build_preview({"filename": "project.zip"}, b"not a zip"))
        self.assertIsNone(build_preview({"filename": "project.zip"}, archive({"../preview.png": png()})))

    def test_svg_and_scratch_limits_fail_closed(self):
        deep = b"<svg viewBox=\"0 0 1 1\">" + b"<g>" * 100 + b"<rect width=\"1\" height=\"1\"/>" + b"</g>" * 100 + b"</svg>"
        self.assertIsNone(build_preview({"filename": "deep.svg"}, deep))
        malformed = archive({"project.json": json.dumps({"targets": {"bad": True}})})
        self.assertIsNone(build_preview({"filename": "bad.sb3", "kind": "scratch"}, malformed))


if __name__ == "__main__":
    unittest.main()

