"""Fixed Inno lifecycle for server/admin and laser/current-user roles.

No operation runs at import; --apply is explicit. Source tests inject the OS
edge. Program rollback never restores project data or queue databases.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import ctypes
import hashlib
import http.client
import ipaddress
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

class LifecycleError(ValueError): pass

def require(condition, message):
    if not condition: raise LifecycleError(message)

def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()

def plain(path):
    path=Path(path).absolute()
    for entry in (path,*path.parents):
        require(not entry.is_symlink() and not entry.is_junction(),'linked_path_rejected')
    return path

def read(path): return json.loads(plain(path).read_text(encoding='utf-8-sig'))
def record(path,value):
    temporary=path.with_suffix('.pending')
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

def verify_payload(root, expected):
    root=plain(root);manifest=root/'installer-payload.json'
    require(re.fullmatch('[a-f0-9]{64}',expected or '') is not None and digest(manifest)==expected,'payload_manifest_hash_mismatch')
    value=read(manifest)
    require(set(value)=={'schema','role','version','files','test_artifact'} and value['schema']==1 and value['role'] in ('server','laser_station'),'invalid_payload_manifest')
    require(isinstance(value['files'],dict) and value['files'],'empty_payload')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    require(actual==set(value['files'])|{'installer-payload.json'},'incomplete_payload_inventory')
    for name,expected_hash in value['files'].items():
        relative=PurePosixPath(name)
        require(not relative.is_absolute() and str(relative)==name and '..' not in relative.parts and not any(c in name for c in '\\:*?"<>|'),'unsafe_payload_path')
        require(re.fullmatch('[a-f0-9]{64}',expected_hash or '') is not None and digest(plain(root/name))==expected_hash,'payload_file_hash_mismatch')
    require('package/runtime/python.exe' in value['files'],'embedded_runtime_required')
    return value

def tree(root):
    result={}
    for path in plain(root).rglob('*'):
        plain(path)
        require(path.is_file() or path.is_dir(),'unsupported_program_entry')
        if path.is_file():result[path.relative_to(root).as_posix()]=digest(path)
    return result

def backup_program(program, backup):
    require(not backup.exists(),'backup_already_exists')
    before=tree(program)
    shutil.copytree(plain(program),backup)
    require(tree(backup)==before,'program_backup_hash_mismatch')
    return before

def restore_program(program, backup, expected):
    # Only the fixed program root is restored. The data/project roots are never
    # passed here, including after the replacement program has accepted writes.
    require(tree(backup)==expected,'program_backup_changed')
    failed=program.with_name(program.name+'.failed-'+uuid.uuid4().hex)
    if program.exists():program.rename(failed)
    shutil.copytree(backup,program)
    require(tree(program)==expected,'program_restore_hash_mismatch')
    return str(failed)

def detect_action(role,program,state):
    """Read installed identity before creating an update journal or stopping tasks."""
    program,state=plain(program),plain(state)
    if not program.exists() and not state.exists():return 'install'
    message='Installationen kunne ikke genkendes. Mapperne er bevaret. Brug den eksisterende administrationsvej til at kontrollere installationen.'
    require(program.is_dir() and state.is_dir(),message)
    try:
        if role=='server':
            owner=read(state/'installation-owner.json')
            require(type(owner.get('schemaVersion')) is int and owner['schemaVersion']==1
                and owner.get('state')=='installed' and owner.get('product')=='ElevhubServer'
                and owner.get('taskName')=='ElevhubServer'
                and plain(owner.get('installRoot',''))==program and plain(owner.get('dataRoot',''))==state
                and not (program/'control').exists(),message)
            require(str(uuid.UUID(owner.get('ownerId','')))==owner['ownerId'],message)
            require((state/'konfiguration.json').is_file(),message)
        else:
            require(role=='laser_station',message)
            from app import laser_station_install as station
            station.active_installation(program)
    except (OSError,ValueError,TypeError,KeyError):
        raise LifecycleError(message) from None
    return 'update'

def validate_server_profile(profile):
    required={'school_name','server_mode','role_ip','student_subnet','theme','machine'}
    require(set(profile)==required and profile['server_mode'] in ('vm','makerspace')
        and profile['theme'] in ('red','yellow','green') and profile['machine'] in ('fbb2','fhexa1'),
        'Vælg en understøttet serverrolle, farve og lasermodel.')
    require(isinstance(profile['school_name'],str) and 0<len(profile['school_name'].strip())<=80
        and all(ord(c)>=32 and ord(c)!=127 for c in profile['school_name']), 'Skriv skolens navn med 1–80 tegn.')
    try:
        ipaddress.IPv4Address(profile['role_ip']);ipaddress.IPv4Network(profile['student_subnet'],strict=False)
    except (ValueError,TypeError):raise LifecycleError('Skriv en gyldig IPv4-adresse og elevnettets CIDR-subnet.') from None
    return profile

def runtime_identity(program,role):
    value=read(plain(program)/'runtime-release.json')
    require(set(value)=={'schema','release_id','role'} and type(value['schema']) is int and value['schema']==1
            and value['role']==role and isinstance(value['release_id'],str)
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+~_-]{0,127}',value['release_id']), 'invalid_runtime_release_identity')
    return value['release_id']


def health_targets(config,owner,state):
    """Follow configured backend ownership; external means separately owned, even on loopback."""
    ports=config.get('backend_ports',{})
    require(all(type(ports.get(name)) is int and 1<=ports[name]<=65535 for name in ('print','design','teacher')),'explicit_backend_ports_required')
    external=external_database_components(owner)
    configured=config.get('external_backends',[])
    require(type(configured) is list and set(configured)==set(external),'backend_ownership_configuration_mismatch')
    targets=[]
    for component,app_id,service,path,surface in (
            ('print','3dprint','printvaerksted','/api/health',None),
            ('design','laser','designvaerksted','/api/laser/health','student')):
        host='127.0.0.1';port=ports[component];scheme='http'
        if component in external:
            apps=[row for row in config.get('apps',[]) if row.get('id')==app_id]
            require(len(apps)==1 and isinstance(apps[0].get('bagved'),str),'external_backend_endpoint_required')
            raw=apps[0]['bagved'];url=urlsplit(raw if '://' in raw else '//'+raw)
            require(url.scheme in ('','http','https') and url.hostname and url.port and not url.username and not url.password
                    and url.path in ('','/') and not url.query and not url.fragment,'invalid_external_backend_endpoint')
            host,port,scheme=url.hostname,url.port,url.scheme or 'http'
        targets.append({'component':component,'host':host,'port':port,'scheme':scheme,'path':path,'service':service,
                        'surface':surface,'owned':component not in external})
    if 'design' not in external:
        targets.append({'component':'teacher','host':'127.0.0.1','port':ports['teacher'],'scheme':'http',
                        'path':'/api/laser/health','service':'designvaerksted','surface':'teacher','owned':True})
    host=config.get('laerer_adresse');port=config.get('laerer_port')
    require(isinstance(host,str) and host in ('127.0.0.1','localhost',config.get('elev_adresse')) and type(port) is int and 1<=port<=65535,'configured_teacher_health_required')
    target={'component':'hub','host':host,'port':port,'scheme':'https' if config.get('tls') else 'http',
            'path':'/api/health','service':'elevhub','surface':None,'owned':True}
    if config.get('tls'):
        tls=config['tls'];require(isinstance(tls,dict) and isinstance(tls.get('ca'),str),'health_ca_required')
        target.update(ca_file=str(plain(plain(state)/tls['ca'])),server_hostname=config.get('elev_adresse'))
        require(isinstance(target['server_hostname'],str) and target['server_hostname'],'health_server_identity_required')
    targets.append(target)
    return targets


def validate_health(payload,target,expected_release):
    require(isinstance(payload,dict) and payload.get('ok') is True and payload.get('service')==target['service'],'server_health_identity_mismatch')
    if target['surface'] is not None:require(payload.get('surface')==target['surface'],'server_health_surface_mismatch')
    if target['owned']:
        require(payload.get('release_id')==expected_release,'server_health_release_mismatch')
        require(type(payload.get('pid')) is int and payload['pid']>0,'server_health_process_identity_missing')
        try: uuid.UUID(payload.get('runtime_instance',''))
        except (ValueError,TypeError,AttributeError):raise LifecycleError('server_health_process_identity_missing') from None
    return payload


class _IdentityHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self,target):
        context=ssl.create_default_context(cafile=target.get('ca_file'))
        super().__init__(target['host'],target['port'],timeout=2,context=context)
        self.server_hostname=target.get('server_hostname',target['host'])
    def connect(self):
        http.client.HTTPConnection.connect(self)
        self.sock=self._context.wrap_socket(self.sock,server_hostname=self.server_hostname)


def fetch_health(target):
    connection=(_IdentityHTTPSConnection(target) if target['scheme']=='https'
                else http.client.HTTPConnection(target['host'],target['port'],timeout=2))
    try:
        connection.request('GET',target['path'],headers={'Accept':'application/json'})
        response=connection.getresponse();body=response.read(65537)
        require(response.status==200 and len(body)<=65536,'server_health_http_failed')
        return json.loads(body)
    finally:connection.close()


def external_database_components(owner):
    values=owner.get('externalBackends',[])
    require(type(values) is list and all(isinstance(value,str) and value in ('print','design') for value in values)
        and len(set(values))==len(values),'Installationsejerens eksterne komponenter er ugyldige.')
    return frozenset(values)

def require_local_print_ownership(owner):
    # An installation whose print queue belongs to a separate, older print
    # program has its own startup, camera and data authority. This installer
    # only updates installations that own their print data themselves.
    require('print' not in external_database_components(owner),
            'external_print_installation_not_supported; existing print tasks and data are preserved')


def manual_database_backup(state,destination,*,external_components=frozenset()):
    """Consistent local snapshots, retained only for deliberate manual recovery."""
    from app.backup_databases import snapshot_database
    state,destination=plain(state),plain(destination)
    require(type(external_components) is frozenset and external_components<=frozenset(('print','design')),'invalid_external_database_components')
    require(not destination.exists(),'database_backup_already_exists')
    destination.mkdir()
    files={};absent=[]
    for relative,role in (('hub/makerspace.sqlite3','hub'),('printvaerksted/queue.sqlite3','print'),('designvaerksted/afleveringer.sqlite3','laser')):
        source=plain(state/relative)
        if not source.is_file():
            component='design' if role=='laser' else role
            require(component in external_components,'En lokal database mangler. Programmet er ikke udskiftet.')
            absent.append(relative);continue
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        snapshot_database(source,target,role=role,timeout=30)
        files[relative]={'sha256':digest(target),'size':target.stat().st_size,'role':role}
    # This small control-plane DB has its own schema. SQLite's backup API also
    # includes committed WAL bytes; a plain file copy would not be sufficient.
    relative='hub/simple-updates.sqlite3';source=plain(state/relative)
    if source.exists():
        target=destination/relative;deadline=time.monotonic()+30
        def progress(*_):
            if time.monotonic()>deadline:raise TimeoutError('Opdateringsdatabasens backup overskred tidsgrænsen.')
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5)) as src, closing(sqlite3.connect(target,timeout=5)) as dst:
            src.backup(dst,pages=100,progress=progress,sleep=0.05)
            dst.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
            require(dst.execute('PRAGMA quick_check').fetchall()==[('ok',)],'update_database_backup_invalid')
            dst.execute('PRAGMA journal_mode=DELETE');dst.commit()
        files[relative]={'sha256':digest(target),'size':target.stat().st_size,'role':'simple_updates'}
    result={'schema':1,'purpose':'manual_recovery_only','automatic_restore':False,
            'scope':'local_databases_only','external_backends_included':False,'absent_external_databases':absent,'files':files}
    record(destination/'manual-recovery.json',result)
    return result

def prune_obsolete_program(stage,expected_files):
    installed_metadata={'installation.json','school-update.json','server-launch.json'}
    old={}
    if (stage/'integrity.json').is_file():
        marker=read(stage/'integrity.json')
        require(isinstance(marker,dict) and marker.get('algorithm')=='sha256' and isinstance(marker.get('files'),dict),'old_program_inventory_invalid')
        old=marker['files']
        for name in old:
            relative=PurePosixPath(name)
            require(str(relative)==name and not relative.is_absolute() and '..' not in relative.parts
                    and not any(c in name for c in '\\:*?"<>|'),'unsafe_old_program_inventory')
    obsolete=set(old)-set(expected_files)-installed_metadata
    removed=[]
    for name in tree(stage):
        parts={Path(part).stem.casefold() for part in PurePosixPath(name).parts}
        retired=bool(parts&{'skolegpt','kredsloeb','datatraener','circuitjs','circuitjs1','cadoodle'}) or Path(name).name=='native-3d.js'
        retired=retired or Path(name).name.casefold().startswith(('workshop-data-','workshop-circuit-'))
        if name not in expected_files and (name in obsolete or retired):
            plain(stage/name).unlink();removed.append(name)
    return removed


def cleanup_duplicate_program(program,parked,backup,expected):
    program,parked,backup=plain(program),plain(parked),plain(backup)
    require(parked.parent==program.parent and parked.name.startswith(program.name+'.previous-'),'unsafe_parked_cleanup')
    suffix=parked.name[len(program.name+'.previous-'):]
    require(str(uuid.UUID(suffix))==suffix,'unsafe_parked_cleanup')
    require(tree(backup)==expected and tree(parked)==expected,'program_cleanup_backup_mismatch')
    shutil.rmtree(parked)


def recovery_code(error):
    value=str(error)
    return value if re.fullmatch(r'[a-zA-Z0-9_:-]{1,180}',value) else type(error).__name__


def recovery_failure(journal,operation,phase,original,recovery,**extra):
    record(journal/'state.json',{'phase':phase,'operation_id':operation,'manual_recovery_required':True,
        'queue_may_remain_paused':True,'original_error':recovery_code(original),'recovery_error':recovery_code(recovery),**extra})
    raise LifecycleError(phase+':'+recovery_code(original)+':'+recovery_code(recovery)) from original


def update_server(package, program, journal, native, *, expected_files=None):
    expected_files=tree(package) if expected_files is None else expected_files
    require(tree(package)==expected_files,'source_package_changed')
    journal=plain(journal);require(not journal.exists(),'operation_journal_exists')
    operation=journal.name;require(str(uuid.UUID(operation))==operation,'invalid_operation_id')
    native.preflight_update(program,journal)
    journal.mkdir(parents=True);native.protect_journal(journal)
    try:native.acquire_pause(operation);native.assert_pause(operation)
    except BaseException:
        record(journal/'state.json',{'phase':'pause_incomplete_no_stop','operation_id':operation,'queue_may_remain_paused':True})
        raise
    record(journal/'state.json',{'phase':'pause_held','operation_id':operation})
    stopped=False;stop_attempted=False;before=None;replaced=False;configure_attempted=False
    try:
        stop_attempted=True;native.stop_owned(journal);stopped=True
        native.backup_databases(journal)
        before=backup_program(program,journal/'program-before')
        stage=program.with_name(program.name+'.candidate-'+operation)
        require(not stage.exists(),'candidate_path_exists')
        shutil.copytree(program,stage)
        require(tree(stage)==before,'existing_program_changed_during_staging')
        removed=prune_obsolete_program(stage,expected_files)
        shutil.copytree(package,stage,dirs_exist_ok=True)
        # Preserve installed metadata and other existing program files; new
        # package files are checked byte-for-byte before replacement.
        require(tree(package)==expected_files and all(digest(stage/name)==want for name,want in expected_files.items()),'candidate_copy_hash_mismatch')
        parked=program.with_name(program.name+'.previous-'+operation)
        require(not parked.exists(),'parked_program_exists')
        record(journal/'state.json',{'phase':'before_replace','operation_id':operation,'program':str(program),'parked':str(parked),'program_backup':before,'removed_obsolete_files':removed})
        program.rename(parked)
        try:stage.rename(program)
        except BaseException:parked.rename(program);raise
        replaced=True
        configure_attempted=True;native.configure_simple(package,journal)
        record(journal/'state.json',{'phase':'program_replaced','operation_id':operation,'program_backup':before})
        native.restore_owned(journal);stopped=False
        native.health();native.assert_pause(operation)
        cleanup='duplicate_removed'
        try:cleanup_duplicate_program(program,parked,journal/'program-before',before)
        except (OSError,ValueError):cleanup='duplicate_retained_for_manual_review'
        record(journal/'state.json',{'phase':'complete_paused','operation_id':operation,'program_backup':before,'cleanup':cleanup})
        return {'status':'complete_paused','operation_id':operation,'cleanup':cleanup}
    except BaseException as original:
        if replaced:
            try:native.stop_owned(journal);stopped=True
            except BaseException as failure:
                recovery_failure(journal,operation,'rollback_stop_failed_manual_recovery_required',original,failure,program_backup=before)
            try:
                restore_program(program,journal/'program-before',before)
                if configure_attempted:native.restore_simple(journal)
            except BaseException as failure:
                recovery_failure(journal,operation,'program_restore_failed_manual_recovery_required',original,failure,program_backup=before)
            record(journal/'state.json',{'phase':'program_restored_paused','operation_id':operation,'program_backup':before})
        if stopped or (stop_attempted and not replaced):
            try:native.restore_owned(journal)
            except BaseException as failure:
                recovery_failure(journal,operation,'task_restore_failed_manual_recovery_required',original,failure,program_backup=before)
        # Never release queue hold or restore databases, even after failure.
        raise

class NativeWindows:
    def __init__(self,payload,role):
        require(os.name=='nt','windows_required')
        self.payload,self.role=plain(payload),role
        administrator=bool(ctypes.windll.shell32.IsUserAnAdmin())
        require((role=='server' and administrator) or (role=='laser_station' and not administrator),'incorrect_role_privilege')
        self.program=plain(Path(os.environ['ProgramFiles'])/'ElevhubServer') if role=='server' else plain(Path(os.environ['LOCALAPPDATA'])/'MakervaerkstedetLaserstation')
        self.state=plain(Path(os.environ['ProgramData'])/'ElevhubServer') if role=='server' else self.program
        self.powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        self.barrier=None
    def command(self,script,*arguments):
        environment=dict(os.environ);environment['PSModulePath']=str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/Modules')
        environment['PATH']=str(Path(os.environ['SystemRoot'])/'System32')+';'+str(self.powershell.parent)+';'+str(Path(os.environ['SystemRoot'])/'System32/OpenSSH')
        subprocess.run([str(self.powershell),'-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(plain(script)),*map(str,arguments)],check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,stdin=subprocess.DEVNULL,env=environment)
    def protect_journal(self,journal):
        tool=Path(os.environ['SystemRoot'])/'System32/icacls.exe'
        for protected in (journal.parent,journal):
            subprocess.run([str(tool),str(protected),'/setowner','*S-1-5-32-544'],check=True,stdout=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            subprocess.run([str(tool),str(protected),'/inheritance:r','/grant:r','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F'],check=True,stdout=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    def preflight_update(self,program,journal):
        require(program==self.program and not (program/'control').exists(),'flat_layout_required_explicit_generation_migration')
        owner=read(self.state/'installation-owner.json')
        require(owner.get('state')=='installed' and owner.get('product')=='ElevhubServer'
                and os.path.normcase(owner.get('installRoot',''))==os.path.normcase(str(program))
                and os.path.normcase(owner.get('dataRoot',''))==os.path.normcase(str(self.state)),'existing_installation_owner_mismatch')
        self.command(self.payload/'task_control.ps1','-Action','Inspect','-Journal',journal)
        config=read(self.state/'konfiguration.json')
        require_local_print_ownership(owner)
        from mkv2.control_plane.maintenance import MachineMaintenanceBarrier
        self.barrier=MachineMaintenanceBarrier(journal.name,printer_profile=config.get('makerspace_printers'),
            laser_profile=config.get('makerspace_laser_queue'),configuration_root=self.state)
    def acquire_pause(self,operation):self.barrier.acquire('server')
    def assert_pause(self,operation):
        require(self.barrier is not None,'maintenance_barrier_not_prepared')
        self.barrier.assert_held('server')
    def stop_owned(self,journal):self.command(self.payload/'task_control.ps1','-Action','Stop','-Journal',journal)
    def restore_owned(self,journal):self.command(self.payload/'task_control.ps1','-Action','Restore','-Journal',journal)
    def configure_simple(self,package,journal):self.command(self.payload/'task_control.ps1','-Action','ConfigureSimple','-Journal',journal,'-PackageRoot',package)
    def restore_simple(self,journal):self.command(self.payload/'task_control.ps1','-Action','RestoreSimple','-Journal',journal)
    def backup_databases(self,journal):
        owner=read(self.state/'installation-owner.json')
        return manual_database_backup(self.state,journal/'databases-manual',external_components=external_database_components(owner))
    def health(self):
        expected=runtime_identity(self.program,'server')
        targets=health_targets(read(self.state/'konfiguration.json'),read(self.state/'installation-owner.json'),self.state)
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            try:
                results={target['component']:validate_health(fetch_health(target),target,expected) for target in targets}
                if 'teacher' in results:
                    require(results['teacher']['runtime_instance']==results['design']['runtime_instance']
                            and results['teacher']['pid']==results['design']['pid'],'design_teacher_process_mismatch')
                return
            except (OSError,ValueError,TypeError,http.client.HTTPException):time.sleep(0.3)
        raise LifecycleError('server_health_failed')
    def install_server(self,package,profile):
        validate_server_profile(profile)
        require(not self.program.exists() and not self.state.exists(),'install_requires_empty_fixed_roots')
        arguments=['-PackageRoot',package,'-SchoolName',profile['school_name'],'-Role',profile['server_mode'],'-RoleIp',profile['role_ip'],'-StudentSubnet',profile['student_subnet'],'-Theme',profile['theme'],'-Machine',profile['machine'],'-AcceptInstall']
        if profile['server_mode']=='vm':arguments.append('-RemoteLaserStation')
        self.command(package/'deployment/school-setup/Setup-School.ps1',*arguments)
        require((self.state/'installation-owner.json').is_file(),'installation_receipt_missing')
        self.health()
        return {'status':'installed','role':'server'}
    def laser(self,package,action,profile):
        from app import laser_station_install as station
        require(action in ('install','update'),'unsupported_action')
        if action=='install':
            require(not (Path(os.environ['LOCALAPPDATA'])/'Laserstation').exists(),'older_laser_station_must_be_removed_first; existing station and plates are preserved')
            require(set(profile)<={'connection_file','plates','beam_studio','flux_api'} and profile.get('connection_file'),'laser_connection_required')
            result=station.install(package,self.program,action='Install',**profile)
            self.command(package/'deployment/school-setup/Setup-LaserStation.ps1','-Action','Update','-InstallPath',self.program,'-FinishSetup')
            return result
        require(not profile,'update_preserves_existing_configuration')
        old=station.active_record(self.program)
        station.stop_station(self.program)
        try:
            result=station.install(package,self.program,action='Update')
            self.command(package/'deployment/school-setup/Setup-LaserStation.ps1','-Action','Update','-InstallPath',self.program,'-FinishSetup')
            return result
        except BaseException:
            # This selector is program/config metadata; plates and central queues
            # are never restored. Existing installer keeps previous versions.
            station.atomic_json(self.program/'active.json',old)
            raise

class WindowsInstallLock:
    """One process owns a role/root operation before inspecting or creating journals."""
    def __init__(self,name):self.name=name;self.handle=None
    def __enter__(self):
        require(os.name=='nt','windows_required')
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p];self.api.CreateMutexW.restype=ctypes.c_void_p
        self.api.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];self.api.WaitForSingleObject.restype=ctypes.c_ulong
        self.api.ReleaseMutex.argtypes=[ctypes.c_void_p];self.api.CloseHandle.argtypes=[ctypes.c_void_p]
        self.handle=self.api.CreateMutexW(None,False,self.name)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        result=self.api.WaitForSingleObject(self.handle,0)
        if result not in (0,0x80):
            self.api.CloseHandle(self.handle);self.handle=None
            if result==258:raise LifecycleError('another_installation_is_running')
            raise ctypes.WinError(ctypes.get_last_error())
        return self
    def __exit__(self,*ignored):
        if self.handle:self.api.ReleaseMutex(self.handle);self.api.CloseHandle(self.handle);self.handle=None


def installation_lock_name(role,program):
    key=hashlib.sha256(str(plain(program)).casefold().encode('utf-8')).hexdigest()[:20]
    return 'Global\\Makervaerkstedet.Install.'+role+'.'+key


def run(payload,expected,action,profile=None,*,apply=False):
    value=verify_payload(payload,expected);role=value['role'];package=Path(payload)/'package'
    require(action in ('auto','install','update'),'unsupported_action')
    if not apply:return {'status':'validated_only','role':role,'action':action,'version':value['version']}
    require(value['test_artifact'] is False,'test_artifact_installation_forbidden')
    sys.path.insert(0,str(package));sys.path.insert(0,str(package/'hub'))
    native=NativeWindows(payload,role);profile=profile or {}
    with WindowsInstallLock(installation_lock_name(role,native.program)):
        detected=detect_action(role,native.program,native.state)
        require(action=='auto' or action==detected,'Handlingen passer ikke til den kendte installation. Ingen program- eller datafiler er ændret.')
        action=detected
        if role=='laser_station':return native.laser(package,action,profile)
        if action=='install':return native.install_server(package,profile)
        require(not profile,'update_preserves_existing_configuration')
        journal=native.state/'installer-updates'/str(uuid.uuid4())
        expected_files={name[len('package/'):]:want for name,want in value['files'].items() if name.startswith('package/')}
        return update_server(package,native.program,journal,native,expected_files=expected_files)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--payload',required=True,type=Path);parser.add_argument('--payload-sha256',required=True)
    parser.add_argument('--action',choices=('auto','install','update'),default='auto');parser.add_argument('--profile',type=Path);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:print(json.dumps(run(args.payload,args.payload_sha256,args.action,read(args.profile) if args.profile else None,apply=args.apply)))
    except (ValueError,OSError,sqlite3.Error,subprocess.CalledProcessError) as error:parser.exit(2,str(error)+'\n')
