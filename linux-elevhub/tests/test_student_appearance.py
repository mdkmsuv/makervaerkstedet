"""Public pupil identity is bounded; only an explicit private profile selects a logo."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from student_appearance import read_appearance

class StudentAppearanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'kiosk.json'
        self.config.write_text(json.dumps({'theme':'green','school_name':'Testskolen','brand':'#1f5a8c','brand_dark':'#143c5e','ui_token':'synthetic-private-ui','token':'synthetic-secret'}))

    def test_public_colours_never_select_a_school_logo_or_expose_credentials(self):
        result = read_appearance(self.config)
        self.assertEqual(result, {'theme':'green','school_name':'Testskolen','brand':'#1f5a8c','brand_dark':'#143c5e','logo_data':''})
        self.config.write_text('{}')
        self.assertEqual(read_appearance(self.config)['logo_data'], '')

    def profile(self):
        base = self.root / 'local-appearance'
        base.mkdir()
        image = b'\x89PNG\r\n\x1a\nsynthetic-test'
        (base/'logo.png').write_bytes(image)
        (base/'manifest.json').write_text(json.dumps({'schemaVersion':1,'theme':'red','school_name':'Eksempelskolen','brand':'#265675','brand_dark':'#15344b','style':{'logo':'logo.png','logo_sha256':hashlib.sha256(image).hexdigest()}}))
        return base,image

    def test_valid_private_profile_overrides_identity_and_supplies_verified_logo(self):
        _,image = self.profile()
        result = read_appearance(self.config)
        self.assertEqual(result['theme'], 'red')
        self.assertEqual(result['school_name'], 'Eksempelskolen')
        self.assertEqual(result['logo_data'], 'data:image/png;base64,'+base64.b64encode(image).decode())
        self.assertNotIn('style', result)

    def test_damaged_or_linked_private_logo_does_not_fall_back_to_public_branding(self):
        base,_ = self.profile()
        (base/'logo.png').write_bytes(b'changed')
        with self.assertRaises(ValueError):read_appearance(self.config)
        (base/'logo.png').unlink()
        (base/'logo.png').symlink_to(self.config)
        with self.assertRaises(ValueError):read_appearance(self.config)

    def test_missing_config_has_neutral_defaults_and_bad_values_are_bounded(self):
        self.config.unlink()
        self.assertEqual(read_appearance(self.config), {'theme':'red','school_name':'','brand':'','brand_dark':'','logo_data':''})
        self.config.write_text(json.dumps({'theme':'oops','school_name':'<test>','brand':'url(https://evil.test)','brand_dark':'#143c5e'}))
        value=read_appearance(self.config)
        self.assertEqual(value['theme'],'red')
        self.assertEqual(value['brand'],'')
        self.assertEqual(value['school_name'],'<test>')  # renderer must escape/assign textContent

if __name__=='__main__':unittest.main()
