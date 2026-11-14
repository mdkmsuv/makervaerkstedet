"""Publisher-verified preservation of bundled apps during the .2 ownership split."""
from copy import deepcopy
import hashlib
import json
import re
from urllib.error import HTTPError
from app_catalog import accept_catalog,compatibility,compare_versions,validate_app_descriptor
from app_operations import Executor,load_verified_catalog

LEGACY='2.0.0~20261114.2'
MODULAR='2.1.0~'
BRIDGE='1.1.0~'
FOUR=frozenset(('laser','folie','3dprint','microbit'))
BUILTIN=FOUR|{'scratch'}
HASH=re.compile('[a-f0-9]{64}\\Z')


def require(ok,code):
    if not ok:raise ValueError(code)


def at_least(value,minimum):return value is not None and compare_versions(value,minimum)>=0


def needed(offer,current,job=None):
    return offer['component']=='studentcore' and (bool(job and job.get('migration'))
        or at_least(offer['version'],MODULAR) and current.get('studentcore') is not None
        and not at_least(current['studentcore'],MODULAR))


def guard_bridge(offer,current):
    if offer['component']=='studentcore' and at_least(offer['version'],MODULAR):
        require(at_least(current.get('updater'),BRIDGE),'updater_bridge_required')
    if offer['component']=='updater' and at_least(offer['version'],BRIDGE):
        require(at_least(current.get('studentcore'),LEGACY),'legacy_core_baseline_required')


def install_plan(output,targets):
    """Allow OS dependencies, but only explicitly verified school packages."""
    require(type(targets) is dict and targets,'invalid_migration_targets')
    for line in output.splitlines():
        require(not line.startswith('Remv '),'unsafe_migration_install_plan')
        if line.startswith(('Inst ','Conf ')):
            match=re.fullmatch(r'(?:Inst|Conf) ([a-z0-9+.-]+)(?::(?:amd64|all))?(?: \[[^\]\r\n]+\])? \(([^\s)]+)(?: [^\r\n]*)?\)(?: \[[^\]\r\n]*\])*',line)
            require(match is not None,'unsafe_migration_install_plan')
            if match[1].startswith('makervaerkstedet-'):
                require(targets.get(match[1])==match[2],'unsafe_migration_install_plan')
    return True


def validate_binding(value,offer):
    require(type(value) is dict and set(value)=={'schema','core_manifest_sha256','revision','catalog_sha256','apps','completed'},
            'invalid_migration_binding')
    require(type(value['schema']) is int and value['schema']==1 and type(value['revision']) is int
            and 0<value['revision']<2**63 and value['revision']==offer['revision']
            and value['core_manifest_sha256']==offer['manifest_sha256']
            and type(value['catalog_sha256']) is str and HASH.fullmatch(value['catalog_sha256'])
            and type(value['completed']) is bool,
            'migration_binding_mismatch')
    apps=value['apps']
    require(type(apps) is list and all(type(app) is str for app in apps)
            and apps==sorted(set(apps)) and set(apps) in (set(FOUR),set(BUILTIN)),
            'invalid_migration_app_set')
    return value


def _binding_json(raw):
    def pairs(items):
        value={}
        for key,item in items:
            require(key not in value,'invalid_migration_binding');value[key]=item
        return value
    try:return json.loads(raw,object_pairs_hook=pairs,
                          parse_constant=lambda _:(_ for _ in ()).throw(ValueError('invalid_migration_binding')))
    except (UnicodeError,json.JSONDecodeError,RecursionError):raise ValueError('invalid_migration_binding') from None


