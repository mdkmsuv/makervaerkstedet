"""Validation boundary for workflow bindings stored with printer settings."""

from copy import deepcopy
import re

_KINDS = frozenset(("manual", "3d_print", "laser"))
_ROLES = frozenset(("operator", "teacher"))
_ACTIONS = frozenset(("submit", "prepare", "approve", "produce", "collect", "clear"))
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", re.ASCII)
_ID = re.compile(r"^[a-f0-9]{32}$", re.ASCII)


def _text(value, field, maximum):
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Ugyldigt workflow-{field}.")
    if any(not char.isprintable() for char in value):
        raise ValueError(f"Ugyldigt workflow-{field}.")
    return value


def _definition(value):
    if not isinstance(value, dict) or set(value) != {"name", "kind", "steps"}:
        raise ValueError("Ugyldig workflowdefinition.")
    name, kind, steps = value["name"], value["kind"], value["steps"]
    _text(name, "navn", 80)
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("Workflowtypen er ugyldig.")
    if not isinstance(steps, list) or not 2 <= len(steps) <= 20:
        raise ValueError("Workflowet skal have 2-20 trin.")
    ids, actions, result = set(), [], []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"id", "label", "role", "action"}:
            raise ValueError("Ugyldigt workflowtrin.")
        identifier = step["id"]
        _text(identifier, "trin-id", 40)
        if not _SLUG.fullmatch(identifier) or identifier in ids:
            raise ValueError("Workflowtrin skal have unikke ASCII-id'er.")
        ids.add(identifier)
        label, role, action = step["label"], step["role"], step["action"]
        _text(label, "trinlabel", 100)
        if (not isinstance(role, str) or not isinstance(action, str)
                or role not in _ROLES or action not in _ACTIONS):
            raise ValueError("Workflowtrinet har en ugyldig rolle eller handling.")
        if action in {"approve", "produce", "clear"} and role != "teacher":
            raise ValueError(f"{action} kræver lærerrollen.")
        result.append({"id": identifier, "label": label, "role": role, "action": action})
        actions.append(action)
    for action in ("submit", "approve", "produce", "collect", "clear"):
        if actions.count(action) != 1:
            raise ValueError(f"Workflowet skal have præcis ét {action}-trin.")
    if actions[0] != "submit" or actions[-1] != "clear":
        raise ValueError("Workflowet skal starte med submit og slutte med clear.")
    if actions.index("approve") > actions.index("produce") or actions.index("collect") < actions.index("produce"):
        raise ValueError("Workflowets sikkerhedsrækkefølge er ugyldig.")
    for action in _ACTIONS - {"prepare"}:
        if actions.count(action) > 1:
            raise ValueError(f"{action} må kun forekomme én gang.")
    return {"name": name, "kind": kind, "steps": result}


def validate_binding(value):
    """Validate and deep-copy a printer workflow binding; ``None`` unassigns."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"id", "revision", "definition"}:
        raise ValueError("Ugyldig workflowbinding.")
    if not isinstance(value["id"], str) or not _ID.fullmatch(value["id"]):
        raise ValueError("Ugyldigt workflow-id.")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ValueError("Workflowrevisionen skal være positiv.")
    definition = _definition(value["definition"])
    if definition["kind"] != "3d_print":
        raise ValueError("Printerworkflows skal være af typen 3d_print.")
    return {"id": value["id"], "revision": value["revision"], "definition": deepcopy(definition)}


def binding_summary(binding):
    if binding is None:
        return None
    checked = validate_binding(binding)
    return {key: checked[key] for key in ("id", "revision")} | {"name": checked["definition"]["name"]}
