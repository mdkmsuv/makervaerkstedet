"""Public Linux updater behavior tests using isolated root fixtures only."""
from __future__ import annotations
import hashlib, importlib.util, io, json, os, sqlite3, subprocess, tarfile, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from scripts.github_update_service.linux import bind_installer

HERE=Path(__file__).resolve().parent; TEMPLATE=HERE/'templates/linux_install.py'
VERSION='2026.10.13'; PACKAGE_SHA='b'*64; OPERATION='github-linux-'+PACKAGE_SHA[:24]; TARGET='student-01'

def load_installer():
    source=bind_installer(TEMPLATE.read_text(encoding='utf-8'),VERSION,OPERATION,[TARGET],PACKAGE_SHA)
    module=importlib.util.module_from_spec(importlib.util.spec_from_loader('public_linux_installer',loader=None))
    exec(compile(source,'<public-linux-installer>','exec'),module.__dict__); return module
installer=load_installer()
BOUND_SOURCE=bind_installer(TEMPLATE.read_text(encoding='utf-8'),VERSION,OPERATION,[TARGET],PACKAGE_SHA)
ADMIN_HELPER_PATH=HERE.parents[1]/'linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update.py'
admin_spec=importlib.util.spec_from_file_location('local_admin_update_crosscomponent',ADMIN_HELPER_PATH)
admin_helper=importlib.util.module_from_spec(admin_spec); admin_spec.loader.exec_module(admin_helper)

def put(root,absolute,data):
    path=Path(root)/absolute.lstrip('/'); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data if isinstance(data,bytes) else data.encode()); return path

def make_payload(path):
    files={'linux-elevhub/server/elevhub/release.json':json.dumps({'release_id':VERSION}).encode(),
        'assets/CaDoodle-Kloster.jar':b'new jar','assets/CaDoodle-ApplicationInstall.zip':b'bootstrap',
        'linux-elevhub/ansible/roles/kiosk/tasks/main.yml':b'- name: Skriv klientrelease\n  ansible.builtin.copy:\n    src: version\n    dest: /etc/elevhub/release.json\n- name: Start lokale elevapps\n  ansible.builtin.systemd_service:\n    name: elevhub-lokal\n    state: started\n',
        'linux-elevhub/ansible/roles/kiosk/handlers/main.yml':b'- name: Genstart lokal elevapp-service\n  ansible.builtin.systemd_service:\n    name: elevhub-lokal\n    state: restarted\n',
        'linux-elevhub/ansible/roles/cadoodle/tasks/main.yml':b'[]\n','linux-elevhub/ansible/ansible.cfg':b'[defaults]\n',
        'linux-elevhub/ansible/roles/kiosk/files/elevbjaelke.py':b'new bar\n'}
    files.update({
        'linux-elevhub/ansible/roles/adminkonto/tasks/update-interface.yml':b'- name: update-only admin fixture\n  ansible.builtin.debug:\n    msg: fixture\n',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update.py':b'admin helper\n',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update-gui.py':b'admin gui\n',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-xsession.sh':b'admin xsession\n',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-session.sh':b'admin session\n',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-return.py':b'admin return\n',
        'linux-elevhub/ansible/roles/adminkonto/templates/admin-bash-profile.j2':b'admin profile {{ adminkonto_navn }}\n',
    })
    manifest={'version':VERSION,'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    files['manifest.json']=json.dumps(manifest).encode()
    with tarfile.open(path,'w:gz') as archive:
        for name,data in files.items():
            info=tarfile.TarInfo(name); info.size=len(data); info.mode=0o644; archive.addfile(info,io.BytesIO(data))
    return manifest

@unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda: -1)()==0,
                     'Linux root filesystem fixtures require an isolated Linux environment')
class PublicLinuxBootTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='github-linux-boot-'); self.root=Path(self.temp.name)/'machine'; self.root.mkdir()
        self.host=patch.object(installer.socket,'gethostname',return_value=TARGET); self.host.start()
        self.controller=patch.object(installer,'controller_available',return_value=True)
        self.controller.start()
        ui_fixture=hashlib.sha256(b'public-test-ui-only').hexdigest()
        enrollment_fixture=hashlib.sha256(b'public-test-enrollment-only').hexdigest()
        kiosk={'computer':TARGET,'server':'192.0.2.10','protokol':'https','usikker_test':False,'ui_token':ui_fixture,'elev':'elev','theme':'red','hub':'https://192.0.2.10:8773','ca_fil':'/etc/elevhub/ca.pem'}
        put(self.root,'/etc/elevhub/kiosk.json',json.dumps(kiosk)); put(self.root,'/etc/elevhub/enhed.json',json.dumps({'computer':TARGET,'token':enrollment_fixture})); put(self.root,'/etc/elevhub/ca.pem','-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n'); put(self.root,'/etc/elevhub/release.json','{"release_id":"old"}'); put(self.root,'/home/elev/Projekter/student.svg','student work')
        put(self.root,'/etc/elevhub/admin-login.json','{"username":"makeradmin"}')
        approval=self.root/'var/lib/elevhub/local-update'; approval.mkdir(parents=True); approval.chmod(0o700)
        (approval/'approval.json').write_text(json.dumps({'schema':1,'computer':TARGET,'version':VERSION,'package_sha256':PACKAGE_SHA,'restart_authorized':True})); (approval/'approval.json').chmod(0o600)
        self.jar='/home/elev/bin/CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.jar'; put(self.root,self.jar,'old jar'); self.package=Path(self.temp.name)/'payload.tar.gz'; make_payload(self.package)
    def tearDown(self): self.controller.stop(); self.host.stop(); self.temp.cleanup()
    def fake_run(self,args,**kwargs):
        if args and args[0]=='ansible-playbook':
            put(self.root,self.jar,b'new jar'); put(self.root,'/usr/local/lib/elevhub/elevbjaelke.py',b'new bar\n')
            put(self.root,'/usr/local/sbin/elevhub-local-update',b'admin helper\n')
            put(self.root,'/usr/local/bin/elevhub-local-update-gui',b'admin gui\n')
            put(self.root,'/usr/local/bin/elevhub-admin-xsession',b'admin xsession\n')
            put(self.root,'/usr/local/bin/elevhub-admin-session',b'admin session\n')
            put(self.root,'/usr/local/sbin/elevhub-admin-return',b'admin return\n')
        return subprocess.CompletedProcess(args,0)
    def stage(self):
        with patch.object(installer,'controller_available',return_value=True): return installer.stage(self.root,TARGET,self.package,installer.digest(self.package),self.package.stat().st_size,BOUND_SOURCE,self.fake_run)
    def test_stage_binds_scope_and_arms_next_boot(self):
        self.assertEqual(self.stage()['status'],'staged_awaiting_restart'); self.assertTrue(installer.armed(self.root,TARGET)); state=installer.at(self.root,installer.STATE); self.assertEqual(installer.journal(state)['computer'],TARGET); self.assertEqual(installer.journal(state)['package_sha256'],PACKAGE_SHA); self.assertEqual(installer.read_json(state/'pins.json')['package_sha256'],PACKAGE_SHA)

    def test_stage_rejects_wrong_or_missing_local_approval(self):
        approval=self.root/'var/lib/elevhub/local-update/approval.json'
        approval.write_text(json.dumps({'schema':1,'computer':TARGET,'version':VERSION,'package_sha256':'c'*64,'restart_authorized':True})); approval.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'local_admin_required'): self.stage()
        approval.unlink()
        with self.assertRaisesRegex(ValueError,'local_admin_required'): self.stage()
        approval.write_text(json.dumps({'schema':1,'computer':TARGET,'version':VERSION,'package_sha256':PACKAGE_SHA,'restart_authorized':True})); approval.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'local_admin_required'): self.stage()

    def test_ordinary_reboot_waits_for_explicit_restart_approval(self):
        approval=self.root/'var/lib/elevhub/local-update/approval.json'
        approval.write_text(json.dumps({'schema':1,'computer':TARGET,'version':VERSION,'package_sha256':PACKAGE_SHA,'restart_authorized':False})); approval.chmod(0o600)
        self.stage(); before=installer.tree(self.root/'home/elev/Projekter')
        self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'staged_awaiting_restart')
        self.assertEqual(installer.tree(self.root/'home/elev/Projekter'),before); self.assertEqual((self.root/self.jar.lstrip('/')).read_bytes(),b'old jar')

    def test_explicit_restart_approval_allows_staged_apply(self):
        approval=self.root/'var/lib/elevhub/local-update/approval.json'
        approval.write_text(json.dumps({'schema':1,'computer':TARGET,'version':VERSION,'package_sha256':PACKAGE_SHA,'restart_authorized':False})); approval.chmod(0o600)
        self.stage(); approval.write_text(json.dumps({'schema':1,'computer':TARGET,'version':VERSION,'package_sha256':PACKAGE_SHA,'restart_authorized':True})); approval.chmod(0o600)
        self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'complete')

    def test_local_admin_helper_approval_stage_wait_restart_apply_contract(self):
        paths=admin_helper.Paths.for_test(self.root)
        admin_helper.write_json(paths.admin,{'username':'makeradmin'})
        (self.root/'etc/elevhub/release.json').write_text('{"version":"2026.10.12"}')
        state={'schema':1,'computer':TARGET,'available':{'version':VERSION,
            'package_sha256':PACKAGE_SHA,'release_id':'fixture-release','notes':'fixture'},
            'job':None,'request':None,'feed_state':'ready'}
        client=Mock(); client.hent_json.side_effect=[state,{**state,'request':{'version':VERSION,'package_sha256':PACKAGE_SHA,'status':'requested'}}]
        prepared=admin_helper.prepare_update(paths,client=client,request_id_factory=lambda:'1'*32)
        self.assertFalse(prepared['install_ready']); self.assertFalse(json.loads(paths.approval.read_text())['restart_authorized'])
        self.stage(); self.assertTrue(admin_helper._local_stage_ready(paths,TARGET))
        self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'staged_awaiting_restart')
        self.assertEqual((self.root/self.jar.lstrip('/')).read_bytes(),b'old jar')
        reboot=[]; result=admin_helper._restart(paths,run=lambda argv,check:reboot.append((argv,check)))
        self.assertEqual(result['status'],'restarting'); self.assertEqual(reboot,[(['/usr/bin/systemctl','reboot'],True)])
        self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'complete')

    def test_generated_update_playbook_syntax_uses_update_interface_only(self):
        self.stage(); state=installer.at(self.root,installer.STATE)
        bundle,manifest=installer.verify_staged(state)
        kiosk,device,previous=installer.profile(self.root,TARGET)
        ansible,playbook=installer.make_variables(self.root,state,kiosk,device,previous,manifest)
        document=json.loads(playbook.read_text())
        roles=document[0]['roles']
        admin_roles=[role for role in roles if isinstance(role,dict) and role.get('tasks_from')=='update-interface']
        self.assertEqual(len(admin_roles),1)
        self.assertNotIn('adminkonto/tasks/main.yml',json.dumps(document))
        result=subprocess.run(['ansible-playbook','--syntax-check',str(playbook),'-i',str(state/'inventory.json'),
                               '-e','@'+str(state/'variables.json')],cwd=ansible,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
    def test_power_loss_during_preapply_leaves_staged_and_retries(self):
        self.stage()
        with patch.object(installer,'backup',side_effect=KeyboardInterrupt('power loss')):
            with self.assertRaises(KeyboardInterrupt): installer.boot(self.root,TARGET,self.fake_run)
        self.assertEqual(installer.journal(installer.at(self.root,installer.STATE))['status'],'staged_awaiting_restart'); self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'complete')
    def test_partial_apply_rolls_back_and_preserves_project(self):
        self.stage(); before=installer.tree(self.root/'home/elev/Projekter')
        def fail(args,**kwargs):
            result=self.fake_run(args,**kwargs)
            if args and args[0]=='ansible-playbook': raise subprocess.CalledProcessError(2,args)
            return result
        self.assertEqual(installer.boot(self.root,TARGET,fail),'failed_rolled_back'); self.assertEqual(installer.tree(self.root/'home/elev/Projekter'),before); self.assertEqual((self.root/self.jar.lstrip('/')).read_text(),'old jar')

    def test_recovery_does_not_require_local_approval(self):
        self.stage(); state=installer.at(self.root,installer.STATE); installer.backup(self.root,state)
        installer.atomic_json(state/'preservation.json',{'projects':installer.tree(self.root/'home/elev/Projekter'),'identity':installer.tree(self.root/'etc/elevhub')})
        installer.atomic_json(state/'journal.json',{'release':VERSION,'computer':TARGET,'operation':OPERATION,
            'package_sha256':PACKAGE_SHA,'status':'applying'})
        (self.root/'var/lib/elevhub/local-update/approval.json').unlink()
        self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'failed_rolled_back')
    def test_legacy_project_metadata_change_is_consistent(self):
        projects=self.root/'home/elev/Projekter/.elevhub'; projects.mkdir(parents=True); put(self.root,'/home/elev/Projekter/.elevhub/filindeks.json','{}'); project='a'*32; blob=b'saved version'; put(self.root,f'/home/elev/Projekter/.elevhub/versioner/{project}/1.bin',blob); database=projects/'projekter.sqlite3'
        with sqlite3.connect(database) as db:
            db.executescript('CREATE TABLE projects (id TEXT, revision INTEGER); CREATE TABLE versions (project TEXT, revision INTEGER, digest TEXT, size INTEGER);'); db.execute('INSERT INTO projects VALUES (?,?)',(project,1)); db.execute('INSERT INTO versions VALUES (?,?,?,?)',(project,1,hashlib.sha256(blob).hexdigest(),len(blob)))
        expected=installer.tree(self.root/'home/elev/Projekter'); put(self.root,'/home/elev/Projekter/.elevhub/filindeks.json','{"scan":{"id":"'+project+'","revision":1}}'); self.assertEqual(installer.verify_projects(self.root/'home/elev/Projekter',expected,legacy=True),'legacy_metadata_validated')
    def test_dbus_reload_failure_requires_review_without_service_restart(self):
        self.stage(); state=installer.at(self.root,installer.STATE); installer.backup(self.root,state); installer.atomic_json(state/'preservation.json',{'projects':installer.tree(self.root/'home/elev/Projekter'),'identity':installer.tree(self.root/'etc/elevhub')}); installer.atomic_json(state/'journal.json',{'release':VERSION,'computer':TARGET,'status':'applying'})
        def fail_reload(args,**kwargs):
            if args==['systemctl','reload','dbus.service']: raise subprocess.CalledProcessError(1,args)
            return self.fake_run(args,**kwargs)
        with self.assertRaisesRegex(RuntimeError,'recovery_required'): installer.boot(self.root,TARGET,fail_reload)
        self.assertEqual(installer.journal(state)['status'],'recovery_required')
    def test_terminal_status_projection_is_public_and_secret_free(self):
        put(self.root,'/proc/sys/kernel/random/boot_id','01234567-89ab-cdef-0123-456789abcdef\n'); installer.publish_status(self.root,'complete'); path=self.root/installer.STATUS_PATH.lstrip('/'); value=json.loads(path.read_text()); self.assertEqual(value['status'],'complete'); self.assertEqual(path.stat().st_mode&0o777,0o644); self.assertEqual(set(value),{'schema','status','phase','release','updated_at','boot_id'}); self.assertNotIn('token',path.read_text())
    def test_terminal_disarm_removes_only_operation_dependency(self):
        self.stage(); self.assertTrue(installer.dependency_path(self.root).exists()); self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'complete'); self.assertFalse(installer.dependency_path(self.root).exists())
    def test_missing_or_changed_update_safety_helper_cannot_confirm_installation(self):
        source='linux-elevhub/ansible/roles/kiosk/files/update_status.py'
        target='/usr/local/lib/elevhub/update_status.py'
        manifest={'files':{'assets/CaDoodle-Kloster.jar':hashlib.sha256(b'old jar').hexdigest(),
                           source:hashlib.sha256(b'reviewed helper').hexdigest()}}
        with self.assertRaises((ValueError,FileNotFoundError)):
            installer.verify_installed(self.root,manifest)
        put(self.root,target,b'wrong helper')
        with self.assertRaises(ValueError):
            installer.verify_installed(self.root,manifest)
        put(self.root,target,b'reviewed helper')
        self.assertEqual(installer.verify_installed(self.root,manifest)[target],
                         hashlib.sha256(b'reviewed helper').hexdigest())
    def test_restore_preserves_symlink_ownership(self):
        source=self.root/'saved-link'; target=self.root/'live-link'
        source.symlink_to('existing-target')
        os.chown(source,123,456,follow_symlinks=False)
        target.symlink_to('old-target')
        installer._restore_entry(source,target)
        self.assertEqual(os.readlink(target),'existing-target')
        self.assertEqual((target.lstat().st_uid,target.lstat().st_gid),(123,456))

    def test_power_loss_mid_restore_keeps_boot_files_and_retries(self):
        put(self.root,'/etc/passwd','root:x:0:0:root:/root:/bin/sh\n')
        self.stage(); state=installer.at(self.root,installer.STATE)
        installer.backup(self.root,state)
        installer.atomic_json(state/'preservation.json',{
            'projects':installer.tree(self.root/'home/elev/Projekter'),
            'identity':installer.tree(self.root/'etc/elevhub'),'preservation_schema':2})
        installer.atomic_json(state/'journal.json',{
            'release':VERSION,'computer':TARGET,'status':'applying'})
        original=installer._restore_entry; calls=0
        def interrupt(source,target):
            nonlocal calls
            calls+=1
            if calls==2: raise KeyboardInterrupt('power loss during restore')
            return original(source,target)
        with patch.object(installer,'_restore_entry',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): installer.boot(self.root,TARGET,self.fake_run)
        self.assertTrue((self.root/'etc/passwd').exists())
        self.assertTrue((self.root/'etc/systemd/system'/installer.SERVICE).exists())
        self.assertTrue(installer.dependency_path(self.root).exists())
        self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'failed_rolled_back')

    def test_recovery_after_writer_restart_accepts_only_valid_metadata_change(self):
        state_dir=self.root/'home/elev/Projekter/.elevhub'; state_dir.mkdir()
        put(self.root,'/home/elev/Projekter/.elevhub/filindeks.json','{}')
        database=state_dir/'projekter.sqlite3'
        with sqlite3.connect(database) as db:
            db.executescript('CREATE TABLE projects (id TEXT, revision INTEGER); '
                            'CREATE TABLE versions (project TEXT, revision INTEGER, digest TEXT, size INTEGER);')
        self.stage()
        def interrupt(args,**kwargs):
            if args==['systemctl','restart','elevhub-lokal']:
                with sqlite3.connect(database) as db:
                    db.execute('CREATE TABLE scanner_touch (value TEXT)')
                raise KeyboardInterrupt('power loss after writer release')
            return self.fake_run(args,**kwargs)
        with self.assertRaises(KeyboardInterrupt): installer.boot(self.root,TARGET,interrupt)
        self.assertTrue(installer.read_json(installer.at(self.root,installer.STATE)/'preservation.json')['project_writer_released'])
        self.assertEqual(installer.boot(self.root,TARGET,self.fake_run),'failed_rolled_back')

    def test_local_manifest_and_legacy_frontend_survive_candidate_apply(self):
        local=self.root/'etc/elevhub/local-appearance'; local.mkdir()
        css=put(self.root,'/etc/elevhub/local-appearance/website.css','body{color:#123456}')
        put(self.root,'/etc/elevhub/local-appearance/manifest.json',json.dumps({
            'schemaVersion':1,'school_name':'Example school','theme':'green',
            'style':{'website_css':'website.css','website_css_sha256':installer.digest(css)}}))
        page=put(self.root,'/usr/local/share/elevhub/start.html',
                 '<img src="assets/klostermarksskolen-logo.png">')
        before=installer.tree(local); original_page=page.read_bytes()
        self.stage()
        def candidate(args,**kwargs):
            result=self.fake_run(args,**kwargs)
            if args[0]=='ansible-playbook':
                css.write_text('candidate css'); page.write_text('candidate page')
            return result
        self.assertEqual(installer.boot(self.root,TARGET,candidate),'complete')
        self.assertEqual(installer.tree(local),before)
        self.assertEqual(page.read_bytes(),original_page)
        receipt=installer.journal(installer.at(self.root,installer.STATE))['installed_files']
        self.assertEqual(receipt['/usr/local/share/elevhub/start.html'],installer.digest(page))
        self.assertEqual(receipt['/etc/elevhub/local-appearance'],installer.tree(local))

    def test_generic_appearance_survives_two_overlays_without_rolling_back_code(self):
        web='/usr/local/share/elevhub/'
        old_start=('<link rel="stylesheet" href="assets/creative-hub.css"><body>'
                   '<a class="brand">Example School</a><p class="eyebrow">EXAMPLE WORKSHOPS</p>'
                   '<figure class="hero-photo"><img src="assets/example-hero.webp"></figure>'
                   '<script src="assets/new-runtime.js"></script><footer class="hub-footer">Example</footer></body>')
        old_css=':root{--brand:#123456;--deep:#234567}'
        old_workshops="const legacy='old workshop code'; const workshops={maker:{image:'example-card.webp'}};"
        old_creative="const legacy='old creative code'; document.title='Værkstedet · Example'; const hero={image:'example-hero.webp'};"
        old_native=("BRAND, BRAND_MOERK = '#123456', '#234567'\n"
                    "THEME_COLORS = {'red': ('#123456', '#234567')}\n"
                    "def old_native_logic(): return 'old native'\n")
        old_startup=("BRAND = \"#123456\"\nBRAND_DARK = \"#234567\"\n"
                     "THEME_COLORS = {'red': ('#123456', '#234567')}\n"
                     "def old_startup_logic(): return 'old startup'\n")
        start=put(self.root,web+'start.html',old_start); css=put(self.root,web+'assets/creative-hub.css',old_css)
        workshops=put(self.root,web+'assets/workshops.js',old_workshops); creative=put(self.root,web+'assets/creative-hub.js',old_creative)
        native=put(self.root,'/usr/local/lib/elevhub/elevbjaelke.py',old_native); startup=put(self.root,'/usr/local/lib/elevhub/startup_screen.py',old_startup)
        for name,data in [('example-hero.webp',b'example hero'),('example-card.webp',b'example card'),('example-logo.svg',b'example logo')]:
            put(self.root,web+'assets/'+name,data)
        first=self.root/'appearance-first'; first.mkdir()
        installer.snapshot_appearance(self.root,first)
        start.write_text('<link rel="stylesheet" href="assets/creative-hub.css"><body data-theme="red">'
                         '<a class="brand">NEW RELEASE</a><p class="eyebrow">NEW WORKSHOPS</p>'
                         '<figure class="hero-photo"><img src="assets/hub-bauhaus-v1.png"></figure>'
                         '<script>new_start_logic()</script><footer class="hub-footer">NEW</footer></body>')
        css.write_text(':root{--brand:#c62b2f;--deep:#8c1d24}')
        workshops.write_text("const release='new workshop code'; const workshops={maker:{image:'workshop-maker-bauhaus-v1.png'}};")
        creative.write_text("const release='new creative code'; document.title='Værkstedet · Makerværkstedet'; const hero={image:'hub-bauhaus-v1.png'};")
        native.write_text("BRAND, BRAND_MOERK = '#c62b2f', '#8c1d24'\n"
                          "THEME_COLORS = {'red': ('#c62b2f', '#8c1d24')}\n"
                          "def new_native_logic(): return 'new native'\n")
        startup.write_text("BRAND = \"#c62b2f\"\nBRAND_DARK = \"#8c1d24\"\n"
                           "THEME_COLORS = {'red': ('#c62b2f', '#8c1d24')}\n"
                           "def new_startup_logic(): return 'new startup'\n")
        installer.restore_appearance(self.root,first)
        self.assertIn('new_start_logic()',start.read_text()); self.assertIn('Example School',start.read_text())
        self.assertEqual((css.parent/'local-school.css').read_text(),old_css); self.assertEqual(css.read_text(),':root{--brand:#c62b2f;--deep:#8c1d24}')
        self.assertIn("release='new workshop code'",workshops.read_text()); self.assertIn("example-card.webp",workshops.read_text())
        self.assertIn("Værkstedet · Makerværkstedet",creative.read_text()); self.assertIn("new creative code",creative.read_text())
        self.assertIn("'#123456', '#234567'",native.read_text()); self.assertIn('new native',native.read_text())
        self.assertIn('BRAND = "#123456"',startup.read_text()); self.assertIn('new startup',startup.read_text())
        self.assertNotIn('#c62b2f',native.read_text()); self.assertNotIn('#c62b2f',startup.read_text())
        second=self.root/'appearance-second'; second.mkdir(); installer.snapshot_appearance(self.root,second)
        css.write_text('neutral second release css'); installer.restore_appearance(self.root,second)
        self.assertEqual((css.parent/'local-school.css').read_text(),old_css); self.assertEqual(css.read_text(),'neutral second release css')

    def test_generic_appearance_rollback_restores_legacy_files(self):
        web='/usr/local/share/elevhub/'
        start=put(self.root,web+'start.html','<body><a class="brand">Example School</a><figure class="hero-photo"><img src="assets/example-hero.webp"></figure></body>')
        css=put(self.root,web+'assets/creative-hub.css',':root{--brand:#123456}')
        put(self.root,web+'assets/example-hero.webp',b'example hero')
        originals={path:path.read_bytes() for path in (start,css)}
        self.stage()
        def fail(args,**kwargs):
            result=self.fake_run(args,**kwargs)
            if args and args[0]=='ansible-playbook':
                start.write_text('new candidate frontend'); css.write_text('new candidate css')
                raise subprocess.CalledProcessError(2,args)
            return result
        self.assertEqual(installer.boot(self.root,TARGET,fail),'failed_rolled_back')
        self.assertEqual({path:path.read_bytes() for path in (start,css)},originals)

    def test_invalid_local_manifest_is_refused_before_arming(self):
        local=self.root/'etc/elevhub/local-appearance'; local.mkdir()
        manifest=local/'manifest.json'
        for value in ([],{'schemaVersion':1,'school_name':'Example school','theme':'green',
                          'style':{'static':{'/x.css':{'path':'missing.css','sha256':'0'*64}}}}):
            with self.subTest(value=value):
                manifest.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'local_appearance_requires_migration'):
                    self.stage()
                self.assertFalse(installer.dependency_path(self.root).exists())

    def test_neutral_frontend_receives_new_release(self):
        page=put(self.root,'/usr/local/share/elevhub/start.html','<img src="assets/hub-bauhaus-v1.png">')
        self.stage()
        def candidate(args,**kwargs):
            result=self.fake_run(args,**kwargs)
            if args[0]=='ansible-playbook': page.write_text('new frontend')
            return result
        self.assertEqual(installer.boot(self.root,TARGET,candidate),'complete')
        self.assertEqual(page.read_text(),'new frontend')

if __name__=='__main__': unittest.main(verbosity=2)
