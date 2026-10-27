import base64
import re
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'deployment/school-setup/setup-wizard-runner.ps1'
MODEL = RUNNER.with_name('setup-wizard-model.ps1')


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class SetupWizardRunnerTests(unittest.TestCase):
    def run_ps(self, body, timeout=40):
        exe = shutil.which('powershell.exe')
        if not exe:
            self.skipTest('Windows PowerShell required')
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'probe.ps1'
            script.write_text("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n$ErrorActionPreference='Stop'\n. " + quote(RUNNER) + '\n. ' + quote(MODEL) + '\n' + body, encoding='utf-8-sig')
            env = os.environ.copy()
            # The Codex parent is PowerShell 7; prevent its bundled modules
            # from shadowing native Windows PowerShell 5 modules in Python children.
            env['PSModulePath'] = str(Path(exe).parent / 'Modules')
            result = subprocess.run([exe, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)], capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def package(self, root, child='Write-Output fixture; exit 0'):
        for name in ('runtime/python.exe', 'elevhub-package.json', 'makerspace-release.json', 'school-package.json', 'deployment/school-setup/Invoke-SetupWizard.ps1', 'deployment/school-setup/Setup-School.ps1', 'deployment/school-setup/Setup-LaserStation.ps1'):
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(child if name.endswith('Setup-School.ps1') else '{}', encoding='utf-8-sig')

    def values(self, root):
        return "@{deployment='single-computer';component='server';schoolName='Fixture';roleIp='192.0.2.10';studentSubnet='192.0.2.0/24';theme='green';machine='fbb2';addressConfirmed=$true;packageRoot=" + quote(root) + '}'

    def test_encoded_arguments_are_literal_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "child '$ space.ps1"
            result = root / 'arguments.json'
            script.write_text('param($SchoolName,$Result,$Flag)\n@{name=$SchoolName;flag=$Flag} | ConvertTo-Json | Set-Content -LiteralPath $Result', encoding='utf-8-sig')
            dangerous = "Skole Å ' ; $(throw 'executed') ` & |\nsecond line"
            value = self.run_ps('$e=Get-SetupWizardEncodedCall ' + quote(script) + ' @{SchoolName=' + quote(dangerous) + ';Result=' + quote(result) + ';Flag=$true}; & (Join-Path $PSHOME powershell.exe) -NoProfile -NonInteractive -EncodedCommand $e; Get-Content -Raw -LiteralPath ' + quote(result))
            self.assertEqual(value['name'].replace('\r\n', '\n'), dangerous)
            self.assertTrue(value['flag'])

    def test_start_server_station_and_cancel_without_launching(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.package(root)
            body = "$env:LOCALAPPDATA=" + quote(root / 'local') + '; $plan=Test-SetupWizardInput ' + self.values(root) + r'''
$script:calls=@()
function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,$PassThru,$ErrorAction,$Verb)
 $script:calls += @{file=$FilePath;arguments=$ArgumentList;cwd=$WorkingDirectory;window=$WindowStyle;verb=$Verb}; return [pscustomobject]@{Id=42}
}
$server=Start-SetupWizardRun $plan
$plan.entry='Setup-LaserStation.ps1'
$station=Start-SetupWizardRun $plan
function Start-Process { throw 'fixture cancellation' }
$cancel=Start-SetupWizardRun $plan
@{calls=$script:calls;cancel=(Get-SetupWizardRunStatus $cancel);cancelProcess=$cancel.process} | ConvertTo-Json -Depth 10
'''
            value = self.run_ps(body)
            self.assertEqual(value['calls'][0]['verb'], 'RunAs')
            self.assertIsNone(value['calls'][1]['verb'])
            for call in value['calls']:
                self.assertEqual(call['window'], 'Hidden')
                self.assertEqual(call['cwd'], str(root))
                self.assertIn('-EncodedCommand', call['arguments'])
                command = base64.b64decode(call['arguments'][-1]).decode('utf-16-le')
                payload = json.loads(base64.b64decode(re.search(r"FromBase64String\('([^']+)'\)", command).group(1)))
                self.assertEqual(Path(payload['script']), root / 'deployment/school-setup/Invoke-SetupWizard.ps1')
                request = json.loads(base64.b64decode(payload['parameters']['RequestBase64']))
                self.assertEqual(request['schoolName'], 'Fixture')
                self.assertEqual(request['packageRoot'], str(root))
                self.assertTrue(Path(payload['parameters']['RunDirectory']).is_relative_to(root / 'local'))
            self.assertEqual(value['cancel']['state'], 'failed')
            self.assertIsNone(value['cancelProcess'])

    def test_exit_without_final_status_and_nextsteps_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = '$d=' + quote(tmp) + r'''
Set-SetupWizardStatus $d @{state='running';phase='starting'}
$p=[pscustomobject]@{HasExited=$true;ExitCode=0}; $p | Add-Member ScriptMethod Refresh {}
$missing=Get-SetupWizardRunStatus @{directory=$d;process=$p}
Set-SetupWizardStatus $d @{state='succeeded';phase='checked';exitCode=0;checks=@(@{ok=$true;name='fixture';message='fixture'});nextSteps=@('first','second')}
$done=Get-SetupWizardRunStatus @{directory=$d;process=$p}
@{missing=$missing;done=$done} | ConvertTo-Json -Depth 8
'''
            value = self.run_ps(body)
            self.assertEqual(value['missing']['state'], 'failed')
            self.assertEqual(value['done']['nextSteps'], 'first\nsecond')

    def worker(self, child, postflight, expected):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.package(root, child)
            run = root / ('a' * 32)
            run.mkdir()
            body = '$v=' + self.values(root) + '; $d=' + quote(run) + '; $script:checks=0\n' + postflight + r'''
$request=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($v | ConvertTo-Json)))
Invoke-SetupWizardWorker $request $d
@{status=(Get-SetupWizardRunStatus @{directory=$d;process=$null});calls=$script:checks} | ConvertTo-Json -Depth 10
'''
            value = self.run_ps(body)
            self.assertEqual(value['status']['state'], expected, value)
            return value

    def test_nonzero_child_never_calls_postflight(self):
        value = self.worker('Write-Output fixture; exit 9', "function Test-SetupWizardPostflight { $script:checks++; throw 'must not run' }", 'failed')
        self.assertEqual(value['calls'], 0)
        self.assertNotEqual(value['status']['exitCode'], 0)
        self.assertEqual(value['status']['phase'], 'installation-failed')

    def test_real_fixture_child_success_then_postflight_success(self):
        value = self.worker('Write-Output fixture; exit 0', "function Test-SetupWizardPostflight { $script:checks++; return @{ok=$true;checks=@(@{name='fixture';ok=$true;message='fixture'});nextSteps=@('fixture next')} }", 'succeeded')
        self.assertEqual(value['calls'], 1)
        self.assertIn('fixture next', value['status']['nextSteps'])

    def test_real_fixture_child_success_but_failed_postflight(self):
        value = self.worker('Write-Output fixture; exit 0', "function Test-SetupWizardPostflight { $script:checks++; return @{ok=$false;checks=@();nextSteps=@('fixture repair')} }", 'failed')
        self.assertGreater(value['calls'], 1)
        self.assertEqual(value['status']['phase'], 'checked')


    def test_help_command_literal_arguments_and_invalid_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "help '$ fixture.ps1"
            output = root / 'help.json'
            child.write_text('param($SchoolName,$Result,[switch]$Resume)\n@{name=$SchoolName;resume=[bool]$Resume} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $Result', encoding='utf-8-sig')
            name = "Å ' quoted \"double\" $(throw 'executed') ` ; &"
            body = '$cmd=Get-SetupWizardHelpCommand ' + quote(child) + ' ([ordered]@{SchoolName=' + quote(name) + ';Result=' + quote(output) + ';Resume=$true;NoStart=$false}); & ([scriptblock]::Create($cmd)); $result=Get-Content -Raw -Encoding UTF8 ' + quote(output) + "; $rejected=$false; try {Get-SetupWizardHelpCommand 'unused' @{'X;throw'=1} | Out-Null} catch {$rejected=$true}; @{result=($result|ConvertFrom-Json);rejected=$rejected;command=$cmd}|ConvertTo-Json -Depth 6"
            value = self.run_ps(body)
            self.assertEqual(value['result']['name'], name)
            self.assertTrue(value['result']['resume'])
            self.assertTrue(value['rejected'])
            self.assertNotIn('-NoStart', value['command'])

    def test_guidance_requires_confirmed_success(self):
        body = '$plan=Test-SetupWizardInput ' + self.values(Path('C:/fixture')) + r'''
$states=@(
 @{state='succeeded';phase='checked';message='fixture'},
 @{state='succeeded';phase='checked';exitCode=9;message='fixture'},
 @{state='succeeded';phase='installing';exitCode=0;message='fixture'},
 @{state='succeeded';phase='checked';exitCode=0;checks=@(@{ok=$false;name='fixture';message='bad'});message='fixture'}
)
@($states | ForEach-Object {Get-SetupWizardGuidance $plan $_}) | ConvertTo-Json -Depth 8
'''
        values = self.run_ps(body)
        for value in values:
            self.assertNotEqual(value['guidanceTitle'], 'Tag værkstedet i brug', value)

    def test_guidance_resume_preserves_server_choices_and_station_actions(self):
        body = '$plan=Test-SetupWizardInput ' + self.values(Path('C:/fixture')) + r'''
$plan.parameters.RemoteLaserStation=$true
$plan.parameters.Role='vm'
$status=@{state='failed';phase='installation-failed';message='fixture failure';log='fixture log'}
$server=Get-SetupWizardGuidance $plan $status
$stationPlan=[ordered]@{entry='Setup-LaserStation.ps1';parameters=[ordered]@{PackageRoot='C:\fixture';Action='Install';ConnectionFile='C:\old.json';NoStart=$true}}
$station=Get-SetupWizardGuidance $stationPlan $status
$cancel=Get-SetupWizardGuidance $stationPlan @{state='failed';phase='not-started';message='cancel'}
$checked=Get-SetupWizardGuidance $plan @{state='failed';phase='checked';message='check failed';checks=@(@{name='tls';ok=$false;message='TLS failed'})}
@{server=$server;station=$station;cancel=$cancel;checked=$checked} | ConvertTo-Json -Depth 8
'''
        value = self.run_ps(body)
        server = value['server']['technicalHelp']
        def payloads(text):
            result = []
            for encoded in re.findall(r'-EncodedCommand ([A-Za-z0-9+/=]+)', text):
                command = base64.b64decode(encoded).decode('utf-16-le')
                payload = re.search(r"FromBase64String\('([^']+)'\)", command).group(1)
                result.append(json.loads(base64.b64decode(payload)))
            return result
        server_payload = payloads(server)[0]
        expected = dict(Resume=True, RemoteLaserStation=True, Role='vm', SchoolName='Fixture', RoleIp='192.0.2.10', StudentSubnet='192.0.2.0/24')
        for key, expected_value in expected.items():
            self.assertEqual(server_payload['parameters'][key], expected_value)
        self.assertTrue(server_payload['script'].endswith('Setup-School.ps1'))
        station = value['station']['technicalHelp']
        station_payloads = payloads(station)
        self.assertEqual(len(station_payloads), 2)
        self.assertEqual(station_payloads[0]['parameters']['Action'], 'ResumeInstall')
        self.assertEqual(station_payloads[1]['parameters']['Action'], 'Update')
        self.assertTrue(station_payloads[1]['parameters']['FinishSetup'])
        for payload in station_payloads:
            self.assertTrue(payload['parameters']['NoStart'])
            self.assertNotIn('ConnectionFile', payload['parameters'])
            self.assertTrue(payload['script'].endswith('Setup-LaserStation.ps1'))
        self.assertEqual(payloads(value['cancel']['technicalHelp']), [])
        self.assertEqual(payloads(value['checked']['technicalHelp']), [])
        self.assertIn('TLS failed', value['checked']['technicalHelp'])
        self.assertIn('punkt 3', value['checked']['guidanceText'])

    def test_running_guidance_is_phase_and_component_specific(self):
        body = r'''
$server=@{entry='Setup-School.ps1'}; $station=@{entry='Setup-LaserStation.ps1'}
@{start=Get-SetupWizardGuidance $server @{state='running';phase='starting'};station=Get-SetupWizardGuidance $station @{state='running';phase='starting'};install=Get-SetupWizardGuidance $server @{state='running';phase='installing'};check=Get-SetupWizardGuidance $station @{state='running';phase='checking'}} | ConvertTo-Json -Depth 6
'''
        value = self.run_ps(body)
        self.assertIn('administratorgodkendelse', value['start']['guidanceText'])
        self.assertNotIn('administratorgodkendelse', value['station']['guidanceText'])
        self.assertIn('Hold computeren tændt', value['install']['guidanceText'])
        self.assertIn('ikke starte en ny installation', value['check']['guidanceText'])


    def test_polling_normalizes_malformed_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = '$d=' + quote(tmp) + ';$plan=Test-SetupWizardInput ' + self.values(Path('C:/fixture')) + r'''
$states=@(
 @{state='succeeded';phase='checked';message='fixture'},
 @{state='succeeded';phase='checked';exitCode=0;checks=@();message='fixture'},
 @{state='succeeded';phase='checked';exitCode=0;checks=@(@{ok=$false;name='fixture';message='bad'});message='fixture'},
 @{state='succeeded';phase='checked';exitCode='oops';checks=@(@{ok=$true});message='fixture'},
 @{state='succeeded';phase='checked';exitCode=0;checks=@($null);message='fixture'},
 @{state='succeeded';phase='checked';exitCode=0;checks=@('bad');message='fixture'}
)
$results=@(foreach($status in $states) {
 Set-SetupWizardStatus $d $status
 Get-SetupWizardRunStatus @{directory=$d;process=$null;plan=$plan}
})
$results | ConvertTo-Json -Depth 8
'''
            values = self.run_ps(body)
            for value in values:
                self.assertEqual(value['state'], 'failed', value)
                self.assertEqual(value['phase'], 'status-invalid', value)
                self.assertNotEqual(value['guidanceTitle'], 'Tag værkstedet i brug')


if __name__ == '__main__':
    unittest.main()
