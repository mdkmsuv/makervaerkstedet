"""Windows operations for the school's journalled generation maintenance.

Only the two installer-owned tasks are mutated, after exact ownership checks.
External applications and surviving child processes must be closed separately.
The controller never changes a firewall rule or unpauses a production queue.
"""
import base64
import json
import ntpath
import os
from pathlib import Path
import subprocess
import time

from . import generation_host as policy
from . import generation_materialization as materialization
from . import generation_transaction as preparation
from . import installation_backup as backup
from .generation_lock import GenerationLock
from .generation_selection import LOCK
from .generation_selection import TRANSITION, _present
from .instance import InstanceLock
from .installation_backup import _plain
from .release_compatibility import inspect_program


def _powershell(script, request):
    if os.name != 'nt':
        raise RuntimeError('Skolevedligeholdelse kræver Windows.')
    env = os.environ.copy()
    root = next((value for key, value in env.items() if key.lower() == 'systemroot'), None)
    if not root:
        raise RuntimeError('SystemRoot mangler.')
    env['PSModulePath'] = ntpath.join(root, r'System32\WindowsPowerShell\v1.0\Modules')
    env['SCHOOL_MAINTENANCE_INPUT'] = base64.b64encode(json.dumps(request).encode()).decode('ascii')
    result = subprocess.run([ntpath.join(root, r'System32\WindowsPowerShell\v1.0\powershell.exe'),
                             '-NoProfile', '-NonInteractive', '-EncodedCommand',
                             base64.b64encode(script.encode('utf-16le')).decode('ascii')],
                            env=env, capture_output=True, encoding='utf-8-sig', timeout=65,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    if result.returncode:
        raise RuntimeError('Windows-vedligeholdelseshandlingen fejlede: ' + result.stderr.strip())
    return json.loads(result.stdout)


# Paths and records travel as structured data, never interpolated executable code.
TASK_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SCHOOL_MAINTENANCE_INPUT)) | ConvertFrom-Json
function Sid($identity) { if ([string]$identity -match '^S-1-') { return [Security.Principal.SecurityIdentifier]::new([string]$identity).Value }; return [Security.Principal.NTAccount]::new([string]$identity).Translate([Security.Principal.SecurityIdentifier]).Value }
$verified = @()
foreach ($expected in $request.tasks) {
  $tasks = @(Get-ScheduledTask -TaskName $expected.name -TaskPath '\' -ErrorAction Stop)
  if ($tasks.Count -ne 1) { throw 'Ambiguous task' }; $task=$tasks[0]
  if ((Sid $task.Principal.UserId) -cne $expected.principal -or [string]$task.Principal.LogonType -cne $expected.logon_type -or [string]$task.Principal.RunLevel -cne $expected.run_level -or [string]$task.Description -cne $expected.description -or @($task.Actions).Count -ne 1) { throw 'Task ownership changed' }
  $a=$task.Actions[0]; $e=$expected.actions[0]
  if ([string]$a.Execute -ine [string]$e.execute -or [string]$a.Arguments -cne [string]$e.arguments -or [string]$a.WorkingDirectory -ine [string]$e.working_directory) { throw 'Task command changed' }
  $verified += $task
}
if ($verified.Count -ne 2) { throw 'Incomplete task receipt' }
if ($request.action -eq 'capture') {
  @($verified | ForEach-Object { [ordered]@{name=$_.TaskName;enabled=[bool]$_.Settings.Enabled;running=([string]$_.State -eq 'Running')} }) | ConvertTo-Json -Compress
} elseif ($request.action -eq 'stop') {
  # Disable both before stopping either; boot/retry triggers cannot race staging.
  foreach ($task in $verified) { Disable-ScheduledTask -TaskName $task.TaskName -TaskPath '\' | Out-Null }
  foreach ($task in $verified) { Stop-ScheduledTask -TaskName $task.TaskName -TaskPath '\' }
  $deadline=[DateTime]::UtcNow.AddSeconds(30)
  do {
    $busy=@($verified | ForEach-Object { Get-ScheduledTask -TaskName $_.TaskName -TaskPath '\' } | Where-Object { [string]$_.State -in @('Running','Queued') })
    if (-not $busy.Count) { break }; Start-Sleep -Milliseconds 200
  } while ([DateTime]::UtcNow -lt $deadline)
  if ($busy.Count) { throw 'Owned tasks did not stop' }
  @{stopped=$true} | ConvertTo-Json -Compress
} elseif ($request.action -eq 'start') {
  foreach ($task in $verified) {
    $before=@($request.states | Where-Object { $_.name -ceq $task.TaskName })
    if ($before.Count -ne 1) { throw 'Incomplete prior task state' }
    if ($before[0].enabled -or $before[0].running) { Enable-ScheduledTask -TaskName $task.TaskName -TaskPath '\' | Out-Null }
    else { Disable-ScheduledTask -TaskName $task.TaskName -TaskPath '\' | Out-Null }
    try {
      if ($before[0].running) {
        $now=Get-ScheduledTask -TaskName $task.TaskName -TaskPath '\'
        if ([string]$now.State -ne 'Running') { Start-ScheduledTask -TaskName $task.TaskName -TaskPath '\' }
      }
    } finally {
      # A disabled task can have been running when captured. Start on demand
      # with a temporary enable, then restore its trigger/boot disabled state.
      if (-not $before[0].enabled) { Disable-ScheduledTask -TaskName $task.TaskName -TaskPath '\' | Out-Null }
    }
  }
  @{restored=$true} | ConvertTo-Json -Compress
} else { throw 'Unknown task operation' }
'''


ACL_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SCHOOL_MAINTENANCE_INPUT)) | ConvertFrom-Json
# Validate the entire path chain and tree before any ACL write.
foreach ($row in $request.paths) {
  $item=Get-Item -LiteralPath $row.path -Force
  while ($null -ne $item) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point' }
    $item=if ($item -is [IO.DirectoryInfo]) { $item.Parent } else { $item.Directory }
  }
}
foreach ($row in $request.paths) {
  $item=Get-Item -LiteralPath $row.path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point' }
  $acl=if ($item.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
  $acl.SetAccessRuleProtection($true,$false)
  $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
  foreach ($grant in $row.grants) {
    $sid=[Security.Principal.SecurityIdentifier]::new([string]$grant.sid)
    $rights=[Security.AccessControl.FileSystemRights]([int]$grant.mask)
    if ($item.PSIsContainer) { $rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,$rights,[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow) }
    else { $rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,$rights,[Security.AccessControl.AccessControlType]::Allow) }
    $acl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $row.path -AclObject $acl
}
@{secured=$true} | ConvertTo-Json -Compress
'''


def acl_plan(folder, data_root, role):
    """Explicit supported ACLs, only for the new materialized generation."""
    folder = _plain(Path(folder), True)
    data_root = _plain(Path(data_root), True)
    if data_root != folder / 'data':
        raise ValueError('Rettigheder må kun etableres på den endelige generationskopi.')
    rows = []
    # backup's scanner refuses junctions/symlinks before constructing the plan.
    from .installation_backup import _scan
    files, directories = _scan(folder, ignore_locks=False)
    for path in [folder, *(folder / name for name in sorted(directories)),
                 *(folder / name for name in sorted(files))]:
        grants = [{'sid': policy.SYSTEM, 'mask': 0x1F01FF}, {'sid': policy.ADMIN, 'mask': 0x1F01FF}]
        rights, users = policy.RX, None
        if path == data_root or data_root in path.parents:
            relative = path.relative_to(data_root).as_posix().lower()
            component = relative.split('/')[0]
            if component == 'admin-ssh' or relative == 'installation-owner.json' or policy._private_tls_path(relative):
                rights = None
            elif component in {'logs', 'hub', 'printvaerksted', 'designvaerksted', 'local-ai'} or relative in {'instance.lock', 'security/enheder.lock'}:
                rights = policy.MODIFY
            elif role == 'makerspace' and component in {'laserstation', 'laserplader'}:
                rights = policy.MODIFY
                users = policy.MODIFY if component == 'laserplader' else policy.RX
        else:
            users = policy.RX
        if rights is not None:
            grants.append({'sid': policy.SERVICE, 'mask': rights})
        if users is not None:
            grants.append({'sid': policy.USERS, 'mask': users})
        rows.append({'path': str(path), 'grants': grants})
    return rows


class WindowsMaintenanceController:
    def __init__(self, host=None, runner=None, timeout=30):
        self.host = host or policy.WindowsGenerationHost(allow_maintenance_wrapper=True)
        self.runner = runner or _powershell
        self.timeout = timeout

    def _snapshot(self, runtime, record):
        snapshot = self.host.snapshot(runtime, record, record)
        roots = policy._record(record, 'source')
        school, owner, config = policy._metadata(snapshot, roots, roots, record['installation_id'])
        policy._check_tasks(snapshot['tasks'], policy._canonical(str(runtime)), record['installation_id'], owner)
        return snapshot, school, config

    def capture(self, runtime, record):
        snapshot, school, config = self._snapshot(runtime, record)
        roots = policy._record(record, 'source')
        policy._check_paths(snapshot['paths'], policy._canonical(str(runtime)), roots, roots, school, config)
        states = self.runner(TASK_SCRIPT, {'action': 'capture', 'tasks': snapshot['tasks']})
        receipt = {'format': 1, 'tasks': snapshot['tasks'], 'states': states}
        self._receipt(runtime, record, receipt)
        return receipt

    def _receipt(self, runtime, record, receipt):
        snapshot, _, _ = self._snapshot(runtime, record)
        if (not isinstance(receipt, dict) or set(receipt) != {'format', 'tasks', 'states'}
                or type(receipt['format']) is not int or receipt['format'] != 1
                or snapshot['tasks'] != receipt['tasks'] or not isinstance(receipt['states'], list)
                or len(receipt['states']) != 2):
            raise ValueError('Windows-taskkvitteringen er ændret.')
        names = set()
        for row in receipt['states']:
            if (not isinstance(row, dict) or set(row) != {'name', 'enabled', 'running'}
                    or row['name'] not in {'ElevhubServer', 'ElevhubSchoolEnrollment'}
                    or row['name'] in names or type(row['enabled']) is not bool or type(row['running']) is not bool):
                raise ValueError('Ukendt oprindelig tasktilstand.')
            names.add(row['name'])

    def stop(self, runtime, record, receipt):
        self._receipt(runtime, record, receipt)
        self.runner(TASK_SCRIPT, {'action': 'stop', 'tasks': receipt['tasks']})
        deadline = time.monotonic() + self.timeout
        while True:
            snapshot, _, _ = self._snapshot(runtime, record)
            try:
                policy._check_processes(snapshot['processes'], set(policy._record(record, 'source').values()), os.getpid(),
                                        maintenance_runtime=policy._canonical(str(runtime)))
                return
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)

    def secure(self, runtime, folder, source, target):
        runtime, folder = _plain(Path(runtime), True), _plain(Path(folder), True)
        # The materializer excludes operational locks from its business-data
        # seal. Reuse the exact activation lock protocol to create ONLY those
        # recognized files before planning ACLs and inspecting required paths.
        # Keep both generations offline throughout ACL establishment/check.
        with GenerationLock(runtime / 'control' / LOCK, exclusive=True), InstanceLock(folder):
            metadata = materialization._read_json(folder / materialization.MANIFEST)
            material = materialization._verify_materialized_locked(folder,
                expected_operation_id=metadata['operation_id'])
            if any(material[key] != target[key] for key in ('program_root', 'data_root', 'manifest_sha256')):
                raise ValueError('Rettighedskontrollen tilhører en anden materialiseret generation.')
            source_profile = preparation._profile(source, inspect_program(Path(source['program_root']))['data_contract'])
            with backup.offline_profile(source_profile), backup._locked_profile(material['profile'], allow_restored=True):
                snapshot = self.host.snapshot(runtime, source, target)
                school, _, _ = policy._metadata(snapshot, policy._record(source, 'source'),
                                                policy._record(target, 'target'), target['installation_id'])
                self.runner(ACL_SCRIPT, {'paths': acl_plan(folder, target['data_root'], school['role'])})
                self.host.check(runtime, source, target)
                # Operational files must not invalidate or rewrite the seal.
                materialization._verify_materialized_locked(folder,
                    expected_operation_id=metadata['operation_id'])

    def start(self, runtime, record, receipt):
        if _present(Path(runtime) / 'control' / TRANSITION):
            raise RuntimeError('En uafsluttet vedligeholdelse må ikke starte tjenester.')
        self._receipt(runtime, record, receipt)
        self.runner(TASK_SCRIPT, {'action': 'start', 'tasks': receipt['tasks'], 'states': receipt['states']})
        # A requested start must actually remain Running (task success alone is
        # not a server-health probe, but a failed launcher must not be success).
        deadline = time.monotonic() + self.timeout
        stable_after = time.monotonic() + min(2, self.timeout)
        while True:
            states = self.runner(TASK_SCRIPT, {'action': 'capture', 'tasks': receipt['tasks']})
            by_name = {row['name']: row for row in states}
            if time.monotonic() >= stable_after and all(by_name.get(row['name'], {}).get('enabled') == row['enabled']
                   and (not row['running'] or by_name.get(row['name'], {}).get('running'))
                   for row in receipt['states']):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('Tjenesterne blev ikke genstartet; genoptag operationen.')
            time.sleep(.2)
