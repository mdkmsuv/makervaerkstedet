"""Synthetic installer identity/profile and actual SQLite snapshot invariants."""
import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('profile_lifecycle',ROOT/'windows/inno/lifecycle.py')
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

    def test_guide_uses_auto_action_danish_labels_and_explicit_profile_pages(self):
        recipe=(ROOT/'windows/inno/Makervaerkstedet.iss').read_text('utf-8')
        self.assertNotIn('ModePage',recipe)
        self.assertIn("+ 'auto' +",recipe)
        for text in ('Skolens opsætning','Rød','Grøn','køpause','RolePage','ThemePage','MachinePage'):
            self.assertIn(text,recipe)
        self.assertNotIn('koepause',recipe)

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
