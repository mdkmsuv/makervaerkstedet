'use strict';
// Desktop acceptance with real templates/editors/project archive; never submits jobs.
// Windows: node linux-elevhub/tests/test_kiosk_desktop_ux.cjs
const {chromium}=require('playwright');
const AxeBuilder=require('@axe-core/playwright').default;
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const output=process.env.KIOSK_UX_OUTPUT||path.join(os.tmpdir(),'mkv-kiosk-desktop-ux');
const linux=p=>'/mnt/'+p[0].toLowerCase()+p.slice(2).replaceAll('\\','/');
async function fixture(){
 const windows=process.platform==='win32';
 const script=path.join(__dirname,'kiosk_desktop_fixture.py');
 const child=spawn(windows?'wsl':(process.env.PYTHON||'python3'),windows?['-e','python3','-B',linux(script),linux(root)]:['-B',script,root],{stdio:['pipe','pipe','pipe'],windowsHide:true});
 let stderr='';child.stderr.on('data',b=>stderr+=b);
 async function close(){if(!child.pid||child.exitCode!==null||child.signalCode!==null)return;await new Promise(resolve=>{const timer=setTimeout(()=>{child.kill();resolve();},5000);child.once('exit',()=>{clearTimeout(timer);resolve();});child.stdin.end('\n');});}
 let urls;
 try{urls=await new Promise((resolve,reject)=>{let value='';const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),45000);child.stdout.on('data',b=>{value+=b;const line=value.split(/\r?\n/).find(l=>l.startsWith('{'));if(line){clearTimeout(timer);try{resolve(JSON.parse(line));}catch(e){reject(e);}}});child.once('error',e=>{clearTimeout(timer);reject(e);});child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+' '+stderr));});});}catch(e){await close();throw e;}
 return {urls,close};
}
(async()=>{
 fs.mkdirSync(output,{recursive:true});const f=await fixture();let browser;
 const fallback='C:/Program Files/Google/Chrome/Application/chrome.exe';
 try{browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:process.platform==='win32'&&fs.existsSync(fallback)?{executablePath:fallback}:{})});}catch(e){await f.close();throw e;}
 const results=[],axe=[];
 let context,page;
 try{context=await browser.newContext({viewport:{width:1366,height:708}});page=await context.newPage();}catch(e){try{await browser.close();}finally{await f.close();}throw e;}
 let finish='completed',finishCalls=0;
 await context.route('http://127.0.0.1:8790/**',async r=>{if(new URL(r.request().url()).pathname==='/apps'){const response=await context.request.get(f.urls.hub+'/apps',{headers:{Authorization:'Bearer fixture-token','X-Elevhub-Computer':'browser-fixture'}});return r.fulfill({response});}finishCalls++;return r.fulfill({status:finish==='error'?503:200,contentType:'application/json',body:JSON.stringify(finish==='error'?{error:'Dit arbejde er stadig åbent. Prøv igen.'}:{id:'a'.repeat(32),state:finish})});});
 page.setDefaultTimeout(7000);
 const snap=async name=>{await page.waitForFunction(()=>[...document.images].filter(img=>{const r=img.getBoundingClientRect();return r.width&&r.height&&r.bottom>0&&r.top<innerHeight;}).every(img=>img.complete),null,{timeout:7000}).catch(()=>{});return page.screenshot({path:path.join(output,name+'.png'),fullPage:false});};
 async function check(name,fn){try{await fn();results.push({name,pass:true});console.log('PASS '+name);}catch(e){results.push({name,pass:false,error:e.message});console.log('FAIL '+name+': '+e.message);await snap('failure-'+results.length).catch(()=>{});}}
 async function audit(name){const a=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();axe.push({name,violations:a.violations});fs.writeFileSync(path.join(output,'axe-'+name+'.json'),JSON.stringify(a,null,2));}
 async function home(mode='open'){await page.request.get(f.urls.hub+'/__set?mode='+mode);await page.goto(f.urls.hub);await page.locator('article[data-app="folie"] .app-open').waitFor();}
 async function started(url){await page.goto(url);await page.locator('#project-classroom').fill('5A');await page.locator('#project-student').fill('Testelev');await page.locator('#project-title').fill('Syntetisk skilt');await page.locator('#project-start-confirm').click();}
 try{
  if(process.argv.includes('--axe-only')){await started(f.urls.folie);await audit('folie');assert.deepEqual(axe[0].violations,[],'Folie has remaining axe violations');console.log('PASS Folie axe: no violations');return;}
  await check('launch error after scrolling is visible with recovery',async()=>{await home();finish='error';await page.locator('article[data-app="folie"] .app-open').click();await page.locator('#fejl').filter({hasText:/Prøv igen/}).waitFor();await snap('launch-error');const box=await page.locator('#fejl').boundingBox();assert(box&&box.y>=0&&box.y+box.height<=708,'Error must be wholly inside viewport after scrolled launch: '+JSON.stringify(box));});
  for(const target of ['.app-image','.app-title'])await check('card '+target+' opens actual Folie',async()=>{await home();finish='completed';await page.locator('article[data-app="folie"] '+target).click();await page.waitForURL(f.urls.folie+'**');await page.locator('#project-classroom').waitFor();});
  await check('action error survives status polling; dismiss restores focus; retry succeeds',async()=>{await home();finish='error';await page.locator('article[data-app="folie"] .app-open').click();await page.locator('#fejl').filter({hasText:/Prøv igen/}).waitFor();await page.waitForTimeout(3500);assert(await page.locator('#kiosk-notice').isVisible());await page.locator('#kiosk-dismiss').click();assert(await page.locator('article[data-app="folie"] .app-open').evaluate(e=>e===document.activeElement));assert.equal(await page.locator('#kiosk-notice').isVisible(),false);finish='completed';await page.keyboard.press('Enter');await page.waitForURL(f.urls.folie+'**');});
  for(const size of [{width:1366,height:708},{width:1920,height:1020}])await check('first-row launch buttons visible on initial '+size.width,async()=>{await page.setViewportSize(size);await home();const rows=await page.locator('#apps > article').evaluateAll(cards=>cards.map(c=>({top:c.getBoundingClientRect().top,button:c.querySelector('.app-open').getBoundingClientRect().toJSON()})));const first=rows.filter(c=>Math.abs(c.top-rows[0].top)<2);assert(first.length>0);assert(first.every(c=>c.button.top>=0&&c.button.bottom<=size.height),'first row launch buttons clipped: '+JSON.stringify(first));await snap('home-initial-'+size.width);});
  await check('ideas retain detail and return focus',async()=>{await home();await page.locator('article[data-app="folie"] .app-about').click();await page.locator('#detail-title').waitFor();assert.match(page.url(),/#vaerksted\/folie/);await page.getByRole('link',{name:/Alle værksteder/}).click();assert(await page.locator('article[data-app="folie"] .app-about').evaluate(e=>e===document.activeElement));await audit('home');await snap('home');});
  await check('filtered idea return restores visible keyboard focus without changing filter',async()=>{
   await page.setViewportSize({width:1366,height:708});await home();
   await page.locator('[data-filter="ide"]').click();
   const ideaApp=await page.locator('#ticket-link').getAttribute('data-app');
   assert.equal(await page.locator('article[data-app="'+ideaApp+'"]').count(),0,'Suggested workshop must be outside the active filter');
   await page.locator('#ticket-link').focus();await page.keyboard.press('Enter');
   await page.locator('#detail-title').waitFor();
   const detailOutline=await page.locator('#detail-title').evaluate(e=>({style:getComputedStyle(e).outlineStyle,width:getComputedStyle(e).outlineWidth}));
   await page.getByRole('link',{name:/Alle værksteder/}).focus();await page.keyboard.press('Enter');
   await page.waitForFunction(()=>document.getElementById('workshop-detail').hidden);
   assert(await page.locator('#workshops-title').evaluate(e=>e===document.activeElement),'Focus must return to the visible workshop heading when the original card is filtered out');
   assert.equal(await page.locator('[data-filter="ide"]').getAttribute('aria-pressed'),'true');
   const heading=await page.locator('#workshops-title').boundingBox();assert(heading&&heading.y>=0&&heading.y+heading.height<=708);
   const outline=await page.locator('#workshops-title').evaluate(e=>({style:getComputedStyle(e).outlineStyle,width:getComputedStyle(e).outlineWidth}));
   assert.equal(outline.style,'solid');assert.equal(outline.width,'3px');assert.deepEqual(detailOutline,outline);
   await snap('filtered-idea-return-focus');
  });
  await check('finish cancellation preserves current page',async()=>{await home();finish='cancelled';const before=page.url(),n=finishCalls;await page.locator('article[data-app="folie"] .app-open').click();await page.waitForTimeout(400);assert(finishCalls>n);assert.equal(page.url(),before);finish='completed';});
  await check('server closure blocks launch at click time',async()=>{await home();await page.request.get(f.urls.hub+'/__set?mode=closed');await page.locator('article[data-app="folie"] .app-open').click();await page.locator('#fejl').filter({hasText:/lærer|ikke åbnes/}).waitFor();assert(page.url().startsWith(f.urls.hub));});
  await check('offline local workshop remains available',async()=>{await home('down');assert.match(await page.locator('article[data-app="folie"]').innerText(),/Tilgængelig offline/);assert.match(await page.locator('article[data-app="microbit"]').innerText(),/Kræver internet/);await page.locator('article[data-app="folie"] .app-open').click();await page.waitForURL(f.urls.folie+'**');});
  for(const width of [1366,1920])await check('Folie full help/new labels at '+width,async()=>{await page.setViewportSize({width,height:width===1366?708:1020});await started(f.urls.folie);const data=await page.locator('.laser-action-label').evaluateAll(es=>es.filter(e=>/Hjælp|Ny tegning/.test(e.textContent)).map(e=>{const range=document.createRange();range.selectNodeContents(e);const t=range.getBoundingClientRect(),b=e.closest('button').getBoundingClientRect();return {text:e.textContent,textRight:t.right,buttonRight:b.right,textLeft:t.left,buttonLeft:b.left};}));assert.equal(data.length,2);assert(data.every(e=>e.textRight<=e.buttonRight&&e.textLeft>=e.buttonLeft),JSON.stringify(data));await snap('folie-'+width);if(width===1366)await audit('folie');});
  await page.setViewportSize({width:1366,height:708});
  await started(f.urls.folie);
  const svg='<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100" data-workshop="vinyl"><rect x="10" y="10" width="35" height="20" fill="black"/></svg>';
  const q=new URLSearchParams({id:'b'.repeat(32),name:'Syntetisk navneskilt',folder:'5A · Testelev',kind:'folie',filename:'skilt.svg',expected:'0'});
  const saved=await page.request.post(f.urls.projekter+'/_elevhub/projekter/api/save?'+q,{headers:{Origin:f.urls.projekter.replace(/\/$/,''),'X-Elevhub-Project':'1','Content-Type':'application/octet-stream'},data:svg});assert.equal(saved.status(),200,await saved.text());
  await check('picker first project identity and action visible together',async()=>{await page.locator('#import-button').click();const frame=page.frameLocator('iframe[title="Projektarkiv"]');await frame.locator('.project').first().waitFor();await snap('project-picker');for(const selector of ['h3','.project-folder','.open-project']){const b=await frame.locator('.project').first().locator(selector).boundingBox();const host=await page.locator('iframe[title="Projektarkiv"]').boundingBox();assert(b&&b.y>=host.y&&b.y+b.height<=host.y+host.height,'First project '+selector+' clipped: '+JSON.stringify({b,host}));}await audit('picker');});
  await check('keyboard picker cancellation keeps current work and focus',async()=>{if(await page.locator('#elevhub-project-dialog').evaluate(d=>d.open))await page.locator('#project-library-close').click();await page.locator('[data-add="rect"]').click();const before=await page.evaluate(()=>window.ElevhubProjectAdapter.signature());await page.locator('#import-button').focus();await page.keyboard.press('Enter');await page.frameLocator('iframe[title="Projektarkiv"]').locator('.open-project').first().focus();await page.keyboard.press('Enter');await page.locator('#project-finish-continue').waitFor();await page.locator('#project-finish-continue').focus();await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>window.ElevhubProjectAdapter.signature()),before);assert(await page.locator('#import-button').evaluate(e=>e===document.activeElement));await snap('work-preserved');});
  await check('project filters contain only actual project categories',async()=>{await page.goto(f.urls.projekter);await page.locator('.project').first().waitFor();assert.deepEqual(await page.locator('#kind option').evaluateAll(es=>es.map(e=>e.value)),['','folie']);const query=new URLSearchParams({id:'c'.repeat(32),name:'Syntetisk model',folder:'5A · Testelev',kind:'3dprint',filename:'model.mkv3d',expected:'0'});const r=await page.request.post(f.urls.projekter+'/_elevhub/projekter/api/save?'+query,{headers:{Origin:f.urls.projekter.replace(/\/$/,''),'X-Elevhub-Project':'1','Content-Type':'application/octet-stream'},data:JSON.stringify({version:1,objects:[]})});assert.equal(r.status(),200,await r.text());await page.reload();await page.getByRole('heading',{name:'Syntetisk model',exact:true}).waitFor();assert.match(await page.locator('#kind').innerText(),/3D-værksted/);assert.doesNotMatch(await page.locator('#kind').innerText(),/CaDoodle|Datatræneren|Kredsløb/);await snap('project-filters');});
  for(const kind of ['laser','3dprint'])await check(kind+' local launch and shared start dialog',async()=>{await home();await page.locator('article[data-app="'+kind+'"] .app-open').click();await page.waitForURL(f.urls[kind]+'**');await page.locator('#project-classroom').waitFor();await page.locator('#project-start-confirm').click();assert(await page.locator('#project-classroom').evaluate(e=>e===document.activeElement));await snap(kind+'-start');});
 }finally{try{fs.writeFileSync(path.join(output,'acceptance.json'),JSON.stringify({results,axe:axe.map(a=>({name:a.name,violations:a.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.length}))}))},null,2));}finally{try{await browser.close();}finally{await f.close();}}}
 if(results.some(r=>!r.pass))process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});
