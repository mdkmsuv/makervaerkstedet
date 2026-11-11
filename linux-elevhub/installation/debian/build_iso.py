#!/usr/bin/env python3
"""Build a public Debian13 netinstall ISO; NEVER boot/install it.

The existing installer/disk guard is reused. Package installation is deferred
until an operator boots the image and explicitly confirms wiping the target.
Debian mirror access is required. No school identities/credentials are embedded.
"""
import argparse,ast,gzip,hashlib,io,json,os,re,shutil,subprocess,tarfile,tempfile,sys
from pathlib import Path

BASE_SHA="a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355"
KEY_SHA="5b9ee5f0ca01ff2d52c311e99f71b6eefd69ed8d9149ef7cef5d80cf89a27f1f"
PACKAGES={"makervaerkstedet-elev":"all","makervaerkstedet-scratch":"all","makervaerkstedet-matchbox":"amd64","makervaerkstedet-updater":"amd64"}
EXTRA_DEBIAN=("openssh-client","libnss3-tools","ca-certificates","console-setup","keyboard-configuration")

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def validated_repository(repo,receipt,receipt_sha):
    if sha(receipt)!=receipt_sha:raise ValueError("release_gate_receipt_hash_mismatch")
    gate=json.loads(receipt.read_text())
    if gate.get("schema")!=1 or gate.get("target")!="debian13-amd64-empty-status" or gate.get("all_packages_simulated") is not True or gate.get("actual_install") is not False or set(gate["packages"])!=set(PACKAGES):raise ValueError("release_package_simulation_required")
    if sha(repo/"makervaerkstedet.gpg")!=KEY_SHA:raise ValueError("wrong_repository_public_key")
    release=(repo/"Release").read_text()
    if re.search(r"^Valid-Until:",release,re.M):raise ValueError("own_repository_must_not_expire")
    hashes={name:(h,int(size)) for h,size,name in re.findall(r"^ ([0-9a-f]{64}) +([0-9]+) +(\S+)$",release,re.M)}
    for name in ("Packages","Packages.gz"):
        if name not in hashes or (sha(repo/name),(repo/name).stat().st_size)!=hashes[name]:raise ValueError("repository_index_hash_mismatch")
    index=(repo/"Packages").read_text()
    if gzip.decompress((repo/"Packages.gz").read_bytes())!=index.encode():raise ValueError("compressed_index_mismatch")
    found={};artifacts=[]
    for block in index.strip().split("\n\n"):
        values=dict(line.split(": ",1) for line in block.splitlines() if ": " in line and not line.startswith(" "))
        name=values.get("Package")
        if name not in PACKAGES:raise ValueError("unexpected_package_in_iso_repository")
        target=gate["packages"][name]
        filename="pool/"+target["file"]
        if values.get("Version")!=target["version"] or values.get("Architecture")!=PACKAGES[name] or target["architecture"]!=PACKAGES[name] or values.get("SHA256")!=target["sha256"] or values.get("Filename")!=filename:raise ValueError("package_gate_repository_mismatch")
        if name in found:raise ValueError("ambiguous_iso_package_version")
        if not re.fullmatch(r"pool/[a-z0-9+_.~:-]+\.deb",filename):raise ValueError("unsafe_package_filename")
        path=repo/filename
        if path.is_symlink() or sha(path)!=target["sha256"] or path.stat().st_size!=int(values["Size"]):raise ValueError("package_bytes_mismatch")
        found[name]=dict(package=name,version=target["version"],architecture=target["architecture"])
        artifacts.append(filename)
    if set(found)!=set(PACKAGES):raise ValueError("missing_iso_package")
    files=["InRelease","Release","Release.gpg","Packages","Packages.gz","makervaerkstedet.gpg",*artifacts]
    # Include native Acquire-By-Hash indices, never arbitrary repository extras.
    for path in sorted((repo/"by-hash/SHA256").glob("*")):
        if not re.fullmatch("[0-9a-f]{64}",path.name) or path.is_symlink() or sha(path)!=path.name:raise ValueError("invalid_by_hash_object")
        files.append(path.relative_to(repo).as_posix())
    for name in files:
        path=repo/name
        if not path.is_file() or path.is_symlink():raise ValueError("regular_public_repository_files_required")
    return list(found.values()),files

