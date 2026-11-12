#!/usr/bin/env python3
"""Prepare explicit public-only Debian input locks; no package installation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from build_debian import sha, DEBIAN_COMPONENTS

CORE_VERSION = "2.0.0~20261112.6"
UPDATER_VERSION = "1.0.0~20261112.5"
SCRATCH_RECEIPT_SHA256 = "e6526962d7114eb8547b527c41cbed348f1cc8fb8c4d44c635dfb8cb0aa00677"

SPECS = {
    "updater": (DEBIAN_COMPONENTS["updater"][0], UPDATER_VERSION, DEBIAN_COMPONENTS["updater"][1], ["python3 (>= 3.13)", "python3-jinja2", "apt", "dpkg", f"makervaerkstedet-elev (>= {CORE_VERSION})"]),
    "core": (DEBIAN_COMPONENTS["studentcore"][0], CORE_VERSION, DEBIAN_COMPONENTS["studentcore"][1], ["python3 (>= 3.11)", "python3-pil", "python3-tk", "python3-xlib", "python3-affine", "python3-qrcode", "python3-shapely", "python3-typing-extensions", "chromium", "xserver-xorg-core", "xserver-xorg-input-libinput", "xinit", "x11-xserver-utils", "x11-xkb-utils", "x11-utils", "xdotool", "udiskie", "udisks2", "polkitd", "sudo", "makervaerkstedet-matchbox (= 1.2.2+git20200512+guard.20261104.1)"]),
    "scratch": (DEBIAN_COMPONENTS["scratch"][0], "15.1.1+local.20261112.2", DEBIAN_COMPONENTS["scratch"][1], []),
    "matchbox": (DEBIAN_COMPONENTS["matchbox"][0], "1.2.2+git20200512+guard.20261105.1", DEBIAN_COMPONENTS["matchbox"][1], ["libc6", "libmatchbox1", "libx11-6", "libxext6", "libxfixes3", "libstartup-notification0", "libexpat1"]),
}
# Core pins the same custom binary package; an incompatible package cannot be
# silently selected by a release builder.
SPECS["core"] = (SPECS["core"][0], CORE_VERSION, SPECS["core"][2],
    [d if not d.startswith("makervaerkstedet-matchbox ") else
     "makervaerkstedet-matchbox (= " + SPECS["matchbox"][1] + ")" for d in SPECS["core"][3]])
# The patched executable uses Debian's installed default theme, keyboard
# bindings and icon. Keep launching the patched path, not the distro binary.
SPECS["core"][3].append("matchbox-window-manager (>= 1.2.2+git20200512-2)")

def text_sha(path):
    """Hash of a text file as committed in Git (LF), whatever the checkout uses."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()

MODEL3D_OUTPUTS = {"model3d-app.js", "model3d-app.js.LEGAL.txt", "model3d-geometry-worker.js", "model3d-manifold.wasm"}

def validate_model3d_bundle(root, built_from=None):
    """The built 3D editor must be the build of these exact sources.

    web/vendor is not in Git. Without this check an editor change without a
    rebuild would ship the old bundle with every gate green. Outputs must match
    byte for byte. The manifest's source hashes must match the folder the
    bundle was built in (built_from, usually the working copy), and those
    sources must equal the packaged sources (root) apart from line endings.
    """
    root = Path(root)
    built_from = Path(built_from) if built_from is not None else root
    manifest = root / "web/vendor/model3d-vendor-manifest.json"
    if not manifest.is_file():
        raise ValueError("model3d_build_receipt_required")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    outputs = value.get("outputs", {})
    if value.get("schema") != 1 or set(outputs) != MODEL3D_OUTPUTS:
        raise ValueError("model3d_complete_bundle_required")
    for name, record in outputs.items():
        path = root / "web/vendor" / name
        if not path.is_file() or sha(path) != record.get("sha256") or path.stat().st_size != record.get("size"):
            raise ValueError("model3d_bundle_hash_mismatch:" + name)
    sources = value.get("source_sha256", {})
    local_js = {p.relative_to(root).as_posix() for p in (root / "web/model3d").rglob("*.js")}
    local_js.add("web/stl-validation.mjs")
    if not local_js <= set(sources):
        raise ValueError("model3d_source_receipt_incomplete")
    runtime_inputs = value.get("text", {}).get("input_sha256", {})
    if "web/assets/model3d/Metrophobic-Regular.ttf" not in runtime_inputs:
        raise ValueError("model3d_runtime_input_receipt_incomplete")
    for name, want in runtime_inputs.items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("model3d_invalid_source_path")
        if name.startswith("node_modules/"):
            continue  # Dependency bytes are pinned by the builder, not shipped as source.
        source = root / name
        if not source.is_file() or sha(source) != want:
            raise ValueError("model3d_runtime_input_hash_mismatch:" + name)
    for name, want in sorted(sources.items()):
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("model3d_invalid_source_path")
        if name in ("package.json", "package-lock.json"):
            # The build script itself refuses dependency versions other than its
            # pinned manifold/three/opentype/esbuild, so unrelated npm changes
            # (other tools in package.json) must not block a package build.
            continue
        source, built = root / name, built_from / name
        if not source.is_file() or not built.is_file():
            raise ValueError("model3d_source_missing:" + name)
        if want not in (sha(built), text_sha(built)) or text_sha(built) != text_sha(source):
            raise ValueError("model3d_source_hash_mismatch:" + name + " (byg 3D-editoren igen: node scripts/build_model3d_vendor.mjs, og commit)")

