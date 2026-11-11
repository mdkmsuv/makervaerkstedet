#!/usr/bin/env python3
"""Root Debian apt updater: school authorization plus publisher verification.

The timer invokes one bounded poll. Apt is the only installation route. The
journal/outbox survives restarts and updater replacement; no session idle gate.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tempfile
from urllib.parse import urlsplit
import urllib.request
from simple_release import (DEBIAN_COMPONENTS, MAX_MANIFEST, MAX_SIGNATURE,
    REPOSITORY, verify_manifest, verify_artifact, accept_sequence, ReleaseVerificationError)
from template_activation import protected, atomic, json_bytes, plain
from runtime_checks import wait_ready, RuntimeCheckError

MINISIGN_SHA256="2c74dffcc1c9a5ee55957c60971998ace2b89f22585631594ec2152c588af8db"
CONFIG=Path("/etc/makervaerkstedet/apt-client.json")
API="/api/device/updates"
HEX=re.compile(r"[a-f0-9]{64}")
VERSION=re.compile(r"[0-9][A-Za-z0-9.+:~\-]{0,127}")
APT=["/usr/bin/apt-get", "-o", "APT::Get::AllowUnauthenticated=false", "-o", "Acquire::AllowInsecureRepositories=false", "-o", "Acquire::AllowDowngradeToInsecureRepositories=false", "-o", "Acquire::Check-Valid-Until=true", "-o", "Acquire::Check-Date=true", "-o", "APT::Update::Error-Mode=any"]

class UpdateError(ValueError):
    pass

def require(ok,code):
    if not ok:raise UpdateError(code)

def strict_json(raw):
    def pairs(values):
        result={}
        for k,v in values:
            require(k not in result,"duplicate_json_key");result[k]=v
        return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(UpdateError("invalid_json_number")))
    except (ValueError,UnicodeError,RecursionError) as error:raise UpdateError("invalid_json") from error

def fields(value,keys):
    require(type(value) is dict and set(value)==set(keys),"invalid_wire_fields")

def digest(value):return hashlib.sha256(value).hexdigest()

def version(value):
    require(isinstance(value,str) and VERSION.fullmatch(value),"invalid_debian_version")
    return value

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise UpdateError("redirect_rejected")

class HTTPS:
    def __init__(self,origin,ca,computer,token):
        parsed=urlsplit(origin)
        require(parsed.scheme=="https" and parsed.hostname and not parsed.username and not parsed.password and parsed.path in ("", "/") and not parsed.query and not parsed.fragment,"https_origin_required")
        self.origin=origin.rstrip("/");self.computer=computer;self.token=token
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))))
    def request(self,path,limit,body=None):
        require(path.startswith("/") and not path.startswith("//") and "\\" not in path,"unsafe_server_path")
        headers={"Accept":"application/json"}
        if body is not None:
            require(path==API,"fixed_poll_path_required")
            headers.update({"Authorization":"Bearer "+self.token,"X-Elevhub-Computer":self.computer})
        data=None if body is None else json_bytes(body)
        if data is not None:headers["Content-Type"]="application/json"
        with self.opener.open(urllib.request.Request(self.origin+path,data=data,headers=headers,method="GET" if body is None else "POST"),timeout=30) as response:
            require(response.status==200,"unexpected_http_status")
            length=response.headers.get("Content-Length")
            require(length is None or (length.isdigit() and int(length)<=limit),"response_too_large")
            raw=response.read(limit+1);require(0<len(raw)<=limit,"response_too_large")
            return raw
    def poll(self,body):return strict_json(self.request(API,MAX_MANIFEST,body))
    def get(self,path,limit):return self.request(path,limit)

def apt_options(origin,ca,source=Path("/etc/apt/sources.list.d/makervaerkstedet.sources"),keyring=Path("/etc/apt/keyrings/makervaerkstedet.gpg")):
    parsed=urlsplit(origin)
    require(parsed.scheme=="https" and parsed.hostname and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*",parsed.hostname),"invalid_apt_origin")
    raw=protected(source).read_bytes();require(len(raw)<=16384,"apt_source_too_large")
    settings={}
    for line in raw.decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):continue
        require(not line[0].isspace() and ":" in line,"invalid_apt_source")
        key,value=line.split(":",1)
        require(key in {"Types","URIs","Suites","Architectures","Signed-By"} and key not in settings,"unsafe_apt_source_option")
        settings[key]=value.strip()
    require(settings.get("Types")=="deb" and settings.get("URIs")==origin.rstrip("/")+"/apt/","apt_origin_mismatch")
    require(settings.get("Signed-By")==str(keyring),"apt_signed_by_required")
    require(settings.get("Suites")=="./","invalid_apt_distribution")
    require(settings.get("Architectures","amd64")=="amd64","invalid_apt_architecture")
    protected(keyring);protected(ca)
    host=parsed.hostname
    return ["-o","Dir::Etc::sourcelist="+str(source),"-o","Dir::Etc::sourceparts=-",
        "-o","Acquire::https::Verify-Peer=true","-o","Acquire::https::Verify-Host=true",
        "-o","Acquire::https::"+host+"::Verify-Peer=true","-o","Acquire::https::"+host+"::Verify-Host=true",
        "-o","Acquire::https::"+host+"::CAInfo="+str(ca),"-o","Acquire::https::"+host+"::AllowRedirect=false"]

class Commands:
    """Fixed argv only; installers are never invoked by tests using this adapter."""
    def __init__(self,options=None):self.options=options
    def apt(self,*,system_dependencies=False):
        require(self.options is not None,"verified_apt_source_required")
        options=self.options
        if system_dependencies:
            options=[value for i,value in enumerate(options) if not (value.startswith("Dir::Etc::") or (value=="-o" and i+1<len(options) and options[i+1].startswith("Dir::Etc::")))]
        return [*APT,*options]
    def call(self,args,*,cwd=None,timeout=None):
        result=subprocess.run(args,cwd=cwd,env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LC_ALL":"C","DEBIAN_FRONTEND":"noninteractive","HOME":"/root"},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=timeout)
        if result.returncode!=0:
            message=result.stderr.decode("utf-8",errors="replace")
            if "dpkg was interrupted" in message or "dpkg --configure -a" in message:
                raise UpdateError("dpkg_manual_repair_required")
            raise UpdateError("command_failed")
        return result.stdout.decode("utf-8",errors="strict")
    def installed(self):
        result=[];self.incomplete=set()
        for component,(package,architecture) in DEBIAN_COMPONENTS.items():
            p=subprocess.run(["/usr/bin/dpkg-query","-W","-f=${Status}\t${Version}\t${Architecture}",package],capture_output=True,text=True,env={"PATH":"/usr/bin:/bin","LC_ALL":"C"})
            if p.returncode!=0:continue
            parts=p.stdout.split("\t")
            if len(parts)==3 and any(parts[0].endswith(" "+state) for state in ("unpacked","half-configured","half-installed","triggers-awaited","triggers-pending")):
                self.incomplete.add(component)
            if len(parts)!=3 or parts[0]!="install ok installed":continue
            require(parts[2]==architecture,"installed_architecture_mismatch")
            result.append({"component":component,"version":version(parts[1])})
        return result
    def older(self,wanted,current):
        p=subprocess.run(["/usr/bin/dpkg","--compare-versions",version(wanted),"lt",version(current)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        require(p.returncode in (0,1),"invalid_debian_version")
        return p.returncode==0
    def download(self,artifact,directory):
        before=set(directory.glob("*.deb"))
        self.call([*self.apt(),"download",artifact["package"]+"="+version(artifact["version"])],cwd=directory)
        created=set(directory.glob("*.deb"))-before
        require(len(created)==1,"unexpected_download_files")
        file=created.pop()
        metadata=self.call(["/usr/bin/dpkg-deb","--field",str(file),"Package","Version","Architecture"])
        expected={"Package":artifact["package"],"Version":artifact["version"],"Architecture":artifact["architecture"]}
        actual=dict(line.split(": ",1) for line in metadata.splitlines() if ": " in line)
        require(actual==expected,"download_package_metadata_mismatch")
        verify_artifact(file,artifact)
        return file
    def refresh(self):self.call([*self.apt(),"update"])
    def install(self,files,rollback):
        flags=["--yes","--no-remove","--no-install-recommends","-o","Dpkg::Options::=--force-confold"]
        if rollback:flags.append("--allow-downgrades")
        targets=[str(p) for p in files]
        # Native apt decides whether installed OS dependencies and the school
        # repository suffice. Keep exactly this scope for simulation and apply;
        # unused system indices cannot supply stale/expired dependency metadata.
        args=[*self.apt(),*flags]
        try:
            self.call([*args,"--simulate","install",*targets])
        except UpdateError as error:
            if str(error)!="command_failed":raise
            # New/changed OS dependencies require successful native validation
            # of all configured sources. Never ignore TLS/signature/expiry errors
            # or retry a partially executed installation.
            self.call([*self.apt(system_dependencies=True),"update"])
            args=[*self.apt(system_dependencies=True),*flags]
            self.call([*args,"--simulate","install",*targets])
        self.call([*args,"install",*targets])
    def activate(self,core_version):
        # Use the newly installed activator after self-update, including its
        # publisher keyring pin, rather than stale imported process code.
        self.call(["/usr/bin/python3","-B",str(Path(__file__).with_name("template_activation.py"))])
        # Fixed local services; no physical printer/laser/Beam Studio operation.
        self.call(["/usr/bin/systemctl","daemon-reload"])
        self.call(["/usr/bin/systemctl","try-restart","elevhub-lokal.service","elevhub-design.service","getty@tty1.service"])
        try:wait_ready(core_version,lambda argv:self.call(argv,timeout=5))
        except (RuntimeCheckError,subprocess.TimeoutExpired) as error:raise UpdateError(str(error) if isinstance(error,RuntimeCheckError) else "runtime_service_timeout") from error


def validate_response(body):
    fields(body,("schema","revision","poll_after_seconds","desired"))
    require(type(body["schema"]) is int and body["schema"]==1,"invalid_poll_schema")
    require(type(body["revision"]) is int and body["revision"]>=0 and type(body["poll_after_seconds"]) is int and 15<=body["poll_after_seconds"]<=86400,"invalid_poll_revision")
    require(type(body["desired"]) is list and len(body["desired"])<=len(DEBIAN_COMPONENTS),"invalid_desired")
    seen=set()
    for offer in body["desired"]:
        fields(offer,("component","version","revision","manifest_sha256","rollback","manifest_path","signature_path","packages"))
        c=offer["component"]
        require(c in DEBIAN_COMPONENTS and c not in seen,"component_not_allowed");seen.add(c)
        version(offer["version"])
        require(type(offer["revision"]) is int and 1<=offer["revision"]<=body["revision"] and type(offer["rollback"]) is bool,"invalid_offer_revision")
        h=offer["manifest_sha256"]
        require(isinstance(h,str) and HEX.fullmatch(h),"invalid_manifest_hash")
        require(offer["manifest_path"]=="/apt/manifests/"+h+".json" and offer["signature_path"]=="/apt/manifests/"+h+".minisig","invalid_manifest_path")
        require(type(offer["packages"]) is list and len(offer["packages"])==1,"invalid_package_list")
    return body

class Client:
    def __init__(self,state,transport,commands,*,minisign,public_key,now=None):
        self.state=protected(state,private=True,directory=True);self.transport=transport;self.commands=commands
        self.minisign=minisign;self.public_key=public_key;self.now=now or (lambda:datetime.now(timezone.utc))
        self.path=self.state/"ledger.json"
        self.ledger=strict_json(protected(self.path,private=True).read_bytes()) if self.path.exists() else {"schema":1,"jobs":{},"outbox":[],"highwater":{}}
        fields(self.ledger,("schema","jobs","outbox","highwater"))
        require(self.ledger["schema"]==1 and type(self.ledger["jobs"]) is dict and type(self.ledger["outbox"]) is list and type(self.ledger["highwater"]) is dict,"invalid_ledger")
    def save(self):atomic(self.path,json_bytes(self.ledger),0o600)
    def enqueue(self,component,revision,state,code):
        result={"component":component,"revision":revision,"state":state,"code":code}
        if result not in self.ledger["outbox"]:self.ledger["outbox"].append(result)
        self.save()
    def verify(self,offer):
        raw=self.transport.get(offer["manifest_path"],MAX_MANIFEST)
        require(digest(raw)==offer["manifest_sha256"],"manifest_hash_mismatch")
        signature=self.transport.get(offer["signature_path"],MAX_SIGNATURE)
        manifest=verify_manifest(raw,signature,minisign_exe=self.minisign,minisign_sha256=MINISIGN_SHA256,public_key=self.public_key,repository=REPOSITORY,role="student",platform="debian-amd64",component=offer["component"],now=self.now())
        require(manifest["version"]==offer["version"] and manifest["artifacts"]==offer["packages"],"authorization_package_mismatch")
        c=offer["component"];old=self.ledger["highwater"].get(c,{"sequence":0,"hash":None})
        require(manifest["sequence"]!=old["sequence"] or old["hash"]==manifest["_manifest_sha256"],"sequence_hash_conflict")
        highest=accept_sequence(manifest,{(REPOSITORY,"student","debian-amd64",c):old["sequence"]},rollback_manifest_sha256=manifest["_manifest_sha256"] if offer["rollback"] else None)
        return manifest,highest
    def tick(self):
        installed=self.commands.installed()
        current={i["component"]:i["version"] for i in installed}
        pending=self.ledger["outbox"][0] if self.ledger["outbox"] else None
        if pending and pending["state"]=="succeeded":
            job=self.ledger["jobs"].get(pending["component"]+":"+str(pending["revision"]))
            if not job or current.get(pending["component"])!=job["version"]:
                pending["state"]="failed";pending["code"]="installed_version_mismatch";self.save()
        body={"schema":1,"installed":installed,"result":{k:v for k,v in pending.items() if k!="component"} if pending else None}
        response=validate_response(self.transport.poll(body))
        if pending:self.ledger["outbox"].pop(0);self.save()
        plan=[];bad=False
        for offer in response["desired"]:
            c=offer["component"];revision=offer["revision"];key=c+":"+str(revision)
            old=self.ledger["jobs"].get(key)
            if old and old["phase"]=="complete" and current.get(c)==offer["version"]:
                require(old["manifest_sha256"]==offer["manifest_sha256"],"revision_manifest_changed")
                continue
            try:
                require(not old or old["manifest_sha256"]==offer["manifest_sha256"],"revision_manifest_changed")
                manifest,highest=self.verify(offer)
                if c in current and self.commands.older(offer["version"],current[c]):require(offer["rollback"],"downgrade_not_authorized")
                high=self.ledger["highwater"].get(c,{"sequence":0,"hash":None})
                if manifest["sequence"]>=high["sequence"]:self.ledger["highwater"][c]={"sequence":highest,"hash":manifest["_manifest_sha256"]}
                job={"manifest_sha256":offer["manifest_sha256"],"version":offer["version"],"phase":"verified"}
                self.ledger["jobs"][key]=job;self.save();plan.append((offer,manifest,job))
            except (UpdateError,ReleaseVerificationError,OSError,ValueError) as error:
                self.failure(c,revision,error);bad=True
        # Authenticate every application component before a single apt command.
        # Mixed core/native updates therefore do not depend on response order.
        if bad:
            for offer,_,_ in plan:self.enqueue(offer["component"],offer["revision"],"failed","plan_rejected")
        elif plan:
            try:
                changed=[p for p in plan if current.get(p[0]["component"])!=p[0]["version"]]
                if changed:
                    size=sum(a["size"] for _,m,_ in changed for a in m["artifacts"])
                    require(shutil.disk_usage(self.state).free>=2*size+128*1024*1024,"insufficient_download_space")
                    self.commands.refresh()
                    with tempfile.TemporaryDirectory(prefix="download-",dir=self.state) as temporary:
                        directory=Path(temporary);directory.chmod(0o700);files=[]
                        for offer,manifest,job in changed:
                            component_dir=directory/offer["component"];component_dir.mkdir(mode=0o700)
                            files.extend(self.commands.download(a,component_dir) for a in manifest["artifacts"])
                            job["phase"]="installing"
                        self.save()
                        self.commands.install(files,any(o["rollback"] for o,_,_ in changed))
                observed={i["component"]:i["version"] for i in self.commands.installed()}
                require(all(observed.get(o["component"])==o["version"] for o,_,_ in plan),"dpkg_manual_repair_required" if getattr(self.commands,"incomplete",set()) else "installed_version_mismatch")
                for _,_,job in plan:job["phase"]="installed"
                self.save();self.commands.activate(observed.get("studentcore"))
                observed={i["component"]:i["version"] for i in self.commands.installed()}
                for offer,_,job in plan:
                    require(observed.get(offer["component"])==offer["version"],"installed_version_mismatch")
                    job["phase"]="complete";self.save();self.enqueue(offer["component"],offer["revision"],"succeeded",None)
            except (UpdateError,ReleaseVerificationError,OSError,ValueError) as error:
                for offer,_,_ in plan:self.failure(offer["component"],offer["revision"],error)
        return {"pending_reports":len(self.ledger["outbox"]),"poll_after_seconds":response["poll_after_seconds"]}
    def failure(self,component,revision,error):
        code=getattr(error,"code",str(error))
        if not isinstance(code,str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}",code):code="local_update_failed"
        self.enqueue(component,revision,"failed",code)


def verify_host(os_release=Path("/usr/lib/os-release"),commands=None):
    import shlex
    values={}
    for line in protected(os_release).read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):continue
        key,value=line.split("=",1)
        parsed=shlex.split(value)
        if len(parsed)==1:values[key]=parsed[0]
    require(values.get("ID")=="debian" and values.get("VERSION_ID")=="13","unsupported_os_baseline")
    require((commands or Commands()).call(["/usr/bin/dpkg","--print-architecture"]).strip()=="amd64","unsupported_host_architecture")

def configured(path):
    config=strict_json(protected(path,private=True).read_bytes())
    fields(config,("schema","server_origin","ca_file","device_file","public_key_file","state_root"))
    require(type(config["schema"]) is int and config["schema"]==1,"invalid_config_schema")
    ca=protected(Path(config["ca_file"]))
    device=strict_json(protected(Path(config["device_file"]),private=True).read_bytes())
    fields(device,("computer","token"))
    require(isinstance(device["computer"],str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}",device["computer"]),"invalid_device_name")
    require(isinstance(device["token"],str) and re.fullmatch(r"[A-Za-z0-9_-]{43,128}",device["token"]),"invalid_root_token")
    public=protected(Path(config["public_key_file"])).read_text().splitlines()
    keys=[line for line in public if not line.startswith("untrusted comment:") and line.strip()]
    require(len(keys)==1,"invalid_public_key_file")
    verify_host()
    state=Path(config["state_root"])
    require(state==Path("/var/lib/makervaerkstedet-updater"),"fixed_state_root_required")
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    protected(state,private=True,directory=True)
    transport=HTTPS(config["server_origin"],ca,device["computer"],device["token"])
    return Client(state,transport,Commands(apt_options(config["server_origin"],ca)),minisign=Path(__file__).parent/"minisign",public_key=keys[0])

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=CONFIG)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument("--check-config",action="store_true")
    mode.add_argument("--check-runtime",action="store_true")
    args=parser.parse_args()
    require(os.geteuid()==0,"root_required")
    # A generic/offline machine has no update credentials. Local apps remain
    # available; absence is not an invitation to bootstrap insecure trust.
    if not args.config.exists():
        require(not (args.check_config or args.check_runtime),"updater_unconfigured")
        print(json.dumps({"state":"unconfigured"}));return
    client=configured(args.config)
    if args.check_config:
        print(json.dumps({"state":"configured"}));return
    if args.check_runtime:
        current={i["component"]:i["version"] for i in client.commands.installed()}
        result=wait_ready(current.get("studentcore"),lambda argv:client.commands.call(argv,timeout=5))
        print(json.dumps({"state":"ready",**result}));return
    lock=plain(client.state/"client.lock")
    with lock.open("a+b") as handle:
        os.chmod(lock,0o600)
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise UpdateError("updater_already_running")
        print(json.dumps(client.tick()))

if __name__=="__main__":main()