def pure_validator(source):
    text=source.read_text();tree=ast.parse(text);parts=["import ipaddress,re\n"]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {"NAME","TOKEN","THEMES"} for t in node.targets):parts.append(ast.get_source_segment(text,node)+"\n")
        elif isinstance(node,ast.FunctionDef) and node.name=="validate_profile":parts.append(ast.get_source_segment(text,node)+"\n")
    if len(parts)!=5:raise ValueError("existing_profile_validator_shape_changed")
    return "\n".join(parts)

def preseed(source,payload_sha,debian_packages):
    text=source.read_text()
    # Preserve the existing Debian locale/network/grub setup. Replace the old
    # drift-account/SSH-key deployment and fail-unsafe fixed disk default.
    start=text.index("### SSH er klar")
    finish=text.index("### Afslut:",start)
    late=("### Generic signed-payload install, only AFTER disk confirmation\n"
      "d-i preseed/late_command string \\\n"
      "    echo '"+payload_sha+"  /cdrom/elevhub/payload.tar.gz' | sha256sum -c - && \\\n"
      "    mkdir -p /target/opt/makervaerkstedet-install && \\\n"
      "    tar -xzf /cdrom/elevhub/payload.tar.gz -C /target/opt/makervaerkstedet-install && \\\n"
      "    in-target python3 -B /opt/makervaerkstedet-install/apt-firstboot.py --payload /opt/makervaerkstedet-install --apply\n\n")
    text=text[:start]+late+text[finish:]
    lines=[]
    replacements={"d-i passwd/user-fullname":"d-i passwd/user-fullname string Elev","d-i passwd/username":"d-i passwd/username string elev","d-i partman-auto/disk":"d-i partman-auto/disk string /dev/elevhub-unselected","tasksel tasksel/first":"tasksel tasksel/first multiselect","d-i pkgsel/include":"d-i pkgsel/include string "+" ".join(debian_packages),"d-i pkgsel/upgrade":"d-i pkgsel/upgrade select none"}
    for line in text.splitlines():
        if line.startswith("#"):continue
        for key,value in replacements.items():
            if line.startswith(key+" "):line=value;break
        lines.append(line.replace("@@NAVN@@","makervaerksted"))
    result="\n".join(lines)+"\n"
    if "@@" in result or "NOPASSWD: ALL" in result or "authorized_keys" in result:raise ValueError("generic_preseed_contains_identity")
    return "# Generic clean install; selected disk is erased ONLY after diskguard confirmation.\n"+result

def verify_recipe(packages,repo,gate,report):
    sys.path.insert(0,str(Path(__file__).parents[2]/"packaging"))
    from apt_sandbox import isolated_config,prove_isolation
    with tempfile.TemporaryDirectory(prefix="mkv-iso-recipe-",dir="/tmp") as temporary:
        root=Path(temporary);root.chmod(0o755)
        trust=root/"debian.gpg";shutil.copyfile("/usr/share/keyrings/debian-archive-keyring.gpg",trust)
        source=root/"debian.sources"
        source.write_text("Types: deb\nURIs: https://deb.debian.org/debian\nSuites: trixie trixie-updates\nComponents: main\nArchitectures: amd64\nSigned-By: "+str(trust)+"\n\nTypes: deb\nURIs: https://security.debian.org/debian-security\nSuites: trixie-security\nComponents: main\nArchitectures: amd64\nSigned-By: "+str(trust)+"\n")
        config=isolated_config(root,source,trust)
        env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LC_ALL":"C","HOME":str(root),"APT_CONFIG":str(config)}
        options=["-o","Acquire::Check-Valid-Until=true","-o","Acquire::Check-Date=true","-o","Acquire::https::Verify-Peer=true","-o","Acquire::https::Verify-Host=true","-o","Acquire::AllowInsecureRepositories=false","-o","APT::Get::AllowUnauthenticated=false","-o","APT::Update::Error-Mode=any"]
        dump=subprocess.check_output(["apt-config",*options,"dump"],text=True,env=env)
        paths=prove_isolation(dump,root);(report/"recipe-apt-config.txt").write_text(dump)
        archives=[]
        for entry in gate["packages"].values():
            target=root/entry["file"];shutil.copyfile(repo/"pool"/entry["file"],target);archives.append(str(target))
        for mode,argv in (("update",["update"]),("simulate",["install","--simulate","--no-install-recommends",*archives,*packages])):
            command=["/usr/bin/apt-get",*options,*argv]
            result=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=240)
            (report/("recipe-"+mode+".log")).write_text(result.stdout+result.stderr)
            if result.returncode:raise ValueError("iso_recipe_dependency_gate_failed:"+mode)
        (report/"recipe-gate.json").write_text(json.dumps({"packages":packages,"program_packages":gate["packages"],"target":"debian13-empty-status","isolated_paths":len(paths),"simulated":True,"installed":False},indent=2)+"\n")

