#!/usr/bin/env python3
"""Explicit Debian13 transition. Default is read-only inspection, never clean install.

An operator reviews the exact inventory and pins its plan SHA before --apply.
Failed/partial attempts require manual review; no automatic retry or data purge.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import ssl
from urllib.parse import urlsplit, quote
import subprocess
import tempfile
from contextlib import contextmanager
from apt_sandbox import isolated_config, prove_isolation

PACKAGES={"makervaerkstedet-elev":"all","makervaerkstedet-scratch":"all","makervaerkstedet-matchbox":"amd64","makervaerkstedet-updater":"amd64"}
LEGACY_UNITS=("elevhub-full-update.service","elevhub-firstboot.service","elevhub-onboarding.service")
LEGACY_FILES=tuple("etc/systemd/system/"+name for name in LEGACY_UNITS)+("etc/systemd/system/getty@tty1.service.d/zz-full-update.conf",)
SESSION_FILES=("etc/profile.d/elevhub-kiosk.sh","home/elev/.bash_profile","home/elev/.profile","etc/systemd/system/getty@tty1.service.d/elevhub-autologin.conf","etc/systemd/system/getty@tty1.service.d/autologin.conf")
IDENTITY_FILES=("etc/elevhub/enhed.json","etc/elevhub/ca.pem","etc/elevhub/kiosk.json")
KEY_SHA="5b9ee5f0ca01ff2d52c311e99f71b6eefd69ed8d9149ef7cef5d80cf89a27f1f"
APT=["/usr/bin/apt-get","-o","APT::Get::AllowUnauthenticated=false","-o","Acquire::AllowInsecureRepositories=false","-o","Acquire::AllowDowngradeToInsecureRepositories=false","-o","Acquire::Check-Valid-Until=true","-o","Acquire::Check-Date=true","-o","APT::Update::Error-Mode=any"]
STATE="var/lib/makervaerkstedet-updater/bootstrap"

def require(ok,code):
    if not ok:raise ValueError(code)
def sha(data):return hashlib.sha256(data).hexdigest()
def strict(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            require(key not in result,"duplicate_json_key");result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique)
def command(args,*,env=None):
    p=subprocess.run(args,check=True,capture_output=True,text=True,stdin=subprocess.DEVNULL,
        env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LC_ALL":"C","DEBIAN_FRONTEND":"noninteractive","HOME":"/root",**(env or {})})
    return p.stdout

def protected_parents(path):
    for parent in path.parents:
        require(not parent.is_symlink(),"linked_bootstrap_parent")
        if parent.exists():
            info=parent.stat()
            require(info.st_uid==0 and (not info.st_mode & 0o022 or bool(info.st_mode & stat.S_ISVTX)),"unprotected_bootstrap_parent")

def regular(path,*,private=False):
    protected_parents(path)
    require(not path.is_symlink() and path.is_file(),"required_regular_file")
    info=path.stat();require(info.st_uid==0 and not info.st_mode & (0o077 if private else 0o022),"unprotected_bootstrap_input")
    return path.read_bytes()

def retired_cadoodle(run):
    try:value=run(["/usr/bin/dpkg-query","-W","-f=${Version}\t${Status}","cadoodle"])
    except subprocess.CalledProcessError as error:
        if error.returncode==1:return None
        raise
    fields=value.strip().split("\t")
    require(len(fields)==2,"invalid_retired_cadoodle_status")
    version,status=fields
    if status in {"deinstall ok config-files","unknown ok not-installed"}:return None
    require(status=="install ok installed","retired_cadoodle_manual_repair_required")
    require(version=="1.9.6","unsupported_retired_cadoodle_version")
    return {"package":"cadoodle","version":version,"status":status}

def inspect(root=Path("/"),run=command):
    values={}
    for line in regular(root/"usr/lib/os-release").decode().splitlines():
        if "=" in line:
            key,value=line.split("=",1);parsed=shlex.split(value)
            if len(parsed)==1:values[key]=parsed[0]
    require(values.get("ID")=="debian" and values.get("VERSION_ID")=="13","unsupported_os_baseline")
    arch=run(["/usr/bin/dpkg","--print-architecture"]).strip();require(arch=="amd64","unsupported_host_architecture")
    files={}
    for name in (*IDENTITY_FILES,*LEGACY_FILES,*SESSION_FILES):
        path=root/name
        require(not path.is_symlink(),"linked_bootstrap_binding")
        if not path.exists():files[name]=None;continue
        require(path.is_file(),"invalid_bootstrap_binding")
        data=path.read_bytes();files[name]=sha(data)
        if name.startswith("etc/"):regular(path,private=name=="etc/elevhub/enhed.json")
    require(all(files[name] for name in IDENTITY_FILES),"existing_enrollment_required")
    device=strict(regular(root/"etc/elevhub/enhed.json",private=True))
    require(set(device)=={"computer","token"} and isinstance(device["computer"],str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}",device["computer"]) and isinstance(device["token"],str) and re.fullmatch(r"[A-Za-z0-9_-]{43,128}",device["token"]),"invalid_existing_enrollment")
    config=strict(regular(root/"etc/elevhub/kiosk.json"))
    require(config.get("computer")==device["computer"] and config.get("elev","elev")=="elev","existing_identity_or_session_mismatch")
    require(config.get("protokol")=="https" and isinstance(config.get("ui_token"),str) and re.fullmatch(r"[A-Za-z0-9_-]{43,128}",config["ui_token"]),"modern_https_ui_enrollment_required")
    origin=urlsplit(config.get("hub",""));server=config.get("server","")
    require(isinstance(server,str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*",server) and origin.scheme=="https" and origin.hostname==server.lower() and not origin.username and not origin.password and origin.path in ("","/") and not origin.query and not origin.fragment,"invalid_existing_school_origin")
    ssl.create_default_context(cafile=str(root/"etc/elevhub/ca.pem"))
    launchers=[name for name in SESSION_FILES[:3] if files[name] and "exec startx /usr/local/lib/elevhub/kiosk-session" in (root/name).read_text()]
    require(len(launchers)==1,"one_reviewed_session_required")
    require(sum(bool(files[name]) for name in SESSION_FILES[3:])==1,"one_reviewed_getty_binding_required")
    allowed_units={*LEGACY_UNITS,"makervaerkstedet-firstboot.service","makervaerkstedet-apt-update.service","makervaerkstedet-apt-update.timer"}
    raw_units=run(["/usr/bin/systemctl","list-unit-files","--no-legend","--no-pager"])
    for line in raw_units.splitlines():
        name=line.split()[0] if line.split() else ""
        relevant=re.search(r"^(?:elevhub|makervaerkstedet|mkv|github).*?(?:update|firstboot|onboarding).*\.(?:service|timer)$",name,re.I)
        require("cadoodle" not in name.lower() and (not relevant or name in allowed_units),"unknown_legacy_update_or_cadoodle_unit")
    dropins=root/"etc/systemd/system/getty@tty1.service.d"
    if dropins.exists():
        for path in dropins.glob("*.conf"):
            require(path.relative_to(root).as_posix() in {*SESSION_FILES[3:],LEGACY_FILES[-1]},"unknown_getty_binding")
    states={}
    for name in (*LEGACY_UNITS,"makervaerkstedet-apt-update.service"):
        raw=run(["/usr/bin/systemctl","show","--property=LoadState,ActiveState,MainPID",name])
        state=dict(line.split("=",1) for line in raw.splitlines() if "=" in line)
        require(state.get("MainPID","0")=="0" and state.get("ActiveState") in {"inactive","failed","active"},"legacy_update_still_running")
        states[name]=state
    return {"schema":1,"target":"debian13-amd64","retired_cadoodle":retired_cadoodle(run),"machine_sha256":sha(regular(root/"etc/machine-id")),"files":files,"legacy_units":states}

def validate_plan(raw,pin,inventory,payload_sha256):
    require(re.fullmatch(r"[a-f0-9]{64}",pin or "") and sha(raw)==pin,"reviewed_plan_hash_required")
    plan=strict(raw);require(type(plan) is dict and set(plan)=={"schema","inventory","payload_sha256"} and type(plan["schema"]) is int and plan["schema"]==1,"invalid_bootstrap_plan")
    require(plan["inventory"]==inventory,"bootstrap_inventory_changed")
    require(plan["payload_sha256"]==payload_sha256,"bootstrap_payload_changed")

def check_payload(payload):
    lock=strict(regular(payload/"install-lock.json"))
    require(set(lock)=={"schema","packages","files"} and type(lock["schema"]) is int and lock["schema"]==1,"invalid_install_lock")
    packages=lock["packages"]
    require(type(packages) is list and len(packages)==len(PACKAGES) and {p["package"]:p["architecture"] for p in packages}==PACKAGES,"exact_four_packages_required")
    for p in packages:require(re.fullmatch(r"[0-9][A-Za-z0-9.+:~\-]{0,127}",p["version"]),"invalid_package_version")
    required={"trust/makervaerkstedet.gpg",*["apt/"+name for name in ("Packages","Packages.gz","Release","InRelease","Release.gpg")]}
    required.update("apt/pool/"+p["package"]+"_"+p["version"].replace(":","%3a")+"_"+p["architecture"]+".deb" for p in packages)
    require(type(lock["files"]) is dict and required<=set(lock["files"]),"incomplete_bootstrap_payload_inventory")
    for name,expected in lock["files"].items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts and "\\" not in name,"unsafe_payload_path")
        path=payload/name
        for parent in (path,*path.parents):require(not parent.is_symlink(),"linked_payload")
        require(sha(regular(path))==expected,"payload_hash_mismatch")
    require(sha(regular(payload/"trust/makervaerkstedet.gpg"))==KEY_SHA,"public_keyring_hash_mismatch")
    return packages

def write(path,data,mode=0o600):
    protected_parents(path)
    require(not path.exists() and not path.is_symlink(),"bootstrap_output_exists")
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with path.open("xb") as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    path.chmod(mode)

def acquire_updater_lock(root):
    import fcntl
    path=root/"var/lib/makervaerkstedet-updater/client.lock"
    protected_parents(path);require(not path.is_symlink(),"linked_updater_lock")
    if path.exists():regular(path,private=True)
    handle=path.open("ab");path.chmod(0o600)
    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close();raise ValueError("updater_already_running") from None
    return handle

@contextmanager
def apt_preflight(payload,packages,root,run):
    # Only temporary APT state is written here. No host hooks/sources/network
    # fallback, service changes or package installation before this gate passes.
    with tempfile.TemporaryDirectory(prefix="mkv-bootstrap-preflight-",dir="/tmp") as temporary:
        scope=Path(temporary);scope.chmod(0o755)
        key=scope/"publisher.gpg";key.write_bytes(regular(payload/"trust/makervaerkstedet.gpg"));key.chmod(0o644)
        source=scope/"bootstrap.sources"
        source.write_text("Types: deb\nURIs: file:"+quote(str(payload/"apt"),safe="/")+"/\nSuites: ./\nArchitectures: amd64\nSigned-By: "+str(key)+"\n")
        config=isolated_config(scope,source,key)
        (scope/"state/status").write_bytes(regular(root/"var/lib/dpkg/status"))
        options=["-o","Dir::Etc::sourcelist="+str(source),"-o","Dir::Etc::sourceparts=-",
                 "-o","Dir::State::lists="+str(scope/"state/lists"),"-o","Dir::Cache::archives="+str(scope/"cache/archives"),
                 "-o","Dir::Cache::pkgcache=","-o","Dir::Cache::srcpkgcache="]
        environment={"APT_CONFIG":str(config)}
        dump=run(["/usr/bin/apt-config",*options,"dump"],env=environment)
        prove_isolation(dump,scope)
        args=[*APT,*options,"--yes","--no-remove","--no-install-recommends","-o","Dpkg::Options::=--force-confold"]
        targets=[p["package"]+"="+p["version"] for p in packages]
        run([*APT,*options,"update"],env=environment)
        run([*args,"--simulate","install",*targets],env=environment)
        # Installation later consumes precisely these signed local indices.
        # APT_CONFIG is deliberately NOT passed to install: native dpkg status
        # and native host hooks remain in effect; all source/list/cache paths
        # remain the same explicitly scoped temporary paths.
        yield args,targets


def apply(payload,plan_raw,plan_pin,*,root=Path("/"),run=command):
    require(os.geteuid()==0,"root_required")
    state=root/STATE;require(not state.exists(),"bootstrap_attempt_exists_manual_review_required")
    inventory=inspect(root,run);packages=check_payload(payload)
    validate_plan(plan_raw,plan_pin,inventory,sha(regular(payload/"install-lock.json")))
    key=root/"etc/apt/keyrings/makervaerkstedet.gpg"
    protected_parents(key);require(not key.is_symlink(),"linked_bootstrap_keyring")
    if key.exists():require(regular(key)==regular(payload/"trust/makervaerkstedet.gpg"),"existing_keyring_mismatch")
    # Coordination state is not a bootstrap failure journal. In particular a
    # lock conflict/preflight failure cannot mark a still-working kiosk failed.
    state.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=state.parent.stat();require(info.st_uid==0 and not info.st_mode & 0o077,"unprotected_updater_state")
    handle=acquire_updater_lock(root)
    mutated=False
    def journal():
        if not state.exists():write(state/"reviewed-plan.json",plan_raw)
    def stop_loaded(name):
        nonlocal mutated
        raw=run(["/usr/bin/systemctl","show","--property=LoadState,ActiveState",name])
        current=dict(line.split("=",1) for line in raw.splitlines() if "=" in line)
        if current.get("LoadState")=="loaded" and current.get("ActiveState") not in {"inactive","failed"}:
            journal();mutated=True;run(["/usr/bin/systemctl","stop",name])
    try:
        if inventory["retired_cadoodle"] is not None:
            run(["/usr/bin/dpkg","--no-act","--remove","cadoodle"])
        with apt_preflight(payload,packages,root,run) as (args,targets):
            raw=run(["/usr/bin/systemctl","show","--property=ActiveState,MainPID","makervaerkstedet-apt-update.service"])
            current=dict(line.split("=",1) for line in raw.splitlines() if "=" in line)
            require(current.get("MainPID","0")=="0" and current.get("ActiveState") in {"inactive","failed"},"updater_started_during_bootstrap")
            for name in ("makervaerkstedet-apt-update.timer","getty@tty1.service","elevhub-lokal.service","elevhub-design.service"):
                stop_loaded(name)
            for name in LEGACY_FILES:
                path=root/name
                if inventory["files"][name]:
                    require(sha(path.read_bytes())==inventory["files"][name],"legacy_binding_changed")
                    journal();backup=state/"previous"/name;backup.parent.mkdir(parents=True,exist_ok=True);os.replace(path,backup);mutated=True
            for name in LEGACY_UNITS:
                path=root/"etc/systemd/system"/name
                require(not path.exists() and not path.is_symlink(),"legacy_mask_conflict")
                journal();path.parent.mkdir(parents=True,exist_ok=True);path.symlink_to("/dev/null");mutated=True
            # No native update/full-source fallback here. Missing OS dependencies
            # are a separate explicit maintenance task before bootstrap retry.
            run([*args,"install",*targets])
            require(retired_cadoodle(run)==inventory["retired_cadoodle"],"retired_cadoodle_inventory_changed")
            if inventory["retired_cadoodle"] is not None:
                run(["/usr/bin/dpkg","--remove","cadoodle"])
                require(retired_cadoodle(run) is None,"retired_cadoodle_still_installed")
            for p in packages:
                value=run(["/usr/bin/dpkg-query","-W","-f=${Status}\t${Version}\t${Architecture}",p["package"]])
                require(value=="install ok installed\t"+p["version"]+"\t"+p["architecture"],"installed_package_mismatch")
            run(["/usr/bin/python3","-B","/usr/lib/makervaerkstedet/updater/template_activation.py"])
            check=run(["/usr/bin/python3","-B","/usr/lib/makervaerkstedet/updater/apt_client.py","--check-config"])
            require(strict(check).get("state")=="configured","bootstrap_updater_not_configured")
            units=["makervaerkstedet-firstboot.service","makervaerkstedet-apt-update.timer","elevhub-lokal.service","elevhub-design.service","getty@tty1.service"]
            run(["/usr/bin/systemctl","daemon-reload"]);run(["/usr/bin/systemctl","enable",*units])
            run(["/usr/bin/systemctl","restart","elevhub-lokal.service","elevhub-design.service","getty@tty1.service"])
            ready=run(["/usr/bin/python3","-B","/usr/lib/makervaerkstedet/updater/apt_client.py","--check-runtime"])
            require(strict(ready).get("state")=="ready","bootstrap_runtime_not_ready")
            run(["/usr/bin/systemctl","start","makervaerkstedet-apt-update.timer"])
            write(state/"complete.json",json.dumps({"schema":1,"plan_sha256":plan_pin,"packages":packages}).encode())
    except BaseException:
        if mutated:write(state/"failed.json",b'{"manual_review_required":true}\n')
        elif state.exists():
            # Only this attempt's single newly created coordination receipt.
            (state/"reviewed-plan.json").unlink();state.rmdir()
        raise
    finally:handle.close()
    return {"state":"complete","packages":len(packages)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true");parser.add_argument("--payload",type=Path);parser.add_argument("--plan",type=Path);parser.add_argument("--plan-sha256")
    args=parser.parse_args();require(os.geteuid()==0,"root_required")
    if not args.apply:
        result={"schema":1,"inventory":inspect()}
        if args.payload is not None:
            check_payload(args.payload);result["payload_sha256"]=sha(regular(args.payload/"install-lock.json"))
        print(json.dumps(result,sort_keys=True));return
    require(args.payload is not None and args.plan is not None,"payload_and_reviewed_plan_required")
    print(json.dumps(apply(args.payload.resolve(),regular(args.plan),args.plan_sha256)))

if __name__=="__main__":main()
