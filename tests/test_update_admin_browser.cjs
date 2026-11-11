/* Isolated UI contract: no school network, SSH or actual installation. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'linux-elevhub/server/elevhub/web/laerer.html'), 'utf8');
const panel = html.slice(html.indexOf('<section id="update-admin"'), html.indexOf('<p><a class="laererside"'));
const notice = html.match(/<dialog id="update-notice"[\s\S]*?<\/dialog>/)?.[0] || '';
const script = fs.readFileSync(path.join(root, 'linux-elevhub/server/elevhub/web/update-admin.js'), 'utf8');

(async()=>{
  const browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_BROWSER_CHANNEL ? {channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL} : {})});
  try {
    const page = await browser.newPage();
    const commands=[], dialogs=[];
    const roles=['linux_student','windows_laser_teacher','windows_server'];
    const state={configured:true,audit:[],releases:roles.map((role,i)=>({id:'release-'+i,role,version:'2026.10.21',ready:true,worker_online:true})),
      targets:roles.map((role,i)=>({id:'target-'+i,name:'Computer '+i,role,release_ids:['release-'+i],credentials_ready:true,enrolled:true,role_verified:true,
        credentials_mode:role==='windows_server'?'local_server':'pinned',job:null,desired_version:null,
        last_verified_version:i===1?'2026.10.21':'2026.10.20',last_verified_at:1000}))};
    for(let i=0;i<roles.length;i++){
      state.releases[i].id=`github-${roles[i]}-2026.10.21`;
      state.targets[i].release_ids=[state.releases[i].id];
    }
    const feed={repository:'example/project',enabled:true,worker_online:true,last_checked:1000,
      latest:{release_id:7,version:'2026.10.21',notes:'<img src=x onerror="window.injected=true">',
        packages:roles.map((role,i)=>({role,asset:'package-'+i+'.zip',size:1024}))},
      downloads:roles.map((role,i)=>({release_id:7,asset:'package-'+i+'.zip',status:'imported'}))};
    page.on('dialog',async dialog=>{dialogs.push(dialog.message());await dialog.accept();});
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/api/updates/state')return route.fulfill({json:state});
      if(url.pathname==='/api/updates/feed/state')return route.fulfill({json:feed});
      if(url.pathname==='/api/laerer/system')return route.fulfill({json:{server:{release_id:'2026.10.21'}}});
      if(url.pathname==='/api/updates/commands'){
        commands.push(route.request().postDataJSON());return route.fulfill({json:{ok:true}});
      }
      if(url.pathname==='/fixture')return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8">'+panel});
      return route.fulfill({status:200,body:''});
    });
    await page.goto('http://127.0.0.1:8877/fixture');
    await page.addScriptTag({content:script});
    await page.evaluate(()=>{document.querySelector('#update-admin').style.display='block';window.UpdateAdmin.start();});
    const serverRelease=state.releases[2].id,laserRelease=state.releases[1].id;
    await page.locator(`[data-feed-release="${serverRelease}"]`).waitFor({state:'visible'});
    assert.equal(await page.locator('#feed-latest img').count(),0,'Release notes must be text');
    assert.equal(await page.evaluate(()=>Boolean(window.injected)),false);
    assert.match(await page.locator('#updates-content').innerText(),/Lokal opdateringstjeneste er klargjort/);
    assert.match(await page.locator('#updates-content').innerText(),/Senest bekræftet installeret: 2026.10.20/);
    await page.locator(`[data-feed-release="${serverRelease}"]`).click();
    assert.equal(await page.locator('[data-release="windows_server"]').inputValue(),serverRelease);
    assert.equal(commands.length,0,'Selecting a release must not enqueue a server update');
    await page.locator('[data-target="target-2"]').check();
    await page.locator('[data-enqueue="windows_server"]').click();
    await page.waitForFunction(()=>document.querySelector('#updates-status').textContent.includes('Anmodningen er gemt'));
    assert.equal(commands.length,1);
    assert.equal(commands[0].action,'enqueue');
    assert.deepEqual(commands[0].params,{release_id:serverRelease,target_ids:['target-2']});
    assert.match(dialogs[0],/kortvarigt utilgængeligt/);
    await page.locator('[data-release="windows_laser_teacher"]').selectOption(laserRelease);
    await page.locator('[data-target="target-1"]').check();
    await page.locator('[data-enqueue="windows_laser_teacher"]').click();
    assert.match(await page.locator('#updates-status').innerText(),/allerede denne eller en nyere/);
    assert.equal(commands.length,1,'Already verified version must not enqueue');
    state.targets[0].job={status:'blocked',code:'version_unknown',attempt:1,paused:false,version:1};
    await page.locator('#updates-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#updates-content').textContent.includes('IT skal kontrollere den eksisterende installation'));
    assert.equal(commands.length,1,'Refreshing a blocked version must not retry installation');
    state.targets[0].job={status:'blocked',code:'windows_long_paths_disabled',attempt:1,paused:false,version:2};
    await page.locator('#updates-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#updates-content').textContent.includes('LongPathsEnabled'));
    assert.match(await page.locator('#updates-content').innerText(),/Opgaver og installerede filer blev ikke ændret/);
    state.targets[0].job={status:'applying',code:'applying',attempt:1,paused:false,version:2};
    await page.locator('#updates-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#updates-content').textContent.includes('Lad computeren være tændt. Genstart ikke.'));
    state.targets[0].job={status:'blocked',code:'recovery_required',attempt:1,paused:false,version:3};
    await page.locator('#updates-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#updates-content').textContent.includes('Gendannelsen er ikke bekræftet'));
    assert.equal(commands.length,1,'Recovery guidance must never dispatch a reset');
    await page.evaluate(()=>window.UpdateAdmin.stop());
    await page.close();

    let noticeCases=0;
    async function noticeFixture(options={}) {
      const context=await browser.newContext();
      const target=await context.newPage();
      const sent=[], reads=[];
      const currentFeed={...structuredClone(feed),schema:1,state:'available',last_checked:Date.now()/1000,
        ...options.feed};
      currentFeed.latest={...currentFeed.latest,repository:currentFeed.repository,...options.latest};
      let releaseSystem;
      const systemGate=options.delaySystem?new Promise(resolve=>{releaseSystem=resolve;}):Promise.resolve();
      await target.route('**/*',async route=>{
        const request=route.request(),url=new URL(request.url());
        if(request.method()==='POST'){sent.push(request.postDataJSON());return route.fulfill({json:{ok:true}});}
        if(url.pathname==='/api/updates/state')return route.fulfill({json:state});
        if(url.pathname==='/api/updates/feed/state'){reads.push('feed');return route.fulfill({json:currentFeed});}
        if(url.pathname==='/api/laerer/system'){
          reads.push('system');await systemGate;
          return route.fulfill({status:options.systemError?503:200,json:{server:{release_id:options.installed===undefined?'2026.10.20.2':options.installed}}});
        }
        if(url.pathname==='/fixture')return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8">'+panel+notice+'<dialog id="other-dialog"><button>Anden besked</button></dialog>'});
        return route.fulfill({status:200,body:''});
      });
      async function load(){await target.goto('http://127.0.0.1:8877/fixture');await target.addScriptTag({content:script});}
      async function settled(){await target.waitForFunction(()=>document.querySelector('#feed-status').textContent.includes('example/project'));await target.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
      await load();
      return {target,context,sent,reads,currentFeed,load,settled,releaseSystem};
    }
    const fresh=await noticeFixture();
    assert.equal(await fresh.target.locator('#update-notice').count(),1,'Teacher panel needs the update notice dialog');
    assert.equal(fresh.reads.length,0,'No feed/version read before teacher login');
    assert.equal(await fresh.target.locator('#update-notice[open]').count(),0);
    await fresh.target.evaluate(()=>window.UpdateAdmin.start());
    await fresh.target.locator('#update-notice[open]').waitFor();
    assert.match(await fresh.target.locator('#update-notice').innerText(),/2026.10.21/);
    assert.equal(await fresh.target.locator('#update-notice-notes').textContent(),feed.latest.notes);
    assert.equal(await fresh.target.locator('#update-notice img').count(),0,'Notice notes must remain text');
    assert.equal(await fresh.target.evaluate(()=>Boolean(window.injected)),false);
    await fresh.target.getByRole('button',{name:'Se opdateringen',exact:true}).click();
    await fresh.target.waitForFunction(()=>document.activeElement?.id==='feed-title');
    assert.equal(await fresh.target.locator('#update-notice[open]').count(),0);
    assert.equal(fresh.sent.length,0,'Viewing a notice must never download, enqueue or install');
    await fresh.target.evaluate(()=>{window.UpdateAdmin.stop();window.UpdateAdmin.start();});
    await fresh.settled();
    assert.equal(await fresh.target.locator('#update-notice[open]').count(),0,'A shown release is not repeated after restart');
    await fresh.load();await fresh.target.evaluate(()=>window.UpdateAdmin.start());await fresh.settled();
    assert.equal(await fresh.target.locator('#update-notice[open]').count(),0,'Shown release survives page reload within browser session');
    await fresh.context.close();noticeCases++;

    const rejected=[
      {installed:'2026.10.21'}, {installed:'2026.10.22'}, {installed:null}, {installed:'unknown'},
      {installed:'2026.02.30'}, {latest:{version:'2026.13.01'}}, {systemError:true},
      {feed:{enabled:false}}, {feed:{error:'network_unavailable'}}, {feed:{state:'check_failed'}},
      {feed:{last_checked:Date.now()/1000-86401}}, {feed:{last_checked:null}},
      {latest:{packages:[{role:'linux_student'}]}}, {latest:{packages:[{role:'windows_laser_teacher'}]}}
    ];
    for(const options of rejected){
      const fixture=await noticeFixture(options);
      await fixture.target.evaluate(()=>window.UpdateAdmin.start());await fixture.settled();
      assert.equal(await fixture.target.locator('#update-notice[open]').count(),0,'No new-server notice for '+JSON.stringify(options));
      assert.equal(fixture.sent.length,0);
      await fixture.context.close();noticeCases++;
    }
    for(const action of ['later','escape','logout']){
      const fixture=await noticeFixture();await fixture.target.evaluate(()=>window.UpdateAdmin.start());
      await fixture.target.locator('#update-notice[open]').waitFor();
      if(action==='later')await fixture.target.getByRole('button',{name:'Senere',exact:true}).click();
      else if(action==='escape')await fixture.target.keyboard.press('Escape');
      else await fixture.target.evaluate(()=>window.UpdateAdmin.stop());
      await fixture.target.waitForFunction(()=>!document.querySelector('#update-notice').open);
      await fixture.target.evaluate(()=>{window.UpdateAdmin.stop();window.UpdateAdmin.start();});await fixture.settled();
      assert.equal(await fixture.target.locator('#update-notice[open]').count(),0,'Dismissed release stays dismissed: '+action);
      assert.equal(fixture.sent.length,0);await fixture.context.close();noticeCases++;
    }
    const delayed=await noticeFixture({delaySystem:true});
    const systemRequested=delayed.target.waitForRequest('**/api/laerer/system');
    await delayed.target.evaluate(()=>window.UpdateAdmin.start());
    await systemRequested;
    await delayed.target.evaluate(()=>window.UpdateAdmin.stop());
    delayed.releaseSystem();await delayed.target.evaluate(()=>new Promise(resolve=>setTimeout(resolve,100)));
    assert.equal(await delayed.target.locator('#update-notice[open]').count(),0,'Late result cannot reopen after logout');
    assert.equal(delayed.sent.length,0);await delayed.context.close();noticeCases++;

    const occupied=await noticeFixture();
    await occupied.target.evaluate(()=>{document.querySelector('#other-dialog').showModal();window.UpdateAdmin.start();});await occupied.settled();
    assert.equal(await occupied.target.locator('#update-notice[open]').count(),0,'Never replace an existing modal');
    await occupied.target.evaluate(()=>{document.querySelector('#other-dialog').close();window.UpdateAdmin.stop();window.UpdateAdmin.start();});
    await occupied.target.locator('#update-notice[open]').waitFor();
    assert.equal(occupied.sent.length,0);await occupied.context.close();noticeCases++;
    console.log(JSON.stringify({passed:9+noticeCases,failed:0,scope:'existing queue contracts plus authenticated newer-server notice, safe text, dismissal/session, version/feed guards, logout races and existing modal'}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
