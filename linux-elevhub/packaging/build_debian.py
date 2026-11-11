#!/usr/bin/env python3
"""Build inert Debian packages from explicit SHA256-bound public input files.

No package manager, installer, service command or network call is invoked.
The private input lock/provenance receipt never enters the package.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile

EPOCH = 1793746800
PACKAGES = {"makervaerkstedet-elev", "makervaerkstedet-scratch",
            "makervaerkstedet-matchbox", "makervaerkstedet-updater"}

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def validate_files(entries):
    checked, targets = [], set()
    for entry in entries:
        if set(entry) != {"source", "path", "sha256", "mode"}:
            raise ValueError("invalid_file_descriptor")
        target = PurePosixPath(entry["path"])
        if (target.is_absolute() or not target.parts or ".." in target.parts
                or target.as_posix() != entry["path"] or target.parts[0] != "usr"
                or "\\" in entry["path"]):
            raise ValueError("package_path_outside_program_tree")
        if entry["path"].casefold() in targets:
            raise ValueError("duplicate_package_path")
        targets.add(entry["path"].casefold())
        if any("cadoodle" in part.lower() for part in target.parts) or target.name.lower() in {"native-3d.js", "java", "java.exe"} or target.suffix.lower() == ".jar":
            raise ValueError("retired_native_3d_input")
        if {"datatraener", "kredsloeb", "skolegpt"}.intersection(part.lower() for part in target.parts) or target.name.startswith(("workshop-data-", "workshop-circuit-")):
            raise ValueError("retired_workshop_input")
        source = Path(entry["source"])
        if any(parent.is_symlink() for parent in (source, *source.parents)) or not source.is_file():
            raise ValueError("input_must_be_regular_unlinked_file")
        if entry["mode"] not in (0o644, 0o755) or not re.fullmatch("[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("invalid_mode_or_hash")
        if sha(source) != entry["sha256"]:
            raise ValueError("input_hash_mismatch:" + entry["path"])
        checked.append((entry, source))
    if not checked:
        raise ValueError("empty_package")
    return checked

def build(spec, output):
    if set(spec) != {"schema", "package", "version", "architecture", "depends", "files", "provenance"} or spec["schema"] != "makervaerkstedet.debian-inputs.v1":
        raise ValueError("invalid_package_input")
    if spec["package"] not in PACKAGES or spec["architecture"] not in {"all", "amd64"}:
        raise ValueError("invalid_package_identity")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+:~\-]*", spec["version"]):
        raise ValueError("invalid_package_version")
    if not isinstance(spec["depends"], list) or any(not isinstance(v, str) or not re.fullmatch(r"[a-z0-9+., ()>=<|~:-]+", v) for v in spec["depends"]):
        raise ValueError("invalid_package_dependencies")
    checked = validate_files(spec["files"])
    output = Path(output)
    if output.exists() or output.with_suffix(".inputs.json").exists():
        raise ValueError("output_exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mkv-deb-") as temporary:
        root = Path(temporary) / "root"
        root.mkdir()
        for entry, source in checked:
            target = root / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if sha(target) != entry["sha256"]:
                raise ValueError("copied_input_hash_mismatch")
            target.chmod(entry["mode"])
        control = root / "DEBIAN"
        control.mkdir()
        content = (f"Package: {spec['package']}\nVersion: {spec['version']}\n"
            f"Architecture: {spec['architecture']}\nMaintainer: Makervaerkstedet <noreply@example.invalid>\n"
            "Section: education\nPriority: optional\n"
            + ("Depends: " + ", ".join(spec["depends"]) + "\n" if spec["depends"] else "")
            + "Description: Makervaerkstedet student program component\n Public program files only; pupil data and local identity are external.\n")
        (control / "control").write_text(content, encoding="utf-8")
        (control / "control").chmod(0o644)
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                path.chmod(0o755)
            os.utime(path, (EPOCH, EPOCH))
        root.chmod(0o755)
        os.utime(root, (EPOCH, EPOCH))
        pending = Path(temporary) / "package.deb"
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", "--uniform-compression", "-Zxz", "-z6", str(root), str(pending)],
            check=True, env={**os.environ, "SOURCE_DATE_EPOCH": str(EPOCH), "LC_ALL": "C"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        partial = output.with_suffix(".deb.part")
        if partial.exists():
            raise ValueError("output_partial_exists")
        created = False
        try:
            with pending.open("rb") as source_stream, partial.open("xb") as target_stream:
                created = True
                shutil.copyfileobj(source_stream, target_stream)
            os.replace(partial, output)
        except Exception:
            if created:
                partial.unlink(missing_ok=True)
            raise
    receipt = {"package": spec["package"], "version": spec["version"], "architecture": spec["architecture"],
        "bytes": output.stat().st_size, "sha256": sha(output), "input_lock_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "provenance": spec["provenance"], "files": [{k: v for k, v in entry.items() if k != "source"} for entry in spec["files"]]}
    output.with_suffix(".inputs.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = build(json.loads(args.input_lock.read_text(encoding="utf-8-sig")), args.output)
    print(json.dumps({k: receipt[k] for k in ("package", "version", "architecture", "bytes", "sha256")}))

if __name__ == "__main__":
    main()
