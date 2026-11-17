"""Synthetic, inert scheduler coverage of explicit auxiliary-task adoption."""
import json, os, subprocess, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PS=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'

@unittest.skipUnless(os.name=='nt' and PS.is_file(),'Windows PowerShell required')
class AuxiliaryTaskTests(unittest.TestCase):
 def scenario(self,name,source=None):
  with tempfile.TemporaryDirectory(prefix='mkv-auxiliary-') as folder:
   p=subprocess.run([str(PS),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'tests/fixtures/windows_auxiliary_tasks.ps1'),'-Source',str(source or ROOT/'system/windows/inno/task_control.ps1'),'-Root',folder,'-Scenario',name],capture_output=True,encoding='utf-8-sig',timeout=20)
   self.assertEqual(p.returncode,0,p.stderr);return json.loads(p.stdout)
 def test_exact_tasks_stop_and_restore_same_definitions(self):
  v=self.scenario('valid');self.assertIsNone(v['error']);self.assertEqual(v['journal_task_count'],4);self.assertTrue(v['definitions_unchanged'])
  for n in ('ElevhubServer-PublicPrintHttp','ElevhubServer-TeacherPrintHttps'):
   self.assertIn('stop:'+n,v['events']);self.assertIn('start:'+n,v['events'])
 def test_native_failure_uses_same_restore_journal(self):
  v=self.scenario('failed-update');self.assertIsNone(v['error']);self.assertTrue(v['definitions_unchanged']);self.assertEqual(len([x for x in v['events'] if x.startswith('start:')]),4)
 def test_disabled_auxiliary_stays_disabled(self):
  v=self.scenario('disabled');self.assertIsNone(v['error']);self.assertNotIn('start:ElevhubServer-TeacherPrintHttps',v['events']);self.assertNotIn('enable:ElevhubServer-TeacherPrintHttps',v['events'])
 def test_malformed_or_unbound_tasks_refuse_before_mutation(self):
  for name in ('missing-task','unknown','duplicate','too-many','metadata-object','metadata-null','extra-field','upper-hash','changed-hash','wrong-executable','wrong-cwd','wrong-principal','multiple-actions','unbound'):
   with self.subTest(name=name):
    v=self.scenario(name);self.assertTrue(v['error']);self.assertEqual(v['events'],[]);self.assertEqual(v['journal_task_count'],0)
 def test_untrusted_owner_refuses_before_reading_any_task_definition(self):
  for name in ('unsafe-owner','linked-owner'):
   with self.subTest(name=name):
    v=self.scenario(name);self.assertTrue(v['error']);self.assertEqual(v['events'],[]);self.assertEqual(v['export_count'],0)
 def test_absent_optional_metadata_preserves_old_contract(self):
  v=self.scenario('no-auxiliary');self.assertIsNone(v['error']);self.assertEqual(v['journal_task_count'],2)
