import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

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
        public=Path(os.environ.get("MKV_PUBLIC_TRUST_DIR",str(SOURCE.parents[3]/"deployment/keys")))
        for name in M.TRUST:shutil.copyfile(public/name,trust/name)
        projects=self.root/"home/elev/Projekter";projects.mkdir(parents=True)
        self.project=projects/"tidligere.projekt";self.project.write_bytes(b"synthetic legacy data")
    def test_clean_activation_creates_browser_policy_without_an_absent_proxy(self):
        M.activate(self.root)
        policy=self.root/"etc/chromium/policies/managed/elevhub.json"
        self.assertTrue(policy.is_file(), "package-only kiosk needs its managed browser policy")
        values=json.loads(policy.read_text())
        self.assertFalse(values["TranslateEnabled"])
        self.assertFalse(values["PrintingEnabled"])
        self.assertEqual(values["HomepageLocation"],"file:///usr/local/share/elevhub/start.html")
        self.assertNotIn("ProxyServer",values)
        self.assertNotIn("ProxyMode",values)
        self.assertIn("http://127.0.0.1:8798/",values["InsecureContentAllowedForUrls"])
        self.assertEqual(policy.stat().st_mode & 0o777,0o644)

    def test_modular_bare_activation_needs_only_projects_and_adds_new_app_port(self):
        runtime=self.root/'usr/share/makervaerkstedet/runtime-release.json'
        runtime.write_text(json.dumps({'schema':1,'release_id':'2.1.0~20261114.1','role':'student'}))
        M.activate(self.root)
        config=json.loads((self.root/'etc/elevhub/kiosk.json').read_text())
        self.assertEqual(config['lokale_app_urler'],{'projekter':'http://127.0.0.1:8791/'})
        self.assertEqual(json.loads((self.root/'etc/elevhub/startup.json').read_text())['urls'],['http://127.0.0.1:8791/_elevhub/health'])
        from app.app_catalog import APP_FIELDS
        import sys
        sys.path.insert(0,str(SOURCE.parents[3]))
        from tests.test_app_catalog import descriptor
        app=self.root/'usr/share/makervaerkstedet/apps/noter';(app/'www').mkdir(parents=True)
        (app/'app.json').write_text(json.dumps(descriptor()))
        (app/'www/index.html').write_text('<html></html>')
        M.activate(self.root)
        policy=json.loads((self.root/'etc/chromium/policies/managed/elevhub.json').read_text())
        self.assertIn('http://127.0.0.1:8799/',policy['InsecureContentAllowedForUrls'])

    def test_browser_policy_uses_explicit_proxy_and_preserves_operator_configuration(self):
        config=M.default_config();config.update(server="school.example",webproxy_required=True)
        path=self.root/"etc/elevhub/kiosk.json";path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config))
        M.activate(self.root)
        policy=self.root/"etc/chromium/policies/managed/elevhub.json"
        self.assertTrue(policy.is_file(), "explicit school proxy policy must be rendered")
        values=json.loads(policy.read_text())
        self.assertEqual(values["ProxyMode"],"fixed_servers")
        self.assertEqual(values["ProxyServer"],"http=127.0.0.1:3128;https=127.0.0.1:3128")
        self.assertEqual(values["ProxyBypassList"],"school.example,127.0.0.1,localhost")
        custom=b'{"TranslateEnabled":true,"HomepageLocation":"https://operator.example/"}\n'
        policy.write_bytes(custom)
        M.activate(self.root)
        self.assertEqual(policy.read_bytes(),custom)
        policy.chmod(0o666)
        with self.assertRaisesRegex(M.ActivationError,"unprotected_configuration"):
            M.activate(self.root)

    def test_browser_policy_context_rejects_ambiguous_proxy_and_invalid_ports(self):
        for value in ("false",1,None):
            with self.subTest(proxy=value):
                config=M.default_config();config["webproxy_required"]=value
                with self.assertRaisesRegex(M.ActivationError,"invalid_webproxy_required"):
                    M.context(config,self.root)
        for value in ([],[True],[0],[65536],["8770"],[8770,8770]):
            with self.subTest(ports=value):
                config=M.default_config();config["elev_porte"]=value
                with self.assertRaisesRegex(M.ActivationError,"invalid_student_ports"):
                    M.context(config,self.root)

    def test_clean_activation_creates_local_startup_checks_and_preserves_existing_policy(self):
        M.activate(self.root)
        startup=self.root/"etc/elevhub/startup.json"
        self.assertTrue(startup.is_file(), "clean kiosk cannot become ready without startup.json")
        self.assertEqual(json.loads(startup.read_text()), {"urls":[
            "http://127.0.0.1:8791/_elevhub/health",
            "http://127.0.0.1:8795/api/runtime/health",
            "http://127.0.0.1:8796/api/runtime/health",
            "http://127.0.0.1:8798/api/runtime/health"]})
        self.assertEqual(startup.stat().st_mode & 0o777, 0o644)
        custom=b'{"urls":["http://127.0.0.1:8791/_elevhub/health"]}\n'
        startup.write_bytes(custom)
        M.activate(self.root)
        self.assertEqual(startup.read_bytes(), custom)

    def test_generated_workshop_readiness_succeeds_without_school_server(self):
        from app.laser_local import create_local_servers, create_print_server

        M.activate(self.root)
        urls=json.loads((self.root/"etc/elevhub/startup.json").read_text())["urls"]
        # Reserve an unreachable upstream: no listener and no external traffic.
        with socket.socket() as upstream:
            upstream.bind(('127.0.0.1',0))
            school=f'http://127.0.0.1:{upstream.getsockname()[1]}'
            laser,folie=create_local_servers(school,laser_port=0,folie_port=0)
            model=create_print_server(school,port=0)
            for server,url in zip((laser,folie,model),urls[1:],strict=True):
                self.addCleanup(server.server_close)
                thread=threading.Thread(target=server.serve_forever,daemon=True)
                thread.start()
                self.addCleanup(server.shutdown)
                path=urlsplit(url).path
                with urlopen(f'http://127.0.0.1:{server.port}{path}',timeout=3) as response:
                    self.assertEqual(response.status,200)
                    self.assertTrue(json.load(response)['local'])
            for server in (laser,folie):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(f'http://127.0.0.1:{server.port}/api/laser/health',timeout=3)
                self.assertEqual(failure.exception.code,503)

    def test_existing_server_3d_link_migrates_to_local_editor_and_project_destination(self):
        config=M.default_config();config.update(server="school.example",computer="test-01")
        config["lokale_app_urler"]={**config["lokale_app_urler"],"3dprint":"https://school.example:8770/"}
        path=self.root/"etc/elevhub/kiosk.json";path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config));path.chmod(0o644)
        before=path.read_bytes()
        values=M.context(config,self.root)
        self.assertEqual(values["kiosk_lokale_app_urler"]["3dprint"],"http://127.0.0.1:8798/")
        M.activate(self.root)
        projects=json.loads((self.root/"etc/elevhub/project-workshops.json").read_text())
        self.assertEqual(projects["3dprint"],"http://127.0.0.1:8798/model3d.html")
        unit=(self.root/"etc/systemd/system/elevhub-design.service").read_text()
        self.assertIn("--print-server-url https://school.example:8770",unit)
        self.assertIn("--model3d-port 8798",unit)
        preserved=json.loads(before);preserved["lokale_app_urler"]["3dprint"]="http://127.0.0.1:8798/"
        self.assertEqual(json.loads(path.read_bytes()),preserved)
        self.assertEqual(self.project.read_bytes(),b"synthetic legacy data")

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
    def test_school_colours_reach_start_page_survive_update_and_bad_values_fall_back(self):
        path=self.root/"etc/elevhub/kiosk.json";path.parent.mkdir(parents=True)
        page=self.root/"usr/local/share/elevhub/start.html"
        config=M.default_config();config.update(theme="green",brand="#1f5a8c",brand_dark="#143c5e")
        path.write_text(json.dumps(config));path.chmod(0o644)
        M.activate(self.root);text=page.read_text()
        self.assertIn('data-theme="green" style="--brand:#1f5a8c;--deep:#143c5e;--brand-dark:#143c5e"',text)
        self.assertEqual(text.count("--brand:#1f5a8c"),1,"the sidebar inherits the school's colours from body")
        M.activate(self.root)  # an apt update re-renders; the private colours stay in kiosk.json
        saved=json.loads(path.read_text())
        self.assertEqual((saved["brand"],saved["brand_dark"]),("#1f5a8c","#143c5e"))
        for bad in ({"brand":"#1F5A8C","brand_dark":"#143c5e"},{"brand":"#1f5a8c"},{"brand":"red","brand_dark":"#143c5e"},
                    {"brand":["#1f5a8c"],"brand_dark":"#143c5e"},{}):
            with self.subTest(bad=bad):
                config=M.default_config();config.update(theme="green",**bad);path.write_text(json.dumps(config))
                M.activate(self.root);text=page.read_text()
                self.assertIn('<body data-theme="green">',text);self.assertNotIn("--brand:",text)
                self.assertEqual(json.loads(path.read_text()).get("brand"),bad.get("brand"),"bad values are kept, only ignored")
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
