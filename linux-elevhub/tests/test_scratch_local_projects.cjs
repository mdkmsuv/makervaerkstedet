const {startFixture,loadPlaywright,loadJSZip}=require('./student_journey_fixture.js');
const path=require('node:path');
const {chromium} = loadPlaywright();
const JSZip = loadJSZip();
const assert = require('node:assert/strict');
(async () => {
 const fixture=await startFixture();
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1024,height:768}}), errors=[], external=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/.test(r.url())&&!r.url().startsWith('http://127.0.0.1:'))external.push(r.url());});
  page.on('dialog',d=>d.accept());
  await page.goto(fixture.urls.scratch+'/');
  await page.waitForFunction(()=>window.ElevhubProjectAdapter);
  await page.locator('#project-classroom').fill('5A');
  await page.locator('#project-student').fill('Testelev');
  await page.locator('#project-title').fill('Månekattens spil');
  await page.locator('#project-start-confirm').click();
  await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
  const sprite=page.getByPlaceholder('Navn',{exact:true});
  await sprite.fill('Månekat'); await sprite.press('Enter');
  await page.waitForFunction(()=>window.ElevhubProjectAdapter.signature().includes('Månekat'));
  await page.locator('#elevhub-project-bar').getByRole('button',{name:'Gem projekt',exact:true}).click();
  const dialog=page.locator('#elevhub-project-dialog');
  await page.waitForFunction(()=>document.querySelector('#elevhub-project-bar')?.innerText.includes('Gemt på denne computer'));
  const project=await page.evaluate(async()=>{const d=await(await fetch('/_elevhub/projekter/api/list')).json();return d.projects.find(p=>p.name==='Månekattens spil');});
  await sprite.fill('Stjernekat');await sprite.press('Enter');
  await page.waitForFunction(()=>window.ElevhubProjectAdapter.signature().includes('Stjernekat'));
  await page.locator('#elevhub-project-bar').getByRole('button',{name:'Gem projekt',exact:true}).click();
  await page.waitForFunction(async()=>{const d=await(await fetch('/_elevhub/projekter/api/list')).json();return d.projects.some(p=>p.revision===2);});
  await page.locator('#elevhub-project-bar').getByRole('button',{name:'Gem projekt',exact:true}).click();
  const meta=await(await page.request.get(fixture.urls.scratch+'/_elevhub/projekter/api/meta?id='+project.id)).json();
  assert.equal(meta.revision,2,'unchanged Scratch zip must not create a duplicate version');
  for(const [revision,name] of [[1,'Månekat'],[2,'Stjernekat']]){
   const response=await page.request.get(`${fixture.urls.scratch}/_elevhub/projekter/api/file?id=${project.id}&revision=${revision}`);
   const zip=await JSZip.loadAsync(await response.body());
   const saved=JSON.parse(await zip.file('project.json').async('string'));
   assert(saved.targets.some(t=>t.name===name));
   assert(Object.keys(zip.files).some(n=>n.endsWith('.svg')),'costumes in sb3');
   assert(Object.keys(zip.files).some(n=>n.endsWith('.wav')),'sounds in sb3');
  }
  await page.goto(`${fixture.urls.scratch}/?elevhub_project=${project.id}&elevhub_revision=1`);
  await page.waitForFunction(()=>window.ElevhubProjectAdapter&&window.ElevhubProjectAdapter.signature().includes('Månekat'));
  await page.locator('#elevhub-project-bar').getByRole('button',{name:'Gem projekt',exact:true}).click();
  await page.waitForFunction(async()=>{const d=await(await fetch('/_elevhub/projekter/api/list')).json();return d.projects.some(p=>p.revision===3);});
  await page.screenshot({path:path.join(fixture.output,'scratch-project-restored.png')});
  // A reload starts a fresh pupil session rather than silently rebinding the
  // previous pupil. Explicit restoration remains available through the shelf.
  await page.reload();
  await page.waitForFunction(()=>window.ElevhubProjectAdapter);
  await page.locator('#project-start-confirm').waitFor();
  await page.locator('#project-classroom').fill('6B');
  await page.locator('#project-student').fill('Næste elev');
  await page.locator('#project-title').fill('Nyt spil');
  await page.locator('#project-start-confirm').click();
  assert(!(await page.evaluate(()=>window.ElevhubProjectAdapter.signature())).includes('Månekat'));
  await page.waitForTimeout(31000);
  const afterReload=await(await page.request.get(fixture.urls.scratch+'/_elevhub/projekter/api/meta?id='+project.id)).json();
  assert.equal(afterReload.revision,3,'blank editor after reload must not overwrite an existing project');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('PASS: real Scratch .sb3 with costumes/sounds, two versions, old-version restore, no blank overwrite, no external requests');
 }finally{await browser.close();await fixture.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
