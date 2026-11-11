"""Actual Windows DACL probe on synthetic temporary folders; no installation."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='nt','requires Windows DACL APIs')
class PublicCacheAclTests(unittest.TestCase):
    def test_only_fixed_cache_gains_localservice_modify_and_links_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='mkv-cache-acl-') as directory:
            root=Path(directory)
            script=root/'probe.ps1'
            script.write_text(r'''
param([string]$Helper,[string]$Root)
$ErrorActionPreference='Stop'
. $Helper
$data=Join-Path $Root 'data'; New-Item -ItemType Directory -Path $data|Out-Null
$security=Join-Path $data 'security'; New-Item -ItemType Directory -Path $security|Out-Null
$config=Join-Path $data 'konfiguration.json'; [IO.File]::WriteAllText($config,'synthetic')
$keys=Join-Path $security 'key.fixture'; [IO.File]::WriteAllText($keys,'synthetic')
$cache=Join-Path $data 'public-apt-cache'; New-Item -ItemType Directory -Path $cache|Out-Null
$fixtureSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$saved=@{}; foreach($p in @($data,$security,$config,$keys)){$saved[$p]=(Get-Acl -LiteralPath $p).Sddl}
try {
 Set-PublicAptCacheAcl $data
 $acl=[IO.Directory]::GetAccessControl($cache)
 if(-not $acl.AreAccessRulesProtected){throw 'Cache inherits broad parent grants'}
 $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
 if($rules.Count -ne 3){throw 'Cache has unexpected DACL entries'}
 foreach($pair in @(@('S-1-5-18','FullControl'),@('S-1-5-32-544','FullControl'),@('S-1-5-19','Modify'))){
  $rule=@($rules|Where-Object{$_.IdentityReference.Value -eq $pair[0]})
  if($rule.Count -ne 1 -or [int]$rule[0].FileSystemRights -ne ([int][Security.AccessControl.FileSystemRights]$pair[1] -bor [int][Security.AccessControl.FileSystemRights]::Synchronize) -or $rule[0].AccessControlType -ne 'Allow' -or $rule[0].InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'){throw 'Wrong fixed cache grant'}
 }
 foreach($p in $saved.Keys){if((Get-Acl -LiteralPath $p).Sddl -cne $saved[$p]){throw 'Private or parent DACL changed'}}
} finally {
 # Cleanup only: give this fixture's owner access to remove the synthetic tree.
 & "$env:SystemRoot\System32\icacls.exe" $cache /grant:r "*$($fixtureSid):(OI)(CI)F" | Out-Null
 if($LASTEXITCODE -ne 0){throw 'Synthetic cache cleanup access could not be restored'}
}
$linked=Join-Path $Root 'linked'; New-Item -ItemType Directory -Path $linked|Out-Null
$junction=Join-Path $linked 'public-apt-cache'
New-Item -ItemType Junction -Path $junction -Target $security|Out-Null
try {
 $rejected=$false
 try {Set-PublicAptCacheAcl $linked} catch {$rejected=$true}
 if(-not $rejected){throw 'Linked cache accepted'}
 if((Get-Acl -LiteralPath $security).Sddl -cne $saved[$security]){throw 'Link target DACL changed'}
} finally {
 # Python's TemporaryDirectory removes this synthetic junction without walking
 # its target (supported by Windows shutil.rmtree since Python 3.8).
}
Write-Output 'PASS: fixed cache DACL; private/parent DACLs preserved; junction rejected'
''',encoding='utf-8')
            environment=dict(os.environ)
            system_root=Path(os.environ.get('SystemRoot','C:/Windows'))
            environment['PSModulePath']=str(system_root/'System32/WindowsPowerShell/v1.0/Modules')
            powershell=system_root/'System32/WindowsPowerShell/v1.0/powershell.exe'
            result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),
                '-Helper',str(ROOT/'windows-elevhub/server/public-apt-cache-acl.ps1'),'-Root',str(root)],
                env=environment,text=True,capture_output=True,timeout=25)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS: fixed cache DACL',result.stdout)

    def test_install_and_upgrade_call_same_fixed_cache_helper(self):
        install=(ROOT/'windows-elevhub/server/install.ps1').read_text('utf-8')
        upgrade=(ROOT/'windows/inno/task_control.ps1').read_text('utf-8')
        self.assertIn('Set-PublicAptCacheAcl $DataRoot',install)
        self.assertIn("Set-PublicAptCacheAcl (Join-Path $env:ProgramData 'ElevhubServer')",upgrade)


if __name__=='__main__':unittest.main()
