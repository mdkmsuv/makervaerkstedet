"""Exercise native Windows job limits without models or printer connections."""
import json
import os
import subprocess
import sys
import unittest

from app.process_tree import ProcessTree
CPU_PERCENT = 40


@unittest.skipUnless(os.name == 'nt', 'Windows job objects')
class ProcessTreeTests(unittest.TestCase):
    def test_nested_job_caps_entire_tree_and_children_inherit_low_priority(self):
        # Match supervisor -> server -> worker -> child, including nested jobs.
        code = '''
import ctypes, json, subprocess, sys, time
from ctypes import wintypes
from app.process_tree import ProcessTree
CPU_PERCENT = 40
input()
runner_code = "import ctypes, time; from ctypes import wintypes; k=ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype=wintypes.HANDLE; k.GetPriorityClass.argtypes=[wintypes.HANDLE]; print(k.GetPriorityClass(k.GetCurrentProcess()), flush=True); time.sleep(60)"
child_code = "import subprocess, sys; input(); p=subprocess.Popen([sys.executable, '-c', " + repr(runner_code) + "]); p.wait()"
child = subprocess.Popen([sys.executable, '-c', child_code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
tree = ProcessTree(child, cpu_percent=CPU_PERCENT)
child.stdin.write('\\n')
child.stdin.flush()
class Rate(ctypes.Structure):
    _fields_ = [('flags', wintypes.DWORD), ('rate', wintypes.DWORD)]
k = tree.kernel
k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
rate = Rate()
assert k.QueryInformationJobObject(tree.handle, 15, ctypes.byref(rate), ctypes.sizeof(rate), None)
print(json.dumps({'pid': child.pid, 'flags': rate.flags, 'rate': rate.rate, 'priority': int(child.stdout.readline())}), flush=True)
input()
tree.close()
child.wait(timeout=10)
print('closed', flush=True)
'''
        process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        tree = None
        try:
            tree = ProcessTree(process)  # Server supervisor stays unrestricted.
            process.stdin.write('\n')
            process.stdin.flush()
            result = json.loads(process.stdout.readline())
            self.assertEqual(result['flags'], 0x1 | 0x4)
            self.assertEqual(result['rate'], CPU_PERCENT * 100)
            self.assertEqual(result['priority'], subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            process.stdin.write('\n')
            process.stdin.flush()
            out, err = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, err)
            self.assertIn('closed', out)
        finally:
            if tree:
                tree.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_rejects_invalid_cap(self):
        for cap in (0, -1, 101):
            with self.assertRaises(ValueError):
                ProcessTree(None, cpu_percent=cap)


if __name__ == '__main__':
    unittest.main()
