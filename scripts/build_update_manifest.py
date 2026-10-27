"""Emit release metadata for verified, privacy-reviewed ZIPs. Never uploads.

Requires the public-source privacy preflight and independent archive SHA-256
pins. Opaque binary members require explicit reviewed immutable binary pins.
Kreditering: Klostermarksskolen.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path,PurePosixPath
import re
import stat
import sys
import tarfile
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import public_source
_spec=importlib.util.spec_from_file_location('publisher_feed_contract',ROOT/'linux-elevhub/server/elevhub/release_feed.py')
feed=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(feed)
MARKER='update-package.json'
TEXT={'.py','.ps1','.cmd','.bat','.js','.css','.html','.json','.md','.txt','.svg','.xml','.yml','.yaml','.sh','.toml','.ini','.conf','.patch'}
TEXT.update({'.service','.cfg','.j2','.templates','.dsc','.desktop','.properties','.csv','.mjs','.cjs','.map','.license',
             '.cs','.manifest','.pyi','.c','.h','.pxd','.pyx','.pc','.rst','.template','.build','._pth',
             '.apache','.bsd','.typed','.webmanifest','.f','.f90','.f95','.pyf'})
TEXT_NAMES=frozenset(('license','copying','metadata','record','wheel','delvewheel','license_geos','license_win32',
                      'zip-safe','debian-copyright','elevhub-cadoodle','ansible.cfg'))
MAX_TEXT=32*1024**2
MAX_INVENTORY=16*1024**2
VERSION_PATTERN=re.compile(r'\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?')


# Package review is independent of source export: prebuilt archives must obey
# the same operational feature boundary. Educational Datatraener remains valid.
PRIVATE_FEATURE_FILES=frozenset(('bed_ai.py','local_ai.py','vision.py','training_samples.py',
    'laser_vision.py','laser_vision_client.py','laser_vision_server.py','server_ai_artifact.py',
    'plate_camera_client.py','camera-batch.js','plate-camera.js'))
FEATURE_COMPONENT=re.compile(r'^(?:ai|ollama|torch|torchvision|torchaudio|transformers|diffusers|onnxruntime|rembg|transparent_background|segment_anything|llama_cpp|timm|safetensors|huggingface_hub|accelerate|tokenizers)(?:$|[-_.])',re.I)
MODEL_SUFFIXES=frozenset(('.onnx','.safetensors','.gguf','.ggml','.tflite','.ckpt','.pt'))


def reject_operational_feature(name):
    path=PurePosixPath(name)
    educational='datatraener' in {part.casefold() for part in path.parts}
    if (path.name.casefold() in PRIVATE_FEATURE_FILES
            or any(FEATURE_COMPONENT.match(part) for part in path.parts)
            or (not educational and path.suffix.casefold() in MODEL_SUFFIXES)):
        raise ValueError('operational_ai_member_in_package: '+name)


def source_name(name):
    """Use original source paths for the exact, reviewed privacy exceptions."""
    parts=name.split('/')
    if len(parts)>2 and parts[0]=='candidate' and parts[1] in {'hub','print'}:
        name='/'.join(parts[2:])
        if parts[1]=='hub' and name.startswith('hub/'):
            name='linux-elevhub/server/elevhub/'+name[4:]
        elif parts[1]=='hub' and name.startswith('server/'):
            name='windows-elevhub/server/'+name[7:]
    if name.startswith('assets/scratch/'):return 'linux-elevhub/server/scratch/'+name[len('assets/scratch/'):]
    if name.startswith('assets/datatraener/'):return 'linux-elevhub/server/datatraener/'+name[len('assets/datatraener/'):]
    return name


def private_name(name):
    parts=PurePosixPath(name).parts
    if (set(p.casefold() for p in parts)&{'data','queues','backups','logs','profiles'}
            or Path(name).suffix.casefold() in {'.pem','.key','.pfx','.p12','.sqlite','.sqlite3','.db','.crt'}
            or Path(name).name.casefold() in {'known_hosts','deployment_key','enheder.json','installation-owner.json','status.json'}):
        raise ValueError('runtime_or_identity_data_in_package')


def review_stream(name,stream,size,policy,binary_pins):
    """Hash every member; text is always inspected, even when its hash is pinned."""
    with ExitStack() as stack:
        reject_operational_feature(name)
        private_name(name)
        text=(Path(name).suffix.casefold() in TEXT or Path(name).name.casefold() in TEXT_NAMES)
        if text and size>MAX_TEXT:raise ValueError('package_text_member_too_large')
        h=hashlib.sha256();content=bytearray() if text else None;count=0
        nested=stack.enter_context(tempfile.SpooledTemporaryFile(max_size=1024**2)) if Path(name).suffix.casefold() in {'.zip','.whl'} else None
        for block in iter(lambda:stream.read(1024*1024),b''):
            count+=len(block);h.update(block)
            if content is not None:content.extend(block)
            if nested is not None:nested.write(block)
            if count>size:raise ValueError('package_member_size_mismatch')
        if count!=size:raise ValueError('package_member_size_mismatch')
        value=h.hexdigest()
        if nested is not None:
            try:
                nested.seek(0)
                if zipfile.is_zipfile(nested):
                    nested.seek(0)
                    with zipfile.ZipFile(nested) as archive:
                        for member in archive.infolist():
                            reject_operational_feature(name+'/'+member.filename)
            finally:nested.close()
        if text:
            if public_source.privacy_findings(source_name(name),bytes(content),policy):
                raise ValueError('package_privacy_preflight_failed: '+name)
        elif value not in binary_pins:raise ValueError('opaque_binary_requires_public_review: '+name)
        return value


def verify_linux_payload(path,version,policy,binary_pins):
    """The outer tar hash never substitutes for reviewing the inner software."""
    actual={};seen=set();manifest=None;release=None;total=0;credit=False
    with tarfile.open(fileobj=path if hasattr(path,'read') else None,
                      name=None if hasattr(path,'read') else path,mode='r|gz') as archive:
        for entry in archive:
            name=member_name(entry.name.rstrip('/'));folded=name.casefold()
            reject_operational_feature(name)
            if folded in seen or len(seen)>=100000:raise ValueError('unsafe_linux_payload')
            seen.add(folded);total+=entry.size
            if total>8*1024**3:raise ValueError('linux_payload_too_large')
            if entry.isdir():continue
            if not entry.isfile():raise ValueError('unsafe_linux_payload')
            stream=archive.extractfile(entry)
            if name in {'manifest.json','linux-elevhub/server/elevhub/release.json','CREDITS.md'}:
                if entry.size>MAX_INVENTORY:raise ValueError('linux_manifest_too_large')
                data=stream.read();value=hashlib.sha256(data).hexdigest()
                if name=='manifest.json':manifest=feed.strict_json(data)
                elif name=='CREDITS.md':credit=all(value.encode() in data for value in (public_source.CREDIT, public_source.AUTHOR_CREDIT))
                else:release=feed.strict_json(data)
                if public_source.privacy_findings(name,data,policy):raise ValueError('package_privacy_preflight_failed')
            else:value=review_stream(name,stream,entry.size,policy,binary_pins)
            actual[name]=value
    if (not isinstance(manifest,dict) or set(manifest)!={'version','files'}
            or manifest['version']!=version or not isinstance(manifest['files'],dict)
            or not isinstance(release,dict) or release.get('release_id')!=version
            or release.get('expected_client_release')!=version):raise ValueError('linux_payload_version_mismatch')
    if set(actual)!=set(manifest['files'])|{'manifest.json'}:raise ValueError('linux_payload_inventory_mismatch')
    if any(not re.fullmatch('[a-f0-9]{64}',str(value)) or actual[name]!=value for name,value in manifest['files'].items()):
        raise ValueError('linux_payload_hash_mismatch')
    required={'CREDITS.md','assets/CaDoodle.deb','assets/CaDoodle-ApplicationInstall.zip','assets/CaDoodle-Kloster.jar',
              'assets/scratch/index.html','assets/datatraener/index.html',
              'linux-elevhub/ansible/roles/kiosk/tasks/main.yml','linux-elevhub/ansible/roles/cadoodle/tasks/main.yml',
              'linux-elevhub/kiosk-window-guard/bin/matchbox-window-manager'}
    if not required.issubset(actual):raise ValueError('linux_payload_incomplete')
    if not credit:raise ValueError('package_credit_missing')
    return manifest


def digest(path):
    with feed.plain(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def member_name(name):
    if (not isinstance(name,str) or not name or str(PurePosixPath(name))!=name or name.startswith('/')
            or any(c in name for c in '\\:*?"<>|') or any(ord(c)<32 for c in name)
            or any(part in ('','.','..') or part.endswith((' ','.')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',part)
                   for part in name.split('/'))):raise ValueError('unsafe_package_member')
    return name


def _version_key(value):
    if not isinstance(value,str) or not VERSION_PATTERN.fullmatch(value):return None
    try:datetime.datetime.strptime(value[:10],'%Y.%m.%d')
    except ValueError:return None
    parts=tuple(int(part) for part in value.split('.'))
    return parts if len(parts)==4 else (*parts,0)


def verify_server_inventory(archive,files,version,allow_older_client_release=False):
    name='candidate/candidate.json'
    if name not in files:raise ValueError('server_candidate_missing')
    if archive.getinfo(name).file_size>MAX_INVENTORY:raise ValueError('server_candidate_inventory_too_large')
    value=feed.strict_json(archive.read(name))
    if (value.get('format')!='makervaerkstedet-school-flat-update-v1' or value.get('version')!=version
            or not isinstance(value.get('files'),dict) or set(value['files'])!={'hub','print'}
            or not isinstance(value.get('trusted_runtime'),dict)):
        raise ValueError('server_candidate_version_format_mismatch')
    expected={name,'CREDITS.md'}
    for role,inventory in value['files'].items():
        if not isinstance(inventory,dict):raise ValueError('server_candidate_inventory_mismatch')
        required={'CREDITS.md','runtime/python.exe','runtime/python314.dll','runtime/python314.zip'}
        if not required.issubset(inventory):raise ValueError('server_candidate_runtime_incomplete')
        for relative,want in inventory.items():
            member_name(relative)
            if not relative.startswith(('app/','web/','hub/','server/','runtime/','third_party/')) and relative!='CREDITS.md':
                raise ValueError('server_candidate_noncode_file')
            outer='candidate/'+role+'/'+relative;expected.add(outer)
            if not isinstance(want,str) or not re.fullmatch('[a-f0-9]{64}',want) or files.get(outer)!=want:
                raise ValueError('server_candidate_inventory_mismatch')
            if relative.startswith('runtime/') and value['trusted_runtime'].get(relative)!=want:
                raise ValueError('server_candidate_runtime_pin_mismatch')
    if set(files)!=expected:raise ValueError('server_candidate_inventory_mismatch')
    release_name='candidate/hub/hub/release.json'
    if release_name not in files or archive.getinfo(release_name).file_size>MAX_INVENTORY:
        raise ValueError('server_candidate_release_missing')
    release=feed.strict_json(archive.read(release_name))
    if release.get('release_id')!=version:
        raise ValueError('server_candidate_release_mismatch')
    client_release=release.get('expected_client_release')
    if allow_older_client_release:
        release_key=_version_key(version);client_key=_version_key(client_release)
        if release_key is None or client_key is None or client_key>release_key:
            raise ValueError('server_candidate_release_mismatch')
    elif client_release!=version:
        raise ValueError('server_candidate_release_mismatch')


def verify_zip(path,role,format_,version,expected,policy,binary_pins,allow_older_client_release=False):
    path=feed.plain(path)
    if (role not in feed.ROLES or format_!=feed.ROLES[role] or not feed.valid_asset(path.name)
            or not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected)
            or not 0<path.stat().st_size<feed.MAX_PACKAGE or digest(path)!=expected):raise ValueError('unverified_package')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist();names=[member_name(e.filename) for e in entries]
        for name in names:reject_operational_feature(name)
        if (not entries or len(entries)>100000 or len(set(n.casefold() for n in names))!=len(names)
                or any((stat.S_IFMT(e.external_attr>>16) and not stat.S_ISREG(e.external_attr>>16)) or e.flag_bits&1 for e in entries)
                or sum(e.file_size for e in entries)>8*1024**3):raise ValueError('unsafe_package_archive')
        marker_name=MARKER if MARKER in names else 'manifest.json' if format_=='laser-code-v1' else None
        if not marker_name or archive.getinfo(marker_name).file_size>MAX_INVENTORY:raise ValueError('package_marker_missing')
        marker=feed.strict_json(archive.read(marker_name))
        if marker_name==MARKER:
            if (set(marker)!={'schema','role','format','version','files'} or type(marker['schema']) is not int or marker['schema']!=1
                    or marker['role']!=role or marker['format']!=format_ or marker['version']!=version
                    or not isinstance(marker['files'],dict)):raise ValueError('package_role_version_mismatch')
            files=marker['files']
        else:
            if (set(marker)!={'format','release','files'} or marker['format']!='makervaerkstedet-station-code-v1'
                    or marker['release']!=version or role!='windows_laser_teacher' or not isinstance(marker['files'],dict)):
                raise ValueError('legacy_laser_marker_mismatch')
            if not {'app/laser_station.py','app/instance.py','app/backup_guard.py','web/laserstation.html','CREDITS.md'}.issubset(marker['files']):
                raise ValueError('legacy_laser_incomplete')
            files={name:info['sha256'] for name,info in marker['files'].items() if isinstance(info,dict) and set(info)=={'sha256','size'}}
            if len(files)!=len(marker['files']):raise ValueError('invalid_package_manifest')
        if set(names)!=set(files)|{marker_name} or 'CREDITS.md' not in files:raise ValueError('package_inventory_mismatch')
        if role=='windows_laser_teacher' and marker_name!='manifest.json':raise ValueError('laser_native_manifest_required')
        if role=='linux_student' and set(files)!={'payload.tar.gz','CREDITS.md'}:raise ValueError('linux_wrapper_incomplete')
        if role=='windows_server':
            verify_server_inventory(archive,files,version,
                                    allow_older_client_release=allow_older_client_release)
        for name,expected_file in files.items():
            member_name(name)
            if not isinstance(expected_file,str) or not re.fullmatch('[a-f0-9]{64}',expected_file):raise ValueError('invalid_package_manifest')
            info=archive.getinfo(name)
            if name=='payload.tar.gz' and role=='linux_student':
                with archive.open(name) as stream:verify_linux_payload(stream,version,policy,binary_pins)
                with archive.open(name) as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            else:
                with archive.open(name) as stream:actual=review_stream(name,stream,info.file_size,policy,binary_pins)
            if actual!=expected_file:raise ValueError('package_file_hash_mismatch')
            if marker_name=='manifest.json' and (type(marker['files'][name]['size']) is not int or marker['files'][name]['size']!=info.file_size):
                raise ValueError('package_file_size_mismatch')
        if not all(value.encode() in archive.read('CREDITS.md') for value in (public_source.CREDIT, public_source.AUTHOR_CREDIT)):raise ValueError('package_credit_missing')
    if digest(path)!=expected:raise ValueError('package_changed_during_review')
    return {'role':role,'asset':path.name,'format':format_,'sha256':expected,'size':path.stat().st_size}


def build_manifest(source_root,version,notes,packages,output,approved_binary_sha256=(),repository='example/project'):
    packages=list(packages)
    source_root=feed.plain(source_root);output=feed.plain(output)
    policy=public_source._load_policy(source_root)
    public_source.audit(source_root,policy)  # Fail before creating any publication output.
    if not hasattr(public_source,'privacy_findings'):raise ValueError('privacy_exporter_upgrade_required')
    if not isinstance(notes,str) or len(notes)>20000 or public_source.privacy_findings('release-notes.txt',notes.encode(),policy):raise ValueError('release_notes_privacy_failed')
    binary_pins=set(approved_binary_sha256)
    if any(not isinstance(pin,str) or not re.fullmatch('[a-f0-9]{64}',pin) for pin in binary_pins):raise ValueError('invalid_reviewed_binary_pin')
    # Reuse immutable artwork/font pins from the already audited public tree.
    # audit() has already checked these exact source pins. Do not rescan all
    # source text solely to recover the immutable artwork/font/source hashes.
    binary_pins.update(public_source._public_image_map(policy).values())
    binary_pins.update(public_source._public_font_map(policy).values())
    binary_pins.update(public_source.SOURCE_ARCHIVES.values())
    # A server-only selected release may advance the server without declaring
    # unchanged pupil clients stale. Full-system manifests keep aligned versions.
    server_only_release=(len(packages)==1 and packages[0].get('role')=='windows_server')
    package_rows=[verify_zip(p['path'],p['role'],p['format'],version,p['expected_sha256'],policy,binary_pins,
                             allow_older_client_release=server_only_release) for p in packages]
    manifest={'schema':1,'product':'Makervaerkstedet','version':version,'notes':notes,'packages':package_rows}
    if not isinstance(repository,str) or not feed.REPOSITORY.fullmatch(repository):
        raise ValueError('invalid_repository')
    metadata={'id':1,'draft':False,'prerelease':False,'tag_name':'v'+version,'assets':[
        {'name':p['asset'],'state':'uploaded','size':p['size'],'browser_download_url':f'https://github.com/{repository}/releases/download/v{version}/{p["asset"]}'} for p in package_rows]}
    feed.release_manifest(metadata,manifest,repository)
    if output.name!='makervaerkstedet-update.json':raise ValueError('manifest_filename_must_match_feed')
    with output.open('x',encoding='utf-8') as stream:json.dump(manifest,stream,ensure_ascii=False,indent=2);stream.write('\n')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--notes',type=Path,required=True)
    parser.add_argument('--package',nargs=4,action='append',metavar=('ROLE','FORMAT','ZIP','EXPECTED_SHA256'),required=True)
    parser.add_argument('--approved-binary-sha256',action='append',default=[])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    packages=[dict(zip(('role','format','path','expected_sha256'),p)) for p in args.package]
    build_manifest(args.source_root,args.version,args.notes.read_text(encoding='utf-8'),packages,args.output,args.approved_binary_sha256)
    print(json.dumps({'status':'manifest_written_not_uploaded','packages':len(packages)}))


if __name__=='__main__':main()
