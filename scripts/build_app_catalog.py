"""Create unsigned schema2 app-catalog metadata from checked native .debs.

Run on Linux/WSL: python -B scripts/build_app_catalog.py --inputs inputs.json
--output catalog.json. Inputs contain schema=2, version, sequence,
source_commit (immutable lowercase Git SHA), release_tag, packages (the app
package records with path/size/sha256/package/version/architecture), and
app_descriptors. Sign the pinned output with build_simple_feed on Windows, or
an explicitly selected existing publisher signer. No keys or installation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import subprocess
import sys
import tarfile
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"system")]
from app.app_catalog import validate_app_descriptor,validate_catalog,_unique_pairs
from app.simple_release import REPOSITORY,MAX_MANIFEST,MAX_ARTIFACT


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def plain(path):
    path=Path(path).absolute()
    if any(part.is_symlink() or part.is_junction() for part in (path,*path.parents)):
        raise ValueError('linked_app_package_input')
    return path


def packaged_descriptor(source,descriptor):
    """Stream native tar data without extracting paths or running package code."""
    target=f"usr/share/makervaerkstedet/apps/{descriptor['id']}/app.json"
    parents={str(parent) for parent in PurePosixPath(target).parents}
    found=[]
    process=subprocess.Popen(['dpkg-deb','--fsys-tarfile',str(source)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        with tarfile.open(fileobj=process.stdout,mode='r|') as archive:
            for member in archive:
                relative=PurePosixPath(member.name)
                if relative.is_absolute() or '..' in relative.parts:raise ValueError('unsafe_app_package_member')
                name=str(relative)
                if name in parents and not member.isdir():raise ValueError('linked_app_descriptor_parent')
                if name!=target:continue
                if not member.isfile() or not 0<member.size<=MAX_MANIFEST or found:
                    raise ValueError('invalid_packaged_app_descriptor')
                raw=archive.extractfile(member).read(MAX_MANIFEST+1)
                value=json.loads(raw,object_pairs_hook=_unique_pairs)
                validate_app_descriptor(value)
                if value!=descriptor:raise ValueError('packaged_app_descriptor_mismatch')
                found.append(value)
        if process.wait()!=0:raise ValueError('app_package_native_read_failed')
    except (tarfile.TarError,UnicodeError,json.JSONDecodeError) as error:
        raise ValueError('invalid_packaged_app_descriptor') from error
    finally:
        process.stdout.close()
        if process.poll() is None:process.kill();process.wait()
    if len(found)!=1:raise ValueError('packaged_app_descriptor_missing')


def checked_package(entry,descriptor=None):
    if type(entry) is not dict or set(entry)!={'path','size','sha256','package','version','architecture'}:
        raise ValueError('invalid_package_input')
    if (type(entry['size']) is not int or not 0<entry['size']<=MAX_ARTIFACT or type(entry['sha256']) is not str
            or re.fullmatch('[a-f0-9]{64}',entry['sha256']) is None):raise ValueError('invalid_package_identity')
    source=plain(entry['path'])
    if not source.is_file() or source.stat().st_size!=entry['size'] or sha(source)!=entry['sha256']:
        raise ValueError('package_hash_or_size_mismatch')
    observed={field.lower():subprocess.check_output(['dpkg-deb','--field',str(source),field],text=True).strip()
              for field in ('Package','Version','Architecture')}
    if any(observed[field]!=entry[field] for field in observed):raise ValueError('package_control_mismatch')
    if descriptor is not None:
        validate_app_descriptor(descriptor)
        if any(entry[field]!=descriptor[field] for field in observed):raise ValueError('app_package_identity_mismatch')
        packaged_descriptor(source,descriptor)
    if sha(source)!=entry['sha256']:raise ValueError('package_changed_during_validation')
    return source


def build(spec):
    fields={'schema','version','sequence','source_commit','release_tag','packages','app_descriptors'}
    if (type(spec) is not dict or set(spec)!=fields or type(spec['schema']) is not int or spec['schema']!=2
            or type(spec['packages']) is not list or type(spec['app_descriptors']) is not list
            or type(spec['source_commit']) is not str or re.fullmatch('[a-f0-9]{40}',spec['source_commit']) is None
            or type(spec['release_tag']) is not str or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.+~-]{0,127}',spec['release_tag']) is None):
        raise ValueError('invalid_catalog_inputs')
    packages={}
    for entry in spec['packages']:
        if type(entry) is not dict or type(entry.get('package')) is not str:raise ValueError('invalid_package_input')
        if entry['package'] in packages:raise ValueError('duplicate_catalog_package')
        packages[entry['package']]=entry
    apps=[]
    for descriptor in spec['app_descriptors']:
        validate_app_descriptor(descriptor)
        if descriptor['package'] not in packages:raise ValueError('catalog_package_set_mismatch')
        entry=packages[descriptor['package']]
        name=f"{entry['package']}_{entry['version']}_{entry['architecture']}.deb"
        apps.append({'descriptor':descriptor,'sequence':spec['sequence'],'source_commit':spec['source_commit'],
                     'artifact':{'name':name,'size':entry['size'],'sha256':entry['sha256'],
                                 'url':f"https://github.com/{REPOSITORY}/releases/download/{spec['release_tag']}/{quote(name,safe='')}"}})
    if set(packages)!={app['descriptor']['package'] for app in apps}:raise ValueError('catalog_package_set_mismatch')
    value={'schema':2,'kind':'makervaerkstedet-app-catalog','repository':REPOSITORY,'role':'student',
           'platform':'debian-amd64','component':'app-catalog','version':spec['version'],'sequence':spec['sequence'],'apps':apps}
    validate_catalog(value,repository=REPOSITORY)
    for app in apps:checked_package(packages[app['descriptor']['package']],app['descriptor'])
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        value=build(json.loads(plain(args.inputs).read_bytes(),object_pairs_hook=_unique_pairs))
        output=plain(args.output)
        with output.open('x',encoding='utf-8') as stream:stream.write(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))+'\n')
    except (ValueError,OSError,subprocess.CalledProcessError) as error:parser.exit(2,str(error)+'\n')
    return 0


if __name__=='__main__':raise SystemExit(main())
