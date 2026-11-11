import importlib.util,os,unittest
from pathlib import Path
from unittest.mock import patch
p=Path(__file__).parents[1]/"windows/inno/lifecycle.py"
s=importlib.util.spec_from_file_location("fresh_lifecycle",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class FixedPath(unittest.TestCase):
 def test_command_adds_only_fixed_openssh_not_operator_path(self):
  n=object.__new__(m.NativeWindows);n.powershell=Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
  with patch.dict(os.environ,{"SystemRoot":"C:/Windows","PATH":"C:/untrusted-user-bin"}),patch.object(m.subprocess,"run") as run,patch.object(m,"plain",side_effect=lambda x:x):
   n.command(Path("C:/fixed/Setup-School.ps1"),"-CheckOnly")
  env=run.call_args.kwargs["env"];self.assertEqual(env["PATH"].replace(chr(92),"/"),"C:/Windows/System32;C:/Windows/System32/WindowsPowerShell/v1.0;C:/Windows/System32/OpenSSH");self.assertNotIn("untrusted",env["PATH"]);self.assertTrue(run.call_args.kwargs["check"])
if __name__=="__main__":unittest.main()
