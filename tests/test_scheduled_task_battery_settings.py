"""Check scheduled-task battery policy by evaluating the real PS commands in memory."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / 'windows-elevhub' / 'server' / 'install.ps1'
SETUP = ROOT / 'deployment' / 'school-setup' / 'Setup-School.ps1'
POWERSHELL = shutil.which('pwsh') or shutil.which('powershell.exe')


PROBE = r'''
$ErrorActionPreference = 'Stop'

# The real Windows constructor creates only an in-memory CIM object.
# Do not evaluate either installer or register/start/stop a task.
Import-Module (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules\ScheduledTasks\ScheduledTasks.psd1') -ErrorAction Stop

function Read-SettingsCommand([string]$Path) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw (($errors | ForEach-Object Message) -join '; ') }
    $nodes = @($ast.FindAll({ param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq 'New-ScheduledTaskSettingsSet'
    }, $true))
    if ($nodes.Count -ne 1) { throw "$Path has $($nodes.Count) settings constructors" }
    $command = $nodes[0].Extent.Text
    $settings = Invoke-Expression $command
    [pscustomobject]@{ path=$Path; command=$command; settings=[pscustomobject]@{
        MultipleInstances = [int]$settings.MultipleInstances
        StartWhenAvailable = $settings.StartWhenAvailable
        DisallowStartIfOnBatteries = $settings.DisallowStartIfOnBatteries
        StopIfGoingOnBatteries = $settings.StopIfGoingOnBatteries
        RestartCount = $settings.RestartCount
        RestartIntervalMinutes = [System.Xml.XmlConvert]::ToTimeSpan($settings.RestartInterval).TotalMinutes
        ExecutionTimeLimitSeconds = [System.Xml.XmlConvert]::ToTimeSpan($settings.ExecutionTimeLimit).TotalSeconds
    } }
}

$results = @()
$results += Read-SettingsCommand $env:INSTALL_SCRIPT
$results += Read-SettingsCommand $env:SETUP_SCRIPT
$results | ConvertTo-Json -Depth 5 -Compress
'''


@unittest.skipUnless(POWERSHELL and os.name == 'nt', 'Windows PowerShell is required')
class ScheduledTaskBatterySettingsTests(unittest.TestCase):
    def test_actual_settings_commands_keep_services_alive_on_battery(self):
        env = {**os.environ, 'INSTALL_SCRIPT': str(INSTALL), 'SETUP_SCRIPT': str(SETUP)}
        result = subprocess.run(
            [POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', PROBE],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=20, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual(len(values), 2)
        for value in values:
            with self.subTest(path=value['path']):
                settings = value['settings']
                self.assertTrue(settings['StartWhenAvailable'])
                self.assertFalse(settings['DisallowStartIfOnBatteries'])
                self.assertFalse(settings['StopIfGoingOnBatteries'])
                self.assertEqual(settings['RestartIntervalMinutes'], 1)
                self.assertEqual(settings['ExecutionTimeLimitSeconds'], 0)
                self.assertEqual(settings['MultipleInstances'], 2)  # IgnoreNew
                if value['path'].endswith(r'windows-elevhub\server\install.ps1'):
                    self.assertEqual(settings['RestartCount'], 12)
                else:
                    self.assertEqual(settings['RestartCount'], 5)


if __name__ == '__main__':
    unittest.main()
