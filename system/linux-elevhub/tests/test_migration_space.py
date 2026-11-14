"""Native inert school archive allocation checks; never install or extract."""
from pathlib import Path
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent/'REPOSITORY-LAYOUT.json').is_file())
sys.path.insert(0,str(ROOT/'system/linux-elevhub/administration'))
try:import migration_space as space
except ImportError:space=None


class MigrationSpace(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(space,'All-package migration space guard is missing')
        self.temp=tempfile.TemporaryDirectory(prefix='mkv-migration-space-');self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name);self.root=self.work/'target';self.root.mkdir()

    def deb(self,name,files):
        folder=self.work/name;control=folder/'DEBIAN';control.mkdir(parents=True)
        (control/'control').write_text(f'Package: {name}\nVersion: 1.0\nArchitecture: all\nMaintainer: QA <qa@example.invalid>\nDescription: inert allocation fixture\n')
        for path,size in files.items():
            target=folder/path;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('wb') as stream:stream.truncate(size)
            target.chmod(0o644)
        for path in folder.rglob('*'):
            if path.is_dir():path.chmod(0o755)
        folder.chmod(0o755)
        output=self.work/(name+'.deb')
        subprocess.run(['dpkg-deb','--build','--root-owner-group','-Zxz','-z6',str(folder),str(output)],check=True,capture_output=True)
        return output

    def test_real_compressed_core_plus_four_apps_cannot_fit_150_mib(self):
        files=[self.deb('makervaerkstedet-elev',{'usr/lib/makervaerkstedet/core.bin':160*1024**2})]
        files += [self.deb('makervaerkstedet-app-'+name,{f'usr/share/makervaerkstedet/apps/{name}/index.html':4096})
                  for name in ('laser','folie','3dprint','microbit')]
        self.assertLess(files[0].stat().st_size,64*1024)
        with patch.object(space.shutil,'disk_usage',return_value=SimpleNamespace(free=150*1024**2)):
            with self.assertRaisesRegex(ValueError,'insufficient_migration_space'):
                space.check_migration_space(files,root=self.root,reserve=0)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_native_commands_check_core_payload_before_any_apt_apply(self):
        sys.path[:0]=[str(ROOT),str(ROOT/'system'),str(ROOT/'system/app')]
        import apt_client
        from tests.test_app_catalog import descriptor
        app=descriptor();core=self.deb('makervaerkstedet-elev',{'usr/lib/makervaerkstedet/core.bin':160*1024**2})
        folder=self.work/'valid-app';control=folder/'DEBIAN';control.mkdir(parents=True)
        (control/'control').write_text('Package: '+app['package']+'\nVersion: '+app['version']
            +'\nArchitecture: all\nMaintainer: QA <qa@example.invalid>\nDescription: inert app\nDepends: makervaerkstedet-elev (>= '+app['min_core']+')\n')
        base=folder/'usr/share/makervaerkstedet/apps'/app['id'];(base/'www').mkdir(parents=True)
        (base/'app.json').write_text(json.dumps(app));(base/'www/index.html').write_text('<html></html>')
        archive=self.work/'valid-app.deb'
        subprocess.run(['dpkg-deb','--build','--root-owner-group',str(folder),str(archive)],check=True,capture_output=True)
        native=apt_client.Commands([],app_root=self.root);native.preflight_app=lambda _: []
        applied=[];native.install=lambda *args,**kwargs:applied.append(args)
        with patch.object(space.shutil,'disk_usage',return_value=SimpleNamespace(free=150*1024**2)):
            with self.assertRaisesRegex(ValueError,'insufficient_migration_space'):
                native.install_migration([core,archive],[app],{'makervaerkstedet-elev':'1.0',app['package']:app['version']})
        self.assertEqual(applied,[])

    def test_all_archives_share_one_volume_budget_and_round_small_files(self):
        files=[self.deb('makervaerkstedet-'+name,{'usr/lib/'+name:1}) for name in ('core','updater')]
        with patch.object(space.shutil,'disk_usage',return_value=SimpleNamespace(free=2**40)):
            result=space.check_migration_space(files,root=self.root,reserve=123)
        self.assertEqual(len(result['volumes']),1)
        volume=result['volumes'][0]
        self.assertGreaterEqual(volume['required_bytes'],4*4096+123)
        self.assertEqual(result['unpacked_bytes'],2)
        self.assertEqual(volume['regular_files'],2)
        self.assertNotIn(str(self.root),str(result))

    def test_real_destination_devices_receive_separate_aggregate_budgets(self):
        (self.root/'usr').mkdir();(self.root/'var/lib/dpkg').mkdir(parents=True)
        (self.root/'var/cache/apt/archives').mkdir(parents=True)
        files=[self.deb('makervaerkstedet-'+name,{'usr/lib/'+name:5*1024**2}) for name in ('core','updater')]
        actual_lstat=os.lstat
        def lstat(path,*args,**kwargs):
            result=actual_lstat(path,*args,**kwargs)
            values={name:getattr(result,name) for name in ('st_mode','st_size','st_uid','st_gid','st_nlink')}
            values['st_dev']=91 if Path(path).is_relative_to(self.root/'usr') else 92
            return SimpleNamespace(**values)
        with patch.object(space.os,'lstat',side_effect=lstat),patch.object(space.shutil,'disk_usage',return_value=SimpleNamespace(free=2**40)):
            result=space.check_migration_space(files,root=self.root,reserve=0)
        volumes={item['device']:item for item in result['volumes']}
        self.assertEqual(set(volumes),{91,92})
        self.assertEqual(volumes[91]['regular_files'],2)
        self.assertGreaterEqual(volumes[91]['required_bytes'],20*1024**2)
        self.assertGreater(volumes[92]['required_bytes'],0)

    def test_existing_target_symlink_cannot_redirect_allocations(self):
        archive=self.deb('makervaerkstedet-core',{'usr/lib/core':1})
        outside=self.work/'outside';outside.mkdir();(self.root/'usr').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'migration_target_symlink'):
            space.check_migration_space([archive],root=self.root,reserve=0)
        self.assertEqual(list(outside.iterdir()),[])

    def raw_deb(self,members,name='malicious'):
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w',format=tarfile.GNU_FORMAT) as archive:
            for member,content in members:archive.addfile(member,io.BytesIO(content) if content is not None else None)
        tar=self.work/(name+'.tar');tar.write_bytes(data.getvalue())
        control=self.work/(name+'-control');control.mkdir()
        (control/'control').write_text('Package: makervaerkstedet-fixture\nVersion: 1.0\nArchitecture: all\nMaintainer: QA\nDescription: inert\n')
        control_tar=self.work/(name+'-control.tar');subprocess.run(['tar','--owner=0','--group=0','-cf',str(control_tar),'-C',str(control),'.'],check=True)
        stage=self.work/(name+'-ar');stage.mkdir();(stage/'debian-binary').write_bytes(b'2.0\n')
        shutil.copyfile(control_tar,stage/'control.tar');shutil.copyfile(tar,stage/'data.tar')
        output=self.work/(name+'.deb');subprocess.run(['ar','rc',str(output),'debian-binary','control.tar','data.tar'],cwd=stage,check=True,capture_output=True)
        return output

    def test_native_tar_path_escape_links_devices_and_duplicate_files_rejected(self):
        for index,mode in enumerate(('escape','symlink','hardlink','device','duplicate','owner','setuid')):
            member=tarfile.TarInfo('../outside' if mode=='escape' else 'usr/lib/core');member.mode=0o644;member.uid=member.gid=0
            content=b'X';member.size=len(content)
            if mode in ('symlink','hardlink'):
                member.type=tarfile.SYMTYPE if mode=='symlink' else tarfile.LNKTYPE;member.linkname='/etc/passwd';member.size=0;content=None
            if mode=='device':member.type=tarfile.CHRTYPE;member.size=0;content=None
            if mode=='owner':member.uid=1000
            if mode=='setuid':member.mode=0o4644
            members=[(member,content)]*(2 if mode=='duplicate' else 1)
            archive=self.raw_deb(members,'bad-'+str(index))
            with self.subTest(mode=mode),self.assertRaises(ValueError):space.check_migration_space([archive],root=self.root,reserve=0)

    def test_duplicate_file_ownership_across_archives_is_rejected(self):
        files=[self.deb('makervaerkstedet-'+name,{'usr/lib/same':1}) for name in ('core','updater')]
        with self.assertRaisesRegex(ValueError,'duplicate_migration_path'):space.check_migration_space(files,root=self.root,reserve=0)

    def test_expansion_count_and_invalid_deb_are_rejected(self):
        archive=self.deb('makervaerkstedet-core',{'usr/lib/core':8192})
        with patch.object(space,'MAX_EXPANDED',4096),self.assertRaisesRegex(ValueError,'migration_archive_limit'):
            space.check_migration_space([archive],root=self.root,reserve=0)
        with patch.object(space,'MAX_FILES',1),self.assertRaisesRegex(ValueError,'migration_archive_limit'):
            space.check_migration_space([archive],root=self.root,reserve=0)
        archive.write_bytes(b'tampered archive')
        with self.assertRaises(ValueError):space.check_migration_space([archive],root=self.root,reserve=0)


if __name__=='__main__':unittest.main()
