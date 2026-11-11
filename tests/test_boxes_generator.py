import xml.etree.ElementTree as ET
import unittest
import re

from app.boxes_generator import generate_box_svg


class BoxesGeneratorTests(unittest.TestCase):
    def test_boxes_py_generates_mm_svg_with_cut_paths(self):
        source, details = generate_box_svg(
            {"kind": "abox", "x": 80, "y": 60, "h": 40, "thickness": 3, "burn": 0.1, "level": 3}
        )
        root = ET.fromstring(source)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertTrue(root.get("width", "").endswith("mm"))
        self.assertTrue(root.get("height", "").endswith("mm"))
        self.assertTrue(1 <= details["width"] <= 600)
        self.assertTrue(1 <= details["height"] <= 600)
        self.assertGreater(len(root.findall(".//{http://www.w3.org/2000/svg}path")), 0)
        self.assertEqual(details["generator"], "Boxes.py ABox")
        viewbox = [float(value) for value in root.get("viewBox").split()]
        self.assertEqual(len(viewbox), 4)
        self.assertAlmostEqual(viewbox[2], details["width"], places=1)
        self.assertAlmostEqual(viewbox[3], details["height"], places=1)
        # Check that the physical path coordinates stay inside the generated
        # SVG viewport (the machine import must not rely on CSS scaling).
        coordinates = [
            float(value)
            for path in root.findall(".//{http://www.w3.org/2000/svg}path")
            for value in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", path.get("d", ""))
        ]
        self.assertTrue(coordinates)
        self.assertLessEqual(max(coordinates), max(details["width"], details["height"]) + 1)

    def test_boxes_py_rejects_out_of_bounds_values(self):
        for key, value in [("x", 301), ("y", 19), ("h", 301), ("thickness", 1), ("burn", 2)]:
            with self.subTest(key=key):
                data = {"kind": "abox", "x": 80, "y": 60, "h": 40, "thickness": 3, "burn": 0.1, "level": 3}
                data[key] = value
                with self.assertRaises(ValueError):
                    generate_box_svg(data)

    def test_boxes_py_rejects_output_larger_than_beambox_plate(self):
        with self.assertRaisesRegex(ValueError, "arbejdsområde"):
            generate_box_svg({"kind": "abox", "x": 300, "y": 300, "h": 300, "thickness": 3, "burn": 0.1, "level": 3})
