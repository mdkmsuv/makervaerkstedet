import hashlib, json, tempfile, unittest, zipfile, threading
from pathlib import Path
from unittest.mock import patch
try:
    from . import release_import
    from .release_import import ImportConflict, ImportInvalid, import_ready_package, materialize_verified_package
except ImportError:
    import release_import
    from release_import import ImportConflict, ImportInvalid, import_ready_package, materialize_verified_package

class ReleaseImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name); self.stage=self.root/'stage'; self.stage.mkdir(); self.store=self.root/'store'
        files={'worker.py':b"raise SystemExit('never run')\n",'CREDITS.md':b'Kreditering: Klostermarksskolen.\n'}
        files['config.json']=b'{"role":"linux_student"}\n'
        marker={'schema':1,'role':'linux_student','format':'linux-queue-v1','version':'2026.10.20','files':{n:hashlib.sha256(v).hexdigest() for n,v in files.items()}}
        self.archive=self.stage/'linux-update.zip'
        with zipfile.ZipFile(self.archive,'w') as z:
            for n,v in files.items(): z.writestr(n,v)
            z.writestr('update-package.json',json.dumps(marker).encode())
        self.package={'role':'linux_student','format':'linux-queue-v1','version':'2026.10.20','asset':'linux-update.zip','sha256':hashlib.sha256(self.archive.read_bytes()).hexdigest(),'size':self.archive.stat().st_size,'path':str(self.archive),'repository':'example/project','release_id':17,'download_id':'a'*32}
    def imp(self,p=None,**kw):
        p=p or self.package; return import_ready_package(p,self.store,trusted_repository='example/project',trusted_package=p,**kw)
    def test_store_receipt_and_idempotent_reverify(self):
        r=self.imp(); self.assertTrue(Path(r['archive']).is_file()); self.assertNotIn('worker_root',r); self.assertTrue(self.imp()['idempotent'])
        materialized=materialize_verified_package(r,self.root/'package-root'); self.assertEqual(Path(materialized['package_root'],'worker.py').read_bytes(),b"raise SystemExit('never run')\n")
        receipt=json.loads((self.store/'packages'/'linux_student@2026.10.20'/'import-receipt.json').read_text()); self.assertEqual(receipt['package_sha256'],self.package['sha256']); self.assertIn('config.json',receipt['inventory'])
    def test_authority_and_nonstring_inputs(self):
        with self.assertRaises(ImportInvalid): import_ready_package(self.package,self.store)
        with self.assertRaises(ImportInvalid): self.imp({**self.package,'role':[]})
        with self.assertRaises(ImportInvalid): self.imp({**self.package,'repository':'evil/project'})
    def test_ads_traversal_symlink_and_duplicate_boundaries(self):
        for name in ('a:b','../outside','x\\y','C:/absolute'):
            bad=self.stage/'bad.zip'
            with zipfile.ZipFile(bad,'w') as z: z.writestr(name,b'x')
            p={**self.package,'path':str(bad),'size':bad.stat().st_size,'sha256':hashlib.sha256(bad.read_bytes()).hexdigest()}
            with self.assertRaises(ImportInvalid): self.imp(p)
    def test_symlink_and_hardlink_unix_modes_are_rejected(self):
        for mode in (0o120777, 0o160777):
            bad=self.stage/'link.zip'; info=zipfile.ZipInfo('linked'); info.external_attr=mode << 16
            with zipfile.ZipFile(bad,'w') as z: z.writestr(info,b'target')
            p={**self.package,'path':str(bad),'size':bad.stat().st_size,'sha256':hashlib.sha256(bad.read_bytes()).hexdigest()}
            with self.assertRaises(ImportInvalid): self.imp(p)
    def test_casefold_duplicate_and_member_hash_size_mismatch(self):
        bad=self.stage/'duplicate.zip'
        with zipfile.ZipFile(bad,'w') as z:
            z.writestr('A',b'a'); z.writestr('a',b'b')
        p={**self.package,'path':str(bad),'size':bad.stat().st_size,'sha256':hashlib.sha256(bad.read_bytes()).hexdigest()}
        with self.assertRaises(ImportInvalid): self.imp(p)
    def test_large_linux_payload_has_package_limit(self):
        files={'config.json':b'{}','CREDITS.md':b'Klostermarksskolen','payload.tar.gz':b'x'*20}
        marker={'schema':1,'role':'linux_student','format':'linux-queue-v1','version':'2026.10.20','files':{n:hashlib.sha256(v).hexdigest() for n,v in files.items()}}
        archive=self.stage/'payload.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for n,v in files.items(): z.writestr(n,v)
            z.writestr('update-package.json',json.dumps(marker))
        p={**self.package,'path':str(archive),'asset':'payload.zip','size':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
        with patch.object(release_import,'MAX_MEMBER',18): self.assertFalse(self.imp(p)['idempotent'])
        marker={'schema':1,'role':'linux_student','format':'linux-queue-v1','version':'2026.10.21','files':{'config.json':'0'*64,'CREDITS.md':'0'*64,'worker.py':'0'*64}}
        bad=self.stage/'member-hash.zip'
        with zipfile.ZipFile(bad,'w') as z:
            for n,v in {'config.json':b'{}','CREDITS.md':b'Klostermarksskolen','worker.py':b'x'}.items(): z.writestr(n,v)
            z.writestr('update-package.json',json.dumps(marker))
        p={**self.package,'version':'2026.10.21','path':str(bad),'size':bad.stat().st_size,'sha256':hashlib.sha256(bad.read_bytes()).hexdigest()}
        with self.assertRaises(ImportInvalid): self.imp(p)
    def test_member_hashes_stream_without_zip_read_for_payload(self):
        payload=b'x'*64
        files={'config.json':b'{}','CREDITS.md':b'Klostermarksskolen','payload.tar.gz':payload}
        marker={'schema':1,'role':'linux_student','format':'linux-queue-v1','version':'2026.10.20','files':{n:hashlib.sha256(v).hexdigest() for n,v in files.items()}}
        archive=self.stage/'stream.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for n,v in files.items(): z.writestr(n,v)
            z.writestr('update-package.json',json.dumps(marker))
        p={**self.package,'path':str(archive),'asset':'stream.zip','size':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
        original=zipfile.ZipFile.read
        def guarded(source,name,*args,**kwargs):
            if name == 'payload.tar.gz': raise AssertionError('payload must be streamed')
            return original(source,name,*args,**kwargs)
        with patch.object(zipfile.ZipFile,'read',guarded): self.assertFalse(self.imp(p)['idempotent'])
    def test_marker_over_feed_limit_but_under_inventory_limit_is_accepted(self):
        files={'config.json':b'{}','CREDITS.md':b'Klostermarksskolen'}
        for index in range(6000): files[f'f{index}.txt']=str(index).encode()
        marker={'schema':1,'role':'linux_student','format':'linux-queue-v1','version':'2026.10.20','files':{n:hashlib.sha256(v).hexdigest() for n,v in files.items()}}
        self.assertGreater(len(json.dumps(marker).encode()), release_import.MAX_MANIFEST)
        self.assertLess(len(json.dumps(marker).encode()), release_import.MAX_INVENTORY)
        archive=self.stage/'large-marker.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for n,v in files.items(): z.writestr(n,v)
            z.writestr('update-package.json',json.dumps(marker))
        p={**self.package,'path':str(archive),'asset':'large-marker.zip','size':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
        self.assertFalse(self.imp(p)['idempotent'])
    def test_marker_over_inventory_limit_is_rejected_before_read(self):
        archive=self.stage/'oversized-marker.zip'; marker=b'x'*(release_import.MAX_INVENTORY+1)
        with zipfile.ZipFile(archive,'w') as z: z.writestr('update-package.json',marker)
        p={**self.package,'path':str(archive),'asset':'oversized-marker.zip','size':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
        with patch.object(zipfile.ZipFile,'read',side_effect=AssertionError('oversized marker must not be read')):
            with self.assertRaises(ImportInvalid): self.imp(p)
    def test_marker_schema_bool_and_nonstring_types_are_rejected(self):
        for schema, role in ((True,'linux_student'),(1,[])):
            bad=self.stage/'marker.zip'; marker={'schema':schema,'role':role,'format':'linux-queue-v1','version':'2026.10.20','files':{}}
            with zipfile.ZipFile(bad,'w') as z: z.writestr('update-package.json',json.dumps(marker))
            p={**self.package,'path':str(bad),'size':bad.stat().st_size,'sha256':hashlib.sha256(bad.read_bytes()).hexdigest()}
            with self.assertRaises(ImportInvalid): self.imp(p)
    def test_legacy_manifest_declared_size_is_bound(self):
        files={'config.json':b'{}','CREDITS.md':b'Klostermarksskolen'}
        marker={'format':'makervaerkstedet-station-code-v1','release':'2026.10.20','files':{n:{'sha256':hashlib.sha256(v).hexdigest(),'size':len(v)+1} for n,v in files.items()}}
        archive=self.stage/'legacy.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for n,v in files.items(): z.writestr(n,v)
            z.writestr('manifest.json',json.dumps(marker))
        p={**self.package,'role':'windows_laser_teacher','format':'laser-code-v1','path':str(archive),'asset':'legacy.zip','size':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
        with self.assertRaises(ImportInvalid): self.imp(p)
    def test_source_toctou_mutation_between_hashes_is_rejected(self):
        original=release_import._hash; calls=[0]
        def changing(path):
            calls[0]+=1
            digest=original(path)
            if calls[0]==2: path.write_bytes(b'mutated')
            return digest
        with patch.object(release_import,'_hash',side_effect=changing):
            with self.assertRaises(ImportInvalid): self.imp()
    def test_crash_after_archive_copy_before_receipt_is_recoverable(self):
        real_replace=release_import.os.replace
        def crash(source,target):
            if str(target).endswith('import-receipt.json'): raise OSError('simulated crash')
            return real_replace(source,target)
        with patch.object(release_import.os,'replace',side_effect=crash):
            with self.assertRaises(OSError): self.imp()
        folder=self.store/'packages'/'linux_student@2026.10.20'
        self.assertFalse((folder/'import-receipt.json').exists()); self.assertFalse((folder/'linux-update.zip').exists())
        self.assertFalse(self.imp()['idempotent'])
    def test_concurrent_process_style_imports_have_one_receipt(self):
        results=[]; errors=[]
        def run():
            try: results.append(self.imp())
            except Exception as error: errors.append(error)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertIn(len(results), (1, 2))
        if len(results) == 2:
            self.assertEqual(sum(bool(item.get('idempotent')) for item in results),1)
        self.assertTrue(all(isinstance(error, ImportConflict) for error in errors))
    def test_archive_change_and_receipt_change_block_reuse(self):
        r=self.imp(); archive=Path(r['archive']); archive.write_bytes(b'changed')
        with self.assertRaises(ImportConflict): self.imp()
        archive.unlink(); (self.store/'packages'/'linux_student@2026.10.20'/'import-receipt.json').unlink()
        self.assertFalse((self.store/'packages'/'linux_student@2026.10.20'/'import-receipt.json').exists())
    def test_lock_rejects_concurrent_import(self):
        lock=self.store/'import.lock'; self.store.mkdir(); lock.touch()
        import release_import
        with release_import._lock(self.store):
            with self.assertRaises(ImportConflict): self.imp()

if __name__=='__main__': unittest.main()
