#!/usr/bin/env python3
"""Prepare explicit public-only Debian input locks; no package installation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from build_debian import sha

CORE_VERSION = "2.0.0~20261109.2"
UPDATER_VERSION = "1.0.0~20261109.4"

SPECS = {
    "updater": ("makervaerkstedet-updater", UPDATER_VERSION, "amd64", ["python3 (>= 3.13)", "python3-jinja2", "apt", "dpkg", f"makervaerkstedet-elev (>= {CORE_VERSION})"]),
    "core": ("makervaerkstedet-elev", CORE_VERSION, "all", ["python3 (>= 3.11)", "python3-pil", "python3-tk", "python3-xlib", "python3-affine", "python3-qrcode", "python3-shapely", "python3-typing-extensions", "chromium", "xserver-xorg-core", "xserver-xorg-input-libinput", "xinit", "x11-xserver-utils", "x11-xkb-utils", "x11-utils", "xdotool", "udiskie", "udisks2", "polkitd", "sudo", "makervaerkstedet-matchbox (= 1.2.2+git20200512+guard.20261104.1)"]),
    "scratch": ("makervaerkstedet-scratch", "15.1.1+local.20261104.1", "all", []),
    "matchbox": ("makervaerkstedet-matchbox", "1.2.2+git20200512+guard.20261105.1", "amd64", ["libc6", "libmatchbox1", "libx11-6", "libxext6", "libxfixes3", "libstartup-notification0", "libexpat1"]),
}
# Core pins the same custom binary package; an incompatible package cannot be
# silently selected by a release builder.
SPECS["core"] = (SPECS["core"][0], CORE_VERSION, SPECS["core"][2],
    [d if not d.startswith("makervaerkstedet-matchbox ") else
     "makervaerkstedet-matchbox (= " + SPECS["matchbox"][1] + ")" for d in SPECS["core"][3]])

def runtime_descriptor(directory,version):
    descriptor=directory/"runtime-release-student.json"
    data=json.dumps({"schema":1,"release_id":version,"role":"student"},sort_keys=True).encode()+b"\n"
    if descriptor.exists():
        if descriptor.read_bytes()!=data:raise ValueError("runtime_descriptor_exists")
    else:descriptor.write_bytes(data)
    return descriptor

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "fallback-root", "scratch", "matchbox", "legacy-manifest", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--session-template", type=Path)
    parser.add_argument("--updater-source", type=Path)
    parser.add_argument("--shared-verifier", type=Path)
    parser.add_argument("--minisign", type=Path)
    parser.add_argument("--minisign-license", type=Path)
    parser.add_argument("--minisign-provenance", type=Path)
    parser.add_argument("--public-trust-dir", type=Path)
    parser.add_argument("--components", nargs="+", choices=sorted(SPECS), default=list(SPECS))
    args = parser.parse_args()
    old = json.loads(args.legacy_manifest.read_text(encoding="utf-8"))["files"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for kind in args.components:
        package, version, arch, dependencies = SPECS[kind]
        files = {}
        def add(source, target, expected=None, mode=0o644):
            actual = sha(source)
            if expected is not None and actual != expected:
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
            expected = {k[len(prefix):]: v for k, v in old.items() if k.startswith(prefix)}
            actual = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
            if not expected or actual != set(expected):
                raise ValueError("historical_app_inventory_changed:" + kind)
            for rel, expected_hash in sorted(expected.items()):
                add(source / rel, "usr/local/share/elevhub/apps/" + kind + "/" + rel, expected_hash)
        elif kind == "updater":
            if any(v is None for v in (args.updater_source,args.shared_verifier,args.minisign,args.minisign_license,args.minisign_provenance,args.public_trust_dir)):
                raise ValueError("explicit_updater_inputs_required")
            add(args.shared_verifier,"usr/lib/makervaerkstedet/updater/simple_release.py","b03803d7bcf443ff7b0d54314df9b78030bf9ee9ed3f510106559b7aeb8258dc")
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
            add(root/"app/runtime_health.py","usr/lib/makervaerkstedet/runtime_health.py","7aee4e3bbb9fd6da8cf56894f6ef2ce2d44c9f20dc85e55f83b39a18b7c8d64c")
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
            for name in ("start.html.j2", "kiosk-session.j2", "elevhub-lokal.service.j2", "elevhub-design.service.j2", "elevhub-regler.service.j2", "elevhub-webproxy.service.j2"):
                source = args.session_template if name == "kiosk-session.j2" and args.session_template else kiosk / "templates" / name
                add(source, "usr/share/makervaerkstedet/templates/" + name)
            for name in ("CREDITS.md", "LICENSE-SCOPE.md", "LICENSE"):
                add(root / name, "usr/share/doc/" + package + "/" + name)
        spec = {"schema": "makervaerkstedet.debian-inputs.v1", "package": package, "version": version,
            "architecture": arch, "depends": dependencies, "files": [files[k] for k in sorted(files)],
            "provenance": {"historical_manifest_sha256": sha(args.legacy_manifest), "source": "reviewed integration MV20-UPDATE-20261104", "component": kind}}
        destination = args.output_dir / (package + ".json")
        if destination.exists():
            raise ValueError("input_lock_exists")
        destination.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"package": package, "files": len(files), "input_lock_sha256": sha(destination)}), flush=True)

if __name__ == "__main__":
    main()
