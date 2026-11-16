// Genuine Scratch/3D editors and local archive. No manual Save clicks, no remote server.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');
const {unzipSync,strFromU8} = require('fflate');
const root = path.resolve(__dirname,'..');
const output = process.env.ELEVHUB_TEST_OUTPUT_DIR;
assert(output,'Use an explicit evidence directory');
async function until(check, label, timeout=30000) {
 const end=Date.now()+timeout;
 while(!await check()) {
  assert(Date.now()<end,'Timed out: '+label);
  await new Promise(r=>setTimeout(r,100));
 }
}

async function fixture() {
 const child=spawn(process.env.ELEVHUB_TEST_PYTHON||'python',
  ['-B',path.join(root,'system/linux-elevhub/tests/autosave_browser_fixture.py')],
  {cwd:root,env:process.env,stdio:['pipe','pipe','pipe'],windowsHide:true});
 let stderr='';child.stderr.on('data',b=>stderr+=b);
 const urls=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>{child.kill();reject(Error(stderr||'Fixture timeout'));},45000);
  child.stdout.on('data',b=>{text+=b;const line=text.split(/\r?\n/).find(s=>s.startsWith('{'));if(line){clearTimeout(timer);resolve(JSON.parse(line));}});
  child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+': '+stderr));});
 });
 return {urls,async close(){child.stdin.end('\n');await new Promise(r=>child.once('exit',r));await fs.writeFile(path.join(output,'fixture.log'),stderr);}};
}

