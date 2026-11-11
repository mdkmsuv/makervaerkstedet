"""Check the narrowly scoped IPv4 /32 firewall ownership equivalence."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GUARDS = ROOT / "windows-elevhub" / "server" / "school-uninstall-guards.ps1"
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
$ast=[Management.Automation.Language.Parser]::ParseFile($env:GUARD_SCRIPT,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
Invoke-Expression (FunctionText $ast 'Test-FirewallAddressEquivalent')
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }

foreach ($pair in @(
    @('192.0.2.1','192.0.2.1'),
    @('192.0.2.1/32','192.0.2.1/32'),
    @('192.0.2.1/32','192.0.2.1'),
    @('192.0.2.1','192.0.2.1/32'),
    @('192.0.2.10/32','192.0.2.10'),
    @('192.0.2.0/24','192.0.2.0/24')
)) {
    Assert (Test-FirewallAddressEquivalent -Actual $pair[0] -Expected $pair[1]) "Equivalent addresses rejected: $($pair -join ' versus ')"
}
foreach ($pair in @(
    @('192.0.2.2','192.0.2.1'),
    @('192.0.2.1/24','192.0.2.1/32'),
    @('192.0.2.1/32','192.0.2.1/24'),
    @('192.0.2.1/0','192.0.2.1'),
    @('Any','192.0.2.1'),
    @('192.0.2.1-192.0.2.2','192.0.2.1'),
    @('192.0.2.1,192.0.2.2','192.0.2.1'),
    @('192.0.2.999','192.0.2.1'),
    @('192.0.2.1/033','192.0.2.1'),
    @('','192.0.2.1'),
    @(' 192.0.2.1','192.0.2.1'),
    @('::1/128','::1')
)) {
    Assert (-not (Test-FirewallAddressEquivalent -Actual $pair[0] -Expected $pair[1])) "Unsafe addresses accepted: $($pair -join ' versus ')"
}
Write-Output 'Firewall address equivalence contract passed.'
'''


@unittest.skipUnless(POWERSHELL, "PowerShell is required for firewall guard tests")
class FirewallAddressEquivalenceTests(unittest.TestCase):
    def test_only_same_ipv4_host_with_optional_32_is_equivalent(self):
        env = {**os.environ, "GUARD_SCRIPT": str(GUARDS)}
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
        self.assertIn("Firewall address equivalence contract passed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
