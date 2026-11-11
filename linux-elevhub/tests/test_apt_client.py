"""Real Linux signatures; apt effects isolated to a synthetic adapter only."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).parents[2]
SHARED=Path(os.environ.get("MKV_SHARED_DIR", str(ROOT/"app")))
PIN=json.loads((ROOT/"linux-elevhub/packaging/updater-pins.json").read_text())["shared_verifier_sha256"]
if hashlib.sha256((SHARED/"simple_release.py").read_bytes()).hexdigest()!=PIN:raise RuntimeError("canonical_shared_source_changed")
sys.path.insert(0,str(SHARED));sys.path.insert(0,str(ROOT/"linux-elevhub/administration"))
import apt_client as M
TOOL=Path(os.environ.get("MKV_TEST_MINISIGN", str(ROOT/"tools/minisign")))
if not hasattr(os,"geteuid") or os.geteuid()!=0 or not TOOL.is_file():
    raise RuntimeError("Linux root and MKV_TEST_MINISIGN are required; no release test may silently skip")

class Transport:
    def __init__(self):
        self.response={"schema":1,"revision":103,"poll_after_seconds":60,"desired":[]};self.files={};self.sent=[];self.fail=False
    def poll(self,body):
        self.sent.append(deepcopy(body))
        if self.fail:raise OSError("network unavailable")
        return deepcopy(self.response)
    def get(self,path,limit):
        value=self.files[path]
        if len(value)>limit:raise M.UpdateError("response_too_large")
        return value

class IsolatedCommands:
    def __init__(self):self.versions={};self.downloaded={};self.installs=0;self.activations=0;self.crash=False;self.lie=False;self.events=[]
    def installed(self):return [{"component":c,"version":v} for c,v in sorted(self.versions.items())]
    def older(self,a,b):return M.Commands().older(a,b)
    def refresh(self):self.events.append("apt-update")
    def download(self,a,directory):
        file=directory/a["name"];file.write_bytes(b"synthetic package "+a["package"].encode())
        M.verify_artifact(file,a);self.downloaded[str(file)]=a;return file
    def install(self,files,rollback):
        self.installs+=1;self.events.append(("apt-install",len(files),rollback))
        if not self.lie:
            for file in files:
                a=self.downloaded[str(file)];c=next(c for c,(p,_) in M.DEBIAN_COMPONENTS.items() if p==a["package"]);self.versions[c]=a["version"]
        if self.crash:raise SystemExit("synthetic process interruption after dpkg")
    def activate(self,core_version):self.activations+=1;self.events.append("activate")

class AptClient(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="mkv-apt-test-",dir="/tmp");self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=self.root/"state";self.state.mkdir(mode=0o700)
        self.tool=self.root/"minisign";shutil.copyfile(TOOL,self.tool);self.tool.chmod(0o700)
        self.secret=self.root/"secret.key";self.public=self.root/"public.key"
        subprocess.run([str(self.tool),"-G","-W","-s",str(self.secret),"-p",str(self.public)],check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.key=self.public.read_text().splitlines()[-1];self.transport=Transport();self.commands=IsolatedCommands()
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.offer=self.signed()
        self.transport.response["desired"]=[self.offer]
    def signed(self,component="studentcore",version="2.0.0",sequence=4,revision=7):
        package,arch=M.DEBIAN_COMPONENTS[component];data=b"synthetic package "+package.encode()
        artifact={"name":package+"_"+version+"_"+arch+".deb","package":package,"version":version,"architecture":arch,"sha256":hashlib.sha256(data).hexdigest(),"size":len(data)}
        value={"schema":1,"repository":M.REPOSITORY,"role":"student","platform":"debian-amd64","component":component,"version":version,"sequence":sequence,"artifacts":[artifact]}
        raw=json.dumps(value,indent=2).encode();h=hashlib.sha256(raw).hexdigest()
        message=self.root/(h+".json");message.write_bytes(raw);signature=self.root/(h+".minisig")
        subprocess.run([str(self.tool),"-S","-s",str(self.secret),"-m",str(message),"-x",str(signature)],check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        path="/apt/manifests/"+h
        self.transport.files[path+".json"]=raw;self.transport.files[path+".minisig"]=signature.read_bytes()
        return {"component":component,"version":version,"revision":revision,"manifest_sha256":h,"rollback":False,"manifest_path":path+".json","signature_path":path+".minisig","packages":[artifact]}
    def test_actual_crypto_install_inventory_and_offer_revision_outbox(self):
        self.client.tick();self.assertEqual(self.commands.installs,1);self.assertEqual(self.commands.activations,1)
        self.assertEqual(self.client.ledger["outbox"][0]["revision"],7)
        self.client.tick()
        self.assertEqual(self.transport.sent[-1]["result"],{"revision":7,"state":"succeeded","code":None})
        self.assertEqual(self.transport.sent[-1]["installed"],[{"component":"studentcore","version":"2.0.0"}])
        self.assertEqual(self.commands.installs,1);self.assertFalse(self.client.ledger["outbox"])
    def test_two_successive_core_updates_and_runtime_failure_never_false_success(self):
        self.client.tick()
        self.transport.response["desired"]=[self.signed(version="2.0.1",sequence=5,revision=8)]
        self.client.tick()
        self.assertEqual(self.commands.versions["studentcore"],"2.0.1")
        self.assertEqual(self.commands.installs,2)
        self.assertEqual(self.client.ledger["outbox"][-1]["state"],"succeeded")
        self.transport.response["desired"]=[self.signed(version="2.0.2",sequence=6,revision=9)]
        self.commands.activate=lambda *_: (_ for _ in ()).throw(M.UpdateError("runtime_identity_mismatch"))
        self.client.tick()
        self.assertEqual(self.client.ledger["outbox"][-1]["state"],"failed")
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"runtime_identity_mismatch")
        self.assertNotEqual(self.client.ledger["jobs"]["studentcore:9"]["phase"],"complete")

    def test_signed_release_still_applies_in_a_far_future_year(self):
        from datetime import datetime,timezone
        self.client.now=lambda:datetime(2099,1,1,tzinfo=timezone.utc)
        self.client.tick()
        self.assertEqual(self.commands.installs,1)
        self.assertEqual(self.client.ledger["outbox"][-1]["state"],"succeeded")

    def test_bad_signature_or_server_package_mismatch_never_installs(self):
        self.transport.files[self.offer["signature_path"]]=b"bad signature"
        self.client.tick();self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"invalid_signature")
        other=self.signed(sequence=5,revision=8);other["packages"][0]["version"]="9.0"
        self.transport.response["desired"]=[other];self.client.tick()
        self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"authorization_package_mismatch")
    def test_downgrade_requires_explicit_exact_manifest_rollback(self):
        self.commands.versions["studentcore"]="3.0.0"
        self.client.tick();self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"downgrade_not_authorized")
        self.offer["rollback"]=True;self.client.tick()
        self.assertEqual(self.commands.installs,1)
        self.assertEqual(self.commands.events[-2],("apt-install",1,True))
    def test_crash_after_version_commit_resumes_activation_and_preserves_report_on_network_loss(self):
        self.transport.response["desired"]=[self.signed("updater",revision=9)]
        self.commands.crash=True
        with self.assertRaises(SystemExit):self.client.tick()
        self.assertEqual(self.commands.installs,1)
        resumed=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.commands.crash=False;resumed.tick()
        self.assertEqual(self.commands.installs,1);self.assertEqual(resumed.ledger["outbox"][0]["revision"],9)
        self.transport.fail=True
        with self.assertRaises(OSError):resumed.tick()
        self.assertTrue(resumed.ledger["outbox"])
    def test_successful_apt_exit_cannot_replace_actual_installed_inventory(self):
        self.commands.lie=True;self.client.tick()
        self.assertEqual(self.client.ledger["outbox"][-1]["state"],"failed")
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"installed_version_mismatch")
        self.assertEqual(self.commands.activations,0)
    def test_half_configured_readonly_dpkg_fixture_never_success_acks_or_touches_projects(self):
        database=self.root/"dpkg-fixture";database.mkdir()
        (database/"status").write_text("Package: makervaerkstedet-updater\nStatus: install ok half-configured\nPriority: optional\nSection: education\nInstalled-Size: 1\nMaintainer: Synthetic <test@example.invalid>\nArchitecture: amd64\nVersion: 2.0.0\nDescription: Synthetic state fixture, never installed\n\n")
        project=self.root/"legacy.klosterskitse";project.write_bytes(b"old project bytes")
        class Pending(IsolatedCommands):
            def installed(self):return M.Commands.installed(self)
        adapter=Pending();self.client.commands=adapter
        self.transport.response["desired"]=[self.signed("updater")]
        run=subprocess.run
        def read_only_query(args,*a,**kw):
            if args[0]=="/usr/bin/dpkg-query":args=[args[0],"--admindir="+str(database),*args[1:]]
            return run(args,*a,**kw)
        with patch("subprocess.run",side_effect=read_only_query):self.client.tick()
        self.assertEqual(self.client.ledger["outbox"][-1]["state"],"failed")
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"dpkg_manual_repair_required")
        self.assertEqual(adapter.activations,0)
        self.assertEqual(project.read_bytes(),b"old project bytes")
    def test_apt_command_failure_never_succeeds_and_reports_manual_dpkg_repair(self):
        self.commands.install=lambda *_: (_ for _ in ()).throw(M.UpdateError("dpkg_manual_repair_required"))
        self.client.tick()
        self.assertEqual(self.client.ledger["outbox"][-1]["state"],"failed")
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"dpkg_manual_repair_required")
        self.assertEqual(self.commands.activations,0)

    def test_batch_verifies_every_manifest_before_one_apt_install(self):
        second=self.signed("matchbox",revision=8)
        self.transport.response["desired"].append(second);self.client.tick()
        self.assertEqual(self.commands.installs,1)
        self.assertIn(("apt-install",2,False),self.commands.events)
        self.assertEqual([r["revision"] for r in self.client.ledger["outbox"]],[7,8])
    def test_paths_unknown_components_extra_fields_and_mutated_bytes_are_rejected(self):
        response=deepcopy(self.transport.response)
        for field,value in (("manifest_path","https://attacker.invalid/x"),("component","arbitrary"),("revision",True)):
            modified=deepcopy(response);modified["desired"][0][field]=value
            with self.assertRaises(M.UpdateError):M.validate_response(modified)
        self.transport.files[self.offer["manifest_path"]]+=b" "
        self.client.tick();self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"manifest_hash_mismatch")
    def test_publisher_highwater_survives_restart_and_old_sequence_needs_exact_rollback(self):
        self.client.tick()
        older=self.signed(version="1.0.0",sequence=3,revision=8)
        self.transport.response["desired"]=[older]
        self.commands.versions["studentcore"]="0.1.0"
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.client.tick()
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"rollback_authorization_required")
        self.assertEqual(self.commands.installs,1)
        older["rollback"]=True;self.client.tick()
        self.assertEqual(self.commands.installs,2)
        self.assertEqual(self.client.ledger["highwater"]["studentcore"]["sequence"],4)

    def test_stale_success_is_downgraded_to_diagnostic_failure_before_poll(self):
        self.client.tick();self.commands.versions["studentcore"]="1.0.0";self.transport.response["desired"]=[]
        self.client.tick()
        self.assertEqual(self.transport.sent[-1]["result"]["state"],"failed")
        self.assertEqual(self.transport.sent[-1]["installed"],[{"component":"studentcore","version":"1.0.0"}])
    def test_private_ledger_and_pinned_tool_are_mandatory(self):
        self.state.chmod(0o755)
        with self.assertRaisesRegex(ValueError,"unprotected"):M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.state.chmod(0o700);self.tool.write_bytes(b"modified tool");self.client.tick()
        self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.client.ledger["outbox"][-1]["code"],"verifier_hash_mismatch")

class HTTPSAndCommands(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="mkv-https-test-",dir="/tmp");self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_real_tls_root_auth_redirect_and_body_bounds(self):
        import http.server,ssl,threading
        key=self.root/"tls.key";cert=self.root/"ca.pem"
        subprocess.run(["/usr/bin/openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1","-keyout",str(key),"-out",str(cert),"-subj","/CN=localhost","-addext","subjectAltName=DNS:localhost"],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        requests=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                if self.headers.get("Authorization")!="Bearer "+"R"*43 or self.headers.get("X-Elevhub-Computer")!="test-01":
                    self.send_error(401);return
                requests.append((self.path,json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))))))
                raw=b'{"schema":1,"revision":0,"poll_after_seconds":60,"desired":[]}'
                self.send_response(200);self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                self.server.public_headers.append(dict(self.headers))
                if self.path in ("/redirect","/http-redirect"):
                    self.send_response(302);self.send_header("Location","http://attacker.invalid/" if self.path=="/http-redirect" else "https://attacker.invalid/");self.end_headers()
                else:
                    self.send_response(200);self.send_header("Content-Length","99999999");self.end_headers()
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
        server.public_headers=[]
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        server.socket=context.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        origin="https://localhost:"+str(server.server_port)
        transport=M.HTTPS(origin,cert,"test-01","R"*43)
        self.assertEqual(transport.poll({"schema":1,"installed":[],"result":None})["revision"],0)
        self.assertEqual(requests[0][0],"/api/device/updates")
        with self.assertRaises(Exception):M.HTTPS(origin,cert,"test-01","U"*43).poll({"schema":1,"installed":[],"result":None})
        with self.assertRaisesRegex(M.UpdateError,"redirect_rejected"):transport.get("/redirect",1024)
        with self.assertRaisesRegex(M.UpdateError,"redirect_rejected"):transport.get("/http-redirect",1024)
        self.assertTrue(all("Authorization" not in h and "X-Elevhub-Computer" not in h for h in server.public_headers))
        with self.assertRaisesRegex(M.UpdateError,"response_too_large"):transport.get("/large",1024)
        with self.assertRaises(Exception):M.HTTPS(origin,Path("/etc/ssl/certs/ca-certificates.crt"),"test-01","R"*43).poll({"schema":1,"installed":[],"result":None})
    def test_real_dpkg_order_and_downloaded_package_metadata_hash(self):
        adapter=M.Commands(options=[])
        self.assertTrue(adapter.older("2.0~rc1","2.0"));self.assertFalse(adapter.older("1:1.0","99.0"))
        package_root=self.root/"synthetic-deb"
        (package_root/"DEBIAN").mkdir(parents=True)
        (package_root/"DEBIAN/control").write_text("Package: makervaerkstedet-matchbox\nVersion: 1.2.2+test.1\nArchitecture: amd64\nMaintainer: Test <test@example.invalid>\nDescription: Synthetic metadata fixture\n")
        file=self.root/"synthetic.deb"
        subprocess.run(["dpkg-deb","--build","--root-owner-group",str(package_root),str(file)],check=True,stdout=subprocess.DEVNULL)
        artifact={"package":"makervaerkstedet-matchbox","version":"1.2.2+test.1","architecture":"amd64","sha256":hashlib.sha256(file.read_bytes()).hexdigest(),"size":file.stat().st_size}
        original=adapter.call
        def command(args,*,cwd=None):
            if args[0]=="/usr/bin/apt-get":
                self.assertEqual(args[-2:], ["download",artifact["package"]+"="+artifact["version"]])
                self.assertNotIn("--allow-unauthenticated",args)
                shutil.copyfile(file,cwd/"download.deb");return ""
            return original(args,cwd=cwd)
        self.root.chmod(0o700)
        with patch.object(adapter,"call",side_effect=command):
            received=adapter.download(artifact,self.root)
        self.assertEqual(received.read_bytes(),file.read_bytes())
        received.unlink();artifact["sha256"]="0"*64
        with patch.object(adapter,"call",side_effect=command),self.assertRaisesRegex(M.ReleaseVerificationError,"artifact_hash_mismatch"):
            adapter.download(artifact,self.root)
    def test_native_apt_source_requires_same_https_origin_signed_by_and_secure_flags(self):
        source=self.root/"maker.sources";key=self.root/"release.gpg";ca=self.root/"ca.pem"
        key.write_bytes(b"synthetic public test key");ca.write_bytes(b"synthetic test CA")
        safe="Types: deb\nURIs: https://school.example:8773/apt/\nSuites: ./\nArchitectures: amd64\nSigned-By: "+str(key)+"\n"
        source.write_text(safe)
        options=M.apt_options("https://school.example:8773",ca,source,key)
        self.assertIn("Acquire::https::Verify-Peer=true",options)
        self.assertIn("Acquire::https::Verify-Host=true",options)
        self.assertIn("Acquire::https::school.example::CAInfo="+str(ca),options)
        self.assertIn("Acquire::https::school.example::AllowRedirect=false",options)
        self.assertNotIn("Acquire::https::AllowRedirect=false",options)
        for unsafe in (safe+"Trusted: yes\n",safe.replace("https://","http://"),safe.replace(str(key),"/tmp/other-key"),safe+"Allow-Insecure: yes\n"):
            source.write_text(unsafe)
            with self.assertRaises(M.UpdateError):M.apt_options("https://school.example:8773",ca,source,key)

    def test_install_uses_same_school_scope_for_simulation_and_apply(self):
        adapter=M.Commands(options=["-o","Dir::Etc::sourcelist=/etc/apt/sources.list.d/makervaerkstedet.sources","-o","Dir::Etc::sourceparts=-","-o","Acquire::https::Verify-Peer=true"])
        for rollback in (False,True):
            with patch.object(adapter,"call",return_value="") as run:
                adapter.install([self.root/"fixed.deb"],rollback)
            simulation,apply=[c.args[0] for c in run.call_args_list]
            self.assertEqual([v for v in simulation if v!="--simulate"],apply)
            self.assertTrue(any(value.startswith("Dir::Etc::") for value in apply))
            self.assertIn("--no-remove",apply)
            self.assertEqual("--allow-downgrades" in apply,rollback)
    def test_needed_system_dependencies_require_strict_refresh_and_matching_resimulation(self):
        options=["-o","Dir::Etc::sourcelist=/etc/apt/sources.list.d/makervaerkstedet.sources","-o","Dir::Etc::sourceparts=-","-o","Acquire::https::school.example::CAInfo=/etc/elevhub/ca.pem"]
        adapter=M.Commands(options=options)
        with patch.object(adapter,"call",side_effect=[M.UpdateError("command_failed"),"","",""]) as run:adapter.install([self.root/"fixed.deb"],False)
        original,refresh,simulation,apply=[c.args[0] for c in run.call_args_list]
        self.assertIn("--simulate",original)
        self.assertEqual(refresh[-1],"update")
        self.assertEqual([v for v in simulation if v!="--simulate"],apply)
        for args in (refresh,simulation,apply):
            self.assertFalse(any(v.startswith("Dir::Etc::") for v in args))
            self.assertIn("Acquire::Check-Valid-Until=true",args)
            self.assertIn("APT::Update::Error-Mode=any",args)
            self.assertIn("Acquire::https::school.example::CAInfo=/etc/elevhub/ca.pem",args)
        with patch.object(adapter,"call",side_effect=[M.UpdateError("command_failed"),M.UpdateError("command_failed")]) as run,self.assertRaises(M.UpdateError):adapter.install([self.root/"fixed.deb"],False)
        self.assertEqual(run.call_count,2)
    def test_preflight_manual_repair_and_actual_install_failure_are_not_retried(self):
        adapter=M.Commands(options=[])
        with patch.object(adapter,"call",side_effect=M.UpdateError("dpkg_manual_repair_required")) as run,self.assertRaisesRegex(M.UpdateError,"manual_repair"):adapter.install([self.root/"fixed.deb"],False)
        self.assertEqual(run.call_count,1)
        with patch.object(adapter,"call",side_effect=["",M.UpdateError("command_failed")]) as run,self.assertRaises(M.UpdateError):adapter.install([self.root/"fixed.deb"],False)
        self.assertEqual(run.call_count,2)
    def test_refresh_is_strictly_school_scoped_with_security_options(self):
        options=["-o","Dir::Etc::sourcelist=/etc/apt/sources.list.d/makervaerkstedet.sources","-o","Dir::Etc::sourceparts=-","-o","Acquire::https::school.example::CAInfo=/etc/elevhub/ca.pem"]
        adapter=M.Commands(options=options)
        with patch.object(adapter,"call",return_value="") as call:adapter.refresh()
        args=call.call_args.args[0]
        self.assertEqual(args[-1],"update")
        self.assertIn("Dir::Etc::sourceparts=-",args)
        self.assertIn("Acquire::Check-Valid-Until=true",args)
        self.assertIn("APT::Update::Error-Mode=any",args)
        self.assertIn("Acquire::AllowInsecureRepositories=false",args)
        self.assertIn("Acquire::https::school.example::CAInfo=/etc/elevhub/ca.pem",args)
    def test_activation_executes_new_installed_cli_and_does_not_restart_itself(self):
        adapter=M.Commands(options=[])
        with patch.object(adapter,"call",return_value="") as call,patch.object(M,"wait_ready") as health:adapter.activate("2.0.0")
        commands=[c.args[0] for c in call.call_args_list]
        self.assertEqual(commands[0],["/usr/bin/python3","-B",str(Path(M.__file__).with_name("template_activation.py"))])
        self.assertEqual(commands[1],["/usr/bin/systemctl","daemon-reload"])
        self.assertNotIn("makervaerkstedet-apt-update.service",str(commands))
        self.assertEqual(health.call_args.args[0],"2.0.0")

    def test_readonly_os_preflight_rejects_wrong_baseline_before_install(self):
        file=self.root/"os-release";file.write_text('ID=debian\nVERSION_ID="13"\n')
        M.verify_host(file)
        file.write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        with self.assertRaisesRegex(M.UpdateError,"unsupported_os_baseline"):M.verify_host(file)

    def test_ui_readable_device_file_is_rejected_before_network_or_state_write(self):
        config=self.root/"client.json";device=self.root/"ui.json";public=self.root/"release.pub";ca=self.root/"ca.pem"
        device.write_text(json.dumps({"computer":"test-01","token":"U"*43}));device.chmod(0o644)
        public.write_text("not needed");ca.write_text("not needed")
        config.write_text(json.dumps({"schema":1,"server_origin":"https://school.example","ca_file":str(ca),"device_file":str(device),"public_key_file":str(public),"state_root":"/var/lib/makervaerkstedet-updater"}));config.chmod(0o600)
        with patch.object(M,"HTTPS",side_effect=AssertionError("network forbidden")),self.assertRaisesRegex(ValueError,"unprotected"):
            M.configured(config)

if __name__=="__main__":unittest.main()
