"""Teacher-authorized bridge to the fixed, independently installed Windows task."""
import json
import ntpath
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import uuid

BASE='/api/admin/server-update'
STATES={'idle','checking','available','current','downloading','verifying','installing','succeeded','failed','interrupted'}
VERSION=re.compile(r'\d+\.\d+\.\d+-\d{8}\.\d+')
HASH=re.compile(r'[a-f0-9]{64}')


def invoke(mode,command_id=None,manifest_sha256=None):
    if sys.platform!='win32':return None
    root=os.environ.get('ProgramFiles',r'C:\Program Files')
    windows=os.environ.get('SystemRoot',r'C:\Windows')
    if not ntpath.isabs(root) or not ntpath.isabs(windows):return None
    helper=ntpath.join(root,'ElevhubServer','server','server-update.ps1')
    powershell=ntpath.join(windows,'System32','WindowsPowerShell','v1.0','powershell.exe')
    if not Path(helper).is_file() or not Path(powershell).is_file():return None
    args=[powershell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',helper,'-Mode',mode]
    if command_id is not None:args+=['-CommandId',command_id]
    if manifest_sha256 is not None:args+=['-ManifestSha256',manifest_sha256]
    try:
        result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8-sig',timeout=8,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdin=subprocess.DEVNULL)
        if result.returncode or len(result.stdout)>65536:return None
        payload=json.loads(result.stdout)
        return payload if isinstance(payload,dict) else None
    except (OSError,subprocess.SubprocessError,ValueError,UnicodeError):return None


def status():
    result=invoke('Status') or {}
    value=result.get('state',{});value=value if isinstance(value,dict) else {}
    installed=value.get('installed_version')
    output={'schema':1,'available':result.get('available') is True,
        'installed_version':installed if isinstance(installed,str) and VERSION.fullmatch(installed) else None,
        'candidate':None,'state':value.get('state') if value.get('state') in STATES else 'idle',
        'command_id':None,'code':None,'downloaded_bytes':0,'total_bytes':0,'updated_at':None,'queues_paused':None}
    candidate=value.get('candidate')
    if (isinstance(candidate,dict) and isinstance(candidate.get('version'),str) and VERSION.fullmatch(candidate['version'])
            and isinstance(candidate.get('manifest_sha256'),str) and HASH.fullmatch(candidate['manifest_sha256'])
            and type(candidate.get('size')) is int and 0<candidate['size']<2**31):
        output['candidate']={k:candidate[k] for k in ('version','manifest_sha256','size')}
    for key in ('downloaded_bytes','total_bytes'):
        if type(value.get(key)) is int and 0<=value[key]<2**31:output[key]=value[key]
    for key,pattern in (('command_id',r'[a-f0-9-]{36}'),('code',r'[a-z_]{1,80}'),('updated_at',r'[0-9T:.+Z-]{1,40}')):
        if isinstance(value.get(key),str) and re.fullmatch(pattern,value[key]):output[key]=value[key]
    if type(value.get('queues_paused')) is bool:output['queues_paused']=value['queues_paused']
    return output


def handle(handler,path,body=None):
    if path not in (BASE,BASE+'/check',BASE+'/install'):return False
    if not handler.logget_ind():handler.svar(401,{'error':'teacher_login_required'});return True
    if handler.command in ('GET','HEAD') and path==BASE:
        handler.svar(200,status());return True
    if handler.command!='POST' or path==BASE:
        handler.svar(405,{'error':'method_not_allowed'});return True
    scheme='https://' if isinstance(handler.connection,ssl.SSLSocket) else 'http://'
    if len(handler.headers.get_all('Host',[]))!=1 or handler.headers.get_all('Origin',[])!=[scheme+handler.headers.get('Host','')]:
        handler.svar(403,{'error':'same_origin_required'});return True
    expected={'command_id'} if path.endswith('/check') else {'command_id','manifest_sha256','confirmation'}
    valid=isinstance(body,dict) and set(body)==expected
    try:valid=valid and isinstance(body['command_id'],str) and str(uuid.UUID(body['command_id']))==body['command_id'] and uuid.UUID(body['command_id']).int!=0
    except (ValueError,KeyError,TypeError,AttributeError):valid=False
    if path.endswith('/install'):
        valid=valid and body.get('confirmation')=='OPDATER SERVER' and isinstance(body.get('manifest_sha256'),str) and HASH.fullmatch(body['manifest_sha256'])
    if not valid:handler.svar(400,{'error':'invalid_server_update_request'});return True
    result=invoke('Check' if path.endswith('/check') else 'Request',body['command_id'],body.get('manifest_sha256'))
    if not result or result.get('accepted') is not True or result.get('command_id')!=body['command_id']:
        handler.svar(503,{'error':'server_update_unavailable'});return True
    handler.svar(202,{'schema':1,'accepted':True,'command_id':body['command_id']})
    return True
