import json
import unittest
import xml.etree.ElementTree as ET

from app.laser_server import NS, clean_svg


class EditableTextTests(unittest.TestCase):
    def source(self, text):
        root = ET.Element(f'{{{NS}}}svg', {'width':'80mm', 'height':'40mm',
            'viewBox':'0 0 80 40', 'data-laservaerksted':'1',
            'data-learning':'private learning notes'})
        group = ET.SubElement(root, f'{{{NS}}}g', {'data-operation':'engrave-fill'})
        ET.SubElement(group, f'{{{NS}}}path', {'d':'M10 10H20V20H10Z',
            'data-operation':'engrave-fill', 'data-text':json.dumps(text)})
        return ET.tostring(root, encoding='unicode')

    def parse(self, text):
        clean, _, _ = clean_svg(self.source(text))
        self.assertNotIn('private learning', clean)
        return ET.fromstring(clean).find(f'.//{{{NS}}}path')

    def test_rotation_and_mirror_survive_queue_sanitisation(self):
        value = {'value':'Åse', 'size':8, 'matrix':[0, -1, -1, 0], 'unknown':'remove'}
        item = self.parse(value)
        self.assertEqual(json.loads(item.get('data-text')), {k:value[k] for k in ('value','size','matrix')})
        self.assertEqual(item.get('d'), 'M10 10H20V20H10Z')

    def test_legacy_text_stays_editable(self):
        text = {'value':'Mit navn', 'size':12}
        self.assertEqual(json.loads(self.parse(text).get('data-text')), text)

    def test_invalid_matrices_drop_metadata_without_changing_the_outline(self):
        for matrix in ([0,0,0,0], [1,0,0], [float('nan'),0,0,1],
                       [float('inf'),0,0,1], [100001,0,0,1], ['1',0,0,1], None):
            with self.subTest(matrix=matrix):
                item = self.parse({'value':'Navn','size':10,'matrix':matrix})
                self.assertIsNone(item.get('data-text'))
                self.assertEqual(item.get('d'), 'M10 10H20V20H10Z')


if __name__ == '__main__':
    unittest.main()
