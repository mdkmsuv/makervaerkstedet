"""Validate actual A1 mini slicing without sending anything to a printer."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from app.slicer import Slicer
from app.stl import inspect_stl

parser = argparse.ArgumentParser()
parser.add_argument("fixtures", type=Path)
parser.add_argument("--work", type=Path, required=True)
args = parser.parse_args()
studio = Path(r"C:\Program Files\Bambu Studio\bambu-studio.exe")
slicer = Slicer(studio, studio.parent / "resources/profiles/BBL", args.work.resolve())
profile = slicer.profiles.find_filament("A1 mini", "GFA00")
results = []
for name in ("through-hole", "crossing-scribble", "erased-scribble"):
    path = args.fixtures / f"{name}.stl"
    metadata = inspect_stl(path.read_bytes())
    job = {"id": name, "quality": "standard", "profile": profile,
        "actual_colour": "#B4121A", "remote_name": f"{name}.gcode.3mf"}
    output = slicer.slice(job, {"model": "A1 mini"}, path)
    results.append({"fixture": name, "stl": metadata, "sliced": str(output), "bytes": output.stat().st_size})
    print(json.dumps(results[-1], ensure_ascii=False), flush=True)
(args.work / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
