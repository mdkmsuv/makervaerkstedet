"""Byg lokale Scratch-figurer af de fastlåste CC0-arkiver i denne app.

Ingen netværk, installation eller ændring af hovedsystemet. Køres før Scratchs
GUI-byg; output lægges i det samme lokale assetbibliotek som standardfigurerne.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

import PIL
from PIL import Image


LIBRARY = Path(__file__).resolve().parents[1] / "library"
ARCHIVES = LIBRARY.parent / "archive-cache"
MARKER = "mkv-extra-library"
KENNEY_ACTIONS = {
    "idle": "stå", "walk": "gå", "run": "løb", "jump": "hop",
    "climb": "klatr", "cheer": "jubel", "talk": "tal", "think": "tænk",
    "interact": "brug", "hold": "hold", "show": "vis", "side": "side",
    "back": "ryg", "behindBack": "hænder-bag-ryg", "wide": "bred",
    "duck": "duk", "down": "ned", "drag": "træk", "hang": "hæng",
    "rope": "reb", "fall": "fald", "fallDown": "fald-ned", "slide": "glid",
    "switch": "skift", "shove": "skub", "shoveBack": "skub-bagud",
    "attack": "slag", "attackKick": "spark-angreb", "kick": "spark",
    "hit": "ramt", "hurt": "av",
}
PIXEL_ACTIONS = {
    "Idle": "hvile", "Run": "løb", "Jump": "hop", "Fall": "fald",
    "Double Jump": "dobbelt-hop", "Wall Jump": "væg-hop", "Hit": "ramt",
}


def _costume(name, data, width, height, tags):
    asset_id = hashlib.md5(data).hexdigest()
    return {"name": name, "assetId": asset_id, "md5ext": asset_id + ".png",
            "dataFormat": "png", "bitmapResolution": 1,
            "rotationCenterX": width / 2, "rotationCenterY": height / 2,
            "tags": tags}


def _sprite(name, costumes, tags, size):
    return {"name": name, "tags": tags, "isStage": False, "variables": {},
            "lists": {}, "broadcasts": {}, "blocks": {}, "comments": {},
            "currentCostume": 0, "costumes": costumes, "sounds": [],
            "volume": 100, "layerOrder": 1, "visible": True, "x": 0, "y": 0,
            "size": size, "direction": 90, "draggable": False,
            "rotationStyle": "all around"}


def build_library(library_root=LIBRARY, archives_dir=ARCHIVES):
    library_root = Path(library_root)
    lock = json.loads((library_root / "sources.json").read_text(encoding="utf-8"))
    if PIL.__version__ != lock["pillow"]:
        raise ValueError("Brug Pillow " + lock["pillow"] + ": se requirements-library.txt")
    sprites, assets, sources = [], {}, []
    for pack in lock["packs"]:
        data = (Path(archives_dir) / pack["archive"]).read_bytes()
        if len(data) != pack["bytes"] or hashlib.sha256(data).hexdigest() != pack["sha256"]:
            raise ValueError("Arkivets størrelse/SHA256 matcher ikke: " + pack["archive"])
        sources.append({key: pack[key] for key in
                        ("id", "archive", "author", "license", "license_url", "source_url", "sha256", "bytes")})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [item.filename for item in archive.infolist() if not item.is_dir()]
            for folder, label in pack["characters"].items():
                tags = [MARKER, "animation", "ekstra", "fantasy",
                        "kenney" if pack["id"].startswith("kenney") else "pixel"]
                costumes = []
                if pack["id"].startswith("kenney"):
                    prefix = folder + "/PNG/Poses/"
                    poses = [name for name in names if name.startswith(prefix) and name.endswith(".png")]

                    def pose_key(name):
                        pose = PurePosixPath(name).stem.rsplit("_", 1)[1]
                        match = re.fullmatch(r"(.+?)(\d*)", pose)
                        action, number = match.groups()
                        return list(KENNEY_ACTIONS).index(action), int(number or 0)

                    for path in sorted(poses, key=pose_key):
                        pose = PurePosixPath(path).stem.rsplit("_", 1)[1]
                        action, number = re.fullmatch(r"(.+?)(\d*)", pose).groups()
                        name = KENNEY_ACTIONS[action] + (f"-{int(number) + 1:02}" if number else "")
                        png = archive.read(path)
                        with Image.open(io.BytesIO(png)) as image:
                            width, height = image.size
                        costume = _costume(name, png, width, height, tags)
                        assets[costume["md5ext"]] = png
                        costumes.append(costume)
                    if len(costumes) != 45:
                        raise ValueError("Forventer 45 positurer for " + folder)
                    size = 100
                else:
                    for action, translated in PIXEL_ACTIONS.items():
                        path = f"Free/Main Characters/{folder}/{action} (32x32).png"
                        with Image.open(io.BytesIO(archive.read(path))) as image:
                            if image.height != 32 or image.width % 32:
                                raise ValueError("Uventet animationsark: " + path)
                            for frame in range(image.width // 32):
                                target = image.crop((frame * 32, 0, (frame + 1) * 32, 32)).convert("RGBA")
                                png_buffer = io.BytesIO()
                                target.save(png_buffer, format="PNG", compress_level=9)
                                png = png_buffer.getvalue()
                                costume = _costume(f"{translated}-{frame + 1:02}", png, 32, 32, tags)
                                assets[costume["md5ext"]] = png
                                costumes.append(costume)
                    if len(costumes) != 43:
                        raise ValueError("Forventer 43 animationsbilleder for " + folder)
                    size = 300
                sprites.append(_sprite(label, costumes, tags, size))
    receipt = {"schema": 1, "sprite_count": len(sprites),
               "costume_count": sum(len(sprite["costumes"]) for sprite in sprites),
               "unique_assets": len(assets), "asset_bytes": sum(map(len, assets.values())),
               "sources": sources,
               "figures": [{"name": sprite["name"], "costumes": len(sprite["costumes"])} for sprite in sprites]}
    return sprites, assets, receipt


def install_library(gui_dir, output_dir, library_root=LIBRARY, archives_dir=ARCHIVES):
    gui_dir, output_dir = Path(gui_dir), Path(output_dir)
    paths = {kind: gui_dir / "src/lib/libraries" / (kind + ".json")
             for kind in ("sprites", "costumes")}
    existing = {kind: json.loads(path.read_text(encoding="utf-8-sig")) for kind, path in paths.items()}
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Outputmappen skal være ny eller tom: " + str(output_dir))
    sprites, assets, receipt = build_library(library_root, archives_dir)
    costumes = [dict(costume, name=costume["name"] + " · " + sprite["name"])
                for sprite in sprites for costume in sprite["costumes"]]
    asset_dir = output_dir / "static/library-assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(assets.items()):
        (asset_dir / name).write_bytes(data)
    (output_dir / "extra-library-manifest.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "EXTRA-LIBRARY-CREDITS.txt").write_bytes((Path(library_root) / "CC0-NOTICE.txt").read_bytes())
    for kind, extra in (("sprites", sprites), ("costumes", costumes)):
        original = [item for item in existing[kind] if MARKER not in item.get("tags", [])]
        paths[kind].write_text(json.dumps(original + extra, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--archives-dir", type=Path, default=ARCHIVES)
    args = parser.parse_args()
    receipt = install_library(args.gui_dir, args.output_dir, archives_dir=args.archives_dir)
    print(json.dumps(receipt, ensure_ascii=True))


if __name__ == "__main__":
    main()
