"""School's existing flat Windows installation update, never a clean installation.

Credits: Klostermarksskolen. Candidate code and migration run only on copies before
any program write. Task ownership/control lives in Invoke-SchoolUpdate.ps1.
"""
from __future__ import annotations
import argparse
import ast
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import re
import zipfile
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import subprocess
import sys
import traceback
import uuid

FORMAT='makervaerkstedet-school-flat-update-v1'
INSTALLS={'hub':Path(r'C:\Program Files\ElevhubServer'),'print':Path(r'C:\Program Files\Printvaerksted')}
DATA={'hub':Path(r'C:\ProgramData\ElevhubServer'),'print':Path(r'C:\ProgramData\Printvaerksted\data\school')}
ACTIVE={'workflow_wait','preparing','dispatching','starting','printing','recovering','attention'}
# Appearance is installation data. Every release file retains its exact hash.
# Legacy appearance is captured explicitly before this updater is invoked.
APPEARANCE_FORMAT='makervaerkstedet-school-appearance-v1'
APPEARANCE_HOOKS={
    'hub':('app/school_appearance.py','app/server.py','app/laser_server.py','web/school-theme.js','hub/elevhub.py','hub/school_appearance.py'),
    'print':('app/school_appearance.py','app/server.py','app/laser_server.py','web/school-theme.js'),
}

PRIVATE_FEATURE_FILES=frozenset(('bed_ai.py','local_ai.py','vision.py','training_samples.py','laser_vision.py',
    'laser_vision_client.py','laser_vision_server.py','server_ai_artifact.py',
    'plate_camera_client.py','camera-batch.js','plate-camera.js'))
MODEL_SUFFIXES=frozenset(('.onnx','.safetensors','.gguf','.ggml','.tflite','.ckpt','.pt'))
FEATURE_COMPONENT=re.compile(r'^(?:ai|ollama|torch|torchvision|torchaudio|transformers|diffusers|onnxruntime|rembg|transparent_background|segment_anything|llama_cpp|timm|safetensors|huggingface_hub|accelerate|tokenizers)(?:$|[-_.])',re.I)
RUNTIME_TEST_DIRECTORIES=frozenset(('tests','testdata'))
LONG_PATH_ERROR='Windows long-path support is required for the server backup. Enable HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled as DWORD 1 (or apply the matching computer policy), then retry. No scheduled tasks or installed files have been changed.'


class LongPathPolicyError(ValueError):
    code='windows_long_paths_disabled'


