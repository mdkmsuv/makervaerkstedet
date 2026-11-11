"""Exercise BOM parsing and exact canonical/legacy shortcut ownership."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION = ROOT / "windows-elevhub" / "server" / "fjern.ps1"
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
$bytes=[IO.File]::ReadAllBytes($env:STAGED_SCRIPT)
if (($bytes[0..2] -join ' ') -ne '239 187 191') { throw 'Staged fjern.ps1 is not UTF-8 BOM encoded.' }
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:STAGED_SCRIPT,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
Invoke-Expression (FunctionText $ast 'Get-TaskShortcutNames')
. $env:GUARDS
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }

$canonical='Elevhub skoleopsætning.lnk'
$names=@(Get-TaskShortcutNames $canonical)
Assert ($names.Count -eq 2) 'Expected exactly canonical and one legacy shortcut name.'
Assert ($names[0] -ceq $canonical) 'Canonical shortcut name was not first.'
Assert ($names[1] -cne $canonical) 'Legacy shortcut alias was not distinct.'
$tempRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
$root=[IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('elevhub-uninstall-encoding-' + [Guid]::NewGuid().ToString('N'))))
$rootName=Split-Path -Leaf $root
Assert ($root.StartsWith($tempRoot,[StringComparison]::OrdinalIgnoreCase)) 'Fixture path escaped the system temp directory.'
Assert ($rootName -match '^elevhub-uninstall-encoding-[0-9a-f]{32}$') 'Fixture path did not use the generated bounded basename.'
New-Item -ItemType Directory -Path $root | Out-Null
try {
    $target=Join-Path $root 'Start.cmd'; New-Item -ItemType File -Path $target | Out-Null
    $shell=New-Object -ComObject WScript.Shell
    foreach($name in $names) {
        $path=Join-Path $root $name
        $shortcut=$shell.CreateShortcut($path); $shortcut.TargetPath=$target; $shortcut.WorkingDirectory=$root; $shortcut.Arguments=''; $shortcut.Save()
        Assert (Get-OwnedShortcut $path $target $root '') "Owned shortcut rejected: $name"
    }
    $foreign=Join-Path $root 'foreign.lnk'; $shortcut=$shell.CreateShortcut($foreign); $shortcut.TargetPath=(Join-Path $root 'Other.cmd'); $shortcut.WorkingDirectory=$root; $shortcut.Arguments=''; $shortcut.Save()
    $rejected=$false; try { Get-OwnedShortcut $foreign $target $root '' } catch { $rejected=$true }
    Assert $rejected 'Foreign target was accepted.'
    $badArgs=Join-Path $root 'bad-args.lnk'; $shortcut=$shell.CreateShortcut($badArgs); $shortcut.TargetPath=$target; $shortcut.WorkingDirectory=$root; $shortcut.Arguments='-foreign'; $shortcut.Save()
    $rejected=$false; try { Get-OwnedShortcut $badArgs $target $root '' } catch { $rejected=$true }
    Assert $rejected 'Foreign arguments were accepted.'
    [pscustomobject]@{powershell=$PSVersionTable.PSVersion.ToString();bom=($bytes[0..2] -join ' ');names=$names;foreignRejected=$rejected} | ConvertTo-Json -Compress
} finally {
    $cleanupRoot=[IO.Path]::GetFullPath($root)
    $cleanupName=Split-Path -Leaf $cleanupRoot
    if (Test-Path -LiteralPath $cleanupRoot) {
        if (-not $cleanupRoot.StartsWith($tempRoot,[StringComparison]::OrdinalIgnoreCase) -or $cleanupName -notmatch '^elevhub-uninstall-encoding-[0-9a-f]{32}$') { throw 'Refusing recursive fixture cleanup outside the generated temp directory.' }
        Remove-Item -LiteralPath $cleanupRoot -Recurse -Force
    }
}
'''


@unittest.skipUnless(POWERSHELL, "PowerShell is required for uninstall encoding tests")
class UninstallEncodingTests(unittest.TestCase):
    def test_bom_and_exact_legacy_shortcut_ownership(self):
        script = Path(os.environ.get("UNINSTALL_ENCODING_SCRIPT", str(PRODUCTION)))
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", HARNESS],
            env={**os.environ, "STAGED_SCRIPT": str(script), "GUARDS": str(GUARDS)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"bom":"239 187 191"', result.stdout)
        self.assertIn('"foreignRejected":true', result.stdout.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
