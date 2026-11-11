import json
from pathlib import Path
import subprocess
import threading
import time
import zipfile
import xml.etree.ElementTree as ET

from .profiles import Profiles


class SliceCancelled(Exception):
    """The service stopped before a prepared file could be transferred."""


class Slicer:
    def __init__(self, executable, profiles_root, work):
        self.executable = Path(executable)
        self.profiles = Profiles(profiles_root)
        self.work = Path(work)
        self.stopping = threading.Event()

    def cancel(self):
        self.stopping.set()

    def _run(self, command, target, log, timeout=600):
        if self.stopping.is_set():
            raise SliceCancelled('Lagskæring afbrudt ved lukning.')
        deadline = time.monotonic() + timeout
        with subprocess.Popen(command, cwd=target, stdout=log, stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            try:
                while True:
                    if self.stopping.is_set():
                        raise SliceCancelled('Lagskæring afbrudt ved lukning.')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        return process.wait(timeout=min(0.2, remaining))
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

    def slice(self, job, config, input_path):
        if config['model'] == 'H2D' and config.get('extruder') not in (0, 1):
            raise ValueError('AMS-forbindelsen til dysen er ikke kendt. Vent på nye printerdata.')
        target = self.work / job["id"]
        machine, process, filament = self.profiles.export(config, job["quality"], job["profile"], job["actual_colour"], target)
        output = target / job["remote_name"]
        command = [str(self.executable), "--debug", "2", "--load-settings", f"{machine};{process}", "--load-filaments", str(filament), "--curr-bed-type", config.get("bed_type", "Textured PEI Plate"), "--arrange", "1", "--orient", "0", "--ensure-on-bed", "--slice", "0", "--outputdir", str(target), "--export-3mf", output.name, str(Path(input_path).resolve())]
        if config['model'] == 'H2D':
            command[1:1] = ['--filament-map-mode', 'Manual', '--filament-map', str(config['extruder'] + 1), '--filament-volume-map', '0', '--nozzle-volume-type', 'Standard,Standard']
        with (target / "slicer.log").open("wb") as log:
            returncode = self._run(command, target, log)
        if returncode != 0 or not output.is_file():
            raise ValueError("Modellen kunne ikke klargøres. Bed en voksen kontrollere modellen og printprofilen.")
        self.complete_metadata(output, config)
        with zipfile.ZipFile(output) as archive:
            gcodes = [name for name in archive.namelist() if name.startswith("Metadata/plate_") and name.endswith(".gcode")]
            if len(gcodes) != 1 or archive.getinfo(gcodes[0]).file_size < 100:
                raise ValueError("Slicing gav ikke præcis én printplade med G-code.")
            for name in archive.namelist():
                if name.endswith((".config", ".model")) and name != "Metadata/project_settings.config":
                    try:
                        ET.fromstring(archive.read(name))
                    except ET.ParseError:
                        raise ValueError("Bambu Studio dannede en ugyldig printfil. Opdatér Bambu Studio.") from None
            settings = json.loads(archive.read("Metadata/project_settings.config"))
            expected = config["model"]
            if settings.get("printer_model") != f"Bambu Lab {expected}":
                raise ValueError("Den færdige fil passer ikke til printermodellen.")
            if settings.get("filament_type") != ["PLA"]:
                raise ValueError("Den færdige fil indeholder et andet materiale end PLA.")
            if settings.get('curr_bed_type') != config.get('bed_type', 'Textured PEI Plate'):
                raise ValueError('Printfilen bruger ikke den valgte printplade.')
            if any(float(value) != 0.4 for value in settings.get('nozzle_diameter', [])) or not settings.get('nozzle_diameter'):
                raise ValueError('Printfilen bruger ikke en 0,4 mm dyse.')
            info = ET.fromstring(archive.read('Metadata/slice_info.config'))
            outside = info.find(".//plate/metadata[@key='outside']")
            if outside is None or outside.get('value') != 'false':
                raise ValueError('Modellen eller dens støtte går uden for printområdet.')
            if config['model'] == 'H2D':
                mapping = info.find(".//plate/metadata[@key='filament_maps']")
                if mapping is None or mapping.get('value') != str(config['extruder'] + 1):
                    raise ValueError('H2D-filen bruger ikke den valgte dyse. Printet er stoppet før afsendelse.')
        return output

    def complete_metadata(self, output, config):
        # Windows Studio 2.5 CLI looks in a missing machine_full directory for this
        # model identifier. Fill only the absent ID from the installed model record;
        # never edit the motion commands or override an existing different model ID.
        expected = self.profiles.items[f"Bambu Lab {config['model']}"]['model_id']
        with zipfile.ZipFile(output) as archive:
            info = ET.fromstring(archive.read('Metadata/slice_info.config'))
            plates = info.findall('plate')
            if len(plates) != 1:
                raise ValueError('Printfilen skal indeholde præcis én printplade.')
            field = plates[0].find("metadata[@key='printer_model_id']")
            if field is None:
                field = ET.SubElement(plates[0], 'metadata', {'key': 'printer_model_id'})
            if field.get('value') and field.get('value') != expected:
                raise ValueError('Printfilens printer-ID passer ikke til den valgte printer.')
            if field.get('value') == expected:
                return
            field.set('value', expected)
            rewritten = output.with_suffix('.tmp')
            with zipfile.ZipFile(rewritten, 'w', zipfile.ZIP_DEFLATED) as target:
                for item in archive.infolist():
                    target.writestr(item, ET.tostring(info, encoding='utf-8', xml_declaration=True) if item.filename == 'Metadata/slice_info.config' else archive.read(item.filename))
        rewritten.replace(output)
