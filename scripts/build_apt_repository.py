"""Build a flat, GPG-signed apt repository. No apt installation or key generation.

Run with existing Linux dpkg-scanpackages/apt-ftparchive. In WSL an explicitly
pinned Windows GPG can sign files using wslpath; secret homedir stays outside
public output. Minisign does not replace this apt signature.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.simple_release import DEBIAN_COMPONENTS
DEBIAN13_KEYRING_SHA256='506b815cbb32d9b6066b4a2aa524071e071761e7e7f68c3ac74f3061ba852017'

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def plain(path):
    path=Path(path).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or part.is_junction():raise ValueError('linked_repository_path')
    return path

def tool_path(path,gpg):
    if gpg.suffix.lower()=='.exe':
        windows=subprocess.check_output(['wslpath','-w',str(path)],text=True).strip()
        if not re.match(r'^[A-Za-z]:',windows):raise ValueError('gpg_requires_local_windows_drive_path')
        return '/'+windows[0].lower()+windows[2:].replace('\\','/')
    return str(path)

def validate_install_gate(record,packages):
    if type(record) is not dict or set(record)!={'path','sha256'} or not isinstance(record['sha256'],str) or re.fullmatch('[a-f0-9]{64}',record['sha256']) is None:
        raise ValueError('install_gate_pin_required')
    path=plain(record['path'])
    if not path.is_file() or path.stat().st_size>1024**2:raise ValueError('install_gate_unavailable')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('install_gate_hash_mismatch')
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('duplicate_install_gate_field')
            value[key]=item
        return value
    receipt=json.loads(raw,object_pairs_hook=unique)
    expected_fields={'schema','target','packages','all_packages_simulated','actual_install','isolated_paths','apt_config_sha256','debian_keyring_sha256'}
    if (type(receipt) is not dict or set(receipt)!=expected_fields or type(receipt['schema']) is not int or receipt['schema']!=1
            or receipt['target']!='debian13-amd64-empty-status' or receipt['all_packages_simulated'] is not True
            or receipt['actual_install'] is not False or type(receipt['isolated_paths']) is not int or receipt['isolated_paths']<=0
            or any(not isinstance(receipt[key],str) or re.fullmatch('[a-f0-9]{64}',receipt[key]) is None for key in ('apt_config_sha256','debian_keyring_sha256'))):
        raise ValueError('clean_debian13_simulation_required')
    if receipt['debian_keyring_sha256']!=DEBIAN13_KEYRING_SHA256:raise ValueError('debian_archive_keyring_pin_mismatch')
    if type(packages) is not list or len(packages)!=len(DEBIAN_COMPONENTS):raise ValueError('exact_release_simulated_packages_required')
    expected={}
    for entry in packages:
        if type(entry) is not dict or not {'package','version','architecture','sha256','path'}<=set(entry):raise ValueError('invalid_package_input')
        if entry['package'] in expected:raise ValueError('duplicate_package_version')
        expected[entry['package']]={key:entry[key] for key in ('version','architecture','sha256')}
        expected[entry['package']]['file']=Path(entry['path']).name
    if set(expected)!={pair[0] for pair in DEBIAN_COMPONENTS.values()} or receipt['packages']!=expected:
        raise ValueError('install_gate_package_mismatch')
    return record['sha256']

def build(spec,output,*,gpg_exe,gpg_sha256,gnupg_home,key_fingerprint):
    if set(spec)!={'schema','packages','test_repository','install_gate'} or type(spec['schema']) is not int or spec['schema']!=1 or type(spec['test_repository']) is not bool:raise ValueError('invalid_repository_inputs')
    install_gate_sha256=validate_install_gate(spec['install_gate'],spec['packages'])
    output=plain(output);home=plain(gnupg_home);gpg=plain(gpg_exe)
    if output.exists():raise ValueError('output_already_exists')
    if home==output or home in output.parents or output in home.parents or 'onedrive' in str(home).casefold():raise ValueError('signing_home_not_private')
    if not home.is_dir() or not re.fullmatch('[A-Fa-f0-9]{40}',key_fingerprint):raise ValueError('explicit_existing_signing_key_required')
    if re.fullmatch('[a-f0-9]{64}',gpg_sha256 or '') is None or not gpg.is_file() or sha(gpg)!=gpg_sha256:raise ValueError('gpg_pin_mismatch')
    if type(spec['packages']) is not list or not spec['packages']:raise ValueError('empty_package_list')
    allowed=set(DEBIAN_COMPONENTS.values());seen=set();rows=[]
    for entry in spec['packages']:
        if set(entry)!={'path','sha256','size','package','version','architecture'}:raise ValueError('invalid_package_input')
        source=plain(entry['path'])
        if not source.is_file() or source.stat().st_size!=entry['size'] or sha(source)!=entry['sha256']:raise ValueError('package_hash_or_size_mismatch')
        if (entry['package'],entry['architecture']) not in allowed:raise ValueError('package_not_allowlisted')
        observed={field.lower():subprocess.check_output(['dpkg-deb','--field',str(source),field],text=True).strip() for field in ('Package','Version','Architecture')}
        if any(observed[field]!=entry[field] for field in ('package','version','architecture')):raise ValueError('package_control_mismatch')
        identity=(entry['package'],entry['version'],entry['architecture'])
        if identity in seen:raise ValueError('duplicate_package_version')
        seen.add(identity)
        filename=entry['package']+'_'+entry['version'].replace(':','%3a')+'_'+entry['architecture']+'.deb'
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._+%~\-]*\.deb',filename):raise ValueError('unsafe_package_filename')
        rows.append((entry,source,filename))
    output.mkdir(parents=True);pool=output/'pool';pool.mkdir()
    for entry,source,filename in rows:
        target=pool/filename;shutil.copyfile(source,target)
        if sha(target)!=entry['sha256']:raise ValueError('package_changed_during_copy')
    scan=subprocess.run(['dpkg-scanpackages','--multiversion','pool','/dev/null'],cwd=output,check=True,capture_output=True)
    (output/'Packages').write_bytes(scan.stdout)
    (output/'Packages.gz').write_bytes(gzip.compress(scan.stdout,mtime=0))
    origin='Makervaerkstedet-Test' if spec['test_repository'] else 'Makervaerkstedet'
    release=subprocess.check_output(['apt-ftparchive','-o','APT::FTPArchive::Release::Origin='+origin,
        '-o','APT::FTPArchive::Release::Label='+origin,'-o','APT::FTPArchive::Release::Architectures=amd64 all','release','.'],cwd=output)
    (output/'Release').write_bytes(b'Acquire-By-Hash: yes\n'+release)
    by_hash=output/'by-hash/SHA256';by_hash.mkdir(parents=True)
    for index_name in ('Packages','Packages.gz'):
        shutil.copyfile(output/index_name,by_hash/sha(output/index_name))
    common=[str(gpg),'--homedir',tool_path(home,gpg),'--batch','--yes','--local-user',key_fingerprint,'--digest-algo','SHA256']
    for filename,action in (('InRelease','--clearsign'),('Release.gpg','--detach-sign')):
        subprocess.run([*common,'--output',tool_path(output/filename,gpg),action,tool_path(output/'Release',gpg)],check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL)
    public=subprocess.check_output([str(gpg),'--homedir',tool_path(home,gpg),'--batch','--export',key_fingerprint])
    if not public:raise ValueError('public_signing_key_export_failed')
    (output/'makervaerkstedet.gpg').write_bytes(public)
    receipt={'schema':1,'test_repository':spec['test_repository'],'signing_fingerprint':key_fingerprint.upper(),'gpg_sha256':gpg_sha256,
             'install_gate_sha256':install_gate_sha256,
             'debian_keyring_sha256':DEBIAN13_KEYRING_SHA256,'producer_sha256':sha(__file__),
             'built_utc':datetime.now(timezone.utc).isoformat(),
             'packages':[{key:entry[key] for key in ('package','version','architecture','sha256','size')} for entry,_,_ in rows],
             'files':{p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*')) if p.is_file()},'package_installation_performed':False}
    return receipt
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--gpg-exe',type=Path,required=True);parser.add_argument('--gpg-sha256',required=True);parser.add_argument('--gnupg-home',type=Path,required=True);parser.add_argument('--key-fingerprint',required=True);parser.add_argument('--receipt',type=Path,required=True);args=parser.parse_args()
    try:
        result=build(json.loads(args.inputs.read_bytes()),args.output,gpg_exe=args.gpg_exe,gpg_sha256=args.gpg_sha256,gnupg_home=args.gnupg_home,key_fingerprint=args.key_fingerprint)
        if args.output==args.receipt or args.output in args.receipt.parents:raise ValueError('private_receipt_must_stay_outside_public_repo')
        args.receipt.write_text(json.dumps(result,indent=2),encoding='utf-8')
    except (ValueError,OSError,subprocess.CalledProcessError) as error:parser.exit(2,str(error)+'\n')
