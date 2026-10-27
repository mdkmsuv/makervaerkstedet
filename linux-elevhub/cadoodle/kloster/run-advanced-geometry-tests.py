#!/usr/bin/env python3
"""Native advanced shape generation and reopened timeline, without app windows."""
import argparse
import os
from pathlib import Path
import subprocess
from build import jdk_tool

parser = argparse.ArgumentParser()
parser.add_argument('--jdk', type=Path, required=True)
parser.add_argument('--jar', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
out = args.out.resolve()
classes = out / 'classes'
classes.mkdir(parents=True, exist_ok=True)
sources = [Path(__file__).resolve().parent / 'tests/gui' / (name + '.java')
           for name in ('AdvancedGeometryHistoryProbe', 'ParameterRegenerationProbe', 'StringParameterRegenerationProbe')]
subprocess.run([str(jdk_tool(args.jdk, 'javac')), '-encoding', 'UTF-8', '-proc:none',
                '-cp', str(args.jar.resolve()), '-d', str(classes), *map(str, sources)], check=True, timeout=90)
for name, arguments in [('AdvancedGeometryHistoryProbe', [str(out / 'fixtures')]),
                        ('ParameterRegenerationProbe', ['fixed', str(out / 'regeneration')]),
                        ('StringParameterRegenerationProbe', ['fixed', str(out / 'string-regeneration')])]:
    with (out / (name + '.log')).open('w', encoding='utf-8') as log:
        subprocess.run([str(jdk_tool(args.jdk, 'java')), '--enable-native-access=ALL-UNNAMED,javafx.graphics',
                        '-Djava.awt.headless=true', '-Duser.home=' + str(out / (name + '-profile')),
                        '-cp', os.pathsep.join((str(classes), str(args.jar.resolve()))), name, *arguments],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    print('PASS: ' + name, flush=True)
