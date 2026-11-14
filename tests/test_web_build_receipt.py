"""A stale copied browser bundle must not become a new application package."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from scripts.validate_web_build import COMMON,TARGETS,validate_web_build


class WebReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'source';self.root.mkdir()
        self.target='folie'
        folder,outputs,required=TARGETS[self.target]
        sources={}
        for name in COMMON|required|{'apps/folie/web/imported.js'}:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'source\n')
            sources[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        self.vendor=self.root/folder;self.vendor.mkdir(parents=True)
        generated={}
        for name in outputs:
            path=self.vendor/name;path.write_bytes(b'bundle')
            generated[name]={'size':6,'sha256':hashlib.sha256(b'bundle').hexdigest()}
        self.receipt={'schema':1,'target':self.target,'source_sha256':sources,'outputs':generated}
        self.write_receipt()

    def write_receipt(self):
        (self.vendor/'web-build-manifest.json').write_text(json.dumps(self.receipt),encoding='utf-8')

    def test_copied_source_requires_same_inputs_even_if_output_matches(self):
        copied=self.root.parent/'export';shutil.copytree(self.root,copied)
        # Checkout CRLF is equivalent to committed LF.
        (self.root/'apps/folie/web/imported.js').write_bytes(b'source\r\n')
        validate_web_build(copied,self.target,self.root)
        (self.root/'apps/folie/web/imported.js').write_bytes(b'changed\r\n')
        with self.assertRaisesRegex(ValueError,'web_source_hash_mismatch'):
            validate_web_build(copied,self.target,self.root)

    def test_output_tamper_is_rejected(self):
        (self.vendor/'laser.js').write_bytes(b'altered')
        with self.assertRaisesRegex(ValueError,'web_output_hash_mismatch'):
            validate_web_build(self.root,self.target)

    def test_missing_worker_and_incomplete_source_receipts_are_rejected(self):
        del self.receipt['outputs']['laser-trace-worker.js'];self.write_receipt()
        with self.assertRaisesRegex(ValueError,'invalid_web_build_receipt'):
            validate_web_build(self.root,self.target)
        self.receipt['outputs']['laser-trace-worker.js']={'size':6,'sha256':hashlib.sha256(b'bundle').hexdigest()}
        del self.receipt['source_sha256']['apps/folie/build.mjs'];self.write_receipt()
        with self.assertRaisesRegex(ValueError,'invalid_web_build_receipt'):
            validate_web_build(self.root,self.target)

    def test_source_paths_cannot_escape(self):
        self.receipt['source_sha256']['../outside.js']='0'*64;self.write_receipt()
        with self.assertRaisesRegex(ValueError,'invalid_web_source_path'):
            validate_web_build(self.root,self.target)


if __name__=='__main__':unittest.main()
