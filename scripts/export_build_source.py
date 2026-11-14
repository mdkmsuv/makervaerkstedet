#!/usr/bin/env python3
"""Export committed or fully verified public sources plus selected, receipt-bound builds."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'system')]
from scripts import public_source
from scripts.validate_web_build import TARGETS, validate_web_build
from repository import repo_root
from app.app_catalog import component_for_app

GENERATOR = {'path': 'scripts/export_build_source.py', 'version': 1}
MAX_FILES = 200_000
MAX_FILE = 128*1024*1024
MAX_TOTAL = 512*1024*1024
MAX_ARCHIVE = MAX_TOTAL + MAX_FILES*1024
MODEL3D = ('model3d-app.js', 'model3d-app.js.LEGAL.txt', 'model3d-geometry-worker.js',
           'model3d-manifold.wasm', 'model3d-vendor-manifest.json')


def _relative(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value or any(ord(c)<32 for c in value):
        raise ValueError('invalid_archive_path')
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix()!=value or any(p in ('.','..','.git') for p in path.parts):
        raise ValueError('invalid_archive_path')
    return value


def _extract_archive(archive, destination):
    """Never delegate link/path handling to tar.extractall()."""
    archive=Path(archive)
    if archive.stat().st_size>MAX_ARCHIVE:raise ValueError('source_archive_total_limit')
    destination = Path(destination)
    destination.mkdir()
    seen, spellings, regular, total = set(), {}, set(), 0
    with tarfile.open(archive, 'r:') as source:
        for index, member in enumerate(source):
            if index >= MAX_FILES:raise ValueError('source_archive_file_limit')
            name = _relative(member.name.rstrip('/') if member.isdir() else member.name)
            folded = name.casefold()
            if folded in seen:raise ValueError('source_archive_duplicate_path')
            seen.add(folded)
            for component in (*reversed(PurePosixPath(name).parents),PurePosixPath(name)):
                spelling=component.as_posix()
                if spelling=='.':continue
                previous=spellings.setdefault(spelling.casefold(),spelling)
                if previous!=spelling:raise ValueError('source_archive_duplicate_path')
            if not (member.isdir() or member.isreg()):raise ValueError('source_archive_non_regular_entry')
            target = destination/name
            if not target.resolve().is_relative_to(destination.resolve()):raise ValueError('invalid_archive_path')
            if member.isdir():
                target.mkdir(parents=True,exist_ok=True)
                continue
            if member.size < 0 or member.size > MAX_FILE:raise ValueError('source_archive_file_size_limit')
            total += member.size
            if total > MAX_TOTAL:raise ValueError('source_archive_total_limit')
            target.parent.mkdir(parents=True,exist_ok=True)
            with source.extractfile(member) as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming,outgoing,1024*1024)
            if target.stat().st_size != member.size:raise ValueError('source_archive_truncated_file')
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
            regular.add(name)
    return regular


def _git_command(root):
    metadata = root/'.git'
    public_source._lstat_checked(metadata)
    if os.name != 'nt' and metadata.is_file() and re.match(r'gitdir:\s*[A-Za-z]:[/\\]',metadata.read_text(encoding='utf-8')):
        windows = subprocess.check_output(['wslpath','-w',str(root)],text=True,timeout=30).strip()
        git = Path('/mnt/c/Program Files/Git/cmd/git.exe')
        if not git.is_file():raise ValueError('windows_git_required_for_worktree')
        return [str(git),'-C',windows,'-c','safe.directory='+windows,'-c','core.autocrlf=false','-c','core.eol=lf']
    return ['git','-C',str(root),'-c','safe.directory='+str(root),'-c','core.autocrlf=false','-c','core.eol=lf']


def _git_archive(root, archive):
    command = _git_command(root)
    commit = subprocess.check_output([*command,'rev-parse','--verify','HEAD^{commit}'],text=True,timeout=60).strip()
    if not re.fullmatch(r'[0-9a-f]{40,64}',commit):raise ValueError('invalid_git_commit')
    with tempfile.TemporaryFile() as errors, archive.open('xb') as output:
        with subprocess.Popen([*command,'archive','--format=tar',commit],stdout=subprocess.PIPE,stderr=errors) as process:
            size = 0
            while chunk := process.stdout.read(1024*1024):
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    process.kill();process.wait()
                    raise ValueError('source_archive_total_limit')
                output.write(chunk)
            code = process.wait(timeout=60)
            if code:
                errors.seek(0)
                raise ValueError('git_archive_failed:'+errors.read(8192).decode(errors='replace'))
    dirty = subprocess.check_output([*command,'diff-index','--name-only','-z','HEAD','--'],timeout=60)
    return commit, len([name for name in dirty.split(b'\0') if name])


def _policy_digest(policy):
    return hashlib.sha256((json.dumps(policy,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()


def _inventory(root):
    files, directories, seen = set(), set(), set()
    for current, dirnames, filenames in os.walk(root,followlinks=False):
        for name in (*dirnames,*filenames):
            path = Path(current)/name
            relative = _relative(path.relative_to(root).as_posix())
            if relative.casefold() in seen:raise ValueError('source_duplicate_path')
            seen.add(relative.casefold())
            info = public_source._lstat_checked(path,reject_hardlink=True)
            if stat.S_ISDIR(info.st_mode):directories.add(relative)
            elif stat.S_ISREG(info.st_mode):files.add(relative)
            else:raise ValueError('source_non_regular_entry')
            if len(seen) > MAX_FILES:raise ValueError('source_file_limit')
    return files, directories


def _generated_paths(apps):
    paths = {'system/web/vendor/viewer.js','system/web/vendor/web-build-manifest.json'}
    if 'folie' in apps:
        folder,names,_ = TARGETS['folie']
        paths.update(folder+'/'+name for name in names|{'web-build-manifest.json'})
    if '3dprint' in apps:paths.update('apps/3dprint/web/vendor/'+name for name in MODEL3D)
    return paths


def _portable_modes(root):
    """Windows and DrvFS without metadata cannot retain POSIX permission bits."""
    if os.name=='nt':return True
    mountinfo=Path('/proc/self/mountinfo')
    if not mountinfo.is_file():return False
    matched=None
    for line in mountinfo.read_text(encoding='utf-8').splitlines():
        before,separator,after=line.partition(' - ')
        fields=before.split();filesystem=after.split()
        if not separator or len(fields)<6 or len(filesystem)<3:continue
        mount=Path(re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[4]))
        if root==mount or root.is_relative_to(mount):
            if matched is None or len(mount.parts)>matched[0]:
                matched=(len(mount.parts),filesystem[0],','.join(fields[5:]+filesystem[1:]))
    if matched is None:return False
    _,kind,options=matched
    drvfs=kind=='drvfs' or kind=='9p' and 'aname=drvfs' in options
    return drvfs and 'metadata' not in re.split(r'[,;]',options)


def _verify_public_source(root, policy, selected, apps):
    manifest_path = root/public_source.MANIFEST_NAME
    if not manifest_path.is_file():raise ValueError('verified_public_source_manifest_required')
    public_source._check_generated_manifest(manifest_path,policy)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['sourcePolicySha256'] != _policy_digest(policy):raise ValueError('source_policy_digest_mismatch')
    expected = {item.relative:item for item in selected}
    recorded = {item['path']:item for item in manifest['files']}
    if set(expected) != set(recorded):raise ValueError('source_manifest_inventory_mismatch')
    portable_modes=_portable_modes(root)
    for name,item in expected.items():
        record = recorded[name]
        mode=int(record['mode'],8)
        if mode & ~0o777:raise ValueError('unsafe_source_manifest_mode:'+name)
        if (record['size'],record['sha256']) != (item.size,item.sha256):
            raise ValueError('source_manifest_content_mismatch:'+name)
        # Windows cannot retain POSIX executable bits. Never discard hash/size checks.
        if not portable_modes and mode != item.mode:
            raise ValueError('source_manifest_mode_mismatch:'+name)
    allowed = set(expected)|{public_source.MANIFEST_NAME}|_generated_paths(('folie','3dprint'))
    files,directories = _inventory(root)
    if files-allowed:raise ValueError('unexpected_source_file:'+sorted(files-allowed)[0])
    parent_paths = {p.as_posix() for name in allowed for p in PurePosixPath(name).parents if p.as_posix()!='.'}
    if directories-parent_paths:raise ValueError('unexpected_source_directory:'+sorted(directories-parent_paths)[0])
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _copy_generated(source, destination, apps):
    validate_web_build(source,'system')
    if 'folie' in apps:validate_web_build(source,'folie')
    records, total = [], 0
    for name in sorted(_generated_paths(apps)):
        origin = source/name
        public_source._check_parent_chain(origin,source)
        info = public_source._lstat_checked(origin,reject_hardlink=True)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:raise ValueError('invalid_generated_build_file:'+name)
        data = origin.read_bytes()
        if len(data)>MAX_FILE:raise ValueError('invalid_generated_build_file:'+name)
        total+=len(data)
        if total>MAX_TOTAL:raise ValueError('generated_build_total_limit')
        target = destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:stream.write(data)
        records.append({'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    validate_web_build(destination,'system',built_from=source)
    if 'folie' in apps:validate_web_build(destination,'folie',built_from=source)
    if '3dprint' in apps:
        import importlib.util
        spec=importlib.util.spec_from_file_location('_export_model3d_gate',ROOT/'apps/3dprint/validate_build.py')
        gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
        gate.validate_model3d_bundle(destination,built_from=source)
    return records


def export_build_source(source_root, output, report, *, apps=()):
    source = Path(source_root).resolve()
    public_source._check_parent_chain(source,Path(source.anchor))
    if repo_root(source)!=source:raise ValueError('repository_layout_missing')
    apps = tuple(apps)
    if len(apps)!=len(set(apps)):raise ValueError('duplicate_selected_app')
    for app in apps:component_for_app(app)
    apps = tuple(sorted(apps))
    output = public_source._safe_output(str(output),source)
    report = public_source._safe_output(str(report),source)
    if report==output or report.is_relative_to(output):raise ValueError('source_report_must_be_outside_export')
    output.parent.mkdir(parents=True,exist_ok=True)
    report.parent.mkdir(parents=True,exist_ok=True)
    result = {'schema':1,'generator':GENERATOR,'output':str(output),'apps':list(apps)}
    with tempfile.TemporaryDirectory(prefix='.mkv-build-source-',dir=output.parent) as temporary:
        stage = Path(temporary).resolve()
        if not stage.is_relative_to(output.parent):raise ValueError('invalid_owned_staging_root')
        if (source/'.git').exists():
            archive=stage/'head.tar'
            commit,dirty=_git_archive(source,archive)
            pure=stage/'committed'
            archived=_extract_archive(archive,pure)
            if repo_root(pure)!=pure:raise ValueError('repository_layout_missing')
            policy=public_source._load_policy(pure)
            selected=public_source._collect(pure,policy)
            public_source._validate_policy(pure,policy,selected)
            excluded=archived-{item.relative for item in selected}
            if public_source.MANIFEST_NAME in excluded:
                public_source._check_generated_manifest(pure/public_source.MANIFEST_NAME,policy)
                excluded.remove(public_source.MANIFEST_NAME)
            if excluded:raise ValueError('unselected_archived_file:'+sorted(excluded)[0])
            result.update(mode='git',commit=commit,uncommitted_tracked_files_not_built=dirty)
        else:
            pure=source
            policy=public_source._load_policy(pure)
            selected=public_source._collect(pure,policy)
            public_source._validate_policy(pure,policy,selected)
            manifest_digest=_verify_public_source(pure,policy,selected,apps)
            result.update(mode='public-source',public_manifest_sha256=manifest_digest)
        exported=stage/'export'
        summary=public_source.export_source(pure,policy,str(exported))
        result.update(source_policy_sha256=_policy_digest(policy),files=summary['files'],bytes=summary['bytes'])
        copied=json.loads((exported/public_source.MANIFEST_NAME).read_text(encoding='utf-8'))
        identity={item.relative:(item.size,item.sha256,item.mode) for item in selected}
        if {item['path']:(item['size'],item['sha256'],int(item['mode'],8)) for item in copied['files']}!=identity:
            raise ValueError('source_changed_after_verification')
        result['generated']=_copy_generated(source,exported,apps)
        public_source._publish(exported,output)
    with report.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2)
        stream.write('\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--apps',nargs='*',default=[])
    args=parser.parse_args()
    result=export_build_source(args.source_root,args.output,args.report,apps=args.apps)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))


if __name__=='__main__':main()
