"""Actual dpkg-deb archive checks; no installation or service changes."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import unittest

SOURCE = Path(__file__).parents[1] / "packaging/build_debian.py"
SPEC = importlib.util.spec_from_file_location("debian_builder", SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

class DebianPackages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "hello.py"
        self.source.write_bytes(b"print('public program')\n")
        self.spec = {"schema":"makervaerkstedet.debian-inputs.v1", "package":"makervaerkstedet-elev",
            "version":"2.0.0~test1", "architecture":"all", "depends":["python3 (>= 3.11)"],
            "provenance":{"source":"synthetic fixture"}, "files":[{"source":str(self.source),
                "path":"usr/local/lib/elevhub/hello.py", "sha256":M.sha(self.source), "mode":420}]}

    def test_active_release_rejects_retired_component_and_keeps_shared_window_manager(self):
        sys.path.insert(0, str(SOURCE.parent))
        import prepare_inputs, check_install
        expected = {"makervaerkstedet-elev", "makervaerkstedet-scratch",
                    "makervaerkstedet-matchbox", "makervaerkstedet-updater"}
        self.assertEqual(M.PACKAGES, expected)
        self.assertEqual(check_install.PACKAGES, expected)
        self.assertEqual({spec[0] for spec in prepare_inputs.SPECS.values()}, expected)
        self.spec["package"] = "makervaerkstedet-datatraener"
        with self.assertRaisesRegex(ValueError, "invalid_package_identity"):
            M.build(self.spec, self.root / "retired.deb")
        self.assertFalse((self.root / "retired.deb").exists())

    def test_runtime_descriptor_binds_core_not_updater_and_cannot_be_silently_reused(self):
        sys.path.insert(0,str(SOURCE.parent))
        import prepare_inputs
        core=prepare_inputs.SPECS["core"][1]
        descriptor=prepare_inputs.runtime_descriptor(self.root,core)
        self.assertEqual(json.loads(descriptor.read_text()),{"schema":1,"release_id":core,"role":"student"})
        self.assertIn("makervaerkstedet-elev (>= "+prepare_inputs.CORE_VERSION+")",prepare_inputs.SPECS["updater"][3])
        self.assertNotEqual(core,prepare_inputs.SPECS["updater"][1])
        with self.assertRaisesRegex(ValueError,"runtime_descriptor_exists"):
            prepare_inputs.runtime_descriptor(self.root,"9.9.9")

    def test_core_depends_cover_commands_in_real_session(self):
        sys.path.insert(0, str(SOURCE.parent))
        import prepare_inputs
        dependencies = prepare_inputs.SPECS["core"][3]
        names = {d.split(" ", 1)[0] for d in dependencies}
        session = (SOURCE.parents[1] / "ansible/roles/kiosk/templates/kiosk-session.j2").read_text()
        commands = {"setxkbmap": "x11-xkb-utils", "udiskie": "udiskie", "xsetroot": "x11-xserver-utils",
                    "xrandr": "x11-xserver-utils", "chromium": "chromium", "python3": "python3",
                    "/usr/local/lib/elevhub/matchbox-window-manager": "makervaerkstedet-matchbox"}
        for command, package in commands.items():
            with self.subTest(command=command):
                self.assertIn(command, session)
                self.assertIn(package, names)
        self.assertIn("makervaerkstedet-matchbox (= 1.2.2+git20200512+guard.20261105.1)", dependencies)
        self.assertIn("libmatchbox1", prepare_inputs.SPECS["matchbox"][3])
        self.assertNotIn("libmb1", prepare_inputs.SPECS["matchbox"][3])
        self.assertTrue({"xserver-xorg-core", "xserver-xorg-input-libinput", "udisks2", "polkitd", "sudo"} <= names)

    def test_release_gate_failure_does_not_publish_success_marker(self):
        from unittest.mock import patch
        sys.path.insert(0,str(SOURCE.parent))
        import build_release,check_install
        output=self.root/"release";output.mkdir()
        (output/"release-build.json").write_text("stale success")
        locks=[]
        for name in sorted(check_install.PACKAGES):
            spec=dict(self.spec,package=name)
            lock=self.root/(name+".json");lock.write_text(json.dumps(spec));locks.append(str(lock))
        args=["build_release","--input-locks",*locks,"--output-dir",str(output),"--debian-keyring",str(self.source),"--report-dir",str(self.root/"reports")]
        with patch.object(sys,"argv",args),patch.object(build_release,"build") as builder,patch.object(build_release,"check",side_effect=RuntimeError("uninstallable dependency")) as gate:
            with self.assertRaisesRegex(RuntimeError,"uninstallable dependency"):build_release.main()
        self.assertEqual(builder.call_count,4)
        self.assertEqual(len(gate.call_args.args[0]),4)
        self.assertFalse((output/"release-build.json").exists())

    def test_changed_input_rejected_before_output(self):
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "input_hash_mismatch"):
            M.build(self.spec, self.root / "bad.deb")
        self.assertFalse((self.root / "bad.deb").exists())

    def test_data_paths_and_retired_runtime_are_rejected(self):
        for name in ("home/elev/Projekter/a", "etc/elevhub/config", "var/lib/elevhub/state", "usr/../home/a", "usr/local/lib/cadoodle.py", "usr/lib/runtime/java", "usr/lib/app.jar", "usr/local/share/elevhub/apps/datatraener/index.html", "usr/local/share/elevhub/apps/kredsloeb/index.html"):
            with self.subTest(name=name):
                self.spec["files"][0]["path"] = name
                with self.assertRaises(ValueError):
                    M.build(self.spec, self.root / "bad.deb")
        self.assertFalse((self.root / "bad.deb").exists())

    @unittest.skipUnless(shutil.which("dpkg-deb"), "existing Linux dpkg-deb required")
    def test_reproducible_real_archive_has_only_program_files_and_no_scripts(self):
        first = M.build(self.spec, self.root / "first.deb")
        second = M.build(self.spec, self.root / "second.deb")
        self.assertEqual(first["sha256"], second["sha256"])
        unpacked = self.root / "unpacked"
        subprocess.run(["dpkg-deb", "--extract", str(self.root / "first.deb"), str(unpacked)], check=True)
        self.assertEqual((unpacked / self.spec["files"][0]["path"]).read_bytes(), self.source.read_bytes())
        control = self.root / "control"
        subprocess.run(["dpkg-deb", "--control", str(self.root / "first.deb"), str(control)], check=True)
        self.assertEqual({p.name for p in control.iterdir()}, {"control"})
        self.assertFalse(any((unpacked / p).exists() for p in ("etc", "var", "home")))
        metadata = subprocess.check_output(["dpkg-deb", "--field", str(self.root / "first.deb"), "Depends"], text=True)
        self.assertEqual(metadata.strip(), "python3 (>= 3.11)")

if __name__ == "__main__":
    unittest.main()