(async()=>{
 await fs.mkdir(output,{recursive:true});
 const server=await fixture();let browser;
 const result={passed:false,remote_requests:[],apps:{}};
 try{
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const selected=(process.env.ELEVHUB_TEST_AUTOSAVE_APPS||'scratch,3dprint').split(',');
  assert(selected.length && selected.every(app=>['scratch','3dprint'].includes(app)));
  for(const app of selected){
   const context=await browser.newContext({viewport:{width:1280,height:900}});
   const errors=[];
   await context.route('**/*',route=>{
    const url=route.request().url();
    if(/^https?:/.test(url)&&!url.startsWith('http://127.0.0.1:')){result.remote_requests.push(url);return route.abort();}
    return route.continue();
   });
   const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
   await page.goto(server.urls[app]);
   await page.locator('#project-start-confirm').waitFor({timeout:45000});
   await page.locator('#project-classroom').fill('5A');
   await page.locator('#project-student').fill('Syntetisk elev');
   const title='AUTOGEM '+app;
   await page.locator('#project-title').fill(title);
   await page.locator('#project-start-confirm').click();
   await page.waitForFunction(()=>document.querySelector('#elevhub-project-bar [role=status]')?.textContent.startsWith('Gemt'),null,{timeout:20000});
   const list=async()=> (await(await page.request.get(new URL('/_elevhub/projekter/api/list',server.urls[app]).href)).json()).projects;
   let project=(await list()).find(p=>p.name===title);assert(project,'First named project must already exist in shelf');
   const first=project.revision;
   if(app==='scratch'){
    const sprite=page.getByPlaceholder('Navn',{exact:true});await sprite.fill('Autogemt kat');await sprite.press('Enter');
    await page.waitForFunction(()=>ElevhubProjectAdapter.signature().includes('Autogemt kat'));
   }else{
    await page.locator('[data-add=box]:not([data-mode])').click();
    await page.waitForFunction(()=>!document.querySelector('[data-add=box]').disabled);
   }
   await until(async()=> (await list()).some(p=>p.id===project.id&&p.revision>first),'changed project in actual archive');
   await until(()=>page.evaluate(async()=>!await ElevhubProjectSession.dirty()),'confirmed clean editor');
   const beforeUndoChange=await page.evaluate(()=>ElevhubProjectAdapter.signature());
   const version=async()=> (await list()).find(p=>p.id===project.id).revision;
   let savedRevision=await version();
   if(app==='scratch'){
    const block=await page.locator('.blocklyFlyout .motion_movesteps').first().boundingBox();
    assert(block,'Scratch movement block must be visible');
    assert(block.y>150 && block.y<850,'Drag the rendered palette block, not a clipped library block');
    await page.mouse.move(block.x+12,block.y+17);await page.mouse.down();
    await page.mouse.move(480,270,{steps:20});await page.mouse.up();
    await page.waitForFunction(()=>ElevhubProjectAdapter.signature().includes('motion_movesteps'));
   }else{
    await page.locator('[data-add=box]:not([data-mode])').click();
    await until(()=>page.evaluate(()=>JSON.parse(ElevhubProjectAdapter.signature()).roots.length===2),'second3Dshape');
   }
   await until(async()=>await version()>savedRevision,'edit persisted before undo');
   await until(()=>page.evaluate(async()=>!await ElevhubProjectSession.dirty()),'saved before undo');
   savedRevision=await version();
   if(app==='scratch'){
    await page.mouse.click(680,430);await page.keyboard.press('Control+z');
    await page.waitForFunction(()=>!ElevhubProjectAdapter.signature().includes('motion_movesteps'));
   }else{
    await page.locator('#undo').click();
    await page.waitForFunction(expected=>ElevhubProjectAdapter.signature()===expected,beforeUndoChange);
   }
   await until(async()=>await version()>savedRevision,'undo across autosave is stored');
   await until(()=>page.evaluate(async()=>!await ElevhubProjectSession.dirty()),'undo stored');
   savedRevision=await version();
   if(app==='scratch'){
    await page.keyboard.press('Control+Shift+z');
    await page.waitForFunction(()=>ElevhubProjectAdapter.signature().includes('motion_movesteps'));
   }else{
    await page.locator('#redo').click();
    await until(()=>page.evaluate(()=>JSON.parse(ElevhubProjectAdapter.signature()).roots.length===2),'3D redo after autosave');
   }
   await until(async()=>await version()>savedRevision,'redo across autosave is stored');
   await until(()=>page.evaluate(async()=>!await ElevhubProjectSession.dirty()),'redo stored');
   project=(await list()).find(p=>p.id===project.id);
   const signature=await page.evaluate(()=>ElevhubProjectAdapter.signature());
   const savedFile=await page.request.get(new URL('/_elevhub/projekter/api/file?'+new URLSearchParams({id:project.id,revision:project.revision}),server.urls[app]).href);
   if(app==='scratch'){
    const savedProject=JSON.parse(strFromU8(unzipSync(new Uint8Array(await savedFile.body()))['project.json']));
    const directBytes=await page.evaluate(async()=>Array.from(new Uint8Array(await(await ElevhubProjectAdapter.export()).arrayBuffer())));
    const directProject=JSON.parse(strFromU8(unzipSync(new Uint8Array(directBytes))['project.json']));
    await fs.writeFile(path.join(output,'scratch-save-diagnosis.json'),JSON.stringify({first,
     browserList:await page.evaluate(async()=>await(await fetch('/_elevhub/projekter/api/list')).json()),project,all:await list(),
     current:JSON.parse(signature).targets.map(t=>t.name),stored:savedProject.targets.map(t=>t.name),
     directExport:directProject.targets.map(t=>t.name)},null,2));
    assert(savedProject.targets.some(t=>t.name==='Autogemt kat'),'Scratch archive must contain the changed sprite');
   }
   await fs.writeFile(path.join(output,app+'-saved-meta.json'),JSON.stringify({project,signature},null,2));
   await page.screenshot({path:path.join(output,app+'-autosaved.png')});
   // Closing without running beforeunload is an interruption, not a normal pupil finish.
   await page.close({runBeforeUnload:false});await context.close();
   const fresh=await browser.newContext({viewport:{width:1280,height:900}});
   const shelf=await fresh.newPage();await shelf.goto(server.urls.shelf);
   await shelf.locator('#search').fill(title);
   const card=shelf.locator('article.project').filter({has:shelf.getByRole('heading',{name:title,exact:true})});
   await card.locator('.open-project').click();
   await shelf.waitForURL(url=>url.origin===new URL(server.urls[app]).origin);
   try { await shelf.locator('#elevhub-project-bar').waitFor({timeout:45000}); }
   catch(error) {
    await shelf.screenshot({path:path.join(output,app+'-reopen-failure.png')});
    await fs.writeFile(path.join(output,app+'-reopen-failure.json'),JSON.stringify({
      url:shelf.url(),text:await shelf.locator('body').innerText(),
      frames:await Promise.all(shelf.frames().map(async frame=>({url:frame.url(),text:(await frame.locator('body').innerText().catch(()=>'' )).slice(0,1800)})))
    },null,2));
    throw error;
   }
   await shelf.waitForFunction(()=>window.ElevhubProjectSession?.naming(),null,{timeout:45000});
   const reopened=await shelf.evaluate(()=>ElevhubProjectAdapter.signature());
   await fs.writeFile(path.join(output,app+'-reopened-signature.json'),JSON.stringify({expected:signature,actual:reopened},null,2));
   if(app==='scratch') {
    assert(JSON.parse(reopened).targets.some(t=>t.name==='Autogemt kat'),'Scratch must reopen the automatically saved sprite');
    assert(reopened.includes('motion_movesteps'),'Scratch must reopen the automatically saved code');
   }
   else assert.equal(reopened,signature,'3D must retain the editable scene');
   await shelf.screenshot({path:path.join(output,app+'-reopened-from-shelf.png')});
   assert.deepEqual(errors,[]);
   result.apps[app]={passed:true,id:project.id,initial_revision:first,saved_revision:project.revision,
    normal_shelf_reopen:true,fresh_browser_context:true,manual_save_clicks:0,editable_content_retained:true,
    undo_across_autosave:true,redo_across_autosave:true};
   // Protect a deliberate older-version view from overwriting the latest autosave.
   await shelf.locator('#elevhub-project-save').click();
   await until(()=>shelf.evaluate(()=>!ElevhubProjectSession.saveState().ready
     ? false : !ElevhubProjectSession.saveState().historical),'manual checkpoint');
   const checkpoint=project.revision;
   if(app==='scratch'){
    const sprite=shelf.getByPlaceholder('Navn',{exact:true});await sprite.fill('Nyeste kat');await sprite.press('Enter');
   }else{
    await shelf.locator('[data-add=box]:not([data-mode])').click();
   }
   const current=async()=> (await(await shelf.request.get(new URL('/_elevhub/projekter/api/meta?id='+project.id,server.urls[app]).href)).json());
   try { await until(async()=>(await current()).revision>checkpoint,'newer automatic version B'); }
   catch(error) {
    await fs.writeFile(path.join(output,app+'-history-setup-failure.json'),JSON.stringify({checkpoint,
     meta:await current(),editor:await shelf.evaluate(()=>({state:ElevhubProjectSession.saveState(),
       naming:ElevhubProjectSession.naming(),status:document.querySelector('#elevhub-project-bar [role=status]')?.textContent,
       signature:ElevhubProjectAdapter.signature()}))},null,2));
    await shelf.screenshot({path:path.join(output,app+'-history-setup-failure.png')});throw error;
   }
   await until(()=>shelf.evaluate(async()=>!await ElevhubProjectSession.dirty()),'newest version saved');
   const latest=(await current()).revision;
   await shelf.close({runBeforeUnload:false});
   const history=await fresh.newPage();await history.goto(server.urls.shelf);
   await history.locator('#search').fill(title);
   const historyCard=history.locator('article.project').filter({has:history.getByRole('heading',{name:title,exact:true})});
   await historyCard.locator('.project-menu summary').click();
   await historyCard.getByRole('button',{name:'Tidligere versioner',exact:true}).click();
   await history.locator('#versions .version').filter({hasText:new RegExp('Version '+checkpoint+'\\s')}).getByRole('button').click();
   await history.waitForURL(url=>url.origin===new URL(server.urls[app]).origin);
   await history.waitForFunction(()=>window.ElevhubProjectSession?.saveState().historical===true);
   await history.getByText('Tidligere version · autogem er på pause. Brug Gem kopi.',{exact:true}).waitFor();
   await history.waitForTimeout(5500);
   const retained=await(await history.request.get(new URL('/_elevhub/projekter/api/meta?id='+project.id,server.urls[app]).href)).json();
   assert.equal(retained.revision,latest,'Viewing old manual A must not overwrite autosave B');
   const latestFile=await history.request.get(new URL('/_elevhub/projekter/api/file?'+new URLSearchParams({id:project.id,revision:latest}),server.urls[app]).href);
   assert.equal(latestFile.status(),200,'Newest autosave file must remain readable');
   if(app==='scratch') assert(JSON.parse(strFromU8(unzipSync(new Uint8Array(await latestFile.body()))['project.json'])).targets.some(t=>t.name==='Nyeste kat'));
   else assert.equal(JSON.parse(await latestFile.text()).scene.roots.length,3);
   await history.screenshot({path:path.join(output,app+'-history-autosave-paused.png')});
   result.apps[app].historical_view_protected=true;
   result.apps[app].history_setup_manual_save_clicks=1;
   await fresh.close();
  }
  assert.deepEqual(result.remote_requests,[]);result.passed=true;
 }finally{
  await browser?.close();await server.close();
  await fs.writeFile(path.join(output,'browser-result.json'),JSON.stringify(result,null,2)+'\n');
 }
 console.log(JSON.stringify(result));
})().catch(e=>{console.error(e);process.exitCode=1;});
