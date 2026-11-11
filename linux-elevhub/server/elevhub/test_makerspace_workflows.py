"""Tests for the pure makerspace workflow contract."""
import copy
import unittest
from makerspace_workflows import templates, validate_definition

class MakerspaceWorkflowTest(unittest.TestCase):
    def test_templates_are_valid_and_isolated(self):
        first, second = templates(), templates()
        self.assertEqual(set(first), {"manual", "3d_print", "laser"})
        for definition in first.values():
            self.assertEqual(validate_definition(definition), definition)
        first["manual"]["steps"][0]["label"] = "ændret"
        self.assertEqual(second["manual"]["steps"][0]["label"], "Indsend opgave")

    def test_rejects_invalid_transitions_and_roles(self):
        definition = templates()["manual"]
        cases = []
        wrong_start = copy.deepcopy(definition)
        wrong_start["steps"][0]["action"] = "prepare"
        cases.append(wrong_start)
        wrong_order = copy.deepcopy(definition)
        wrong_order["steps"][2], wrong_order["steps"][3] = wrong_order["steps"][3], wrong_order["steps"][2]
        cases.append(wrong_order)
        wrong_role = copy.deepcopy(definition)
        wrong_role["steps"][3]["role"] = "operator"
        cases.append(wrong_role)
        duplicate_produce = copy.deepcopy(definition)
        duplicate_produce["steps"].insert(4, copy.deepcopy(duplicate_produce["steps"][3]))
        duplicate_produce["steps"][4]["id"] = "produce-again"
        cases.append(duplicate_produce)
        for case in cases:
            with self.assertRaises(ValueError):
                validate_definition(case)

    def test_rejects_hidden_fields_controls_and_code_values(self):
        definition = templates()["manual"]
        extra = copy.deepcopy(definition)
        extra["code"] = "__import__('os').system('x')"
        with self.assertRaises(ValueError):
            validate_definition(extra)
        control = copy.deepcopy(definition)
        control["steps"][0]["label"] = "<script>alert(1)</script>\n"
        with self.assertRaises(ValueError):
            validate_definition(control)
        plain = copy.deepcopy(definition)
        plain["steps"][0]["label"] = "<b>Elevens model</b>"
        self.assertEqual(validate_definition(plain)["steps"][0]["label"], "<b>Elevens model</b>")

    def test_rejects_missing_required_actions_before_transition_checks(self):
        definition = templates()["manual"]
        for required in ("submit", "approve", "produce", "collect", "clear"):
            candidate = copy.deepcopy(definition)
            step = next(item for item in candidate["steps"] if item["action"] == required)
            step["action"] = "prepare"
            with self.assertRaisesRegex(ValueError, "præcis ét"):
                validate_definition(candidate)

    def test_rejects_whitespace_and_unicode_nonprintable_text(self):
        definition = templates()["manual"]
        for field in ("name",):
            candidate = copy.deepcopy(definition)
            candidate[field] = "   "
            with self.assertRaises(ValueError):
                validate_definition(candidate)
        for value in ("\u0085", "\u200b", " \u00a0 "):
            candidate = copy.deepcopy(definition)
            candidate["steps"][0]["label"] = value
            with self.assertRaises(ValueError):
                validate_definition(candidate)

if __name__ == "__main__":
    unittest.main()

