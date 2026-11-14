"""Real committed Git and verified public-source build exports; no mocked audit."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from scripts import public_source
from scripts import export_build_source as exporter
from scripts.validate_web_build import COMMON, TARGETS

ROOT = Path(__file__).resolve().parents[1]


def make_source(parent):
    root = parent / 'source æ with spaces'
    root.mkdir()
    sources = COMMON | set().union(*(value[2] for value in TARGETS.values()))
    for name in sources:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == 'REPOSITORY-LAYOUT.json':
            path.write_bytes((ROOT / name).read_bytes())
        else:
            path.write_bytes(b'{}\n' if path.suffix == '.json' else b'// synthetic reviewed source\n')
    for app in ('laser','folie','3dprint','scratch','microbit'):
        folder=root/'apps'/app;folder.mkdir(parents=True,exist_ok=True)
        (folder/'app.json').write_bytes((ROOT/'apps'/app/'app.json').read_bytes())
    model_sources={'apps/3dprint/build.mjs','scripts/build_model3d_vendor.mjs',
                   'apps/3dprint/web/model3d.html','apps/3dprint/web/model3d.css','apps/3dprint/web/model3d/app.js'}
    for name in model_sources:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'// synthetic 3D source\n')
    font='apps/3dprint/web/assets/model3d/Metrophobic-Regular.ttf'
    path=root/font;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((ROOT/font).read_bytes())
    (root / 'system/web/unrelated.js').write_text('// committed source\n')
    (root / 'CREDITS.md').write_text(public_source.AUTHOR_CREDIT+'\n'+public_source.CREDIT+'\n', encoding='utf-8')
    (root / '.gitignore').write_text('**/vendor/\n', encoding='utf-8')
    policy = {'schemaVersion': 1, 'project': 'MakervÃ¦rkstedet', 'credit': public_source.CREDIT,
              'sourceRoots': ['system/web', 'sdk/web', 'scripts', 'apps'],
              'explicitFiles': ['CREDITS.md', '.gitignore', 'REPOSITORY-LAYOUT.json', 'package.json',
                                'package-lock.json', 'deployment/public-source-manifest.json'],
              'includeExtensions': ['.py', '.js', '.mjs', '.json', '.md', '.html', '.css'], 'includeNames': [],
              'excludeDirectoryNames': ['.git', 'vendor'], 'excludeGlobs': [], 'requiredDocs': ['CREDITS.md'],
              'limits': {'maxFileBytes': 2*1024*1024, 'maxTotalBytes': 8*1024*1024}}
    policy['explicitFiles'].append(font)
    policy['publicFonts']=[{'path':font,'sha256':hashlib.sha256((root/font).read_bytes()).hexdigest(),
                           'provenance':'Reviewed real font used only in isolated synthetic QA'}]
    path = root / 'deployment/public-source-manifest.json'
    path.parent.mkdir()
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Synthetic QA', '-c', 'user.email=qa@example.invalid',
                    'commit', '-qm', 'Synthetic reviewed source'], check=True, capture_output=True)
    for target, (directory, outputs, required) in TARGETS.items():
        vendor = root / directory
        vendor.mkdir(parents=True)
        generated = {}
        for name in outputs:
            data = ('synthetic compiled '+target+' '+name).encode()
            (vendor / name).write_bytes(data)
            generated[name] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        receipt = {'schema': 1, 'target': target,
                   'source_sha256': {name: hashlib.sha256((root/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                                     for name in COMMON|required}, 'outputs': generated}
        (vendor/'web-build-manifest.json').write_text(json.dumps(receipt), encoding='utf-8')
    vendor=root/'apps/3dprint/web/vendor';vendor.mkdir(parents=True)
    outputs={}
    for name in exporter.MODEL3D[:-1]:
        data=b'synthetic 3D output';(vendor/name).write_bytes(data)
        outputs[name]={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    receipt={'schema':1,'source_sha256':{name:hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
                                      for name in model_sources|COMMON|{'sdk/web/stl-validation.mjs'}},
             'text':{'input_sha256':{font:hashlib.sha256((root/font).read_bytes()).hexdigest()}},'outputs':outputs}
    (vendor/'model3d-vendor-manifest.json').write_text(json.dumps(receipt),encoding='utf-8')
    return root


def run_workflow_case(parent, release_set, app=None):
    """Real Git/export guards; bounded package-builder stubs never invoke apt."""
    source=make_source(parent)
    packaging=source/'system/linux-elevhub/packaging';packaging.mkdir(parents=True)
    for name in ('prepare_inputs.py','prepare_app_inputs.py','build_release.py'):
        (packaging/name).write_text('# Synthetic packaging command intercepted by the test runner.\n',encoding='utf-8')
    policy_path=source/'deployment/public-source-manifest.json'
    policy=json.loads(policy_path.read_text(encoding='utf-8'))
    policy['sourceRoots'].append('system/linux-elevhub/packaging')
    policy_path.write_text(json.dumps(policy),encoding='utf-8')
    subprocess.run(['git','-C',str(source),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Synthetic QA','-c','user.email=qa@example.invalid',
                    'commit','-qm','Synthetic packaging interfaces'],check=True,capture_output=True)
    released=parent/'released';released.mkdir()
    (released/'makervaerkstedet-matchbox_1_amd64.deb').write_bytes(b'synthetic pinned Matchbox')
    (released/'makervaerkstedet-matchbox_1_amd64.inputs.json').write_text(json.dumps({'files':[]}),encoding='utf-8')
    (released/'makervaerkstedet-matchbox_unrelated_amd64.deb').write_bytes(b'never select by wildcard')
    (released/'makervaerkstedet-scratch_15.1.1+local.20261104.1_all.inputs.json').write_text('{}',encoding='utf-8')
    scratch=json.loads((source/'apps/scratch/app.json').read_text(encoding='utf-8'))
    (released/(scratch['package']+'_'+scratch['version']+'_'+scratch['architecture']+'.deb')).write_bytes(b'old archive never copied')
    bindir=parent/'bin';bindir.mkdir()
    stub=bindir/'python3'
    stub.write_text(r"""#!/usr/bin/python3
