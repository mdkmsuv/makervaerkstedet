import math
import unittest

from shapely.geometry import box

from app.laser_material import check_material, material_geometry, normalize_regions, place_on_material


def region(points, holes=None, region_id='material'):
    return {'id': region_id, 'points': points, 'holes': holes or [], 'source': 'manual'}


def rectangle(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class MaterialGeometryTests(unittest.TestCase):
    def test_normalizes_closed_rings_and_rejects_invalid_camera_output(self):
        closed = rectangle(0, 0, 100, 80)
        self.assertEqual(normalize_regions([region(closed + [closed[0]])])[0]['points'], closed)
        bad = [
            region([[0, 0], [100, 80], [0, 80], [100, 0]]),  # crossing
            region(rectangle(-1, 0, 20, 20)),
            region(rectangle(0, 0, 100, 80), [rectangle(80, 20, 110, 30)]),
            region(rectangle(0, 0, 100, 80), [rectangle(20, 20, 40, 40), rectangle(30, 30, 50, 50)]),
            region([[0, 0], [math.nan, 0], [0, 20]]),
            region(rectangle(0, 0, 100, 80) + [[0, 80]]),
        ]
        for candidate in bad:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                normalize_regions([candidate])

    def test_material_margin_clears_bed_edges_and_holes(self):
        regions = [region(rectangle(0, 0, 100, 80), [rectangle(40, 30, 60, 50)])]
        safe = material_geometry(regions, margin=3, bed=(100, 80))
        self.assertTrue(safe.covers(box(3, 3, 20, 20)))
        self.assertFalse(safe.covers(box(1, 3, 20, 20)))
        self.assertFalse(safe.covers(box(35, 35, 39, 40)))
        self.assertTrue(safe.covers(box(20, 33, 36, 40)))

    def test_overlapping_approved_regions_do_not_fill_a_cutout(self):
        regions = [region(rectangle(0, 0, 100, 80), [rectangle(40, 30, 60, 50)], 'first'),
                   region(rectangle(50, 0, 100, 80), region_id='second')]
        safe = material_geometry(regions, margin=0, bed=(100, 80))
        self.assertFalse(safe.covers(box(50, 35, 55, 40)))

    def test_placement_stays_inside_concavity_and_avoids_hole(self):
        # L-shaped sheet with an internal cutout in its broad arm.
        outline = [[0, 0], [140, 0], [140, 70], [60, 70], [60, 130], [0, 130]]
        regions = [region(outline, [rectangle(70, 20, 95, 45)])]
        items = [{'id': f'job-{i}', 'width': 20, 'height': 15} for i in range(12)]
        placements, unplaced = place_on_material(items, regions, bed=(140, 130), margin=3, gap=4)
        self.assertEqual(unplaced, [])
        self.assertEqual(check_material([dict(p, geometry={'width': 20, 'height': 15}) for p in placements],
                                        regions, bed=(140, 130), margin=3, gap=4), [])
        self.assertEqual(place_on_material(items, regions, bed=(140, 130), margin=3, gap=4)[0], placements)

    def test_rotation_allows_fit_and_no_fit_is_reported(self):
        regions = [region(rectangle(0, 0, 44, 24))]
        placements, unplaced = place_on_material([
            {'id': 'rotated', 'width': 15, 'height': 35},
            {'id': 'too-big', 'width': 100, 'height': 100},
        ], regions, margin=2, bed=(44, 24))
        self.assertEqual(unplaced, ['too-big'])
        self.assertEqual(placements[0]['rotation'], 90)

    def test_check_rejects_hole_outside_missing_approval_and_gap(self):
        regions = [region(rectangle(0, 0, 100, 80), [rectangle(40, 30, 60, 50)])]
        entries = [
            {'id': 'a', 'geometry': {'width': 10, 'height': 10}, 'x': 5, 'y': 5, 'rotation': 0},
            {'id': 'b', 'geometry': {'width': 10, 'height': 10}, 'x': 17, 'y': 5, 'rotation': 0},
            {'id': 'c', 'geometry': {'width': 10, 'height': 10}, 'x': 40, 'y': 30, 'rotation': 0},
            {'id': 'd', 'geometry': {'width': 10, 'height': 10}, 'x': 95, 'y': 20, 'rotation': 0},
        ]
        problems = check_material(entries, regions, bed=(100, 80))
        self.assertTrue(any('for tæt på a' in problem for problem in problems))
        self.assertTrue(any('c ligger uden for' in problem for problem in problems))
        self.assertTrue(any('d ligger uden for' in problem for problem in problems))
        self.assertTrue(check_material(entries[:1], [], bed=(100, 80)))

    def test_bad_dimensions_and_nonfinite_placement_are_not_accepted(self):
        regions = [region(rectangle(0, 0, 100, 80))]
        with self.assertRaises(ValueError):
            place_on_material([{'id': 'bad', 'width': 0, 'height': 10}], regions, bed=(100, 80))
        problems = check_material([{'id': 'bad', 'geometry': {'width': 10, 'height': 10},
                                    'x': math.inf, 'y': 0, 'rotation': 0}], regions, bed=(100, 80))
        self.assertTrue(any('ugyldig' in problem for problem in problems))


if __name__ == '__main__':
    unittest.main()
