#!/usr/bin/env python3
"""Verify a signed flat apt repo in isolated /tmp roots; NEVER apt install.

Public repository/keyring input only. All TLS private keys are temporary /tmp
files. Configuration paths and absence of host hooks are checked before update.
"""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import sys

ROOT=Path(__file__).parents[2]
sys.path.insert(0,os.environ.get("MKV_SHARED_DIR",str(ROOT/"app")))
sys.path.insert(0,str(ROOT/"linux-elevhub/administration"))
sys.path.insert(0,str(ROOT/"linux-elevhub/packaging"))
import apt_client

NAMES=("InRelease","Release","Release.gpg","Packages","Packages.gz")

from apt_sandbox import isolated_config, prove_isolation

def case(repo,keyring,mode,report,server_source):
    files={name:(repo/name).read_bytes() for name in NAMES if (repo/name).is_file()}
    by_hash=repo/"by-hash/SHA256"
    if by_hash.is_dir():
        for file in by_hash.iterdir():
            if file.is_file() and re.fullmatch("[a-f0-9]{64}",file.name):
                data=file.read_bytes()
                if hashlib.sha256(data).hexdigest()!=file.name:raise RuntimeError("invalid_by_hash_input")
                files["by-hash/SHA256/"+file.name]=data
    if not {"InRelease","Packages"}<=set(files):raise RuntimeError("missing_signed_repository_inputs")
    if mode=="tampered":
        data=files["InRelease"]
        index=data.find(b"Origin:")
        if index<0:raise RuntimeError("missing_origin_for_tamper_fixture")
        data=bytearray(data);data[index]=ord("X");files["InRelease"]=bytes(data)
    with tempfile.TemporaryDirectory(prefix="mkv-native-apt-",dir="/tmp") as temp:
        base=Path(temp);base.chmod(0o755)
        secrets=base/"secrets";secrets.mkdir(mode=0o700)
        key=secrets/"https.key";cert=secrets/"https.crt"
        subprocess.run(["/usr/bin/openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1","-keyout",str(key),"-out",str(cert),"-subj","/CN=localhost","-addext","subjectAltName=DNS:localhost"],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        # Reuse the actual product handler, dispatch and TLS server. Only its
        # request log is observed; no protocol/connection behaviour is replaced.
        sys.path.insert(0,str(server_source))
        import elevhub
        from simple_updates import SimpleUpdates
        configroot=base/"config";configroot.mkdir()
        cache=configroot/"public-apt-cache"
        shutil.copytree(repo,cache)
        for name,data in files.items():
            target=cache/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        service=SimpleUpdates(base/"data",{"apt_cache":"public-apt-cache","catalog":[],"device_bindings":{}},configroot,None)
        headers=[]
        class RecordingHub(elevhub.Hub):
            def log_request(self,*args):
                headers.append({"path":self.path,"status":args[0],"headers":dict(self.headers)})
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
        server=elevhub.Server(("127.0.0.1",0),RecordingHub,tls=tls)
        server.simple_updates=service;server.navn="native-apt-test"
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            root=base/"apt-root";root.mkdir(mode=0o755);(root/"etc").mkdir()
            public_ca=root/"etc/ca.pem";shutil.copyfile(cert,public_ca)
            if mode=="wrong-ca":
                other_key=secrets/"other.key";other_cert=secrets/"other.crt"
                subprocess.run(["/usr/bin/openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1","-keyout",str(other_key),"-out",str(other_cert),"-subj","/CN=untrusted-test-ca"],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                shutil.copyfile(other_cert,public_ca)
            public_key=root/"etc/keyring.gpg";shutil.copyfile(keyring,public_key)
            origin="https://localhost:"+str(server.server_port)
            source=root/"etc/makervaerkstedet.sources"
            source.write_text("Types: deb\nURIs: "+origin+"/apt/\nSuites: ./\nArchitectures: amd64\nSigned-By: "+str(public_key)+"\n")
            config=isolated_config(root,source,public_key)
            options=apt_client.apt_options(origin,public_ca,source,public_key)
            options += ["-o","APT::Get::List-Cleanup=false"]
            environment={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LC_ALL":"C","HOME":str(root),"APT_CONFIG":str(config)}
            common=[*apt_client.APT[1:],*options]
            dump=subprocess.check_output(["/usr/bin/apt-config",*common,"dump"],env=environment,text=True)
            paths=prove_isolation(dump,root)
            report.mkdir(parents=True,exist_ok=True)
            (report/(mode+"-apt-config.txt")).write_text(dump)
            # No network/index command is reached before isolation is proven.
            result=subprocess.run(["/usr/bin/apt-get",*common,"update"],env=environment,cwd=root,capture_output=True,text=True,timeout=120)
            log=result.stdout+result.stderr;(report/(mode+"-apt-update.log")).write_text(log)
            (report/(mode+"-requests.json")).write_text(json.dumps(headers,indent=2)+"\n")
            expected_success=mode=="valid"
            if (result.returncode==0)!=expected_success:raise RuntimeError("unexpected_native_apt_result:"+mode+":"+str(result.returncode))
            if mode=="wrong-ca" and "certificate verify failed" not in log.lower():raise RuntimeError("wrong_ca_rejection_not_proven")
            policy=""
            if expected_success:
                policy=subprocess.check_output(["/usr/bin/apt-cache",*common,"policy","makervaerkstedet-elev"],env=environment,cwd=root,text=True)
                if "Candidate:" not in policy or "Candidate: (none)" in policy:raise RuntimeError("candidate_not_loaded")
                (report/"valid-apt-policy.txt").write_text(policy)
            if any("Authorization" in h["headers"] for h in headers):raise RuntimeError("credential_on_public_apt_fetch")
            return {"case":mode,"exit_code":result.returncode,"isolation_proven_before_update":True,
                "config_sha256":hashlib.sha256(dump.encode()).hexdigest(),"requests":len(headers),"no_authorization_headers":True,
                "candidate_loaded":bool(policy),"host_install_or_service_commands":False,
                "checked_path_count":len(paths),"handler":"elevhub.Hub/Server + simple_updates.SimpleUpdates",
                "server_sources":{n:hashlib.sha256((server_source/n).read_bytes()).hexdigest() for n in ("elevhub.py","simple_updates.py")}}
        finally:server.shutdown();server.server_close();thread.join()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,required=True);parser.add_argument("--keyring",type=Path,required=True)
    parser.add_argument("--server-source",type=Path,required=True);parser.add_argument("--report-dir",type=Path,required=True)
    args=parser.parse_args()
    if os.geteuid()!=0:raise RuntimeError("synthetic_root_test_requires_root")
    results=[case(args.repo,args.keyring,"valid",args.report_dir,args.server_source),case(args.repo,args.keyring,"tampered",args.report_dir,args.server_source),case(args.repo,args.keyring,"wrong-ca",args.report_dir,args.server_source)]
    (args.report_dir/"native-apt-results.json").write_text(json.dumps(results,indent=2)+"\n")
    print(json.dumps(results))

if __name__=="__main__":main()
