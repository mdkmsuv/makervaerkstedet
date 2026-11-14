/* Current teacher update UI; all API writes are intercepted, never installed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const web = path.join(root, 'linux-elevhub/server/elevhub/web');
const html = fs.readFileSync(path.join(web, 'kontrolpanel.html'), 'utf8');
const script = fs.readFileSync(path.join(web, 'update-admin.js'), 'utf8');
const css = fs.readFileSync(path.join(web, 'kontrolpanel.css'), 'utf8');
const origin = 'http://127.0.0.1:8877';
const hashes = {student:'a'.repeat(64), rollback:'b'.repeat(64), server:'c'.repeat(64), laser:'d'.repeat(64)};
let checks = 0;
async function check(name, fn) {await fn(); checks++; console.log('PASS '+name);}

(async()=>{
  const browser = await chromium.launch({headless:true,
    ...(process.env.TEST_CHROMIUM ? {executablePath:process.env.TEST_CHROMIUM} :
      {channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL || (process.platform==='win32'?'chrome':undefined)})});
  const errors = [];
  async function fixture(options={}) {
    const context=await browser.newContext({viewport:{width:1366,height:900}});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    const sent=[], reads=[], receipts=new Map();
    const state={schema:1,revision:7,catalog_errors:[{component:'<img src=x onerror="window.injected=true">',code:'untrusted_manifest'}],
      catalog:[
        {manifest_sha256:hashes.student,component:'studentcore',version:'2026.10.21',role:'student',platform:'debian-amd64',orderable:true,rollback_required:false},
        {manifest_sha256:hashes.rollback,component:'studentcore',version:'2026.10.20',role:'student',platform:'debian-amd64',orderable:true,rollback_required:true},
        {manifest_sha256:hashes.server,component:'server',version:'2026.10.21',role:'server',platform:'windows-amd64',orderable:false},
        {manifest_sha256:hashes.laser,component:'laser',version:'2026.10.21',role:'laser_station',platform:'windows-amd64',orderable:false}],
      devices:[
        {device:'elev-01',role:'student',platform:'debian-amd64',installed:[{component:'studentcore',version:'2026.10.20'}],desired:[],result:null,last_seen:'2026-11-14T08:00:00Z'},
        {device:'server-01',role:'server',platform:'windows-amd64',installed:[],desired:[],result:null,last_seen:null},
        {device:'laser-01',role:'laser_station',platform:'windows-amd64',installed:[{component:'laser',version:'2026.10.21'}],desired:[],result:null,last_seen:null}],
      unbound_devices:['pc-new']};
    const source={schema:1,enabled:true,state:'ready',active_sequence:12,seen_sequence:12,last_success:'2026-11-14T08:00:00Z'};
    const control={readStatus:200,nextError:null,dropNext:false,badReceipt:false};
    let releaseRead;
    const gate=options.delayRead?new Promise(resolve=>{releaseRead=resolve;}):Promise.resolve();
    await page.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.pathname==='/fixture')return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname==='/update-admin.js')return route.fulfill({contentType:'application/javascript',body:script});
      // Authentication/navigation is covered by the real-handler teacher journey.
      // Here the complete current DOM hosts the isolated UpdateAdmin lifecycle.
      if(url.pathname==='/kontrolpanel.js')return route.fulfill({contentType:'application/javascript',body:''});
      if(url.pathname==='/kontrolpanel.css')return route.fulfill({contentType:'text/css',body:css});
      if(url.pathname.startsWith('/api/') && request.method()==='POST') {
        const body=request.postDataJSON();sent.push({path:url.pathname,body});
        assert(['/api/admin/updates','/api/admin/updates/bind','/api/admin/updates/refresh'].includes(url.pathname),'Only current update commands may be sent');
        if(control.nextError){const failure=control.nextError;control.nextError=null;return route.fulfill({status:failure.status,json:{error:failure.code}});}
        let receipt=receipts.get(body.command_id);
        if(!receipt){
          if(url.pathname==='/api/admin/updates/refresh')receipt={schema:1,command_id:body.command_id,accepted:true};
          else {
            state.revision++;
            receipt={schema:1,command_id:body.command_id,revision:state.revision};
            if(url.pathname==='/api/admin/updates')for(const name of body.devices)state.devices.find(d=>d.device===name).desired=[{component:body.component,hash:body.manifest_sha256,revision:state.revision,available:true}];
          }
          receipts.set(body.command_id,receipt);
        }
        if(control.dropNext){control.dropNext=false;return route.abort('failed');}
        return route.fulfill({json:control.badReceipt?{...receipt,command_id:'wrong-receipt'}:receipt});
      }
      if(url.pathname==='/api/admin/updates') {
        reads.push('updates');await gate;
        if(control.nextReadGate){const pendingRead=control.nextReadGate;control.nextReadGate=null;await pendingRead;}
        return route.fulfill({status:control.readStatus,json:control.readStatus===200?state:{error:'authentication_required'}});
      }
      if(url.pathname==='/api/admin/updates/source'){reads.push('source');await gate;return route.fulfill({json:source});}
      if(url.pathname.startsWith('/api/'))throw Error('Retired or unexpected API: '+url.pathname);
      return route.fulfill({status:200,body:''});
    });
    async function load(){
      await page.goto(origin+'/fixture');
      await page.evaluate(()=>{
        document.getElementById('panel').hidden=false;document.getElementById('nav').hidden=false;
        document.getElementById('app-shell').classList.add('signed-in');
        document.querySelectorAll('[data-view]').forEach(n=>n.hidden=n.dataset.view!=='teknik');
      });
    }
    async function settled(){await page.waitForFunction(()=>document.querySelector('#updates-status').textContent.includes('Status hentet'));}
    async function start(){await page.evaluate(()=>window.UpdateAdmin.start());await page.locator('#simple-order').waitFor();await page.locator('#source-status').filter({hasText:'Seneste søgning'}).waitFor();}
    async function refresh(){
      const requested=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/admin/updates' && request.method()==='GET');
      await page.locator('#updates-refresh').click();await (await (await requested).response()).finished();
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await settled();
    }
    async function selectStudent(){await page.locator('[name=release]').selectOption(hashes.student);await page.locator('[name=device][value="elev-01"]').check();}
    await load();return {context,page,state,source,control,sent,reads,receipts,releaseRead,load,start,settled,refresh,selectStudent};
  }
  try {
    const fx=await fixture();const {page,state,sent,control}=fx;
    await check('No feed or version reads before authorized start; current panel renders safe text',async()=>{
      assert.deepEqual(fx.reads,[]);assert.equal(sent.length,0);
      await fx.start();await page.getByRole('heading',{name:'Opdatér computere',exact:true}).waitFor();
      assert.equal(await page.locator('#updates-content img').count(),0);assert.equal(await page.evaluate(()=>Boolean(window.injected)),false);
      assert.match(await page.locator('#updates-content').innerText(),/Installeret, rapporteret af computeren/);
      assert.match(await page.locator('#updates-content').innerText(),/Ikke rapporteret/);
      assert.equal(sent.length,0,'Opening status must not order or install anything');
      if(process.env.ELEVHUB_TEST_OUTPUT){fs.mkdirSync(process.env.ELEVHUB_TEST_OUTPUT,{recursive:true});await page.screenshot({path:path.join(process.env.ELEVHUB_TEST_OUTPUT,'updates-current.png')});}
    });
    await check('Windows requires signed role installers; rollback catalog entries are not offered',async()=>{
      const values=await page.locator('[name=release] option').evaluateAll(items=>items.map(x=>x.value));
      assert.deepEqual(values,['',hashes.student]);
      assert.match(await page.locator('#updates-content').innerText(),/Windows-opdateringer installeres med den signerede rolle-EXE/);
      await page.locator('[name=release]').selectOption(hashes.student);assert.equal(sent.length,0);
      await page.locator('[name=device][value="server-01"]').check();await page.locator('#simple-order button').click();
      assert.match(await page.locator('#updates-status').innerText(),/samme rolle og platform/);assert.equal(sent.length,0);
      await page.locator('[name=device][value="server-01"]').uncheck();
    });
    await check('Order binds exact catalog hash, revision and selected device; desired is not installed',async()=>{
      await fx.selectStudent();await page.locator('#simple-order button').click();
      await page.waitForFunction(()=>document.querySelector('#updates-status').textContent.includes('Bestillingen er gemt'));
      assert.equal(sent.length,1);assert.match(sent[0].body.command_id,/^[a-f0-9-]{36}$/);
      assert.deepEqual({...sent[0].body,command_id:null},{command_id:null,expected_revision:7,devices:['elev-01'],component:'studentcore',manifest_sha256:hashes.student,rollback_manifest_sha256:null});
      assert.equal(state.devices[0].installed[0].version,'2026.10.20');
      const card=page.locator('#updates-content article').filter({has:page.getByRole('heading',{name:'elev-01',exact:true})});
      assert.match(await card.innerText(),/Installeret, rapporteret af computeren:.*2026\.10\.20/);
      assert.match(await card.innerText(),/Ønsket:.*2026\.10\.21/);
    });
    await check('Device binding uses explicit role/platform and current revision',async()=>{
      await page.locator('[data-bind="pc-new"] select').selectOption('laser_station');
      await page.locator('[data-bind="pc-new"] button').click();await page.waitForFunction(()=>document.querySelector('#simple-order button')?.disabled===false);
      assert.equal(sent[1].path,'/api/admin/updates/bind');
      assert.deepEqual({...sent[1].body,command_id:null},{command_id:null,expected_revision:8,device:'pc-new',role:'laser_station',platform:'windows-amd64'});
    });
    await check('Revision conflict sends no implicit new order; stale controls wait for a fresh snapshot',async()=>{
      let releaseSnapshot;control.nextReadGate=new Promise(resolve=>{releaseSnapshot=resolve;});
      control.nextError={status:409,code:'update_revision_conflict'};
      const previousRevision=state.revision;state.revision++;const count=sent.length;
      await fx.selectStudent();await page.locator('#simple-order button').click();
      await page.locator('#updates-status').filter({hasText:'update_revision_conflict'}).waitFor();
      assert.equal(sent.at(-1).body.expected_revision,previousRevision);
      assert.equal(await page.locator('#simple-order button').isDisabled(),true);
      assert.equal(await page.locator('#updates-retry-request').isVisible(),false);
      releaseSnapshot();await page.waitForFunction(()=>document.querySelector('#simple-order button')?.disabled===false);
      assert.equal(sent.length,count+1,'Fetching current revision must not resend a refused order');
    });
    await check('Lost receipt survives reload; retry uses the same command ID and cannot duplicate revision',async()=>{
      control.dropNext=true;await fx.selectStudent();await page.locator('#simple-order button').click();await page.locator('#updates-retry-request').waitFor();
      const applied=structuredClone(sent.at(-1)), revision=state.revision;
      assert.equal(await page.locator('#simple-order button').isDisabled(),true);
      await fx.load();await fx.start();await page.locator('#updates-retry-request').click();
      await page.locator('#updates-retry-request').waitFor({state:'hidden'});
      assert.deepEqual(sent.at(-1),applied);assert.equal(state.revision,revision);
    });
    await check('Mismatched receipt remains unresolved; same-ID clarification preserves the accepted order',async()=>{
      control.badReceipt=true;await fx.selectStudent();await page.locator('#simple-order button').click();await page.locator('#updates-retry-request').waitFor();
      const applied=structuredClone(sent.at(-1)),revision=state.revision;control.badReceipt=false;
      await page.locator('#updates-retry-request').click();await page.locator('#updates-retry-request').waitFor({state:'hidden'});
      assert.deepEqual(sent.at(-1),applied);assert.equal(state.revision,revision);
    });
    await check('Source search is explicit; disabled source cannot dispatch',async()=>{
      const count=sent.length;await page.locator('#source-refresh').click();await page.locator('#updates-status').filter({hasText:'Søgningen er bestilt'}).waitFor();
      assert.equal(sent.length,count+1);assert.equal(sent.at(-1).path,'/api/admin/updates/refresh');
      assert.deepEqual(Object.keys(sent.at(-1).body),['command_id']);
      fx.source.enabled=false;await fx.refresh();await page.waitForFunction(()=>document.querySelector('#source-refresh').disabled);
      assert.equal(sent.length,count+1);fx.source.enabled=true;await fx.refresh();
    });
    await check('Failed/recovery reports are observations; viewing or refreshing never retries installation',async()=>{
      state.devices[0].result={state:'failed',revision:10,code:'recovery_required'};const count=sent.length;
      await fx.refresh();assert.match(await page.locator('#updates-content').innerText(),/Fejl · revision 10 · recovery_required/);
      assert.equal(sent.length,count);assert.equal(state.devices[0].installed[0].version,'2026.10.20');
    });
    await check('Expired authentication disables stale controls and never sends a new order',async()=>{
      const count=sent.length;control.readStatus=401;await page.locator('#updates-refresh').click();
      await page.locator('#updates-status').filter({hasText:'Log ind igen'}).waitFor();
      assert.equal(await page.locator('#simple-order button').isDisabled(),true);assert.equal(sent.length,count);
    });
    await page.evaluate(()=>window.UpdateAdmin.stop());await fx.context.close();
    await check('Results arriving after logout cannot render or dispatch; restart can fetch a fresh snapshot',async()=>{
      const delayed=await fixture({delayRead:true});const requested=delayed.page.waitForRequest('**/api/admin/updates');
      await delayed.page.evaluate(()=>window.UpdateAdmin.start());await requested;
      await delayed.page.evaluate(()=>window.UpdateAdmin.stop());delayed.releaseRead();
      await delayed.page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,100)));
      assert.equal(await delayed.page.locator('#simple-order').count(),0);assert.equal(delayed.sent.length,0);
      await delayed.start();assert.equal(delayed.sent.length,0);await delayed.page.evaluate(()=>window.UpdateAdmin.stop());await delayed.context.close();
    });
    assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:checks,failed:0,scope:'current control panel update API, pinned catalog/revision/role contracts, safe text, explicit source actions, same-ID retries and logout/auth races'}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
