"""Shared test fixtures are importable both as modules and as a package."""

from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'system'), str(ROOT)]
