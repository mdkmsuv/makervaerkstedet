"""Build an immutable station-code update from current, explicit source inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.laser_station_install import APP_FILES, WEB_FILES


def build(destination, release="2026.10.20"):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    files = tuple(sorted(set(APP_FILES + WEB_FILES + (
        "web/assets/klostermarksskolen-logo.png", "CREDITS.md", "LICENSE", "LICENSE-SCOPE.md"))))
    manifest = {"format": "makervaerkstedet-station-code-v1", "release": release, "files": {}}
    for name in files:
        data = (ROOT / name).read_bytes()
        manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    archive = destination / "station-code.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name in files:
            data = (ROOT / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest["files"][name]["sha256"]:
                raise ValueError("Source changed during build")
            output.writestr(name, data)
        output.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for name in ("queue_update.py", "remote_runner.py"):
        shutil.copy2(Path(__file__).with_name(name), destination / name)
    receipt = {"release": release, "files": len(files), "archive_size": archive.stat().st_size,
               "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
               "runner_sha256": hashlib.sha256((destination / "remote_runner.py").read_bytes()).hexdigest(),
               "scope": "Current station code; preserve installed runtime, identity, configuration and plates"}
    (destination / "build.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output)))
