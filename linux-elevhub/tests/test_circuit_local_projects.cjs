const {startFixture,loadPlaywright,loadJSZip}=require('./student_journey_fixture.js');
const path=require('node:path');
const {chromium} = loadPlaywright();
const assert = require('node:assert/strict');
(async () => {
 const fixture=await startFixture();
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1280,height:900}}), errors=[], external=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/.test(r.url())&&!r.url().startsWith('http://127.0.0.1:'))external.push(r.url());});
  page.on('dialog',d=>d.accept());
  await page.goto(fixture.urls.kredsloeb+'/');
  await page.waitForFunction(()=>window.ElevhubProjectAdapter);
  await page.locator('#project-classroom').fill('5A');
  await page.locator('#project-student').fill('Testelev');
  await page.locator('#project-title').fill('Mit første kredsløb');
  await page.locator('#project-start-confirm').click();
  await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
  // Freeze the simulator before a byte-for-byte persistence check: lamp
  // temperature is part of CircuitJS export and changes while time advances.
  const first=await page.evaluate(async()=>{
   await window.ElevhubProjectAdapter.export();
   document.querySelector('#simulator').contentWindow.CircuitJS1.setSimRunning(false);
   return await(await window.ElevhubProjectAdapter.export()).text();
  });
  assert(first.startsWith('$ ') || first.startsWith('<cir '));
  await page.locator('#elevhub-project-bar').getByRole('button',{name:'Gem projekt',exact:true}).click();
  const dialog=page.locator('#elevhub-project-dialog');
  await page.waitForFunction(()=>document.querySelector('#elevhub-project-bar')?.innerText.includes('Gemt på denne computer'));
  const project=await page.evaluate(async()=>{const d=await(await fetch('/_elevhub/projekter/api/list')).json();return d.projects.find(p=>p.name==='Mit første kredsløb');});
  assert.equal(project.kind,'kredsloeb');
  const downloaded=await(await page.request.get(fixture.urls.kredsloeb+'/_elevhub/projekter/api/file?id='+project.id)).text();
  assert.equal(downloaded,first);
  // Re-selecting even the same example starts a new project. It must never
  // keep the previous saved identity and later autosave over those edits.
  await page.locator('.example-button[data-example-id="kontakt"]').click();
  await page.waitForFunction(()=>document.querySelector('.example-button[data-example-id="kontakt"]')?.getAttribute('aria-pressed')==='true');
  assert.equal((await(await page.request.get(fixture.urls.projekter+'/_elevhub/projekter/api/meta?id='+project.id)).json()).revision,1);
  await page.locator('#elevhub-project-bar').getByRole('button',{name:'Åbn projekt',exact:true}).click();
  const picker=page.frameLocator('#elevhub-project-dialog iframe');
  await picker.locator('#search').fill('Mit første kredsløb');
  await picker.getByRole('button',{name:'Åbn her',exact:true}).first().click();
  await page.locator('#project-finish-discard').click();
  await dialog.waitFor({state:'hidden'});
  const stableCircuit=text=>text.replace(/\s+te="[^"]*"/g,' te="<runtime>"');
  assert.equal(stableCircuit(await page.evaluate(async()=>await(await window.ElevhubProjectAdapter.export()).text())),stableCircuit(first));
  await page.screenshot({path:path.join(fixture.output,'circuit-project-restored.png')});
  const health=await(await page.request.get(fixture.urls.projekter+'/_elevhub/health')).json();
  assert.equal(health.navigation.id,'kredsloeb','embedded picker must not change current workshop');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('PASS CircuitJS full text save/download, same-example isolation, project picker restore, correct local navigation and no external requests');
 }finally{await browser.close();await fixture.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
