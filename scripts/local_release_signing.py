"""Local publisher signing through CurrentUser-DPAPI and pinned native CLIs.

No key creation, no secret export command, no installation or publication.
The only durable private files are the already-authorized DPAPI blobs outside
OneDrive. Temporary plaintext is restricted to current user/SYSTEM and removed.
"""
from __future__ import annotations
from contextlib import contextmanager
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

class LocalSigningError(ValueError):pass

def _plain(path):
    path=Path(path).absolute()
    for entry in (path,*path.parents):
        if entry.is_symlink() or entry.is_junction():raise LocalSigningError('linked_signing_path')
    return path

def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def _windows():
    if os.name!='nt':raise LocalSigningError('current_user_windows_signer_required')

def _sid():
    executable=Path(os.environ['SystemRoot'])/'System32/whoami.exe'
    raw=subprocess.check_output([str(executable),'/user','/fo','csv','/nh'])
    match=re.search(rb'S-1-5-(?:[0-9]+-)*[0-9]+',raw)
    if not match:raise LocalSigningError('current_user_sid_unavailable')
    return match.group().decode()

def _assert_acl(paths):
    # The command contains paths only, never key bytes/passphrases. Base64 keeps
    # path quoting out of PowerShell syntax, including non-ASCII account names.
    encoded=base64.b64encode(json.dumps([str(path) for path in paths]).encode('utf-8')).decode()
    code="""$paths=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s'))|ConvertFrom-Json;
$me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
foreach($p in $paths){$a=Get-Acl -LiteralPath $p;$s=@($a.Access|ForEach-Object{$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value});
if(-not $a.AreAccessRulesProtected -or $s.Count -ne 2 -or @($s|Where-Object{$_ -notin @($me,'S-1-5-18')}).Count -or $a.Owner -ne [Security.Principal.WindowsIdentity]::GetCurrent().Name){exit 9}}""" % encoded
    shell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    environment=dict(os.environ);environment['PSModulePath']=str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/Modules')
    result=subprocess.run([str(shell),'-NoLogo','-NoProfile','-NonInteractive','-Command',code],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise LocalSigningError('signing_acl_or_owner_mismatch')

def _root():
    _windows();root=_plain(Path.home()/'.makervaerkstedet/release-signing')
    paths=[root,root/'public-provenance.json',root/'publisher-minisign.secret.dpapi',root/'publisher-apt.secret.dpapi']
    if not all(path.exists() for path in paths):raise LocalSigningError('existing_signing_material_required')
    _assert_acl(paths)
    return root

@contextmanager
def _private_temporary():
    _windows();base=_plain(Path(os.environ['LOCALAPPDATA'])/'Temp')
    if 'onedrive' in str(base).casefold():raise LocalSigningError('private_temp_outside_sync_required')
    temporary=Path(tempfile.mkdtemp(prefix='mkv-signer-',dir=base));sid=_sid()
    tool=Path(os.environ['SystemRoot'])/'System32/icacls.exe'
    try:
        commands=[['/inheritance:r'],['/setowner','*'+sid],['/remove:g','*S-1-5-32-544'],['/grant:r','*'+sid+':(OI)(CI)F','*S-1-5-18:(OI)(CI)F']]
        for arguments in commands:
            result=subprocess.run([str(tool),str(temporary),*arguments],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:raise LocalSigningError('private_temp_acl_failed')
        _assert_acl([temporary])
        yield temporary
    finally:
        if temporary.resolve().parent!=base.resolve() or not temporary.name.startswith('mkv-signer-'):raise LocalSigningError('unsafe_private_cleanup_target')
        shutil.rmtree(temporary)

def _unprotect(path):
    _windows();data=_plain(path).read_bytes()
    if not data or len(data)>16*1024*1024:raise LocalSigningError('invalid_private_blob')
    class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    crypt.CryptUnprotectData.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    buffer=(ctypes.c_ubyte*len(data)).from_buffer_copy(data);source=Blob(len(data),buffer);output=Blob()
    if not crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):raise LocalSigningError('dpapi_current_user_decryption_failed')
    try:return ctypes.string_at(output.data,output.size)
    finally:kernel.LocalFree(output.data)

def _tool(path,metadata,field):
    path=_plain(path)
    if not path.is_file() or _sha(path)!=metadata[field]:raise LocalSigningError('signer_tool_pin_mismatch')
    return path

def sign_minisign(path,*,minisign_exe,signature=None):
    root=_root();metadata=json.loads((root/'public-provenance.json').read_bytes());tool=_tool(minisign_exe,metadata,'minisign_tool_sha256')
    path=_plain(path);signature=_plain(signature or path.with_name(path.name+'.minisig'))
    if signature.exists():raise LocalSigningError('signature_exists_refusing_overwrite')
    before=_sha(path)
    with _private_temporary() as temporary:
        key=temporary/'publisher.key';key.write_bytes(_unprotect(root/'publisher-minisign.secret.dpapi'))
        for arguments in ([str(tool),'-S','-s',str(key),'-m',str(path),'-x',str(signature)],
                          [str(tool),'-V','-m',str(path),'-x',str(signature),'-p',str(root/'minisign.pub')]):
            result=subprocess.run(arguments,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:raise LocalSigningError('native_minisign_sign_or_verify_failed')
    if _sha(path)!=before:raise LocalSigningError('signed_input_changed')
    return {'sha256':before,'signature_sha256':_sha(signature),'signature':str(signature),'public_key_sha256':metadata['minisign_public_file_sha256']}

def msys_path(path):
    path=Path(path).absolute()
    if not path.drive or len(path.drive)!=2:raise LocalSigningError('drive_qualified_gpg_path_required')
    return '/'+path.drive[0].lower()+path.as_posix()[2:]

@contextmanager
def apt_signing_home(*,gpg_exe):
    root=_root();metadata=json.loads((root/'public-provenance.json').read_bytes());tool=_tool(gpg_exe,metadata,'gpg_tool_sha256')
    with _private_temporary() as temporary:
        home=temporary/'g';home.mkdir()
        try:
            result=subprocess.run([str(tool),'--no-options','--homedir',msys_path(home),'--batch','--import'],input=_unprotect(root/'publisher-apt.secret.dpapi'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:raise LocalSigningError('native_gpg_import_failed')
            yield home,metadata['apt_fingerprint'],metadata['gpg_tool_sha256']
        finally:
            subprocess.run([str(tool.with_name('gpgconf.exe')),'--homedir',msys_path(home),'--kill','all'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
