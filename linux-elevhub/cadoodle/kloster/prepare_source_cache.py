#!/usr/bin/env python3
"""Materialize the build's pinned Git sources into a reproducible cache.

The cache layout is ``<out>/<repository>/<commit>/<repository path>`` and is
accepted by ``build.py --source-cache``.  This helper never fetches or mutates
Git repositories: pass already available local clones and verify each exact
commit before copying its requested paths.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

APP_COMMIT = "f723ccd25dbc2caf6c2818ddaa4c47193d75aa6c"
ENGINE_COMMIT = "b3db3606c74344d705c9084af8872cbc12f35a0f"
KERNEL_COMMIT = "457a64691504f1231ae5e6d5fd1d99577d96a5b4"

PINNED = (
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/MainController.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/WorkplaneManager.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/ViewCube.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/controls/ResizeSessionManager.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/controls/ControlSprites.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/controls/ResizingHandle.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/numbers/ThreedNumber.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/SelectionBox.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/ActiveProject.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/align/AlignHandle.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/align/AlignManager.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/rotate/RotationHandle.java"),
    ("CaDoodle-Application", APP_COMMIT, "src/main/java/com/commonwealthrobotics/rotate/RotationSessionManager.java"),
    ("BowlerStudio", ENGINE_COMMIT, "src/main/java/com/neuronrobotics/bowlerstudio/threed/BowlerStudio3dEngine.java"),
    ("BowlerStudio", ENGINE_COMMIT, "src/main/java/com/neuronrobotics/bowlerstudio/SplashManager.java"),
    ("bowler-script-kernel", KERNEL_COMMIT, "src/main/java/com/neuronrobotics/bowlerkernel/Bezier3d/Manipulation.java"),
    ("bowler-script-kernel", KERNEL_COMMIT, "src/main/java/com/neuronrobotics/bowlerstudio/scripting/cadoodle/UnGroup.java"),
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", action="append", nargs=2, metavar=("NAME", "PATH"), required=True,
                        help="Local Git clone mapping, e.g. CaDoodle-Application C:/src/CaDoodle-Application")
    args = parser.parse_args()
    repos = {name: Path(path).resolve() for name, path in args.repo}
    output = args.out.resolve()
    for name, commit, relpath in PINNED:
        repo = repos.get(name)
        if repo is None:
            raise SystemExit(f"Missing --repo mapping for {name}")
        if not (repo / ".git").exists():
            raise SystemExit(f"Not a local Git clone: {repo}")
        resolved = git(repo, "rev-parse", f"{commit}^{{commit}}")
        if resolved != commit:
            raise SystemExit(f"{name} does not contain exact pinned commit {commit}")
        target = output / name / commit / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        # Read the committed blob, rather than the working tree, so local edits
        # cannot silently enter a supposedly pinned cache.
        blob = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{relpath}"],
                              check=True, capture_output=True).stdout
        target.write_bytes(blob)
        print(f"cached {name}@{commit}:{relpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
