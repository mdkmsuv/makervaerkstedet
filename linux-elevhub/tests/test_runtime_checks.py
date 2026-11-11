"""Version/PID bound readiness without any host service operations."""
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import urlsplit
import uuid

SOURCE=Path(__file__).parents[1]/"administration/runtime_checks.py"
SPEC=importlib.util.spec_from_file_location("student_runtime_checks",SOURCE)
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

class RuntimeReadiness(unittest.TestCase):
    def setUp(self):
        self.version="2.0.0~20261109.1"
        self.values={}
        for port,path,service,app in M.ENDPOINTS:
            self.values[port]={"release_id":self.version,"service":service,"pid":101 if service=="elevhub-lokal" else 202,
                               "runtime_instance":str(uuid.UUID(int=1 if service=="elevhub-lokal" else 2)),"local":True,"app":app}
    def call(self,args):
        return "ActiveState=active\nMainPID="+("101" if args[-1]=="elevhub-lokal.service" else "202" if args[-1]=="elevhub-design.service" else "303")+"\n"
    def read(self,url):return self.values[urlsplit(url).port]
    def test_running_exact_core_and_unchanged_core_after_updater_only_release_pass(self):
        self.assertEqual(M.check_once(self.version,self.call,self.read)["core_version"],self.version)
        # No overall-feed tag is involved: the retained core remains authoritative.
        self.assertEqual(M.check_once(self.version,self.call,self.read)["core_version"],self.version)
    def test_wrong_release_pid_and_unknown_identity_fail(self):
        for key,bad in (("release_id","9.9.9"),("release_id",None),("pid",999),("runtime_instance",None)):
            with self.subTest(key=key,bad=bad):
                old=self.values[8791][key];self.values[8791][key]=bad
                with self.assertRaises(M.RuntimeCheckError):M.check_once(self.version,self.call,self.read)
                self.values[8791][key]=old
    def test_inactive_service_and_split_old_new_process_are_not_ready(self):
        with self.assertRaisesRegex(M.RuntimeCheckError,"service_inactive"):
            M.check_once(self.version,lambda _:"ActiveState=inactive\nMainPID=0",self.read)
        self.values[8792]["runtime_instance"]=str(uuid.uuid4())
        with self.assertRaisesRegex(M.RuntimeCheckError,"instance_mismatch"):
            M.check_once(self.version,self.call,self.read)
    def test_changed_core_descriptor_does_not_make_old_runtime_current(self):
        with self.assertRaisesRegex(M.RuntimeCheckError,"identity_mismatch"):
            M.check_once("2.0.0~20261110.1",self.call,self.read)
    def test_missing_core_and_bounded_endpoint_failure_are_explicit(self):
        with self.assertRaisesRegex(M.RuntimeCheckError,"core_version_required"):
            M.check_once(None,self.call,self.read)
        def offline(_):raise OSError("offline")
        with self.assertRaisesRegex(M.RuntimeCheckError,"endpoint_unavailable"):
            M.wait_ready(self.version,self.call,read=offline,timeout=0)

if __name__=="__main__":unittest.main()
