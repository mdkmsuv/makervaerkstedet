"""Produce the fixed GitHub feed assets from an already signed public flat repo."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import zipfile
from app.simple_feed import ASSET,DESCRIPTOR,verify_descriptor
from app.simple_release import REPOSITORY,DEBIAN_COMPONENTS,verify_manifest
from scripts.local_release_signing import sign_minisign

REQUIRED={'Packages','Packages.gz','Release','InRelease','Release.gpg'}
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def allowed(name):
    if name in REQUIRED|{'makervaerkstedet.gpg'}:return True
    match=re.fullmatch(r'pool/(makervaerkstedet-[a-z0-9-]+)_[A-Za-z0-9._+~%\-]+_(?:all|amd64)\.deb',name)
    if match:return match[1] in {package for package,architecture in DEBIAN_COMPONENTS.values()}
    if re.fullmatch(r'manifests/[a-f0-9]{64}\.(?:json|minisig)',name):return True
    return re.fullmatch(r'by-hash/SHA256/[a-f0-9]{64}',name) is not None

def snapshot(repo,output,*,expected_files=None):
    repo=Path(repo).absolute();output=Path(output).absolute()
    if output.exists() or output==repo or repo in output.parents:raise ValueError('new_snapshot_outside_repo_required')
    files={}
    for path in sorted(repo.rglob('*')):
        for parent in (path,*path.parents):
            if parent.is_symlink() or parent.is_junction():raise ValueError('linked_snapshot_input')
        if path.is_dir():continue
        name=path.relative_to(repo).as_posix()
        if not allowed(name):raise ValueError('nonpublic_snapshot_input:'+name)
        files[name]={'sha256':sha(path),'size':path.stat().st_size}
    if not REQUIRED<=set(files) or not any(name.startswith('pool/') for name in files):raise ValueError('incomplete_flat_repository')
    if len({name.casefold() for name in files})!=len(files):raise ValueError('snapshot_case_collision')
    if expected_files is not None and {name:value['sha256'] for name,value in files.items()}!=expected_files:
        raise ValueError('snapshot_original_input_mismatch')
    for name in files:
        if name.startswith('manifests/') and name.endswith('.json'):
            digest=Path(name).stem
            if files[name]['sha256']!=digest or 'manifests/'+digest+'.minisig' not in files:raise ValueError('component_manifest_binding_mismatch')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for name,record in files.items():
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.external_attr=0o100644<<16
            with archive.open(info,'w',force_zip64=True) as target,(repo/name).open('rb') as source:
                digest=hashlib.sha256();size=0
                for block in iter(lambda:source.read(1024*1024),b''):
                    target.write(block);digest.update(block);size+=len(block)
            if digest.hexdigest()!=record['sha256'] or size!=record['size']:raise ValueError('snapshot_input_changed')
        info=zipfile.ZipInfo('snapshot-inventory.json',(1980,1,1,0,0,0));info.external_attr=0o100644<<16
        archive.writestr(info,json.dumps({'schema':1,'files':files},sort_keys=True,separators=(',',':')).encode())
    return {'name':ASSET,'sha256':sha(output),'size':output.stat().st_size}

def component_manifests(repo,receipt,*,minisign_exe,minisign_sha256,public_key,sequence):
    directory=Path(repo)/'manifests';directory.mkdir(exist_ok=True)
    reverse={pair:component for component,pair in DEBIAN_COMPONENTS.items()};results=[]
    for artifact in receipt['packages']:
        component=reverse[(artifact['package'],artifact['architecture'])]
        name=artifact['package']+'_'+artifact['version'].replace(':','%3a')+'_'+artifact['architecture']+'.deb'
        if sha(Path(repo)/'pool'/name)!=artifact['sha256']:raise ValueError('repository_artifact_changed')
        value={'schema':1,'repository':REPOSITORY,'role':'student','platform':'debian-amd64','component':component,
               'version':artifact['version'],'sequence':sequence,
               'artifacts':[dict(artifact,name=name)]}
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode();digest=hashlib.sha256(raw).hexdigest();path=directory/(digest+'.json')
        signature=directory/(digest+'.minisig')
        if path.exists() or signature.exists():
            if not path.is_file() or path.read_bytes()!=raw or not signature.is_file():raise ValueError('incomplete_immutable_component_manifest')
            # Reuse the exact signature bytes: the importer treats this object
            # pair as immutable even when minisign's trusted timestamp changes.
        else:
            with path.open('xb') as stream:stream.write(raw)
            sign_minisign(path,minisign_exe=minisign_exe,signature=signature)
        signature_bytes=signature.read_bytes()
        verified=verify_manifest(raw,signature_bytes,minisign_exe=minisign_exe,minisign_sha256=minisign_sha256,public_key=public_key,
            repository=REPOSITORY,role='student',platform='debian-amd64',component=component,now=datetime.now(timezone.utc))
        results.append({'component':component,'version':artifact['version'],'manifest_sha256':verified['_manifest_sha256'],
                        'signature_sha256':hashlib.sha256(signature_bytes).hexdigest()})
    return results

def build(repo,output,receipt,*,minisign_exe,minisign_sha256,public_key,release_tag,sequence):
    output=Path(output)
    if output.exists():raise ValueError('feed_output_exists')
    # Bind native signed indexes and package bytes to the completed repository
    # build before using the separate feed signing authority.
    files=receipt.get('files')
    if not isinstance(files,dict) or not REQUIRED<=set(files):raise ValueError('verified_repository_receipt_required')
    for name,expected in files.items():
        if not allowed(name) or sha(Path(repo)/name)!=expected:raise ValueError('repository_receipt_hash_mismatch')
    output.mkdir(parents=True)
    manifests=component_manifests(repo,receipt,minisign_exe=minisign_exe,minisign_sha256=minisign_sha256,public_key=public_key,sequence=sequence)
    expected=dict(files)
    for item in manifests:
        expected['manifests/'+item['manifest_sha256']+'.json']=item['manifest_sha256']
        expected['manifests/'+item['manifest_sha256']+'.minisig']=item['signature_sha256']
    record=snapshot(repo,output/ASSET,expected_files=expected)
    value={'schema':1,'repository':REPOSITORY,'release_tag':release_tag,'sequence':sequence,'snapshot':record}
    raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode();path=output/DESCRIPTOR
    with path.open('xb') as stream:stream.write(raw)
    sign_minisign(path,minisign_exe=minisign_exe)
    verified=verify_descriptor(raw,path.with_name(DESCRIPTOR+'.minisig').read_bytes(),minisign_exe=minisign_exe,minisign_sha256=minisign_sha256,public_key=public_key,now=datetime.now(timezone.utc))
    return {'descriptor_sha256':verified['_descriptor_sha256'],'snapshot':record,'components':manifests,'published':False}
