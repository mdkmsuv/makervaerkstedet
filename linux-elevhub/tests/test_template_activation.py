import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).parents[1]/"administration/template_activation.py"
SPEC=importlib.util.spec_from_file_location("template_activation_test",SOURCE)
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

if not hasattr(os,"geteuid") or os.geteuid()!=0:raise RuntimeError("Linux root required for release activation checks")

class TemplateActivation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="mkv-activation-",dir="/tmp");self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        templates=self.root/"usr/share/makervaerkstedet/templates";templates.mkdir(parents=True)
        source=SOURCE.parents[1]/"ansible/roles/kiosk/templates"
        integrated=Path(os.environ.get("MKV_TEMPLATE_DIR",str(source)))
        for name in M.TEMPLATES:
            actual=integrated/name if name=="start.html.j2" and integrated.exists() else source/name
            shutil.copyfile(actual,templates/name)
        trust=self.root/"usr/share/makervaerkstedet/trust";trust.mkdir(parents=True)
        public=Path(os.environ.get("MKV_PUBLIC_TRUST_DIR",str(SOURCE.parents[2]/"deployment/keys")))
        for name in M.TRUST:shutil.copyfile(public/name,trust/name)
        projects=self.root/"home/elev/Projekter";projects.mkdir(parents=True)
        self.project=projects/"tidligere.klosterskitse";self.project.write_bytes(b"synthetic legacy data")
    def test_offline_firstboot_renders_local_apps_without_root_identity_or_network(self):
        with patch("urllib.request.urlopen",side_effect=AssertionError("network forbidden")):
            result=M.activate(self.root)
        self.assertFalse(result["paired"])
        page=(self.root/"usr/local/share/elevhub/start.html").read_text()
        session=(self.root/"usr/local/lib/elevhub/kiosk-session").read_text()
        self.assertIn("http://127.0.0.1:8792/",page)
        self.assertNotIn("fresh_session.py",session)
        self.assertNotIn("start_cad",page)
        self.assertFalse((self.root/"etc/elevhub/enhed.json").exists())
        self.assertFalse((self.root/"etc/makervaerkstedet/apt-client.json").exists())
        design=(self.root/"etc/systemd/system/elevhub-design.service").read_text()
        self.assertIn("https://127.0.0.1:8771",design)
        self.assertNotIn("--ca-file",design)
        self.assertEqual(self.project.read_bytes(),b"synthetic legacy data")
        config=(self.root/"etc/elevhub/kiosk.json").read_bytes()
        M.activate(self.root)
        self.assertEqual((self.root/"etc/elevhub/kiosk.json").read_bytes(),config)
    def test_refresh_replaces_stale_rendered_ui_preserves_local_config_and_never_exports_root_token(self):
        config=M.default_config();config.update(server="school.example",computer="test-01",school_name="<Skole>",token="ROOT_SECRET_NOT_FOR_BROWSER")
        path=self.root/"etc/elevhub/kiosk.json";path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config));path.chmod(0o644)
        old=self.root/"usr/local/share/elevhub/start.html";old.parent.mkdir(parents=True);old.write_text("stale native binding")
        before=path.read_bytes();M.activate(self.root)
        text=old.read_text()
        self.assertNotIn("ROOT_SECRET_NOT_FOR_BROWSER",text)
        self.assertNotIn("stale native binding",text)
        self.assertIn("&lt;Skole&gt;",text)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.project.read_bytes(),b"synthetic legacy data")
    def test_existing_custom_data_paths_and_session_preferences_survive_activation(self):
        config=M.default_config();config.update(tastatur="de",screen_output="HDMI-1",screen_mode="1920x1080",mesa_gl=True)
        file=self.root/"etc/elevhub/kiosk.json";file.parent.mkdir(parents=True);file.write_text(json.dumps(config))
        unit=self.root/"etc/systemd/system/elevhub-lokal.service";unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nExecStart=/usr/bin/python3 /usr/local/lib/elevhub/projekter/lokal.py --projects /home/elev/Bevarede --state /var/lib/elevhub-arkiv\n")
        M.activate(self.root)
        text=unit.read_text();session=(self.root/"usr/local/lib/elevhub/kiosk-session").read_text()
        self.assertIn("--projects /home/elev/Bevarede --state /var/lib/elevhub-arkiv",text)
        self.assertIn("setxkbmap de",session);self.assertIn("xrandr --output HDMI-1 --mode 1920x1080",session)
        self.assertIn("--use-gl=angle",session)
        self.assertEqual(self.project.read_bytes(),b"synthetic legacy data")

    def test_writable_configuration_parent_is_rejected(self):
        config=self.root/"etc/elevhub/kiosk.json";config.parent.mkdir(parents=True)
        config.write_text(json.dumps(M.default_config()));config.parent.chmod(0o777)
        with self.assertRaisesRegex(M.ActivationError,"unprotected_parent"):M.activate(self.root)

    def test_existing_root_enrollment_bootstraps_signed_apt_without_exporting_credentials(self):
        config=M.default_config();config.update(server="school.example",hub="https://school.example:8773",computer="test-01")
        file=self.root/"etc/elevhub/kiosk.json";file.parent.mkdir(parents=True);file.write_text(json.dumps(config))
        device=file.parent/"enhed.json";device.write_text(json.dumps({"computer":"test-01","token":"R"*43}));device.chmod(0o600)
        ca=file.parent/"ca.pem";ca.write_bytes(b"synthetic public CA")
        before=device.read_bytes();result=M.activate(self.root)
        self.assertTrue(result["paired"])
        source=(self.root/"etc/apt/sources.list.d/makervaerkstedet.sources").read_text()
        self.assertIn("URIs: https://school.example:8773/apt/",source)
        self.assertIn("Signed-By: /etc/apt/keyrings/makervaerkstedet.gpg",source)
        self.assertNotIn("Trusted:",source)
        settings=self.root/"etc/makervaerkstedet/apt-client.json"
        self.assertEqual(settings.stat().st_mode&0o777,0o600)
        self.assertNotIn("R"*43,settings.read_text())
        self.assertNotIn("R"*43,(self.root/"usr/local/share/elevhub/start.html").read_text())
        self.assertEqual(device.read_bytes(),before)
        M.activate(self.root)
        self.assertEqual(device.read_bytes(),before)

    def test_project_gpg_keyring_is_refreshed_but_school_and_minisign_trust_remain(self):
        M.activate(self.root)
        ring=self.root/"etc/apt/keyrings/makervaerkstedet.gpg"
        ring.write_bytes(b"synthetic previous public project keyring")
        public=self.root/"etc/makervaerkstedet/release.pub";public.write_bytes(b"operator-managed minisign public trust")
        folder=self.root/"etc/elevhub"
        device=folder/"enhed.json";device.write_bytes(b'{"computer":"test-01","token":"KEEP_ROOT_TOKEN"}');device.chmod(0o600)
        ca=folder/"ca.pem";ca.write_bytes(b"preserve existing school CA")
        config=folder/"kiosk.json";values=json.loads(config.read_text());values.update(server="school.example",hub="https://school.example:8773",computer="test-01");config.write_text(json.dumps(values))
        before={p:p.read_bytes() for p in (public,device,ca,config,self.project)}
        M.activate(self.root)
        self.assertEqual(ring.read_bytes(),(self.root/"usr/share/makervaerkstedet/trust/makervaerkstedet.gpg").read_bytes())
        self.assertEqual(ring.stat().st_mode&0o777,0o644)
        for path,value in before.items():self.assertEqual(path.read_bytes(),value)

    def test_all_templates_validated_before_replacing_live_files(self):
        old=self.root/"usr/local/share/elevhub/start.html";old.parent.mkdir(parents=True);old.write_bytes(b"prior page")
        template=self.root/"usr/share/makervaerkstedet/templates/kiosk-session.j2";template.write_text("{{ missing_required_value }}")
        with self.assertRaises(Exception):M.activate(self.root)
        self.assertEqual(old.read_bytes(),b"prior page")
        self.assertFalse((self.root/"etc/elevhub/kiosk.json").exists())
    def test_link_or_writable_config_rejected_and_real_cli_runs_against_extracted_root(self):
        config=self.root/"etc/elevhub/kiosk.json";config.parent.mkdir(parents=True);config.write_text(json.dumps(M.default_config()));config.chmod(0o666)
        with self.assertRaisesRegex(M.ActivationError,"unprotected"):M.activate(self.root)
        config.chmod(0o644)
        result=subprocess.run(["/usr/bin/python3","-B",str(SOURCE),"--root",str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        config.unlink();config.symlink_to(self.project)
        with self.assertRaisesRegex(M.ActivationError,"linked"):M.activate(self.root)

if __name__=="__main__":unittest.main()