def _entry_hash(entry):
    return hashlib.sha256(json.dumps(entry,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class Migration:
    def __init__(self,client,offer,job,current):
        self.client,self.offer,self.job=client,offer,job
        guard_bridge(offer,current)
        previous=job.get('migration')
        if previous is None:
            require(at_least(current.get('studentcore'),LEGACY) and not at_least(current['studentcore'],MODULAR),
                    'legacy_core_baseline_required')
            require('scratch' not in getattr(client.commands,'incomplete',set()),'dpkg_manual_repair_required')
            path='/api/device/migration/'+offer['manifest_sha256']+'/'+str(offer['revision'])
            binding=validate_binding(_binding_json(client.transport.get(path,16384)),offer)
            require(not binding['completed'],'migration_already_completed')
            require(set(binding['apps'])==FOUR|({'scratch'} if current.get('scratch') else set()),
                    'migration_installed_set_changed')
        else:
            require(type(previous) is dict and set(previous)=={'binding','entries','from_core','from_scratch','phase'}
                    and previous['phase'] in ('verified','applying','installed','complete','reported'), 'invalid_migration_journal')
            binding=validate_binding(previous['binding'],offer)
            require(at_least(previous['from_core'],LEGACY) and not at_least(previous['from_core'],MODULAR)
                    and ('scratch' in binding['apps'])==(previous['from_scratch'] is not None),
                    'invalid_migration_journal')
        catalog=load_verified_catalog(client,binding['catalog_sha256'])
        indexed={entry['descriptor']['id']:entry for entry in catalog['apps']}
        require(BUILTIN<=set(indexed),'migration_catalog_incomplete')
        self.entries=[deepcopy(indexed[app]) for app in binding['apps']]
        self.descriptors=[entry['descriptor'] for entry in self.entries]
        for app in self.descriptors:
            require(compatibility(app,core_version=offer['version'])['compatible'],'app_incompatible')
        if previous is not None:
            require(previous['entries']==self.entries,'migration_catalog_identity_changed')
        executor=Executor(client.state,client.commands)
        executor.bind_identity(getattr(client.transport,'identity_sha256',None))
        water=executor.ledger['highwater']
        if catalog['sequence']<water.get('sequence',0):
            require(previous is not None,'catalog_rollback_rejected')
            for entry in self.entries:
                app_id=entry['descriptor']['id'];approved=executor.ledger['approved'].get(app_id)
                require(approved is not None and approved['entry']==entry
                        and water.get('apps',{}).get(app_id,{}).get('sha256')==_entry_hash(entry),
                        'migration_catalog_identity_changed')
        else:water=accept_catalog(catalog,water)
        executor.ledger['highwater']=water
        for entry in self.entries:
            executor.ledger['approved'][entry['descriptor']['id']]={'entry':deepcopy(entry),
                'catalog_sha256':binding['catalog_sha256']}
        executor.save()
        if previous is None:
            job['migration']={'binding':deepcopy(binding),'entries':deepcopy(self.entries),
                              'from_core':current['studentcore'],'from_scratch':current.get('scratch'),'phase':'verified'}
            client.save()
        self.missing=[]

    def observe(self):
        inventory=self.client.commands.app_inventory(self.descriptors)
        require(type(inventory) is list and len(inventory)==len(self.descriptors),'invalid_app_inventory')
        values={item['id']:item for item in inventory}
        require(len(values)==len(inventory) and set(values)=={a['id'] for a in self.descriptors},'invalid_app_inventory')
        missing=[]
        for entry in self.entries:
            app=entry['descriptor'];item=values[app['id']]
            require(item['package']==app['package'] and item['state'] in ('installed','absent','incomplete'),
                    'invalid_app_inventory')
            require(item['state']!='incomplete','dpkg_manual_repair_required')
            if item['state']=='installed':
                require(type(item['version']) is str,'invalid_app_inventory')
                require(compare_versions(item['version'],app['version'])<=0,'app_downgrade_rejected')
            if item['state']!='installed' or item['version']!=app['version'] or not self.client.commands.app_matches(app):
                missing.append(entry)
        self.missing=missing
        return not missing

    def preflight(self):
        self.observe()
        for app in self.descriptors:self.client.commands.preflight_app(app)
        return sum(entry['artifact']['size'] for entry in self.missing)

    def download(self,directory):
        files=[]
        for entry in self.missing:
            app=entry['descriptor'];folder=directory/app['id'];folder.mkdir(mode=0o700)
            artifact={key:entry['artifact'][key] for key in ('name','size','sha256')}
            artifact.update(package=app['package'],version=app['version'],architecture=app['architecture'])
            files.append(self.client.commands.download(artifact,folder))
        return files

    def phase(self,value):
        self.job['migration']['phase']=value;self.client.save()

    def assert_installed(self):require(self.observe(),'migration_app_state_mismatch')

    def activate(self,core):
        self.assert_installed();self.phase('installed')
        self.client.commands.activate_migration(core,self.descriptors)
        self.assert_installed();self.phase('complete')


def completed_apps(client,job):
    """Check the durable selected set before resending a success receipt."""
    try:
        journal=job['migration'];binding=journal['binding']
        validate_binding(binding,{'manifest_sha256':job['manifest_sha256'],'revision':binding['revision']})
        entries=journal['entries']
        require(type(entries) is list and len(entries)==len(binding['apps']),'invalid_migration_journal')
        descriptors=[validate_app_descriptor(entry['descriptor']) for entry in entries]
        require({a['id'] for a in descriptors}==set(job['migration']['binding']['apps']),'invalid_migration_journal')
        values=client.commands.app_inventory(descriptors)
        return (len(values)==len(descriptors) and len({item['id'] for item in values})==len(descriptors)
            and all(item['state']=='installed' and item['package']==app['package'] and item['version']==app['version']
                    and client.commands.app_matches(app)
                    for app in descriptors for item in values if item['id']==app['id'])
            and {item['id'] for item in values}=={app['id'] for app in descriptors})
    except (ValueError,OSError,KeyError,TypeError):return False


def confirm_completed(client,job,current):
    """Omission from an offer is not proof of completion: require explicit status."""
    journal=job.get('migration')
    if not journal or journal['phase'] in ('acknowledged','superseded'):return False
    binding=journal['binding'];offer={'manifest_sha256':job['manifest_sha256'],'revision':binding['revision']}
    path='/api/device/migration/'+binding['core_manifest_sha256']+'/'+str(binding['revision'])
    try:
        status=validate_binding(_binding_json(client.transport.get(path,16384)),offer)
        require(all(status[key]==value for key,value in binding.items() if key!='completed'),
                'migration_binding_mismatch')
        # A durable completed binding proves the server already accepted both
        # the exact receipt and app inventory. Subsequent authorized core/app
        # changes must not trap this old intent or reinstall its former apps.
        if journal['phase'] in ('complete','reported') and status['completed']:
            journal['phase']='acknowledged';client.save();return True
    except HTTPError as error:
        if error.code==404:
            try:
                raw=error.read(16385)
                if len(raw)<=16384 and _binding_json(raw)=={'error':'migration_not_found'}:
                    journal['phase']='superseded';client.save();return False
            except (ValueError,OSError):pass
    except (ValueError,OSError):pass
    return False
