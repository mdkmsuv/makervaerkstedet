"""Select program sources on an unchanged, firewall-bound embedded runtime.

This file is also the standalone child launcher. Keep it stdlib-only: importing
the installed ``app`` here would cache old modules before selecting a release.
It does not change an active pointer, service ownership, data or restore holds.
"""
import argparse
from contextlib import ExitStack
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import stat
import sys
import threading

MANIFEST = 'makerspace-release.json'
CREDIT = 'Kreditering: Klostermarksskolen.'
LAUNCHER = 'app/generation_runtime.py'
START_FILES = (LAUNCHER, 'app/generation_selection.py', 'app/generation_lock.py')
ENTRIES = {
    'server': ('script', 'server/bootstrap.py'),
    'print': ('module', 'app.background'),
    'print-server': ('module', 'app.server'),
    'design': ('module', 'app.laser_server'),
    'boxes-worker': ('module', 'app.boxes_worker'),
    'hub': ('script', 'hub/elevhub.py'),
    'enrollment': ('script', 'deployment/school-setup/enrollment.py'),
    'school-admin': ('module', 'app.school_admin'),
    'laser-station': ('module', 'app.laser_station'),
}
_selection = None
_active_selection = None
_integrity_lease = None
MAX_FILES = 200_000
MAX_MANIFEST = 64 * 1024 * 1024


def _plain(path, directory=None):
    path = Path(path).absolute()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Program og runtime skal ligge på lokale drev.')
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Program- og runtimestier må ikke indeholde links.')
    if directory is True and not path.is_dir() or directory is False and not path.is_file():
        raise ValueError('Program- eller runtimefil mangler.')
    return path.resolve(strict=True)


def _relative(name):
    if (not isinstance(name, str) or not name or any(c in name for c in '\\:*?"<>|')
            or any(not c.isprintable() for c in name)):
        raise ValueError('Ugyldig programsti.')
    for part in name.split('/'):
        if (part in ('', '.', '..') or part.endswith((' ', '.'))
                or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL'}
                or re.fullmatch(r'(COM|LPT)[1-9]', part.split('.')[0].upper())):
            raise ValueError('Ugyldig programsti.')
    return name


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Dubleret manifestnøgle.')
        value[key] = item
    return value


def _inventory(root):
    """No following links; include empty directories in runtime equality."""
    files, directories, folded = {}, set(), set()
    pending = [root]
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            child = _plain(child)
            name = _relative(child.relative_to(root).as_posix())
            if name.casefold() in folded:
                raise ValueError('Programstier må ikke kun afvige i store/små bogstaver.')
            folded.add(name.casefold())
            if len(folded) > MAX_FILES:
                raise ValueError('For mange programfiler og mapper.')
            if child.is_dir():
                directories.add(name)
                pending.append(child)
            elif child.is_file():
                info = child.stat()
                files[name] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            else:
                raise ValueError('Programmet indeholder en fil af ukendt type.')
    return files, directories


def _metadata(path):
    info = _plain(path, False).stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _integrity_snapshot(package, stable):
    """Cheap but complete namespace and identity check; never follows reparse points."""
    return (_inventory(_plain(package, True)),
            _inventory(_plain(stable / 'runtime', True)),
            tuple(_metadata(stable / name) for name in START_FILES))


def _wire_snapshot(snapshot):
    package, runtime, helpers = snapshot
    return {'package_files': package[0], 'package_directories': sorted(package[1]),
            'runtime_files': runtime[0], 'runtime_directories': sorted(runtime[1]),
            'helpers': helpers}


def _process_birth(pid):
    """Windows FILETIME creation value also prevents trusting a reused PID."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), 'Procesidentitet kunne ikke læses.')
    try:
        values = [(wintypes.DWORD * 2)() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in values)):
            raise OSError(ctypes.get_last_error(), 'Procesidentitet kunne ikke læses.')
        return values[0][0] | values[0][1] << 32
    finally:
        kernel.CloseHandle(handle)


def _windows_argv(command):
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        raise OSError(ctypes.get_last_error(), 'Proceskommandoen kunne ikke læses.')
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(pointer)


def _native_powershell():
    """Use the actual Windows directories, never caller-supplied SystemRoot."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    def directory(name):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        function.restype = wintypes.UINT
        size = 260
        while size <= 32768:
            buffer = ctypes.create_unicode_buffer(size)
            length = function(buffer, size)
            if length == 0:
                raise OSError(ctypes.get_last_error(), 'Windows-systemmappen kunne ikke læses.')
            if length < size:
                return Path(buffer.value)
            size = length + 1
        raise OSError('Windows-systemmappen har en ugyldig længde.')

    system = directory('GetSystemDirectoryW')
    windows = directory('GetWindowsDirectoryW')
    return system / 'WindowsPowerShell/v1.0/powershell.exe', windows


