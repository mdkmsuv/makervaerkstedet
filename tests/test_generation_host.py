"""Guard tests: injected installation snapshots; collector launch is bounded/mocked."""
import base64
import copy
import json
import ntpath
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.generation_host import (WindowsGenerationHost, COLLECTOR, SYSTEM, SERVICE, ADMIN,
                                 USERS, READ, RX, MODIFY, _command, _canonical)
RUNTIME = r'C:\Program Files\ElevhubServer'
SOURCE = RUNTIME + r'\program'
TARGET = r'D:\prepared\program'
DATA = r'D:\prepared\data'
OLD_DATA = r'D:\source\data'
ID = 'a' * 32
GUID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'


def record(program, data, revision=1):
    return dict(format=1, installation_id=ID, revision=revision, runtime_root=RUNTIME,
                program_root=program, data_root=data, manifest_sha256='b'*64)


def rule(sid, mask, type='Allow', inherit_only=False):
    return dict(sid=sid, mask=mask, type=type, inherit_only=inherit_only)


def acl(path, service=READ, private=False):
    rules = [rule(SYSTEM, 0x1F01FF), rule(ADMIN, 0x1F01FF)]
    if not private and service:
        rules.append(rule(SERVICE, service | 0x100000))
    return dict(path=path, reparse=False, owner=ADMIN, dacl_present=True, unsupported=False, acl=rules)


def snapshot(role='vm'):
    owner = dict(schemaVersion=1,ownerId=GUID, installRoot=RUNTIME, product='ElevhubServer', state='installed',
                 startupProtocol='generation-v1', roleIp='192.0.2.10', teacherPort=8768, teacherAddress='127.0.0.1')
    tasks=[]
    for name, principal, entry, tail, description in (
        ('ElevhubServer', SERVICE, 'server', ['--role-ip','192.0.2.10','--teacher-port','8768','--teacher-address','127.0.0.1'], 'ElevhubServer owner='+GUID),
        ('ElevhubSchoolEnrollment', SYSTEM, 'enrollment', ['serve'], 'Elevhub skole-enrollment')):
        tasks.append(dict(name=name, principal=principal, logon_type='ServiceAccount', run_level='Highest', description=description,
                          actions=[dict(execute=RUNTIME+r'\runtime\python.exe', working_directory=RUNTIME,
                                        arguments=_command(RUNTIME,ID,entry,tail))]))
    paths={}
    roots = [RUNTIME,SOURCE,TARGET,OLD_DATA,DATA]
    for root in roots:
        paths[_canonical(root)]=acl(root,RX)
        parent = ntpath.dirname(root)
        while parent and parent != root:
            paths.setdefault(_canonical(parent),acl(parent,service=0))
            root,parent=parent,ntpath.dirname(parent)
    for child in ('control','control\\generation.lock','app','app\\generation_runtime.py','runtime','runtime\\python.exe'):
        path=RUNTIME+'\\'+child; paths[_canonical(path)]=acl(path,RX)
    for data in (OLD_DATA,DATA):
        readonly=('konfiguration.json','backup-protocol.json','school.json','security','security\\server.key','security\\server.crt','security\\ca.crt','security\\enheder.json','security\\enrollment','security\\enrollment\\instance.lock','security\\enrollment\\operations','security\\enrollment\\operations\\instance.lock')
        service=('instance.lock','security\\enheder.lock','logs','hub','hub\\instance.lock','printvaerksted','printvaerksted\\instance.lock','designvaerksted','designvaerksted\\instance.lock','local-ai')
        private=('installation-owner.json','admin-ssh','admin-ssh\\id_ed25519')
        for child in readonly+service+private:
            path=data+'\\'+child;paths[_canonical(path)]=acl(path,service=MODIFY if child in service else READ,private=child in private)
        if role=='makerspace':
            for child in ('laserstation','laserstation\\laser-installation.json','Laserplader','Laserplader\\instance.lock','Laserplader\\backup-protocol.json'):
                path=data+'\\'+child;paths[_canonical(path)]=acl(path,MODIFY)
    return dict(tasks=tasks,processes=[],paths=list(paths.values()),metadata=dict(laser=({name:dict(config=dict(role='Station',plates=data+r'\Laserplader'),protocol=dict(protocol=1,role='laser_station')) for name,data in (('source',OLD_DATA),('target',DATA))} if role=='makerspace' else None),owner=owner,school=dict(schemaVersion=1,role=role,school_name='Testskolen',theme='eget',server='192.0.2.10',student_subnet='192.0.2.0/24',admin_public_key='ssh-ed25519 AAAAtest'),config=dict(external_backends=[],tls=dict(certifikat=DATA+r'\security\server.crt',noegle=DATA+r'\security\server.key',ca=DATA+r'\security\ca.crt'),enhedsregister=DATA+r'\security\enheder.json')))


