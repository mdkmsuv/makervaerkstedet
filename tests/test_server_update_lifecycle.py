"""Installer bridge provisioning/rollback with a synthetic task and folders."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'system/windows-elevhub/server/server-update-lifecycle.ps1'


class BridgeLifecycleTests(unittest.TestCase):
    def run_case(self,case):
        self.assertTrue(SOURCE.is_file(),'Fresh installs and updates must provision the admin bridge')
        if not shutil.which('powershell.exe'):self.skipTest('Windows PowerShell required')
        with tempfile.TemporaryDirectory(prefix='mkv-update-lifecycle-') as directory:
            folder=Path(directory);script=folder/'fixture.ps1'
            script.write_text(r'''param($Source,$Root,$Case)
$ErrorActionPreference='Stop'
$env:PSModulePath=Join-Path $PSHOME 'Modules'
. $Source
if($Case -eq 'remove-absent'){
 $helper=Join-Path $Root 'absent-helper.ps1'
 [IO.File]::WriteAllText($helper,'@{schema=1;removed=$false}|ConvertTo-Json -Compress')
 Invoke-ServerUpdateBridgeCommand -Helper $helper -Mode Remove|ConvertTo-Json -Compress
 exit 0
}
$global:testPaths=@{Store=(Join-Path $Root 'store');Helper=(Join-Path $Root 'store\worker.ps1');InstalledHelper=(Join-Path $Root 'program\server-update.ps1');Task='fake-task'}
$journal=Join-Path $Root 'journal'
foreach($p in @($testPaths.Store,(Split-Path $testPaths.InstalledHelper -Parent),$journal)){New-Item -ItemType Directory -Path $p|Out-Null}
[IO.File]::WriteAllText($testPaths.InstalledHelper,'new-helper')
$global:hadTask=$Case -ne 'new'
$global:events=[Collections.Generic.List[string]]::new()
if($hadTask){[IO.File]::WriteAllText($testPaths.Helper,'old-helper')}
function Get-ServerUpdateLifecyclePaths {return $global:testPaths}
function Get-ScheduledTask {param($TaskName,$ErrorAction);if($global:hadTask){return @{TaskName=$TaskName}}}
function Invoke-ServerUpdateBridgeCommand($Helper,$Mode){
 $global:events.Add($Mode)
 if($Mode -eq 'Status'){return @{available=$true}}
 if($Mode -eq 'Install'){$global:hadTask=$true;[IO.File]::WriteAllText($testPaths.Helper,'new-helper');return @{installed=$true}}
 if($Mode -eq 'Remove'){$global:hadTask=$false;return @{removed=$true}}
}
Enable-ServerUpdateBridge -Journal $journal
if($Case -eq 'tamper'){[IO.File]::WriteAllText((Join-Path $journal 'server-update-worker-before.ps1'),'tampered')}
$rejected=$false;$failure=$null
try{Restore-ServerUpdateBridge -Journal $journal -FallbackHelper $testPaths.InstalledHelper}catch{$rejected=$true;$failure=$_.Exception.Message}
@{events=@($events);has_task=$hadTask;helper=[IO.File]::ReadAllText($testPaths.Helper);rejected=$rejected;error=$failure}|ConvertTo-Json -Compress
''',encoding='utf-8-sig')
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),str(SOURCE),str(folder),case],text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return json.loads(result.stdout)

    def test_existing_task_and_helper_are_restored_after_program_rollback(self):
        value=self.run_case('existing')
        self.assertFalse(value['rejected'],value['error'])
        self.assertTrue(value['has_task']);self.assertEqual(value['helper'],'old-helper')
        self.assertEqual(value['events'],['Status','Install']);self.assertFalse(value['rejected'])

    def test_new_task_is_removed_on_rollback_without_deleting_evidence(self):
        value=self.run_case('new')
        self.assertFalse(value['has_task']);self.assertEqual(value['helper'],'new-helper')
        self.assertEqual(value['events'],['Install','Remove'])

    def test_modified_restore_input_is_not_installed_as_system_code(self):
        value=self.run_case('tamper')
        self.assertTrue(value['rejected']);self.assertEqual(value['helper'],'new-helper')

    def test_remove_acknowledges_already_absent_bridge(self):
        value=self.run_case('remove-absent')
        self.assertEqual(value['schema'],1)
        self.assertIs(value['removed'],False)


if __name__=='__main__':unittest.main()
