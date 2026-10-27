"""Resolve installed Bambu system presets, including inherited G-code templates."""
import copy
import json
from pathlib import Path

MODELS = {
    "A1 mini": {"machine": "Bambu Lab A1 mini 0.4 nozzle", "process_suffix": "A1M", "size": [175, 175, 175]},
    "P1S": {"machine": "Bambu Lab P1S 0.4 nozzle", "process_suffix": "X1C", "size": [250, 250, 250]},
    "H2D": {"machine": "Bambu Lab H2D 0.4 nozzle", "process_suffix": "H2D", "size": [300, 310, 320]},
}
QUALITIES = {
    "fast": {"name": "Hurtig", "layer": "0.28", "infill": "10%", "preset": "0.28mm Extra Draft"},
    "standard": {"name": "Standard", "layer": "0.2", "infill": "15%", "preset": "0.20mm Standard"},
    "beautiful": {"name": "Smuk", "layer": "0.12", "infill": "15%", "preset": "0.12mm Fine"},
}


class Profiles:
    def __init__(self, root):
        self.root = Path(root)
        self.items = {}
        self.cache = {}
        for category in ("machine", "process", "filament"):
            for path in (self.root / category).glob("*.json"):
                doc = json.loads(path.read_text(encoding="utf-8-sig"))
                self.items[doc.get("name", path.stem)] = doc

    def resolve(self, name, chain=()):
        if name in self.cache:
            return copy.deepcopy(self.cache[name])
        if name in chain or name not in self.items:
            raise ValueError(f"Bambu-profilen mangler eller kan ikke læses: {name}")
        item = self.items[name]
        value = self.resolve(item["inherits"], (*chain, name)) if item.get("inherits") else {}
        for included in item.get("include", []):
            value.update(self.resolve(included, (*chain, name)))
        value.update({k: v for k, v in item.items() if k not in {"inherits", "include"}})
        self.cache[name] = value
        return copy.deepcopy(value)

    def find_filament(self, model, filament_id):
        # This school uses only Bambu Basic/Matte. A1 external spools commonly
        # report Generic PLA (GFL99); the shared PLA default uses Basic's lower
        # A1 mini volumetric limit (21 vs Matte's 22), at the same 220 C.
        if model == 'A1 mini' and filament_id == 'GFL99':
            filament_id = 'GFA00'
        machine = MODELS[model]["machine"]
        for name, raw in self.items.items():
            if raw.get("type") != "filament" or raw.get("instantiation") != "true" or not name.startswith(("Bambu PLA Basic @", "Bambu PLA Matte @")):
                continue
            item = self.resolve(name)
            if (item.get("filament_id") == filament_id and machine in item.get("compatible_printers", []) and item.get("filament_type", [None])[0] == "PLA"):
                # Modified/foaming PLA needs additional choices; keep the pupil presets predictable.
                if "Aero" not in name:
                    return name
        return None

    def export(self, config, quality, filament_name, colour, target):
        model = MODELS[config["model"]]
        q = QUALITIES[quality]
        suffix = model["process_suffix"]
        process_name = f"{q['preset']} @BBL {suffix}"
        if process_name not in self.items:
            process_name = f"0.20mm Standard @BBL {suffix}"
        machine = self.resolve(model["machine"])
        if config['model'] == 'H2D' and [str(v) for v in machine.get('physical_extruder_map', [])] != ['1', '0']:
            raise ValueError('H2D-profilens dyseforbindelser har ændret sig. En voksen skal kontrollere Bambu-profilen.')
        process = self.resolve(process_name)
        filament = self.resolve(filament_name)
        if filament.get("filament_type", [None])[0] != "PLA" or model["machine"] not in filament.get("compatible_printers", []):
            raise ValueError("Filamentprofilen er ikke PLA til denne printer.")
        # CLI needs full JSON presets. Do not substitute a profile from another model.
        machine["printer_settings_id"] = model["machine"]
        machine["curr_bed_type"] = config.get("bed_type", "Textured PEI Plate")
        process.update(layer_height=q["layer"], print_settings_id=f"Printværksted {q['name']}", enable_support="1", support_type="tree(auto)", support_on_build_plate_only="0", sparse_infill_density=q["infill"])
        filament["filament_colour"] = [colour]
        filament["filament_settings_id"] = [filament_name]
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        paths = []
        for filename, value in (("machine.json", machine), ("process.json", process), ("filament.json", filament)):
            value["from"] = "system"
            value["instantiation"] = "true"
            path = target / filename
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            paths.append(path)
        return paths