class GenerationHostTests(unittest.TestCase):
    def setUp(self):
        self.source=record(SOURCE,OLD_DATA)
        self.target=record(TARGET,DATA,2)
        self.value=snapshot()

    def check(self,value=None):
        return WindowsGenerationHost(lambda payload: copy.deepcopy(self.value if value is None else value)).check(RUNTIME,self.source,self.target)

    def test_real_generation_v1_vm_and_makerspace(self):
        for role in ('vm','makerspace'):
            self.assertIsNone(self.check(snapshot(role)))

    def test_laser_plate_locks_and_config_both_generations(self):
        for name, data in (('source', OLD_DATA), ('target', DATA)):
            for child in ('Laserplader\\instance.lock', 'Laserplader\\backup-protocol.json'):
                changed = snapshot('makerspace')
                changed['paths'] = [p for p in changed['paths'] if _canonical(p['path']) != _canonical(data + '\\' + child)]
                with self.assertRaises(ValueError): self.check(changed)
            changed = snapshot('makerspace'); changed['metadata']['laser'][name]['config']['plates'] = r'C:\external\Laserplader'
            with self.assertRaises(ValueError): self.check(changed)
            changed = snapshot('makerspace'); changed['metadata']['laser'][name]['protocol']['role'] = 'server'
            with self.assertRaises(ValueError): self.check(changed)

    def test_public_runtime_boundary_accepts_path(self):
        self.assertIsNone(WindowsGenerationHost(lambda _:self.value).check(Path(RUNTIME),self.source,self.target))

    def test_format_and_revision_validation(self):
        for key,value in [('format',True),('revision',True),('revision',0),('revision',2**63)]:
            with self.subTest(key=key,value=value):
                original=self.target.copy();self.target[key]=value
                with self.assertRaises(ValueError):self.check()
                self.target=original

    def test_exact_tasks(self):
        mutations=[lambda t:t.update(principal=ADMIN),lambda t:t.update(description='ElevhubServer owner='+ID),
                   lambda t:t['actions'].append(t['actions'][0].copy()),
                   lambda t:t['actions'][0].update(arguments=t['actions'][0]['arguments']+' "--expected-selection" "'+ 'b'*64+'"'),
                   lambda t:t['actions'][0].update(arguments=t['actions'][0]['arguments'].replace('generation_runtime.py','evil_generation_runtime.py')),
                   lambda t:t['actions'][0].update(arguments=t['actions'][0]['arguments'].replace('192.0.2.10','192.0.2.11')),
                   lambda t:t.update(logon_type='Interactive')]
        for mutation in mutations:
            changed=copy.deepcopy(self.value);mutation(changed['tasks'][0])
            with self.assertRaises(ValueError):self.check(changed)

    def test_acl_privilege_policy(self):
        mutations=[lambda a:a['acl'].append(rule(USERS,MODIFY)),
                   lambda a:a['acl'].append(rule('S-1-5-21-10-11-12-1000',MODIFY)),
                   lambda a:a['acl'].append(rule(SERVICE,READ,type='Deny')),
                   lambda a:a.update(dacl_present=False),lambda a:a.update(unsupported=True),
                   lambda a:a.update(owner=USERS),lambda a:a['acl'][-1].update(mask=0x100000),
                   lambda a:a.update(reparse=True)]
        for mutation in mutations:
            changed=copy.deepcopy(self.value);mutation(changed['paths'][0])
            with self.assertRaises(ValueError):self.check(changed)

    def test_child_override_and_parent_delete_child(self):
        for path in (TARGET+r'\app\evil.py',r'D:\prepared'):
            changed=copy.deepcopy(self.value)
            info=next((p for p in changed['paths'] if _canonical(p['path'])==_canonical(path)),None)
            if info is None:info=acl(path);changed['paths'].append(info)
            info['acl'].append(rule(USERS,0x40))
            with self.assertRaises(ValueError):self.check(changed)

    def test_service_created_data_owner_only_where_service_is_allowed_writer(self):
        for child in ('hub\\instance.lock', 'printvaerksted\\instance.lock', 'logs'):
            changed=copy.deepcopy(self.value)
            next(p for p in changed['paths'] if _canonical(p['path'])==_canonical(DATA+'\\'+child))['owner']=SERVICE
            self.assertIsNone(self.check(changed))
        for path in (TARGET, RUNTIME+r'\control', DATA+r'\konfiguration.json',
                     DATA+r'\installation-owner.json', DATA+r'\admin-ssh\id_ed25519',
                     DATA+r'\security\server.key', DATA+r'\security\enrollment'):
            changed=copy.deepcopy(self.value)
            next(p for p in changed['paths'] if _canonical(p['path'])==_canonical(path))['owner']=SERVICE
            with self.assertRaises(ValueError):self.check(changed)

    @unittest.skipUnless(os.name == 'nt', 'requires Windows argv parser')
    def test_only_fixed_literal_operator_wrappers_are_optional_process_exception(self):
        from app.generation_host import _maintenance_wrapper
        system = os.environ['SystemRoot']
        executable = ntpath.join(system, r'System32\WindowsPowerShell\v1.0\powershell.exe')
        script = RUNTIME+r'\deployment\school-setup\Maintain-School.ps1'
        args = [executable, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script,
                '-Action', 'resume', '-RuntimeRoot', RUNTIME, '-Folder', r'D:\School maintenance\operation']
        row = dict(name='powershell.exe', path=executable,
                   command_line=subprocess.list2cmdline(args), pid=os.getpid()+1)
        self.assertTrue(_maintenance_wrapper(row, _canonical(RUNTIME)))
        changed = copy.deepcopy(self.value); changed['processes'] = [row]
        with self.assertRaises(ValueError):self.check(changed)
        self.assertIsNone(WindowsGenerationHost(lambda _:changed, allow_maintenance_wrapper=True).check(RUNTIME,self.source,self.target))
        for values in (args+['-Command', 'Remove-Item'],
                       [executable,'-NoProfile','-EncodedCommand','anything'],
                       [executable,'-NoProfile','-File',r'D:\Foreign\Maintain-School.ps1'],
                       [executable,'-File',script],
                       args+['-Action','cancel'],
                       args+['-Unknown','anything']):
            invalid=dict(row,command_line=subprocess.list2cmdline(values))
            self.assertFalse(_maintenance_wrapper(invalid,RUNTIME))
        invalid=dict(row,path=r'C:\Foreign\powershell.exe')
        self.assertFalse(_maintenance_wrapper(invalid,RUNTIME))
        cmd = ntpath.join(system, r'System32\cmd.exe')
        cmd_script = RUNTIME+r'\deployment\school-setup\Maintain-School.cmd'
        for command in ('"'+cmd+'" /c ""'+cmd_script+'" "',
                        '"'+cmd+'" /c "'+cmd_script+'" -Action status -Folder D:\\operation'):
            self.assertTrue(_maintenance_wrapper(dict(row,name='cmd.exe',path=cmd,command_line=command),RUNTIME),command)
        for command in ('"'+cmd+'" /c "'+cmd_script+'" & echo unsafe',
                        '"'+cmd+'" /c "'+cmd_script+'" -Action status -Folder %TEMP%',
                        '"'+cmd+'" /c "'+cmd_script+'" -Unknown value'):
            self.assertFalse(_maintenance_wrapper(dict(row,name='cmd.exe',path=cmd,command_line=command),RUNTIME))

    def test_parent_sibling_creation_does_not_grant_child_replacement(self):
        changed=copy.deepcopy(self.value)
        next(p for p in changed['paths'] if _canonical(p['path'])==_canonical(r'D:\\prepared'))['acl'].append(rule('S-1-5-11',4))
        self.assertIsNone(self.check(changed))

    @unittest.skipUnless(os.name == 'nt', 'requires Windows argv parser')
    def test_school_menu_parent_is_literal_owned_wrapper_for_tls_maintenance(self):
        from app.generation_host import _maintenance_wrapper
        system = os.environ['SystemRoot']
        executable = ntpath.join(system, r'System32\WindowsPowerShell\v1.0\powershell.exe')
        script = RUNTIME + r'\deployment\school-setup\Manage-School.ps1'
        args = [executable, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', script, '-PackageRoot', RUNTIME, '-DataRoot', DATA]
        row = dict(name='powershell.exe', path=executable,
                   command_line=subprocess.list2cmdline(args), pid=os.getpid()+1)
        self.assertTrue(_maintenance_wrapper(row, _canonical(RUNTIME)))
        for extra in (['-Command', 'unsafe'], ['-Unknown', 'value'], ['-PackageRoot', r'D:\foreign']):
            self.assertFalse(_maintenance_wrapper(dict(row, command_line=subprocess.list2cmdline(args + extra)), RUNTIME))
        changed = copy.deepcopy(self.value)
        changed['processes'] = [row]
        self.assertIsNone(WindowsGenerationHost(lambda _: changed, allow_maintenance_wrapper=True).check(
            RUNTIME, self.source, self.target))
        cmd = ntpath.join(system, r'System32\cmd.exe')
        cmd_script = RUNTIME + r'\deployment\school-setup\Manage-School.cmd'
        self.assertTrue(_maintenance_wrapper(dict(row, name='cmd.exe', path=cmd,
            command_line='"' + cmd + '" /c ""' + cmd_script + '" "'), RUNTIME))

    def test_private_key_reader_and_missing_locks(self):
        for child in ('admin-ssh\\id_ed25519','instance.lock','hub\\instance.lock','konfiguration.json','security\\enrollment\\operations\\instance.lock'):
            changed=copy.deepcopy(self.value);path=_canonical(DATA+'\\'+child)
            changed['paths']=[p for p in changed['paths'] if _canonical(p['path']) != path]
            with self.assertRaises(ValueError):self.check(changed)
        changed=copy.deepcopy(self.value)
        next(p for p in changed['paths'] if p['path']==DATA+r'\admin-ssh\id_ed25519')['acl'].append(rule(SERVICE,READ))
        with self.assertRaises(ValueError):self.check(changed)

    def test_unknown_layout_and_external_backend(self):
        for key,value in [('school',dict(schemaVersion=1,role='unknown')),('config',dict(external_backends=['print']))]:
            changed=copy.deepcopy(self.value);changed['metadata'][key]=value
            with self.assertRaises(ValueError):self.check(changed)

    def test_retained_ca_and_tls_journals_are_admin_only_in_both_generations(self):
        for root in (OLD_DATA, DATA):
            for name in ('ca.key', 'tls-initialization.json', 'tls-initialization.json.tmp',
                         'tls-renewal.json', '.tls-renewal.json.owned.tmp',
                         'tls-renewal-receipt.json', '.tls-renewal-receipt.json.owned.tmp'):
                with self.subTest(root=root, name=name):
                    changed = copy.deepcopy(self.value)
                    info = acl(root + '\\security\\' + name, private=True)
                    changed['paths'].append(info)
                    self.assertIsNone(self.check(changed))
                    info['acl'].append(rule(SERVICE, READ))
                    with self.assertRaises(ValueError):
                        self.check(changed)

    def test_process_failclosed_and_only_own_pid_exempt(self):
        processes=[('Beam Studio.exe',r'C:\outside\Beam Studio.exe','beam'),('flux_api.exe',r'C:\other\flux_api.exe','flux'),
                   ('ghost.exe',r'C:\other\ghost.exe','ghost'),('python.exe','',''),
                   ('python.exe',r'C:\Python\python.exe','python "'+TARGET+r'\main.py"'),
                   ('worker.exe',DATA+r'\worker.exe','worker')]
        for name,path,command in processes:
            changed=copy.deepcopy(self.value);changed['processes']=[dict(name=name,path=path,command_line=command,pid=os.getpid()+1)]
            with self.assertRaises(ValueError):self.check(changed)
            changed['processes'][0]['pid']=os.getpid();self.assertIsNone(self.check(changed))

    def test_non_windows_collection_does_not_touch_host(self):
        with patch('app.generation_host.os.name','posix'),patch('app.generation_host.subprocess.run') as run:
            with self.assertRaises(RuntimeError):WindowsGenerationHost().snapshot(RUNTIME,self.source,self.target)
            run.assert_not_called()

    def test_collector_launch_is_fixed_encoded_hidden_and_utf8(self):
        result=subprocess.CompletedProcess([],0,json.dumps(self.value),'')
        with patch('app.generation_host.os.name','nt'),patch.dict(os.environ,{'SystemRoot':r'C:\Windows'}),patch('app.generation_host.subprocess.run',return_value=result) as run:
            self.assertEqual(WindowsGenerationHost().snapshot(RUNTIME,self.source,self.target),self.value)
        args,kwargs=run.call_args
        self.assertEqual(args[0][:4],[r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand'])
        self.assertEqual(base64.b64decode(args[0][4]).decode('utf-16le'),COLLECTOR)
        self.assertEqual(kwargs['creationflags'],subprocess.CREATE_NO_WINDOW)
        self.assertEqual(kwargs['encoding'],'utf-8-sig')
        self.assertEqual(json.loads(base64.b64decode(kwargs['env']['GENERATION_HOST_INPUT']))['target_record'],self.target)
        self.assertIn('GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])',COLLECTOR)
        self.assertIn('Get-ChildItem -LiteralPath $folder -Force',COLLECTOR)

    @unittest.skipUnless(os.name=='nt','PowerShell descriptor probe requires Windows')
    def test_collector_pathinfo_on_temporary_file_only(self):
        functions=COLLECTOR[COLLECTOR.index('function Sid'):COLLECTOR.index('$roots =')]
        with tempfile.TemporaryDirectory() as directory:
            probe=Path(directory)/'probe.txt';probe.write_text('probe')
            script="$ErrorActionPreference='Stop'\n$tokens=$null; $errors=$null; $null=[Management.Automation.Language.Parser]::ParseInput([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:GENERATION_TEST_SCRIPT)),[ref]$tokens,[ref]$errors); if ($errors.Count) { throw ($errors | Out-String) }\n"+functions+"\nPathInfo $env:GENERATION_TEST_PROBE | ConvertTo-Json -Depth 10 -Compress"
            env=os.environ.copy();env['GENERATION_TEST_PROBE']=str(probe);env['GENERATION_TEST_SCRIPT']=base64.b64encode(COLLECTOR.encode()).decode()
            env['PSModulePath']=ntpath.join(next(v for k,v in env.items() if k.lower()=='systemroot'),r'System32\WindowsPowerShell\v1.0\Modules')
            result=subprocess.run([ntpath.join(next(v for k,v in env.items() if k.lower()=='systemroot'),r'System32\WindowsPowerShell\v1.0\powershell.exe'),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],env=env,capture_output=True,text=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode,0,result.stderr)
            info=json.loads(result.stdout);self.assertFalse(info['reparse']);self.assertTrue(info['dacl_present'])
            self.assertTrue(all(r['sid'].startswith('S-1-') and isinstance(r['mask'],int) for r in info['acl']))

if __name__=='__main__':unittest.main()
