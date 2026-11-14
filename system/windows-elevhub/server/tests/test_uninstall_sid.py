"""Verify uninstall task ownership is checked by resolved SID, not UI language."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path


SERVER = Path(__file__).resolve().parents[1]
UNINSTALL = SERVER / "fjern.ps1"
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
$ast=[Management.Automation.Language.Parser]::ParseFile($env:UNINSTALL_SCRIPT,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
Invoke-Expression (FunctionText $ast 'Get-TaskPrincipalSid')
Invoke-Expression (FunctionText $ast 'Get-TaskPrincipalSidFromTask')
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }

$localized = ([Security.Principal.SecurityIdentifier]'S-1-5-19').Translate([Security.Principal.NTAccount]).Value
Assert ((Get-TaskPrincipalSid $localized) -eq 'S-1-5-19') "Localized Local Service account did not resolve: $localized"
Assert ((Get-TaskPrincipalSid 'S-1-5-19') -eq 'S-1-5-19') 'Local Service SID changed during normalization.'
Assert ((Get-TaskPrincipalSid 'S-1-5-18') -eq 'S-1-5-18') 'SYSTEM SID changed during normalization.'

$serverTask=[pscustomobject]@{Principal=[pscustomobject]@{UserId=$localized}}
$enrollmentTask=[pscustomobject]@{Principal=[pscustomobject]@{UserId=([Security.Principal.SecurityIdentifier]'S-1-5-18').Translate([Security.Principal.NTAccount]).Value}}
Assert ((Get-TaskPrincipalSidFromTask $serverTask) -eq 'S-1-5-19') 'Localized server task owner was rejected.'
Assert ((Get-TaskPrincipalSidFromTask $enrollmentTask) -eq 'S-1-5-18') 'Localized enrollment task owner was rejected.'

Assert ((Get-TaskPrincipalSid 'S-1-5-21-999-999-999-999') -ne 'S-1-5-19') 'Foreign SID was normalized to Local Service.'
foreach ($principal in @('LOKAL UKENDT\KONTO','')) {
    $resolved = Get-TaskPrincipalSid $principal
    Assert ($null -eq $resolved) "Unknown task owner was accepted: $principal"
}
Assert ((Get-TaskPrincipalSidFromTask ([pscustomobject]@{Principal=[pscustomobject]@{UserId='S-1-5-18'}})) -ne 'S-1-5-19') 'SYSTEM was accepted as the server task owner.'
Assert ((Get-TaskPrincipalSidFromTask ([pscustomobject]@{Principal=[pscustomobject]@{UserId='S-1-5-21-999-999-999-999'}})) -ne 'S-1-5-19') 'Foreign SID was accepted as the server task owner.'
Write-Output 'Uninstall task principal SID contract passed.'
'''


@unittest.skipUnless(POWERSHELL, "PowerShell is required for uninstall SID tests")
class UninstallPrincipalSidTests(unittest.TestCase):
    def test_ast_normalizes_localized_accounts_and_rejects_foreign_owners(self):
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", HARNESS],
            env={**os.environ, "UNINSTALL_SCRIPT": str(UNINSTALL)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Uninstall task principal SID contract passed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
