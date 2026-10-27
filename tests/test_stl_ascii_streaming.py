import unittest
from unittest.mock import patch

from app.stl import inspect_stl
from scripts.smoke_slice import cube


def tetrahedron_stl():
    triangles = (
        ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
        ((0, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((0, 0, 0), (0, 0, 1), (1, 0, 0)),
        ((1, 0, 0), (0, 0, 1), (0, 1, 0)),
    )
    facets = []
    for points in triangles:
        facets.append('facet normal 0 0 0\n outer loop\n')
        facets.extend('  vertex %s %s %s\n' % point for point in points)
        facets.append(' endloop\nendfacet\n')
    return ('solid tetra\n' + ''.join(facets) + 'endsolid tetra\n').encode('ascii')


class AsciiStlStreamingTests(unittest.TestCase):
    def test_valid_ascii_stl_preserves_geometry_metadata_without_findall(self):
        with patch('app.stl.re.findall', side_effect=AssertionError('findall is not allowed')):
            result = inspect_stl(tetrahedron_stl())
        self.assertEqual(result['triangles'], 4)
        self.assertEqual(result['size'], [1.0, 1.0, 1.0])

    def test_invalid_ascii_stl_keeps_validation_errors(self):
        with self.assertRaisesRegex(ValueError, 'ufuldstændig'):
            inspect_stl(tetrahedron_stl().replace(b'endfacet', b'end__facet'))
        with self.assertRaisesRegex(ValueError, 'ugyldige koordinater'):
            inspect_stl(tetrahedron_stl().replace(b'vertex 0 0 0', b'vertex nope 0 0', 1))

    def test_binary_stl_regression_and_triangle_limit(self):
        self.assertEqual(inspect_stl(cube())['size'], [20, 20, 20])
        with patch('app.stl.MAX_TRIANGLES', 3):
            with self.assertRaisesRegex(ValueError, 'for mange trekanter'):
                inspect_stl(tetrahedron_stl())


if __name__ == '__main__':
    unittest.main()
