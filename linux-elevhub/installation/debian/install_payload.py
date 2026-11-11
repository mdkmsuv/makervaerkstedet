#!/usr/bin/env python3
"""Clean-installer payload entrypoint. --check is inert; --apply is installer-only.

Never used for an existing-machine program update. No identities, private keys,
student projects or school credentials are supplied by the generic image.
"""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess

PACKAGES={"makervaerkstedet-elev":"all","makervaerkstedet-scratch":"all","makervaerkstedet-matchbox":"amd64","makervaerkstedet-updater":"amd64"}
APT=["/usr/bin/apt-get","-o","APT::Get::AllowUnauthenticated=false","-o","Acquire::AllowInsecureRepositories=false","-o","Acquire::AllowDowngradeToInsecureRepositories=false","-o","Acquire::Check-Valid-Until=true","-o","Acquire::Check-Date=true","-o","APT::Update::Error-Mode=any"]

def check(payload):
    manifest=json.loads((payload/"install-lock.json").read_text())
    if set(manifest)!={"schema","packages","files"} or manifest["schema"]!=1:raise ValueError("invalid_install_lock")
    if {p["package"]:p["architecture"] for p in manifest["packages"]}!=PACKAGES:raise ValueError("incomplete_install_package_set")
    for p in manifest["packages"]:
        if not re.fullmatch(r"[0-9][A-Za-z0-9.+:~\-]{0,127}",p["version"]):raise ValueError("invalid_package_version")
    for name,expected in manifest["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:raise ValueError("unsafe_payload_path")
        file=payload/name
        if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=expected:raise ValueError("payload_hash_mismatch")
    return manifest

def command(argv):
    subprocess.run(argv,check=True,env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LC_ALL":"C","DEBIAN_FRONTEND":"noninteractive"},stdin=subprocess.DEVNULL)

def apply(payload,manifest):
    if os.geteuid()!=0:raise ValueError("root_required")
    marker=Path("/var/lib/makervaerkstedet-updater/clean-install-complete.json")
    if marker.exists():raise ValueError("clean_install_already_complete")
    if Path("/etc/elevhub/enhed.json").exists():raise ValueError("existing_enrollment_not_clean_install")
    projects=Path("/home/elev/Projekter")
    if projects.exists() and any(projects.iterdir()):raise ValueError("existing_project_data_not_clean_install")
    source=Path("/etc/apt/sources.list.d/makervaerkstedet-install.sources")
    if source.exists():raise ValueError("install_source_exists")
    key=Path("/etc/apt/keyrings/makervaerkstedet.gpg");key.parent.mkdir(parents=True,exist_ok=True)
    public=(payload/"trust/makervaerkstedet.gpg").read_bytes()
    if hashlib.sha256(public).hexdigest()!="5b9ee5f0ca01ff2d52c311e99f71b6eefd69ed8d9149ef7cef5d80cf89a27f1f":raise ValueError("public_keyring_hash_mismatch")
    key.write_bytes(public);key.chmod(0o644)
    source.parent.mkdir(parents=True,exist_ok=True)
    source.write_text("Types: deb\nURIs: file:"+str(payload/"apt")+"/\nSuites: ./\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/makervaerkstedet.gpg\n")
    try:
        command([*APT,"update"])
        command([*APT,"--yes","--no-remove","--no-install-recommends","install",*[p["package"]+"="+p["version"] for p in manifest["packages"]]])
    finally:source.unlink(missing_ok=True)
    for item in manifest["packages"]:
        value=subprocess.check_output(["/usr/bin/dpkg-query","-W","-f=${Status}\t${Version}\t${Architecture}",item["package"]],text=True)
        if value!="install ok installed\t"+item["version"]+"\t"+item["architecture"]:raise ValueError("installed_package_mismatch")
    # Reuse the existing local OS user/session, without any new pupil login.
    user=pwd.getpwnam("elev")
    command(["/usr/sbin/usermod","--lock","-a","-G","audio,video,plugdev,dialout","elev"])
    if "elev" in grp.getgrnam("sudo").gr_mem:command(["/usr/bin/gpasswd","-d","elev","sudo"])
    projects.mkdir(parents=True,exist_ok=True);os.chown(projects,user.pw_uid,user.pw_gid)
    profile=Path("/home/elev/.bash_profile")
    text='if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then\n    exec startx /usr/local/lib/elevhub/kiosk-session -- -nolisten tcp\nfi\n'
    if profile.exists() and profile.read_text()!=text:raise ValueError("unexpected_existing_login_profile")
    profile.write_text(text);profile.chmod(0o644)
    getty=Path("/etc/systemd/system/getty@tty1.service.d/autologin.conf");getty.parent.mkdir(parents=True,exist_ok=True)
    getty.write_text("[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin elev --noclear %I $TERM\n")
    power=Path("/etc/sudoers.d/elevhub-power")
    power.write_text("elev ALL=(root) NOPASSWD: /usr/local/sbin/elevhub-power poweroff, /usr/local/sbin/elevhub-power reboot\n");power.chmod(0o440)
    command(["/usr/sbin/visudo","-c","-f",str(power)])
    enrollment=Path("/usr/lib/makervaerkstedet/enrollment")
    enrollment.mkdir(parents=True,exist_ok=True)
    for name in ("onboarding.py","profile_validation.py","enroll_device.py"):
        destination=enrollment/name
        if destination.exists():raise ValueError("enrollment_program_already_exists")
        shutil.copyfile(payload/"enrollment"/name,destination);destination.chmod(0o644)
    unit=Path("/etc/systemd/system/makervaerkstedet-enroll.service")
    if unit.exists():raise ValueError("enrollment_unit_already_exists")
    shutil.copyfile(payload/"enrollment/makervaerkstedet-enroll.service",unit);unit.chmod(0o644)
    command(["/usr/bin/python3","-B","/usr/lib/makervaerkstedet/updater/template_activation.py"])
    # --root writes boot links only. Never start a service in the installer.
    command(["/usr/bin/systemctl","--root=/","enable","makervaerkstedet-enroll.service","makervaerkstedet-firstboot.service","makervaerkstedet-apt-update.timer","elevhub-lokal.service","elevhub-design.service"])
    marker.write_text(json.dumps({"schema":1,"packages":manifest["packages"]})+"\n");marker.chmod(0o600)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload",type=Path,required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args();manifest=check(args.payload)
    if args.apply:apply(args.payload,manifest)
    print(json.dumps({"checked":True,"applied":args.apply,"packages":len(manifest["packages"])}))

if __name__=="__main__":main()
