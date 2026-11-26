"""3D publisher build/source gate; this app owns the compiler receipt contract."""
import hashlib
import json
import re
from pathlib import Path

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def text_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n",b"\n")).hexdigest()

MODEL3D_OUTPUTS = {"model3d-app.js", "model3d-app.js.LEGAL.txt", "model3d-geometry-worker.js", "model3d-manifold.wasm"}
MODEL3D_LICENSES = {"manifold-3d-LICENSE.txt", "three-LICENSE.txt", "opentype-LICENSE.txt", "Metrophobic-OFL.txt"}
FONT_SOURCE = "apps/3dprint/web/assets/model3d/"
FONT_MODULE = "apps/3dprint/web/model3d/fonts.js"

def validate_model3d_font_assets(web, receipt, *, legacy_licenses=False):
    """Verify all catalog assets in both source app packages and flat Windows payloads."""
    font_dir = web / "assets/model3d"
    license_dir = web / "assets/licenses/model3d"
    provenance_file = font_dir / "font-provenance.json"
    provenance = json.loads(provenance_file.read_text(encoding="utf-8")) if provenance_file.is_file() else {}
    text = receipt.get("text", {})
    catalog = provenance.get("catalog")
    modern = "fonts" in text or FONT_MODULE in receipt.get("source_sha256", {}) or catalog is not None
    if not modern and not legacy_licenses:
        return

    licenses = receipt.get("license_sha256", {})
    expected_licenses = MODEL3D_LICENSES.copy()
    if "apps/3dprint/web/model3d/three-mf.js" in receipt.get("source_sha256", {}):
        expected_licenses.add("fflate-LICENSE.txt")
    if modern:
        runtime = text.get("input_sha256", {})
        if not catalog or not catalog.get("fonts") or runtime.get(FONT_SOURCE + provenance_file.name) != sha(provenance_file):
            raise ValueError("model3d_font_receipt_incomplete")
        standard = {"file": "Metrophobic-Regular.ttf", "sha256": provenance["font_sha256"], "license": "Metrophobic-OFL.txt"}
        fonts = {"mkv-sans": standard}
        font_license_pins = {standard["license"]: provenance["license_sha256"]}
        for entry in catalog["fonts"]:
            key, license_name = entry["key"], entry["license_file"]
            filename = entry["source_path"].rsplit("/", 1)[-1]
            if (key in fonts or not re.fullmatch(r"[A-Za-z0-9-]+\.ttf", filename)
                    or license_name in font_license_pins or not re.fullmatch(r"[A-Za-z0-9-]+-OFL\.txt", license_name)):
                raise ValueError("model3d_font_receipt_incomplete")
            fonts[key] = {"file": filename, "sha256": entry["font_sha256"], "license": license_name}
            font_license_pins[license_name] = entry["license_sha256"]
        if text.get("fonts") != fonts:
            raise ValueError("model3d_font_receipt_incomplete")
        for record in fonts.values():
            filename, want = record["file"], record["sha256"]
            if runtime.get(FONT_SOURCE + filename) != want:
                raise ValueError("model3d_font_receipt_incomplete")
            source = font_dir / filename
            if not source.is_file() or sha(source) != want:
                raise ValueError("model3d_font_hash_mismatch:" + filename)
        expected_licenses.update(font_license_pins)
        if any(licenses.get(name) != want for name, want in font_license_pins.items()):
            raise ValueError("model3d_license_receipt_required")
    if set(licenses) != expected_licenses:
        raise ValueError("model3d_license_receipt_required")
    for name, want in licenses.items():
        source = license_dir / name
        if not source.is_file() or sha(source) != want:
            raise ValueError("model3d_license_hash_mismatch:" + name)

def validate_model3d_bundle(root, built_from=None):
    """The built 3D editor must be the build of these exact sources.

    The app vendor directory is not in Git. Without this check an editor change without a
    rebuild would ship the old bundle with every gate green. Outputs must match
    byte for byte. The manifest's source hashes must match the folder the
    bundle was built in (built_from, usually the working copy), and those
    sources must equal the packaged sources (root) apart from line endings.
    """
    root = Path(root)
    built_from = Path(built_from) if built_from is not None else root
    manifest = root / "apps/3dprint/web/vendor/model3d-vendor-manifest.json"
    if not manifest.is_file():
        raise ValueError("model3d_build_receipt_required")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    outputs = value.get("outputs", {})
    if value.get("schema") != 1 or set(outputs) != MODEL3D_OUTPUTS:
        raise ValueError("model3d_complete_bundle_required")
    for name, record in outputs.items():
        path = root / "apps/3dprint/web/vendor" / name
        if not path.is_file() or sha(path) != record.get("sha256") or path.stat().st_size != record.get("size"):
            raise ValueError("model3d_bundle_hash_mismatch:" + name)
    sources = value.get("source_sha256", {})
    local_js = {p.relative_to(root).as_posix() for p in (root / "apps/3dprint/web/model3d").rglob("*.js")}
    local_js.update({"sdk/web/stl-validation.mjs", "apps/3dprint/build.mjs",
                     "scripts/build_model3d_vendor.mjs", "scripts/repository-layout.mjs",
                     "REPOSITORY-LAYOUT.json", "apps/3dprint/web/model3d.html",
                     "apps/3dprint/web/model3d.css"})
    if not local_js <= set(sources):
        raise ValueError("model3d_source_receipt_incomplete")
    validate_model3d_font_assets(root / "apps/3dprint/web", value)
    runtime_inputs = value.get("text", {}).get("input_sha256", {})
    if "apps/3dprint/web/assets/model3d/Metrophobic-Regular.ttf" not in runtime_inputs:
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
