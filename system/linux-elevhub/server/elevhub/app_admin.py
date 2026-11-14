"""Signed app choices, observed inventory and durable per-device actions."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import ssl
import uuid
from simple_updates import UpdateError,canonical,fields,plain,verifier_path

HASH=re.compile('[a-f0-9]{64}\\Z')
STATES=frozenset(('installed','absent','incomplete'))


def require(ok,code,status=400):
    if not ok:raise UpdateError(code,status)


def contract():
    spec=importlib.util.spec_from_file_location('_mkv_app_admin_contract',verifier_path().with_name('app_catalog.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class AppAdmin:
    def __init__(self,service,*,verifier=None,db=None):
        self.service=service;self.contract=contract();self.verifier=verifier or self.contract.verify_catalog
        self.catalog_observer=None
        self.artifact_checks={}
        if db is not None:self._initialize(db)
        else:
            with service.transaction() as connection:self._initialize(connection)

    def _initialize(self,db):
        # executescript implicitly commits: individual statements preserve a core
        # order's existing transaction and shared revision when initialized lazily.
        fresh=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apps_meta'").fetchone() is None
        statements=(
            "CREATE TABLE IF NOT EXISTS apps_meta(id INTEGER PRIMARY KEY CHECK(id=1),highwater TEXT NOT NULL,catalog_hash TEXT,defaults TEXT NOT NULL)",
            "INSERT OR IGNORE INTO apps_meta VALUES(1,'{}',NULL,'[]')",
            "CREATE TABLE IF NOT EXISTS apps_catalogs(hash TEXT PRIMARY KEY,document TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS apps_reports(device TEXT PRIMARY KEY,body TEXT NOT NULL,seen TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS apps_enrolled(device TEXT PRIMARY KEY,defaults_done INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS apps_jobs(job_id TEXT PRIMARY KEY,device TEXT NOT NULL,app_id TEXT NOT NULL,action TEXT NOT NULL,catalog_hash TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,code TEXT,created TEXT NOT NULL)")
        for statement in statements:db.execute(statement)
        if fresh:
            for device in self.service.devices(db):db.execute('INSERT OR IGNORE INTO apps_enrolled VALUES(?,1)',(device,))

    def _revision(self,db):return db.execute('SELECT revision FROM meta WHERE id=1').fetchone()[0]

    def _advance(self,db):
        db.execute('UPDATE meta SET revision=revision+1 WHERE id=1')
        return self._revision(db)

    def _records(self):
        if self.service.catalog_path.exists():
            from release_feed import strict_json
            raw=plain(self.service.catalog_path).read_bytes();require(len(raw)<=2*1024**2,'app_catalog_index_too_large',503)
            value=strict_json(raw);fields(value,('schema','sequence','descriptor_sha256','catalog'))
            require(value['schema']==1 and type(value['catalog']) is list,'invalid_app_catalog_index',503)
            return value['catalog']
        return self.service.config['catalog']

    def _verify(self,digest):
        require(type(digest) is str and HASH.fullmatch(digest),'invalid_app_catalog_hash')
        message=plain(self.service.cache/'manifests'/(digest+'.json'))
        signature=plain(message.with_suffix('.minisig'))
        require(message.is_file() and message.stat().st_size<=1024**2 and signature.is_file()
                and signature.stat().st_size<=16384,'app_catalog_unavailable',503)
        raw=message.read_bytes();require(hashlib.sha256(raw).hexdigest()==digest,'app_catalog_hash_mismatch',503)
        config=self.service.config
        return self.verifier(raw,signature.read_bytes(),minisign_exe=plain(self.service.base/config['minisign_exe']),
            minisign_sha256=config['minisign_sha256'],public_key=config['public_key'],repository=self.contract.simple_release.REPOSITORY)

    def _artifacts(self,value):
        for entry in value['apps']:
            artifact=entry['artifact'];path=plain(self.service.cache/'pool'/artifact['name'])
            require(path.is_file(),'app_artifact_unavailable',503)
            info=path.stat();stamp=(info.st_size,info.st_mtime_ns,info.st_ctime_ns,artifact['sha256'])
            require(info.st_size==artifact['size'],'app_artifact_hash_mismatch',503)
            if self.artifact_checks.get(str(path))!=stamp:
                with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                require(digest==artifact['sha256'],'app_artifact_hash_mismatch',503)
                self.artifact_checks[str(path)]=stamp

    def catalog(self,db):
        errors=[];values=[]
        for record in self._records():
            if type(record) is not dict or record.get('component')!='app-catalog':continue
            try:
                require(set(record)=={'manifest','signature','role','platform','component'}
                    and record['role']=='student' and record['platform']=='debian-amd64','invalid_app_catalog_record',503)
                match=re.fullmatch('public-apt-cache/manifests/([a-f0-9]{64})\\.json',record['manifest'])
                require(match is not None and record['signature']==record['manifest'][:-5]+'.minisig','invalid_app_catalog_record',503)
                value=self._verify(match[1]);self._artifacts(value);values.append(value)
            except (ValueError,OSError) as error:
                code=getattr(error,'code',str(error));errors.append(code if re.fullmatch('[a-z][a-z0-9_]{0,79}',code) else 'app_catalog_invalid')
        meta=db.execute('SELECT * FROM apps_meta WHERE id=1').fetchone()
        current=meta['catalog_hash'];water=json.loads(meta['highwater'])
        for value in sorted(values,key=lambda item:item['sequence']):
            try:
                accepted=self.contract.accept_catalog(value,water)
                digest=value['_manifest_sha256']
                if current!=digest:
                    db.execute('INSERT OR IGNORE INTO apps_catalogs VALUES(?,?)',(digest,canonical(value)))
                    db.execute('UPDATE apps_meta SET highwater=?,catalog_hash=? WHERE id=1',(canonical(accepted),digest))
                    self._advance(db);current=digest;water=accepted
            except ValueError as error:errors.append(str(error))
        if current is None:return None,errors
        try:
            value=self._verify(current)
            if self.catalog_observer is not None:
                self.catalog_observer([entry['descriptor'] for entry in value['apps']])
            return value,errors
        except (ValueError,OSError):return None,errors+['accepted_app_catalog_unavailable']

    def _devices(self,db):
        return sorted(device for device in self.service.devices(db)
            if self.service.binding(device,db)=={'role':'student','platform':'debian-amd64'})

    def _report(self,db,device):
        row=db.execute('SELECT * FROM apps_reports WHERE device=?',(device,)).fetchone()
        return (json.loads(row['body']),row['seen']) if row else (None,None)

    def _capable(self,db,device,report):
        if report is None or report['capabilities']!=['apps-v1']:return False
        legacy=db.execute('SELECT installed FROM reports WHERE device=?',(device,)).fetchone()
        if legacy is None:return False
        observed={item['component']:item['version'] for item in json.loads(legacy[0])}
        return (report['updater_version'] is not None and report['core_version'] is not None
            and observed.get('updater')==report['updater_version'] and observed.get('studentcore')==report['core_version']
            and self.contract.compare_versions(report['updater_version'],'1.1.0~')>=0)

    def overview(self):
        with self.service.transaction() as db:
            value,errors=self.catalog(db);devices=[];metadata={}
            if value is not None:
                for entry in value['apps']:
                    app=entry['descriptor'];metadata[app['id']]={'descriptor':app,'catalog_sha256':value['_manifest_sha256'],'retired':False}
            for device in self._devices(db):
                report,seen=self._report(db,device)
                devices.append({'device':device,'role':'student','platform':'debian-amd64','last_seen':seen,
                    'capable':self._capable(db,device,report),'core_version':report['core_version'] if report else None,
                    'installed':report['installed'] if report else [],'issues':report['issues'] if report else [],
                    'inventory_known':report is not None})
            # Retired apps remain visible and removable from an accepted historical catalog.
            installed_ids={item['id'] for device in devices for item in device['installed'] if item['state']!='absent'}
            for row in db.execute('SELECT * FROM apps_catalogs ORDER BY rowid DESC'):
                historical=json.loads(row['document'])
                for entry in historical['apps']:
                    app=entry['descriptor']
                    if app['id'] in installed_ids and app['id'] not in metadata:
                        metadata[app['id']]={'descriptor':app,'catalog_sha256':row['hash'],'retired':True}
            if self.catalog_observer is not None:
                self.catalog_observer([item['descriptor'] for item in metadata.values()])
            return {'schema':1,'revision':self._revision(db),'catalog':list(metadata.values()),'catalog_errors':errors,
                'devices':devices,'jobs':[dict(row) for row in db.execute('SELECT * FROM apps_jobs ORDER BY revision DESC,job_id')],
                'defaults':json.loads(db.execute('SELECT defaults FROM apps_meta WHERE id=1').fetchone()[0]),
                'catalog_sha256':value['_manifest_sha256'] if value else None}

    def _command(self,db,body,endpoint):
        self.service.command_id(body['command_id'])
        encoded=canonical({'endpoint':endpoint,'body':body})
        old=db.execute('SELECT * FROM commands WHERE id=?',(body['command_id'],)).fetchone()
        if old:
            require(old['body']==encoded,'command_id_reused',409)
            return encoded,json.loads(old['receipt'])
        require(type(body['expected_revision']) is int and body['expected_revision']==self._revision(db),'stale_revision',409)
        return encoded,None

    def _entry(self,db,app_id,digest,*,current=None,remove=False):
        self.contract.component_for_app(app_id)
        require(type(digest) is str and HASH.fullmatch(digest),'invalid_app_catalog_hash')
        row=db.execute('SELECT 1 FROM apps_catalogs WHERE hash=?',(digest,)).fetchone()
        require(row is not None,'app_catalog_not_accepted',409)
        require(remove or current is not None and current['_manifest_sha256']==digest,'app_catalog_changed',409)
        value=self._verify(digest)
        entries=[entry for entry in value['apps'] if entry['descriptor']['id']==app_id]
        require(len(entries)==1,'app_not_in_catalog')
        if not remove:self._artifacts(value)
        return entries[0]

    def _queue(self,db,device,app_id,action,digest,revision):
        db.execute("UPDATE apps_jobs SET state='superseded',code='newer_app_intent' WHERE device=? AND app_id=? AND state='queued'",(device,app_id))
        job=uuid.uuid4().hex
        db.execute('INSERT INTO apps_jobs VALUES(?,?,?,?,?,?,?,NULL,?)',
            (job,device,app_id,action,digest,revision,'queued',self.service.clock().isoformat()))
        return job

    def order(self,body):
        fields(body,('command_id','expected_revision','devices','app_id','action','catalog_sha256'))
        require(body['action'] in ('install','update','remove'),'invalid_app_action')
        require(type(body['devices']) is list and 1<=len(body['devices'])<=128
            and all(type(d) is str for d in body['devices']) and len(set(body['devices']))==len(body['devices']),'invalid_app_targets')
        with self.service.transaction() as db:
            current,_=self.catalog(db);encoded,old=self._command(db,body,'apps-order')
            if old is not None:return old
            entry=self._entry(db,body['app_id'],body['catalog_sha256'],current=current,remove=body['action']=='remove')
            for device in body['devices']:
                require(device in self._devices(db),'unknown_app_device',403)
                require(not self.service.migration_active(db,device),'migration_in_progress',409)
                report,_=self._report(db,device)
                require(self._capable(db,device,report),'app_updater_bridge_required',409)
                if body['action']!='remove':
                    compatible=self.contract.compatibility(entry['descriptor'],core_version=report['core_version'])
                    require(compatible['compatible'],'app_incompatible',409)
                    actual=next((item for item in report['installed'] if item['id']==body['app_id']),None)
                    if body['action']=='update':require(actual is not None and actual['state']=='installed','app_not_installed',409)
                    if actual is not None and actual['state']=='installed':
                        require(self.contract.compare_versions(entry['descriptor']['version'],actual['version'])>=0,'app_downgrade_rejected',409)
            revision=self._advance(db)
            jobs=[self._queue(db,device,body['app_id'],body['action'],body['catalog_sha256'],revision) for device in body['devices']]
            receipt={'schema':1,'command_id':body['command_id'],'revision':revision,'jobs':jobs}
            db.execute('INSERT INTO commands VALUES(?,?,?)',(body['command_id'],encoded,canonical(receipt)))
            return receipt

    def defaults(self,body):
        fields(body,('command_id','expected_revision','catalog_sha256','apps'))
        require(type(body['apps']) is list and len(body['apps'])<=128 and all(type(a) is str for a in body['apps'])
            and len(set(body['apps']))==len(body['apps']),'invalid_default_apps')
        with self.service.transaction() as db:
            current,_=self.catalog(db);encoded,old=self._command(db,body,'apps-defaults')
            if old is not None:return old
            for app_id in body['apps']:self._entry(db,app_id,body['catalog_sha256'],current=current)
            revision=self._advance(db);apps=sorted(body['apps'])
            db.execute('UPDATE apps_meta SET defaults=? WHERE id=1',(canonical(apps),))
            receipt={'schema':1,'command_id':body['command_id'],'revision':revision,'defaults':apps}
            db.execute('INSERT INTO commands VALUES(?,?,?)',(body['command_id'],encoded,canonical(receipt)))
            return receipt

    def _validate_poll(self,body):
        fields(body,('schema','capabilities','updater_version','core_version','installed','issues','result'))
        require(type(body['schema']) is int and body['schema']==1 and body['capabilities']==['apps-v1'],'invalid_app_poll')
        for key in ('updater_version','core_version'):
            if body[key] is not None:self.contract.compare_versions(body[key],body[key])
        require(type(body['installed']) is list and len(body['installed'])<=128 and type(body['issues']) is list and len(body['issues'])<=128,'invalid_app_inventory')
        seen=set()
        for item in body['installed']:
            fields(item,('id','package','version','state'));self.contract.component_for_app(item['id'])
            expected='makervaerkstedet-app-'+item['id']
            require((item['package']==expected or item['id']=='scratch' and item['package']=='makervaerkstedet-scratch')
                and item['id'] not in seen and item['state'] in STATES,'invalid_app_inventory')
            seen.add(item['id'])
            if item['state']=='installed':self.contract.compare_versions(item['version'],item['version'])
            elif item['state']=='absent':require(item['version'] is None,'invalid_app_inventory')
            elif item['version'] is not None:self.contract.compare_versions(item['version'],item['version'])
        for issue in body['issues']:
            fields(issue,('app_id','code'));self.contract.component_for_app(issue['app_id'])
            require(type(issue['code']) is str and re.fullmatch('[a-z][a-z0-9_]{0,79}',issue['code']),'invalid_app_issue')
        result=body['result']
        if result is not None:
            fields(result,('job_id','revision','state','code'))
            require(type(result['job_id']) is str and re.fullmatch('[a-f0-9]{32}',result['job_id'])
                and type(result['revision']) is int and result['revision']>0 and result['state'] in ('succeeded','failed')
                and (result['code'] is None or type(result['code']) is str and re.fullmatch('[a-z][a-z0-9_]{0,79}',result['code'])),'invalid_app_result')

    def poll(self,device,body):
        self._validate_poll(body)
        with self.service.transaction() as db:
            require(device in self._devices(db),'unknown_app_device',403)
            current,_=self.catalog(db)
            db.execute('INSERT OR REPLACE INTO apps_reports VALUES(?,?,?)',(device,canonical(body),self.service.clock().isoformat()))
            result=body['result']
            if result is not None:
                job=db.execute('SELECT * FROM apps_jobs WHERE job_id=? AND device=?',(result['job_id'],device)).fetchone()
                require(job is not None and job['revision']==result['revision'],'unknown_app_job',409)
                if job['state']=='queued':
                    state,code=result['state'],result['code']
                    if state=='succeeded':
                        actual=next((item for item in body['installed'] if item['id']==job['app_id']),None)
                        expected=self._entry(db,job['app_id'],job['catalog_hash'],remove=True)['descriptor']
                        ok=actual is not None and (actual['state']=='absent' if job['action']=='remove' else
                            actual['state']=='installed' and actual['version']==expected['version'] and actual['package']==expected['package'])
                        if not ok:state,code='failed','app_observed_state_mismatch'
                    db.execute('UPDATE apps_jobs SET state=?,code=? WHERE job_id=?',(state,code,job['job_id']))
            self.service.complete_migration(db,device,admin=self)
            migrating=self.service.migration_active(db,device)
            marker=db.execute('SELECT defaults_done FROM apps_enrolled WHERE device=?',(device,)).fetchone()
            if marker is None:db.execute('INSERT INTO apps_enrolled VALUES(?,0)',(device,));done=False
            else:done=bool(marker[0])
            capable=self._capable(db,device,body)
            if not done and capable and current is not None and not migrating:
                selected=json.loads(db.execute('SELECT defaults FROM apps_meta WHERE id=1').fetchone()[0])
                available={entry['descriptor']['id']:entry['descriptor'] for entry in current['apps']}
                if all(app_id in available and self.contract.compatibility(available[app_id],core_version=body['core_version'])['compatible'] for app_id in selected):
                    present={item['id'] for item in body['installed'] if item['state']=='installed'}
                    missing=[app_id for app_id in selected if app_id not in present]
                    if missing:
                        revision=self._advance(db)
                        for app_id in missing:self._queue(db,device,app_id,'install',current['_manifest_sha256'],revision)
                    db.execute('UPDATE apps_enrolled SET defaults_done=1 WHERE device=?',(device,))
            jobs=[]
            if capable and not migrating:
                for row in db.execute("SELECT * FROM apps_jobs WHERE device=? AND state='queued' ORDER BY revision,job_id LIMIT 16",(device,)):
                    jobs.append({'job_id':row['job_id'],'revision':row['revision'],'action':row['action'],
                                 'app_id':row['app_id'],'catalog_sha256':row['catalog_hash']})
            return {'schema':1,'jobs':jobs}

    def catalog_file(self,digest,extension):
        require(type(digest) is str and HASH.fullmatch(digest) and extension in ('json','minisig'),'invalid_app_catalog_path',404)
        with self.service.transaction() as db:
            self.catalog(db)
            require(db.execute('SELECT 1 FROM apps_catalogs WHERE hash=?',(digest,)).fetchone() is not None,'app_catalog_not_accepted',404)
        return plain(self.service.cache/'manifests'/(digest+'.'+extension))
