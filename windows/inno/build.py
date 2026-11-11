"""Offline Inno build with complete pinned compiler/package inventories."""
from __future__ import annotations
import argparse
import base64
import os
import hashlib
import importlib.util
import json
from pathlib import Path,PurePosixPath
import re
import shutil
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[2]
import sys
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import build_update_manifest as review

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def safe(root,name):
    relative=PurePosixPath(name)
    if relative.is_absolute() or str(relative)!=name or '..' in relative.parts or any(c in name for c in '\\:*?"<>|'):raise ValueError('unsafe_input_path')
    path=root/name
    for parent in (path,*path.parents):
        if parent.is_symlink() or parent.is_junction():raise ValueError('linked_input_rejected')
    return path

def inventory(root,files):
    if not isinstance(files,dict) or not files:raise ValueError('full_inventory_required')
    entries=list(root.rglob('*'))
    for entry in entries:safe(root,entry.relative_to(root).as_posix())
    actual={p.relative_to(root).as_posix() for p in entries if p.is_file()}
    if actual!=set(files) or len({name.casefold() for name in files})!=len(files):raise ValueError('inventory_mismatch')
    for name,want in files.items():
        if re.fullmatch('[a-f0-9]{64}',want or '') is None or sha(safe(root,name))!=want:raise ValueError('input_hash_mismatch')

def stage_verified(source,target,files):
    shutil.copytree(source,target)
    # Compare with the ORIGINAL pins, never hashes measured after the copy.
    inventory(target,files)


def copy_file_verified(source,target,expected):
    shutil.copyfile(source,target)
    if sha(target)!=expected:raise ValueError('copied_release_trust_hash_mismatch')


def validate(value,*,test_artifact=False):
    if set(value)!={'schema','role','version','windows_version','compiler_root','compiler_files','package_root','files','public_review','release_trust'} or value['schema']!=1:raise ValueError('invalid_build_inputs')
    if value['role'] not in ('server','laser_station'):raise ValueError('unsupported_role')
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+){2}(?:[+~.-][a-zA-Z0-9.]+)?',value['version']) is None:raise ValueError('invalid_product_version')
    if re.fullmatch(r'\d{1,5}(?:\.\d{1,5}){3}',value['windows_version']) is None or any(int(part)>65535 for part in value['windows_version'].split('.')):raise ValueError('invalid_windows_version')
    if value['public_review'] is not True:raise ValueError('reviewed_generic_inputs_required')
    trust=value['release_trust']
    if set(trust)!={'minisign_exe','minisign_sha256','public_key','license_file','license_sha256','provenance_file','provenance_sha256'}:raise ValueError('explicit_release_trust_required')
    verifier=Path(trust['minisign_exe'])
    if re.fullmatch('[a-f0-9]{64}',trust['minisign_sha256'] or '') is None or not verifier.is_file() or sha(verifier)!=trust['minisign_sha256']:raise ValueError('minisign_input_hash_mismatch')
    try:key=base64.b64decode(trust['public_key'],validate=True)
    except (ValueError,TypeError) as error:raise ValueError('publisher_public_key_required') from error
    if len(key)!=42 or key[:2]!=b'Ed':raise ValueError('publisher_public_key_required')
    for item in ('license','provenance'):
        path=Path(trust[item+'_file'])
        if re.fullmatch('[a-f0-9]{64}',trust[item+'_sha256'] or '') is None or not path.is_file() or sha(path)!=trust[item+'_sha256']:raise ValueError('minisign_'+item+'_hash_mismatch')
    compiler=Path(value['compiler_root']);package=Path(value['package_root'])
    inventory(compiler,value['compiler_files']);inventory(package,value['files'])
    if 'ISCC.exe' not in value['compiler_files']:raise ValueError('inno_compiler_required')
    required={'runtime/python.exe','runtime/python314.dll','runtime/python314.zip','runtime/python314._pth','elevhub-package.json','school-package.json','makerspace-release.json','integrity.json','CREDITS.md'}
    if value['role']=='server':required|={'server/install.ps1','server/public-apt-cache-acl.ps1','server/verify.py','deployment/school-setup/Setup-School.ps1','deployment/school-setup/enrollment.py','hub/konfiguration.windows.json','hub/simple_updates.py','app/simple_release.py'}
    else:required|={'app/laser_station_install.py','deployment/school-setup/Setup-LaserStation.ps1'}
    if not required<=set(value['files']):raise ValueError('complete_role_runtime_required')
    for name in value['files']:
        review.reject_removed_runtime(name)
        if Path(name).suffix.lower() in ('.key','.pfx','.p12','.sqlite','.sqlite3','.db') or Path(name).name in ('school.json','installation-owner.json','server-launch.json','windows.json'):raise ValueError('private_state_in_package')
    if not test_artifact:
        if not (package/'runtime/python.exe').read_bytes().startswith(b'MZ'):raise ValueError('invalid_python_executable')
        with zipfile.ZipFile(package/'runtime/python314.zip') as archive:
            if not {'encodings/__init__.py','encodings/__init__.pyc'}.intersection(archive.namelist()):raise ValueError('incomplete_python_stdlib')
    if not test_artifact and value['role']=='server':
        import importlib.util
        spec=importlib.util.spec_from_file_location('model3d_package_gate',ROOT/'windows-elevhub/build_packages.py')
        gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
        gate.validate_model3d_assets(package)
    return compiler,package

