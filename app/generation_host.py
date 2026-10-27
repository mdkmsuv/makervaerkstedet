"""Conservative, read-only Windows preflight. Caller holds every lifetime lock.

This is a policy check of supported simple filesystem DACLs, not AccessCheck.
Deny/object/callback ACEs, unresolvable identities and unknown layouts refuse.
"""
from __future__ import annotations
import base64
import json
import ntpath
import os
import re
import subprocess
import uuid

SYSTEM = 'S-1-5-18'
SERVICE = 'S-1-5-19'
ADMIN = 'S-1-5-32-544'
USERS = 'S-1-5-32-545'
TRUSTED_INSTALLER = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
PRIVILEGED = {SYSTEM, ADMIN}
ADMIN_TLS_FILES = frozenset({'ca.key', 'tls-initialization.json',
                            'tls-renewal.json', 'tls-renewal-receipt.json'})
# FileSystemRights values, including standard rights. Synchronize is not read.
READ = 0x20089
RX = 0x200A9
MODIFY = 0x301BF
WRITE = 0xD0156  # write data/append/EA/attributes, delete, WRITE_DAC/OWNER
KNOWN = 0x1F01FF
# Windows file-object GENERIC_MAPPING (including SYNCHRONIZE). A collector
# using FileSystemRights may return the same DWORD as a signed Int32.
# https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights
_GENERIC_FILE_RIGHTS = {
    0x80000000: 0x120089,  # FILE_GENERIC_READ
    0x40000000: 0x120116,  # FILE_GENERIC_WRITE
    0x20000000: 0x1200A0,  # FILE_GENERIC_EXECUTE
    0x10000000: KNOWN,     # FILE_ALL_ACCESS
}


def _private_tls_path(relative):
    relative = relative.replace('/', '\\').lower()
    if not relative.startswith('security\\'):
        return False
    name = relative.removeprefix('security\\')
    return '\\' not in name and any(
        name == private or name == private + '.tmp'
        or (name.startswith('.' + private + '.') and name.endswith('.tmp'))
        for private in ADMIN_TLS_FILES)


def _file_access_mask(value):
    """Expand file generic rights before applying the existing ACL policy.

    Accept only a DWORD or its signed Int32 representation. Unknown specific
    bits remain errors, even on inherit-only ACEs; nothing is ignored merely
    because a rule belongs to an ancestor directory.
    """
    if type(value) is not int or not -(2**31) <= value < 2**32:
        raise ValueError('Ugyldig Windows-rettighedsmaske.')
    mask = value & 0xFFFFFFFF
    for generic, specific in _GENERIC_FILE_RIGHTS.items():
        if mask & generic:
            mask = (mask & ~generic) | specific
    if not mask or mask & ~KNOWN:
        raise ValueError('Ukendt Windows-rettighedsmaske.')
    return mask


def _canonical(value):
    if not isinstance(value, str) or not re.match(r'^[A-Za-z]:[\\/]', value) or value.startswith(('\\\\', '//')):
        raise ValueError('Der kræves lokale absolutte Windows-stier.')
    if ':' in value[2:] or any(part.endswith((' ', '.')) for part in re.split(r'[\\/]', value)[1:] if part):
        raise ValueError('Tvetydig Windows-sti.')
    normal = ntpath.normcase(ntpath.normpath(value))
    return normal if len(normal) == 3 else normal.rstrip('\\')


def _under(value, roots):
    value = _canonical(value)
    return any(value == root or value.startswith(root + '\\') for root in roots)