import json,os,pathlib,sys
original=sys.argv[1:];args=list(original)
while args and args[0] in ('-B','-u'):args.pop(0)
name=pathlib.Path(args[0]).name if args and not args[0].startswith('-') else ''
if name not in ('prepare_inputs.py','prepare_app_inputs.py','build_release.py'):
 os.execv('/usr/bin/python3',['/usr/bin/python3',*original])
def option(name):return args[args.index(name)+1]
with open(os.environ['MKV_WORKFLOW_CALLS'],'a') as log:log.write(json.dumps({'script':name,'args':args})+'\n')
out=pathlib.Path(option('--output-dir'));out.mkdir(parents=True,exist_ok=True)
if name=='prepare_inputs.py':
 kinds=[]
 for value in args[args.index('--components')+1:]:
  if value.startswith('--'):break
  kinds.append(value)
 for kind in kinds:
  package='makervaerkstedet-'+{'core':'elev','updater':'updater','matchbox':'matchbox','scratch':'scratch'}[kind]
  value={'schema':'makervaerkstedet.debian-inputs.v1','package':package,'version':'1','architecture':'amd64' if kind=='matchbox' else 'all','files':[],'depends':[]}
  (out/(package+'.json')).write_text(json.dumps(value))
elif name=='prepare_app_inputs.py':
 app=option('--app');descriptor=json.loads((pathlib.Path(option('--source-root'))/'apps'/app/'app.json').read_text())
 if app=='scratch':assert pathlib.Path(option('--scratch-lock')).is_file()
 value={'schema':'makervaerkstedet.debian-inputs.v2','package':descriptor['package'],'version':descriptor['version'],'architecture':descriptor['architecture'],'files':[],'depends':[],'app_descriptor':descriptor}
 (out/(descriptor['package']+'.json')).write_text(json.dumps(value))
else:
 paths=[]
 for value in args[args.index('--input-locks')+1:]:
  if value.startswith('--'):break
  paths.append(pathlib.Path(value))
 specs=[json.loads(path.read_text()) for path in paths]
 kind=option('--release-set');expected={'base':3,'app':4,'migration':8}[kind]
 assert len(specs)==expected
 assert len({s['package'] for s in specs})==expected
 assert {s['package'] for s in specs}>={'makervaerkstedet-elev','makervaerkstedet-updater','makervaerkstedet-matchbox'}
 if kind!='base':assert all(s['schema'].endswith('.v2') for s in specs if s['package'] not in ('makervaerkstedet-elev','makervaerkstedet-updater','makervaerkstedet-matchbox'))
 assert (out/'makervaerkstedet-matchbox_1_amd64.deb').read_bytes()==b'synthetic pinned Matchbox'
 assert json.loads((out/'makervaerkstedet-matchbox_1_amd64.inputs.json').read_text())=={'files':[]}
 for spec in specs:
  path=out/(spec['package']+'_'+spec['version']+'_'+spec['architecture']+'.deb')
  if spec['package']!='makervaerkstedet-matchbox':path.write_bytes(b'built '+spec['package'].encode())
 (out/'release-build.json').write_text(json.dumps({'test_only':True,'release_set':kind,'packages':sorted(s['package'] for s in specs)}))
