import base64
from io import BytesIO
import math
import tempfile
import unittest

from PIL import Image

from app.laser_scene import MaterialScene, PHOTO_SIZE, rectify_image


def photo_url():
    image = Image.new('RGB', (120, 80), 'white')
    for x in range(60):
        for y in range(40):
            image.putpixel((x, y), (255, 0, 0))
    for x in range(60, 120):
        for y in range(40):
            image.putpixel((x, y), (0, 255, 0))
    for x in range(60):
        for y in range(40, 80):
            image.putpixel((x, y), (0, 0, 255))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def image_data(**changes):
    result = {'image': photo_url(), 'corners': [[0, 0], [120, 0], [120, 80], [0, 80]],
              'heightMm': 3, 'confirmed': True}
    result.update(changes)
    return result


def region():
    return {'id': 'sheet', 'points': [[0, 0], [300, 0], [300, 200], [0, 200]], 'holes': []}


class ImageRectificationTests(unittest.TestCase):
    def test_identity_corners_preserve_quadrant_orientation_and_height(self):
        rectified, height, corners = rectify_image(image_data())
        self.assertEqual(rectified.size, PHOTO_SIZE)
        self.assertEqual(height, 3)
        self.assertEqual(corners, [(0, 0), (120, 0), (120, 80), (0, 80)])
        for pixel, expected in [((150, 100), (255, 0, 0)), ((1050, 100), (0, 255, 0)),
                                ((150, 650), (0, 0, 255)), ((1050, 650), (255, 255, 255))]:
            with self.subTest(pixel=pixel):
                self.assertEqual(rectified.getpixel(pixel), expected)

    def test_inset_corners_map_selected_rectangle_to_canvas(self):
        data = image_data(corners=[[10, 10], [110, 10], [110, 70], [10, 70]])
        rectified, _, _ = rectify_image(data)
        self.assertEqual(rectified.getpixel((150, 100)), (255, 0, 0))
        self.assertEqual(rectified.getpixel((1050, 100)), (0, 255, 0))
        self.assertEqual(rectified.getpixel((150, 650)), (0, 0, 255))

    def test_rejects_unconfirmed_bad_height_and_bad_image(self):
        for changes in ({'confirmed': False}, {'heightMm': -1}, {'heightMm': 101},
                        {'heightMm': math.nan}, {'heightMm': True}, {'image': 'data:image/png;base64,%%%%'},
                        {'image': 'data:image/png;base64,AAAAA'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                rectify_image(image_data(**changes))

    def test_rejects_reordered_crossing_tiny_and_outside_corners(self):
        invalid = [
            [[0, 0], [120, 80], [120, 0], [0, 80]],
            [[0, 0], [8, 0], [8, 5], [0, 5]],
            [[0, 0], [121, 0], [120, 80], [0, 80]],
            [[0, 0], [120, 0], [120, 80], [math.inf, 80]],
        ]
        for corners in invalid:
            with self.subTest(corners=corners), self.assertRaises(ValueError):
                rectify_image(image_data(corners=corners))


class SceneRevisionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.scene = MaterialScene(self.folder.name)
        self.first = self.scene.new_image(image_data())

    def approved_scene(self):
        edited = self.scene.update_regions({'sceneId': self.first['id'], 'revision': 1,
                                            'regions': [region()]})
        approved = self.scene.approve_regions({'sceneId': self.first['id'],
                                                'revision': edited['revision'], 'confirmed': True})
        return edited, approved

    def test_scene_image_and_snapshot_are_isolated(self):
        self.assertEqual((self.first['width'], self.first['height'],
                          self.first['imageWidth'], self.first['imageHeight']), (600, 375, 1200, 750))
        self.assertTrue(self.scene.image(self.first['id']).startswith(b'\x89PNG'))
        snapshot = self.scene.snapshot()
        snapshot['corners'][0] = (999, 999)
        self.assertEqual(self.scene.snapshot()['corners'][0][0], 0)
        self.assertEqual(self.scene.snapshot()['revision'], 1)
        self.assertFalse(self.scene.snapshot()['approved'])

    def test_stale_revision_and_missing_revision_cannot_edit_or_approve(self):
        edited, approved = self.approved_scene()
        self.assertEqual((edited['revision'], approved['revision']), (2, 3))
        for payload in ({'sceneId': self.first['id'], 'revision': 1, 'regions': [region()]},
                        {'sceneId': self.first['id'], 'regions': [region()]},
                        {'sceneId': self.first['id'], 'revision': True, 'regions': [region()]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.scene.update_regions(payload)
        with self.assertRaises(ValueError):
            self.scene.approve_regions({'sceneId': self.first['id'], 'revision': 2, 'confirmed': True})
        with self.assertRaises(ValueError):
            self.scene.require({'sceneId': self.first['id'], 'sceneRevision': 2}, approved=True)
        with self.assertRaises(ValueError):
            self.scene.require({'sceneId': self.first['id'], 'sceneRevision': None})
        with self.assertRaises(ValueError):
            self.scene.update_regions({'sceneId': self.first['id'], 'revision': None, 'regions': [region()]})

    def test_approval_is_bound_to_revision_and_source_fingerprint(self):
        _, approved = self.approved_scene()
        request = {'sceneId': approved['id'], 'sceneRevision': approved['revision'], 'confirmed': True}
        token = self.scene.approve_placements(request, 'source-v1')
        self.assertEqual(self.scene.validate_approval(dict(request, approvalToken=token), 'source-v1')['id'], approved['id'])
        with self.assertRaises(ValueError):
            self.scene.validate_approval(dict(request, approvalToken=token), 'source-v2')
        with self.assertRaises(ValueError):
            self.scene.validate_approval(dict(request, approvalToken='wrong'), 'source-v1')
        changed = self.scene.update_regions({'sceneId': approved['id'], 'revision': approved['revision'],
                                             'regions': [region()]})
        self.assertFalse(changed['approved'])
        with self.assertRaises(ValueError):
            self.scene.validate_approval(dict(request, approvalToken=token), 'source-v1')

    def test_new_placement_approval_replaces_prior_token(self):
        _, approved = self.approved_scene()
        request = {'sceneId': approved['id'], 'sceneRevision': approved['revision'], 'confirmed': True}
        first = self.scene.approve_placements(request, 'source-v1')
        second = self.scene.approve_placements(request, 'source-v1')
        self.assertNotEqual(first, second)
        with self.assertRaises(ValueError):
            self.scene.validate_approval(dict(request, approvalToken=first), 'source-v1')
        self.assertEqual(self.scene.validate_approval(dict(request, approvalToken=second), 'source-v1')['id'], approved['id'])

    def test_invalid_new_image_keeps_approved_scene_and_existing_image(self):
        _, approved = self.approved_scene()
        old_image = self.scene.image(approved['id'])
        with self.assertRaises(ValueError):
            self.scene.new_image(image_data(corners=[[0, 0], [120, 80], [120, 0], [0, 80]]))
        self.assertEqual(self.scene.snapshot(), approved)
        self.assertEqual(self.scene.image(approved['id']), old_image)

    def test_unhashable_approval_token_is_rejected_as_bad_input(self):
        _, approved = self.approved_scene()
        request = {'sceneId': approved['id'], 'sceneRevision': approved['revision'], 'approvalToken': {}}
        with self.assertRaises(ValueError):
            self.scene.validate_approval(request, 'source-v1')

    def test_new_image_and_clear_invalidate_prior_scene(self):
        _, approved = self.approved_scene()
        old_image_path = self.scene.folder / (approved['id'] + '.png')
        second = self.scene.new_image(image_data(heightMm=5))
        self.assertNotEqual(second['id'], approved['id'])
        self.assertFalse(old_image_path.exists())
        with self.assertRaises(ValueError):
            self.scene.image(approved['id'])
        self.scene.clear()
        self.assertIsNone(self.scene.snapshot())
        with self.assertRaises(ValueError):
            self.scene.require({'sceneId': second['id'], 'sceneRevision': second['revision']})


if __name__ == '__main__':
    unittest.main()
