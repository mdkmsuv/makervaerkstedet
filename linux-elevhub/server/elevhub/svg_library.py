"""Standalone hub facade for the single canonical fixed-origin library."""
import importlib.util
from simple_updates import verifier_path

_spec = importlib.util.spec_from_file_location('_hub_svg_library', verifier_path().with_name('svg_library.py'))
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)
Library = _shared.Library
safe_svg = _shared.safe_svg
ORIGIN = _shared.ORIGIN
MAX_BYTES = _shared.MAX_BYTES
