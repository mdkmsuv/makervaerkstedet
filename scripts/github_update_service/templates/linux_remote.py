"""Prepared queue helper. Template pins are filled only by prepare.py."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import sys
import types

PAYLOAD_SHA='@@PAYLOAD_SHA@@'
PAYLOAD_SIZE=0 # @@PAYLOAD_SIZE@@
INSTALLER_B64='@@INSTALLER_B64@@'

def module():
    source=base64.b64decode(INSTALLER_B64).decode('utf-8')
    target=types.ModuleType('queued_linux_installer')
    exec(compile(source,'<pinned-linux-installer>','exec'),target.__dict__)
    return target,source

def main():
    installer,source=module()
    if os.geteuid()!=0: raise ValueError('root required')
    mode,name,value=sys.argv[1:]
    kiosk,device,_=installer.profile(Path('/'),name)
    if mode=='probe':
        if not re.fullmatch(r'[a-f0-9]{64}',value): raise ValueError('invalid challenge')
        pub=installer.regular(Path('/etc/ssh/ssh_host_ed25519_key.pub')).read_text().split()
        public=' '.join(pub[:2]); token=hashlib.sha256(device['token'].encode('ascii')).digest()
        proof=hmac.new(token,(value+'\n'+name+'\n'+public).encode('ascii'),hashlib.sha256).hexdigest()
        state=Path(installer.STATE); receipt=installer.journal(state)
        if receipt and (receipt.get('computer')!=name or receipt.get('release')!=installer.RELEASE): raise ValueError('journal identity mismatch')
        status=receipt.get('status')
        if status=='staged_awaiting_restart' and not installer.armed(Path('/'),name): status='stage_retry'
        if status=='failed_before_apply': status='profile_missing'
        if status=='complete':
            if (not receipt.get('preservation_verified') or not receipt.get('installed_files')
                    or installer.read_json(state/'pins.json').get('payload_sha256')!=PAYLOAD_SHA
                    or installer.read_json(Path('/etc/elevhub/release.json')).get('release_id')!=installer.RELEASE):
                status='recovery_required'
            for path,expected in receipt.get('installed_files',{}).items():
                target=Path(path)
                actual=installer.tree(target) if target.is_dir() else installer.digest(installer.regular(target))
                if actual!=expected: status='recovery_required'
        approval=installer.approval_state(Path('/'),name)
        result={'hostname':socket.gethostname(),'publickey':public,'proof':proof,
            'installed_release':installer.read_json(Path('/etc/elevhub/release.json')).get('release_id') if Path('/etc/elevhub/release.json').exists() else None,
            'release':installer.RELEASE if status=='complete' else None,'operation':installer.OPERATION,
            'payload_sha256':PAYLOAD_SHA,'package_sha256':installer.PACKAGE_SHA,
            'local_admin_approved':approval is not None,
            'restart_authorized':bool(approval and approval.get('restart_authorized')),
            'update_status':status,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    elif mode=='stage':
        if not installer.STAGE_RE.fullmatch(value): raise ValueError('invalid staging directory')
        stage=installer.regular(Path(value)/'payload.tar.gz')
        result=installer.stage(Path('/'),name,stage,PAYLOAD_SHA,PAYLOAD_SIZE,source)
        # Remove the large account-owned transfer after root has verified/staged it.
        stage.unlink(); Path(value).rmdir()
    else: raise ValueError('unknown mode')
    print(json.dumps(result,separators=(',',':')))

if __name__=='__main__':
    try: main()
    except Exception as error:
        # No arbitrary process output, tokens, config contents or source in logs.
        category='profile_missing' if 'profile_missing' in str(error) else 'remote_update_failed'
        print(category,file=sys.stderr); raise SystemExit(1)
