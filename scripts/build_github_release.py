"""Build installable GitHub update assets from audited source and pinned inputs.

No network access or uploading. Third-party binary inputs require an independent
SHA-256 pin and a public-distribution review. Kreditering: Klostermarksskolen.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
import stat
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import build_update_manifest as manifest_builder

REPOSITORY=manifest_builder.feed.REPOSITORY
VERSION=re.compile(r'\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?')
HEX=re.compile(r'[0-9a-f]{64}')
LINUX_PATHS=(
    'CREDITS.md','linux-elevhub/ansible/roles','linux-elevhub/ansible/site.yml',
    'linux-elevhub/ansible/ansible.cfg','linux-elevhub/ansible/requirements.yml',
    'linux-elevhub/projekter','linux-elevhub/server/kredsloeb','linux-elevhub/server/microbit-controller',
    'linux-elevhub/server/elevhub/release.json','linux-elevhub/server/elevhub/web/assets',
    'linux-elevhub/cadoodle/elevhub-cadoodle','linux-elevhub/cadoodle/klargoer.py',
    'linux-elevhub/cadoodle/fresh_session.py','linux-elevhub/cadoodle/bootstrap.py',
    'linux-elevhub/cadoodle/kloster/deploy.py','linux-elevhub/kiosk-window-guard/matchbox-close-guard.patch',
    'linux-elevhub/kiosk-window-guard/README.md','linux-elevhub/kiosk-window-guard/COPYING',
    'linux-elevhub/kiosk-window-guard/debian-copyright','linux-elevhub/kiosk-window-guard/build.sh',
    'linux-elevhub/kiosk-window-guard/matchbox-source','linux-elevhub/LICENSE','linux-elevhub/THIRD_PARTY.md')


def _sha256(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _input(path,digest):
    if not isinstance(path,(str,Path)) or not HEX.fullmatch(digest or ''):
        raise ValueError('missing_or_unpinned_build_input')
    path=manifest_builder.feed.plain(Path(os.path.abspath(path)))
    if not path.exists():raise ValueError('missing_or_unpinned_build_input')
    if path.is_file():
        if path.stat().st_nlink>1:raise ValueError('hardlinked_build_input')
        actual=_sha256(path)
    elif path.is_dir():
        h=hashlib.sha256()
        for child in sorted(path.rglob('*')):
            manifest_builder.feed.plain(child)
            if child.is_dir():continue
            if not child.is_file() or child.stat().st_nlink>1:raise ValueError('linked_build_input')
            relative=child.relative_to(path).as_posix().encode();size=child.stat().st_size
            h.update(len(relative).to_bytes(8,'big'));h.update(relative);h.update(size.to_bytes(8,'big'))
            with child.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        actual=h.hexdigest()
    else:raise ValueError('missing_or_unpinned_build_input')
    if actual!=digest:raise ValueError('build_input_hash_mismatch')
    return path


def _module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _zip_write(archive,name,path=None,data=None):
    info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
    info.external_attr=0o100644<<16
    with archive.open(info,'w',force_zip64=True) as output:
        if path is not None:
            with Path(path).open('rb') as source:shutil.copyfileobj(source,output,1024*1024)
        else:output.write(data)


def _json(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()


def _zip_package(path,role,format_,version,files):
    """Stream paths, without reading a multi-gigabyte payload into memory."""
    rows={name:_sha256(value) for name,value in files.items()}
    marker={'schema':1,'role':role,'format':format_,'version':version,'files':rows}
    with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in sorted(files):_zip_write(archive,name,path=files[name])
        _zip_write(archive,'update-package.json',data=_json(marker))
    if any(_sha256(files[name])!=value for name,value in rows.items()):raise ValueError('build_input_changed')
    return path


def _copy(source,target):
    source=manifest_builder.feed.plain(source)
    if source.is_dir():
        for child in sorted(source.iterdir()):_copy(child,target/child.name)
    elif source.is_file():
        target.parent.mkdir(parents=True,exist_ok=True)
        if source.suffix in {'.sh','.py','.yml','.j2','.service','.cfg','.templates'} or source.name=='elevhub-cadoodle':
            with source.open('rb') as stream:target.write_bytes(stream.read().replace(b'\r\n',b'\n'))
        else:shutil.copyfile(source,target)
    else:raise ValueError('required_source_missing: '+source.name)


def _build_linux(source,spec,version,destination,work):
    external=spec.get('external',{})
    required={'deb','bootstrap','kloster','scratch','datatraener','matchbox'}
    if set(external)!=required:raise ValueError('linux_external_inputs_incomplete')
    stage=work/'linux-payload';stage.mkdir()
    tasks=(source/'linux-elevhub/ansible/roles/kiosk/tasks/main.yml').read_text(encoding='utf-8')
    section=tasks.split('    kiosk_lokal_design_filer:\n',1)[1].split('  block:\n',1)[0]
    design=re.findall(r'^      - ([a-zA-Z0-9_./-]+)$',section,re.MULTILINE)
    if not design:raise ValueError('linux_design_inventory_missing')
    for name in (*LINUX_PATHS,*design):
        manifest_builder.member_name(name);_copy(source/name,stage/name)
    for key,name in {'deb':'assets/CaDoodle.deb','bootstrap':'assets/CaDoodle-ApplicationInstall.zip',
                     'kloster':'assets/CaDoodle-Kloster.jar','scratch':'assets/scratch',
                     'datatraener':'assets/datatraener',
                     'matchbox':'linux-elevhub/kiosk-window-guard/bin/matchbox-window-manager'}.items():
        _copy(external[key]['verified'],stage/name)
    for name in ('assets/scratch/index.html','assets/datatraener/index.html'):
        if not (stage/name).is_file():raise ValueError('linux_application_entry_missing')
    (stage/'linux-elevhub/server/elevhub/release.json').write_bytes(_json(
        {'release_id':version,'expected_client_release':version}))
    files={p.relative_to(stage).as_posix():_sha256(p) for p in sorted(stage.rglob('*')) if p.is_file()}
    (stage/'manifest.json').write_bytes(_json({'version':version,'files':files}))
    payload=work/'payload.tar.gz'
    with (payload.open('xb') as raw,
          gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=1) as compressed,
          tarfile.open(fileobj=compressed,mode='w',format=tarfile.PAX_FORMAT) as archive):
        for path in sorted(stage.rglob('*')):
            if not path.is_file():continue
            info=tarfile.TarInfo(path.relative_to(stage).as_posix());info.size=path.stat().st_size
            info.uid=info.gid=0;info.mtime=0;info.mode=0o644
            with path.open('rb') as stream:archive.addfile(info,stream)
    return _zip_package(destination/'linux-update.zip','linux_student','linux-queue-v1',version,
                        {'payload.tar.gz':payload,'CREDITS.md':source/'CREDITS.md'})


def _build_laser(source,version,destination):
    # Parse the existing literal station allowlist without importing the caller's
    # app package, which may belong to a different checkout on sys.path.
    tree=ast.parse((source/'app/laser_station_install.py').read_text(encoding='utf-8'))
    scope={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'APP_FILES','WEB_FILES'} for t in node.targets):
            exec(compile(ast.Module(body=[node],type_ignores=[]),'<station-allowlist>','exec'),{'__builtins__':{'tuple':tuple}},scope)
    files=sorted(set(scope['APP_FILES']+scope['WEB_FILES']+('CREDITS.md','LICENSE','LICENSE-SCOPE.md')))
    rows={name:{'sha256':_sha256(source/name),'size':(source/name).stat().st_size} for name in files}
    path=destination/'station-code.zip'
    with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in files:_zip_write(archive,name,path=source/name)
        _zip_write(archive,'manifest.json',data=_json({'format':'makervaerkstedet-station-code-v1','release':version,'files':rows}))
    return path


def _build_server(source,spec,version,destination,work):
    trusted=spec['trusted_runtime']['verified']
    module=_module(source/'scripts/school_server_update/update.py','server_release_builder')
    candidate=work/'server-candidate';module.build(source,trusted,candidate,version)
    metadata=json.loads((candidate/'candidate.json').read_text(encoding='utf-8'))
    release=candidate/'hub/hub/release.json'
    if not release.is_file():raise ValueError('server_release_metadata_missing')
    release.write_bytes(_json({'release_id':version,'expected_client_release':version}))
    metadata['files']={role:module.tree(candidate/role) for role in ('hub','print')}
    metadata['created_utc']='1980-01-01T00:00:00+00:00'
    (candidate/'candidate.json').write_bytes(_json(metadata));module.verify(candidate)
    files={'CREDITS.md':source/'CREDITS.md'}
    files.update({'candidate/'+p.relative_to(candidate).as_posix():p for p in sorted(candidate.rglob('*')) if p.is_file()})
    return _zip_package(destination/'server-update.zip','windows_server','school-server-v1',version,files)


def _prepare_inputs(value,base):
    if (not isinstance(value,dict) or set(value)-{'schema','packages','approved_binary_sha256'}
            or value.get('schema')!=1 or not isinstance(value.get('packages'),dict) or not value['packages']):
        raise ValueError('invalid_build_input_manifest')
    if set(value['packages'])-set(manifest_builder.feed.ROLES):raise ValueError('unsupported_build_role')
    pins=value.get('approved_binary_sha256',[])
    if not isinstance(pins,list) or any(not isinstance(pin,str) or not HEX.fullmatch(pin) for pin in pins):
        raise ValueError('invalid_reviewed_binary_pin')
    for role,spec in value['packages'].items():
        required={'external'} if role=='linux_student' else {'trusted_runtime'} if role=='windows_server' else set()
        if not isinstance(spec,dict) or set(spec)!=required:raise ValueError('invalid_role_build_spec')
        inputs=list(spec.get('external',{}).values())+([spec['trusted_runtime']] if 'trusted_runtime' in spec else [])
        for item in inputs:
            if (not isinstance(item,dict) or set(item)!={'path','sha256','public_review'}
                    or not isinstance(item['public_review'],str) or len(item['public_review'])<24):
                raise ValueError('explicit_public_input_review_required')
            path=Path(item['path']);item['verified']=_input(path if path.is_absolute() else base/path,item['sha256'])
    return value


def fetch_build_inputs(url,expected,repository,destination,opener=None):
    """Fetch only a pinned asset in this repository's GitHub releases.

    Never executes downloaded files. The same member and link restrictions as
    update packages apply before extraction; all paths in the input manifest
    must stay inside the bundle. Source builders still audit every output.
    """
    if (not REPOSITORY.fullmatch(repository) or not HEX.fullmatch(expected or '')
            or not re.fullmatch(r'https://github\.com/'+re.escape(repository)+r'/releases/download/[A-Za-z0-9][A-Za-z0-9_.-]{0,120}/[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.zip',url or '')):
        raise ValueError('untrusted_build_inputs_url')
    destination=manifest_builder.feed.plain(destination)
    if destination.exists():raise FileExistsError('build_inputs_output_exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='github-inputs-',dir=destination.parent) as temporary:
        archive_path=Path(temporary)/'inputs.zip';h=hashlib.sha256();size=0;started=time.monotonic()
        with (opener or manifest_builder.feed.open_github)(url,repository) as response,archive_path.open('xb') as output:
            while True:
                block=response.read(1024*1024)
                if not block:break
                size+=len(block)
                if size>=manifest_builder.feed.MAX_PACKAGE or time.monotonic()-started>1800:
                    raise ValueError('build_inputs_download_limit')
                output.write(block);h.update(block)
        if not size or h.hexdigest()!=expected:raise ValueError('build_inputs_hash_mismatch')
        stage=Path(temporary)/'extracted';stage.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            entries=archive.infolist();names=[]
            for entry in entries:
                names.append(manifest_builder.member_name(entry.filename.rstrip('/')))
                mode=entry.external_attr>>16
                if (entry.flag_bits&1 or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))):
                    raise ValueError('unsafe_build_inputs_archive')
            if (not entries or len(entries)>100000 or len(set(n.casefold() for n in names))!=len(names)
                    or sum(e.file_size for e in entries)>8*1024**3):raise ValueError('unsafe_build_inputs_archive')
            for entry,name in zip(entries,names):
                target=stage/name
                if entry.is_dir():target.mkdir(parents=True,exist_ok=True);continue
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(entry) as source,target.open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
        manifest=stage/'build-input-manifest.json'
        if not manifest.is_file() or manifest.stat().st_size>manifest_builder.feed.MAX_MANIFEST:
            raise ValueError('build_input_manifest_missing')
        value=manifest_builder.feed.strict_json(manifest.read_bytes())
        for spec in value.get('packages',{}).values():
            for item in list(spec.get('external',{}).values())+([spec['trusted_runtime']] if 'trusted_runtime' in spec else []):
                manifest_builder.member_name(item.get('path'))
        _prepare_inputs(value,stage)
        shutil.move(str(stage),str(destination))
    return destination/'build-input-manifest.json'


def _check_release_roles(roles,scope):
    if scope not in {'system','selected'}:raise ValueError('invalid_release_scope')
    roles=list(roles)
    if len(set(roles))!=len(roles):raise ValueError('duplicate_package_role')
    if not roles or set(roles)-set(manifest_builder.feed.ROLES):raise ValueError('unsupported_build_role')
    if scope=='system' and set(roles)!=set(manifest_builder.feed.ROLES):
        raise ValueError('system_release_requires_all_roles')


def build_packages(source_root,version,input_manifest,destination,scope='system'):
    """A clean audited export prevents local untracked software/data inclusion."""
    manifest=manifest_builder.feed.plain(input_manifest)
    value=_prepare_inputs(manifest_builder.feed.strict_json(manifest.read_bytes()),manifest.parent)
    _check_release_roles(value['packages'],scope)
    output=manifest_builder.feed.plain(destination);output.mkdir(parents=True,exist_ok=True)
    source_root=manifest_builder.feed.plain(source_root)
    policy=manifest_builder.public_source._load_policy(source_root)
    specs=[]
    # The exporter's isolation boundary requires staging outside source_root,
    # including when the requested asset output is ./publisher in CI.
    with tempfile.TemporaryDirectory(prefix='github-release-',dir=source_root.parent) as temporary:
        work=Path(temporary);source=work/'source'
        manifest_builder.public_source.export_source(source_root,policy,str(source))
        for role,spec in sorted(value['packages'].items()):
            if role=='linux_student':path=_build_linux(source,spec,version,output,work)
            elif role=='windows_laser_teacher':path=_build_laser(source,version,output)
            else:path=_build_server(source,spec,version,output,work)
            specs.append({'path':path,'role':role,'format':manifest_builder.feed.ROLES[role],
                          'expected_sha256':_sha256(path),'approved_binary_sha256':value.get('approved_binary_sha256',[])})
        # Each independently pinned input must remain the exact selected input
        # until all copying has completed, including external directory trees.
        for spec in value['packages'].values():
            for item in list(spec.get('external',{}).values())+([spec['trusted_runtime']] if 'trusted_runtime' in spec else []):
                _input(item['verified'],item['sha256'])
    return specs


def build_release(source_root,repository,version,notes,packages,output_dir,
                  approved_binary_sha256=(),input_manifest=None,scope='system'):
    if not REPOSITORY.fullmatch(repository):raise ValueError('invalid_repository')
    if not VERSION.fullmatch(version):raise ValueError('invalid_version')
    source_root=manifest_builder.feed.plain(source_root);output_dir=manifest_builder.feed.plain(output_dir)
    if not source_root.is_dir():raise ValueError('source_root_missing')
    names=('makervaerkstedet-update.json','release-plan.json','SHA256SUMS.txt')
    if any((output_dir/name).exists() for name in names):raise FileExistsError('publication_output_exists')
    if input_manifest is not None:
        if packages:raise ValueError('package_sources_are_ambiguous')
        packages=build_packages(source_root,version,input_manifest,output_dir,scope=scope)
    _check_release_roles((p['role'] for p in packages),scope)
    if len({Path(p['path']).name.casefold() for p in packages})!=len(packages):raise ValueError('duplicate_asset_name')
    pins=set(approved_binary_sha256)
    for package in packages:pins.update(package.get('approved_binary_sha256',()))
    output_dir.mkdir(parents=True,exist_ok=True)
    result=manifest_builder.build_manifest(source_root,version,notes,packages,output_dir/names[0],
                                           approved_binary_sha256=pins,repository=repository)
    # The plan always names assets physically present beside the manifest.
    for package in packages:
        source=manifest_builder.feed.plain(package['path']);target=output_dir/source.name
        if source!=target:
            if target.exists():raise FileExistsError('package_output_exists')
            shutil.copyfile(source,target)
        if _sha256(target)!=package['expected_sha256']:raise ValueError('package_changed_during_copy')
    assets=[dict(item,url=f'https://github.com/{repository}/releases/download/v{version}/{item["asset"]}') for item in result['packages']]
    manifest_path=output_dir/names[0]
    plan={'schema':1,'product':'Makervaerkstedet','repository':repository,'version':version,'tag':'v'+version,
          'status':'validated_local_only','upload':'not_performed','scope':scope,
          'manifest':{'asset':names[0],'sha256':_sha256(manifest_path),'size':manifest_path.stat().st_size},'assets':assets}
    (output_dir/names[1]).write_bytes(_json(plan))
    rows=[(names[0],_sha256(manifest_path))]+[(item['asset'],item['sha256']) for item in assets]
    (output_dir/names[2]).write_text(''.join(f'{sha}  {name}\n' for name,sha in rows),encoding='ascii',newline='\n')
    return plan


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True);parser.add_argument('--repository',required=True)
    parser.add_argument('--version',required=True);parser.add_argument('--notes',type=Path,required=True)
    parser.add_argument('--scope',choices=('system','selected'),default='system',
                        help='system requires Linux student, Windows server and laser updates; selected explicitly permits a subset')
    parser.add_argument('--package',nargs=4,action='append',default=[],metavar=('ROLE','FORMAT','ZIP','EXPECTED_SHA256'))
    parser.add_argument('--build-input-manifest',type=Path);parser.add_argument('--approved-binary-sha256',action='append',default=[])
    parser.add_argument('--build-inputs-url');parser.add_argument('--build-inputs-sha256')
    parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args(argv)
    packages=[dict(zip(('role','format','path','expected_sha256'),row)) for row in args.package]
    if args.build_inputs_url or args.build_inputs_sha256:
        if args.build_input_manifest or packages:parser.error('Choose one package input method')
        with tempfile.TemporaryDirectory(prefix='github-release-inputs-') as temporary:
            manifest=fetch_build_inputs(args.build_inputs_url,args.build_inputs_sha256,args.repository,Path(temporary)/'inputs')
            plan=build_release(args.source_root,args.repository,args.version,args.notes.read_text(encoding='utf-8'),[],
                               args.output_dir,args.approved_binary_sha256,input_manifest=manifest,scope=args.scope)
    else:
        plan=build_release(args.source_root,args.repository,args.version,args.notes.read_text(encoding='utf-8'),packages,
                           args.output_dir,args.approved_binary_sha256,input_manifest=args.build_input_manifest,scope=args.scope)
    print(json.dumps({'status':plan['status'],'repository':plan['repository'],'version':plan['version'],'assets':len(plan['assets'])}))
    return 0


if __name__=='__main__':raise SystemExit(main())
