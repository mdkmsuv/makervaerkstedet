"""Produce the fixed GitHub feed assets from an already signed public flat repo."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"system")]
from app.simple_feed import ASSET,DESCRIPTOR,verify_descriptor
from app.simple_release import REPOSITORY,DEBIAN_COMPONENTS,verify_manifest
from app.app_catalog import validate_catalog,verify_catalog,_unique_pairs
from scripts.build_app_catalog import plain
from scripts.build_apt_repository import validate_release_packages
from scripts.local_release_signing import sign_minisign

REQUIRED={'Packages','Packages.gz','Release','InRelease','Release.gpg'}
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def allowed(name,*,app_packages=()):
    if name in REQUIRED|{'makervaerkstedet.gpg'}:return True
    match=re.fullmatch(r'pool/(makervaerkstedet-[a-z0-9-]+)_[A-Za-z0-9._+~%\-]+_(?:all|amd64)\.deb',name)
    if match:return (match[1] in {package for package,architecture in DEBIAN_COMPONENTS.values()}
                     or (match[1] in app_packages and re.fullmatch(r'makervaerkstedet-app-[a-z0-9][a-z0-9-]{0,63}',match[1]) is not None))
    if re.fullmatch(r'manifests/[a-f0-9]{64}\.(?:json|minisig)',name):return True
    return re.fullmatch(r'by-hash/SHA256/[a-f0-9]{64}',name) is not None

def snapshot(repo,output,*,expected_files=None,app_packages=()):
    repo=Path(repo).absolute();output=Path(output).absolute()
    if output.exists() or output==repo or repo in output.parents:raise ValueError('new_snapshot_outside_repo_required')
    files={}
    for path in sorted(repo.rglob('*')):
        for parent in (path,*path.parents):
            if parent.is_symlink() or parent.is_junction():raise ValueError('linked_snapshot_input')
        if path.is_dir():continue
        name=path.relative_to(repo).as_posix()
        if not allowed(name,app_packages=app_packages):raise ValueError('nonpublic_snapshot_input:'+name)
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
    app_packages={app['package'] for app in receipt.get('app_descriptors',[])} if receipt.get('schema')==2 else set()
    for artifact in receipt['packages']:
        if artifact['package'] in app_packages:continue
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

def checked_catalog(record,receipt,*,release_tag,sequence):
    if type(record) is not dict or set(record)!={'path','sha256'} or type(record['sha256']) is not str or re.fullmatch('[a-f0-9]{64}',record['sha256']) is None:
        raise ValueError('app_catalog_pin_required')
    path=plain(record['path'])
    if not path.is_file() or not 0<path.stat().st_size<=1024**2:raise ValueError('app_catalog_unavailable')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('app_catalog_hash_mismatch')
    value=json.loads(raw,object_pairs_hook=_unique_pairs)
    validate_catalog(value,repository=REPOSITORY)
    expected={item['package']:item for item in receipt['app_descriptors']}
    packages={item['package']:item for item in receipt['packages']}
    if value['sequence']!=sequence or {item['descriptor']['package'] for item in value['apps']}!=set(expected):
        raise ValueError('app_catalog_repository_binding_mismatch')
    for item in value['apps']:
        descriptor=item['descriptor'];artifact=item['artifact'];package=packages[descriptor['package']]
        name=f"{package['package']}_{package['version']}_{package['architecture']}.deb"
        if (descriptor!=expected[descriptor['package']] or artifact!={'name':name,'sha256':package['sha256'],'size':package['size'],
                'url':f'https://github.com/{REPOSITORY}/releases/download/{release_tag}/{quote(name,safe="")}' }):
            raise ValueError('app_catalog_repository_binding_mismatch')
    return raw


def catalog_manifest(repo,raw,*,minisign_exe,minisign_sha256,public_key):
    directory=Path(repo)/'manifests';directory.mkdir(exist_ok=True)
    digest=hashlib.sha256(raw).hexdigest();path=directory/(digest+'.json');signature=directory/(digest+'.minisig')
    if path.exists() or signature.exists():
        if not path.is_file() or path.read_bytes()!=raw or not signature.is_file():raise ValueError('incomplete_immutable_app_catalog')
    else:
        with path.open('xb') as stream:stream.write(raw)
        sign_minisign(path,minisign_exe=minisign_exe,signature=signature)
    signature_bytes=signature.read_bytes()
    verified=verify_catalog(raw,signature_bytes,minisign_exe=minisign_exe,minisign_sha256=minisign_sha256,public_key=public_key,repository=REPOSITORY)
    return {'component':'app-catalog','version':verified['version'],'manifest_sha256':verified['_manifest_sha256'],
            'signature_sha256':hashlib.sha256(signature_bytes).hexdigest()}


def build(repo,output,receipt,*,minisign_exe,minisign_sha256,public_key,release_tag,sequence,app_catalog=None):
    output=Path(output)
    if output.exists():raise ValueError('feed_output_exists')
    modern=type(receipt.get('schema')) is int and receipt['schema']==2
    app_packages=set();catalog_raw=None
    if modern:
        validate_release_packages(receipt.get('packages'),receipt.get('release_set'),receipt.get('app_descriptors'))
        app_packages={item['package'] for item in receipt['app_descriptors']}
        if app_catalog is not None or receipt['release_set']!='base':
            catalog_raw=checked_catalog(app_catalog,receipt,release_tag=release_tag,sequence=sequence)
    elif app_catalog is not None:raise ValueError('modern_repository_receipt_required')
    # Bind native signed indexes and package bytes to the completed repository
    # build before using the separate feed signing authority.
    files=receipt.get('files')
    if not isinstance(files,dict) or not REQUIRED<=set(files):raise ValueError('verified_repository_receipt_required')
    for name,expected in files.items():
        if not allowed(name,app_packages=app_packages) or sha(plain(Path(repo)/name))!=expected:raise ValueError('repository_receipt_hash_mismatch')
    if modern:
        expected_pool={f"pool/{item['package']}_{item['version'].replace(':','%3a')}_{item['architecture']}.deb":item['sha256'] for item in receipt['packages']}
        if {name:digest for name,digest in files.items() if name.startswith('pool/')}!=expected_pool:
            raise ValueError('repository_package_inventory_mismatch')
    output.mkdir(parents=True)
    manifests=component_manifests(repo,receipt,minisign_exe=minisign_exe,minisign_sha256=minisign_sha256,public_key=public_key,sequence=sequence)
    if catalog_raw is not None:manifests.append(catalog_manifest(repo,catalog_raw,minisign_exe=minisign_exe,minisign_sha256=minisign_sha256,public_key=public_key))
    expected=dict(files)
    for item in manifests:
        expected['manifests/'+item['manifest_sha256']+'.json']=item['manifest_sha256']
        expected['manifests/'+item['manifest_sha256']+'.minisig']=item['signature_sha256']
    record=snapshot(repo,output/ASSET,expected_files=expected,app_packages=app_packages)
    value={'schema':1,'repository':REPOSITORY,'release_tag':release_tag,'sequence':sequence,'snapshot':record}
    raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode();path=output/DESCRIPTOR
    with path.open('xb') as stream:stream.write(raw)
    sign_minisign(path,minisign_exe=minisign_exe)
    verified=verify_descriptor(raw,path.with_name(DESCRIPTOR+'.minisig').read_bytes(),minisign_exe=minisign_exe,minisign_sha256=minisign_sha256,public_key=public_key,now=datetime.now(timezone.utc))
    return {'descriptor_sha256':verified['_descriptor_sha256'],'snapshot':record,'components':manifests,'published':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True,help='Completed native apt repository receipt')
    parser.add_argument('--minisign-exe',type=Path,required=True);parser.add_argument('--minisign-sha256',required=True)
    parser.add_argument('--public-key',required=True);parser.add_argument('--release-tag',required=True);parser.add_argument('--sequence',type=int,required=True)
    parser.add_argument('--app-catalog',type=Path);parser.add_argument('--app-catalog-sha256')
    parser.add_argument('--result',type=Path,required=True,help='Private build result outside public repository/feed')
    args=parser.parse_args(argv)
    try:
        result_path=plain(args.result)
        if result_path.exists():raise ValueError('feed_result_exists')
        if any(result_path==plain(directory) or plain(directory) in result_path.parents for directory in (args.repo,args.output)):
            raise ValueError('private_feed_result_outside_public_output_required')
        if (args.app_catalog is None)!=(args.app_catalog_sha256 is None):raise ValueError('app_catalog_pin_required')
        catalog=None if args.app_catalog is None else {'path':str(args.app_catalog),'sha256':args.app_catalog_sha256}
        result=build(args.repo,args.output,json.loads(plain(args.receipt).read_bytes(),object_pairs_hook=_unique_pairs),
            minisign_exe=args.minisign_exe,minisign_sha256=args.minisign_sha256,public_key=args.public_key,
            release_tag=args.release_tag,sequence=args.sequence,app_catalog=catalog)
        with result_path.open('x',encoding='utf-8') as stream:stream.write(json.dumps(result,indent=2)+'\n')
    except (ValueError,OSError) as error:parser.exit(2,str(error)+'\n')
    return 0


if __name__=='__main__':raise SystemExit(main())
