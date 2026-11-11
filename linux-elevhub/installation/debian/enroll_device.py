#!/usr/bin/env python3
"""Optional one-time setup using the EXISTING school USB/ticket enrollment.

No school contact is made unless the operator explicitly chooses setup. Skipping
or timing out starts local workshops; no pupil login/storage model is introduced.
"""
import importlib.util
import json
import os
import pwd
from pathlib import Path
import select
import subprocess
import sys

sys.path.insert(0,"/usr/lib/makervaerkstedet/updater")
from template_activation import activate,atomic,json_bytes,protected,default_config
import onboarding

HERE=Path(__file__).resolve().parent

def validator():
    spec=importlib.util.spec_from_file_location("profile_validation",HERE/"profile_validation.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.validate_profile

def trust_school_ca(root,ca,runner):
    # Same OS + Chromium NSS trust as the existing kiosk role. Called only
    # after explicit enrollment on the installed machine, never by ISO build.
    trust=root/"usr/local/share/ca-certificates/elevhub-ca.crt"
    atomic(trust,ca.encode(),0o644)
    runner(["/usr/sbin/update-ca-certificates"])
    user=pwd.getpwnam("elev")
    nss=root/"home/elev/.pki/nssdb"
    nss.mkdir(parents=True,exist_ok=True)
    for path in (nss.parent,nss):os.chown(path,user.pw_uid,user.pw_gid)
    database="sql:"+str(nss)
    prefix=["/usr/sbin/runuser","-u","elev","--","/usr/bin/certutil","-d",database]
    if not (nss/"cert9.db").exists():runner([*prefix,"-N","--empty-password"])
    runner([*prefix,"-A","-n","Elevhub-CA","-t","C,,","-i",str(trust)])

def command(argv):
    subprocess.run(argv,check=True,stdin=subprocess.DEVNULL)

def apply_profile(root=Path("/"),*,trust_runner=None):
    profile=root/"etc/elevhub-install"
    settings=json.loads(protected(profile/"settings.json",private=True).read_text())
    device=json.loads(protected(profile/"device.json",private=True).read_text())
    ca=protected(profile/"ca.crt",private=True).read_text()
    public=protected(profile/"admin.pub",private=True).read_text()
    validator()(settings,device,ca,public)
    target=root/"etc/elevhub/enhed.json"
    identity={"computer":device["computer"],"token":device["token"]}
    if target.exists() and json.loads(protected(target,private=True).read_text())!=identity:
        raise ValueError("existing_identity_must_not_be_replaced")
    path=root/"etc/elevhub/kiosk.json"
    config=json.loads(protected(path).read_text()) if path.exists() else default_config()
    config.pop("token",None)
    config.update(computer=device["computer"],computer_visning=device["computer"],server=settings["server"],
        hub="https://"+settings["server"]+":8773",protokol="https",ui_token=device["ui_token"],
        theme=settings.get("theme","red"),school_name=settings.get("school_name",""))
    atomic(path,json_bytes(config),0o644)
    atomic(root/"etc/elevhub/ca.pem",ca.encode(),0o644)
    # Root identity is committed last; firstboot can finish activation offline
    # after a crash without consuming another enrollment ticket.
    if trust_runner is not None:trust_school_ca(root,ca,trust_runner)
    atomic(target,json_bytes(identity),0o600)
    result=activate(root)
    return {"enrolled":True,"updater_configured":result["paired"]}

def main():
    if os.geteuid()!=0:raise ValueError("root_required")
    if Path("/etc/elevhub/enhed.json").exists():return
    if not Path("/etc/elevhub-install/settings.json").exists():
        print("\nSkoleopsætning: tryk S og Enter inden 10 sekunder.\nEnter eller vent: start lokale værksteder uden skoleforbindelse.\n",flush=True)
        ready,_,_=select.select([sys.stdin],[],[],10)
        if not ready or sys.stdin.readline().strip().lower()!="s":return
        try:onboarding.main(firstboot_path=HERE/"profile_validation.py")
        except KeyboardInterrupt:return
    result=apply_profile(trust_runner=command)
    print("Tilmeldt. Læreren vælger student / debian-amd64 i opdateringspanelet.",flush=True)
    print(json.dumps(result))

if __name__=="__main__":main()
