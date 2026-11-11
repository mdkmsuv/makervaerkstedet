#!/usr/bin/env python3
"""Mandatory four-package Debian 13 installability gate. Simulation ONLY.

An empty dpkg status models a clean Debian13 dependency set, independently of
packages installed on the build host. Only signed Debian indices are downloaded;
no package installation, maintainer script or service command is executed.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, tempfile
from pathlib import Path
from apt_sandbox import isolated_config, prove_isolation

PACKAGES={"makervaerkstedet-elev","makervaerkstedet-updater","makervaerkstedet-scratch","makervaerkstedet-matchbox"}

def check(packages,keyring,report):
    if not hasattr(os,"geteuid") or os.geteuid()!=0:raise RuntimeError("release_gate_requires_Linux_root")
    release=Path("/etc/os-release").read_text()
    if not re.search(r'^ID=debian$',release,re.M) or not re.search(r'^VERSION_ID="?13"?$',release,re.M):raise RuntimeError("release_gate_requires_Debian13")
    if subprocess.check_output(["dpkg","--print-architecture"],text=True).strip()!="amd64":raise RuntimeError("release_gate_requires_amd64")
    identity={}
    for path in packages:
        fields=subprocess.check_output(["dpkg-deb","--field",str(path),"Package","Version","Architecture"],text=True)
        values=dict(line.split(": ",1) for line in fields.splitlines())
        name=values["Package"]
        if name in identity or name not in PACKAGES or values["Architecture"] not in {"all","amd64"}:raise RuntimeError("invalid_release_package_set")
        identity[name]={"version":values["Version"],"architecture":values["Architecture"],"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"file":path.name}
    if set(identity)!=PACKAGES:raise RuntimeError("all_release_packages_required")
    report.mkdir(parents=True,exist_ok=True)
    receipt=report/"install-simulation.json";receipt.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="mkv-install-check-",dir="/tmp") as temporary:
        root=Path(temporary);root.chmod(0o755)
        source=root/"debian.sources";trust=root/"debian-archive-keyring.gpg";shutil.copyfile(keyring,trust)
        source.write_text("Types: deb\nURIs: https://deb.debian.org/debian\nSuites: trixie trixie-updates\nComponents: main\nArchitectures: amd64\nSigned-By: "+str(trust)+"\n\nTypes: deb\nURIs: https://security.debian.org/debian-security\nSuites: trixie-security\nComponents: main\nArchitectures: amd64\nSigned-By: "+str(trust)+"\n")
        config=isolated_config(root,source,trust)
        env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LC_ALL":"C","HOME":str(root),"APT_CONFIG":str(config)}
        options=["-o","APT::Architecture=amd64","-o","Acquire::Check-Valid-Until=true","-o","Acquire::Check-Date=true","-o","Acquire::https::Verify-Peer=true","-o","Acquire::https::Verify-Host=true","-o","Acquire::AllowInsecureRepositories=false","-o","APT::Get::AllowUnauthenticated=false","-o","APT::Update::Error-Mode=any"]
        dump=subprocess.check_output(["apt-config",*options,"dump"],env=env,text=True)
        paths=prove_isolation(dump,root);(report/"apt-config.txt").write_text(dump)
        # No network or simulation is reached until every write path and hook
        # has passed the same isolation check used by the native HTTPS test.
        for name,args in (("update",["update"]),("simulate",["install","--simulate","--no-install-recommends"])):
            if name=="simulate":
                local=root/"packages";local.mkdir()
                for path in packages:shutil.copyfile(path,local/path.name)
                args += [str(local/path.name) for path in packages]
            command=["/usr/bin/apt-get",*options,*args]
            result=subprocess.run(command,env=env,cwd=root,capture_output=True,text=True,timeout=240)
            (report/(name+".log")).write_text(result.stdout+result.stderr)
            if result.returncode:raise RuntimeError("installability_gate_failed:"+name+":"+str(result.returncode))
        value={"schema":1,"target":"debian13-amd64-empty-status","packages":identity,"all_packages_simulated":True,"actual_install":False,"isolated_paths":len(paths),"apt_config_sha256":hashlib.sha256(dump.encode()).hexdigest(),"debian_keyring_sha256":hashlib.sha256(trust.read_bytes()).hexdigest()}
        receipt.write_text(json.dumps(value,indent=2)+"\n")
        return value

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--packages",nargs=len(PACKAGES),type=Path,required=True);p.add_argument("--debian-keyring",type=Path,required=True);p.add_argument("--report-dir",type=Path,required=True)
    a=p.parse_args();print(json.dumps(check(a.packages,a.debian_keyring,a.report_dir)))
if __name__=="__main__":main()
