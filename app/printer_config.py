"""Pure validation for the shared Bambu printer configuration boundary."""

import ipaddress
import re
import uuid

from .profiles import MODELS
from .hardware_workflows import binding_summary, validate_binding


class ConfigConflict(ValueError):
    """The requested printer would collide with another saved configuration."""


_ALLOWED = frozenset((
    "id", "name", "location", "model", "host", "serial", "access_code",
    "enabled", "bed_type",
))
_BED_TYPES = frozenset(("Textured PEI Plate", "High Temp Plate", "Supertack Plate"))
_HEX_ID = re.compile(r"^[a-f0-9]{32}$")
_ASCII_SERIAL = re.compile(r"^[A-Za-z0-9]{1,30}$")


def _text(value, field, maximum, *, blank=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"Ugyldigt {field}.")
    if not blank and not value.strip():
        raise ValueError(f"Ugyldigt {field}.")
    if any(not char.isprintable() for char in value):
        raise ValueError(f"Ugyldigt {field}.")
    return value.strip()


def _secret(value, *, old=None):
    if value is None or value == "":
        if old is not None:
            return old
        raise ValueError("Udfyld printerens LAN-adgangskode.")
    if not isinstance(value, str) or len(value) > 100 or not value.strip():
        raise ValueError("Udfyld printerens LAN-adgangskode.")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Ugyldig printerens LAN-adgangskode.")
    return value.strip()


def _existing_rows(existing):
    if existing is None:
        return []
    if isinstance(existing, dict):
        return list(existing.values())
    if isinstance(existing, (list, tuple)):
        return list(existing)
    raise ValueError("Ugyldig eksisterende printerkonfiguration.")


def validate_printer(data, old=None, existing=None):
    """Validate and return one complete printer configuration.

    ``old`` is the existing row for an update, or ``None`` for creation;
    ``existing`` is the complete current collection used for duplicate checks.
    The function performs no I/O and never contacts a printer.
    """
    if not isinstance(data, dict) or not set(data) <= _ALLOWED:
        raise ValueError("Ukendte printerfelter.")
    if old is not None and not isinstance(old, dict):
        raise ValueError("Ugyldig eksisterende printer.")
    rows = _existing_rows(existing)
    old_id = old.get("id") if old else None
    if old is not None:
        if not isinstance(old_id, str) or not old_id:
            raise ValueError("Ugyldigt eksisterende printer-id.")
        printer_id = old_id
    elif "id" in data:
        printer_id = data["id"]
        if not isinstance(printer_id, str) or not _HEX_ID.fullmatch(printer_id):
            raise ValueError("Ugyldigt printer-id.")
    else:
        printer_id = uuid.uuid4().hex

    model = data.get("model")
    if not isinstance(model, str) or model not in MODELS:
        raise ValueError("Vælg A1 mini, P1S eller H2D.")
    host = data.get("host")
    if not isinstance(host, str):
        raise ValueError("Skriv printerens lokale IPv4-adresse.")
    try:
        address = ipaddress.ip_address(host)
        if (address.version != 4 or not address.is_private or address.is_loopback
                or address.is_multicast or address.is_unspecified):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Skriv printerens lokale IPv4-adresse.") from None
    name = _text(data.get("name"), "printernavn", 50)
    location = _text(data.get("location", old.get("location", "") if old else ""), "placering", 80, blank=True)
    serial = data.get("serial")
    if not isinstance(serial, str) or not _ASCII_SERIAL.fullmatch(serial):
        raise ValueError("Udfyld printerens serienummer.")
    enabled = data.get("enabled", old.get("enabled", False) if old else False)
    if type(enabled) is not bool:
        raise ValueError("enabled skal være true eller false.")
    bed_type = data.get("bed_type", old.get("bed_type", "Textured PEI Plate") if old else "Textured PEI Plate")
    if not isinstance(bed_type, str) or bed_type not in _BED_TYPES:
        raise ValueError("Vælg en understøttet pladetype.")
    access_code = _secret(data.get("access_code"), old=old.get("access_code") if old else None)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("id") == old_id:
            continue
        if old is None and row.get("id") == printer_id:
            raise ConfigConflict("Printer-id'et er allerede tilføjet.")
        if row.get("serial") == serial or row.get("host") == str(address):
            raise ConfigConflict("Printeren er allerede tilføjet.")
    old_version = old.get("version", 1) if old is not None else None
    if old is not None and (type(old_version) is not int or old_version < 1):
        raise ValueError("Ugyldig eksisterende printerversion.")
    return {
        "id": printer_id, "name": name, "location": location, "model": model,
        "host": str(address), "serial": serial, "access_code": access_code,
        "enabled": enabled, "bed_type": bed_type,
        "version": old_version + 1 if old is not None else 1,
        **({"workflow": validate_binding(old.get("workflow"))} if old is not None and "workflow" in old else {}),
    }


def public_printer(config):
    """Return the admin-safe printer representation without ``access_code``."""
    if not isinstance(config, dict):
        raise ValueError("Ugyldig printerkonfiguration.")
    result = {key: config[key] for key in (
        "id", "name", "location", "model", "host", "serial", "enabled",
        "bed_type", "version",
    ) if key in config}
    result.setdefault('version', 1)
    result.setdefault('location', '')
    result['workflow'] = binding_summary(config.get('workflow'))
    return result
