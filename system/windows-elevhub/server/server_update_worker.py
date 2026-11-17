"""Short SYSTEM-only phases around the existing native server installer.

The independent PowerShell task owns EXE execution. This interpreter exits before
the installer renames the program tree. No browser-supplied path or URL is used.
"""
from datetime import datetime, timezone
from contextlib import closing
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import sys
import time
import uuid

PROGRAM = Path(__file__).resolve().parents[1]
if not (PROGRAM/'app/simple_release.py').is_file():
    source = next(p for p in Path(__file__).resolve().parents if (p/'REPOSITORY-LAYOUT.json').is_file())
    sys.path[:0] = [str(source/'system'), str(source/'system/linux-elevhub/server/elevhub')]
else:
    sys.path[:0] = [str(PROGRAM), str(PROGRAM/'hub')]
from app.simple_release import REPOSITORY, verify_manifest, verify_artifact, artifact_url
from release_feed import open_github, plain, strict_json

HASH = re.compile(r'[a-f0-9]{64}')
VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)-(\d{8})\.(\d+)')
REQUEST_FIELDS = {'schema','action','command_id','manifest_sha256'}
MINIMUM_INSTALLER = '2.1.0-20261117.4' # First native EXE supporting /UPDATEID and the locked version guard.
NATIVE_JOURNAL_MAXIMUM = 16 * 1024**2 # Per-file rollback inventory exceeds 1 MiB in a normal server package.
TEMPLATE_SETTINGS = {'repository','minisign_exe','minisign_sha256','public_key','apt_cache','source'}


class UpdateFailure(ValueError):
    def __init__(self, code): self.code=code; super().__init__(code)


def require(condition, code):
    if not condition: raise UpdateFailure(code)


def read(path, maximum=65536):
    path=plain(path)
    require(path.stat().st_size<=maximum,'metadata_too_large')
    return strict_json(path.read_bytes())


