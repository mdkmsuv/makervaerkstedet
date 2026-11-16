"""Lav Scratch-editorens inputlås til den eksisterende app-pakkekommando.

Versionen og alle bytes kommer fra denne app. Der bruges ingen tidligere
kernepakke, fastlåst historisk filliste eller installerede elevdata.
"""
import argparse
import hashlib
import json
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REQUIRED = {"index.html", "guistandalone.js", "source.zip", "LICENSE",
            "LICENSE-scratch-gui", "TRADEMARK", "TRADEMARK-scratch-gui",
            "CREDITS.txt", "EXTRA-LIBRARY-CREDITS.txt",
            "library-assets-manifest.json", "extra-library-manifest.json"}
ROOT_FILES = REQUIRED | {"standalone.html", "DEPENDENCY-AUDIT.md", "guistandalone.js.LICENSE.txt", "guistandalone.js.map"}


def make_lock(build_dir):
    build_dir = Path(build_dir)
    if build_dir.is_symlink() or not build_dir.is_dir():
        raise ValueError("Editorens output skal være en almindelig mappe")
    missing = sorted(name for name in REQUIRED if not (build_dir / name).is_file())
    if missing:
        raise ValueError("Editorbygget mangler: " + ", ".join(missing))
    descriptor = json.loads((APP / "app.json").read_text(encoding="utf-8"))
    entries = []
    for path in sorted(build_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError("Afviser link i editorbygget: " + str(path))
        if not path.is_file():
            continue
        relative = path.relative_to(build_dir)
        if relative.parts[0] == "creative":
            continue  # App-owned resources are supplied and checked by files.json.
        if relative.as_posix() == "installation.json":
            continue  # Private build metadata is a sidecar, never pupil program data.
        if relative.parts[0] not in ("static", "chunks") and relative.as_posix() not in ROOT_FILES:
            raise ValueError("Uventet fil i editorbygget: " + relative.as_posix())
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entries.append({"source": str(path.resolve()),
                        "path": "usr/local/share/elevhub/apps/scratch/" + relative.as_posix(),
                        "sha256": digest,
                        "mode": 0o644})
    return {"schema": "makervaerkstedet.debian-inputs.v2",
            "package": descriptor["package"], "version": descriptor["version"],
            "architecture": descriptor["architecture"],
            "depends": ["makervaerkstedet-elev (>= " + descriptor["min_core"] + ")"],
            "files": entries,
            "provenance": {"component": "scratch", "source": "app-owned editor build",
                           "producer": "apps/scratch/build/make_editor_lock.py"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    lock = make_lock(args.build_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(lock, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"package": lock["package"], "version": lock["version"], "files": len(lock["files"])}))