def _record(value, name):
    fields = {'format', 'installation_id', 'revision', 'runtime_root', 'program_root', 'data_root', 'manifest_sha256'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['format']) is not int or value['format'] != 1
        or type(value['revision']) is not int or not 1 <= value['revision'] < 2**63
        or not isinstance(value['installation_id'], str) or not re.fullmatch('[a-f0-9]{32}', value['installation_id'])
        or not isinstance(value['manifest_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['manifest_sha256'])):
        raise ValueError(name + ': ukendt generationsformat.')
    roots = {key: _canonical(value[key]) for key in ('runtime_root', 'program_root', 'data_root')}
    for key in ('runtime_root', 'program_root'):
        if _under(roots['data_root'], [roots[key]]) or _under(roots[key], [roots['data_root']]):
            raise ValueError('Program og data overlapper.')
    return roots


def _command(runtime, installation_id, entry, tail):
    # Same always-quoted Win32 argv contract as generation-startup.ps1.
    values = ['-B', '-I', runtime + r'\app\generation_runtime.py', '--runtime-root', runtime,
              '--active', '--expected-installation-id', installation_id, '--entry', entry, '--', *tail]
    def quote(value):
        value = re.sub(r'(\\*)"', lambda m: m[1] * 2 + '\\"', str(value))
        value = re.sub(r'(\\+)$', lambda m: m[1] * 2, value)
        return '"' + value + '"'
    return ' '.join(map(quote, values))


def _metadata(snapshot, source, target, installation_id):
    metadata = snapshot.get('metadata')
    if not isinstance(metadata, dict) or set(metadata) != {'school', 'owner', 'config', 'laser'}:
        raise ValueError('Skolens layoutmetadata mangler.')
    school, owner, config = (metadata[key] for key in ('school', 'owner', 'config'))
    if (not isinstance(school, dict) or type(school.get('schemaVersion')) is not int or school.get('schemaVersion') != 1 or school.get('role') not in ('vm', 'makerspace')
        or not isinstance(owner, dict) or owner.get('startupProtocol') != 'generation-v1'
        or type(owner.get('schemaVersion')) is not int or owner.get('schemaVersion') != 1
        or owner.get('product') != 'ElevhubServer' or owner.get('state') != 'installed'
        or not isinstance(config, dict) or config.get('external_backends', []) != []):
        raise ValueError('Ukendt skole-/komponentlayout (eksterne backends understøttes ikke).')
    try:
        if uuid.UUID(owner['ownerId']).hex != installation_id:
            raise ValueError('Forkert installationsejer.')
    except (KeyError, AttributeError, TypeError):
        raise ValueError('Installationsejeren mangler.') from None
    if _canonical(owner.get('installRoot')) != target['runtime_root']:
        raise ValueError('Ejeren tilhører en anden runtime.')
    if any(key not in owner for key in ('roleIp', 'teacherPort', 'teacherAddress')):
        raise ValueError('Installerens opstartsparametre mangler.')
    if (any(not isinstance(owner[key], str) or not owner[key] for key in ('roleIp', 'teacherAddress'))
        or type(owner['teacherPort']) is not int or not 1 <= owner['teacherPort'] <= 65535):
        raise ValueError('Ugyldige opstartsparametre.')
    expected_security = target['data_root'] + r'\security'
    tls = config.get('tls')
    if (not isinstance(tls, dict) or any(_canonical(tls.get(key)) != expected_security + '\\' + filename
        for key, filename in (('certifikat', 'server.crt'), ('noegle', 'server.key'), ('ca', 'ca.crt')))
        or _canonical(config.get('enhedsregister')) != expected_security + r'\enheder.json'):
        raise ValueError('Konfigurationen bruger et ukendt sikkerhedslayout.')
    if config.get('makerspace_laser_queue') is not None:
        raise ValueError('Separat laserkø er endnu ikke et understøttet hostlayout.')
    printers = config.get('makerspace_printers')
    if printers is not None and (not isinstance(printers, dict)
        or _canonical(printers.get('key_file')) != target['data_root'] + r'\printvaerksted\makerspace-bridge.key'):
        raise ValueError('Printerbroens nøgle ligger uden for det kendte layout.')
    laser = metadata['laser']
    if school['role'] == 'vm':
        if laser is not None:
            raise ValueError('VM-layoutet har ukendt laserstation.')
    else:
        if not isinstance(laser, dict) or set(laser) != {'source', 'target'}:
            raise ValueError('Laserstationens layoutmetadata mangler.')
        for name, generation in (('source', source), ('target', target)):
            row = laser[name]
            if not isinstance(row, dict) or set(row) != {'config', 'protocol'}:
                raise ValueError('Ukendt laserstationlayout.')
            station, protocol = row['config'], row['protocol']
            if (not isinstance(station, dict) or station.get('role') != 'Station'
                or _canonical(station.get('plates')) != generation['data_root'] + r'\laserplader'
                or protocol != {'protocol': 1, 'role': 'laser_station'}
                or not isinstance(protocol, dict) or type(protocol.get('protocol')) is not int):
                raise ValueError('Laserstationen skal bruge generationens egne Laserplader med kendt låseprotokol.')
    return school, owner, config


def _check_tasks(tasks, runtime, installation_id, owner):
    if not isinstance(tasks, list) or len(tasks) != 2:
        raise ValueError('Task-inventaret er ufuldstændigt.')
    expected = {'ElevhubServer': (SERVICE, 'server', ['--role-ip', owner['roleIp'], '--teacher-port', str(owner['teacherPort']), '--teacher-address', owner['teacherAddress']], 'ElevhubServer owner=' + owner['ownerId']),
                'ElevhubSchoolEnrollment': (SYSTEM, 'enrollment', ['serve'], 'Elevhub skole-enrollment')}
    seen = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {'name', 'actions', 'principal', 'logon_type', 'run_level', 'description'}:
            raise ValueError('Ukendt taskformat.')
        name = task['name']
        if name not in expected or name in seen:
            raise ValueError('Ukendt eller dubleret task.')
        seen.add(name)
        principal, entry, tail, description = expected[name]
        actions = task['actions']
        if (task['principal'] != principal or task['logon_type'] != 'ServiceAccount' or task['run_level'] != 'Highest'
            or task['description'] != description or not isinstance(actions, list) or len(actions) != 1):
            raise ValueError(name + ': forkert ejer, beskrivelse eller antal handlinger.')
        action = actions[0]
        if (not isinstance(action, dict) or set(action) != {'execute', 'arguments', 'working_directory'}
            or _canonical(action['execute']) != runtime + r'\runtime\python.exe'
            or _canonical(action['working_directory']) != runtime
            or action['arguments'] != _command(owner['installRoot'], installation_id, entry, tail)):
            raise ValueError(name + ': forkert generationskommando.')


def _windows_argv(command):
    if os.name != 'nt':
        return []
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        raise ValueError('Windows-proceskommandoen kunne ikke fortolkes.')
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(pointer)


def _maintenance_wrapper(item, runtime):
    """Recognize only the fixed operator wrappers, never arbitrary shell code.

    The host's complete runtime ACL check also covers these fixed scripts.
    This opt-in is for the operator's waiting parent; ordinary activation hosts
    retain their stricter process policy. Script parameters are literal values.
    """
    command, path = item['command_line'], item['path']
    system = next((value for key, value in os.environ.items() if key.lower() == 'systemroot'), None)
    if not system or not isinstance(command, str) or not path:
        return False
    name = str(item['name']).lower()
    try:
        arguments = _windows_argv(command)
        if not arguments:
            return False
        if name == 'powershell.exe':
            expected_executable = ntpath.join(system, r'System32\WindowsPowerShell\v1.0\powershell.exe')
            if _canonical(path) != _canonical(expected_executable):
                return False
            arguments = arguments[1:]
            no_profile = False
            while arguments and arguments[0].lower() != '-file':
                flag = arguments.pop(0).lower()
                if flag in ('-nologo', '-noninteractive'):
                    continue
                if flag == '-noprofile':
                    no_profile = True
                    continue
                if flag == '-executionpolicy' and arguments and arguments.pop(0).lower() == 'bypass':
                    continue
                return False
            if not no_profile or len(arguments) < 2 or arguments.pop(0).lower() != '-file':
                return False
            script = _canonical(arguments.pop(0))
            manage = script == _canonical(runtime + r'\deployment\school-setup\Manage-School.ps1')
            if not manage and script != _canonical(runtime + r'\deployment\school-setup\Maintain-School.ps1'):
                return False
        elif name == 'cmd.exe':
            if (_canonical(path) != _canonical(ntpath.join(system, r'System32\cmd.exe'))
                    or any(character in command for character in '&|<>^%!')):
                return False
            invocation = re.fullmatch(r'\s*(?:"[^"]+"|\S+)\s+(?:(?:/d|/s)\s+)*(?:/c|/k)\s+(.+?)\s*', command, re.IGNORECASE)
            if invocation is None:
                return False
            tail = invocation[1]
            # The file association uses /c ""path with spaces.cmd" args".
            # Remove only its extra outer shell quotes before Win32 argv parse.
            if tail.startswith('""') and tail.endswith('"'):
                tail = tail[1:-1]
            arguments = _windows_argv('wrapper ' + tail)[1:]
            if not arguments:
                return False
            script = arguments.pop(0)
            manage = _canonical(script) == _canonical(runtime + r'\deployment\school-setup\Manage-School.cmd')
            if not manage and _canonical(script) != _canonical(runtime + r'\deployment\school-setup\Maintain-School.cmd'):
                return False
        else:
            return False
        allowed = ({'-packageroot', '-dataroot'} if manage else
                   {'-action', '-runtimeroot', '-folder', '-materializedfolder', '-programroot',
                    '-backupsource', '-completedfolder', '-settledfolder'})
        seen = set()
        while arguments:
            if len(arguments) < 2:
                return False
            flag, value = arguments.pop(0).lower(), arguments.pop(0)
            if flag not in allowed or flag in seen:
                return False
            seen.add(flag)
            if flag in {'-runtimeroot', '-packageroot'} and _canonical(value) != _canonical(runtime):
                return False
            if flag == '-dataroot':
                _canonical(value)
            if flag == '-action' and value.lower() not in ('update', 'restore', 'rollback', 'resume', 'cancel', 'status'):
                return False
        return True
    except (ValueError, OSError):
        return False


def _check_processes(processes, roots, own_pid, *, maintenance_runtime=None):
    if not isinstance(processes, list):
        raise ValueError('Procesinventaret mangler.')
    for item in processes:
        if not isinstance(item, dict) or set(item) != {'name', 'path', 'command_line', 'pid'} or type(item['pid']) is not int:
            raise ValueError('Ukendt procesformat.')
        if item['pid'] == own_pid:
            continue
        if maintenance_runtime is not None and _maintenance_wrapper(item, maintenance_runtime):
            continue
        name = str(item['name']).lower()
        if any(word in name for word in ('beam', 'flux', 'ghost')):
            raise ValueError('En native makerspaceproces kører stadig.')
        path, command = item['path'], item['command_line']
        if ('python' in name or any(word in name for word in ('bambu', 'cadoodle', 'laser'))) and (not path or not command):
            raise ValueError('En relevant proces kan ikke identificeres.')
        if path and _under(path, roots):
            raise ValueError('En generationsproces kører stadig.')
        if not isinstance(command, str):
            raise ValueError('Proceskommando kan ikke identificeres.')
        # Match roots directly so quoted paths containing spaces remain covered.
        normal = command.lower().replace('/', '\\')
        if any(root in normal for root in roots):
            raise ValueError('En proceskommando bruger generationslayoutet.')


def _acl(info, writers, readers=None, required=0, ancestor=False):
    fields = {'path', 'reparse', 'owner', 'dacl_present', 'unsupported', 'acl'}
    if (not isinstance(info, dict) or set(info) != fields or info['reparse'] is not False
        or info['dacl_present'] is not True or info['unsupported'] is not False
        # Services legitimately own files they create in their writable data
        # components. Ownership grants implicit DACL control, so permit it only
        # where that same SID is already an authorized writer. Code, config and
        # private administration still require a privileged owner.
        or info['owner'] not in PRIVILEGED | {TRUSTED_INSTALLER} | writers or not isinstance(info['acl'], list) or not info['acl']):
        raise ValueError('Usikker eller ikke-understøttet sikkerhedsdescriptor.')
    service_mask = 0
    for ace in info['acl']:
        if (not isinstance(ace, dict) or set(ace) != {'sid', 'mask', 'type', 'inherit_only'}
            or not isinstance(ace['sid'], str) or not re.fullmatch(r'S-1-\d+(?:-\d+)+', ace['sid'])
            or ace['type'] != 'Allow' or type(ace['inherit_only']) is not bool):
            raise ValueError('Deny eller ukendt ACL-regel afvises.')
        mask = _file_access_mask(ace['mask'])
        # Inspect inherit-only ACEs too: their children must not acquire unsafe rights.
        write_mask = 0xD0040 if ancestor else WRITE
        if mask & write_mask and ace['sid'] not in writers and not (ancestor and ace['inherit_only']):
            raise ValueError('ACL giver uautoriseret skriveadgang.')
        if readers is not None and ace['sid'] not in readers:
            raise ValueError('Private administrationsdata har for bred adgang.')
        if ace['sid'] == SERVICE and not ace['inherit_only']:
            service_mask |= mask
    if service_mask & required != required:
        raise ValueError('LocalService mangler de krævede rettigheder.')


def _check_paths(paths, runtime, source, target, school, config):
    if not isinstance(paths, list):
        raise ValueError('Stiinventaret mangler.')
    by_path = {}
    roots = {runtime, source['program_root'], target['program_root'], source['data_root'], target['data_root']}
    ancestors = set()
    for root in roots:
        parent = ntpath.dirname(root)
        while parent and parent != root:
            ancestors.add(_canonical(parent))
            root, parent = parent, ntpath.dirname(parent)
    for info in paths:
        path = _canonical(info.get('path')) if isinstance(info, dict) else ''
        if path in by_path:
            raise ValueError('Dubleret ACL-sti.')
        by_path[path] = info
        if path in ancestors and not _under(path, roots):
            # Parent replacement (DELETE_CHILD) is as dangerous as child writes.
            _acl(info, PRIVILEGED | {TRUSTED_INSTALLER}, ancestor=True)
        elif _under(path, {runtime, source['program_root'], target['program_root']}):
            _acl(info, PRIVILEGED, required=READ if path == runtime + r'\control\generation.lock' else RX)
        elif _under(path, {source['data_root'], target['data_root']}):
            data = next(root for root in (target['data_root'], source['data_root']) if _under(path, {root}))
            relative = path[len(data):].lstrip('\\')
            component = relative.split('\\')[0]
            if component == 'admin-ssh' or relative == 'installation-owner.json' or _private_tls_path(relative):
                _acl(info, PRIVILEGED, readers=PRIVILEGED)
            elif component in {'logs', 'hub', 'printvaerksted', 'designvaerksted', 'local-ai'} or relative == 'instance.lock' or relative == 'security\\enheder.lock':
                _acl(info, PRIVILEGED | {SERVICE},
                     readers=PRIVILEGED | {SERVICE} if component == 'security' else None,
                     required=MODIFY)
            elif school['role'] == 'makerspace' and component in {'laserstation', 'laserplader'}:
                _acl(info, PRIVILEGED | {SERVICE} | ({USERS} if component == 'laserplader' else set()), required=MODIFY)
            elif component == 'security':
                # TLS keys and device/enrollment data follow the installer's
                # private security-tree contract. A restricted parent alone
                # cannot protect a known file path with a broader read ACE.
                _acl(info, PRIVILEGED, readers=PRIVILEGED | {SERVICE}, required=READ)
            else:
                _acl(info, PRIVILEGED, required=READ)
        else:
            raise ValueError('ACL-sti uden for det kendte layout.')
    required = roots | ancestors | {runtime + r'\control', runtime + r'\control\generation.lock'}
    for data in (source['data_root'], target['data_root']):
        required |= {data + '\\' + child for child in ('konfiguration.json', 'instance.lock', 'backup-protocol.json', 'installation-owner.json', 'school.json', 'admin-ssh', 'admin-ssh\\id_ed25519', 'security', 'security\\server.key', 'security\\server.crt', 'security\\ca.crt', 'security\\enheder.json', 'security\\enheder.lock', 'security\\enrollment', 'security\\enrollment\\instance.lock', 'security\\enrollment\\operations', 'security\\enrollment\\operations\\instance.lock', 'logs', 'hub', 'hub\\instance.lock', 'printvaerksted', 'printvaerksted\\instance.lock', 'designvaerksted', 'designvaerksted\\instance.lock', 'local-ai')}
        if school['role'] == 'makerspace':
            required |= {data + r'\laserstation', data + r'\laserstation\laser-installation.json', data + r'\laserplader', data + r'\laserplader\instance.lock', data + r'\laserplader\backup-protocol.json'}
    if config.get('makerspace_printers') is not None:
        required.add(target['data_root'] + r'\printvaerksted\makerspace-bridge.key')
    if not required <= by_path.keys():
        raise ValueError('ACL-inventaret mangler nødvendige rødder, konfigurationer eller låse.')


# The script is constant: paths/records are only transported as structured environment data.
COLLECTOR = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:GENERATION_HOST_INPUT)) | ConvertFrom-Json
function Sid($identity) { if ([string]$identity -match '^S-1-') { return [Security.Principal.SecurityIdentifier]::new([string]$identity).Value }; return [Security.Principal.NTAccount]::new([string]$identity).Translate([Security.Principal.SecurityIdentifier]).Value }
function PathInfo($path) {
  $item = Get-Item -LiteralPath $path -Force
  $acl = Get-Acl -LiteralPath $path
  $raw = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(),0)
  $bad = $false
  foreach ($ace in $raw.DiscretionaryAcl) { if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback -or [string]$ace.AceQualifier -notin @('AccessAllowed','AccessDenied') -or ([int]$ace.AceFlags -band -32)) { $bad = $true } }
  [ordered]@{path=[string]$item.FullName;reparse=(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0);owner=[string]$raw.Owner.Value;dacl_present=($null -ne $raw.DiscretionaryAcl);unsupported=$bad;acl=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object {[ordered]@{sid=[string]$_.IdentityReference.Value;mask=[int]$_.FileSystemRights;type=[string]$_.AccessControlType;inherit_only=(($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0)}})}
}
$roots = @([string]$request.runtime_root,[string]$request.source_record.program_root,[string]$request.target_record.program_root,[string]$request.source_record.data_root,[string]$request.target_record.data_root) | Select-Object -Unique
$all = @{}
# Inspect parents before recursion, and never traverse a reparse point.
foreach ($root in $roots) {
  $current = Get-Item -LiteralPath $root -Force
  while ($null -ne $current) { $info = PathInfo $current.FullName; if ($info.reparse) { throw 'Reparse point' }; $all[$info.path]=$info; $current=$current.Parent }
  $queue = [Collections.Generic.Queue[string]]::new(); $queue.Enqueue($root)
  while ($queue.Count) {
    $folder = $queue.Dequeue()
    foreach ($child in Get-ChildItem -LiteralPath $folder -Force) {
      $info = PathInfo $child.FullName; if ($info.reparse) { throw 'Reparse point' }; $all[$info.path]=$info
      if ($child.PSIsContainer) { $queue.Enqueue($child.FullName) }
    }
  }
}
$tasks = @('ElevhubServer','ElevhubSchoolEnrollment' | ForEach-Object {
  $task = @(Get-ScheduledTask -TaskName $_ -TaskPath '\' -ErrorAction Stop)
  if ($task.Count -ne 1) { throw 'Ambiguous task' }; $task=$task[0]
  [ordered]@{name=$_;actions=@($task.Actions | ForEach-Object {[ordered]@{execute=[string]$_.Execute;arguments=[string]$_.Arguments;working_directory=[string]$_.WorkingDirectory}});principal=(Sid $task.Principal.UserId);logon_type=[string]$task.Principal.LogonType;run_level=[string]$task.Principal.RunLevel;description=[string]$task.Description}
})
$processes = @(Get-CimInstance Win32_Process | ForEach-Object {[ordered]@{name=[string]$_.Name;path=[string]$_.ExecutablePath;command_line=[string]$_.CommandLine;pid=[int]$_.ProcessId}})
$data = [string]$request.target_record.data_root
$metadata = [ordered]@{school=(Get-Content -LiteralPath (Join-Path $data 'school.json') -Raw | ConvertFrom-Json);owner=(Get-Content -LiteralPath (Join-Path $data 'installation-owner.json') -Raw | ConvertFrom-Json);config=(Get-Content -LiteralPath (Join-Path $data 'konfiguration.json') -Raw | ConvertFrom-Json);laser=$null}
if ($metadata.school.role -eq 'makerspace') {
  function LaserMetadata($root) { [ordered]@{config=(Get-Content -LiteralPath (Join-Path $root 'laserstation\laser-installation.json') -Raw | ConvertFrom-Json);protocol=(Get-Content -LiteralPath (Join-Path $root 'Laserplader\backup-protocol.json') -Raw | ConvertFrom-Json)} }
  $metadata.laser=[ordered]@{source=(LaserMetadata ([string]$request.source_record.data_root));target=(LaserMetadata $data)}
}
[ordered]@{tasks=$tasks;processes=$processes;paths=@($all.Values);metadata=$metadata} | ConvertTo-Json -Depth 30 -Compress
'''


class WindowsGenerationHost:
    def __init__(self, snapshot_provider=None, *, allow_maintenance_wrapper=False):
        self.snapshot_provider = snapshot_provider
        self.allow_maintenance_wrapper = allow_maintenance_wrapper

    def snapshot(self, runtime_root, source_record, target_record):
        payload = json.dumps({'runtime_root': os.fspath(runtime_root), 'source_record': source_record, 'target_record': target_record}, ensure_ascii=True)
        if self.snapshot_provider is not None:
            value = self.snapshot_provider(payload)
        else:
            if os.name != 'nt':
                raise RuntimeError('Windows-hostkontrollen kræver Windows.')
            env = os.environ.copy()
            env['GENERATION_HOST_INPUT'] = base64.b64encode(payload.encode('utf-8')).decode('ascii')
            system_root = next((value for key, value in env.items() if key.lower() == 'systemroot'), None)
            if not system_root:
                raise RuntimeError('SystemRoot mangler.')
            # Do not load user/bundled PowerShell modules in this security preflight.
            env['PSModulePath'] = ntpath.join(system_root, r'System32\WindowsPowerShell\v1.0\Modules')
            executable = ntpath.join(system_root, r'System32\WindowsPowerShell\v1.0\powershell.exe')
            encoded = base64.b64encode(COLLECTOR.encode('utf-16le')).decode('ascii')
            result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], env=env,
                                    capture_output=True, encoding='utf-8-sig', timeout=60, check=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode or not result.stdout.strip():
                raise RuntimeError('Windows-hostsnapshot kunne ikke indlæses.')
            try:
                value = json.loads(result.stdout)
            except (ValueError, TypeError) as error:
                raise RuntimeError('Ugyldigt Windows-hostsnapshot.') from error
        if not isinstance(value, dict) or set(value) != {'tasks', 'processes', 'paths', 'metadata'}:
            raise ValueError('Ukendt Windows-hostsnapshot.')
        return value

    def check(self, runtime_root, source_record, target_record):
        runtime_root = os.fspath(runtime_root)
        runtime = _canonical(runtime_root)
        source, target = _record(source_record, 'source'), _record(target_record, 'target')
        if source['runtime_root'] != runtime or target['runtime_root'] != runtime or source_record['installation_id'] != target_record['installation_id']:
            raise ValueError('Generationsrecords tilhører en anden installation.')
        snapshot = self.snapshot(runtime_root, source_record, target_record)
        school, owner, config = _metadata(snapshot, source, target, target_record['installation_id'])
        _check_tasks(snapshot['tasks'], runtime, target_record['installation_id'], owner)
        _check_processes(snapshot['processes'], {runtime, source['program_root'], target['program_root'], source['data_root'], target['data_root']}, os.getpid(),
                         maintenance_runtime=runtime if self.allow_maintenance_wrapper else None)
        _check_paths(snapshot['paths'], runtime, source, target, school, config)