def build(value,output,*,test_artifact=False):
    compiler,package=validate(value,test_artifact=test_artifact);output=Path(output)
    if output.exists():raise ValueError('output_already_exists')
    output.mkdir(parents=True);own=Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix='mkv-inno-') as temporary:
        payload=Path(temporary)/'payload';payload.mkdir();target=payload/'package';stage_verified(package,target,value['files'])
        staged_compiler=Path(temporary)/'compiler';stage_verified(compiler,staged_compiler,value['compiler_files'])
        # Include only the existing maintenance HTTP helper, never the old updater.
        if value['role']=='server':
            for name in ('mkv2/__init__.py','mkv2/control_plane/__init__.py','mkv2/control_plane/maintenance.py','mkv2/control_plane/validation.py'):
                dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
            verifier=target/'tools/minisign/minisign.exe';verifier.parent.mkdir(parents=True,exist_ok=True);copy_file_verified(value['release_trust']['minisign_exe'],verifier,value['release_trust']['minisign_sha256'])
            notices=target/'licenses/minisign';notices.mkdir(parents=True,exist_ok=True)
            copy_file_verified(value['release_trust']['license_file'],notices/'LICENSE',value['release_trust']['license_sha256'])
            copy_file_verified(value['release_trust']['provenance_file'],notices/'upstream-provenance.json',value['release_trust']['provenance_sha256'])
            config_path=target/'hub/konfiguration.windows.json';config=json.loads(config_path.read_text(encoding='utf-8-sig'))
            config['apps']=[entry for entry in config.get('apps',[]) if entry.get('id')!='cadoodle' and entry.get('native_app')!='cadoodle']
            config['simple_updates']={'repository':'mdkmsuv/makervaerkstedet','minisign_exe':str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'ElevhubServer/tools/minisign/minisign.exe'),'minisign_sha256':value['release_trust']['minisign_sha256'],'public_key':value['release_trust']['public_key'],'apt_cache':'public-apt-cache','device_bindings':{},'catalog':[],'source':{'enabled':True,'refresh_seconds':86400}}
            config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        (target/'runtime-release.json').write_text(json.dumps({'schema':1,'release_id':value['version'],'role':value['role']},sort_keys=True)+'\n',encoding='utf-8')
        shutil.copyfile(ROOT/'app/runtime_health.py',target/'app/runtime_health.py')
        if not test_artifact:
            # Reseal using the established school-package format after adding
            # public trust/helper files. Source inputs remain untouched.
            from scripts.build_school_package import write_release_manifest
            for marker_name in ('elevhub-package.json','school-package.json'):
                marker_path=target/marker_name;marker=json.loads(marker_path.read_bytes());marker['version']=value['version'];marker_path.write_text(json.dumps(marker,indent=2),encoding='utf-8')
            integrity={p.relative_to(target).as_posix():sha(p) for p in target.rglob('*') if p.is_file() and p.name not in ('integrity.json','makerspace-release.json')}
            (target/'integrity.json').write_text(json.dumps({'algorithm':'sha256','files':integrity},indent=2),encoding='utf-8')
            write_release_manifest(target,value['version'])
        for name in ('lifecycle.py','task_control.ps1'):shutil.copyfile(own/name,payload/name)
        files={p.relative_to(payload).as_posix():sha(p) for p in payload.rglob('*') if p.is_file()}
        manifest=payload/'installer-payload.json';manifest.write_text(json.dumps({'schema':1,'role':value['role'],'version':value['version'],'files':files,'test_artifact':test_artifact},sort_keys=True),encoding='utf-8')
        expected=sha(manifest);label='Makervaerkstedet-'+value['role']+('-source-test' if test_artifact else '-Setup')
        recipe=own/'Makervaerkstedet.iss'
        arguments=[str(staged_compiler/'ISCC.exe'),'/Qp','/DPayloadRoot='+str(payload),'/DAppRole='+value['role'],'/DProductVersion='+value['version'],
            '/DWindowsVersion='+value['windows_version'],'/DPayloadSha256='+expected,'/DOutputRoot='+str(output.resolve()),'/DOutputName='+label,str(recipe)]
        subprocess.run(arguments,check=True)
        exe=output/(label+'.exe')
        if not exe.is_file():raise ValueError('compiler_output_missing')
        receipt={'schema':1,'artifact':exe.name,'sha256':sha(exe),'role':value['role'],'version':value['version'],'test_artifact':test_artifact,
                 'compiler_sha256':value['compiler_files']['ISCC.exe'],'payload_manifest_sha256':expected,'recipe_sha256':sha(recipe),
                 'authenticode_signed':False,'installed':False,'input_files':value['files'],'toolchain_files':value['compiler_files']}
        (output/'build-provenance.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        return receipt
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--test-artifact',action='store_true');args=parser.parse_args()
    try:print(json.dumps(build(json.loads(args.inputs.read_bytes()),args.output,test_artifact=args.test_artifact)))
    except (ValueError,OSError,subprocess.CalledProcessError) as error:parser.exit(2,str(error)+'\n')
