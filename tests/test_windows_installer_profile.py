"""Synthetic installer identity/profile and actual SQLite snapshot invariants."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('profile_lifecycle',source_path('windows/inno/lifecycle.py', ROOT))
life=importlib.util.module_from_spec(spec);spec.loader.exec_module(life)


class InstallerProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.program=self.root/'program';self.state=self.root/'data'

    def known_server(self):
        self.program.mkdir();self.state.mkdir()
        owner={'schemaVersion':1,'state':'installed','product':'ElevhubServer','taskName':'ElevhubServer',
               'ownerId':str(uuid.uuid4()),'installRoot':str(self.program),'dataRoot':str(self.state)}
        (self.state/'installation-owner.json').write_text(json.dumps(owner),encoding='utf-8')
        (self.state/'konfiguration.json').write_text('{}',encoding='utf-8')
        return owner

    @unittest.skipUnless(os.name == 'nt', 'Windows native PowerShell boundary')
    def test_native_script_error_is_readable_and_arguments_remain_literal(self):
        folder = self.root / "QA's [script]"
        folder.mkdir()
        script = folder / 'fail.ps1'
        script.write_text("param([string]$OutputFile,[string]$Value,[switch]$RequireBound)\n"
                          "if(-not $RequireBound){throw 'Required switch was not bound.'}\n"
                          "[IO.File]::WriteAllText($OutputFile,$Value,[Text.UTF8Encoding]::new($false))\n"
                          "throw 'Skolens opsætning mangler. Prøv igen.'\n", encoding='utf-8-sig')
        output = folder / 'received.txt'
        value = "literal '; throw 'unexpected code' ; $(Get-Date)"
        native = life.NativeWindows.__new__(life.NativeWindows)
        native.powershell = Path(shutil.which('powershell.exe'))
        original_run = subprocess.run
        def capture(*args, **kwargs):
            kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
            return original_run(*args, **kwargs)
        with patch.object(life.subprocess, 'run', side_effect=capture), self.assertRaises(subprocess.CalledProcessError) as failure:
            native.command(script, '-OutputFile', output, '-Value', value, '-RequireBound')
        self.assertEqual(output.read_text(encoding='utf-8'), value)
        self.assertIn('MKV_INSTALL_ERROR=Skolens opsætning mangler. Prøv igen.', failure.exception.stderr)

    def test_empty_roots_install_and_known_identity_updates_without_mutation(self):
        self.assertEqual(life.detect_action('server',self.program,self.state),'install')
        self.assertFalse(self.program.exists());self.assertFalse(self.state.exists())
        self.known_server();before=life.tree(self.state)
        self.assertEqual(life.detect_action('server',self.program,self.state),'update')
        self.assertEqual(life.tree(self.state),before)
        self.assertFalse((self.state/'installer-updates').exists())

    def test_partial_unknown_wrong_owner_or_generation_layout_is_preserved(self):
        self.program.mkdir()
        with self.assertRaisesRegex(life.LifecycleError,'kunne ikke genkendes'):life.detect_action('server',self.program,self.state)
        self.program.rmdir();owner=self.known_server()
        for field,value in [('dataRoot',str(self.root/'elsewhere')),('state','installing'),('ownerId','not-an-owner')]:
            changed=dict(owner,**{field:value});(self.state/'installation-owner.json').write_text(json.dumps(changed))
            before=life.tree(self.state)
            with self.assertRaisesRegex(life.LifecycleError,'kunne ikke genkendes'):life.detect_action('server',self.program,self.state)
            self.assertEqual(life.tree(self.state),before)
        (self.state/'installation-owner.json').write_text(json.dumps(owner));(self.program/'control').mkdir()
        with self.assertRaisesRegex(life.LifecycleError,'kunne ikke genkendes'):life.detect_action('server',self.program,self.state)

    def test_all_supported_profile_choices_remain_explicit_and_bad_profile_is_rejected(self):
        value={'school_name':'En anden skole ÆØÅ','server_mode':'vm','role_ip':'192.0.2.2',
               'student_subnet':'192.0.2.0/24','theme':'red','machine':'fbb2'}
        for mode in ('vm','makerspace'):
            for theme in ('red','yellow','green'):
                for machine in ('fbb2','fhexa1'):
                    life.validate_server_profile(dict(value,server_mode=mode,theme=theme,machine=machine))
        for field,bad in [('school_name',''),('machine','unknown'),('server_mode','admin'),('theme','blue'),('role_ip','not-ip')]:
            with self.assertRaises(life.LifecycleError):life.validate_server_profile(dict(value,**{field:bad}))

    def test_private_colours_are_optional_paired_and_readable(self):
        value={'school_name':'Prøveskolen','server_mode':'makerspace','role_ip':'192.0.2.2',
               'student_subnet':'192.0.2.0/24','theme':'green','machine':'fbb2'}
        colours={'brand':'#1f5a8c','brand_dark':'#143c5e'}
        self.assertEqual(life.validate_server_profile(dict(value,**colours)),dict(value,**colours))
        for bad,fragment in [({'brand':'#1f5a8c'},'både'),({'brand_dark':'#143c5e'},'både'),
                             ({'brand':'#1F5A8C','brand_dark':'#143c5e'},'#rrggbb'),
                             ({'brand':'#ffd400','brand_dark':'#143c5e'},'for lys'),
                             ({'brand':'#1f5a8c','brand_dark':'#999999'},'5,3:1'),
                             ({'brand':'','brand_dark':''},'#rrggbb')]:
            with self.subTest(bad=bad),self.assertRaisesRegex(life.LifecycleError,fragment):
                life.validate_server_profile(dict(value,**bad))
        with self.assertRaises(life.LifecycleError):life.validate_server_profile(dict(value,logo='x.png'))

    def test_install_passes_colours_to_setup_school_only_when_present(self):
        value={'school_name':'Prøveskolen','server_mode':'makerspace','role_ip':'192.0.2.2',
               'student_subnet':'192.0.2.0/24','theme':'green','machine':'fbb2'}
        for profile,expected in [(value,None),(dict(value,brand='#1f5a8c',brand_dark='#143c5e'),['-Brand','#1f5a8c','-BrandDark','#143c5e'])]:
            with self.subTest(profile=profile):
                native=life.NativeWindows.__new__(life.NativeWindows)
                native.program=self.root/'absent-program';native.state=self.root/'absent-data'
                calls=[]
                native.command=lambda script,*arguments:calls.append(list(arguments))
                native.health=lambda:None
                with patch.object(life.Path,'is_file',return_value=True):
                    native.install_server(self.root/'package',profile)
                arguments=calls[0]
                self.assertEqual(arguments[arguments.index('-Theme')+1],'green')
                if expected is None:self.assertNotIn('-Brand',arguments);self.assertNotIn('-BrandDark',arguments)
                else:self.assertEqual(arguments[arguments.index('-Brand'):arguments.index('-Brand')+4],expected)

    def test_guide_uses_auto_action_danish_labels_and_explicit_profile_pages(self):
        recipe=(source_path('windows/inno/Makervaerkstedet.iss', ROOT)).read_text('utf-8')
        self.assertNotIn('ModePage',recipe)
        self.assertIn("+ 'auto' +",recipe)
        for text in ('Skolens opsætning','Rød','Grøn','forbliver pauset','RolePage','ThemePage','MachinePage'):
            self.assertIn(text,recipe)
        self.assertNotIn('koepause',recipe)
        # Private colours come from command-line parameters, never from a wizard page.
        self.assertIn("{param:BRAND|}",recipe);self.assertIn("{param:BRANDDARK|}",recipe)
        self.assertNotIn('BrandPage',recipe)

    def test_sqlite_backups_include_committed_wal_and_never_restore_later_data(self):
        self.state.mkdir();hub=self.state/'hub';hub.mkdir()
        path=hub/'makerspace.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''CREATE TABLE objects(type,id,version,body);
                CREATE TABLE workflow_versions(id,revision,body);
                CREATE TABLE requests(actor,request_id,digest,response);
                CREATE TABLE audit(sequence,at,actor,action,target,request_id,outcome);
                PRAGMA user_version=1;''')
        control=sqlite3.connect(hub/'simple-updates.sqlite3');self.addCleanup(control.close)
        control.execute('PRAGMA journal_mode=WAL');control.execute('CREATE TABLE revision(value)')
        control.execute('INSERT INTO revision VALUES(7)');control.commit()
        destination=self.root/'manual'
        result=life.manual_database_backup(self.state,destination,external_components=frozenset(('print','design')))
        self.assertFalse(result['automatic_restore']);self.assertEqual(result['purpose'],'manual_recovery_only')
        self.assertEqual(set(result['files']),{'hub/makerspace.sqlite3','hub/simple-updates.sqlite3'})
        with closing(sqlite3.connect(destination/'hub/simple-updates.sqlite3')) as copied:
            self.assertEqual(copied.execute('SELECT value FROM revision').fetchone()[0],7)
            self.assertEqual(copied.execute('PRAGMA journal_mode').fetchone()[0],'delete')
        control.execute('UPDATE revision SET value=8');control.commit()
        self.assertEqual(control.execute('SELECT value FROM revision').fetchone()[0],8)
        self.assertEqual(control.execute('PRAGMA journal_mode').fetchone()[0],'wal')
        self.assertFalse((destination/'hub/simple-updates.sqlite3-wal').exists())

    def test_missing_local_database_is_a_failure_before_program_replacement(self):
        self.state.mkdir();destination=self.root/'manual'
        with self.assertRaisesRegex(life.LifecycleError,'lokal database mangler'):
            life.manual_database_backup(self.state,destination)
        self.assertFalse((destination/'manual-recovery.json').exists())


if __name__=='__main__':unittest.main()