""",encoding='utf-8',newline='\n')
    stub.chmod(0o755)
    env={**os.environ,'PATH':str(bindir)+os.pathsep+os.environ['PATH'],'MKV_WORKFLOW_CALLS':str(parent/'calls.jsonl')}
    command=['bash',str(ROOT/'system/linux-elevhub/packaging/build_from_source.sh'),str(source),
             str(parent/'inputs'),str(parent/'tools'),str(released),str(parent/'work')]
    if release_set!='base':command+=['--release-set',release_set]
    if app:command+=['--app',app]
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
    return result,parent/'work',[json.loads(line) for line in (parent/'calls.jsonl').read_text(encoding='utf-8').splitlines()] if (parent/'calls.jsonl').exists() else []


class BuildSourceExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mkv-build-export-')
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.source = make_source(self.parent)

    def export(self, name='export', source=None, apps=()):
        return exporter.export_build_source(source or self.source, self.parent/name,
                                            self.parent/(name+'.report.json'), apps=apps)

    def test_git_exports_head_with_verified_viewer_and_only_selected_generated_files(self):
        (self.source/'system/web/unrelated.js').write_text('// uncommitted change\n')
        (self.source/'untracked-private.txt').write_text('never export this fixture')
        result = self.export()
        output = self.parent/'export'
        self.assertEqual((output/'system/web/unrelated.js').read_text(encoding='utf-8'), '// committed source\n')
        self.assertFalse((output/'untracked-private.txt').exists())
        self.assertFalse((output/'.git').exists())
        self.assertTrue((output/'system/web/vendor/viewer.js').is_file())
        self.assertFalse((output/'apps/folie/web/vendor').exists())
        self.assertEqual(result['mode'], 'git')
        self.assertEqual(len(result['generated']), 2)
        manifest = json.loads((output/public_source.MANIFEST_NAME).read_text(encoding='utf-8'))
        self.assertNotIn('system/web/vendor/viewer.js', {item['path'] for item in manifest['files']})
        self.assertEqual(json.loads((self.parent/'export.report.json').read_text(encoding='utf-8')), result)

    def test_selected_folie_output_is_gated_against_both_source_trees(self):
        result = self.export(apps=('folie',))
        self.assertEqual(len(result['generated']), 5)
        self.assertTrue((self.parent/'export/apps/folie/web/vendor/laser.js').is_file())
        (self.source/'apps/folie/web/laser.js').write_text('// changed without rebuild\n')
        with self.assertRaisesRegex(ValueError, 'web_source_hash_mismatch'):
            self.export(name='stale', apps=('folie',))
        self.assertFalse((self.parent/'stale').exists())

    def test_selected_3d_outputs_require_the_real_app_gate(self):
        result=self.export(apps=('3dprint',))
        self.assertEqual(len(result['generated']),7)
        (self.source/'apps/3dprint/web/vendor/model3d-manifold.wasm').write_bytes(b'tampered WASM')
        with self.assertRaisesRegex(ValueError,'model3d_bundle_hash_mismatch'):
            self.export(name='bad-3d',apps=('3dprint',))
        self.assertFalse((self.parent/'bad-3d').exists())

    def test_committed_unselected_file_cannot_be_silently_dropped(self):
        path = self.source/'private-note.txt'
        path.write_text('synthetic excluded note')
        subprocess.run(['git','-C',str(self.source),'add',path.name],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.source),'-c','user.name=Synthetic QA','-c','user.email=qa@example.invalid',
                        'commit','-qm','Unselected file'],check=True,capture_output=True)
        with self.assertRaisesRegex(ValueError, 'unselected_archived_file'):
            self.export()
        self.assertFalse((self.parent/'export').exists())

    def test_no_git_export_requires_full_bound_manifest_and_rejects_extras(self):
        self.export()
        public = self.parent/'export'
        result = self.export(name='rebuilt', source=public)
        self.assertEqual(result['mode'], 'public-source')
        (public/'extra.txt').write_text('synthetic unexpected file')
        with self.assertRaisesRegex(ValueError, 'unexpected_source_file'):
            self.export(name='extra', source=public)
        (public/'extra.txt').unlink()
        (public/'system/web/unrelated.js').write_text('// modified source\n')
        with self.assertRaisesRegex(ValueError, 'source_manifest_content_mismatch'):
            self.export(name='modified', source=public)

    def test_no_git_base_accepts_stale_unselected_fixed_outputs_without_copying_them(self):
        self.export(apps=('folie','3dprint'))
        public=self.parent/'export'
        (public/'apps/folie/web/vendor/laser.js').write_bytes(b'stale unselected Folie output')
        result=self.export(name='base',source=public)
        self.assertEqual(len(result['generated']),2)
        self.assertFalse((self.parent/'base/apps/folie/web/vendor').exists())
        self.assertFalse((self.parent/'base/apps/3dprint/web/vendor').exists())
        (public/'apps/folie/web/vendor/unknown.js').write_bytes(b'not a declared generated file')
        with self.assertRaisesRegex(ValueError,'unexpected_source_file'):
            self.export(name='unknown',source=public)

    def test_public_manifest_rejects_privileged_modes_and_posix_permission_changes(self):
        self.export()
        public=self.parent/'export';path=public/public_source.MANIFEST_NAME
        value=json.loads(path.read_text(encoding='utf-8'));original=value['files'][0]['mode']
        value['files'][0]['mode']='4755';path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'unsafe_source_manifest_mode'):
            self.export(name='unsafe-mode',source=public)
        value['files'][0]['mode']=original;path.write_text(json.dumps(value),encoding='utf-8')
        if os.name!='nt':
            self.assertFalse(exporter._portable_modes(public),'Linux fixtures must use a real POSIX filesystem')
            item=public/value['files'][0]['path'];item.chmod(int(original,8)^0o111)
            with self.assertRaisesRegex(ValueError,'source_manifest_mode_mismatch'):
                self.export(name='changed-mode',source=public)

    def test_no_git_policy_digest_and_manifest_inventory_are_checked(self):
        self.export()
        public = self.parent/'export'
        manifest_path=public/public_source.MANIFEST_NAME
        value=json.loads(manifest_path.read_text(encoding='utf-8'))
        value['sourcePolicySha256']='0'*64
        manifest_path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'source_policy_digest_mismatch'):
            self.export(name='policy',source=public)
        value['sourcePolicySha256']=hashlib.sha256((json.dumps(public_source._load_policy(public),ensure_ascii=False,
            sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()
        value['files'].pop()
        manifest_path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'source_manifest_inventory_mismatch'):
            self.export(name='inventory',source=public)

    def test_missing_receipt_tampered_output_missing_marker_and_overwrite_fail(self):
        (self.source/'system/web/vendor/viewer.js').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'web_output_hash_mismatch'):
            self.export()
        self.assertFalse((self.parent/'export').exists())
        (self.source/'system/web/vendor/web-build-manifest.json').unlink()
        with self.assertRaisesRegex(ValueError,'web_build_receipt_required'):
            self.export(name='receipt')
        with self.assertRaises(ValueError):
            exporter.export_build_source(self.source,self.source/'inside',self.parent/'inside.json')
        output=self.parent/'existing';output.mkdir();(output/'sentinel').write_text('preserve')
        with self.assertRaises(ValueError):self.export(name='existing')
        self.assertEqual((output/'sentinel').read_text(encoding='utf-8'),'preserve')
        (self.source/'REPOSITORY-LAYOUT.json').unlink()
        with self.assertRaisesRegex(ValueError,'repository_layout_missing'):
            self.export(name='marker')

    def test_unsafe_archive_members_are_rejected_before_writing_outside(self):
        for label, members in (
            ('traversal',[('../outside',b'x',None)]),
            ('symlink',[('link',b'',tarfile.SYMTYPE)]),
            ('hardlink',[('link',b'',tarfile.LNKTYPE)]),
            ('case',[('Code.js',b'a',None),('code.js',b'b',None)]),
            ('parent-case',[('Dir/a.js',b'a',None),('dir/b.js',b'b',None)]),
            ('drive',[('C:/escape',b'x',None)])):
            archive=self.parent/(label+'.tar')
            with tarfile.open(archive,'w') as tar:
                for name,data,kind in members:
                    item=tarfile.TarInfo(name);item.size=len(data)
                    if kind:item.type=kind;item.linkname='outside'
                    tar.addfile(item,io.BytesIO(data))
            with self.subTest(kind=label),self.assertRaises(ValueError):
                exporter._extract_archive(archive,self.parent/label)
        self.assertFalse((self.parent/'outside').exists())
        oversized=self.parent/'oversized.tar';item=tarfile.TarInfo('huge.js');item.size=exporter.MAX_FILE+1
        oversized.write_bytes(item.tobuf()+b'\0'*1024)
        with self.assertRaisesRegex(ValueError,'source_archive_file_size_limit'):
            exporter._extract_archive(oversized,self.parent/'oversized')


if __name__=='__main__':unittest.main()
