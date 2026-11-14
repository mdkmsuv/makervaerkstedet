"""3D publisher build/source gate; this app owns the compiler receipt contract."""
import hashlib
import json
from pathlib import Path

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def text_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n",b"\n")).hexdigest()

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
