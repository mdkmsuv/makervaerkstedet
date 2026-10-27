#!/usr/bin/env python3
"""Private Xvfb integration probe for candidate17 BackgroundPrewarm."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

NONCE = "0123456789abcdef0123456789abcdef"
ROOT = Path(__file__).resolve().parents[4]
JAR = ROOT / "output/cadoodle/child-ui-17-20261006/linux-result/CaDoodle-Kloster.jar"
JDK = Path("/home/elevhub/.cache/cadoodle-update-20261005/zulu25.36.15-ca-fx-jdk25.0.4-linux_x64")


def windows() -> list[str]:
    found: set[str] = set()
    for args in (("search", "--onlyvisible", "--class", "com.commonwealthrobotics.Main"),
                 ("search", "--onlyvisible", "--class", "com.neuronrobotics.bowlerstudio.Main"),
                 ("search", "--onlyvisible", "--name", ".*(CaDoodle|Splash).*")):
        result = subprocess.run(["xdotool", *args], text=True, capture_output=True, timeout=2)
        found.update(line for line in result.stdout.splitlines() if line.strip())
    return sorted(found)


def wait_for(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists(): return True
        time.sleep(.1)
    return False


def main() -> None:
    if not JAR.is_file() or not (JDK / "bin/java").is_file():
        raise SystemExit("candidate17 JAR or bundled Linux JDK is missing")
    with tempfile.TemporaryDirectory(prefix="cadoodle-prewarm-full-") as home:
        home_path = Path(home)
        (home_path / "bin/CaDoodle-ApplicationInstall").mkdir(parents=True)
        (home_path / "bin/CaDoodle-ApplicationInstall/currentversion.txt").write_text("46\n")
        cached_workspace = ROOT / "Documents/CaDoodle-workspace"
        if cached_workspace.is_dir():
            shutil.copytree(cached_workspace, home_path / "Documents/CaDoodle-workspace")
        marker_root = home_path / ".local/state/elevhub/prewarm"
        marker_root.mkdir(parents=True)
        env = dict(os.environ, HOME=home, USER_HOME=home,
                   ELEVHUB_CADOODLE_PREWARM=NONCE,
                   _JAVA_OPTIONS="-Duser.home=" + home)
        log_path = ROOT / "output/cadoodle/child-ui-17-20261006/privateprewarm.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen([str(JDK / "bin/java"), "--enable-native-access=javafx.graphics",
                                    "-Dprism.forceGPU=true",
                                    "-Duser.home=" + home,
                                    "-cp", str(JAR), "com.commonwealthrobotics.Main"],
                                   env=env, stdout=log_handle, stderr=subprocess.STDOUT)
        ready = marker_root / (NONCE + ".ready")
        show = marker_root / (NONCE + ".show")
        try:
            if not wait_for(ready, 90):
                output = log_path.read_text(encoding="utf-8", errors="replace")[-12000:]
                raise AssertionError("ready marker missing; process=%s log_tail=%r" % (process.poll(), output))
            data = json.loads(ready.read_text(encoding="utf-8"))
            if data.get("nonce") != NONCE or type(data.get("pid")) is not int:
                raise AssertionError("ready marker has invalid pid/nonce: " + repr(data))
            before = windows()
            if before:
                raise AssertionError("visible CaDoodle/splash before show: " + repr(before))
            show.write_text(NONCE, encoding="ascii")
            deadline = time.monotonic() + 20
            after: list[str] = []
            while time.monotonic() < deadline:
                after = windows()
                if after: break
                time.sleep(.1)
            if not after:
                raise AssertionError("CaDoodle did not become visible after show marker")
            print(json.dumps({"ready": True, "visibleBeforeShow": before,
                              "visibleAfterShow": after, "processAlive": process.poll() is None}))
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill()
            log_handle.close()


if __name__ == "__main__":
    main()
