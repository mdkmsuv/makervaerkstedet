#!/usr/bin/env python3
"""Run finish/start dialogs on private Linux Xvfb displays, never the active desktop."""
import argparse
from pathlib import Path
import os
import subprocess
import signal

BASE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--jdk', required=True, type=Path)
parser.add_argument('--jar', required=True, type=Path)
parser.add_argument('--out', required=True, type=Path)
parser.add_argument('--only', nargs='+', help='Run only these probe class names.')
args = parser.parse_args()
if os.name != 'posix':
    raise SystemExit('Use Linux/WSL with xvfb-run for these isolated dialog tests.')
args.out = args.out.resolve()
classes = args.out / 'classes'
classes.mkdir(parents=True, exist_ok=True)
probes = ['ProjectCloseGuardProbe', 'ProjectStartDialogProbe', 'com.commonwealthrobotics.kloster.LearningFinishProbe',
          'ProjectTitleFinishProbe', 'ProjectInboxFinishProbe']
if args.only:
    probes = [name for name in probes if name in args.only or name.rsplit('.', 1)[-1] in args.only]
    if not probes:
        parser.error('--only did not match a probe')
sources = [BASE / 'tests/gui' / (name.rsplit('.', 1)[-1] + '.java') for name in probes]
subprocess.run([str(args.jdk / 'bin/javac'), '-encoding', 'UTF-8', '-proc:none', '-cp', str(args.jar.resolve()),
                '-d', str(classes), *map(str, sources)], check=True, timeout=60)
for name in probes:
    label = name.rsplit('.', 1)[-1]
    profile = args.out / label / 'profile'
    profile.mkdir(parents=True, exist_ok=True)
    with (args.out / (label + '.log')).open('w', encoding='utf-8') as log:
        process = subprocess.Popen(['xvfb-run', '-a', str(args.jdk / 'bin/java'), '-Xmx512m', '--enable-native-access=javafx.graphics',
                        f'-Duser.home={profile}', '-cp', os.pathsep.join([str(classes), str(args.jar.resolve())]), name],
                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            result = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
            raise
        if result:
            raise subprocess.CalledProcessError(result, process.args)
    print('PASS:', label, flush=True)
