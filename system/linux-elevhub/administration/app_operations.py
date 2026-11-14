"""Publisher-bound app actions. Separate journal and protocol from legacy offers."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit

from app_catalog import (validate_app_descriptor, accept_catalog, compatibility,
                         compare_versions, verify_catalog, component_for_app)
from simple_release import MAX_MANIFEST, MAX_SIGNATURE, REPOSITORY
from template_activation import atomic, json_bytes, plain, protected

API = '/api/device/apps'
PROTOCOL = 'apps-v1'
HEX = re.compile(r'[a-f0-9]{64}\Z')
JOB = re.compile(r'[a-f0-9]{32}\Z')
MINISIGN_SHA256 = '2c74dffcc1c9a5ee55957c60971998ace2b89f22585631594ec2152c588af8db'


def require(ok, code):
    if not ok: raise ValueError(code)


def load_verified_catalog(legacy,digest):
    """Immutable signed metadata survives an interrupted authorized operation."""
    require(type(digest) is str and HEX.fullmatch(digest),'invalid_app_catalog_hash')
    cache=plain(legacy.state/'app-catalogs');cache.mkdir(exist_ok=True,mode=0o700)
    message=plain(cache/(digest+'.json'));sigfile=plain(cache/(digest+'.minisig'))
    cached=message.exists() and sigfile.exists()
    if cached:
        require(protected(message,private=True).stat().st_size<=MAX_MANIFEST
                and protected(sigfile,private=True).stat().st_size<=MAX_SIGNATURE,'cached_app_catalog_too_large')
        raw=message.read_bytes();signature=sigfile.read_bytes()
    else:
        path='/apt/apps/catalog/'+digest
        raw=legacy.transport.get(path+'.json',MAX_MANIFEST)
        signature=legacy.transport.get(path+'.minisig',MAX_SIGNATURE)
    require(hashlib.sha256(raw).hexdigest()==digest,'app_catalog_hash_mismatch')
    catalog=verify_catalog(raw,signature,minisign_exe=legacy.minisign,minisign_sha256=MINISIGN_SHA256,
                           public_key=legacy.public_key,repository=REPOSITORY)
    if not cached:atomic(message,raw,0o600);atomic(sigfile,signature,0o600)
    return catalog


def validate_offer(offer):
    require(type(offer) is dict and set(offer) == {'job_id','revision','action','app_id','catalog_sha256'}, 'invalid_app_job')
    require(type(offer['job_id']) is str and JOB.fullmatch(offer['job_id'])
            and type(offer['revision']) is int and 0 < offer['revision'] < 2**63
            and offer['action'] in ('install','update','remove')
            and type(offer['catalog_sha256']) is str and HEX.fullmatch(offer['catalog_sha256']), 'invalid_app_job')
    component_for_app(offer['app_id'])
    return offer


def removal_plan(output, descriptor):
    """Native apt may not remove dependencies or install anything on removal."""
    validate_app_descriptor(descriptor)
    removed = set()
    for line in output.splitlines():
        if line.startswith(('Inst ', 'Conf ')): raise ValueError('unsafe_app_removal_plan')
        if line.startswith('Remv '):
            parts = line.split()
            require(len(parts) >= 2, 'unsafe_app_removal_plan')
            removed.add(parts[1])
    require(removed == {descriptor['package']}, 'unsafe_app_removal_plan')
    return removed


def install_plan(output,descriptor):
    app=validate_app_descriptor(descriptor)
    for line in output.splitlines():
        if line.startswith('Remv '):raise ValueError('unsafe_app_install_plan')
        if line.startswith(('Inst ','Conf ')):
            match=re.fullmatch(r'(?:Inst|Conf) ([a-z0-9+.-]+)(?::amd64)?(?: \[[^\]]+\])? \(([^\s)]+)(?: [^\r\n]*)?\)',line)
            require(match is not None and match[1]==app['package'] and match[2]==app['version'],'unsafe_app_install_plan')
    return True


def installed_descriptors(root=Path('/usr/share/makervaerkstedet/apps'),*,issues=None):
    """Metadata is package-owned, never writable by the kiosk account."""
    root = plain(root)
    if not root.exists(): return []
    protected(root, directory=True)
    result = []
    for folder in sorted(root.iterdir()):
        try:
            if not folder.is_dir(): continue
            path = plain(folder/'app.json')
            if not path.exists(): continue
            require(protected(path).stat().st_size <= 16384, 'app_descriptor_too_large')
            raw = path.read_bytes()
            value = json.loads(raw)
            require(type(value) is dict,'invalid_app_descriptor')
            if value.get('schema') != 2: continue  # .2 migration handled explicitly.
            validate_app_descriptor(value)
            require(value['id'] == folder.name, 'app_descriptor_directory_mismatch')
            result.append(value)
        except (ValueError,OSError):
            if issues is None:raise
            if re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',folder.name):
                issues.append({'app_id':folder.name,'code':'installed_manifest_invalid'})
    return result


class Executor:
    def __init__(self, state, commands):
        self.state = plain(state); self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = plain(self.state/'apps-ledger.json'); self.commands = commands
        self.ledger = json.loads(self.path.read_bytes()) if self.path.exists() else {'schema':1,'jobs':{},'highwater':{},'outbox':[]}
        base={'schema','jobs','highwater','outbox'}
        require(type(self.ledger) is dict and base<=set(self.ledger)<=base|{'approved','identity_sha256'}
                and self.ledger['schema'] == 1 and type(self.ledger['jobs']) is dict
                and type(self.ledger['highwater']) is dict and type(self.ledger['outbox']) is list, 'invalid_app_ledger')
        self.ledger.setdefault('approved',{})
        require(type(self.ledger['approved']) is dict,'invalid_app_ledger')

    def save(self): atomic(self.path, json_bytes(self.ledger), 0o600)

    def bind_identity(self,identity):
        from identity_state import scoped_ledger
        scoped=scoped_ledger(self.state,self.ledger,identity,kind='apps')
        if scoped is not self.ledger:self.ledger=scoped;self.save()

    def reject(self, offer, error):
        validate_offer(offer)
        previous=self.ledger['jobs'].get(offer['job_id'])
        require(not previous or previous['offer']==offer,'job_identity_changed')
        if previous and previous['phase'] in ('complete','failed','repair'):return previous['result']
        code=str(error)
        if not re.fullmatch('[a-z][a-z0-9_]{0,79}',code):code='app_operation_failed'
        result={'job_id':offer['job_id'],'revision':offer['revision'],'state':'failed','code':code}
        self.ledger['jobs'][offer['job_id']]={'offer':dict(offer),'phase':'failed','result':result}
        if result not in self.ledger['outbox']:self.ledger['outbox'].append(result)
        self.save()
        return result

    def execute(self, offer, catalog, *, core_version):
        validate_offer(offer)
        old = self.ledger['jobs'].get(offer['job_id'])
        require(not old or old['offer'] == offer, 'job_identity_changed')
        if old and old['phase'] in ('complete','failed','repair'):return old['result']
        latest=max((job['offer']['revision'] for job in self.ledger['jobs'].values()
                    if job['offer']['app_id']==offer['app_id']),default=0)
        if offer['revision']<latest:return self.reject(offer,ValueError('app_job_superseded'))
        require(type(catalog) is dict and catalog.get('_manifest_sha256') == offer['catalog_sha256'], 'app_catalog_hash_mismatch')
        entries = [entry for entry in catalog['apps'] if entry['descriptor']['id'] == offer['app_id']]
        require(len(entries) == 1, 'app_not_in_catalog')
        entry = entries[0]; app = entry['descriptor']
        if catalog['sequence']<self.ledger['highwater'].get('sequence',0):
            approved=self.ledger['approved'].get(app['id'])
            entry_hash=hashlib.sha256(json.dumps(entry,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            resume=old is not None and old.get('entry')==entry and old['phase'] in ('verified','applying','installed') and self.ledger['highwater'].get('apps',{}).get(app['id'],{}).get('sha256')==entry_hash
            require(approved is not None and approved['entry']==entry and (offer['action']=='remove' or resume),
                    'historical_app_job_not_authorized')
            highwater=self.ledger['highwater']
        else:
            highwater=accept_catalog(catalog,self.ledger['highwater'])
        self.ledger['highwater'] = highwater
        self.ledger['approved'][app['id']]={'entry':json.loads(json.dumps(entry)),'catalog_sha256':offer['catalog_sha256']}
        job = old or {'offer':dict(offer), 'phase':'verified','entry':json.loads(json.dumps(entry))}
        self.ledger['jobs'][offer['job_id']] = job; self.save()
        def observe():
            values = self.commands.app_inventory([app])
            require(len(values) == 1 and values[0]['id'] == app['id'] and values[0]['package'] == app['package'], 'invalid_app_inventory')
            value = values[0]
            require(value['state'] in ('installed','absent','incomplete'), 'invalid_app_inventory')
            require(value['state'] != 'incomplete', 'dpkg_manual_repair_required')
            return value
        try:
            current = observe()
            wanted_absent = offer['action'] == 'remove'
            self.commands.preflight_app(app,removed=wanted_absent)
            satisfied = current['state'] == 'absent' if wanted_absent else (current['state'] == 'installed' and current['version'] == app['version'])
            if not wanted_absent and satisfied:satisfied=self.commands.app_matches(app)
            if not wanted_absent:
                require(compatibility(app, core_version=core_version)['compatible'], 'app_incompatible')
                require(offer['action'] != 'update' or current['state'] == 'installed', 'app_not_installed')
                require(current['state'] != 'installed' or compare_versions(app['version'],current['version']) >= 0, 'app_downgrade_rejected')
            if not satisfied:
                if not wanted_absent:
                    require(shutil.disk_usage(self.state).free>=2*entry['artifact']['size']+128*1024**2,'insufficient_download_space')
                job['phase'] = 'applying'; self.save()
                if wanted_absent: self.commands.remove_app(app)
                else:
                    self.commands.refresh()
                    artifact = {key:entry['artifact'][key] for key in ('name','size','sha256')}
                    artifact.update(package=app['package'],version=app['version'],architecture=app['architecture'])
                    with tempfile.TemporaryDirectory(prefix='app-download-',dir=self.state) as temporary:
                        directory=Path(temporary);directory.chmod(0o700)
                        package=self.commands.download(artifact,directory)
                        self.commands.install_app([package],app)
            current = observe()
            require(current['state'] == 'absent' if wanted_absent else
                    current['state'] == 'installed' and current['version'] == app['version'], 'app_installed_state_mismatch')
            require(self.commands.app_absent(app) if wanted_absent else self.commands.app_matches(app),
                    'app_removal_incomplete' if wanted_absent else 'app_installed_manifest_mismatch')
            job['phase'] = 'installed'; self.save()
            self.commands.activate_app(core_version,app,removed=wanted_absent)
            current = observe()
            require(current['state'] == 'absent' if wanted_absent else
                    current['state'] == 'installed' and current['version'] == app['version'], 'app_installed_state_mismatch')
            require(self.commands.app_absent(app) if wanted_absent else self.commands.app_matches(app),
                    'app_removal_incomplete' if wanted_absent else 'app_installed_manifest_mismatch')
            result = {'job_id':offer['job_id'],'revision':offer['revision'],'state':'succeeded','code':None}
            job['phase'] = 'complete'
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            code = str(error)
            if not re.fullmatch('[a-z][a-z0-9_]{0,79}', code): code='app_operation_failed'
            job['phase'] = 'repair' if code == 'dpkg_manual_repair_required' else 'failed'
            result = {'job_id':offer['job_id'],'revision':offer['revision'],'state':'failed','code':code}
        job['result'] = result
        if result not in self.ledger['outbox']: self.ledger['outbox'].append(result)
        self.save(); return result


class AppClient:
    def __init__(self, legacy,*,manifest_root=Path('/usr/share/makervaerkstedet/apps')):
        self.legacy = legacy
        self.manifest_root=manifest_root
        self.executor = Executor(legacy.state, legacy.commands)

    def tick(self):
        old = self.legacy
        self.executor.bind_identity(getattr(old.transport,'identity_sha256',None))
        current = {item['component']:item['version'] for item in old.commands.installed()}
        issues=[]
        root_error=False
        try:descriptors=installed_descriptors(self.manifest_root,issues=issues)
        except (ValueError,OSError):descriptors=[];root_error=True
        known={item['id']:item for item in descriptors}
        for app_id,record in self.executor.ledger['approved'].items():
            known.setdefault(app_id,record['entry']['descriptor'])
        inventory=[]
        for app_id,descriptor in known.items():
            try:
                record=old.commands.app_inventory([descriptor])[0]
                if record['state']=='installed' and (root_error or record['version']!=descriptor['version'] or not old.commands.app_matches(descriptor)):
                    issues.append({'app_id':app_id,'code':'installed_manifest_mismatch'})
                if any(issue['app_id']==app_id for issue in issues):record['state']='incomplete'
                inventory.append(record)
            except (ValueError,OSError):
                inventory.append({'id':app_id,'package':descriptor['package'],'version':None,'state':'incomplete'})
        pending = self.executor.ledger['outbox'][0] if self.executor.ledger['outbox'] else None
        body={'schema':1,'capabilities':[PROTOCOL],'updater_version':current.get('updater'),
              'core_version':current.get('studentcore'),'installed':inventory,'issues':issues,'result':pending}
        # A .2 school server may not have this endpoint yet. Never change the
        # schema1 core-update request or ask an old updater to parse app jobs.
        try: response = old.transport.poll_apps(body)
        except HTTPError as error:
            if error.code == 404: return {'state':'server_bridge_pending'}
            raise
        require(type(response) is dict and set(response) == {'schema','jobs'}
                and type(response['schema']) is int and response['schema'] == 1 and type(response['jobs']) is list
                and len(response['jobs']) <= 16, 'invalid_app_poll_response')
        for offer in response['jobs']: validate_offer(offer)
        if pending:
            self.executor.ledger['outbox'].pop(0); self.executor.save()
        if getattr(old,'migration_pending',False):
            return {'state':'migration_pending','jobs':len(response['jobs'])}
        for offer in response['jobs']:
            try:
                previous=self.executor.ledger['jobs'].get(offer['job_id'])
                require(not previous or previous['offer']==offer,'job_identity_changed')
                if previous and previous['phase'] in ('complete','failed','repair'):
                    if previous['result'] not in self.executor.ledger['outbox']:
                        self.executor.ledger['outbox'].append(previous['result']);self.executor.save()
                    continue
                catalog=load_verified_catalog(old,offer['catalog_sha256'])
                self.executor.execute(offer,catalog,core_version=current.get('studentcore'))
            except (ValueError,OSError,subprocess.SubprocessError) as error:
                self.executor.reject(offer,error)
        return {'state':'polled','jobs':len(response['jobs'])}
