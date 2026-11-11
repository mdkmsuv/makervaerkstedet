"""Pure validation and built-in templates for makerspace workflows."""

from copy import deepcopy
import re

_KINDS = frozenset(("manual", "3d_print", "laser"))
_ROLES = frozenset(("operator", "teacher"))
_ACTIONS = frozenset(("submit", "prepare", "approve", "produce", "collect", "clear"))
_TOP_FIELDS = frozenset(("name", "kind", "steps"))
_STEP_FIELDS = frozenset(("id", "label", "role", "action"))
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", re.ASCII)
_ACTION_LABELS = {
    "submit": "Indsend", "prepare": "Forbered", "approve": "Godkend",
    "produce": "Producer", "collect": "Aflever eller afhent", "clear": "Ryd",
}


class WorkflowValidationError(ValueError):
    """A deliberate, safe explanation suitable for the teacher panel."""


_TEMPLATES = {
    "manual": {"name": "Manualt værkstedsforløb", "kind": "manual", "steps": [
        {"id": "submit", "label": "Indsend opgave", "role": "operator", "action": "submit"},
        {"id": "prepare", "label": "Forbered arbejdsplads", "role": "teacher", "action": "prepare"},
        {"id": "approve", "label": "Godkend opgaven", "role": "teacher", "action": "approve"},
        {"id": "produce", "label": "Gennemfør fysisk arbejde", "role": "teacher", "action": "produce"},
        {"id": "collect", "label": "Aflever eller afhent", "role": "operator", "action": "collect"},
        {"id": "clear", "label": "Ryd arbejdsplads", "role": "teacher", "action": "clear"},
    ]},
    "3d_print": {"name": "3D-printforløb", "kind": "3d_print", "steps": [
        {"id": "submit", "label": "Indsend model", "role": "operator", "action": "submit"},
        {"id": "prepare", "label": "Forbered print", "role": "teacher", "action": "prepare"},
        {"id": "approve", "label": "Godkend print", "role": "teacher", "action": "approve"},
        {"id": "produce", "label": "Bekræft fysisk produktion", "role": "teacher", "action": "produce"},
        {"id": "collect", "label": "Afhent print", "role": "operator", "action": "collect"},
        {"id": "clear", "label": "Ryd printområde", "role": "teacher", "action": "clear"},
    ]},
    "laser": {"name": "Laserforløb", "kind": "laser", "steps": [
        {"id": "submit", "label": "Indsend design", "role": "operator", "action": "submit"},
        {"id": "prepare", "label": "Forbered laserarbejde", "role": "teacher", "action": "prepare"},
        {"id": "approve", "label": "Godkend laserarbejde", "role": "teacher", "action": "approve"},
        {"id": "produce", "label": "Bekræft fysisk produktion", "role": "teacher", "action": "produce"},
        {"id": "collect", "label": "Afhent emne", "role": "operator", "action": "collect"},
        {"id": "clear", "label": "Ryd laserområde", "role": "teacher", "action": "clear"},
    ]},
}

def _text(value, field, maximum):
    if isinstance(value, bool) or not isinstance(value, str):
        raise WorkflowValidationError(f"{field} skal være tekst.")
    if not value or not value.strip() or len(value) > maximum:
        raise WorkflowValidationError(f"{field} skal være 1-{maximum} tegn.")
    if any(not char.isprintable() for char in value):
        raise WorkflowValidationError(f"{field} må ikke indeholde kontroltegn.")
    return value

def validate_definition(value):
    """Validate and return an independent workflow definition."""
    if isinstance(value, bool) or not isinstance(value, dict):
        raise WorkflowValidationError("Workflowet skal indeholde navn, type og trin.")
    if set(value) != _TOP_FIELDS:
        raise WorkflowValidationError("Workflowet skal indeholde navn, type og trin.")
    name = _text(value["name"], "Navnet", 80)
    kind = _text(value["kind"], "Workflowtypen", 20)
    if kind not in _KINDS:
        raise WorkflowValidationError("Vælg workflowtypen manuel station, 3D-print eller laser.")
    steps = value["steps"]
    if isinstance(steps, (str, bytes, bytearray)) or not isinstance(steps, list):
        raise WorkflowValidationError("Workflowets trin skal være en liste.")
    if not 2 <= len(steps) <= 20:
        raise WorkflowValidationError("Workflowet skal have 2-20 trin.")
    result_steps, ids, actions = [], set(), []
    for step in steps:
        if isinstance(step, bool) or not isinstance(step, dict) or set(step) != _STEP_FIELDS:
            raise WorkflowValidationError("Hvert trin skal have et id, et navn, en ansvarlig og en handling.")
        identifier = _text(step["id"], "Trinnets id", 40)
        if not _SLUG.fullmatch(identifier):
            raise WorkflowValidationError("Trinnets id må kun bruge små bogstaver a-z, tal og bindestreger mellem ordene.")
        if identifier in ids:
            raise WorkflowValidationError("To trin må ikke have samme id. Ret et af trinnenes id.")
        ids.add(identifier)
        label = _text(step["label"], "Trinnets navn", 100)
        role = _text(step["role"], "Den ansvarlige", 20)
        action = _text(step["action"], "Handlingen", 20)
        if role not in _ROLES:
            raise WorkflowValidationError("Vælg deltager eller lærer som ansvarlig for trinnet.")
        if action not in _ACTIONS:
            raise WorkflowValidationError("Vælg en af de understøttede handlinger for trinnet.")
        if action in ("approve", "produce", "clear") and role != "teacher":
            raise WorkflowValidationError(f"Trinnet med handlingen '{_ACTION_LABELS[action]}' skal have læreren som ansvarlig.")
        result_steps.append({"id": identifier, "label": label, "role": role, "action": action})
        actions.append(action)
    required = ("submit", "approve", "produce", "collect", "clear")
    for action in required:
        count = actions.count(action)
        if count != 1:
            raise WorkflowValidationError(f"Workflowet skal have præcis ét trin med handlingen '{_ACTION_LABELS[action]}'.")
    if actions[0] != "submit" or actions[-1] != "clear":
        raise WorkflowValidationError("Workflowet skal starte med 'Indsend' og slutte med 'Ryd'.")
    if actions.index("approve") > actions.index("produce"):
        raise WorkflowValidationError("'Godkend' skal komme før 'Producer'.")
    if actions.index("collect") < actions.index("produce"):
        raise WorkflowValidationError("'Aflever eller afhent' skal komme efter 'Producer'.")
    for action in _ACTIONS - {"prepare"}:
        if actions.count(action) > 1:
            raise WorkflowValidationError(f"Handlingen '{_ACTION_LABELS[action]}' må kun forekomme én gang.")
    return {"name": name, "kind": kind, "steps": result_steps}

def templates():
    """Return fresh copies of built-in workflow definitions."""
    return deepcopy(_TEMPLATES)