def _native_process_identity(pid):
    """Read a process token SID and image directly from Windows, not PowerShell."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                   wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [('sid', ctypes.c_void_p), ('attributes', wintypes.DWORD)]

    process = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        raise OSError(ctypes.get_last_error(), 'Procesidentitet kunne ikke læses.')
    token = wintypes.HANDLE()
    try:
        image = ctypes.create_unicode_buffer(32768)
        image_size = wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(image_size)):
            raise OSError(ctypes.get_last_error(), 'Procesbillede kunne ikke læses.')
        if not advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            raise OSError(ctypes.get_last_error(), 'Procestoken kunne ikke læses.')
        required = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))  # TokenUser
        if not required.value or required.value > 65536:
            raise OSError('Procestoken har en ugyldig længde.')
        data = ctypes.create_string_buffer(required.value)
        if not advapi.GetTokenInformation(token, 1, data, required,
                                          ctypes.byref(required)):
            raise OSError(ctypes.get_last_error(), 'Bruger-SID kunne ikke læses.')
        user = ctypes.cast(data, ctypes.POINTER(SidAndAttributes)).contents
        sid_pointer = ctypes.c_void_p()
        if not advapi.ConvertSidToStringSidW(user.sid, ctypes.byref(sid_pointer)):
            raise OSError(ctypes.get_last_error(), 'Bruger-SID kunne ikke konverteres.')
        try:
            sid = ctypes.wstring_at(sid_pointer.value)
        finally:
            kernel.LocalFree(sid_pointer)
        return sid, _plain(Path(image.value), False)
    finally:
        if token.value:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def _native_powershell_environment(executable, windows):
    # Windows variable names are case-insensitive. Remove every caller spelling
    # before setting the three values used by PowerShell/module discovery.
    names = {'systemroot', 'windir', 'psmodulepath'}
    environment = {key: value for key, value in os.environ.items()
                   if key.casefold() not in names
                   and not key.casefold().startswith(('cor_', 'coreclr_', 'dotnet_', 'complus_'))}
    environment.update(SystemRoot=str(windows), windir=str(windows),
                       PSModulePath=str(executable.parent / 'Modules'))
    return environment


def _trusted_server_process(parent_pid, package, stable, manifest_sha256, active,
                            selection_sha256=None, installation_id=None):
    """Only the installed LocalService launcher can issue reusable proof."""
    import subprocess
    script = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$PSModuleAutoLoadingPreference='None'
$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules')
Microsoft.PowerShell.Core\Import-Module -Name ([IO.Path]::Combine($PSHOME,'Modules','Microsoft.PowerShell.Utility','Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
Microsoft.PowerShell.Core\Import-Module -Name ([IO.Path]::Combine($PSHOME,'Modules','CimCmdlets','CimCmdlets.psd1')) -ErrorAction Stop
$ids=[Console]::In.ReadToEnd() | Microsoft.PowerShell.Utility\ConvertFrom-Json
@(foreach($item in $ids){
  $id=[int]$item
  $process=CimCmdlets\Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$id"
  if(@($process).Count -ne 1){throw 'Process missing'}
  $owner=CimCmdlets\Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid
  if($owner.ReturnValue -ne 0){throw 'Process owner missing'}
  [ordered]@{pid=$id;sid=[string]$owner.Sid;executable=[string]$process.ExecutablePath;
    command=[string]$process.CommandLine}
}) | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 4 -Compress
'''
    try:
        expected_executable = _plain(stable / 'runtime/python.exe', False)
        if (any(_native_process_identity(pid) != ('S-1-5-19', expected_executable)
                for pid in (parent_pid, os.getpid()))):
            return False
        executable, windows = _native_powershell()
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        environment = _native_powershell_environment(executable, windows)
        result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                input=json.dumps([parent_pid, os.getpid()]), capture_output=True, text=True,
                                timeout=12, env=environment)
        if result.returncode:
            return False
        records = json.loads(result.stdout)
        if (type(records) is not list or len(records) != 2
                or any(type(item) is not dict or set(item) != {'pid', 'sid', 'executable', 'command'}
                       for item in records)):
            return False
        parent, child = records
        if (parent['pid'] != parent_pid or child['pid'] != os.getpid()
                or parent['sid'] != 'S-1-5-19' or child['sid'] != 'S-1-5-19'
                or _plain(parent['executable'], False) != expected_executable
                or _plain(child['executable'], False) != expected_executable):
            return False
        command = _windows_argv(parent['command'])
        if (len(command) < 10 or _plain(command[0], False) != expected_executable
                or command[1:3] != ['-B', '-I']
                or _plain(command[3], False) != _plain(stable / LAUNCHER, False)
                or '--' not in command):
            return False
        options = command[4:command.index('--')]
        required_keys = {'--runtime-root', '--entry'}
        allowed_keys = set(required_keys)
        if active:
            required_keys.add('--active')
            allowed_keys |= {'--active', '--expected-installation-id', '--expected-selection'}
        else:
            required_keys |= {'--package-root', '--manifest-sha256'}
            allowed_keys |= {'--package-root', '--manifest-sha256'}
        keys = [item for item in options if item.startswith('--')]
        if not required_keys <= set(keys) or not set(keys) <= allowed_keys or len(keys) != len(set(keys)):
            return False
        values = {}
        index = 0
        while index < len(options):
            key = options[index]
            if key == '--active':
                values[key] = True
                index += 1
            else:
                if index + 1 >= len(options) or options[index + 1].startswith('--'):
                    return False
                values[key] = options[index + 1]
                index += 2
        if (_plain(values['--runtime-root'], True) != _plain(stable, True)
                or values['--entry'] != 'server'):
            return False
        if not active and (_plain(values['--package-root'], True) != _plain(package, True)
                           or values['--manifest-sha256'] != manifest_sha256):
            return False
        if active and (not isinstance(selection_sha256, str)
                       or not re.fullmatch(r'[a-f0-9]{64}', selection_sha256)
                       or not isinstance(installation_id, str)
                       or not re.fullmatch(r'[a-f0-9]{32}', installation_id)
                       or (values.get('--expected-selection') is not None
                           and values['--expected-selection'] != selection_sha256)
                       or (values.get('--expected-installation-id') is not None
                           and values['--expected-installation-id'] != installation_id)):
            return False
        return True
    except Exception:
        return False


def _namespace_acl_is_private(package, stable, snapshot):
    """Fast path only when every directory forbids unprivileged namespace edits.

    A directory sharing lease does not stop FILE_ADD_FILE on Windows. The
    installed code tree therefore needs a simple, privileged-writer DACL on
    each directory, including ancestors that can replace a whole subtree.
    Unknown descriptors or a failed ACL read cause ordinary full verification.
    """
    import subprocess
    roots = [package, stable / 'runtime', stable / 'app']
    roots += [package / name for name in snapshot[0][1]]
    roots += [stable / 'runtime' / name for name in snapshot[1][1]]
    paths = set()
    for root in roots:
        paths.add(str(root))
        for parent in root.parents:
            if parent.anchor == str(parent):
                break
            paths.add(str(parent))
    script = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$PSModuleAutoLoadingPreference='None'
$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules')
$modules=([IO.Path]::Combine($PSHOME,'Modules'))
Microsoft.PowerShell.Core\Import-Module -Name ([IO.Path]::Combine($modules,'Microsoft.PowerShell.Utility','Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
Microsoft.PowerShell.Core\Import-Module -Name ([IO.Path]::Combine($modules,'Microsoft.PowerShell.Management','Microsoft.PowerShell.Management.psd1')) -ErrorAction Stop
Microsoft.PowerShell.Core\Import-Module -Name ([IO.Path]::Combine($modules,'Microsoft.PowerShell.Security','Microsoft.PowerShell.Security.psd1')) -ErrorAction Stop
$paths=[Console]::In.ReadToEnd() | Microsoft.PowerShell.Utility\ConvertFrom-Json
$result=@(foreach($path in $paths){
  $item=Microsoft.PowerShell.Management\Get-Item -LiteralPath $path -Force
  $acl=Microsoft.PowerShell.Security\Get-Acl -LiteralPath $path
  $raw=[Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(),0)
  $bad=$false
  foreach($ace in $raw.DiscretionaryAcl){
    if($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback -or
       [string]$ace.AceQualifier -ne 'AccessAllowed'){ $bad=$true }
  }
  [ordered]@{path=[string]$item.FullName;reparse=(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0);
    owner=[string]$raw.Owner.Value;dacl=($null -ne $raw.DiscretionaryAcl);bad=$bad;
    rules=@(foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
      [ordered]@{sid=[string]$rule.IdentityReference.Value;mask=[int]$rule.FileSystemRights;
        type=[string]$rule.AccessControlType;
        inherit_only=(($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0)}
    })}
})
$result | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 8 -Compress
'''
    try:
        if _native_process_identity(os.getpid()) != (
                'S-1-5-19', _plain(stable / 'runtime/python.exe', False)):
            return False
        executable, windows = _native_powershell()
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        environment = _native_powershell_environment(executable, windows)
        result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                input=json.dumps(sorted(paths)), capture_output=True, text=True,
                                timeout=60, env=environment)
        if result.returncode:
            return False
        records = json.loads(result.stdout)
        if type(records) is not list or len(records) != len(paths):
            return False
        privileged = {'S-1-5-18', 'S-1-5-32-544',
                      'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'}
        seen = set()
        for item in records:
            if (type(item) is not dict or set(item) != {'path', 'reparse', 'owner', 'dacl', 'bad', 'rules'}
                    or item['reparse'] is not False or item['dacl'] is not True or item['bad'] is not False
                    or item['owner'] not in privileged or type(item['rules']) is not list):
                return False
            seen.add(os.path.normcase(os.path.normpath(item['path'])))
            for rule in item['rules']:
                if (type(rule) is not dict or set(rule) != {'sid', 'mask', 'type', 'inherit_only'}
                        or rule['type'] != 'Allow' or type(rule['mask']) is not int
                        or type(rule['inherit_only']) is not bool):
                    return False
                mask = rule['mask'] & 0xffffffff
                creator_owner_template = (rule['sid'] == 'S-1-3-0' and rule['inherit_only'])
                if (mask & (0xD0156 | 0x40 | 0x40000000 | 0x10000000)
                        and rule['sid'] not in privileged and not creator_owner_template):
                    return False
        return seen == {os.path.normcase(os.path.normpath(path)) for path in paths}
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return False


def _private_roots(package, stable):
    # Cheap first gate: a writable ancestor can never qualify for the fast
    # path, so do not open thousands of handles in development/QA layouts.
    try:
        return _namespace_acl_is_private(package, stable,
                                         (({}, set()), ({}, set()), ()))
    except Exception:
        return False


class IntegrityLease:
    """Hold Windows share-read handles across validation and child startup.

    No persisted cache exists. A failed handle or namespace check disables the
    fast path and all children fall back to their ordinary full validation.
    """
    def __init__(self, package, stable):
        self.package = _plain(package, True)
        self.stable = _plain(stable, True)
        self.handles = []
        self.snapshot = None
        self.listener = None
        self.address = None
        self.authkey = None
        self.live = False

    def __enter__(self):
        if os.name != 'nt':
            return self
        try:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self._close_handle = kernel.CloseHandle
            self.snapshot = _integrity_snapshot(self.package, self.stable)
            package_files, package_dirs = self.snapshot[0]
            runtime_files, runtime_dirs = self.snapshot[1]
            paths = ([(self.package, True)]
                     + [(self.package / name, True) for name in package_dirs]
                     + [(self.package / name, False) for name in package_files]
                     + [(self.stable / 'runtime', True)]
                     + [(self.stable / 'runtime' / name, True) for name in runtime_dirs]
                     + [(self.stable / 'runtime' / name, False) for name in runtime_files]
                     + [(self.stable / name, False) for name in START_FILES])
            for path, directory in paths:
                # Both snapshots walk every component with lstat. Opening the
                # known inventory directly avoids tens of thousands of extra
                # ancestor stats while FILE_FLAG_OPEN_REPARSE_POINT prevents
                # this handle from following a swapped link.
                flags = 0x02000000 if directory else 0
                handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, flags | 0x00200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise OSError(ctypes.get_last_error(), 'Integritetslease kunne ikke låse en fil.')
                self.handles.append(handle)
            if self.snapshot != _integrity_snapshot(self.package, self.stable):
                raise ValueError('Programmet ændredes under etablering af integritetslease.')
            self.live = True
        except Exception:
            self.close()
        return self

    def assert_unchanged(self):
        if not self.live or self.snapshot != _integrity_snapshot(self.package, self.stable):
            raise ValueError('Programmet ændredes efter integritetskontrollen.')

    def start_proofs(self, manifest_sha256, selection_sha256=None):
        if not self.live:
            return
        from multiprocessing.connection import Listener
        if not _namespace_acl_is_private(self.package, self.stable, self.snapshot):
            return
        self.assert_unchanged()
        self.address = r'\\.\pipe\makerspace-integrity-' + secrets.token_hex(16)
        self.authkey = secrets.token_bytes(32)
        self.listener = Listener(self.address, family='AF_PIPE', authkey=self.authkey)
        pid, birth = os.getpid(), _process_birth(os.getpid())
        os.environ['MAKERSPACE_INTEGRITY_PROOF'] = json.dumps({
            'pipe': self.address, 'key': base64.b64encode(self.authkey).decode('ascii'),
            'pid': pid, 'birth': birth, 'manifest': manifest_sha256,
            'selection': selection_sha256}, separators=(',', ':'))

        def serve():
            # The server starts at most three fixed children. Bound attempts
            # prevent a local caller from turning this into an open oracle.
            issued = set()
            for _attempt in range(6):
                if not self.live:
                    break
                try:
                    with self.listener.accept() as connection:
                        import ctypes
                        from ctypes import wintypes
                        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                        kernel.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                        kernel.GetNamedPipeClientProcessId.restype = wintypes.BOOL
                        client_pid = wintypes.DWORD()
                        if not kernel.GetNamedPipeClientProcessId(connection.fileno(), ctypes.byref(client_pid)):
                            continue
                        if not connection.poll(2):
                            continue
                        request = json.loads(connection.recv_bytes(1024).decode('utf-8'))
                        if (type(request) is dict and set(request) == {'entry', 'pid'}
                                and request['entry'] in ('print', 'design', 'hub')
                                and request['entry'] not in issued
                                and type(request['pid']) is int and request['pid'] == client_pid.value):
                            self.assert_unchanged()
                            issued.add(request['entry'])
                            proof = {'entry': request['entry'], 'parent': pid,
                                     'birth': birth, 'manifest': manifest_sha256,
                                     'selection': selection_sha256,
                                     'snapshot': _wire_snapshot(self.snapshot)}
                            connection.send_bytes(json.dumps(proof, separators=(',', ':')).encode('utf-8'))
                except Exception:
                    if not self.live:
                        break
        threading.Thread(target=serve, name='generation-integrity-proof', daemon=True).start()

    def close(self):
        self.live = False
        os.environ.pop('MAKERSPACE_INTEGRITY_PROOF', None)
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        for handle in reversed(self.handles):
            self._close_handle(handle)
        self.handles.clear()

    def __exit__(self, *_args):
        self.close()


def _child_proof_exchange(package, stable, manifest_sha256, entry, selection_sha256,
                          active, installation_id):
    """One attempt. The caller bounds the entire pipe authentication exchange."""
    if os.name != 'nt' or entry not in ('print', 'design', 'hub'):
        return False
    try:
        from multiprocessing.connection import Client
        value = json.loads(os.environ.get('MAKERSPACE_INTEGRITY_PROOF', 'null'))
        if (type(value) is not dict or set(value) != {'pipe', 'key', 'pid', 'birth', 'manifest', 'selection'}
                or value['manifest'] != manifest_sha256 or value['selection'] != selection_sha256
                or type(value['pid']) is not int or os.getppid() != value['pid']
                or _process_birth(value['pid']) != value['birth']):
            return False
        if not _trusted_server_process(value['pid'], package, stable, manifest_sha256, active,
                                       selection_sha256, installation_id):
            return False
        with Client(value['pipe'], family='AF_PIPE',
                    authkey=base64.b64decode(value['key'], validate=True)) as connection:
            # Win32 reports the actual pipe server, independent of the value
            # supplied in the child's environment.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetNamedPipeServerProcessId.restype = wintypes.BOOL
            server_pid = wintypes.DWORD()
            if (not kernel.GetNamedPipeServerProcessId(connection.fileno(), ctypes.byref(server_pid))
                    or server_pid.value != value['pid']):
                return False
            connection.send_bytes(json.dumps({'entry': entry, 'pid': os.getpid()}).encode('utf-8'))
            if not connection.poll(3):
                return False
            proof = json.loads(connection.recv_bytes(16 * 1024 * 1024).decode('utf-8'))
        if (type(proof) is not dict or set(proof) != {'entry', 'parent', 'birth', 'manifest', 'selection', 'snapshot'}
                or any(proof[key] != expected for key, expected in (
                    ('entry', entry), ('parent', value['pid']), ('birth', value['birth']),
                    ('manifest', manifest_sha256), ('selection', selection_sha256)))
                or _process_birth(value['pid']) != value['birth']):
            return False
        return proof['snapshot'] == json.loads(json.dumps(_wire_snapshot(
            _integrity_snapshot(package, stable))))
    except Exception:
        return False


def _child_proof(package, stable, manifest_sha256, entry, selection_sha256,
                 active=False, installation_id=None, *, timeout=30):
    """Optional bounded proof; a stalled pipe falls back to full verification."""
    result = [False]

    def attempt():
        result[0] = _child_proof_exchange(package, stable, manifest_sha256,
                                          entry, selection_sha256, active, installation_id)

    worker = threading.Thread(target=attempt, name='generation-proof-client', daemon=True)
    worker.start()
    worker.join(timeout)
    return result[0] if not worker.is_alive() else False


def _try_parent_proof(integrity, package, stable, manifest_sha256, entry,
                      selection_sha256, active, installation_id):
    try:
        return (integrity is not None and integrity.live
                and _namespace_acl_is_private(integrity.package, integrity.stable, integrity.snapshot)
                and _child_proof(package, stable, manifest_sha256, entry,
                                 selection_sha256, active, installation_id))
    except Exception:
        return False


def server_integrity_lease(package_root, runtime_root, manifest_sha256):
    """Return only this launcher's live, byte-verified and locked server lease."""
    try:
        main = sys.modules.get('_makerspace_verified_launcher')
        integrity = getattr(main, '_integrity_lease', None)
        if (main is None or integrity is None
                or not getattr(integrity, 'live', False) or getattr(integrity, 'listener', None) is None
                or _plain(getattr(main, '__file__', ''), False) != _plain(Path(runtime_root) / LAUNCHER, False)
                or integrity.package != _plain(package_root, True)
                or integrity.stable != _plain(runtime_root, True)
                or getattr(main, '_verified_manifest', None) != manifest_sha256
                or not _trusted_server_process(os.getpid(), _plain(package_root, True),
                                               _plain(runtime_root, True), manifest_sha256,
                                               _active_selection is not None,
                                               _active_selection[1] if _active_selection is not None else None,
                                               _active_selection[0]['installation_id']
                                               if _active_selection is not None else None)):
            return False
        integrity.assert_unchanged()
        return True
    except (OSError, ValueError, AttributeError):
        return False


