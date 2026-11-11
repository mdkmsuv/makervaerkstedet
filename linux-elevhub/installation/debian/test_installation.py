"""Synthetic extracted-package tests; never install, boot or contact a school."""
import importlib.util,json,os,sys,tempfile,unittest,subprocess,gzip,hashlib,shutil
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).parent
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE.parent));sys.path.insert(0,str(HERE.parents[1]/"administration"))
import build_iso,install_payload,enroll_device
if not hasattr(os,"geteuid") or os.geteuid()!=0:raise RuntimeError("Linux root required for synthetic installation tests")

class Installation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="mkv-iso-test-",dir="/tmp");self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def validator(self):
        namespace={};exec(build_iso.pure_validator(HERE.parent/"firstboot.py"),namespace);return namespace["validate_profile"]
    def extract(self):
        for name in ("MKV_CORE_DEB","MKV_UPDATER_DEB"):
            if name not in os.environ:raise RuntimeError(name+" is required, not skipped")
            subprocess.run(["dpkg-deb","--extract",os.environ[name],str(self.root)],check=True)
    def profile(self):
        folder=self.root/"etc/elevhub-install";folder.mkdir(parents=True,mode=0o700)
        values={"settings.json":json.dumps({"computer":"synthetic-01","server":"10.0.0.2","theme":"green","school_name":"Synthetic"}),"device.json":json.dumps({"computer":"synthetic-01","token":"R"*43,"ui_token":"U"*43}),"ca.crt":"-----BEGIN CERTIFICATE-----\nSYNTHETIC\n-----END CERTIFICATE-----\n","admin.pub":"ssh-ed25519 "+"A"*68}
        for name,text in values.items():
            path=folder/name;path.write_text(text);path.chmod(0o600)
    def test_enrollment_activates_extracted_templates_without_root_token_in_browser(self):
        self.extract();self.profile();project=self.root/"home/elev/Projekter/old.klosterskitse";project.parent.mkdir(parents=True);project.write_bytes(b"preserve existing bytes")
        with patch.object(enroll_device,"validator",self.validator):result=enroll_device.apply_profile(self.root)
        self.assertTrue(result["updater_configured"])
        self.assertEqual(project.read_bytes(),b"preserve existing bytes")
        self.assertEqual((self.root/"etc/elevhub/enhed.json").stat().st_mode&0o777,0o600)
        html=(self.root/"usr/local/share/elevhub/start.html").read_text()
        kiosk=(self.root/"etc/elevhub/kiosk.json").read_text()
        self.assertNotIn("R"*43,html+kiosk);self.assertIn("U"*43,kiosk)
        self.assertIn("https://10.0.0.2:8773/apt/",(self.root/"etc/apt/sources.list.d/makervaerkstedet.sources").read_text())
    def test_existing_different_identity_is_preserved(self):
        self.extract();self.profile();target=self.root/"etc/elevhub/enhed.json";target.parent.mkdir(parents=True);target.write_text('{"computer":"old","token":"KEEP"}');target.chmod(0o600)
        with patch.object(enroll_device,"validator",self.validator),self.assertRaisesRegex(ValueError,"existing_identity"):enroll_device.apply_profile(self.root)
        self.assertIn("KEEP",target.read_text());self.assertFalse((target.parent/"kiosk.json").exists())
    def test_preseed_keeps_guard_and_has_no_identity_or_ssh_server(self):
        source=HERE.parents[1]/"testmiljoe/vm/preseed-usb.skabelon.cfg"
        text=build_iso.preseed(source,"a"*64,["python3",*build_iso.EXTRA_DEBIAN])
        self.assertIn("/dev/elevhub-unselected",text);self.assertIn("sha256sum -c -",text)
        self.assertNotIn("openssh-server",text);self.assertNotIn("NOPASSWD: ALL",text);self.assertNotIn("@@",text)
        self.assertIn("d-i passwd/user-password-crypted password !",text)
        validator=build_iso.pure_validator(HERE.parent/"firstboot.py")
        self.assertNotIn("ansible",validator.lower());self.assertNotIn("def main",validator)
    def test_release_hash_lines_and_exact_four_package_binding(self):
        self.extract();repo=self.root/"repo";(repo/"pool").mkdir(parents=True)
        shutil.copyfile(self.root/"usr/share/makervaerkstedet/trust/makervaerkstedet.gpg",repo/"makervaerkstedet.gpg")
        entries={};blocks=[]
        for name,arch in build_iso.PACKAGES.items():
            filename=name+"_1.0_"+arch+".deb";file=repo/"pool"/filename;file.write_bytes(b"synthetic fixture "+name.encode())
            entries[name]={"file":filename,"version":"1.0","architecture":arch,"sha256":build_iso.sha(file)}
            blocks.append("Package: "+name+"\nVersion: 1.0\nArchitecture: "+arch+"\nFilename: pool/"+filename+"\nSHA256: "+build_iso.sha(file)+"\nSize: "+str(file.stat().st_size)+"\n")
        (repo/"Packages").write_text("\n".join(blocks));(repo/"Packages.gz").write_bytes(gzip.compress((repo/"Packages").read_bytes()))
        (repo/"Release").write_text("SHA256:\n"+"".join(" "+build_iso.sha(repo/name)+" "+str((repo/name).stat().st_size)+" "+name+"\n" for name in ("Packages","Packages.gz")))
        for name in ("InRelease","Release.gpg"):(repo/name).write_text("synthetic parser fixture, not signature evidence")
        receipt=self.root/"gate.json";receipt.write_text(json.dumps({"schema":1,"target":"debian13-amd64-empty-status","all_packages_simulated":True,"actual_install":False,"packages":entries}))
        packages,_=build_iso.validated_repository(repo,receipt,build_iso.sha(receipt));self.assertEqual(len(packages),4)
        (repo/"Packages").write_text("changed")
        with self.assertRaisesRegex(ValueError,"index_hash_mismatch"):build_iso.validated_repository(repo,receipt,build_iso.sha(receipt))

    def test_inert_payload_check_rejects_changed_bytes(self):
        payload=self.root/"payload";payload.mkdir();(payload/"public.txt").write_text("original")
        packages=[{"package":k,"architecture":v,"version":"1.0"} for k,v in build_iso.PACKAGES.items()]
        (payload/"install-lock.json").write_text(json.dumps({"schema":1,"packages":packages,"files":{"public.txt":build_iso.sha(payload/"public.txt")}}))
        self.assertEqual(len(install_payload.check(payload)["packages"]),4)
        (payload/"public.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError,"payload_hash_mismatch"):install_payload.check(payload)
if __name__=="__main__":unittest.main()
