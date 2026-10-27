import unittest

from app.printer_config import ConfigConflict, public_printer, validate_printer


BASE = {
    "name": "Front", "location": "Lokale 1", "model": "P1S",
    "host": "192.168.1.40", "serial": "ABC123", "access_code": "secret",
    "enabled": False, "bed_type": "Textured PEI Plate",
}


class PrinterConfigTests(unittest.TestCase):
    def test_optional_update_omissions_preserve_operational_settings(self):
        old = validate_printer({**BASE, 'enabled': True, 'bed_type': 'High Temp Plate'})
        fields = {key: value for key, value in BASE.items() if key not in ('location', 'enabled', 'bed_type')}
        result = validate_printer({**fields, 'name': 'Renamed'}, old, [old])
        self.assertEqual((result['location'], result['enabled'], result['bed_type']),
                         ('Lokale 1', True, 'High Temp Plate'))
        explicit = validate_printer({**fields, 'location': '', 'enabled': False,
                                     'bed_type': 'Supertack Plate'}, old, [old])
        self.assertEqual((explicit['location'], explicit['enabled'], explicit['bed_type']),
                         ('', False, 'Supertack Plate'))

    def test_create_and_update_version_with_legacy_old_id(self):
        created = validate_printer(BASE, existing=[])
        self.assertEqual(created["version"], 1)
        old = {**created, "id": "a1mini1"}
        updated = validate_printer({**BASE, "name": "Back", "access_code": ""}, old=old,
                                   existing=[old])
        self.assertEqual((updated["id"], updated["version"], updated["access_code"]),
                         ("a1mini1", 2, "secret"))

    def test_public_whitelist_omits_secret_and_includes_location_version(self):
        row = validate_printer(BASE)
        public = public_printer(row)
        self.assertNotIn("access_code", public)
        self.assertEqual(public["location"], "Lokale 1")
        self.assertEqual(public["version"], 1)

    def test_strict_bool_and_invalid_types(self):
        for field, value in (("enabled", "false"), ("name", 3), ("serial", 3),
                             ("host", 3), ("model", None)):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    validate_printer({**BASE, field: value})

    def test_unknown_fields_and_model_rejected(self):
        with self.assertRaises(ValueError):
            validate_printer({**BASE, "version": 99})
        with self.assertRaises(ValueError):
            validate_printer({**BASE, "model": "X1 Carbon"})

    def test_id_rules(self):
        with self.assertRaises(ValueError):
            validate_printer({**BASE, "id": "legacy"})
        created = validate_printer({**BASE, "id": "a" * 32})
        self.assertEqual(created["id"], "a" * 32)
        with self.assertRaises(ConfigConflict):
            validate_printer({**BASE, "id": "a" * 32, "serial": "OTHER",
                              "host": "192.168.1.41"}, existing=[created])

    def test_secret_blank_only_preserves_on_update(self):
        with self.assertRaises(ValueError):
            validate_printer({**BASE, "access_code": ""})
        old = validate_printer(BASE)
        self.assertEqual(validate_printer({**BASE, "access_code": ""}, old=old,
                                          existing=[old])["access_code"], "secret")
        with self.assertRaises(ValueError):
            validate_printer({**BASE, "access_code": "has space"})

    def test_duplicates_exclude_old_but_reject_other_rows(self):
        old = validate_printer(BASE)
        other = {**BASE, "id": "b" * 32, "serial": "OTHER"}
        with self.assertRaises(ConfigConflict):
            validate_printer({**BASE, "name": "New"}, old=old, existing=[old, other])
        self.assertEqual(validate_printer({**BASE, "name": "New"}, old=old,
                                          existing=[old])["name"], "New")


if __name__ == "__main__":
    unittest.main()