def _pth(runtime):
    tag = f'python{sys.version_info.major}{sys.version_info.minor}'
    expected = [tag + '.zip', '.', '..', 'Lib/site-packages']
    names = {p.name for p in runtime.glob('*._pth')}
    if names != {tag + '._pth'}:
        raise ValueError('Runtime skal have den forventede embedded Python-stikonfiguration.')
    lines = [line.strip() for line in (runtime / (tag + '._pth')).read_text(encoding='utf-8-sig').splitlines()
             if line.strip() and not line.strip().startswith('#')]
    if lines != expected:
        raise ValueError('Runtime har ikke den understøttede isolerede Python-søgesti.')
    _plain(runtime / (tag + '.zip'), False)
    _plain(runtime / 'Lib/site-packages', True)
    return [str(runtime / (tag + '.zip')), str(runtime), str(runtime / 'Lib/site-packages')]


def _validate_entry_manifest(package, manifest_sha256, entry):
    """Rebind a parent's complete proof to this child's requested role."""
    path = _plain(package / MANIFEST, False)
    if path.stat().st_size > MAX_MANIFEST:
        raise ValueError('Programmanifestet er for stort.')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise ValueError('Den valgte programgeneration matcher ikke manifestets identitet.')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
    kind, target = ENTRIES[entry]
    role = 'print' if entry == 'print' else 'laser' if entry == 'design' else 'hub'
    source = target.replace('.', '/') + '.py' if kind == 'module' else target
    if (type(value) is not dict or type(value.get('files')) is not dict
            or type(value.get('data_contract')) is not dict
            or role not in value['data_contract'] or source not in value['files']
            or 'app/__init__.py' not in value['files']):
        raise ValueError('Programgenerationen indeholder ikke den ønskede rolle og indgang.')


