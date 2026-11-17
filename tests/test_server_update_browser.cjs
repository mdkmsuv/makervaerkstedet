/* Rendered server-update journey. Every API write is intercepted; no installer runs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {default:AxeBuilder} = require('@axe-core/playwright');
const root = path.resolve(__dirname,'..');
const web = path.join(root,'system/linux-elevhub/server/elevhub/web');
const origin = 'http://127.0.0.1:18879';
const endpoint = '/api/admin/server-update';
const hash = 'a'.repeat(64);
let browser, count=0;
const output = process.env.ELEVHUB_TEST_OUTPUT;
const read = name => fs.existsSync(path.join(web,name)) ? fs.readFileSync(path.join(web,name),'utf8') : '';
async function fixture(entry='kontrolpanel.html',viewport={width:1366,height:900},options={}) {
  const context=await browser.newContext({viewport});
  const page=await context.newPage(), sent=[], reads=[], errors=[], receipts=new Map();
  const state={schema:1,available:true,installed_version:'2.1.0-20261117.2',candidate:null,state:'idle',command_id:null,code:null,downloaded_bytes:0,total_bytes:0,updated_at:null,queues_paused:null};
  const control={status:200,offline:false,drop:false,badReceipt:false,oldSnapshot:null,postStatus:202};
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const r=route.request(),u=new URL(r.url());
    if(u.pathname==='/fixture')return route.fulfill({contentType:'text/html',body:read(entry)});
    if(u.pathname===endpoint && r.method()==='GET'){
      reads.push('server');
      if(control.offline)return route.abort();
      return route.fulfill({status:control.status,json:control.status===200?(control.oldSnapshot||state):{error:'authentication_required'}});
    }
    if(u.pathname.startsWith(endpoint+'/') && r.method()==='POST'){
      const body=r.postDataJSON();sent.push({path:u.pathname,body});
      assert.match(body.command_id,/^[a-f0-9-]{36}$/);
      assert([endpoint+'/check',endpoint+'/install'].includes(u.pathname));
      assert.deepEqual(Object.keys(body).sort(),(u.pathname.endsWith('/check')?['command_id']:['command_id','confirmation','manifest_sha256']).sort());
      if(u.pathname.endsWith('/install')){assert.equal(body.confirmation,'OPDATER SERVER');assert.equal(body.manifest_sha256,hash);}
      if(control.postStatus!==202)return route.fulfill({status:control.postStatus,json:{error:'candidate_changed'}});
      let receipt=receipts.get(body.command_id);
      if(!receipt){receipt={schema:1,accepted:true,command_id:body.command_id};receipts.set(body.command_id,receipt);state.command_id=body.command_id;state.state=u.pathname.endsWith('/check')?'checking':'downloading';}
      if(control.drop){control.drop=false;return route.abort();}
      return route.fulfill({status:202,json:control.badReceipt?{...receipt,command_id:'wrong'}:receipt});
    }
    if(u.pathname.startsWith('/api/') && r.method()!=='GET')throw Error('Unexpected write: '+u.pathname);
    if(u.pathname==='/api/admin/updates')return route.fulfill({json:{schema:1,revision:1,catalog:[],devices:[],catalog_errors:[],unbound_devices:[]}});
    if(u.pathname==='/api/admin/updates/source')return route.fulfill({json:{schema:1,enabled:false,state:'idle'}});
    if(options.realAuth && u.pathname==='/api/laerer/session')return route.fulfill({json:{logget_ind:true,kode_valgt:true,lokal:false}});
    if(options.realAuth && u.pathname==='/api/laerer/oversigt')return route.fulfill({json:{computere:[],apps:[],pause:{paused:false}}});
    if(options.realAuth && u.pathname==='/api/makerspace/state')return route.fulfill({json:{sources:{printers:'ok'},inventory:[]}});
    if(options.realAuth && ['/kontrolpanel.js','/laerer.js'].includes(u.pathname))return route.fulfill({contentType:'application/javascript',body:read(u.pathname.slice(1))});
    if(options.realAuth && u.pathname==='/app-admin.js')return route.fulfill({contentType:'application/javascript',body:'window.AppAdmin={start(){},stop(){}};'});
    if(options.realAuth && u.pathname==='/school-profile.js')return route.fulfill({contentType:'application/javascript',body:'window.SchoolProfile={start(){},stop(){}};'});
    if(['/kontrolpanel.js','/laerer.js','/app-admin.js','/school-profile.js','/system-overview.js'].includes(u.pathname))return route.fulfill({contentType:'application/javascript',body:''});
    const name=u.pathname.slice(1);
    return route.fulfill({contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/plain',body:read(name)});
  });
  async function activate(on=true){await page.evaluate(on=>{
    document.getElementById('panel').hidden=!on;
    const login=document.getElementById('login');if(login)login.hidden=on;
    const nav=document.getElementById('nav');if(nav)nav.hidden=!on;
    document.getElementById('app-shell')?.classList.toggle('signed-in',on);
    document.querySelectorAll('[data-view]').forEach(e=>e.hidden=e.dataset.view!=='teknik');
    if(window.ServerUpdate)window.ServerUpdate.onAuthFailure(()=>{document.getElementById('panel').hidden=true;document.getElementById('login').hidden=false;window.UpdateAdmin.stop();});
    if(on)window.UpdateAdmin.start();else window.UpdateAdmin.stop();
  },on);}
  async function refresh(){const response=page.waitForResponse(r=>new URL(r.url()).pathname===endpoint&&r.request().method()==='GET');await page.locator('#server-update-refresh').click();await(await response).finished();await page.waitForTimeout(30);}
  async function available(){Object.assign(state,{state:'available',candidate:{version:'2.1.0-20261117.3',manifest_sha256:hash,size:1024*1024},total_bytes:1024*1024});await refresh();}
  async function confirm(){await page.locator('#server-update-install').click();await page.locator('#server-update-confirm').waitFor();await page.locator('#server-update-confirm-submit').click();}
  async function load(){await page.goto(origin+'/fixture#teknik');}
  await load();
  return {context,page,state,control,sent,reads,receipts,errors,activate,refresh,available,confirm,load};
}
async function check(name,fn){await fn();count++;console.log('PASS '+name);}
(async()=>{try{
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||(process.platform==='win32'?'msedge':undefined)});
  for(const entry of ['kontrolpanel.html','laerer.html'])await check(entry+': authorized lifecycle, search and explicit confirmation',async()=>{
    const f=await fixture(entry);try{
      assert.equal(await f.page.locator('#server-update-card').count(),1,'Both admin entrances need the stable server card');
      assert.equal(f.reads.length,0);assert.equal(f.sent.length,0);
      await f.activate();await f.page.locator('#server-update-status').filter({hasText:'Søg'}).waitFor();
      assert(f.reads.length>0);assert.equal(f.sent.length,0,'Opening the panel never searches or installs');
      await f.page.locator('#server-update-check').click();await f.page.locator('#server-update-status').filter({hasText:'Søger'}).waitFor();
      assert.equal(f.sent.length,1);assert.equal(f.sent[0].path,endpoint+'/check');
      await f.available();await f.page.locator('#server-update-install').click();
      assert.equal(await f.page.evaluate(()=>document.activeElement.id),'server-update-confirm-cancel');
      assert.match(await f.page.locator('#server-update-confirm').innerText(),/køerne.*pause/is);
      assert.match(await f.page.locator('#server-update-confirm').innerText(),/ikke.*genstartes/is);
      await f.page.locator('#server-update-confirm-cancel').click();
      assert.equal(f.sent.length,1);assert.equal(await f.page.evaluate(()=>document.activeElement.id),'server-update-install');
      await f.confirm();await f.page.locator('#server-update-status').filter({hasText:'Henter'}).waitFor();
      assert.equal(f.sent.length,2);assert.equal(f.sent[1].path,endpoint+'/install');
      await f.activate(false);const before=f.reads.length;await f.page.waitForTimeout(50);assert.equal(f.reads.length,before);assert.deepEqual(f.errors,[]);
    }finally{await f.context.close();}
  });
  await check('Download, expected disconnect, login recovery and backend-confirmed completion',async()=>{
    const f=await fixture();try{
      await f.activate();await f.available();await f.confirm();await f.page.locator('#server-update-status').filter({hasText:'Henter'}).waitFor();
      Object.assign(f.state,{state:'downloading',downloaded_bytes:524288,total_bytes:1048576});await f.refresh();assert.equal(await f.page.locator('#server-update-progress').getAttribute('value'),'524288');
      f.state.state='installing';f.state.queues_paused=true;await f.refresh();assert.equal(await f.page.locator('#server-update-progress').isVisible(),false);
      assert.equal(await f.page.locator('#server-update-resume').isVisible(),false,'Do not invite queue changes while installation is running');
      f.control.offline=true;await f.page.locator('#server-update-refresh').click();await f.page.locator('#server-update-status').filter({hasText:'Henter status igen automatisk'}).waitFor();assert.equal(await f.page.locator('#server-update-install').isEnabled(),false);
      assert.match(await f.page.locator('#server-update-queues').innerText(),/Senest kendte/);
      f.control.offline=false;f.control.status=401;await f.page.locator('#server-update-refresh').click();await f.page.locator('#login').waitFor();
      assert((await f.page.evaluate(()=>sessionStorage.getItem('makervaerkstedet.server-update.v1'))),'Operation identity survives login');
      f.control.status=200;await f.activate();Object.assign(f.state,{state:'succeeded',installed_version:'2.1.0-20261117.2'});await f.refresh();assert.doesNotMatch(await f.page.locator('#server-update-status').innerText(),/er opdateret til/);
      f.state.installed_version='2.1.0-20261117.3';await f.refresh();assert.match(await f.page.locator('#server-update-status').innerText(),/er opdateret til 2\.1\.0-20261117\.3/);assert.match(await f.page.locator('#server-update-queues').innerText(),/pause/);
      assert.equal(f.sent.length,1,'Reconnect and login never resubmit an install');
      const accessible=await new AxeBuilder({page:f.page}).include('#server-update-card').analyze();assert.deepEqual(accessible.violations.map(v=>v.id),[]);
      if(output){fs.mkdirSync(output,{recursive:true});await f.page.locator('#server-update-card').screenshot({path:path.join(output,'server-update-succeeded.png')});}
    }finally{await f.context.close();}
  });
  await check('Lost receipt survives reload and can only resend the same command',async()=>{
    const f=await fixture();try{
      await f.activate();await f.available();f.control.oldSnapshot=structuredClone(f.state);f.control.drop=true;await f.confirm();await f.page.locator('#server-update-retry').waitFor();
      const original=structuredClone(f.sent[0]);await f.load();await f.activate();await f.page.locator('#server-update-retry').waitFor();await f.page.locator('#server-update-retry').click();
      await f.page.locator('#server-update-retry').waitFor({state:'hidden'});assert.deepEqual(f.sent[1],original);assert.equal(f.receipts.size,1);
    }finally{await f.context.close();}
  });
  await check('Failed, interrupted, unavailable, malicious and stale states fail closed',async()=>{
    const f=await fixture();try{
      await f.activate();await f.available();Object.assign(f.state,{state:'failed',code:'signature_failed',queues_paused:false});await f.refresh();assert.equal(await f.page.locator('#server-update-install').isEnabled(),false);assert.match(await f.page.locator('#server-update-error').innerText(),/ikke/);
      f.state.state='interrupted';await f.refresh();assert.match(await f.page.locator('#server-update-status').innerText(),/afklaret|afbrudt/);
      assert.equal(await f.page.locator('#server-update-check').innerText(),'Afklar opdateringen');assert.equal(await f.page.locator('#server-update-check').isEnabled(),true);
      Object.assign(f.state,{state:'idle',available:false});await f.refresh();assert.equal(await f.page.locator('#server-update-check').isEnabled(),false);
      Object.assign(f.state,{state:'available',available:true,candidate:{version:'<img src=x onerror="window.injected=true">',manifest_sha256:hash,size:20}});await f.refresh();assert.equal(await f.page.locator('#server-update-card img').count(),0);assert.equal(await f.page.evaluate(()=>Boolean(window.injected)),false);
      f.state.schema=99;await f.refresh();assert.equal(await f.page.locator('#server-update-install').isEnabled(),false);assert.equal(f.sent.length,0);
      Object.assign(f.state,{schema:1,installed_version:null});await f.refresh();assert.equal(await f.page.locator('#server-update-install').isEnabled(),false);assert.match(await f.page.locator('#server-update-status').innerText(),/ukendt/);
      f.state.installed_version='2.1.0-20261117.2';await f.refresh();f.control.postStatus=409;await f.confirm();await f.page.locator('#server-update-error').filter({hasText:'afvist'}).waitFor();assert.equal(f.sent.length,1);assert.equal(f.receipts.size,0);assert.equal(await f.page.locator('#server-update-retry').isVisible(),false);
    }finally{await f.context.close();}
  });
  await check('Interrupted reconciliation only checks; retained download is informational after confirmed success',async()=>{
    const f=await fixture();try{
      await f.activate();await f.available();Object.assign(f.state,{state:'interrupted',command_id:'12345678-1234-4234-8234-123456789abc'});await f.refresh();
      assert.match(await f.page.locator('#server-update-status').innerText(),/kun.*tidligere/);assert.match(await f.page.locator('#server-update-status').innerText(),/ikke.*igen/);
      const reads=f.sent.length;await f.refresh();assert.equal(f.sent.length,reads,'GET refresh remains read-only');
      await f.page.locator('#server-update-check').click();await f.page.locator('#server-update-status').filter({hasText:'Søger'}).waitFor();assert.equal(f.sent.length,1);assert.equal(f.sent[0].path,endpoint+'/check');
      f.state.state='interrupted';await f.refresh();assert.match(await f.page.locator('#server-update-status').innerText(),/Kontakt IT/);
      Object.assign(f.state,{state:'succeeded',installed_version:f.state.candidate.version,code:'download_retained'});await f.refresh();
      assert.match(await f.page.locator('#server-update-status').innerText(),/er opdateret til/);assert.equal(await f.page.locator('#server-update-error').isVisible(),false);
      assert.match(await f.page.locator('#server-update-detail').textContent(),/Opdateringen lykkedes.*installationsfil.*bevaret/i);assert.doesNotMatch(await f.page.locator('#server-update-detail').textContent(),/Fejlkode/);
      await f.page.locator('#server-update-card summary').click();if(output)await f.page.locator('#server-update-card').screenshot({path:path.join(output,'server-update-retained-download.png')});
    }finally{await f.context.close();}
  });
  await check('Real scripts on both admin entrances route 401 to normal login and password focus',async()=>{
    for(const entry of ['kontrolpanel.html','laerer.html']){
      const f=await fixture(entry,{width:1366,height:900},{realAuth:true});try{
        await f.page.locator('#panel').waitFor();await f.available();await f.confirm();await f.page.locator('#server-update-status').filter({hasText:'Henter'}).waitFor();
        const identity=f.sent[0].body.command_id;f.control.status=401;await f.refresh();await f.page.locator('#login').waitFor();
        const loginField=entry==='kontrolpanel.html'?'password':'login-kode';assert.equal(await f.page.evaluate(()=>document.activeElement.id),loginField);
        assert.match(await f.page.locator(entry==='kontrolpanel.html'?'#status':'#login-fejl').innerText(),/Log ind igen.*serveropdateringens resultat/);
        assert((await f.page.evaluate(()=>sessionStorage.getItem('makervaerkstedet.server-update.v1'))).includes(identity));assert.equal(f.sent.length,1);assert.deepEqual(f.errors,[]);
        if(output)await f.page.screenshot({path:path.join(output,entry.replace('.html','')+'-real-login.png')});
      }finally{await f.context.close();}
    }
  });
  await check('Small-screen confirmation, stable keyboard focus and scoped accessibility',async()=>{
    const f=await fixture('kontrolpanel.html',{width:390,height:844});try{
      await f.activate();await f.available();await f.refresh();assert.equal(await f.page.evaluate(()=>document.activeElement.id),'server-update-refresh');
      await f.page.locator('#server-update-install').click();const box=await f.page.locator('#server-update-confirm').boundingBox();assert(box.x>=0 && box.x+box.width<=390 && box.height<=844);
      const result=await new AxeBuilder({page:f.page}).include('#server-update-confirm').analyze();assert.deepEqual(result.violations.map(v=>v.id),[]);
      if(output){fs.mkdirSync(output,{recursive:true});await f.page.screenshot({path:path.join(output,'server-update-confirm-small.png'),fullPage:true});}
      await f.page.keyboard.press('Escape');assert.equal(await f.page.evaluate(()=>document.activeElement.id),'server-update-install');
    }finally{await f.context.close();}
  });
  console.log('PASS '+count+' rendered server-update checks');
}catch(e){console.error(e);process.exitCode=1;}finally{await browser?.close();}})();
