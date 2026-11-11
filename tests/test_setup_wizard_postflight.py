import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSTFLIGHT = ROOT / "deployment" / "school-setup" / "setup-wizard-postflight.ps1"


class SetupWizardPostflightTests(unittest.TestCase):
    @classmethod
    def ps(cls):
        return shutil.which("powershell.exe") or shutil.which("pwsh")

    def run_ps(self, body):
        exe = self.ps()
        if not exe:
            self.skipTest("PowerShell kræves")
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "postflight.ps1"
            script.write_text(
                "Set-StrictMode -Version Latest\n"
                ". '" + str(POSTFLIGHT).replace("'", "''") + "'\n. '" + str(POSTFLIGHT.with_name("setup-wizard-model.ps1")).replace("'", "''") + "'\n" + body,
                encoding="utf-8-sig",
            )
            return subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                capture_output=True, text=True, timeout=30,
            )

    def test_server_success_checks_owned_listeners_and_https_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = r'''
$env:ProgramFiles = $args[0]; $env:ProgramData = Join-Path $args[0] 'data'
$install = Join-Path $env:ProgramFiles 'ElevhubServer'; $data = Join-Path $env:ProgramData 'ElevhubServer'
New-Item -ItemType Directory -Force (Join-Path $data 'security'), (Join-Path $install 'runtime') | Out-Null
[ordered]@{schemaVersion=1;product='ElevhubServer';state='installed';installRoot=$install;dataRoot=$data;roleIp='192.0.2.10';teacherAddress='127.0.0.1';studentSubnet='192.0.2.0/24';teacherPort=8780} | ConvertTo-Json | Set-Content (Join-Path $data 'installation-owner.json')
Set-Content (Join-Path $install 'runtime\python.exe') 'fixture'
Set-Content (Join-Path $data 'school.json') '{"schemaVersion":1,"school_name":"Testskolen","server":"192.0.2.10","student_subnet":"192.0.2.0/24","role":"makerspace"}'
Set-Content (Join-Path $data 'security\ca.crt') 'PUBLIC-CA'
function Get-ScheduledTask { param($TaskName); [pscustomobject]@{State='Running'} }
function Get-NetTCPConnection { param($State,$LocalAddress,$LocalPort); [pscustomobject]@{OwningProcess=42} }
function Get-CimInstance { param($Class,$Filter); [pscustomobject]@{ExecutablePath=(Join-Path $install 'runtime\python.exe')} }
function Test-SetupPostflightHttps { param($Address,$Port) }
$plan=Test-SetupWizardInput @{deployment='single-computer';component='server';schoolName='Testskolen';roleIp='192.0.2.10';studentSubnet='192.0.2.0/24';theme='green';machine='fbb2';addressConfirmed=$true;packageRoot='C:\fixture'}
$before = @(Get-ChildItem -LiteralPath $install,$data -Recurse -File | Get-FileHash | ForEach-Object Hash) -join ','
$result = Test-SetupWizardPostflight $plan
$after = @(Get-ChildItem -LiteralPath $install,$data -Recurse -File | Get-FileHash | ForEach-Object Hash) -join ','
if ($before -cne $after) { throw 'Postflight mutated installed files' }
$result | ConvertTo-Json -Depth 8 -Compress
'''
            # Inject the temporary root into the isolated PowerShell fixture.
            result = self.run_ps(body.replace("$args[0]", "'" + str(root).replace("'", "''") + "'"))
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertTrue(value["ok"], result.stdout)
            self.assertIn("enrollment-listener", [item["name"] for item in value["checks"]])
            for mutation in (
                "function Get-ScheduledTask { [pscustomobject]@{State='Ready'} }",
                "function Test-SetupPostflightHttps { throw 'TLS endpoint unavailable' }",
                "$o=Get-Content -Raw (Join-Path $data 'installation-owner.json') | ConvertFrom-Json; $o.product='Other'; $o | ConvertTo-Json | Set-Content (Join-Path $data 'installation-owner.json')",
                "$o=Get-Content -Raw (Join-Path $data 'installation-owner.json') | ConvertFrom-Json; $o.installRoot='C:\\Other'; $o | ConvertTo-Json | Set-Content (Join-Path $data 'installation-owner.json')",
                "$o=Get-Content -Raw (Join-Path $data 'installation-owner.json') | ConvertFrom-Json; $o.state='installing'; $o | ConvertTo-Json | Set-Content (Join-Path $data 'installation-owner.json')",
                "function Assert-SetupPostflightPlainPath { throw 'reparse fixture' }",
            ):
                with self.subTest(mutation=mutation):
                    altered = body.replace('$before =', mutation + '\n$before =', 1)
                    r = self.run_ps(altered.replace("$args[0]", "'" + str(root).replace("'", "''") + "'"))
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertFalse(json.loads(r.stdout)['ok'], r.stdout)


    def test_server_listener_owned_by_other_process_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = r'''
$env:ProgramFiles = 'ROOT'; $env:ProgramData = Join-Path 'ROOT' 'data'
$install = Join-Path $env:ProgramFiles 'ElevhubServer'; $data = Join-Path $env:ProgramData 'ElevhubServer'
New-Item -ItemType Directory -Force (Join-Path $data 'security'), (Join-Path $install 'runtime') | Out-Null
[ordered]@{schemaVersion=1;product='ElevhubServer';state='installed';installRoot=$install;dataRoot=$data;roleIp='192.0.2.10';teacherAddress='127.0.0.1';studentSubnet='192.0.2.0/24';teacherPort=8780} | ConvertTo-Json | Set-Content (Join-Path $data 'installation-owner.json')
Set-Content (Join-Path $install 'runtime\python.exe') 'fixture'
Set-Content (Join-Path $data 'school.json') '{"schemaVersion":1,"school_name":"Testskolen","server":"192.0.2.10","student_subnet":"192.0.2.0/24","role":"makerspace"}'
Set-Content (Join-Path $data 'security\ca.crt') 'PUBLIC-CA'
function Get-ScheduledTask { [pscustomobject]@{State='Running'} }
function Get-NetTCPConnection { [pscustomobject]@{OwningProcess=42} }
function Get-CimInstance { [pscustomobject]@{ExecutablePath='C:\Other\python.exe'} }
function Test-SetupPostflightHttps { }
$plan=Test-SetupWizardInput @{deployment='single-computer';component='server';schoolName='Testskolen';roleIp='192.0.2.10';studentSubnet='192.0.2.0/24';theme='green';machine='fbb2';addressConfirmed=$true;packageRoot='C:\fixture'}
Test-SetupWizardPostflight $plan | ConvertTo-Json -Depth 8 -Compress
'''.replace("'ROOT'", "'" + str(root).replace("'", "''") + "'")
            result = self.run_ps(body)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertFalse(value["ok"])
            self.assertTrue(any(not item["ok"] for item in value["checks"]))

    def test_station_postflight_does_not_start_station_and_requires_matching_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = r'''
$env:LOCALAPPDATA = 'ROOT'
$install = Join-Path $env:LOCALAPPDATA 'MakervaerkstedetLaserstation'; $version = Join-Path $install 'versions\aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
New-Item -ItemType Directory -Force (Join-Path $version 'program\runtime') | Out-Null
Set-Content (Join-Path $install 'active.json') '{"format":1,"installation_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","kind":"makervaerkstedet-laserstation","version_directory":"versions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
$connection='{"schemaVersion":1,"kind":"makervaerkstedet-laserstation-connection","school_name":"Testskolen","caCertificatePem":"-----BEGIN CERTIFICATE-----fixture","server_url":"https://192.0.2.10:8780","station_id":"laser-fbb2","machine":"fbb2"}'
Set-Content (Join-Path $version 'connection.json') $connection
[IO.File]::WriteAllText((Join-Path $version 'school-ca.pem'), '-----BEGIN CERTIFICATE-----fixture')
$file=Join-Path 'ROOT' 'connection.json'; Set-Content $file $connection
function Invoke-SetupStationInfo { [pscustomobject]@{connection=(Get-Content -Raw $file | ConvertFrom-Json)} }
$plan=Test-SetupWizardInput @{deployment='separate-computers';component='laser-station';connectionFile=$file;packageRoot='C:\fixture'}
Test-SetupWizardPostflight $plan | ConvertTo-Json -Depth 8 -Compress
'''.replace("'ROOT'", "'" + str(root).replace("'", "''") + "'")
            result = self.run_ps(body)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertTrue(value["ok"], result.stdout)
            self.assertIn("start den separat", value["nextSteps"][0])
            for field in ('school_name', 'caCertificatePem', 'server_url', 'station_id', 'machine'):
                with self.subTest(field=field):
                    mutation = "$c=Get-Content -Raw (Join-Path $version 'connection.json') | ConvertFrom-Json; $c." + field + "='changed'; $c | ConvertTo-Json | Set-Content (Join-Path $version 'connection.json')"
                    r = self.run_ps(body.replace('Test-SetupWizardPostflight $plan |', mutation + '\nTest-SetupWizardPostflight $plan |'))
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertFalse(json.loads(r.stdout)['ok'], r.stdout)



if __name__ == "__main__":
    unittest.main()
