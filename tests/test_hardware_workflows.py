import copy
import unittest

from app.hardware_workflows import binding_summary, validate_binding
from app.printer_config import public_printer, validate_printer


def binding(kind="3d_print"):
    return {
        "id": "a" * 32, "revision": 3,
        "definition": {"name": "Printforløb", "kind": kind, "steps": [
            {"id": "submit", "label": "Indsend", "role": "operator", "action": "submit"},
            {"id": "approve", "label": "Godkend", "role": "teacher", "action": "approve"},
            {"id": "produce", "label": "Producer", "role": "teacher", "action": "produce"},
            {"id": "collect", "label": "Afhent", "role": "operator", "action": "collect"},
            {"id": "clear", "label": "Ryd", "role": "teacher", "action": "clear"},
        ]},
    }


class HardwareWorkflowTests(unittest.TestCase):
    def test_binding_is_deep_validated_and_summary_is_safe(self):
        value = validate_binding(binding())
        value["definition"]["steps"][0]["label"] = "changed"
        self.assertEqual(binding_summary(binding()), {"id": "a" * 32, "revision": 3, "name": "Printforløb"})
        self.assertIsNone(validate_binding(None))

    def test_requires_print_kind_and_security_steps(self):
        with self.assertRaises(ValueError):
            validate_binding(binding("manual"))
        invalid = copy.deepcopy(binding())
        invalid["definition"]["steps"][2]["role"] = "operator"
        with self.assertRaises(ValueError):
            validate_binding(invalid)

    def test_printer_validation_rejects_input_workflow_but_preserves_old(self):
        base = {"name": "Front", "location": "", "model": "P1S", "host": "192.168.1.40",
                "serial": "ABC123", "access_code": "secret", "enabled": False,
                "bed_type": "Textured PEI Plate"}
        old = {**validate_printer(base), "workflow": binding()}
        updated = validate_printer({**base, "access_code": ""}, old=old, existing=[old])
        self.assertEqual(updated["workflow"]["id"], "a" * 32)
        self.assertEqual(public_printer(updated)["workflow"]["name"], "Printforløb")
        with self.assertRaises(ValueError):
            validate_printer({**base, "workflow": binding()})


if __name__ == "__main__":
    unittest.main()
