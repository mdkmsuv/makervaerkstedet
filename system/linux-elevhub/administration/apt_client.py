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
        self.identity_sha256=digest(json_bytes({'schema':1,'origin':[parsed.scheme,parsed.hostname.lower(),parsed.port or 443],
            'computer':computer,'token_sha256':digest(token.encode())}))
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))))
    def request(self,path,limit,body=None):
        require(path.startswith("/") and not path.startswith("//") and "\\" not in path,"unsafe_server_path")
        headers={"Accept":"application/json"}
        if body is not None:
            require(path in (API,'/api/device/apps'),"fixed_poll_path_required")
            headers.update({"Authorization":"Bearer "+self.token,"X-Elevhub-Computer":self.computer})
        elif (path == '/api/device/school-profile'
                or re.fullmatch(r'/api/device/school-profile/assets/[a-f0-9]{64}\.(?:png|ttf|css|svg|jpg|jpeg|webp|woff|woff2|otf|ico)',path)
                or re.fullmatch(r'/api/device/migration/[a-f0-9]{64}/[1-9][0-9]{0,18}',path)):
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
    def poll_apps(self,body):return strict_json(self.request('/api/device/apps',MAX_MANIFEST,body))
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
    def __init__(self,options=None,*,app_root=Path('/usr/share/makervaerkstedet/apps')):self.options=options;self.app_root=app_root
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
    def app_inventory(self,descriptors):
        from app_catalog import validate_app_descriptor
        result=[]
        for descriptor in descriptors:
            app=validate_app_descriptor(descriptor)
            p=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${Status}\t${Version}\t${Architecture}',app['package']],
                capture_output=True,text=True,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
            if p.returncode:
                require(p.returncode==1 and ('no packages found matching '+app['package']) in p.stderr,'app_inventory_failed')
                status='absent';current=None
            else:
                parts=p.stdout.split('\t')
                require(len(parts)==3,'app_inventory_failed')
                status='installed' if parts[0]=='install ok installed' else ('absent' if parts[0] in ('deinstall ok config-files','unknown ok not-installed') else 'incomplete')
                current=version(parts[1]) if status!='absent' else None
                require(status=='absent' or parts[2]==app['architecture'],'installed_architecture_mismatch')
            result.append({'id':app['id'],'package':app['package'],'version':current,'state':status})
        return result
    def remove_app(self,descriptor):
        from app_operations import removal_plan
        from app_catalog import validate_app_descriptor
        app=validate_app_descriptor(descriptor)
        args=[*self.apt(),'--yes','-o','APT::Get::AutomaticRemove=false']
        simulated=self.call([*args,'--simulate','remove',app['package']])
        removal_plan(simulated,app)
        self.call([*args,'remove',app['package']])
    def app_matches(self,descriptor):
        from app_catalog import validate_app_descriptor
        app=validate_app_descriptor(descriptor)
        path=plain(self.app_root/app['id']/'app.json')
        if not path.is_file():return False
        protected(path)
        if path.stat().st_size>16384:return False
        try:return validate_app_descriptor(strict_json(path.read_bytes()))==app
        except ValueError:return False
    def app_absent(self,descriptor):
        from app_catalog import validate_app_descriptor
        app=validate_app_descriptor(descriptor)
        return not plain(self.app_root/app['id']/'app.json').exists()
    def preflight_app(self,descriptor,*,removed=False):
        from app_operations import installed_descriptors
        from app_catalog import validate_app_descriptor,project_extensions_overlap
        app=validate_app_descriptor(descriptor);others=[]
        for existing in installed_descriptors(self.app_root,issues=[]):
            if existing['id']==app['id']:continue
            inventory=self.app_inventory([existing])[0]
            if inventory['state']=='absent':continue
            if not removed:
                require(urlsplit(existing['start']).port!=urlsplit(app['start']).port,'app_port_conflict')
                a,b=app['project'],existing['project']
                require(a is None or b is None or (a['kind']!=b['kind'] and not project_extensions_overlap(a['extension'],b['extension'])),
                        'app_project_format_conflict')
            others.append(existing)
        # Preserve the set of currently responsive apps across the shared service
        # restart. Already broken apps are reported separately by inventory.
        from concurrent.futures import ThreadPoolExecutor
        from runtime_checks import read_health
        def ready(existing):
            from app_catalog import GATEWAY_CAPABILITIES
            caps=existing['capabilities'];network=bool(existing['aflevering']) or any(c in GATEWAY_CAPABILITIES for c in caps['required']+caps['optional'])
            path='/api/runtime/health' if network else '/_elevhub/health'
            try:
                value=read_health('http://127.0.0.1:'+str(urlsplit(existing['start']).port)+path)
                if not isinstance(value,dict):return False
                return value.get('app')==existing['id'] and value.get('app_version')==existing['version'] and value.get('local') is True
            except (OSError,ValueError):return False
        with ThreadPoolExecutor(max_workers=16) as pool:
            self.app_survivors=[existing for existing,ok in zip(others,pool.map(ready,others)) if ok]
        return self.app_survivors
    def install_app(self,files,descriptor):
        from app_archive import inspect_deb
        from app_operations import install_plan
        require(len(files)==1,'one_app_archive_required')
        archive=inspect_deb(files[0],descriptor)
        self.check_app_space([archive])
        args=[*self.apt(),'--yes','--no-remove','--no-install-recommends','--reinstall',
              '-o','APT::Get::AutomaticRemove=false','-o','Dpkg::Options::=--force-confold']
        targets=[str(path) for path in files]
        install_plan(self.call([*args,'--simulate','install',*targets]),descriptor)
        self.call([*args,'install',*targets])
    def check_app_space(self,archives):
        volumes={}
        for archive in archives:
            for key,allocation in archive['allocation'].items():
                if not allocation['files'] and not allocation['directories']:continue
                target=self.app_root if key=='app' else Path('/usr/local/share/elevhub/apps/scratch')
                probe=plain(target)
                while not probe.exists():probe=probe.parent
                volume=probe.stat().st_dev;stats=os.statvfs(probe)
                # Account for all apps sharing a volume, block rounding and
                # dpkg's temporary copies before the single native transaction.
                estimate=allocation['bytes']+(allocation['files']+allocation['directories'])*stats.f_frsize
                item=volumes.setdefault(volume,{'path':probe,'bytes':0});item['bytes']+=2*estimate
        for item in volumes.values():
            require(shutil.disk_usage(item['path']).free>=item['bytes']+128*1024**2,'insufficient_install_space')
    def install_migration(self,files,descriptors,targets):
        from app_archive import inspect_deb
        from migration_space import check_migration_space
        indexed={app['package']:app for app in descriptors};survivors={}
        for app in descriptors:
            for survivor in self.preflight_app(app):survivors[survivor['id']]=survivor
        for file in files:
            package=self.call(['/usr/bin/dpkg-deb','--field',str(file),'Package']).strip()
            if package in indexed:inspect_deb(file,indexed[package])
        self.migration_space=check_migration_space(files)
        self.migration_survivors=[value for key,value in survivors.items() if key not in {a['id'] for a in descriptors}]
        self.install(files,False,migration_targets=targets)
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
    def install(self,files,rollback,*,migration_targets=None):
        flags=["--yes","--no-remove","--no-install-recommends","-o","Dpkg::Options::=--force-confold",
               '-o','APT::Get::AutomaticRemove=false']
        if rollback:flags.append("--allow-downgrades")
        if migration_targets is not None:flags.append('--reinstall')
        targets=[str(p) for p in files]
        # Native apt decides whether installed OS dependencies and the school
        # repository suffice. Keep exactly this scope for simulation and apply;
        # unused system indices cannot supply stale/expired dependency metadata.
        args=[*self.apt(),*flags]
        try:
            simulation=self.call([*args,"--simulate","install",*targets])
        except UpdateError as error:
            if str(error)!="command_failed":raise
            # New/changed OS dependencies require successful native validation
            # of all configured sources. Never ignore TLS/signature/expiry errors
            # or retry a partially executed installation.
            self.call([*self.apt(system_dependencies=True),"update"])
            args=[*self.apt(system_dependencies=True),*flags]
            simulation=self.call([*args,"--simulate","install",*targets])
        if migration_targets is not None:
            from app_migration import install_plan
            install_plan(simulation,migration_targets)
        self.call([*args,"install",*targets])
    def activate_migration(self,core_version,descriptors):
        apps={app['id']:app for app in [*getattr(self,'migration_survivors',[]),*descriptors]}
        self.activate(core_version,apps=[{**app,'port':urlsplit(app['start']).port} for app in apps.values()])
    def activate_app(self,core_version,descriptor,*,removed=False):
        app={**descriptor,'port':urlsplit(descriptor['start']).port}
        survivors=[{**item,'port':urlsplit(item['start']).port} for item in getattr(self,'app_survivors',[])]
        return self.activate(core_version,apps=survivors+([] if removed else [app]))
    def activate(self,core_version,*,apps=None):
        # Use the newly installed activator after self-update, including its
        # publisher keyring pin, rather than stale imported process code.
        self.call(["/usr/bin/python3","-B",str(Path(__file__).with_name("template_activation.py"))])
        # Fixed local services; no physical printer/laser/Beam Studio operation.
        self.call(["/usr/bin/systemctl","daemon-reload"])
        from app_catalog import compare_versions
        if compare_versions(core_version,'2.1.0~')>=0:
            self.call(["/usr/bin/systemctl","enable","--now","elevhub-svg-library.service"])
        self.call(["/usr/bin/systemctl","try-restart","elevhub-lokal.service","elevhub-design.service","elevhub-regler.service","getty@tty1.service"])
        try:
            if apps is None and compare_versions(core_version,'2.1.0~')<0:
                scratch=any(item['component']=='scratch' for item in self.installed())
                wait_ready(core_version,lambda argv:self.call(argv,timeout=5),legacy_scratch=scratch)
            elif apps is None:wait_ready(core_version,lambda argv:self.call(argv,timeout=5))
            else:wait_ready(core_version,lambda argv:self.call(argv,timeout=5),apps=apps)
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
        require(type(self.ledger) is dict and set(self.ledger) in (
            {'schema','jobs','outbox','highwater'},{'schema','jobs','outbox','highwater','identity_sha256'}),'invalid_ledger')
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
        from identity_state import scoped_ledger
        scoped=scoped_ledger(self.state,self.ledger,getattr(self.transport,'identity_sha256',None),kind='core')
        if scoped is not self.ledger:self.ledger=scoped;self.save()
        from app_migration import (Migration,needed,guard_bridge,at_least,MODULAR,
                                   completed_apps,confirm_completed)
        installed=self.commands.installed()
        current={i["component"]:i["version"] for i in installed}
        pending=self.ledger["outbox"][0] if self.ledger["outbox"] else None
        if pending and pending["state"]=="succeeded":
            job=self.ledger["jobs"].get(pending["component"]+":"+str(pending["revision"]))
            if not job or current.get(pending["component"])!=job["version"]:
                pending["state"]="failed";pending["code"]="installed_version_mismatch";self.save()
            elif job.get('migration') and job['migration']['phase']!='acknowledged' and not completed_apps(self,job):
                pending['state']='failed';pending['code']='migration_app_state_mismatch';self.save()
        body={"schema":1,"installed":installed,"result":{k:v for k,v in pending.items() if k!="component"} if pending else None}
        response=validate_response(self.transport.poll(body))
        if pending and pending['state']=='succeeded':
            job=self.ledger['jobs'].get(pending['component']+':'+str(pending['revision']))
            if job and job.get('migration') and job['migration']['phase']!='acknowledged':
                job['migration']['phase']='reported';self.save()
        for job in self.ledger['jobs'].values():
            if job.get('migration'):confirm_completed(self,job,current)
        self.migration_pending=any(job.get('migration') and job['migration']['phase'] not in ('acknowledged','superseded')
                                   for job in self.ledger['jobs'].values())
        # Schema1 Scratch desires belong to the bundled generation. Once the
        # split core is installed only the app journal may select/remove it.
        from app_catalog import compare_versions
        if ('studentcore' in current and compare_versions(current['studentcore'],'2.1.0~')>=0
                or any(o['component']=='studentcore' and at_least(o['version'],MODULAR) for o in response['desired'])):
            response['desired']=[o for o in response['desired'] if o['component']!='scratch']
        if pending:self.ledger["outbox"].pop(0);self.save()
        plan=[];bad=False;migrations={}
        for offer in response["desired"]:
            c=offer["component"];revision=offer["revision"];key=c+":"+str(revision)
            old=self.ledger["jobs"].get(key)
            if old and old['manifest_sha256']!=offer['manifest_sha256']:
                self.failure(c,revision,UpdateError('revision_manifest_changed'));bad=True;continue
            if old and old.get('migration') and old['migration']['phase'] in ('acknowledged','superseded'):
                require(old['manifest_sha256']==offer['manifest_sha256'],'revision_manifest_changed')
                if current.get(c)!=offer['version']:self.failure(c,revision,UpdateError('migration_already_completed'))
                continue
            migrating=needed(offer,current,old)
            if migrating:self.migration_pending=True
            if (old and old["phase"]=="complete" and current.get(c)==offer["version"]
                    and (not migrating or completed_apps(self,old))):
                require(old["manifest_sha256"]==offer["manifest_sha256"],"revision_manifest_changed")
                # A crash between durable completion and outbox write must not
                # strand a native migration which the server has not confirmed.
                if migrating:self.enqueue(c,revision,'succeeded',None)
                continue
            try:
                require(not old or old["manifest_sha256"]==offer["manifest_sha256"],"revision_manifest_changed")
                require(not getattr(self.commands,'incomplete',set()),'dpkg_manual_repair_required')
                guard_bridge(offer,current)
                manifest,highest=self.verify(offer)
                if c in current and self.commands.older(offer["version"],current[c]):require(offer["rollback"],"downgrade_not_authorized")
                high=self.ledger["highwater"].get(c,{"sequence":0,"hash":None})
                if manifest["sequence"]>=high["sequence"]:self.ledger["highwater"][c]={"sequence":highest,"hash":manifest["_manifest_sha256"]}
                job=old or {"manifest_sha256":offer["manifest_sha256"],"version":offer["version"]}
                job['phase']='verified';self.ledger["jobs"][key]=job;self.save()
                if migrating:migrations[key]=Migration(self,offer,job,current)
                plan.append((offer,manifest,job))
            except (UpdateError,ReleaseVerificationError,OSError,ValueError) as error:
                self.failure(c,revision,error);bad=True
        # Authenticate every application component before a single apt command.
        # Mixed core/native updates therefore do not depend on response order.
        if bad:
            for offer,_,_ in plan:self.enqueue(offer["component"],offer["revision"],"failed","plan_rejected")
        elif plan:
            try:
                migration=next(iter(migrations.values()),None)
                app_size=migration.preflight() if migration is not None else 0
                changed=[p for p in plan if current.get(p[0]["component"])!=p[0]["version"]]
                if changed or app_size:
                    size=app_size+sum(a["size"] for _,m,_ in changed for a in m["artifacts"])
                    require(shutil.disk_usage(self.state).free>=2*size+128*1024*1024,"insufficient_download_space")
                    self.commands.refresh()
                    with tempfile.TemporaryDirectory(prefix="download-",dir=self.state) as temporary:
                        directory=Path(temporary);directory.chmod(0o700);files=[]
                        for offer,manifest,job in changed:
                            component_dir=directory/offer["component"];component_dir.mkdir(mode=0o700)
                            files.extend(self.commands.download(a,component_dir) for a in manifest["artifacts"])
                            job["phase"]="installing"
                        if migration is not None:
                            apps_dir=directory/'apps';apps_dir.mkdir(mode=0o700)
                            files.extend(migration.download(apps_dir));migration.phase('applying')
                        self.save()
                        if migration is None:self.commands.install(files,any(o["rollback"] for o,_,_ in changed))
                        else:
                            targets={a['package']:a['version'] for _,manifest,_ in plan for a in manifest['artifacts']}
                            targets.update({app['package']:app['version'] for app in migration.descriptors})
                            self.commands.install_migration(files,migration.descriptors,targets)
                observed={i["component"]:i["version"] for i in self.commands.installed()}
                require(all(observed.get(o["component"])==o["version"] for o,_,_ in plan),"dpkg_manual_repair_required" if getattr(self.commands,"incomplete",set()) else "installed_version_mismatch")
                if migration is not None:migration.assert_installed()
                for _,_,job in plan:job["phase"]="installed"
                self.save()
                if migration is None:self.commands.activate(observed.get('studentcore'))
                else:migration.activate(observed.get('studentcore'))
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
        result=wait_ready(current.get("studentcore"),lambda argv:client.commands.call(argv,timeout=5),legacy_scratch='scratch' in current)
        print(json.dumps({"state":"ready",**result}));return
    lock=plain(client.state/"client.lock")
    with lock.open("a+b") as handle:
        os.chmod(lock,0o600)
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise UpdateError("updater_already_running")
        result=client.tick()
        from school_profile_sync import sync as sync_school_profile
        result['school_profile']=sync_school_profile(client.transport)
        from app_operations import AppClient
        result['apps']=AppClient(client).tick()
        print(json.dumps(result))

if __name__=="__main__":main()
