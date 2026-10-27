"""Explicit addresses and retained legacy metadata; no live installation APIs."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER=Path(__file__).resolve().parents[1]
PROJECT=SERVER.parents[1]
PS=shutil.which('powershell.exe') or shutil.which('pwsh')
ADDRESS='198.51.100.42'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module); return module

bootstrap=load('privacy_server_bootstrap',SERVER/'bootstrap.py')
provision=load('privacy_server_provision',SERVER/'provision.py')
benchmark=load('privacy_server_benchmark',SERVER/'benchmark.py')

HARNESS=r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$case=Get-Content -LiteralPath $env:ROLE_CASE -Raw -Encoding UTF8|ConvertFrom-Json
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:ROLE_SCRIPT,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Source parse error'}
$guards=[Management.Automation.Language.Parser]::ParseFile((Join-Path (Split-Path -Parent $env:ROLE_SCRIPT) 'school-uninstall-guards.ps1'),[ref]$tokens,[ref]$errors)
foreach($function in @($guards.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})){Invoke-Expression $function.Extent.Text}
foreach($name in @('Get-InstalledRoleIp','Get-InstalledStudentSubnet')) {
 $node=@($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $name})
 if($node.Count -ne 1){throw 'Missing metadata resolver'}
 Invoke-Expression $node[0].Extent.Text
}
$DataRoot=Join-Path $env:ROLE_ROOT 'data'; $InstallRoot=Join-Path $env:ROLE_ROOT 'program'
New-Item -ItemType Directory -Path $DataRoot|Out-Null
$owner=$case.owner
if($null -ne $case.runtime){[IO.File]::WriteAllText((Join-Path $DataRoot 'konfiguration.json'),($case.runtime|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))}
if($null -ne $case.school){[IO.File]::WriteAllText((Join-Path $DataRoot 'school.json'),($case.school|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))}
try {
 if($case.mode -eq 'new' -or $case.mode -eq 'resume') {
  $RoleIp=''; $Resume=$case.mode -eq 'resume'
  if($Resume){
   foreach($pair in @(@{name='schemaVersion';value=1},@{name='product';value='ElevhubServer'},@{name='installRoot';value=$InstallRoot},@{name='dataRoot';value=$DataRoot})){
    $owner|Add-Member -NotePropertyName $pair.name -NotePropertyValue $pair.value -Force
   }
   [IO.File]::WriteAllText((Join-Path $DataRoot 'installation-owner.json'),($owner|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
  }
  $block=@($ast.EndBlock.Statements|Where-Object {$_.Extent.Text.StartsWith('if ([string]::IsNullOrWhiteSpace($RoleIp))')})
  if($block.Count -ne 1){throw 'Missing explicit install role check'}
  Invoke-Expression $block[0].Extent.Text
  $result=$RoleIp
 } elseif($case.mode -eq 'network') {
  $ip=Get-InstalledRoleIp $owner $DataRoot
  $result=Get-InstalledStudentSubnet $owner $DataRoot $ip
 } else { $result=Get-InstalledRoleIp $owner $DataRoot }
 @{ok=$true;value=$result}|ConvertTo-Json -Compress
} catch { @{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress }
'''

class RoleSelectionPrivacyTests(unittest.TestCase):
    def test_template_is_documentation_only_not_a_runtime_default(self):
        self.assertEqual(bootstrap.ROLE_IP,'192.0.2.2')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in ('app','web','hub','server','runtime','data'): (root/name).mkdir()
            for name in ('runtime/python.exe','hub/elevhub.py','server/provision.py'): (root/name).write_bytes(b'fixture')
            shutil.copy2(SERVER/'hub/konfiguration.windows.json',root/'hub/konfiguration.windows.json')
            self.assertIsNone(bootstrap.check(root,root/'data')['roleIp'])
            with self.assertRaises(ValueError): bootstrap.resolve_role_ip(root/'data')
            self.assertEqual(bootstrap.check(root,root/'data',role_ip=ADDRESS)['roleIp'],ADDRESS)

    def test_legacy_startup_resolves_only_stored_config_and_refuses_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); config=root/'konfiguration.json'; owner=root/'installation-owner.json'
            config.write_text(json.dumps({'elev_adresse':ADDRESS}))
            before=config.read_bytes()
            self.assertEqual(bootstrap.resolve_role_ip(root),ADDRESS)
            owner.write_text(json.dumps({'roleIp':'203.0.113.15'}))
            with self.assertRaises(ValueError): bootstrap.resolve_role_ip(root)
            with self.assertRaises(ValueError): bootstrap.resolve_role_ip(root,ADDRESS)
            self.assertEqual(config.read_bytes(),before)

    def test_unresolved_invalid_and_redirected_runtime_addresses_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); config=root/'konfiguration.json'
            for value in (None,'0.0.0.0','127.0.0.1','::1','224.0.0.1','school.invalid'):
                config.write_text(json.dumps({'elev_adresse':value}))
                with self.subTest(value=value),self.assertRaises(ValueError): bootstrap.resolve_role_ip(root)
            config.write_text(json.dumps({'elev_adresse':ADDRESS}))
            with patch.object(bootstrap,'_reparse',side_effect=lambda p:Path(p)==config):
                with self.assertRaises(ValueError): bootstrap.resolve_role_ip(root)

    def test_provisioning_and_benchmark_cli_require_selected_address_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            cases=[(provision,['init-tls','--output-dir',str(root/'security')]),
                (provision,['renew-tls','--output-dir',str(root/'security')]),
                (provision,['enroll','--registry',str(root/'enheder.json'),'--name','fixture-01',
                    '--ca-certificate',str(root/'ca.crt'),'--output',str(root/'enrollment.json')]),
                (benchmark,['--install-root',str(root),'--data-root',str(root),'--check-only'])]
            for module,args in cases:
                with self.subTest(args=args),patch.object(sys,'argv',['fixture',*args]),contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error: module.main()
                    self.assertEqual(error.exception.code,2)
            self.assertEqual(list(root.iterdir()),[])

    @unittest.skipUnless(PS,'Native PowerShell AST fixture requires PowerShell')
    def test_actual_powershell_resolvers_cover_legacy_metadata_and_ambiguity(self):
        cases=[
            ({'mode':'address','owner':{'roleIp':ADDRESS}},True,ADDRESS),
            ({'mode':'address','owner':{},'runtime':{'elev_adresse':ADDRESS}},True,ADDRESS),
            ({'mode':'address','owner':{'roleIp':ADDRESS},'runtime':{'elev_adresse':'203.0.113.15'}},False,None),
            ({'mode':'address','owner':{}},False,None),
            ({'mode':'address','owner':{'roleIp':'127.0.0.1'}},False,None),
            ({'mode':'network','owner':{'roleIp':ADDRESS,'roleNetwork':'198.51.100.0/24'}},True,'198.51.100.0/24'),
            ({'mode':'network','owner':{'roleIp':ADDRESS}},False,None),
            ({'mode':'network','owner':{},'runtime':{'elev_adresse':ADDRESS},'school':{
                'schemaVersion':1,'server':ADDRESS,'student_subnet':'198.51.100.0/24'}},True,'198.51.100.0/24'),
            ({'mode':'network','owner':{},'runtime':{'elev_adresse':ADDRESS},'school':{
                'schemaVersion':1,'server':'203.0.113.15','student_subnet':'198.51.100.0/24'}},False,None)]
        for name in ('install.ps1','fjern.ps1'):
            for case,ok,value in cases:
                with self.subTest(script=name,case=case):
                    result=self.ps_case(name,case); self.assertEqual(result['ok'],ok,result)
                    if ok: self.assertEqual(result['value'],value)

    @unittest.skipUnless(PS,'Native PowerShell AST fixture requires PowerShell')
    def test_real_installer_branch_requires_new_choice_and_resolves_resume(self):
        for case,ok,value in [({'mode':'new','owner':{}},False,None),
            ({'mode':'resume','owner':{},'runtime':{'elev_adresse':ADDRESS}},True,ADDRESS),
            ({'mode':'resume','owner':{'roleIp':ADDRESS},'runtime':{'elev_adresse':'203.0.113.15'}},False,None),
            ({'mode':'resume','owner':{}},False,None)]:
            with self.subTest(case=case):
                result=self.ps_case('install.ps1',case); self.assertEqual(result['ok'],ok,result)
                if ok: self.assertEqual(result['value'],value)

    @unittest.skipUnless(PS,'Native PowerShell AST fixture requires PowerShell')
    def test_legacy_resume_without_marker_role_keeps_existing_config_and_keys(self):
        from test_installer_resume_behavior import InstallerResumeBehaviorTest
        fixture=InstallerResumeBehaviorTest('test_replay_retains_owner_tls_queue_and_exact_configuration')
        fixture.run_fixture(r'''
$Resume=[Management.Automation.SwitchParameter]::new($false); Invoke-Expression $configure
$runtimeConfig=Join-Path $DataRoot 'konfiguration.json'
$configHash=(Get-FileHash -LiteralPath $runtimeConfig).Hash
$keyHash=(Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash
$saved=Get-Content -LiteralPath $ownerMarker -Raw -Encoding UTF8|ConvertFrom-Json
$saved.PSObject.Properties.Remove('roleIp'); Write-JsonAtomic $ownerMarker $saved
$Resume=[Management.Automation.SwitchParameter]::new($true)
Invoke-Expression $preflight; Invoke-Expression $prepareMarker
Assert ($marker.roleIp -eq $RoleIp) 'Resume did not retain resolved installed address.'
Assert ((Get-FileHash -LiteralPath $runtimeConfig).Hash -eq $configHash) 'Legacy resume changed config.'
Assert ((Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash -eq $keyHash) 'Legacy resume changed TLS key.'
''')

    def ps_case(self,name,case):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); script=root/'harness.ps1'; data=root/'case.json'
            script.write_text(HARNESS,encoding='utf-8-sig')
            data.write_text(json.dumps({'runtime':None,'school':None,**case}),encoding='utf-8')
            env=os.environ.copy(); env.update(ROLE_CASE=str(data),ROLE_ROOT=str(root),ROLE_SCRIPT=str(SERVER/name))
            result=subprocess.run([PS,'-NoProfile','-NonInteractive','-Command',HARNESS],env=env,
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return json.loads(result.stdout.strip())

if __name__=='__main__': unittest.main(verbosity=2)
