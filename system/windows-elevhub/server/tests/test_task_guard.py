"""Exercise the installer task guard without invoking installation or Windows resources."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSTALL = ROOT / "windows-elevhub" / "server" / "install.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


HARNESS = r'''
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function FunctionText($Ast,[string]$Name) {
    $nodes=@($Ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name},$true))
    if ($nodes.Count -ne 1) { throw "Missing unique function $Name" }
    return $nodes[0].Extent.Text
}
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:INSTALL_SCRIPT,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
Invoke-Expression (FunctionText $ast 'Get-TaskPrincipalSid')
Invoke-Expression (FunctionText $ast 'Assert-OwnedScheduledTask')

$expectedDescription='ElevhubServer owner=11111111-1111-4111-8111-111111111111'
$expectedExecute='C:\Program Files\ElevhubServer\runtime\python.exe'
$expectedWorkingDirectory='C:\Program Files\ElevhubServer'
$expectedArguments='-B "-I" "C:\Program Files\ElevhubServer\app\generation_runtime.py" --runtime-root "C:\Program Files\ElevhubServer" --active --expected-installation-id "11111111111141118111111111111111" --entry "server" -- "--role-ip" "192.0.2.1" "--teacher-port" "18780" "--teacher-address" "127.0.0.1"'
function New-OwnedTask([string]$Principal,[string]$Arguments=$expectedArguments) {
    return [pscustomobject]@{
        Description=$expectedDescription
        Actions=@([pscustomobject]@{Execute=$expectedExecute;WorkingDirectory=$expectedWorkingDirectory;Arguments=$Arguments})
        Principal=[pscustomobject]@{UserId=$Principal}
    }
}
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Mismatch($Task,[string]$Field) {
    $rejected=$false
    try { Assert-OwnedScheduledTask $Task $expectedDescription $expectedExecute $expectedWorkingDirectory $expectedArguments }
    catch { $rejected=$true; Assert ($_.Exception.Message -match $Field) "Mismatch lacked field diagnostic: $Field" }
    Assert $rejected "Unexpectedly accepted mismatch: $Field"
}

$localized = ([Security.Principal.SecurityIdentifier]'S-1-5-19').Translate([Security.Principal.NTAccount]).Value
foreach ($principal in @('S-1-5-19','NT AUTHORITY\LOCAL SERVICE',$localized)) {
    Assert ((Get-TaskPrincipalSid $principal) -eq 'S-1-5-19') "Principal did not normalize: $principal"
    Assert-OwnedScheduledTask (New-OwnedTask $principal) $expectedDescription $expectedExecute $expectedWorkingDirectory $expectedArguments
}
foreach ($principal in @('S-1-5-18','S-1-5-21-999-999-999-999')) {
    $rejected=$false
    try { Assert-OwnedScheduledTask (New-OwnedTask $principal) $expectedDescription $expectedExecute $expectedWorkingDirectory $expectedArguments }
    catch { $rejected=$true; Assert ($_.Exception.Message -match 'principal') "Principal rejection lacked diagnostic: $principal" }
    Assert $rejected "Unexpectedly accepted principal: $principal"
}
$badDescription=New-OwnedTask 'S-1-5-19'; $badDescription.Description='foreign'; Assert-Mismatch $badDescription 'description'
$badArguments=New-OwnedTask 'S-1-5-19' ($expectedArguments + ' "unexpected"'); Assert-Mismatch $badArguments 'arguments'
$badActions=New-OwnedTask 'S-1-5-19'; $badActions.Actions=@(); Assert-Mismatch $badActions 'actions'
$badExecute=New-OwnedTask 'S-1-5-19'; $badExecute.Actions[0].Execute='C:\Other\python.exe'; Assert-Mismatch $badExecute 'execute'
$badWorkingDirectory=New-OwnedTask 'S-1-5-19'; $badWorkingDirectory.Actions[0].WorkingDirectory='C:\Other'; Assert-Mismatch $badWorkingDirectory 'workingDirectory'

Write-Output 'Task guard contract passed.'
'''


@unittest.skipUnless(POWERSHELL, "PowerShell is required for installer task guard tests")
class TaskGuardTests(unittest.TestCase):
    def test_principal_normalization_and_mismatch_diagnostics(self):
        env = {**os.environ, "INSTALL_SCRIPT": str(INSTALL)}
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", HARNESS],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Task guard contract passed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
