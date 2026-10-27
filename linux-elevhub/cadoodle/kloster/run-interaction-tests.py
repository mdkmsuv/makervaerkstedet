#!/usr/bin/env python3
"""Bounded native drawing/input probes. No Stage, application or desktop control.

Linux requires a JavaFX-capable display (e.g. xvfb-run) and its normal GTK libs.
The probes send synthetic JavaFX events only to their own unattached scene graph.
"""
import argparse
import os
from pathlib import Path
import subprocess
from build import jdk_tool

BASE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--jdk", required=True, type=Path)
parser.add_argument("--jar", required=True, type=Path)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
args.jar = args.jar.resolve()
args.out.mkdir(parents=True, exist_ok=True)
classes = args.out.resolve() / "classes"
classes.mkdir(exist_ok=True)
probes = ["ManipulationGestureProbe", "ManipulationHistoryProbe", "ManipulationPersistenceProbe", "HistoryOrderProbe", "ResizeMeshProbe", "ResizeHandleGestureProbe", "SelectionMeshInputProbe", "SelectionVisibilityProbe", "ModelEditQueueProbe", "MutationOrderProbe", "DimensionCommitProbe", "AlignmentInputProbe", "RotationInputProbe", "MirrorInputProbe", "WorkplaneInputProbe", "SurfaceToolInputProbe", "PatternInputProbe",
          "com.commonwealthrobotics.kloster.tests.gui.AdvancedParameterInputProbe",
          "com.commonwealthrobotics.kloster.tests.gui.StringParameterInputProbe",
          "com.commonwealthrobotics.kloster.tests.gui.TouchModesProbe",
          "com.commonwealthrobotics.kloster.tests.gui.TouchGestureGateTest",
          "com.commonwealthrobotics.kloster.tests.gui.DimensionInputProbe",
          "com.commonwealthrobotics.kloster.LearningLibraryProbe"]
sources = [BASE / ("tests" if name in ("SelectionMeshInputProbe", "SelectionVisibilityProbe") else "tests/gui") /
           (name.rsplit(".", 1)[-1] + ".java") for name in probes]
sources.append(BASE / "tests/DrawingWorkflowTest.java")
subprocess.run([str(jdk_tool(args.jdk, "javac")), "-encoding", "UTF-8", "-proc:none",
                "-cp", str(args.jar), "-d", str(classes), *map(str, sources)], check=True, timeout=90)
for name in [*probes, "DrawingWorkflowTest"]:
    label = name.rsplit(".", 1)[-1]
    test_dir = args.out.resolve() / label
    test_dir.mkdir(exist_ok=True)
    command = [str(jdk_tool(args.jdk, "java")), "--enable-native-access=javafx.graphics",
               f"-Duser.home={test_dir / 'profile'}", "-cp",
               os.pathsep.join((str(classes), str(args.jar))), name]
    if name in ("DrawingWorkflowTest", "PatternInputProbe"): command.append(str(test_dir / "fixture"))
    with (args.out / (label + ".log")).open("w", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=90)
    if result.returncode:
        raise SystemExit(f"FAIL: {label}; see {args.out / (label + '.log')}")
    print(f"PASS: {label}", flush=True)