def windows_long_paths_enabled(registry=None, platform=None):
    if (os.name if platform is None else platform)!='nt': return True
    try:
        if registry is None: import winreg as registry
        key=registry.OpenKey(registry.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Control\FileSystem')
        try: value,kind=registry.QueryValueEx(key,'LongPathsEnabled')
        finally: registry.CloseKey(key)
        return kind==registry.REG_DWORD and value==1
    except (ImportError,OSError):
        return False


def require_windows_long_paths():
    if not windows_long_paths_enabled(): raise LongPathPolicyError(LONG_PATH_ERROR)


def private_feature_path(relative, educational=False):
    path=Path(relative)
    educational = educational or 'datatraener' in {part.casefold() for part in path.parts}
    return (path.name.casefold() in PRIVATE_FEATURE_FILES
            or (not educational and path.suffix.casefold() in MODEL_SUFFIXES)
            or any(FEATURE_COMPONENT.match(part) for part in path.parts))


def runtime_member_excluded(relative):
    """Exclude third-party test fixtures, but retain executable runtime data/licenses."""
    parts=PurePosixPath(relative).parts
    try: site_packages=parts.index('site-packages')
    except ValueError: return False
    return any(part.casefold() in RUNTIME_TEST_DIRECTORIES for part in parts[site_packages+1:])


def validate_release_file(path,relative):
    if private_feature_path(relative):
        raise ValueError('Excluded private feature in candidate: '+str(relative))
    if path.suffix.casefold() in {'.zip','.whl'}:
        with zipfile.ZipFile(plain(path)) as archive:
            if any(private_feature_path(name) for name in archive.namelist()):
                raise ValueError('Excluded private feature in runtime archive: '+str(relative))


def plain(path):
    path=Path(os.path.abspath(path))
    for p in (path,*path.parents):
        try: info=p.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Reparse point or symlink refused')
    return path

def hashfile(path):
    with plain(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def read(path): return json.loads(plain(path).read_text(encoding='utf-8-sig'))

def write(path,obj):
    path=plain(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with tmp.open('xb') as f:
            f.write((json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode());f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally: tmp.unlink(missing_ok=True)

def tree(root):
    return {p.relative_to(root).as_posix():hashfile(p) for p in plain(root).rglob('*') if plain(p).is_file() and '__pycache__' not in p.parts and not p.name.endswith(('.pyc','-wal','-shm','.lock'))}

def safe_relative(value):
    p=PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or '\\' in value or ':' in value: raise ValueError('Unsafe candidate path')
    return p

def merge_integrity(current,updates):
    """Preserve both deployed formats: legacy print map and wrapped server map."""
    if not isinstance(current,dict): raise ValueError('Unsupported installed integrity metadata')
    wrapped='files' in current
    entries=current.get('files') if wrapped else current
    if (not isinstance(entries,dict) or (wrapped and current.get('algorithm')!='sha256')
            or any(not isinstance(v,str) or len(v)!=64 or any(c not in '0123456789abcdefABCDEF' for c in v) for v in entries.values())):
        raise ValueError('Unsupported installed integrity metadata')
    merged=dict(entries);merged.update(updates)
    if wrapped:
        result=dict(current);result['files']=merged;return result
    return merged

def verify(candidate,expected=None):
    candidate=plain(candidate);manifest=read(candidate/'candidate.json')
    if manifest.get('format')!=FORMAT or set(manifest['files'])!={'hub','print'}: raise ValueError('Wrong candidate format')
    if expected and hashfile(candidate/'candidate.json')!=expected: raise ValueError('Candidate manifest hash differs')
    for rel in manifest.get('trusted_runtime',{}):
        safe_relative(rel)
        if private_feature_path(rel): raise ValueError('Excluded private feature in trusted runtime: '+rel)
    for role,files in manifest['files'].items():
        actual=tree(candidate/role)
        if actual!=files: raise ValueError('Candidate file inventory differs: '+role)
        for rel in files:
            safe_relative(rel)
            validate_release_file(candidate/role/rel,rel)
            if not rel.startswith(('app/','web/','hub/','server/','runtime/','third_party/')) and rel!='CREDITS.md': raise ValueError('Candidate contains non-code file')
            if rel.startswith('runtime/') and rel not in manifest['trusted_runtime']: raise ValueError('Untrusted runtime file')
        all_hooks=manifest.get('appearance_hook')
        hooks=all_hooks.get(role) if isinstance(all_hooks,dict) else None
        if (not isinstance(hooks,dict) or hooks.get('schemaVersion')!=1
                or set(hooks.get('files',{}))!=set(APPEARANCE_HOOKS[role])):
            raise ValueError('Candidate lacks compatible school appearance hook: '+role)
        for rel in APPEARANCE_HOOKS[role]:
            if rel not in files or hooks['files'].get(rel)!=files[rel]:
                raise ValueError('Candidate school appearance hook hash differs: '+role+'/'+rel)
        for rel in ('app/school_appearance.py',) + (('hub/elevhub.py',) if role=='hub' else ()):
            module=ast.parse((candidate/role/rel).read_text(encoding='utf-8-sig'))
            if not any(isinstance(node,ast.Assign)
                       and any(isinstance(t,ast.Name) and t.id=='LOCAL_APPEARANCE_VERSION' for t in node.targets)
                       and isinstance(node.value,ast.Constant) and node.value.value==1
                       for node in module.body):
                raise ValueError('Candidate appearance reader capability missing: '+role+'/'+rel)
    if manifest['files']['hub']['hub/school_appearance.py']!=manifest['files']['hub']['app/school_appearance.py']:
        raise ValueError('Hub and application appearance readers differ')
    return manifest

def build(source,trusted,target,version):
    source=plain(source);trusted=plain(trusted);target=plain(target)
    if target.exists(): raise ValueError('Candidate output already exists')
    integrity=read(trusted/'integrity.json')['files'];runtime={}
    for p in (trusted/'runtime').rglob('*'):
        rel=p.relative_to(trusted).as_posix()
        if p.is_file() and not runtime_member_excluded(rel) and '__pycache__' not in p.parts and p.suffix!='.pyc':
            validate_release_file(p,rel)
            if integrity.get(rel)!=hashfile(p): raise ValueError('Trusted runtime integrity mismatch: '+rel)
            runtime[rel]=hashfile(p)
    target.mkdir(parents=True)
    base_ignore=shutil.ignore_patterns('__pycache__','*.pyc','test_*','konfiguration.json','konfiguration.windows.json','*.before-*')
    def ignore(directory,names):
        return set(base_ignore(directory,names)) | {name for name in names if private_feature_path(name, educational='datatraener' in {part.casefold() for part in Path(directory).parts})}
    for role in INSTALLS:
        dest=target/role
        for name in ('app','web'):
            shutil.copytree(source/name,dest/name,ignore=ignore)
        shutil.copytree(source/'third_party/boxes.py/boxes',dest/'third_party/boxes.py/boxes',ignore=ignore)
        def runtime_ignore(directory,names):
            excluded=set(ignore(directory,names))
            for name in names:
                candidate=Path(directory)/name
                rel=candidate.relative_to(trusted).as_posix()
                if runtime_member_excluded(rel): excluded.add(name)
            return excluded
        shutil.copytree(trusted/'runtime',dest/'runtime',ignore=runtime_ignore)
        shutil.copy2(source/'CREDITS.md',dest/'CREDITS.md')
        if role=='hub':
            shutil.copytree(source/'linux-elevhub/server/elevhub',dest/'hub',ignore=ignore)
            shutil.copytree(source/'windows-elevhub/server',dest/'server',ignore=ignore)
            shutil.copy2(source/'windows-elevhub/server/hub/konfiguration.windows.json',dest/'hub/konfiguration.windows.json')
    files={role:tree(target/role) for role in INSTALLS}
    manifest={'format':FORMAT,'version':version,'created_utc':datetime.now(timezone.utc).isoformat(),'credits':'Klostermarksskolen','trusted_runtime':runtime,'files':files,'appearance_hook':{role:{'schemaVersion':1,'files':{rel:files[role][rel] for rel in APPEARANCE_HOOKS[role]}} for role in INSTALLS}}
    write(target/'candidate.json',manifest);verify(target)
    return {'candidate_sha256':hashfile(target/'candidate.json'),'version':version,'files':{role:len(files) for role,files in manifest['files'].items()}}

def database(path):
    path=plain(path)
    with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)) as c:
        if c.execute('pragma quick_check').fetchone()[0]!='ok': raise ValueError('Database check failed')
        c.row_factory=sqlite3.Row
        return {r[0]:[dict(row) for row in c.execute('select * from "'+r[0].replace('"','""')+'"')] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}

def assert_idle(data=None):
    data=DATA if data is None else data
    p=database(data['print']/'queue.sqlite3')
    if any(j['status'] in ACTIVE for j in p['jobs']): raise ValueError('Active print job; postpone update')
    h=database(data['hub']/'designvaerksted/afleveringer.sqlite3')
    if any(j.get('handoff_claimed') and not j.get('physical_done') for j in h['jobs']): raise ValueError('Active laser handoff; postpone update')
    return {'print_jobs':len(p['jobs']),'laser_jobs':len(h['jobs'])}

def assert_preserved(before,after):
    """Every previous row/column retained; additive schema changes are allowed."""
    for table,rows in before.items():
        old_cols=set(rows[0]) if rows else set()
        candidates=[{k:r.get(k) for k in old_cols} for r in after.get(table,[])]
        for row in rows:
            if row not in candidates: raise ValueError('Migration changed or lost an existing '+table+' row')

def migrate(data):
    from app.core import Queue
    from app.laser_server import Inbox
    p=data['print'];h=data['hub']/'designvaerksted'
    previous_files={role:{k:v for k,v in tree(root).items() if not k.endswith('.sqlite3')} for role,root in data.items()}
    before_p=database(p/'queue.sqlite3');before_h=database(h/'afleveringer.sqlite3')
    q=Queue(p,read(p/'settings.json')['printers']);q.db.close()
    Inbox(h)
    after_p=database(p/'queue.sqlite3');after_h=database(h/'afleveringer.sqlite3')
    assert_preserved(before_p,after_p);assert_preserved(before_h,after_h)
    for role,files in previous_files.items():
        current=tree(data[role])
        if any(current.get(k)!=v for k,v in files.items()): raise ValueError('Migration changed or removed an existing data file')
    return {'print_jobs':len(after_p['jobs']),'laser_jobs':len(after_h['jobs']),'preserved_previous_rows':True,'preserved_previous_files':sum(map(len,previous_files.values()))}

def trial(candidate,snapshot,output):
    """Executed with candidate embedded interpreter; never imports live app."""
    output=plain(output)
    if output.exists(): raise ValueError('Trial destination exists')
    shutil.copytree(snapshot,output)
    return migrate({role:output/role for role in DATA})

def startup_check(candidate):
    """Read installed paths/TLS/configuration with candidate code; no services."""
    sys.path[:0]=[str(candidate/'hub'),str(candidate/'hub/server'),str(candidate/'hub/hub')]
    from bootstrap import check
    import app.server, app.laser_server, app.light_service
    import elevhub
    from app.school_appearance import school_appearance, school_appearance_manifest_present
    for folder in (DATA['hub']/'hub',DATA['hub']/'designvaerksted',DATA['print']):
        if school_appearance_manifest_present(folder) and school_appearance(folder) is None:
            raise ValueError('Candidate cannot read the installed local appearance')
    from sikkerhed import tls_kontekst
    config=read(DATA['hub']/'konfiguration.json')
    check(candidate/'hub',DATA['hub'],installed=True,role_ip=config['elev_adresse'],teacher_port=config['laerer_port'],teacher_address=config['laerer_adresse'])
    validated=elevhub.laes_konfiguration(DATA['hub']/'konfiguration.json')
    tls_kontekst(validated,DATA['hub'])
    from app.slicer import Slicer
    settings=read(DATA['print']/'settings.json')
    studio=Path(settings['studio_path'])
    if not studio.is_file(): raise ValueError('Configured Bambu Studio executable missing')
    Slicer(studio,studio.parent/'resources/profiles/BBL',DATA['print']/'sliced')
    return {'candidate_config_tls_and_imports':'pass','hardware_started':False}

def snapshot_data(output,data=None):
    data=DATA if data is None else data
    if output.exists(): raise ValueError('Snapshot destination exists')
    result={}
    for role,root in data.items():
        shutil.copytree(plain(root),output/role,ignore=shutil.ignore_patterns('*.lock','*-wal','*-shm','__pycache__'))
        # A killed Windows process may leave committed rows only in WAL. Use
        # SQLite's online backup API, then compare logical rows with the source.
        for db in root.rglob('*.sqlite3'):
            target=output/role/db.relative_to(root)
            with closing(sqlite3.connect('file:'+db.as_posix()+'?mode=ro',uri=True)) as source_db, closing(sqlite3.connect(target)) as dest_db:
                source_db.backup(dest_db)
            if database(db)!=database(target): raise ValueError('SQLite snapshot differs')
        result[role]=tree(output/role)
        without_db=lambda values:{k:v for k,v in values.items() if not k.endswith('.sqlite3')}
        if without_db(tree(root))!=without_db(result[role]): raise ValueError('Data changed during stopped snapshot')
    write(output/'snapshot.json',result)
    return result

def profile_paths(data):
    return {'hub':plain(data['hub']/'school.json'),
            'print':plain(data['print'].parent/'school.json')}

def appearance_paths(data):
    return {'hub':plain(data['hub']/'local-appearance'),
            'design':plain(data['hub']/'designvaerksted/local-appearance'),
            'print':plain(data['print']/'local-appearance')}

def appearance_tree(root):
    """No file exclusions: every local asset, including its manifest, is bound."""
    root=plain(root)
    if not root.exists(): return None
    if not (root/'manifest.json').is_file(): raise ValueError('Local appearance manifest missing')
    return {p.relative_to(root).as_posix():hashfile(p)
            for p in root.rglob('*') if plain(p).is_file()}

def capture_customization(output, data=None, installs=None):
    """Snapshot existing installation data; never infer or replace its theme."""
    data=DATA if data is None else data
    installs=INSTALLS if installs is None else installs
    output=plain(output)
    if output.exists(): raise ValueError('Customization snapshot destination exists')
    output.mkdir(parents=True)
    result={'format':APPEARANCE_FORMAT,'captured_utc':datetime.now(timezone.utc).isoformat(),
            'profiles':{},'assets':{}}
    for role,path in profile_paths(data).items():
        result['profiles'][role]=hashfile(path) if path.is_file() else None
        if path.is_file():
            destination=output/'profiles'/role/'school.json'
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,destination)
    for role,appearance in appearance_paths(data).items():
        inventory=appearance_tree(appearance)
        # A legacy branded installation needs an explicit one-time capture.
        # Silently guessing the skin from a release would lose local choices.
        if inventory is None and any(
                (installs['hub' if role=='design' else role]/rel).is_file() for rel in
                ('web/assets/klostermarksskolen-logo.png','hub/web/assets/klostermarksskolen-logo.png')):
            raise ValueError('Capture the installed local school appearance before updating: '+role)
        result['assets'][role]=inventory
        if inventory is not None:
            shutil.copytree(appearance,output/'assets'/role)
            if appearance_tree(output/'assets'/role)!=inventory:
                raise ValueError('Local appearance changed during snapshot')
    write(output/'receipt.json',result)
    verify_customization_receipt(result,data)
    return result

def verify_customization_receipt(receipt,data=None):
    data=DATA if data is None else data
    if (receipt.get('format')!=APPEARANCE_FORMAT
            or set(receipt.get('profiles',{}))!=set(data)
            or set(receipt.get('assets',{}))!=set(appearance_paths(data))):
        raise ValueError('Invalid local appearance preservation receipt')
    for role,path in profile_paths(data).items():
        observed=hashfile(path) if path.is_file() else None
        if observed!=receipt['profiles'][role]:
            raise ValueError('Installed school profile changed: '+role)
    for role,path in appearance_paths(data).items():
        if appearance_tree(path)!=receipt['assets'][role]:
            raise ValueError('Installed school appearance changed: '+role)
    return receipt

def restore_customization(snapshot, data=None, installs=None):
    """Rollback only: restore hashed files, without deleting whole data trees."""
    data=DATA if data is None else data
    root=plain(snapshot);receipt=read(root/'receipt.json')
    for role,path in profile_paths(data).items():
        expected=receipt['profiles'][role]
        if expected is None:
            # The updater never creates a profile. Do not delete an unexpected
            # profile: leave an explicit recovery error instead.
            if path.exists(): raise ValueError('Unexpected school profile during rollback')
        else:
            backup=plain(root/'profiles'/role/'school.json')
            if hashfile(backup)!=expected: raise ValueError('Profile rollback backup differs')
            if not path.exists() or hashfile(path)!=expected: shutil.copy2(backup,path)
    for role,files in receipt['assets'].items():
        target=appearance_paths(data)[role]
        if files is None:
            if target.exists(): raise ValueError('Unexpected school appearance during rollback')
            continue
        source=plain(root/'assets'/role)
        if appearance_tree(source)!=files: raise ValueError('Appearance rollback backup differs')
        for rel,expected in files.items():
            destination=plain(target/safe_relative(rel))
            destination.parent.mkdir(parents=True,exist_ok=True)
            if not destination.exists() or hashfile(destination)!=expected:
                shutil.copy2(source/rel,destination)
    return verify_customization_receipt(receipt,data)

def verify_customization(snapshot, data=None, installs=None):
    return verify_customization_receipt(read(plain(snapshot)/'receipt.json'),data)

def bridge_config(path,data=None):
    data=DATA if data is None else data
    config=read(path);before=dict(config)
    additions={'makerspace_printers':{'backend':'127.0.0.1:8765','key_file':str(data['print']/'makerspace-bridge.key')},'makerspace_laser_queue':{'backend':'127.0.0.1:8767','key_file':str(data['hub']/'designvaerksted/makerspace-laser.key')}}
    for k,v in additions.items():
        if k in config and config[k]!=v: raise ValueError('Existing bridge configuration conflicts')
        config[k]=v
    if any(config[k]!=v for k,v in before.items()): raise ValueError('Original configuration changed')
    write(path,config)

def restore_program(journal):
    record=read(journal/'journal.json')
    for role,files in record['old_files'].items():
        root=INSTALLS[role]
        for rel,old_hash in files.items():
            target=plain(root/safe_relative(rel))
            if old_hash is None: target.unlink(missing_ok=True)
            else:
                backup=journal/'program-before'/role/rel
                if hashfile(backup)!=old_hash: raise ValueError('Rollback backup differs')
                shutil.copy2(backup,target)
    record['state']='program_rolled_back';write(journal/'journal.json',record)

def apply(candidate,journal,expected):
    require_windows_long_paths()
    manifest=verify(candidate,expected);journal=plain(journal)
    if journal.exists(): raise ValueError('Update journal already exists; inspect previous transaction')
    journal.mkdir(parents=True)
    # Runtime executables/interpreter ABI never change in this update.
    for role,root in INSTALLS.items():
        if not (root/'installation.json').is_file(): raise ValueError('Existing installation metadata missing')
        merge_integrity(read(root/'integrity.json'),{})
        for rel in ('runtime/python.exe','runtime/python314.dll','runtime/python314.zip'):
            if hashfile(root/rel)!=manifest['trusted_runtime'][rel]: raise ValueError('Runtime ABI hash differs; requires another update path')
    if shutil.disk_usage(journal).free < 1024**3: raise ValueError('At least 1 GiB free space required for backup/trial/rollback')
    sys.path.insert(0,str(candidate/'hub'))
    from app.instance import InstanceLock
    with ExitStack() as stack:
        for folder in (DATA['hub'],DATA['hub']/'hub',DATA['hub']/'designvaerksted',DATA['print'],DATA['print']/'supervisor'):
            stack.enter_context(InstanceLock(folder))
        import psutil
        if any(str(p.info.get('exe') or '').lower().startswith(str(root).lower()+'\\runtime\\') for p in psutil.process_iter(['exe']) for root in INSTALLS.values()): raise ValueError('Installed runtime process remains')
        assert_idle()
        snapshot_data(journal/'data-before')
        customization=capture_customization(journal/'customization-before')
        check_result=subprocess.run([str(candidate/'hub/runtime/python.exe'),'-B',str(Path(__file__).resolve()),'startup-check','--candidate',str(candidate)],cwd=candidate/'hub',capture_output=True,text=True,timeout=40)
        if check_result.returncode: raise ValueError('Candidate startup configuration check failed: '+check_result.stderr[-2000:])
        # Probe dependencies/imports and actual migrations in an isolated data copy.
        command=[str(candidate/'hub/runtime/python.exe'),'-B',str(Path(__file__).resolve()),'trial','--snapshot',str(journal/'data-before'),'--output',str(journal/'trial-data')]
        result=subprocess.run(command,cwd=candidate/'hub',capture_output=True,text=True,timeout=120)
        if result.returncode: raise ValueError('Candidate migration trial failed: '+result.stderr[-2000:])
        old={}
        for role,files in manifest['files'].items():
            old[role]={}
            for rel in list(files)+['integrity.json','school-update.json']:
                target=plain(INSTALLS[role]/rel);old[role][rel]=hashfile(target) if target.exists() else None
                if target.exists():
                    backup=journal/'program-before'/role/rel;backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
        before_pause=next((r['value'] for r in database(DATA['print']/'queue.sqlite3')['settings'] if r['key']=='paused'),None)
        record={'format':FORMAT,'state':'prepared','version':manifest['version'],'candidate_sha256':expected,'old_files':old,'customization':customization,'migration_trial':json.loads(result.stdout),'before_print_paused':before_pause}
        write(journal/'journal.json',record)
        try:
            assert_idle()
            for role,files in manifest['files'].items():
                root=INSTALLS[role]
                for rel,want in files.items():
                    dest=plain(root/rel)
                    if dest.exists() and hashfile(dest)==want: continue
                    dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(candidate/role/rel,dest)
                    if hashfile(dest)!=want: raise ValueError('Installed file verification failed')
                write(root/'integrity.json',merge_integrity(read(root/'integrity.json'),files))
                write(root/'school-update.json',{'format':FORMAT,'version':manifest['version'],'candidate_sha256':expected,'installed_utc':datetime.now(timezone.utc).isoformat(),'credits':'Klostermarksskolen','customization':customization})
            # Code changes must never write the separate appearance data.
            verify_customization(journal/'customization-before')
            # No service imported here; final migration runs from current candidate.
            result=subprocess.run([str(candidate/'hub/runtime/python.exe'),'-B',str(Path(__file__).resolve()),'migrate-live'],cwd=candidate/'hub',capture_output=True,text=True,timeout=120)
            if result.returncode: raise ValueError('Live migration failed: '+result.stderr[-2000:])
            verify_customization(journal/'customization-before')
            bridge_config(DATA['hub']/'konfiguration.json')
            with closing(sqlite3.connect(DATA['print']/'queue.sqlite3')) as db:
                db.execute("insert or replace into settings(key,value) values('paused','true')");db.commit()
            record['state']='applied_paused';record['preservation']=json.loads(result.stdout);write(journal/'journal.json',record)
        except BaseException:
            # Preserve the original exception before rollback can replace its
            # traceback. Journal ACLs restrict this diagnostic to administrators.
            (journal/'failure-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
            try:
                restore_program(journal)
            except BaseException:
                (journal/'rollback-failure-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
                raise
            # Services are still stopped and data locks held: restore exact data snapshot.
            for role,root in DATA.items():
                for p in list(root.rglob('*')):
                    if p.is_file() and not p.name.endswith('.lock') and p.relative_to(root).as_posix() not in read(journal/'data-before/snapshot.json')[role]: p.unlink()
                shutil.copytree(journal/'data-before'/role,root,dirs_exist_ok=True)
            snapshot=read(journal/'data-before/snapshot.json')
            for role,root in DATA.items():
                actual=tree(root)
                if any(actual.get(name)!=sha for name,sha in snapshot[role].items()):
                    raise ValueError('Rollback data verification failed')
            restore_customization(journal/'customization-before')
            verify_customization(journal/'customization-before')
            record=read(journal/'journal.json');record['state']='rolled_back';record['rollback_verified']=True
            write(journal/'journal.json',record)
            raise
    return {'status':'applied_paused','version':manifest['version'],'migration':record['preservation'],'journal':str(journal)}

def installed(candidate,expected):
    """Read-only version evidence; a release marker alone is insufficient."""
    manifest=verify(candidate,expected)
    marker_customization=None
    for role,files in manifest['files'].items():
        marker=read(INSTALLS[role]/'school-update.json')
        if marker.get('version')!=manifest['version'] or marker.get('candidate_sha256')!=expected:
            raise ValueError('Installed version marker differs')
        if not marker.get('customization'): raise ValueError('Appearance preservation receipt missing')
        if marker_customization is not None and marker_customization!=marker['customization']:
            raise ValueError('Installed appearance receipts differ between roles')
        marker_customization=marker['customization']
        if any(hashfile(INSTALLS[role]/name)!=sha for name,sha in files.items()):
            raise ValueError('Installed candidate files differ')
    verify_customization_receipt(marker_customization)
    return {'version':manifest['version'],'candidate_sha256':expected,'installed_files_verified':True,'school_customization_verified':bool(marker_customization)}


def finish(candidate,journal,expected):
    """Called only after native task restoration and service health checks."""
    record=read(journal/'journal.json')
    if record.get('state')!='applied_paused' or record.get('candidate_sha256')!=expected:
        raise ValueError('Update is not ready for native completion')
    evidence=installed(candidate,expected)
    if not (Path(str(journal)+'.tasks')/'after.json').is_file():
        raise ValueError('Native task completion receipt missing')
    record.update(state='complete',completed_utc=datetime.now(timezone.utc).isoformat(),**evidence)
    write(journal/'journal.json',record)
    return evidence


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['build','verify','trial','migrate-live','apply','idle','long-path-preflight','startup-check','finish','installed']);p.add_argument('--source',type=Path);p.add_argument('--trusted',type=Path);p.add_argument('--candidate',type=Path);p.add_argument('--output',type=Path);p.add_argument('--snapshot',type=Path);p.add_argument('--journal',type=Path);p.add_argument('--version');p.add_argument('--sha256');a=p.parse_args()
    if a.action=='build': result=build(a.source,a.trusted,a.output,a.version)
    elif a.action=='verify': result={'verified':True,'version':verify(a.candidate,a.sha256)['version']}
    elif a.action=='trial': result=trial(a.candidate,a.snapshot,a.output)
    elif a.action=='migrate-live': result=migrate(DATA)
    elif a.action=='idle': result=assert_idle()
    elif a.action=='long-path-preflight': require_windows_long_paths();result={'long_paths_enabled':True}
    elif a.action=='startup-check': result=startup_check(a.candidate)
    elif a.action=='finish': result=finish(a.candidate,a.journal,a.sha256)
    elif a.action=='installed': result=installed(a.candidate,a.sha256)
    else: result=apply(a.candidate,a.journal,a.sha256)
    print(json.dumps(result))
if __name__=='__main__': main()
