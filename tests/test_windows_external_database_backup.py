"""R3-WIN-1: existing owner component lists and real synthetic SQLite snapshots."""
from contextlib import closing
import hashlib,importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from app.backup_databases import _SCHEMAS

spec=importlib.util.spec_from_file_location('external_backup_lifecycle',Path(__file__).resolve().parents[1]/'windows/inno/lifecycle.py')
life=importlib.util.module_from_spec(spec);spec.loader.exec_module(life)

class ExternalBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.journal=self.root/'journal';self.journal.mkdir()
        self.native=life.NativeWindows.__new__(life.NativeWindows);self.native.state=self.root

    def database(self,role):
        relative={'hub':'hub/makerspace.sqlite3','print':'printvaerksted/queue.sqlite3','laser':'designvaerksted/afleveringer.sqlite3'}[role]
        p=self.root/relative;p.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(p)) as db:
            for table,columns in _SCHEMAS[role]['tables'].items():
                db.execute('CREATE TABLE '+table+' ('+','.join('"'+c+'"' for c in columns)+')')
            db.execute('PRAGMA user_version='+str(_SCHEMAS[role]['versions'][0]));db.commit()
        return p

    def owner(self,value):
        (self.root/'installation-owner.json').write_text(json.dumps({'externalBackends':value}))

    def test_external_print_only_skips_print_preserves_local_hub_and_laser(self):
        paths=[self.database('hub'),self.database('laser')];before={p:p.read_bytes() for p in paths}
        self.owner(['print']);result=self.native.backup_databases(self.journal)
        self.assertEqual(set(result['files']),{'hub/makerspace.sqlite3','designvaerksted/afleveringer.sqlite3'})
        self.assertEqual(result['absent_external_databases'],['printvaerksted/queue.sqlite3'])
        self.assertFalse(result['automatic_restore'])
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_local_server_requires_all_local_databases_and_keeps_data(self):
        paths=[self.database(role) for role in ('hub','print','laser')]
        before={p:p.read_bytes() for p in paths};self.owner([])
        result=self.native.backup_databases(self.journal);self.assertEqual(len(result['files']),3)
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_missing_local_laser_is_not_hidden_by_external_print(self):
        self.database('hub');self.owner(['print'])
        with self.assertRaisesRegex(life.LifecycleError,'lokal database mangler'):self.native.backup_databases(self.journal)
        self.assertFalse((self.journal/'databases-manual/manual-recovery.json').exists())

    def test_malformed_owner_components_fail_before_any_backup(self):
        for value in (True,{'print':True},['unknown'],['hub'],['print','print'],['print',{}]):
            with self.subTest(value=value):
                self.owner(value)
                with self.assertRaisesRegex(life.LifecycleError,'eksterne komponenter'):self.native.backup_databases(self.journal)
                self.assertFalse((self.journal/'databases-manual').exists())

if __name__=='__main__':unittest.main()
