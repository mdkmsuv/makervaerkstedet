"""Publisher-bound app actions. Separate journal and protocol from legacy offers."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
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


def installed_descriptors(root=Path('/usr/share/makervaerkstedet/apps')):
    """Metadata is package-owned, never writable by the kiosk account."""
    root = plain(root)
    if not root.exists(): return []
    protected(root, directory=True)
    result = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir(): continue
        path = plain(folder/'app.json')
        if not path.exists(): continue
        raw = protected(path).read_bytes()
        require(len(raw) <= 16384, 'app_descriptor_too_large')
        value = json.loads(raw)
        if value.get('schema') != 2: continue  # .2 migration handled explicitly.
        validate_app_descriptor(value)
        require(value['id'] == folder.name, 'app_descriptor_directory_mismatch')
        result.append(value)
    return result


class Executor:
    def __init__(self, state, commands):
        self.state = plain(state); self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = plain(self.state/'apps-ledger.json'); self.commands = commands
        self.ledger = json.loads(self.path.read_bytes()) if self.path.exists() else {'schema':1,'jobs':{},'highwater':{},'outbox':[]}
        require(type(self.ledger) is dict and set(self.ledger) == {'schema','jobs','highwater','outbox'}
                and self.ledger['schema'] == 1 and type(self.ledger['jobs']) is dict
                and type(self.ledger['highwater']) is dict and type(self.ledger['outbox']) is list, 'invalid_app_ledger')

    def save(self): atomic(self.path, json_bytes(self.ledger), 0o600)

    def reject(self, offer, error):
        validate_offer(offer)
        previous=self.ledger['jobs'].get(offer['job_id'])
        require(not previous or previous['offer']==offer,'job_identity_changed')
        code=str(error)
        if not re.fullmatch('[a-z][a-z0-9_]{0,79}',code):code='app_operation_failed'
        result={'job_id':offer['job_id'],'revision':offer['revision'],'state':'failed','code':code}
        self.ledger['jobs'][offer['job_id']]={'offer':offer,'phase':'failed','result':result}
        if result not in self.ledger['outbox']:self.ledger['outbox'].append(result)
        self.save()
        return result

    def execute(self, offer, catalog, *, core_version):
        validate_offer(offer)
        old = self.ledger['jobs'].get(offer['job_id'])
        require(not old or old['offer'] == offer, 'job_identity_changed')
        require(type(catalog) is dict and catalog.get('_manifest_sha256') == offer['catalog_sha256'], 'app_catalog_hash_mismatch')
        highwater = accept_catalog(catalog, self.ledger['highwater'])
        entries = [entry for entry in catalog['apps'] if entry['descriptor']['id'] == offer['app_id']]
        require(len(entries) == 1, 'app_not_in_catalog')
        entry = entries[0]; app = entry['descriptor']
        self.ledger['highwater'] = highwater
        job = old or {'offer':offer, 'phase':'verified'}
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
            satisfied = current['state'] == 'absent' if wanted_absent else (current['state'] == 'installed' and current['version'] == app['version'])
            if job['phase'] == 'complete' and satisfied: return job['result']
            if not wanted_absent:
                require(compatibility(app, core_version=core_version)['compatible'], 'app_incompatible')
                require(offer['action'] != 'update' or current['state'] == 'installed', 'app_not_installed')
                require(current['state'] != 'installed' or compare_versions(app['version'],current['version']) >= 0, 'app_downgrade_rejected')
            if not satisfied:
                job['phase'] = 'applying'; self.save()
                if wanted_absent: self.commands.remove_app(app)
                else:
                    self.commands.refresh()
                    artifact = {key:entry['artifact'][key] for key in ('name','size','sha256')}
                    artifact.update(package=app['package'],version=app['version'],architecture=app['architecture'])
                    with tempfile.TemporaryDirectory(prefix='app-download-',dir=self.state) as temporary:
                        directory=Path(temporary);directory.chmod(0o700)
                        package=self.commands.download(artifact,directory)
                        self.commands.install([package],False)
            current = observe()
            require(current['state'] == 'absent' if wanted_absent else
                    current['state'] == 'installed' and current['version'] == app['version'], 'app_installed_state_mismatch')
            job['phase'] = 'installed'; self.save()
            self.commands.activate(core_version)
            current = observe()
            require(current['state'] == 'absent' if wanted_absent else
                    current['state'] == 'installed' and current['version'] == app['version'], 'app_installed_state_mismatch')
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
    def __init__(self, legacy):
        self.legacy = legacy
        self.executor = Executor(legacy.state, legacy.commands)

    def tick(self):
        old = self.legacy
        current = {item['component']:item['version'] for item in old.commands.installed()}
        inventory = old.commands.app_inventory(installed_descriptors())
        pending = self.executor.ledger['outbox'][0] if self.executor.ledger['outbox'] else None
        body={'schema':1,'capabilities':[PROTOCOL],'updater_version':current.get('updater'),
              'core_version':current.get('studentcore'),'installed':inventory,'result':pending}
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
        for offer in response['jobs']:
            try:
                digest = offer['catalog_sha256']; path='/apt/apps/catalog/'+digest
                raw=old.transport.get(path+'.json',MAX_MANIFEST)
                require(hashlib.sha256(raw).hexdigest() == digest, 'app_catalog_hash_mismatch')
                signature=old.transport.get(path+'.minisig',MAX_SIGNATURE)
                catalog=verify_catalog(raw,signature,minisign_exe=old.minisign,minisign_sha256=MINISIGN_SHA256,
                                       public_key=old.public_key,repository=REPOSITORY)
                self.executor.execute(offer,catalog,core_version=current.get('studentcore'))
            except (ValueError,OSError,subprocess.SubprocessError) as error:
                self.executor.reject(offer,error)
        return {'state':'polled','jobs':len(response['jobs'])}
