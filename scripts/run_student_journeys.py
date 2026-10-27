"""Capture the six formerly skipped demos against real isolated product services.

No installation, UAC, active pilot or physical device is used. The external
MakeCode editor and physical hardware remain separate acceptance boundaries.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
TESTS = ['test_box_through_elevhub.cjs', 'test_circuit_local_projects.cjs',
         'test_desktop_maker.cjs', 'test_local_projects_browser.cjs',
         'test_project_opening_browser.cjs', 'test_scratch_local_projects.cjs']


def fingerprint():
    files = set()
    for folder, glob in [('linux-elevhub/projekter','**/*'), ('linux-elevhub/server/kredsloeb','*'),
                         ('linux-elevhub/server/microbit-controller','*'),
                         ('linux-elevhub/server/elevhub/web/assets','*.js'), ('web','*'), ('app','*.py')]:
        files.update(p for p in (ROOT/folder).glob(glob) if p.is_file() and
                     '__pycache__' not in p.parts and p.suffix in ('.js','.py','.html','.css','.json','.txt'))
    files.update(ROOT/'linux-elevhub/tests'/name for name in TESTS)
    files.update(ROOT/name for name in ['linux-elevhub/tests/student_journey_fixture.py',
                 'linux-elevhub/tests/student_journey_fixture.js', 'linux-elevhub/tests/kiosk_browser_fixture.py',
                 'linux-elevhub/server/style-workshop.py', 'linux-elevhub/tests/test_workshop_style_assets.py',
                 'scripts/run_student_journeys.py'])
    for app in ('scratch','datatraener'):
        record = ROOT/f'linux-elevhub/server/{app}/installation.json'
        files.add(record)
        source = Path(json.loads(record.read_text(encoding='utf-8-sig'))['outputPath'])
        files.update(source.glob('*.js'))
        files.update(source.glob('assets/*.js'))
        files.add(source/'index.html')
    return {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p):
            hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reason', required=True)
    parser.add_argument('--test', choices=TESTS, action='append')
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    out = ROOT/'output/maturity-final-20261013/student'/started.replace(':','-')
    out.mkdir(parents=True)
    report = {'startedAt':started, 'reason':args.reason, 'inputSha256':fingerprint(),
              'boundaries':['headless Chromium on Windows; no pupil hardware',
                            'no installation, physical printer/laser/foil or administrator access',
                            'MakeCode recognition and offline guidance only; external editor import not exercised'],
              'results':[]}
    for name in args.test or TESTS:
        command = ['node',str(ROOT/'linux-elevhub/tests'/name)]
        start = time.monotonic()
        result = subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                text=True,encoding='utf-8',errors='replace',timeout=360)
        (out/(name+'.log')).write_text(result.stdout,encoding='utf-8')
        report['results'].append({'command':command,'test':name,'exitCode':result.returncode,
                                  'seconds':round(time.monotonic()-start,3),
                                  'status':'pass' if result.returncode==0 else 'fail'})
        print(name,report['results'][-1]['status'],flush=True)
        (out/'receipt.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    report['completedAt'] = datetime.now(timezone.utc).isoformat()
    report['inputStable'] = report['inputSha256']==fingerprint()
    report['counts'] = {status:sum(item['status']==status for item in report['results']) for status in ('pass','fail','skip')}
    (out/'receipt.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(out)
    return int(report['counts']['fail']>0 or not report['inputStable'])


if __name__ == '__main__':
    raise SystemExit(main())