def validate_generation(package_root, runtime_root, manifest_sha256, entry=None):
    """Read-only validation; checksums bind a selection, not a publisher."""
    package = _plain(package_root, True)
    stable = _plain(runtime_root, True)
    if not isinstance(manifest_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', manifest_sha256):
        raise ValueError('Angiv det kontrollerede udgivelsesmanifests SHA-256.')
    path = _plain(package / MANIFEST, False)
    if path.stat().st_size > MAX_MANIFEST:
        raise ValueError('Programmanifestet er for stort.')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise ValueError('Den valgte programgeneration matcher ikke manifestets identitet.')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
    if (not isinstance(value, dict)
            or set(value) != {'format', 'product', 'release', 'credits', 'data_contract', 'files'}
            or type(value['format']) is not int or value['format'] != 1
            or value['product'] != 'Makerværkstedet' or value['credits'] != CREDIT
            or not isinstance(value['release'], str)
            or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}', value['release'])
            or not isinstance(value['files'], dict) or not 1 <= len(value['files']) <= MAX_FILES):
        raise ValueError('Ukendt programmanifest.')
    contract = value['data_contract']
    if (not isinstance(contract, dict) or 'hub' not in contract
            or set(contract) - {'hub', 'print', 'laser'}
            or any(type(v) is not int or v != 1 for v in contract.values())):
        raise ValueError('Ukendt datakontrakt.')
    if entry is not None:
        if entry not in ENTRIES:
            raise ValueError('Ukendt programindgang.')
        role = ('print' if entry in ('print', 'print-server') else
                'laser' if entry in ('design', 'laser-station', 'boxes-worker') else 'hub')
        kind, target = ENTRIES[entry]
        source = target.replace('.', '/') + '.py' if kind == 'module' else target
        if role not in contract or source not in value['files'] or 'app/__init__.py' not in value['files']:
            raise ValueError('Programgenerationen indeholder ikke den ønskede rolle og indgang.')
    before = _inventory(package)
    for name in (*before[0], *before[1]):
        if (name.split('/')[0] in ('app', 'hub', 'server') or name.startswith('deployment/school-setup/')) and (
                '__pycache__' in name.split('/') or name.endswith(('.pyc', '.pyo'))):
            raise ValueError('Programgenerationen må ikke indeholde gammel Python-bytecode.')
    if set(before[0]) != set(value['files']) | {MANIFEST} or MANIFEST in value['files']:
        raise ValueError('Programfilerne svarer ikke til manifestet.')
    for name, record in value['files'].items():
        _relative(name)
        if (not isinstance(record, dict) or set(record) != {'sha256', 'size'}
                or type(record['size']) is not int or not 0 <= record['size'] <= 2 * 1024 ** 3
                or not isinstance(record['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', record['sha256'])
                or before[0][name][2] != record['size'] or _hash(package / name) != record['sha256']):
            raise ValueError('En programfil matcher ikke manifestet: ' + name)
    if 'CREDITS.md' not in value['files'] or CREDIT not in (package / 'CREDITS.md').read_text(encoding='utf-8'):
        raise ValueError('Krediteringen mangler.')
    runtime = _plain(stable / 'runtime', True)
    selected_runtime = _plain(package / 'runtime', True)
    _plain(runtime / 'python.exe', False)
    _pth(runtime)
    _pth(selected_runtime)
    actual, directories = _inventory(runtime)
    selected, selected_directories = _inventory(selected_runtime)
    if set(actual) != set(selected) or directories != selected_directories:
        raise ValueError('Programgenerationen kræver en anden runtime.')
    for name in actual:
        if name.split('/')[0].casefold() in ('app', 'app.py', 'app.pyc', 'app.pyd', 'hub', 'server'):
            raise ValueError('Runtime må ikke indeholde en konkurrerende programpakke.')
        record = value['files']['runtime/' + name]
        if actual[name][2] != record['size'] or _hash(runtime / name) != record['sha256']:
            raise ValueError('Programgenerationen kræver en anden runtime: ' + name)
    # The launcher is a stable ABI. Updating it needs a separate installer
    # transaction rather than silently loading a different dispatcher in app.
    for name in START_FILES:
        if name not in value['files'] or _hash(_plain(stable / name, False)) != value['files'][name]['sha256']:
            raise ValueError('Programgenerationen kræver en anden startmekanisme.')
    if _inventory(package) != before or _inventory(runtime) != (actual, directories) or _hash(path) != manifest_sha256:
        raise ValueError('Programmet ændredes under kontrollen.')
    if any(_hash(_plain(stable / name, False)) != value['files'][name]['sha256'] for name in START_FILES):
        raise ValueError('Startmekanismen ændredes under kontrollen.')
    return {'release': value['release'], 'manifest_sha256': manifest_sha256}


def clean_environment(environment=None):
    result = {key: value for key, value in (os.environ if environment is None else environment).items()
              if not key.upper().startswith('PYTHON')}
    result['PYTHONDONTWRITEBYTECODE'] = '1'
    return result


def build_command(package_root, runtime_root, manifest_sha256, entry, arguments):
    if entry not in ENTRIES:
        raise ValueError('Ukendt programindgang.')
    stable, package = Path(runtime_root).absolute(), Path(package_root).absolute()
    if _active_selection is not None:
        record, selected_hash = _active_selection
        if (package.resolve(strict=True), stable.resolve(strict=True), manifest_sha256) != _selection:
            raise ValueError('En underproces kan ikke skifte programgeneration.')
        from .generation_selection import bind_arguments
        return build_active_command(stable, entry, bind_arguments(record, entry, arguments), selected_hash)
    return [str(stable / 'runtime/python.exe'), '-B', '-I', str(stable / LAUNCHER),
            '--package-root', str(package), '--runtime-root', str(stable),
            '--manifest-sha256', manifest_sha256, '--entry', entry, '--', *map(str, arguments)]


def build_active_command(runtime_root, entry, arguments, expected_selection=None):
    if entry not in ENTRIES:
        raise ValueError('Ukendt programindgang.')
    stable = Path(runtime_root).absolute()
    command = [str(stable / 'runtime/python.exe'), '-B', '-I', str(stable / LAUNCHER),
               '--runtime-root', str(stable), '--active', '--entry', entry]
    if expected_selection is not None:
        command.extend(['--expected-selection', expected_selection])
    return command + ['--', *map(str, arguments)]


def bind_generation(package_root, runtime_root, manifest_sha256):
    """Bind the already verified selection inside the freshly imported app.

    This is process-local, not an environment variable accepted by old installs.
    Each Python child verifies the selection anew through the standalone entry.
    """
    global _selection
    value = (Path(package_root).resolve(strict=True), Path(runtime_root).resolve(strict=True), manifest_sha256)
    if value[0] != Path(__file__).resolve(strict=True).parents[1]:
        raise ValueError('Startmekanismen er importeret fra en anden programgeneration.')
    if _selection is not None and _selection != value:
        raise ValueError('En kørende proces kan ikke skifte programgeneration.')
    _selection = value


def bind_active_selection(record, selected_hash):
    global _active_selection
    assert_generation(record['program_root'], record['runtime_root'], record['manifest_sha256'])
    value = (dict(record), selected_hash)
    if _active_selection is not None and _active_selection != value:
        raise ValueError('En kørende proces kan ikke skifte datageneration.')
    _active_selection = value


def selected_data_root(program_root):
    """Also bind auxiliary storage (including retained legacy storage) to the same data root."""
    if _active_selection is None:
        return None
    if Path(program_root).resolve(strict=True) != _selection[0]:
        raise ValueError('Datastier kan kun vælges for den kørende programgeneration.')
    return Path(_active_selection[0]['data_root'])


def active_details():
    if _active_selection is None:
        raise ValueError('Denne administrationsindgang kræver aktivt generationsvalg.')
    return dict(_active_selection[0])


def active_selection_sha256():
    """Identity of the leased pointer, for a parent's delayed child startup."""
    if _active_selection is None:
        raise RuntimeError('Processen har ikke et aktivt generationsvalg.')
    return _active_selection[1]


def child_command(module, arguments):
    if _selection is None:
        return [sys.executable, '-B', '-m', module, *map(str, arguments)]
    for entry, (kind, target) in ENTRIES.items():
        if kind == 'module' and target == module:
            return build_command(*_selection, entry, arguments)
    raise ValueError('Underprocessen har ingen kontrolleret generationsindgang.')


def assert_generation(package_root, runtime_root, manifest_sha256):
    expected = (Path(package_root).resolve(strict=True), Path(runtime_root).resolve(strict=True), manifest_sha256)
    if _selection != expected:
        raise ValueError('Programgenerationen er ikke bundet af den faste startmekanisme.')


def child_cwd(default):
    return _selection[1] / 'runtime' if _selection is not None else default


def prepare_imports(package_root, runtime_root, entry):
    """Use only in a fresh process, after validate_generation succeeds."""
    if entry not in ENTRIES:
        raise ValueError('Ukendt programindgang.')
    if any(name == 'app' or name.startswith('app.') or name == 'server' or name.startswith('server.')
           for name in sys.modules):
        raise RuntimeError('Generationsvalg kræver en frisk proces uden indlæst programkode.')
    package = _plain(package_root, True)
    runtime = _plain(Path(runtime_root) / 'runtime', True)
    paths = _pth(runtime)
    # Keep the stdlib and extension modules ahead of source folders, just as
    # the supported embedded _pth does. Never retain its old '..' entry.
    sys.path[:] = paths[:2] + ([str(package / 'hub')] if entry == 'hub' else []) + [str(package), paths[2]]
    environment = clean_environment()
    os.environ.clear()
    os.environ.update(environment)
    sys.dont_write_bytecode = True
    os.chdir(runtime)


def dispatch(package_root, entry, arguments):
    kind, target = ENTRIES[entry]
    package = Path(package_root)
    path = package / (target.replace('.', '/') + '.py' if kind == 'module' else target)
    _plain(path, False)
    sys.argv = [str(path), *arguments]
    if kind == 'module':
        runpy.run_module(target, run_name='__main__', alter_sys=True)
    else:
        runpy.run_path(str(path), run_name='__main__')


def main(argv=None):
    global _integrity_lease
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--package-root', type=Path)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--active', action='store_true')
    parser.add_argument('--expected-selection')
    parser.add_argument('--expected-installation-id')
    parser.add_argument('--entry', choices=ENTRIES, required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        stable = _plain(args.runtime_root, True)
        if _plain(Path(sys.executable), False) != _plain(stable / 'runtime/python.exe', False):
            raise ValueError('Start med den installerede, firewallbundne Python-runtime.')
        if _plain(Path(__file__), False) != _plain(stable / LAUNCHER, False):
            raise ValueError('Start med installationens faste startmekanisme.')
        if not sys.flags.isolated:
            raise ValueError('Generationsvalg kræver isoleret Python-opstart (-I).')
        with ExitStack() as lifetime:
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            lease = None
            if args.active:
                if args.package_root is not None or args.manifest_sha256 is not None:
                    raise ValueError('Aktivt generationsvalg kan ikke kombineres med et manuelt programvalg.')
                source = _plain(stable / 'app/generation_selection.py', False)
                # Execute the verified source path directly, never a stale .pyc
                # belonging to the stable helper before candidate validation.
                selection = runpy.run_path(str(source), run_name='_makerspace_generation_selection')
                lease = lifetime.enter_context(selection['SelectionLease'](stable, args.expected_selection))
                if args.expected_installation_id is not None and args.expected_installation_id != lease.record['installation_id']:
                    raise ValueError('Generationsvalget tilhører en anden installationsejer.')
                package = Path(lease.record['program_root'])
                manifest_hash = lease.record['manifest_sha256']
                arguments = selection['bind_arguments'](lease.record, args.entry, arguments)
            else:
                control = stable / 'control'
                try:
                    control.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError('Denne installation kræver aktivt generationsvalg; manuelt valg afvises.')
                if (args.expected_selection is not None or args.expected_installation_id is not None
                        or args.package_root is None or args.manifest_sha256 is None):
                    raise ValueError('Angiv et aktivt generationsvalg eller både programmappe og manifesthash.')
                package = _plain(args.package_root, True)
                manifest_hash = args.manifest_sha256
            integrity = None
            if args.entry == 'server' and _private_roots(package, stable):
                integrity = lifetime.enter_context(IntegrityLease(package, stable))
            elif (args.entry in ('print', 'design', 'hub')
                  and os.environ.get('MAKERSPACE_INTEGRITY_PROOF')
                  and _private_roots(package, stable)):
                integrity = lifetime.enter_context(IntegrityLease(package, stable))
            selected_hash = lease.sha256 if lease is not None else None
            verified_by_parent = _try_parent_proof(integrity, package, stable, manifest_hash,
                                                   args.entry, selected_hash, args.active,
                                                   lease.record['installation_id'] if lease else None)
            if verified_by_parent:
                _validate_entry_manifest(package, manifest_hash, args.entry)
            else:
                validate_generation(package, stable, manifest_hash, args.entry)
            if args.entry in ('print', 'design', 'hub'):
                os.environ.pop('MAKERSPACE_INTEGRITY_PROOF', None)
            if args.entry == 'server' and integrity is not None and integrity.live:
                try:
                    integrity.start_proofs(manifest_hash, selected_hash)
                except Exception:
                    # Optimization failure leaves the original full checks in
                    # place, including bootstrap and every child launcher.
                    integrity.close()
                if integrity.listener is not None:
                    _integrity_lease = integrity
                    globals()['_verified_manifest'] = manifest_hash
                    sys.modules['_makerspace_verified_launcher'] = sys.modules['__main__']
            if args.entry == 'server':
                # These values are bound by the launcher, not inherited from an
                # arbitrary caller's service arguments.
                forbidden = ('--package-root', '--runtime-root', '--manifest-sha256')
                if any(item.split('=', 1)[0] in forbidden for item in arguments):
                    raise ValueError('Serverens programvalg fastlægges af startmekanismen.')
                arguments += ['--package-root', str(package), '--runtime-root', str(stable),
                              '--manifest-sha256', manifest_hash]
            prepare_imports(package, stable, args.entry)
            # Import only after removing the stable installation's app path. The
            # selected helper is byte-identical but carries this process's binding.
            from app import generation_runtime as selected_runtime
            selected_runtime.bind_generation(package, stable, manifest_hash)
            if lease is not None:
                selected_runtime.bind_active_selection(lease.record, lease.sha256)
            dispatch(package, args.entry, arguments)
    except (OSError, ValueError, RuntimeError) as error:
        print('Generationsstart afvist: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
