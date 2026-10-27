"""One installed identity and data location for boot tasks and desktop launchers."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def installation(root=ROOT):
    from .generation_runtime import selected_data_root, active_details
    selected = selected_data_root(root)
    settings_root = Path(active_details()['runtime_root']) if selected is not None else Path(root)
    path = settings_root / 'installation.json'
    value = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if selected is not None:
        value['data_root'] = str(selected)
    return value


def data_root(root=ROOT):
    return Path(installation(root).get('data_root', str(Path(root) / 'data'))).resolve()


def service_port(root=ROOT):
    return int(installation(root).get('port', 8765))


def queue_folder(demo=False):
    if demo and installation().get('autostart') == 'Boot':
        import os
        return Path(os.environ['LOCALAPPDATA']) / 'Printvaerksted-Demo' / 'data'
    return data_root() / ('demo' if demo else 'school')
