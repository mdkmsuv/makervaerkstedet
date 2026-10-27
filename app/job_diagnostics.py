"""Persistent, teacher-only preparation diagnostics outside transient job files."""
import json
import re
import time
from pathlib import Path


def diagnostic_path(folder, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Ugyldigt printjob.')
    return Path(folder) / 'job-diagnostics' / (job_id + '.json')


def save_diagnostic(folder, job_id, stage, error):
    target = diagnostic_path(folder, job_id)
    log = Path(folder) / 'sliced' / job_id / 'slicer.log'
    tail = ''
    if log.is_file():
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 32768))
            tail = stream.read(32768).decode('utf-8', errors='replace')
    # Never serialize exception arguments: transfer errors can include credentials.
    message = str(error) if stage == 'slicing' and isinstance(error, ValueError) else ''
    if error.__class__.__name__ == 'TimeoutExpired' and stage == 'slicing':
        message = 'Bambu Studio blev stoppet efter 10 minutter uden en færdig printfil.'
    data = {'stage': stage, 'error_type': error.__class__.__name__,
            'message': message, 'log_tail': tail if stage == 'slicing' else '', 'created': time.time()}
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)
    return data


def read_diagnostic(folder, job_id):
    target = diagnostic_path(folder, job_id)
    if not target.is_file():
        return {'message': 'Der er ingen gemt detaljeret fejlrapport for dette print.', 'log_tail': ''}
    return json.loads(target.read_text(encoding='utf-8'))
