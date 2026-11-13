"""Synthetic existing-machine transition; every host command is recorded, never run."""
import importlib.util
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).parents[1]/"installation/debian/bootstrap_existing.py"
sys.path.insert(0,str(SOURCE.parents[2]/"packaging"))
SPEC=importlib.util.spec_from_file_location("student_bootstrap_test",SOURCE)
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
UID,GID=4242,4243  # synthetic pupil account

@unittest.skipUnless(hasattr(os,"geteuid") and os.geteuid()==0,"Linux root required for protected synthetic tree")
class Bootstrap(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="mkv-bootstrap-test-",dir="/tmp");self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.calls=[];self.environments=[];self.cadoodle=None
        self.file("var/lib/dpkg/status","")
        self.file("usr/lib/os-release",'ID=debian\nVERSION_ID="13"\n')
        self.file("etc/machine-id","synthetic-machine\n")
        self.file("etc/elevhub/enhed.json",json.dumps({"computer":"test-01","token":"R"*43}),0o600)
        self.file("etc/elevhub/ca.pem","synthetic public CA")
        self.file("etc/elevhub/kiosk.json",json.dumps({"computer":"test-01","elev":"elev","protokol":"https","ui_token":"U"*43,"server":"school.example","hub":"https://school.example:8773"}))
        self.file(M.SESSION_FILES[0],"exec startx /usr/local/lib/elevhub/kiosk-session -- -nolisten tcp\n")
        self.file(M.SESSION_FILES[3],"[Service]\nExecStart=-/sbin/agetty --autologin elev %I $TERM\n")
        self.file(M.LEGACY_FILES[0],"[Service]\nExecStart=/old-reviewed-updater\n")
        self.passwd(M.PUPIL_HOME)
        self.home=self.directory("home/elev",0o750);self.projects=self.directory("home/elev/Projekter",0o750)
        self.archive=self.directory("home/elev/Projekter/.elevhub",0o700)
        self.payload=self.root/"payload";self.payload.mkdir()
        self.public=b"synthetic public signing key"
        (self.payload/"trust").mkdir();(self.payload/"trust/makervaerkstedet.gpg").write_bytes(self.public)
        self.packages=[{"package":name,"architecture":arch,"version":"1.0"} for name,arch in M.PACKAGES.items()]
        files={"trust/makervaerkstedet.gpg":M.sha(self.public)}
        required=["apt/"+name for name in ("Packages","Packages.gz","Release","InRelease","Release.gpg")]
        required += ["apt/pool/"+p["package"]+"_"+p["version"]+"_"+p["architecture"]+".deb" for p in self.packages]
        for name in required:
            file=self.payload/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b"synthetic:"+name.encode());files[name]=M.sha(file.read_bytes())
        lock={"schema":1,"packages":self.packages,"files":files}
        (self.payload/"install-lock.json").write_text(json.dumps(lock))
        ca=patch.object(M.ssl,"create_default_context");ca.start();self.addCleanup(ca.stop)
        self.keypatch=patch.object(M,"KEY_SHA",M.sha(self.public));self.keypatch.start();self.addCleanup(self.keypatch.stop)
    def file(self,name,text,mode=0o644):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);path.chmod(mode);return path
    def passwd(self,home):
        self.file("etc/passwd","root:x:0:0:root:/root:/bin/bash\n"+":".join(("elev","x",str(UID),str(GID),"Elev",home,"/bin/bash"))+"\n")
    def directory(self,name,mode=0o750):
        path=self.root/name;path.mkdir(parents=True,exist_ok=True);os.chown(path,UID,GID);path.chmod(mode);return path
    def pupil(self,name,text="synthetic pupil bytes"):
        """A pupil-owned file below ~/Projekter, with pupil-owned folders."""
        path=self.projects/name
        for parent in reversed(path.relative_to(self.projects).parents[:-1]):self.directory((self.projects/parent).relative_to(self.root),0o700)
        path.write_text(text);os.chown(path,UID,GID);return path
    def record(self,args,**kwargs):
        self.calls.append(args);self.environments.append(kwargs.get("env"))
        if args[0]=="/usr/bin/apt-config":return Path(kwargs["env"]["APT_CONFIG"]).read_text()
        if args[:2]==["/usr/bin/dpkg","--print-architecture"]:return "amd64\n"
        if args[0]=="/usr/bin/systemctl" and args[1]=="show":
            if "--value" in args:return "not-found\n"
            return "LoadState=loaded\nActiveState=inactive\nMainPID=0\n"
        if args[0]=="/usr/bin/dpkg-query" and args[-1]=="cadoodle":
            if self.cadoodle is None:raise subprocess.CalledProcessError(1,args)
            return self.cadoodle+"\tinstall ok installed"
        if args==["/usr/bin/dpkg","--remove","cadoodle"]:self.cadoodle=None;return ""
        if args[0]=="/usr/bin/dpkg-query":
            p=next(p for p in self.packages if p["package"]==args[-1]);return "install ok installed\t"+p["version"]+"\t"+p["architecture"]
        if args[-1]=="--check-config":return '{"state":"configured"}'
        if args[-1]=="--check-runtime":return '{"state":"ready"}'
        return ""
    def plan(self):
        data=json.dumps(M.plan_document(self.root,self.record,self.payload)).encode();return data,M.sha(data)
    def test_inspection_is_read_only_and_does_not_expose_tokens(self):
        inventory=M.inspect(self.root,self.record)
        self.assertNotIn("R"*43,json.dumps(inventory));self.assertFalse((self.root/M.STATE).exists())
        self.assertTrue(all(a[0] in {"/usr/bin/dpkg","/usr/bin/dpkg-query","/usr/bin/systemctl"} for a in self.calls))
    def test_unsupported_os_and_changed_inventory_fail_before_mutation(self):
        raw,pin=self.plan();self.file("etc/machine-id","changed")
        with self.assertRaisesRegex(ValueError,"inventory_changed"):M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertFalse((self.root/M.STATE).exists())
        self.file("usr/lib/os-release",'ID=ubuntu\nVERSION_ID="26.04"\n')
        with self.assertRaisesRegex(ValueError,"unsupported_os"):M.inspect(self.root,self.record)
    def test_second_session_and_unreviewed_plan_are_rejected(self):
        raw,pin=self.plan()
        with self.assertRaisesRegex(ValueError,"plan_hash"):M.validate_plan(raw,"0"*64,M.inspect(self.root,self.record),"0"*64)
        self.file(M.SESSION_FILES[1],"exec startx /usr/local/lib/elevhub/kiosk-session\n")
        with self.assertRaisesRegex(ValueError,"one_reviewed_session"):M.inspect(self.root,self.record)
    def test_unknown_update_unit_or_extra_getty_override_is_not_silently_accepted(self):
        def unknown(args,**kwargs):
            if args[1:2]==["list-unit-files"]:return "elevhub-unknown-update.service enabled\n"
            return self.record(args,**kwargs)
        with self.assertRaisesRegex(ValueError,"unknown_legacy"):M.inspect(self.root,unknown)
        self.file("etc/systemd/system/getty@tty1.service.d/unreviewed.conf","[Service]\n")
        with self.assertRaisesRegex(ValueError,"unknown_getty"):M.inspect(self.root,self.record)

    def test_timer_race_is_rejected_before_session_or_legacy_file_changes(self):
        raw,pin=self.plan()
        def raced(args,**kwargs):
            if args[1:2]==["show"] and args[2:3]==["--property=ActiveState,MainPID"]:
                return "ActiveState=active\nMainPID=987\n"
            return self.record(args,**kwargs)
        with self.assertRaisesRegex(ValueError,"started_during_bootstrap"):
            M.apply(self.payload,raw,pin,root=self.root,run=raced)
        self.assertFalse((self.root/M.LEGACY_FILES[0]).is_symlink())
        self.assertFalse(any(a[:2]==["/usr/bin/systemctl","stop"] and "getty@tty1.service" in a for a in self.calls))
    def test_bootstrap_holds_the_same_updater_lock_until_runtime_is_verified(self):
        raw,pin=self.plan();checked=[]
        def locked(args,**kwargs):
            if args[-1]=="--check-runtime":
                with self.assertRaisesRegex(ValueError,"updater_already_running"):
                    M.acquire_updater_lock(self.root)
                checked.append(True)
            return self.record(args,**kwargs)
        M.apply(self.payload,raw,pin,root=self.root,run=locked)
        self.assertEqual(checked,[True])
        handle=M.acquire_updater_lock(self.root);handle.close()

    def test_payload_inventory_and_reviewed_payload_digest_are_mandatory(self):
        raw,pin=self.plan()
        lock=self.payload/"install-lock.json";data=json.loads(lock.read_text());data["files"].pop("apt/InRelease");lock.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,"incomplete_bootstrap_payload"):M.check_payload(self.payload)
        with self.assertRaisesRegex(ValueError,"payload_changed"):
            M.validate_plan(raw,pin,M.inspect(self.root,self.record),"0"*64)

    def test_failed_local_source_update_or_simulation_leaves_kiosk_and_bindings_unchanged(self):
        for phase in ("update","simulate"):
            with self.subTest(phase=phase):
                self.calls.clear();self.environments.clear();raw,pin=self.plan()
                original=(self.root/M.LEGACY_FILES[0]).read_bytes()
                def failed(args,**kwargs):
                    if args[0]=="/usr/bin/apt-get" and (args[-1]=="update" if phase=="update" else "--simulate" in args):
                        self.record(args,**kwargs);raise subprocess.CalledProcessError(100,args)
                    return self.record(args,**kwargs)
                with self.assertRaises(subprocess.CalledProcessError):M.apply(self.payload,raw,pin,root=self.root,run=failed)
                self.assertEqual((self.root/M.LEGACY_FILES[0]).read_bytes(),original)
                self.assertFalse((self.root/M.STATE).exists())
                self.assertFalse(any(a[:2]==["/usr/bin/systemctl","stop"] for a in self.calls))
                apt=[(a,e) for a,e in zip(self.calls,self.environments) if a[0]=="/usr/bin/apt-get"]
                self.assertTrue(apt)
                for a,e in apt:
                    self.assertIn("Dir::Etc::sourceparts=-",a);self.assertIn("APT_CONFIG",e)
                self.assertEqual(sum(a[-1]=="update" for a,e in apt),1)
    def test_actual_native_apt_config_dump_proves_temporary_hook_free_preflight(self):
        observed=[]
        def native_dump(args,**kwargs):
            if args[0]=="/usr/bin/apt-config":
                value=M.command(args,**kwargs);observed.append(value);return value
            return self.record(args,**kwargs)
        with M.apt_preflight(self.payload,self.packages,self.root,native_dump) as (args,targets):
            self.assertIn("Dir::Etc::sourceparts=-",args)
            self.assertEqual(len(targets),4)
        self.assertEqual(len(observed),1)
        self.assertNotIn('/etc/apt/apt.conf.d',observed[0])
        self.assertFalse((self.root/M.STATE).exists())

    def test_lock_conflict_has_no_failure_marker_or_preflight_or_stops(self):
        raw,pin=self.plan()
        with patch.object(M,"acquire_updater_lock",side_effect=ValueError("updater_already_running")):
            with self.assertRaisesRegex(ValueError,"updater_already_running"):
                M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertFalse((self.root/M.STATE).exists())
        self.assertFalse(any(a[0]=="/usr/bin/apt-get" or a[:2]==["/usr/bin/systemctl","stop"] for a in self.calls))
    def test_partially_failed_first_stop_keeps_manual_review_receipt(self):
        raw,pin=self.plan()
        def partial_stop(args,**kwargs):
            if args[1:3]==["show","--property=LoadState,ActiveState"]:
                return "LoadState=loaded\nActiveState=active\n"
            if args[:2]==["/usr/bin/systemctl","stop"]:
                self.record(args,**kwargs);raise subprocess.CalledProcessError(1,args)
            return self.record(args,**kwargs)
        with self.assertRaises(subprocess.CalledProcessError):
            M.apply(self.payload,raw,pin,root=self.root,run=partial_stop)
        self.assertTrue((self.root/M.STATE/"failed.json").is_file())
        self.assertTrue((self.root/M.STATE/"reviewed-plan.json").is_file())
        self.assertFalse((self.root/M.STATE/"complete.json").exists())

    def test_only_loaded_active_units_are_stopped_and_preflight_precedes_stop(self):
        raw,pin=self.plan()
        def partial_old(args,**kwargs):
            if args[1:3]==["show","--property=LoadState,ActiveState"]:
                self.calls.append(args);self.environments.append(None)
                return "LoadState=not-found\nActiveState=inactive\n" if args[-1]=="elevhub-design.service" else "LoadState=loaded\nActiveState=active\n"
            return self.record(args,**kwargs)
        M.apply(self.payload,raw,pin,root=self.root,run=partial_old)
        stops=[a for a in self.calls if a[:2]==["/usr/bin/systemctl","stop"]]
        self.assertFalse(any("elevhub-design.service" in a for a in stops))
        first_stop=next(i for i,a in enumerate(self.calls) if a[:2]==["/usr/bin/systemctl","stop"])
        simulation=next(i for i,a in enumerate(self.calls) if "--simulate" in a)
        self.assertLess(simulation,first_stop)

    def test_known_cadoodle_is_plan_bound_dryrun_checked_then_only_exact_package_removed(self):
        self.cadoodle="1.9.6";raw,pin=self.plan()
        self.assertEqual(json.loads(raw)["inventory"]["retired_cadoodle"]["version"],"1.9.6")
        M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        dry=next(i for i,a in enumerate(self.calls) if a==["/usr/bin/dpkg","--no-act","--remove","cadoodle"])
        remove=next(i for i,a in enumerate(self.calls) if a==["/usr/bin/dpkg","--remove","cadoodle"])
        self.assertLess(dry,remove);self.assertIsNone(self.cadoodle)
        self.assertFalse(any("purge" in a or "autoremove" in a or a in {"--force-all","--force-depends","--force-remove-reinstreq"} for cmd in self.calls for a in cmd))
    def test_reverse_dependency_preflight_failure_has_no_kiosk_change_or_failed_marker(self):
        self.cadoodle="1.9.6";raw,pin=self.plan()
        def blocked(args,**kwargs):
            if args==["/usr/bin/dpkg","--no-act","--remove","cadoodle"]:raise subprocess.CalledProcessError(1,args)
            return self.record(args,**kwargs)
        with self.assertRaises(subprocess.CalledProcessError):M.apply(self.payload,raw,pin,root=self.root,run=blocked)
        self.assertFalse((self.root/M.STATE).exists());self.assertEqual(self.cadoodle,"1.9.6")
        self.assertFalse(any(a[:2]==["/usr/bin/systemctl","stop"] for a in self.calls))
    def test_changed_retired_version_rejects_plan_and_absent_package_never_removes(self):
        raw,pin=self.plan();self.cadoodle="1.9.6"
        with self.assertRaisesRegex(ValueError,"inventory_changed"):M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.cadoodle="9.9.9"
        with self.assertRaisesRegex(ValueError,"unsupported_retired"):M.inspect(self.root,self.record)
        self.cadoodle=None;raw,pin=self.plan();self.calls.clear();M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertFalse(any(a==["/usr/bin/dpkg","--remove","cadoodle"] for a in self.calls))
    def test_retired_package_removal_failure_preserves_manual_repair_journal(self):
        self.cadoodle="1.9.6";raw,pin=self.plan()
        def failed(args,**kwargs):
            if args==["/usr/bin/dpkg","--remove","cadoodle"]:raise subprocess.CalledProcessError(1,args)
            return self.record(args,**kwargs)
        with self.assertRaises(subprocess.CalledProcessError):M.apply(self.payload,raw,pin,root=self.root,run=failed)
        self.assertTrue((self.root/M.STATE/"failed.json").is_file());self.assertFalse((self.root/M.STATE/"complete.json").exists())

    def test_transition_masks_only_named_bindings_preserves_identity_and_wipes_old_projects(self):
        project=self.pupil("old.sb3","historical bytes")
        before=(self.root/"etc/elevhub/enhed.json").read_bytes();raw,pin=self.plan()
        result=M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertEqual(result["state"],"complete")
        self.assertFalse(project.exists());self.assertEqual((self.root/"etc/elevhub/enhed.json").read_bytes(),before)
        self.assertEqual(os.readlink(self.root/M.LEGACY_FILES[0]),"/dev/null")
        self.assertIn("old-reviewed-updater",(self.root/M.STATE/"previous"/M.LEGACY_FILES[0]).read_text())
        self.assertFalse((self.root/"etc/apt/sources.list.d/makervaerkstedet-bootstrap.sources").exists())
        self.assertFalse(any("rm" in a or "purge" in a for cmd in self.calls for a in cmd))
    def test_failed_runtime_never_completes_or_starts_timer_and_requires_manual_review(self):
        raw,pin=self.plan()
        def broken(args,**kwargs):
            if args[-1]=="--check-runtime":raise RuntimeError("synthetic runtime failure")
            return self.record(args,**kwargs)
        with self.assertRaisesRegex(RuntimeError,"runtime failure"):M.apply(self.payload,raw,pin,root=self.root,run=broken)
        self.assertTrue((self.root/M.STATE/"failed.json").is_file());self.assertFalse((self.root/M.STATE/"complete.json").exists())
        self.assertNotIn(["/usr/bin/systemctl","start","makervaerkstedet-apt-update.timer"],self.calls)
    def test_missing_enrollment_and_mutated_payload_are_rejected(self):
        (self.payload/"trust/makervaerkstedet.gpg").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError,"payload_hash"):M.check_payload(self.payload)
        (self.root/"etc/elevhub/enhed.json").unlink()
        with self.assertRaisesRegex(ValueError,"existing_enrollment"):M.inspect(self.root,self.record)

    # One-time wipe of old pupil projects (owner's decision 13 Nov 2026).
    def kept(self):
        names=("var/lib/elevhub-design/udbakke/r1.json","var/lib/elevhub-design/udbakke/afvist/r0.json",
               "var/lib/elevhub-design/udbakke/print/p1.json","etc/elevhub/project-workshops.json",
               "home/elev/.config/chromium/Default/Preferences")
        for name in names:self.file(name,"keep "+name)
        return {name:(self.root/name).read_bytes() for name in (*names,*M.IDENTITY_FILES)}
    def outside(self):
        outside=self.root/"outside";outside.mkdir();(outside/"keep.svg").write_text("outside");return outside
    def test_migration_wipes_every_pupil_project_and_keeps_queues_identity_and_folders(self):
        kept=self.kept()
        for name in ("Laser/7a/Bil.laser.json","Folie/Hjerte.svg","3D-print/CaDoodle/Raket/Raket.doodle","3D-print/model.stl",
                     ".elevhub/versioner/abc/1.bin",".elevhub/projekter.sqlite3",".elevhub/filindeks.json"):self.pupil(name)
        self.directory("home/elev/Projekter/Tom")
        raw,pin=self.plan();plan=json.loads(raw);summary=plan["pupil_projects_summary"]
        self.assertEqual((summary["files"],summary["directories"],summary["other"]),(7,9,0))
        self.assertEqual([(t["path"],t["roles"]) for t in plan["inventory"]["pupil_projects"]["targets"]],
                         [("/home/elev/Projekter",["downloads","projekter"]),("/home/elev/Projekter/.elevhub",["arkiv"])])
        for secret in ("Bil","Hjerte","Raket","model","versioner","sqlite"):self.assertNotIn(secret,raw.decode())
        def running(args,**kwargs):
            if args[1:3]==["show","--property=LoadState,ActiveState"]:
                self.calls.append(args);self.environments.append(None);return "LoadState=loaded\nActiveState=active\n"
            return self.record(args,**kwargs)
        result=M.apply(self.payload,raw,pin,root=self.root,run=running)
        self.assertEqual(result["pupil_projects_wiped"],summary)
        self.assertEqual(list(self.projects.iterdir()),[self.archive]);self.assertEqual(list(self.archive.iterdir()),[])
        for path,mode in ((self.projects,0o750),(self.archive,0o700)):
            info=path.stat();self.assertEqual((info.st_uid,info.st_gid,info.st_mode&0o7777),(UID,GID,mode))
        self.assertEqual({name:(self.root/name).read_bytes() for name in kept},kept)
        receipt=(self.root/M.STATE/"complete.json").read_text()
        self.assertEqual(json.loads(receipt)["pupil_projects_wiped"],summary);self.assertNotIn("Bil",receipt)
        stop=[self.calls.index(["/usr/bin/systemctl","stop",name]) for name in ("elevhub-lokal.service","user-%d.slice"%UID)]
        check=self.calls.index(["/usr/bin/systemctl","show","--property=ActiveState","elevhub-lokal.service"])
        install=next(i for i,a in enumerate(self.calls) if a[0]=="/usr/bin/apt-get" and "install" in a and "--simulate" not in a)
        restart=next(i for i,a in enumerate(self.calls) if a[:2]==["/usr/bin/systemctl","restart"])
        self.assertLess(max(stop),check);self.assertLess(install,check);self.assertLess(check,restart)
        # The wipe belongs to the migration only; the attempt cannot be re-run.
        self.pupil("Laser/ny.laser.json","new work")
        with self.assertRaisesRegex(ValueError,"bootstrap_attempt_exists"):M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertEqual((self.projects/"Laser/ny.laser.json").read_text(),"new work")
    def test_running_project_writer_blocks_the_wipe_and_requires_review(self):
        project=self.pupil("Folie/a.svg");raw,pin=self.plan()
        def running(args,**kwargs):
            if args[1:3]==["show","--property=ActiveState"]:return "ActiveState=active\n"
            return self.record(args,**kwargs)
        with self.assertRaisesRegex(ValueError,"pupil_project_writer_running"):M.apply(self.payload,raw,pin,root=self.root,run=running)
        self.assertTrue(project.exists());self.assertTrue((self.root/M.STATE/"failed.json").is_file())
    def test_linked_project_folder_or_archive_is_refused_without_change(self):
        outside=self.outside()
        for victim,mode in (("home/elev/Projekter/.elevhub",0o700),("home/elev/Projekter",0o750)):
            with self.subTest(victim=victim):
                path=self.root/victim;shutil.rmtree(path);path.symlink_to(outside,target_is_directory=True)
                with self.assertRaisesRegex(ValueError,"linked_pupil_projects"):M.inspect(self.root,self.record)
                self.assertEqual((outside/"keep.svg").read_text(),"outside")
                path.unlink();self.directory(victim,mode)
        self.directory("home/elev/Projekter/.elevhub",0o700);raw,pin=self.plan()
        shutil.rmtree(self.projects);self.projects.symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,"linked_pupil_projects"):M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertEqual((outside/"keep.svg").read_text(),"outside");self.assertFalse((self.root/M.STATE).exists())
    def test_links_inside_projects_are_removed_without_following(self):
        outside=self.outside()
        (self.projects/"mappe-link").symlink_to(outside,target_is_directory=True)
        (self.projects/"fil-link").symlink_to(self.root/"etc/elevhub/enhed.json")
        self.pupil("Laser/x/a.laser.json");(self.projects/"Laser/x/dybt-link").symlink_to(outside/"keep.svg")
        raw,pin=self.plan();self.assertEqual(json.loads(raw)["pupil_projects_summary"]["other"],3)
        M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertEqual([p.name for p in self.projects.iterdir()],[".elevhub"])
        self.assertEqual((outside/"keep.svg").read_text(),"outside");self.assertTrue((self.root/"etc/elevhub/enhed.json").is_file())
    def test_missing_project_folder_is_recreated_for_the_project_service(self):
        shutil.rmtree(self.projects);raw,pin=self.plan()
        self.assertEqual([t["exists"] for t in json.loads(raw)["inventory"]["pupil_projects"]["targets"]],[False,False])
        result=M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertEqual(result["pupil_projects_wiped"],dict.fromkeys(M.WIPE_COUNTS,0))
        for path,mode in ((self.projects,0o750),(self.archive,0o700)):
            info=path.stat();self.assertEqual((info.st_uid,info.st_gid,info.st_mode&0o7777),(UID,GID,mode))
    def test_empty_project_folder_keeps_its_mode_and_gets_an_archive(self):
        self.archive.rmdir();self.projects.chmod(0o700);raw,pin=self.plan()
        M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertEqual(self.projects.stat().st_mode&0o7777,0o700);self.assertEqual(list(self.projects.iterdir()),[self.archive])
        self.assertEqual((self.archive.stat().st_uid,self.archive.stat().st_mode&0o7777),(UID,0o700))
    def test_changed_project_binding_after_review_is_refused_before_mutation(self):
        project=self.pupil("Folie/a.svg")
        for change in ("owner","mode","service"):
            with self.subTest(change=change):
                raw,pin=self.plan()
                if change=="owner":os.chown(self.projects,UID+1,GID)
                elif change=="mode":self.projects.chmod(0o755)
                else:self.file(M.PROJECT_SERVICE,"[Service]\nExecStart=/usr/bin/python3 lokal.py --projects /home/elev/Andet --state /home/elev/Andet/.elevhub\n")
                with self.assertRaisesRegex(ValueError,"inventory_changed|unexpected_pupil_project_owner"):
                    M.apply(self.payload,raw,pin,root=self.root,run=self.record)
                self.assertTrue(project.exists());self.assertFalse((self.root/M.STATE).exists())
                os.chown(self.projects,UID,GID);self.projects.chmod(0o750);(self.root/M.PROJECT_SERVICE).unlink(missing_ok=True)
    def test_project_folder_swapped_during_installation_is_not_followed(self):
        outside=self.outside();self.pupil("Folie/a.svg");raw,pin=self.plan()
        def swap(args,**kwargs):
            if args[-1].endswith("template_activation.py"):
                os.rename(self.projects,self.root/"home/elev/gammel");self.projects.symlink_to(outside,target_is_directory=True)
            return self.record(args,**kwargs)
        with self.assertRaisesRegex(ValueError,"linked_pupil_projects"):M.apply(self.payload,raw,pin,root=self.root,run=swap)
        self.assertEqual((outside/"keep.svg").read_text(),"outside");self.assertTrue((self.root/"home/elev/gammel/Folie/a.svg").is_file())
        self.assertTrue((self.root/M.STATE/"failed.json").is_file());self.assertFalse((self.root/M.STATE/"complete.json").exists())
    def test_download_folder_and_custom_project_service_paths_are_all_wiped(self):
        self.file(M.PROJECT_SERVICE,"[Service]\nExecStart=/usr/bin/python3 lokal.py --projects /home/elev/Arbejde --state /home/elev/Arbejde/.elevhub\n")
        self.directory("home/elev/Arbejde");self.directory("home/elev/Arbejde/.elevhub",0o700);self.directory("home/elev/Arbejde/Laser",0o700)
        (self.root/"home/elev/Arbejde/Laser/a.laser.json").write_text("old");self.pupil("Hentet.svg")
        raw,pin=self.plan();self.assertEqual(json.loads(raw)["pupil_projects_summary"]["files"],2)
        M.apply(self.payload,raw,pin,root=self.root,run=self.record)
        self.assertEqual([p.name for p in (self.root/"home/elev/Arbejde").iterdir()],[".elevhub"])
        # ~/Projekter is only the browser's download folder here: emptied entirely.
        self.assertEqual(list(self.projects.iterdir()),[])
    def test_unsupported_or_overlapping_project_paths_are_refused(self):
        for line in ("--projects /etc/elevhub","--projects /home/elev/Projekter --state /home/elev/Projekter/a/b",
                     "--projects /home/elev/../root","--projects=/home/elev/Projekter","--projects /home/elev/Arbejde --state /var/lib/arkiv"):
            with self.subTest(line=line):
                self.file(M.PROJECT_SERVICE,"[Service]\nExecStart=/usr/bin/python3 lokal.py "+line+"\n")
                with self.assertRaisesRegex(ValueError,"unsupported_pupil_project_path|overlapping_pupil_project_paths|ambiguous_project_paths"):
                    M.inspect(self.root,self.record)
        (self.root/M.PROJECT_SERVICE).unlink()
        self.passwd("/srv/elev")
        with self.assertRaisesRegex(ValueError,"unsupported_pupil_home"):M.inspect(self.root,self.record)
        os.chown(self.projects,0,0);self.passwd(M.PUPIL_HOME)
        with self.assertRaisesRegex(ValueError,"unexpected_pupil_project_owner"):M.inspect(self.root,self.record)

if __name__=="__main__":unittest.main()