def write(path, value):
    path=plain(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=plain(path.with_name('.'+path.name+'-'+uuid.uuid4().hex))
    try:
        with temporary.open('xb') as stream:
            stream.write((json.dumps(value,sort_keys=True)+'\n').encode());stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally: temporary.unlink(missing_ok=True)


def version(value):
    match=VERSION.fullmatch(value or '')
    require(match is not None,'unsupported_server_version')
    return tuple(map(int,match.groups()))


def file_hash(path):
    with plain(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


class Worker:
    def __init__(self, store, program, data, *, opener=open_github, verifier=verify_manifest, health_check=None):
        self.store,self.program,self.data=map(plain,(store,program,data))
        self.opener,self.verifier=opener,verifier
        self.health_check=health_check or self.health
        self.state_path=self.store/'state.json'

    def installed_version(self):
        marker=read(self.program/'runtime-release.json')
        require(marker.get('schema')==1 and marker.get('role')=='server','server_identity_missing')
        version(marker.get('release_id'))
        return marker['release_id']

    def status(self):
        if self.state_path.exists():return read(self.state_path)
        return {'schema':1,'installed_version':self.installed_version(),'candidate':None,'state':'idle',
                'command_id':None,'code':None,'downloaded_bytes':0,'total_bytes':0,'updated_at':None,'queues_paused':None}

    def save(self, **changes):
        state=self.status();state.update(changes,updated_at=datetime.now(timezone.utc).isoformat())
        write(self.state_path,state)
        return state

    def request(self, action):
        request=read(self.store/'inbox/request.json',4096)
        require(set(request)==REQUEST_FIELDS and type(request['schema']) is int and request['schema']==1,'invalid_request')
        require(request['action']==action and type(request['command_id']) is str,'invalid_request')
        try: require(str(uuid.UUID(request['command_id']))==request['command_id'] and uuid.UUID(request['command_id']).int!=0,'invalid_request')
        except (ValueError,TypeError,AttributeError):raise UpdateFailure('invalid_request') from None
        digest=request['manifest_sha256']
        require(digest is None if action=='check' else isinstance(digest,str) and HASH.fullmatch(digest),'invalid_request')
        receipt=plain(self.store/'receipts'/(request['command_id']+'.json'))
        require(not receipt.exists(),'request_already_recorded')
        write(receipt,request)
        return request

    def trust(self):
        # Public pins from the protected installed package, never writable school config.
        value=read(self.program/'hub/konfiguration.windows.json',1024*1024)['simple_updates']
        require(value['repository']==REPOSITORY,'repository_mismatch')
        return {'minisign_exe':plain(self.program/'tools/minisign/minisign.exe'),
                'minisign_sha256':value['minisign_sha256'],'public_key':value['public_key'],
                'repository':REPOSITORY,'role':'server','platform':'windows-amd64','component':'server',
                'now':datetime.now(timezone.utc)}

    def download(self,url,limit,destination=None):
        total=0;last=0
        with self.opener(url,REPOSITORY) as response:
            if destination is None:
                raw=response.read(limit+1);require(len(raw)<=limit,'download_too_large');return raw
            with plain(destination).open('xb') as output:
                while block:=response.read(min(1024*1024,limit-total+1)):
                    total+=len(block);require(total<=limit,'artifact_size_mismatch');output.write(block)
                    if time.monotonic()-last>=1:
                        self.save(downloaded_bytes=total);last=time.monotonic()
                output.flush();os.fsync(output.fileno())
        require(total==limit,'artifact_size_mismatch')
        self.save(downloaded_bytes=total)

    def authenticated(self,raw,signature):
        manifest=self.verifier(raw,signature,**self.trust())
        require(manifest['role']=='server' and manifest['platform']=='windows-amd64'
                and manifest['component']=='server' and manifest['repository']==REPOSITORY,'role_mismatch')
        require(len(manifest['artifacts'])==1,'one_server_installer_required')
        artifact=manifest['artifacts'][0]
        require(artifact['version']==manifest['version'] and artifact['name']=='Makervaerkstedet-server-Setup.exe','installer_version_mismatch')
        require(version(manifest['version'])>=version(MINIMUM_INSTALLER),'installer_too_old')
        ledger=read(self.store/'highwater.json') if (self.store/'highwater.json').exists() else {'sequence':0,'sha256':None}
        require(manifest['sequence']>=ledger['sequence'],'release_replay_rejected')
        require(manifest['sequence']!=ledger['sequence'] or manifest['_manifest_sha256']==ledger['sha256'],'release_sequence_changed')
        return manifest

    def check(self):
        command=self.request('check')
        # A lost worker must never turn an unresolved installation into a fresh
        # search/install. Only its exact native journal can resolve the outcome.
        if self.unfinished():return self.finish(None)
        self.save(state='checking',command_id=command['command_id'],code=None,downloaded_bytes=0,total_bytes=0)
        metadata=strict_json(self.download('https://api.github.com/repos/'+REPOSITORY+'/releases/latest',2*1024**2))
        tag=metadata.get('tag_name')
        require(type(tag) is str and tag.startswith('v') and not metadata.get('draft') and not metadata.get('prerelease'),'invalid_release')
        version(tag[1:])
        prefix='https://github.com/'+REPOSITORY+'/releases/download/'+tag+'/'
        raw=self.download(prefix+'makervaerkstedet-server.json',1024*1024)
        signature=self.download(prefix+'makervaerkstedet-server.json.minisig',16384)
        manifest=self.authenticated(raw,signature)
        require(tag=='v'+manifest['version'],'release_tag_mismatch')
        write(self.store/'highwater.json',{'sequence':manifest['sequence'],'sha256':manifest['_manifest_sha256']})
        write(self.store/'candidate.json',{'raw':raw.decode('utf-8'),'signature':signature.decode('utf-8')})
        installed=self.installed_version()
        return self.save(state='available' if version(manifest['version'])>version(installed) else 'current',
            installed_version=installed,candidate={'version':manifest['version'],
                'manifest_sha256':manifest['_manifest_sha256'],'size':manifest['artifacts'][0]['size']},code=None)

    def protected(self):
        names=('school.json','installation-owner.json','hub/laerer.json','admin-ssh/id_ed25519','admin-ssh/id_ed25519.pub',
               'security/ca.key','security/ca.crt','security/server.key','security/server.crt','security/enheder.json',
               'designvaerksted/makerspace-laser.key','printvaerksted/makerspace-bridge.key')
        config=read(self.data/'konfiguration.json',2*1024**2)
        settings=config.get('simple_updates')
        require(isinstance(settings,dict),'server_configuration_missing')
        # Native ConfigureSimple may refresh only these packaged trust/feed
        # defaults. School bindings, catalog, ports, apps and unknown keys stay.
        config['simple_updates']={k:v for k,v in settings.items() if k not in TEMPLATE_SETTINGS}
        return {'files':{name:file_hash(self.data/name) for name in names if (self.data/name).is_file()},
            'configuration':hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()}

    def verify_template_settings(self):
        actual=read(self.data/'konfiguration.json',2*1024**2)['simple_updates']
        expected=read(self.program/'hub/konfiguration.windows.json',1024*1024)['simple_updates']
        require({k:actual[k] for k in TEMPLATE_SETTINGS if k in actual}==
                {k:expected[k] for k in TEMPLATE_SETTINGS if k in expected},'protected_data_changed')

    def unfinished(self):
        path=self.store/'operation.json'
        if not path.exists():return False
        operation=read(path)
        if 'outcome' not in operation:return True
        # Recover a crash between saving the terminal receipt and UI state.
        if self.status().get('command_id')==operation['command_id']:
            self.save(**operation['outcome'])
        return False

    def prepare(self):
        require(not self.unfinished(),'operation_unresolved')
        command=self.request('install');before=self.status()
        self.save(command_id=command['command_id'],code=None)
        require(before['state']=='available' and before.get('candidate',{}).get('manifest_sha256')==command['manifest_sha256'],'selected_release_changed')
        cached=read(self.store/'candidate.json',2*1024**2)
        manifest=self.authenticated(cached['raw'].encode(),cached['signature'].encode())
        require(manifest['_manifest_sha256']==command['manifest_sha256'],'selected_release_changed')
        installed=self.installed_version()
        require(version(manifest['version'])>version(installed),'server_already_current')
        directory=plain(self.store/'downloads'/command['command_id']);directory.mkdir(parents=True,exist_ok=False)
        artifact=manifest['artifacts'][0];exe=directory/artifact['name']
        self.save(state='downloading',command_id=command['command_id'],code=None,total_bytes=artifact['size'],downloaded_bytes=0,queues_paused=None)
        self.download(artifact_url(manifest,artifact),artifact['size'],exe)
        self.save(state='verifying');verify_artifact(exe,artifact)
        require(self.installed_version()==installed,'server_version_changed')
        require(not (self.data/'installer-updates'/command['command_id']).exists(),'operation_already_exists')
        write(self.store/'operation.json',{'command_id':command['command_id'],'version':manifest['version'],
            'previous_version':installed,'exe':str(exe),'artifact':artifact,'protected':self.protected()})
        self.save(state='installing')
        return {'exe':str(exe),'sha256':artifact['sha256'],'command_id':command['command_id'],'version':manifest['version']}

    def health(self,expected):
        config=read(self.data/'konfiguration.json',2*1024**2)
        context=ssl.create_default_context(cafile=str(self.data/config['tls']['ca']))
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            connection=http.client.HTTPSConnection(config['elev_adresse'],config['laerer_port'],context=context,timeout=5)
            try:
                connection.request('GET','/api/health');response=connection.getresponse();body=response.read(65537)
                health=strict_json(body)
                if response.status==200 and health.get('ok') is True and health.get('service')=='elevhub' and health.get('release_id')==expected:return True
            except (OSError,ValueError,http.client.HTTPException):pass
            finally:connection.close()
            time.sleep(.5)
        return False

    def paused(self):
        path=self.data/'printvaerksted/queue.sqlite3'
        if not path.exists():return None
        try:
            with closing(sqlite3.connect(plain(path).as_uri()+'?mode=ro',uri=True)) as db:
                row=db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
                return row[0]=='true' if row else None
        except sqlite3.Error:return None

    def cleanup_download(self,operation):
        exe=plain(Path(operation['exe']))
        require(exe==self.store/'downloads'/operation['command_id']/operation['artifact']['name'],'download_path_changed')
        if exe.exists():
            verify_artifact(exe,operation['artifact'])
            exe.unlink() # Only the successful operation's verified EXE; backups/logs stay.

    def terminal(self,operation,**outcome):
        operation['outcome']=outcome
        write(self.store/'operation.json',operation)
        return self.save(**outcome)

    def finish(self,exit_code):
        operation=read(self.store/'operation.json')
        require(self.status()['command_id']==operation['command_id'],'operation_changed')
        if 'outcome' in operation:return self.save(**operation['outcome'])
        try:
            installed=self.installed_version()
            path=self.data/'installer-updates'/operation['command_id']/'state.json'
            journal=read(path,NATIVE_JOURNAL_MAXIMUM) if path.exists() else None
            require(journal is None or journal.get('operation_id')==operation['command_id'],'installer_completion_unconfirmed')
            require(self.protected()==operation['protected'],'protected_data_changed')
            if installed==operation['previous_version']:
                rolled_back=journal and journal.get('phase') in {'program_restored_paused','pause_incomplete_no_stop'} and journal.get('hold_released') is True
                # A known terminated EXE that never created a journal failed its
                # preflight. An unknown interrupted EXE is never inferred safe.
                stopped_before_start=journal is None and exit_code is not None and exit_code!=0
                require(rolled_back or stopped_before_start,'installer_completion_unconfirmed')
                require(self.health_check(installed) is True,'server_health_unconfirmed')
                return self.terminal(operation,state='failed',installed_version=installed,code='installer_failed',queues_paused=self.paused())
            require(installed==operation['version'],'installed_version_mismatch')
            require(journal and journal.get('phase')=='complete_paused' and journal.get('hold_released') is True,'installer_completion_unconfirmed')
            self.verify_template_settings()
            require(self.health_check(installed) is True,'server_health_unconfirmed')
        except (OSError,ValueError) as error:
            return self.save(state='interrupted',code=getattr(error,'code','completion_unconfirmed'),queues_paused=self.paused())
        cleanup=None
        try:self.cleanup_download(operation)
        except (OSError,ValueError):cleanup='download_retained'
        return self.terminal(operation,state='succeeded',installed_version=installed,code=cleanup,queues_paused=self.paused())

    def fail(self,code='worker_failed'):
        operation_path=self.store/'operation.json'
        if operation_path.exists():
            operation=read(operation_path)
            if 'outcome' not in operation:return self.finish(None)
            if self.status().get('command_id')==operation['command_id']:return self.save(**operation['outcome'])
        # No private paths, network exceptions, configuration or process output in UI.
        allowed={'installer_failed','installed_version_mismatch','installer_completion_unconfirmed',
                 'protected_data_changed','server_health_unconfirmed','completion_unconfirmed','worker_failed',
                 'invalid_signature','release_replay_rejected','release_sequence_changed','selected_release_changed',
                 'artifact_hash_mismatch','artifact_size_mismatch','server_already_current','release_tag_mismatch',
                 'server_version_changed','installer_too_old'}
        return self.save(state='failed',code=code if code in allowed else 'worker_failed',queues_paused=self.paused())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--action',choices=('check','prepare','finish','fail'),required=True)
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--exit-code',type=int);parser.add_argument('--code',choices=('worker_failed',),default='worker_failed')
    args=parser.parse_args()
    require(os.name=='nt','windows_required')
    require(plain(args.store)==plain(Path(os.environ['ProgramData'])/'MakervaerkstedetServerUpdate'),'fixed_store_required')
    require(PROGRAM==plain(Path(os.environ['ProgramFiles'])/'ElevhubServer'),'fixed_program_required')
    worker=Worker(args.store,PROGRAM,Path(os.environ['ProgramData'])/'ElevhubServer')
    try:
        result=worker.finish(args.exit_code) if args.action=='finish' else worker.fail(args.code) if args.action=='fail' else getattr(worker,args.action)()
        print(json.dumps(result));return 0
    except (OSError,ValueError,KeyError,TypeError) as error:
        if getattr(error,'code',None)!='request_already_recorded':worker.fail(getattr(error,'code','worker_failed'))
        print(json.dumps({'error':'worker_failed'}));return 1


if __name__=='__main__':raise SystemExit(main())
