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
from pathlib import Path, PurePosixPath
import importlib.util
import pwd
import re
import shutil
import subprocess

# The verifier is shared by source builds and the standalone installer payload.
_release_path = Path(__file__).resolve().with_name("simple_release.py")
if not _release_path.is_file():
    _source = Path(__file__).resolve()
    if tuple(parent.name for parent in _source.parents[:3]) != ("debian", "installation", "linux-elevhub"):
        raise FileNotFoundError("shared_release_verifier_required")
    _release_path = _source.parents[3] / "app/simple_release.py"
_release_spec = importlib.util.spec_from_file_location("_mkv_simple_release", _release_path)
_release_module = importlib.util.module_from_spec(_release_spec)
_release_spec.loader.exec_module(_release_module)
PACKAGES = {package: architecture for package, architecture in _release_module.DEBIAN_COMPONENTS.values()}
BASE_PACKAGES = {name: arch for name, arch in PACKAGES.items()
                 if name != _release_module.DEBIAN_COMPONENTS['scratch'][0]}
APT=["/usr/bin/apt-get","-o","APT::Get::AllowUnauthenticated=false","-o","Acquire::AllowInsecureRepositories=false","-o","Acquire::AllowDowngradeToInsecureRepositories=false","-o","Acquire::Check-Valid-Until=true","-o","Acquire::Check-Date=true","-o","APT::Update::Error-Mode=any"]

def strict_json(text):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate_json_key')
            result[key]=value
        return result
    return json.loads(text,object_pairs_hook=unique)


def validate_packages(packages,release_set):
    expected=BASE_PACKAGES if release_set=='base' else PACKAGES if release_set=='legacy' else None
    if expected is None or type(packages) is not list or len(packages)!=len(expected):raise ValueError('incomplete_install_package_set')
    found={}
    for item in packages:
        if type(item) is not dict or set(item)!={'package','architecture','version'}:raise ValueError('invalid_install_package')
        name=item['package'];version=item['version']
        if not isinstance(name,str) or name in found or name not in expected or item['architecture']!=expected[name]:raise ValueError('incomplete_install_package_set')
        if not isinstance(version,str) or not re.fullmatch(r'[0-9][A-Za-z0-9.+:~\-]{0,127}',version):raise ValueError('invalid_package_version')
        floor={'makervaerkstedet-elev':'2.1','makervaerkstedet-updater':'1.1'}.get(name)
        if release_set=='base' and floor and subprocess.run(['/usr/bin/dpkg','--compare-versions',version,'ge',floor],capture_output=True).returncode!=0:raise ValueError('modular_base_version_required')
        found[name]=item['architecture']
    if found!=expected:raise ValueError('incomplete_install_package_set')
    return expected


def check(payload):
    manifest=strict_json((payload/'install-lock.json').read_text())
    if type(manifest) is not dict or type(manifest.get('schema')) is not int:raise ValueError('invalid_install_lock')
    modern=manifest['schema']==2
    fields={'schema','packages','files'}|({'release_set'} if modern else set())
    if set(manifest)!=fields or manifest['schema'] not in (1,2) or (modern and manifest['release_set']!='base'):raise ValueError('invalid_install_lock')
    validate_packages(manifest['packages'],'base' if modern else 'legacy')
    if type(manifest['files']) is not dict or not manifest['files']:raise ValueError('invalid_payload_inventory')
    actual=set()
    for entry in payload.rglob('*'):
        if entry.is_symlink():raise ValueError('unsafe_payload_path')
        if entry.is_file() and entry!=payload/'install-lock.json':actual.add(entry.relative_to(payload).as_posix())
    if actual!=set(manifest['files']):raise ValueError('payload_inventory_mismatch')
    for name,expected in manifest["files"].items():
        if not isinstance(name,str) or not name or PurePosixPath(name).is_absolute() or PurePosixPath(name).as_posix()!=name or '..' in PurePosixPath(name).parts or '\\' in name or ':' in name:raise ValueError('unsafe_payload_path')
        if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('invalid_payload_hash')
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
    units=["makervaerkstedet-enroll.service","makervaerkstedet-firstboot.service","makervaerkstedet-apt-update.timer","elevhub-lokal.service","elevhub-design.service"]
    if manifest['schema']==2:units.append('elevhub-svg-library.service')
    command(["/usr/bin/systemctl","--root=/","enable",*units])
    marker.write_text(json.dumps({"schema":1,"packages":manifest["packages"]})+"\n");marker.chmod(0o600)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload",type=Path,required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args();manifest=check(args.payload)
    if args.apply:apply(args.payload,manifest)
    print(json.dumps({"checked":True,"applied":args.apply,"packages":len(manifest["packages"])}))

if __name__=="__main__":main()
