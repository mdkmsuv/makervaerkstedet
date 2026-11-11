"""Read-only normalisation of existing printer and laser observations.

The functions in this module do not register machines, contact drivers, or
change queues.  They turn the existing local snapshots into the small common
machine shape used by a future makerspace overview.

Input schemas
-------------
``printer_machines(snapshot)`` accepts the result of
``system_overview.PrinterSnapshot.snapshot()``::

    {"status": "ok"|..., "checked_at": number-or-ISO-string,
     "items": [{"id": str, "name": str, "model": str,
                "status": str, "online": bool, "enabled": bool}, ...]}

An observation whose collection status is not ``"ok"`` produces ``[]``;
the caller should keep the collection status on its overview root.

``laser_machine(source_id, state, observed_at)`` accepts the actual
``GET /api/station/state`` body.  The identifying fields are ``bed.model``
and ``bed.machine``; connection fields are ``loggedIn`` and ``flux``, and
``directTransfer.available`` is the configured/enabled read-only flag.  All
other station fields (including ``server``, ``lastPlate``, scene/material
data, jobs, and pupil names) are deliberately ignored.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any


_BAMBU_MODELS = frozenset(("A1 mini", "P1S", "H2D"))
_LASER_MODELS = frozenset(("fbb2", "fhexa1"))
_STATUSES = frozenset((
    "unknown", "offline", "disabled", "attention", "printing",
    "preparing", "ready", "bed",
))


def _timestamp(value: Any) -> Any:
    """Return a safe timestamp value, or ``None`` for malformed values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str) and value and len(value) <= 64:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return value
    return None


def _text(value: Any, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable() and ch not in "<>")[:limit]


def printer_machines(snapshot: Any) -> list[dict[str, Any]]:
    """Normalise a valid Bambu snapshot into read-only common machine rows.

    Unknown collection status, malformed timestamps, and malformed rows are
    represented as an empty collection or unknown/offline rows respectively;
    no row can become ``ready`` without a valid online observation.
    """
    if not isinstance(snapshot, dict) or snapshot.get("status") != "ok":
        return []
    checked_at = _timestamp(snapshot.get("checked_at"))
    items = snapshot.get("items")
    if not isinstance(items, list):
        return []
    rows = []
    for item in items:
        if not isinstance(item, dict) or not _text(item.get("id")):
            continue
        model = _text(item.get("model"))
        raw_status = item.get("status")
        status = raw_status if isinstance(raw_status, str) and raw_status in _STATUSES else "unknown"
        valid_observation = checked_at is not None
        online = item.get("online") is True and valid_observation and status not in ("unknown", "offline")
        enabled = item.get("enabled") is True
        if not valid_observation:
            status, online = "unknown", False
        elif status == "ready" and not online:
            status = "offline"
        rows.append({
            "id": "bambu:" + _text(item.get("id")),
            "kind": "3d_print", "adapter": "bambu",
            "source_id": _text(item.get("id")),
            "name": _text(item.get("name")), "model": model,
            "status": status, "observed_at": checked_at,
            "online": online, "enabled": enabled,
            "supported": model in _BAMBU_MODELS,
            "capabilities": {"read": ["status", "model", "connection"]},
        })
    return rows


def laser_machine(source_id: Any, state: Any, observed_at: Any) -> dict[str, Any] | None:
    """Normalise one FLUX station state without exposing private payloads.

    A malformed state or timestamp still returns a machine row when its source
    identifier is usable, but it is always ``unknown`` and offline.  Only
    ``fbb2`` and ``fhexa1`` are marked supported.
    """
    source = _text(source_id)
    if not source:
        return None
    timestamp = _timestamp(observed_at)
    valid = (isinstance(state, dict) and isinstance(state.get("bed"), dict)
             and isinstance(state.get("loggedIn"), bool)
             and isinstance(state.get("flux"), bool)
             and isinstance(state.get("directTransfer"), dict)
             and isinstance(state["directTransfer"].get("available"), bool))
    bed = state.get("bed") if valid else {}
    model = _text(bed.get("model"))
    name = _text(bed.get("machine"))
    valid = valid and bool(model) and bool(name)
    supported = model in _LASER_MODELS
    transfer = state.get("directTransfer") if valid else None
    transfer_ok = isinstance(transfer, dict) and transfer.get("available") is True
    # These are software/service indicators, not physical laser telemetry.
    # A separate physical probe is required before online/readiness can be set.
    service_available = bool(valid and timestamp is not None and state.get("loggedIn") is True)
    connection_ready = bool(service_available and state.get("flux") is True and transfer_ok)
    online = False
    enabled = bool(valid and transfer_ok)
    status = "unknown"
    return {
        "id": "flux:" + source, "kind": "laser", "adapter": "flux",
        "source_id": source, "name": name, "model": model,
        "status": status, "observed_at": timestamp, "online": online,
        "enabled": enabled, "supported": supported,
        "service_available": service_available,
        "connection_ready": connection_ready,
        "capabilities": {"read": ["status", "model", "connection"]},
    }