def runtime_descriptor(directory,version):
    descriptor=directory/"runtime-release-student.json"
    data=json.dumps({"schema":1,"release_id":version,"role":"student"},sort_keys=True).encode()+b"\n"
    if descriptor.exists():
        if descriptor.read_bytes()!=data:raise ValueError("runtime_descriptor_exists")
    else:descriptor.write_bytes(data)
    return descriptor

# The app files are staged as build inputs and become package-owned files.
# Micro:bit serves its wrapper locally, but the MakeCode editor needs Internet.
APP_MANIFESTS = {
    "core": (
        ("laser", "Laser", "http://127.0.0.1:8795/", True, "laser-submit", False),
        ("folie", "Folie", "http://127.0.0.1:8796/", True, None, False),
        ("3dprint", "3D-v\u00e6rksted", "http://127.0.0.1:8798/model3d.html", True, "print-job", False),
        ("microbit", "micro:bit", "http://127.0.0.1:8797/", True, None, True),
    ),
    "scratch": (("scratch", "Scratch", "http://127.0.0.1:8792/", True, None, False),),
}


def app_descriptors(directory, kind, version):
    for app_id, name, start, offline, delivery, internet in APP_MANIFESTS.get(kind, ()):
        value = {"schema": 1, "id": app_id, "navn": name, "version": version,
                 "start": start, "offline": offline, "aflevering": delivery,
                 "kraever_internet": internet, "licens": "Se pakkens licensoplysninger"}
        path = directory / ("app-manifest-" + app_id + ".json")
        data = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("app_manifest_exists:" + app_id)
        else:
            path.write_bytes(data)
        yield path, "usr/share/makervaerkstedet/apps/" + app_id + "/app.json"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "fallback-root", "scratch", "matchbox", "legacy-manifest", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--scratch-receipt", type=Path)
    parser.add_argument("--session-template", type=Path)
    parser.add_argument("--updater-source", type=Path)
    parser.add_argument("--shared-verifier", type=Path)
    parser.add_argument("--minisign", type=Path)
    parser.add_argument("--minisign-license", type=Path)
    parser.add_argument("--minisign-provenance", type=Path)
    parser.add_argument("--public-trust-dir", type=Path)
    parser.add_argument("--vendor-built-from", type=Path,
                        help="Folder the git-ignored web/vendor bundle was built in (default: --source-root).")
    parser.add_argument("--components", nargs="+", choices=sorted(SPECS), default=list(SPECS))
    args = parser.parse_args()
    old = json.loads(args.legacy_manifest.read_text(encoding="utf-8"))["files"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for kind in args.components:
        package, version, arch, dependencies = SPECS[kind]
        files = {}
        def add(source, target, expected=None, mode=0o644, text=False):
            actual = sha(source)
            if expected is not None and (text_sha(source) if text else actual) != expected:
                raise ValueError("historical_input_hash_mismatch:" + target)
            if target in files:
                raise ValueError("duplicate_destination:" + target)
            files[target] = {"source": str(source), "path": target, "sha256": actual, "mode": mode}
        def tree(source, target):
            for p in sorted(source.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}:
                    add(p, target + "/" + p.relative_to(source).as_posix())
        if kind == "scratch":
            source = getattr(args, kind)
            prefix = "assets/" + kind + "/"
            expected = {k[len(prefix):]: (v, 0o644) for k, v in old.items() if k.startswith(prefix)}
            if args.scratch_receipt is not None:
                if sha(args.scratch_receipt) != SCRATCH_RECEIPT_SHA256:
                    raise ValueError("scratch_receipt_hash_mismatch")
                receipt = json.loads(args.scratch_receipt.read_text(encoding="utf-8"))
                if (receipt.get("package"), receipt.get("version"), receipt.get("architecture"), receipt.get("sha256")) != (
                        "makervaerkstedet-scratch", "15.1.1+local.20261104.1", "all",
                        "1ae6af12a62c0bb4111967473563f99a1fa97e2cf0c7a02711fa608679713c50"):
                    raise ValueError("invalid_scratch_receipt")
                target_prefix = "usr/local/share/elevhub/apps/scratch/"
                expected = {}
                for entry in receipt["files"]:
                    target = entry["path"]
                    if not target.startswith(target_prefix):
                        raise ValueError("invalid_scratch_receipt_path")
                    rel = target[len(target_prefix):]
                    if not rel or rel in expected or Path(rel).is_absolute() or ".." in Path(rel).parts or entry["mode"] not in (0o644, 0o755):
                        raise ValueError("invalid_scratch_receipt_path")
                    expected[rel] = (entry["sha256"], entry["mode"])
            actual = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
            if not expected or actual != set(expected):
                raise ValueError("historical_app_inventory_changed:" + kind)
            for rel, (expected_hash, mode) in sorted(expected.items()):
                add(source / rel, "usr/local/share/elevhub/apps/" + kind + "/" + rel, expected_hash, mode)
        elif kind == "updater":
            if any(v is None for v in (args.updater_source,args.shared_verifier,args.minisign,args.minisign_license,args.minisign_provenance,args.public_trust_dir)):
                raise ValueError("explicit_updater_inputs_required")
            add(args.shared_verifier,"usr/lib/makervaerkstedet/updater/simple_release.py","cb3b2c64bdb278e794f2e8c464531f88d4bf9eca3afd9996d70b8baf352a4a86",text=True)
            add(args.minisign,"usr/lib/makervaerkstedet/updater/minisign","2c74dffcc1c9a5ee55957c60971998ace2b89f22585631594ec2152c588af8db",0o755)
            add(args.minisign_license,"usr/share/doc/"+package+"/minisign-LICENSE","01aa50818d6609e85a9610b7d04da037e8b28bcf3bb5b5b4963c0c3f7abdabbd")
            add(args.minisign_provenance,"usr/share/doc/"+package+"/minisign-provenance.json")
            add(args.public_trust_dir/"minisign.pub","usr/share/makervaerkstedet/trust/minisign.pub","44de94f24b5a84e5dd35d81d7a90052e45a9950c51f93584ad79c4e4019f4aa5")
            add(args.public_trust_dir/"makervaerkstedet.gpg","usr/share/makervaerkstedet/trust/makervaerkstedet.gpg","5b9ee5f0ca01ff2d52c311e99f71b6eefd69ed8d9149ef7cef5d80cf89a27f1f")
            for name in ("apt_client.py","template_activation.py","runtime_checks.py","os_maintenance.py"):
                add(args.updater_source/"linux-elevhub/administration"/name,"usr/lib/makervaerkstedet/updater/"+name)
            add(args.updater_source/"linux-elevhub/packaging/apt_sandbox.py","usr/lib/makervaerkstedet/updater/apt_sandbox.py")
            add(args.updater_source/"linux-elevhub/installation/debian/bootstrap_existing.py","usr/lib/makervaerkstedet/updater/bootstrap_existing.py")
            add(args.updater_source/"linux-elevhub/administration/README-APT.md","usr/share/doc/"+package+"/README-APT.md")
            units=args.updater_source/"linux-elevhub/packaging/systemd"
            for name in ("makervaerkstedet-apt-update.service","makervaerkstedet-apt-update.timer","makervaerkstedet-firstboot.service"):
                add(units/name,"usr/lib/systemd/system/"+name)
            add(args.updater_source/"linux-elevhub/packaging/updater-pins.json","usr/share/doc/"+package+"/BUILD-PINS.json")
            add(args.source_root/"CREDITS.md","usr/share/doc/"+package+"/CREDITS.md")
        elif kind == "matchbox":
            add(args.matchbox, "usr/local/lib/elevhub/matchbox-window-manager", "1e12237a37a8e023dca7da641ba68f210c7922a277b4dcaa9b06a4d665802930", 0o755)
            for name in ("COPYING", "debian-copyright", "README.md", "matchbox-close-guard.patch", "build.sh"):
                add(args.source_root / "linux-elevhub/kiosk-window-guard" / name, "usr/share/doc/" + package + "/" + name)
        else:
            root = args.source_root
            add(root/"app/runtime_health.py","usr/lib/makervaerkstedet/runtime_health.py","54c6b4451edce5e451703b661693632c620b0ae284ee795a90161bef7e4b2fe4",text=True)
            validate_model3d_bundle(root, args.vendor_built_from)
            descriptor=runtime_descriptor(args.output_dir,version)
            add(descriptor,"usr/share/makervaerkstedet/runtime-release.json")
            tree(root / "linux-elevhub/projekter", "usr/local/lib/elevhub/projekter")
            tasks = (root / "linux-elevhub/ansible/roles/kiosk/tasks/main.yml").read_text(encoding="utf-8")
            section = tasks.split("    kiosk_lokal_design_filer:\n", 1)[1].split("  block:\n", 1)[0]
            paths = re.findall(r"^      - ([a-zA-Z0-9_./-]+)$", section, re.MULTILINE)
            if not paths:
                raise ValueError("missing_design_allowlist")
            for rel in paths:
                source = root / rel
                target = "usr/local/lib/elevhub/designvaerksted/" + rel
                if source.is_dir():
                    tree(source, target)
                elif source.is_file():
                    add(source, target)
                else:
                    add(args.fallback_root / rel, target, old[rel])
            kiosk = root / "linux-elevhub/ansible/roles/kiosk"
            for p in sorted((kiosk / "files").glob("*.py")):
                if not p.name.startswith("test_") and p.name != "cadoodle_vaerksted.py":
                    target = "usr/local/sbin/elevhub-power" if p.name == "elevhub-power.py" else "usr/local/lib/elevhub/" + p.name
                    add(p, target, mode=0o755 if p.name == "elevhub-power.py" else 0o644)
            tree(root / "linux-elevhub/server/microbit-controller", "usr/local/share/elevhub/apps/microbit")
            assets = root / "linux-elevhub/server/elevhub/web/assets"
            for p in sorted(assets.iterdir()):
                if p.is_file() and p.name != "native-3d.js" and not p.name.startswith(("workshop-data-", "workshop-circuit-")):
                    add(p, "usr/local/share/elevhub/assets/" + p.name)
            for name in ("start.html.j2", "kiosk-session.j2", "elevhub-lokal.service.j2", "elevhub-design.service.j2", "project-workshops.json.j2", "chromium-politik.json.j2", "elevhub-regler.service.j2", "elevhub-webproxy.service.j2"):
                source = args.session_template if name == "kiosk-session.j2" and args.session_template else kiosk / "templates" / name
                add(source, "usr/share/makervaerkstedet/templates/" + name)
            for name in ("CREDITS.md", "LICENSE-SCOPE.md", "LICENSE"):
                add(root / name, "usr/share/doc/" + package + "/" + name)
        for source, target in app_descriptors(args.output_dir, kind, version):
            add(source, target)
        spec = {"schema": "makervaerkstedet.debian-inputs.v1", "package": package, "version": version,
            "architecture": arch, "depends": dependencies, "files": [files[k] for k in sorted(files)],
            "provenance": {"historical_manifest_sha256": sha(args.legacy_manifest), "source": "reviewed integration MV20-UPDATE-20261104", "component": kind}}
        if kind == "scratch" and args.scratch_receipt is not None:
            spec["provenance"]["scratch_receipt_sha256"] = SCRATCH_RECEIPT_SHA256
        destination = args.output_dir / (package + ".json")
        if destination.exists():
            raise ValueError("input_lock_exists")
        destination.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"package": package, "files": len(files), "input_lock_sha256": sha(destination)}), flush=True)

if __name__ == "__main__":
    main()