def build(args):
    if sha(args.base_iso)!=BASE_SHA:raise ValueError("unverified_base_iso")
    if args.output.exists() or args.output.with_suffix(".iso.part").exists():raise ValueError("iso_output_exists")
    packages,repo_files=validated_repository(args.repo,args.release_receipt,args.release_receipt_sha256)
    args.evidence_dir.mkdir(parents=True,exist_ok=True)
    source=args.source_root;installation=source/"linux-elevhub/installation";here=Path(__file__).parent
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mkv-iso-",dir="/tmp") as temporary:
        root=Path(temporary);payload=root/"payload";payload.mkdir();extra=root/"extra";extra.mkdir()
        provenance={}
        def copy(src,dst):
            if src.is_symlink() or not src.is_file():raise ValueError("explicit_regular_source_required")
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
            if src.suffix in {".sh",".templates"}:dst.write_bytes(dst.read_bytes().replace(b"\r\n",b"\n"))
            dst.chmod(0o644);provenance[str(src.relative_to(source)) if src.is_relative_to(source) else src.name]=sha(src)
        for name in repo_files:copy(args.repo/name,payload/"apt"/name)
        copy(args.repo/"makervaerkstedet.gpg",payload/"trust/makervaerkstedet.gpg")
        copy(here/"install_payload.py",payload/"apt-firstboot.py")
        copy(here/"bootstrap_existing.py",payload/"bootstrap_existing.py")
        copy(source/"linux-elevhub/packaging/apt_sandbox.py",payload/"apt_sandbox.py")
        copy(here/"enroll_device.py",payload/"enrollment/enroll_device.py")
        copy(here/"makervaerkstedet-enroll.service",payload/"enrollment/makervaerkstedet-enroll.service")
        copy(installation/"onboarding.py",payload/"enrollment/onboarding.py")
        copy(installation/"onboarding.py",extra/"onboarding.py")
        (payload/"enrollment/profile_validation.py").write_text(pure_validator(installation/"firstboot.py"))
        provenance["linux-elevhub/installation/firstboot.py"]=sha(installation/"firstboot.py")
        lock={"schema":1,"packages":packages,"files":{p.relative_to(payload).as_posix():sha(p) for p in sorted(payload.rglob("*")) if p.is_file()}}
        (payload/"install-lock.json").write_text(json.dumps(lock,indent=2)+"\n")
        # Exercise the actual inert entrypoint; --apply is NEVER called here.
        checked=subprocess.run(["python3","-B",str(payload/"apt-firstboot.py"),"--payload",str(payload)],check=True,capture_output=True,text=True)
        (args.evidence_dir/"payload-check.json").write_text(checked.stdout)
        with tarfile.open(extra/"payload.tar.gz","w:gz") as archive:
            for path in sorted(payload.rglob("*")):archive.add(path,arcname=path.relative_to(payload).as_posix(),recursive=False)
        payload_sha=sha(extra/"payload.tar.gz")
        for name in ("partman-disk-guard.sh","select-install-disk.sh","disk-selection.templates"):copy(installation/name,extra/name)
        for name in ("partman-disk-guard.sh","select-install-disk.sh"):
            subprocess.run(["/bin/sh","-n",str(extra/name)],check=True)
        core=next(p for p in packages if p["package"]=="makervaerkstedet-elev")
        gate=json.loads(args.release_receipt.read_text());corefile=args.repo/"pool"/gate["packages"][core["package"]]["file"]
        deps=subprocess.check_output(["dpkg-deb","--field",str(corefile),"Depends"],text=True)
        debian_packages=sorted(set([d.strip().split(" ")[0] for d in deps.split(",") if not d.strip().startswith("makervaerkstedet-")]+list(EXTRA_DEBIAN)))
        verify_recipe(debian_packages,args.repo,gate,args.evidence_dir)
        cfg=root/"preseed.cfg";cfg.write_text(preseed(source/"linux-elevhub/testmiljoe/vm/preseed-usb.skabelon.cfg",payload_sha,debian_packages))
        copy(cfg,args.evidence_dir/"preseed.cfg")
        helper=source/"linux-elevhub/testmiljoe/vaert/byg-usb-iso.sh"
        script=root/"build-hybrid.sh"
        script.write_text(helper.read_text().replace("menu label ^ELEVHUB automatisk installation","menu label ^Makervaerkstedet installation - SLETTER VALGT DISK"))
        provenance["linux-elevhub/testmiljoe/vaert/byg-usb-iso.sh"]=sha(helper)
        partial=args.output.with_suffix(".iso.part")
        log=args.evidence_dir/"xorriso-build.log"
        with log.open("w") as stream:
            subprocess.run(["/bin/bash",str(script),str(args.base_iso),str(cfg),str(partial),"Makervaerkstedet",str(extra)],check=True,stdout=stream,stderr=subprocess.STDOUT,env={**os.environ,"TMPDIR":str(root)})
        # Read the built image back through xorriso, including the modified initrd.
        extracted=root/"extracted";extracted.mkdir()
        subprocess.run(["xorriso","-osirrox","on","-indev",str(partial),"-extract","/elevhub",str(extracted/"elevhub"),"-extract","/install.amd/initrd.gz",str(extracted/"initrd.gz")],check=True,capture_output=True)
        if sha(extracted/"elevhub/payload.tar.gz")!=payload_sha:raise ValueError("iso_payload_hash_mismatch")
        initrd=gzip.decompress((extracted/"initrd.gz").read_bytes())
        for expected in (cfg.read_bytes(),(extra/"partman-disk-guard.sh").read_bytes()):
            if expected not in initrd:raise ValueError("iso_initrd_expected_input_missing")
        boot=subprocess.check_output(["xorriso","-indev",str(partial),"-report_el_torito","plain"],stderr=subprocess.STDOUT,text=True)
        if "BIOS" not in boot or "UEFI" not in boot:raise ValueError("missing_hybrid_boot_entries")
        (args.evidence_dir/"boot-records.txt").write_text(boot)
        os.replace(partial,args.output)
        receipt={"schema":1,"output":args.output.name,"sha256":sha(args.output),"bytes":args.output.stat().st_size,"base_iso_sha256":BASE_SHA,"release_gate_sha256":args.release_receipt_sha256,"payload_sha256":payload_sha,"packages":packages,"extra_debian_dependencies":list(EXTRA_DEBIAN),"disk_guard_sha256":{name:sha(extra/name) for name in ("partman-disk-guard.sh","select-install-disk.sh","disk-selection.templates")},"shell_line_endings":"LF","mirror_required":True,"school_credentials":False,"boot_tested":False,"installed":False,"public_inputs":provenance}
        (args.evidence_dir/"iso-build.json").write_text(json.dumps(receipt,indent=2)+"\n")
        print(json.dumps({k:receipt[k] for k in ("output","sha256","bytes","mirror_required","boot_tested","installed")}))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("base-iso","repo","release-receipt","output","evidence-dir"):p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--release-receipt-sha256",required=True);p.add_argument("--source-root",type=Path,default=Path(__file__).parents[3])
    build(p.parse_args())
if __name__=="__main__":main()
