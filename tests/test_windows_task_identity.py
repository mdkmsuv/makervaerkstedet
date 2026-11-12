import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'windows/inno/task_control.ps1'

def powershell(script):
    value=base64.b64encode(script.encode('utf-16le')).decode()
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',value],capture_output=True,text=True)
    if result.returncode:raise AssertionError(result.stderr[-2000:])
    return json.loads(result.stdout.strip())

class TaskIdentityTests(unittest.TestCase):
    def test_localized_account_and_sid_resolve_identically_and_system_is_distinct(self):
        result=powershell(r'''
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('__SCRIPT__',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse failure'}
$f=$ast.Find({param($a)$a -is [Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq 'Resolve-TaskPrincipalSid'},$true)
Invoke-Expression $f.Extent.Text
$name=([Security.Principal.SecurityIdentifier]::new('S-1-5-19')).Translate([Security.Principal.NTAccount]).Value
@{localized=(Resolve-TaskPrincipalSid $name);sid=(Resolve-TaskPrincipalSid 'S-1-5-19');system=(Resolve-TaskPrincipalSid 'SYSTEM')}|ConvertTo-Json -Compress
'''.replace('__SCRIPT__',str(SCRIPT).replace("'","''")))
        self.assertEqual(result['localized'],'S-1-5-19');self.assertEqual(result['sid'],'S-1-5-19');self.assertEqual(result['system'],'S-1-5-18')

    def scenario(self,enrollment_running,changed=False,legacy=False,bad_hash=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state=root/'data/ElevhubServer';state.mkdir(parents=True);program=root/'program/ElevhubServer';program.mkdir(parents=True)
            owner=str(uuid.uuid4());(state/'installation-owner.json').write_text(json.dumps({'ownerId':owner,**({'externalBackends':['print']} if legacy else {})}))
            config={'elev_adresse':'192.0.2.2','laerer_port':8780,'laerer_adresse':'127.0.0.1'};(state/'konfiguration.json').write_text(json.dumps(config));journal=state/'installer-updates'/str(uuid.uuid4());journal.mkdir(parents=True)
            if legacy:
                for name in ('server/enrollment.py','deployment/school-setup/enrollment.py'):
                    path=program/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            script=r'''
$ErrorActionPreference='Stop';$env:ProgramFiles='__PROGRAM__';$env:ProgramData='__DATA__';$root=Join-Path $env:ProgramFiles ElevhubServer;$state=Join-Path $env:ProgramData ElevhubServer
$global:events=[Collections.Generic.List[string]]::new();$global:tasks=@{}
$argsServer='-B "{0}" --package-root "{1}" --data-root "{2}" --role-ip "__ROLEIP__" --teacher-port "8780" --teacher-address "127.0.0.1"' -f (Join-Path $root 'server\bootstrap.py'),$root,$state
$argsEnroll='-B "{0}" serve --data-root "{1}"' -f (Join-Path $root '__ENROLLPATH__'),$state
$global:tasks.ElevhubServer=[pscustomobject]@{TaskName='ElevhubServer';State='Running';Description='ElevhubServer owner=__OWNER__';Settings=@{Enabled=$true};Principal=@{UserId='S-1-5-19'};Actions=@([pscustomobject]@{Execute=(Join-Path $root 'runtime\python.exe');Arguments=$argsServer;WorkingDirectory=$root})}
$global:tasks.ElevhubSchoolEnrollment=[pscustomobject]@{TaskName='ElevhubSchoolEnrollment';State='__ENROLLSTATE__';Description='Elevhub skole-enrollment';Settings=@{Enabled=__ENABLE__};Principal=@{UserId='S-1-5-18'};Actions=@([pscustomobject]@{Execute=(Join-Path $root 'runtime\python.exe');Arguments=$argsEnroll+'__CHANGED__';WorkingDirectory=$root})}
__HASHMOCK__
function Get-ScheduledTask($TaskName,$TaskPath){return $global:tasks[$TaskName]}
# Like Windows: Settings/Enabled is absent while enabled and present as false when disabled.
function Export-ScheduledTask($TaskName,$TaskPath){$settings=if($global:tasks[$TaskName].Settings.Enabled){'<Settings><Hidden>false</Hidden></Settings>'}else{"<Settings>`r`n    <Enabled>false</Enabled>`r`n    <Hidden>false</Hidden></Settings>"};return '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>'+$settings+'<Name>'+$TaskName+'</Name></Task>'}
function Disable-ScheduledTask($TaskName,$TaskPath){$global:events.Add('disable:'+$TaskName);$global:tasks[$TaskName].Settings.Enabled=$false}
function Stop-ScheduledTask($TaskName,$TaskPath){$global:events.Add('stop:'+$TaskName);$global:tasks[$TaskName].State='Ready'}
function Enable-ScheduledTask($TaskName,$TaskPath){$global:events.Add('enable:'+$TaskName);$global:tasks[$TaskName].Settings.Enabled=$true}
function Start-ScheduledTask($TaskName,$TaskPath){$global:events.Add('start:'+$TaskName);$global:tasks[$TaskName].State='Running'}
function Get-CimInstance($ClassName){return @()}
$failure=$null
try{& '__SCRIPT__' -Action Stop -Journal '__JOURNAL__';if($LASTEXITCODE -ne 0){throw 'Stop failed'};& '__SCRIPT__' -Action Restore -Journal '__JOURNAL__'}catch{$failure=$_.Exception.Message}
@{events=@($global:events);failure=$failure}|ConvertTo-Json -Compress
'''
            replacements={'__PROGRAM__':str(root/'program'),'__DATA__':str(root/'data'),'__OWNER__':owner,'__SCRIPT__':str(SCRIPT),'__JOURNAL__':str(journal),'__ENROLLSTATE__':'Running' if enrollment_running else 'Disabled','__ENABLE__':'$true' if enrollment_running else '$false','__CHANGED__':' --unexpected' if changed else '', '__ROLEIP__':'192.0.2.2','__ENROLLPATH__':r'server\enrollment.py' if legacy else r'deployment\school-setup\enrollment.py','__HASHMOCK__':("function Get-FileHash {return @{Hash='"+('0'*64 if bad_hash else 'b6a150d4db163e235d5e0cba00ea76adc68ac1bab174ae22895c9ad0e61b2ca6')+"'}}") if legacy else ''}
            for old,new in replacements.items():script=script.replace(old,new)
            return powershell(script)
    def test_running_enrollment_is_stopped_and_restored_with_server(self):
        result=self.scenario(True);self.assertIsNone(result['failure']);self.assertIn('stop:ElevhubSchoolEnrollment',result['events']);self.assertIn('start:ElevhubSchoolEnrollment',result['events']);self.assertIn('start:ElevhubServer',result['events'])
    def test_disabled_enrollment_is_not_enabled_or_started(self):
        result=self.scenario(False);self.assertIsNone(result['failure']);self.assertNotIn('start:ElevhubSchoolEnrollment',result['events']);self.assertNotIn('enable:ElevhubSchoolEnrollment',result['events'])
    def test_own_disable_does_not_block_restore(self):
        # Regression: the update disabled the tasks and then refused to restore
        # them, because the exported XML gained <Enabled>false</Enabled>.
        result=self.scenario(True);self.assertIsNone(result['failure'],result)
        self.assertEqual([e for e in result['events'] if e.startswith('start:')],['start:ElevhubServer','start:ElevhubSchoolEnrollment'])
    def test_changed_enrollment_is_rejected_before_any_task_mutation(self):
        result=self.scenario(True,True);self.assertIn('Enrollment task ownership',result['failure']);self.assertEqual(result['events'],[])

if __name__=='__main__':unittest.main()
