const {startFixture,loadPlaywright,loadJSZip}=require('./student_journey_fixture.js');
const path=require('node:path');
// Real local services and disposable synthetic projects; no physical print job.
const { chromium } = loadPlaywright();
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
let out;
let pages = [
 ['hub','http://127.0.0.1:8808/','#apps .app'],
 ['print','http://127.0.0.1:8765/','#send-button'],
 ['design','http://127.0.0.1:8766/','#choose-laser'],
 ['laser','http://127.0.0.1:8766/laser.html','#drawing'],
 ['folie','http://127.0.0.1:8766/folie.html','#vinyl-setup'],
 ['scratch','http://127.0.0.1:8792/','#creative-app-header #elevhub-project-bar'],
 ['datatraener','http://127.0.0.1:8793/','.creative-data-mission'],
 ['kredsloeb','http://127.0.0.1:8794/','#examples button'],
 ['projekter','http://127.0.0.1:8791/','#search']
];
(async()=>{
 const fixture=await startFixture();out=fixture.output;
 pages=pages.map(([name,url,ready])=>[name,fixture.urls[name]+(name==='laser'||name==='folie'?'':'/'),ready]);
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--host-resolver-rules=MAP school.example.test 127.0.0.1','--proxy-bypass-list=school.example.test,127.0.0.1,localhost']});
 const report={at:new Date().toISOString(),layouts:[],flows:[],errors:[]};
 const seed=await browser.newContext();
 const query=new URLSearchParams({id:require('node:crypto').randomUUID().replaceAll('-',''),name:'Mit testprojekt',folder:'5A · Testelev',kind:'laser',filename:'test.svg',expected:0});
 const response=await seed.request.post(fixture.urls.projekter+'/_elevhub/projekter/api/save?'+query,{headers:{Origin:fixture.urls.projekter,'Content-Type':'application/octet-stream','X-Elevhub-Project':'1'},data:'<svg xmlns="http://www.w3.org/2000/svg"><rect width="20" height="20"/></svg>'});
 assert.equal(response.status(),200);await seed.close();
 try {
  for(const viewport of [{width:1024,height:768},{width:1366,height:768},{width:1920,height:1080}]){
   const context=await browser.newContext({viewport});
   for(const [name,url,ready] of pages){
    const p=await context.newPage(); const errors=[];p.on('pageerror',e=>errors.push(e.message));
    try {
     await p.goto(url,{waitUntil:'domcontentloaded'}); await p.locator(ready).first().waitFor({timeout:60000});
     if(name==='scratch') await p.locator('[class*="stage-wrapper_stage-wrapper"]').first().waitFor({timeout:60000});
     if(name==='kredsloeb') await p.waitForFunction(()=>document.getElementById('simulator').contentWindow.CircuitJS1,{timeout:30000});
     if(name==='datatraener') await p.getByRole('img',{name:'loading',exact:true}).waitFor({state:'hidden',timeout:20000});
     if(['scratch','datatraener','kredsloeb','laser','folie'].includes(name)){
      await p.locator('#project-classroom').fill('5A');await p.locator('#project-student').fill('Testelev');await p.locator('#project-title').fill('Værkstedsprøve');await p.locator('#project-start-confirm').click();
     }
     await p.evaluate(()=>document.fonts.ready);
     const layout=await p.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,height:innerHeight,
      missingImages:Array.from(document.images).filter(i=>i.currentSrc&&i.complete&&!i.naturalWidth).map(i=>i.currentSrc),
      editor:(()=>{const e=document.querySelector('.editor');if(!e)return null;const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height}})()}));
     report.layouts.push({name,viewport,...layout,errors});
     assert(layout.scrollWidth<=viewport.width+2,`${name} horizontal overflow at ${viewport.width}: ${layout.scrollWidth}`);
     assert.equal(layout.missingImages.length,0,`${name} missing images`);
     if(layout.editor) assert(layout.editor.bottom<=viewport.height+28,`${name} editor bottom ${layout.editor.bottom} exceeds screen ${viewport.height}`);
     await p.screenshot({path:`${out}/${name}-${viewport.width}.png`});
     if(viewport.width===1366){
      if(name==='hub'){
       const ids=await p.locator('#apps .app').evaluateAll(els=>els.map(e=>e.dataset.app));
       assert(ids.includes('internet'),'Internet is available on cold offline start');
       assert(await p.locator('[data-app="internet"] .app-open').isEnabled());
       for(const id of ids) assert(await p.locator(`[data-app="${id}"] .app-open`).isEnabled(),`${id} open during outage`);
       await p.request.get(fixture.urls.hub+'/__set?mode=closed');
       await p.waitForFunction(()=>!document.querySelector('[data-app="internet"]'),{},{timeout:15000});
       assert.equal(await p.locator('[data-app="laser"]').count(),0);
       await p.request.get(fixture.urls.hub+'/__set?mode=down');
       await p.waitForFunction(()=>document.querySelector('[data-app="internet"] .app-open')&&!document.querySelector('[data-app="internet"] .app-open').disabled,{},{timeout:15000});
       await p.request.get(fixture.urls.hub+'/__set?mode=open');
       await p.waitForFunction(()=>window.CreativeHub&&!window.CreativeHub.isOffline(),{},{timeout:15000});
       report.flows.push(`hub: ${ids.length} cold-offline entries open including Internet, reconnect current closed policy, outage clears restrictions, reconnect open policy`);
      }
      if(['scratch','datatraener'].includes(name)){
       await p.locator('#creative-app-header > button').click();await p.locator('#creative-app-help').waitFor();await p.locator('.creative-first-steps summary').click();assert.equal(await p.locator('.creative-first-steps li').count(),3);await p.keyboard.press('Escape');await p.locator('#creative-app-help').waitFor({state:'hidden',timeout:3000});
       if(name==='scratch')assert.equal(await p.locator('#elevhub-project-bar').count(),1);report.flows.push(`${name}: ideas/help, keyboard close${name==='scratch'?', merged save header':''}`);
      }
      if(name==='datatraener'){await p.getByText('Data',{exact:true}).click();assert(await p.getByText('Din micro:bit er ikke tilsluttet endnu.',{exact:true}).isVisible());report.flows.push('data: real disconnected hardware guidance');}
      if(name==='projekter'){
       await p.locator('#search').fill('QA-no-matching-project-20260920');await p.locator('#empty-projects').waitFor();assert.match(await p.locator('#empty-title').innerText(),/ingen|find/i);await p.locator('#clear-search').click();assert.equal(await p.locator('#search').inputValue(),'');report.flows.push('project shelf: search, no-match guidance, clear search');
      }
      if(['laser','folie'].includes(name)){
       await p.getByRole('button',{name:'Firkant',exact:true}).click();assert(await p.locator('#object-properties').isVisible());
       await p.locator('#object-width').fill('42');await p.locator('#object-width').press('Tab');
       const download=p.waitForEvent('download');await p.locator('#export-button').click();const file=await download;await file.saveAs(`${out}/qa-${name}.projekt.svg`);
       assert.match(await fs.readFile(`${out}/qa-${name}.projekt.svg`,'utf8'),/<svg/);
       await p.locator('#help-button').click();await p.locator('#help-dialog').waitFor();await p.keyboard.press('Escape');
       if(name==='folie'){await p.locator('.studio-ideas').click();await p.locator('.studio-idea-dialog').waitFor();await p.keyboard.press('Escape');}
       else{await p.locator('#laser-projects-button').click();await p.locator('#laser-project-dialog').waitFor();await p.keyboard.press('Escape');}
       await p.locator('#undo').click();assert(await p.locator('#redo').isEnabled());
       report.flows.push(`${name}: shape, width, real project export, help, ideas, undo`);
      }
      if(name==='print'){
       assert(await p.locator('#send-button').isDisabled());await p.getByRole('button',{name:'Se udfordringen: Din egen spilfigur',exact:true}).click();await p.locator('dialog[open]').waitFor();await p.keyboard.press('Escape');
       report.flows.push('print: real printer status, no-model guard, maker challenge');
      }
      if(name==='kredsloeb'){
       const buttons=p.locator('#examples button');for(let n=0;n<await buttons.count();n++){await buttons.nth(n).click();if(await p.locator('#project-finish-discard').isVisible())await p.locator('#project-finish-discard').click();await p.waitForFunction(i=>document.querySelectorAll('#examples button')[i]?.getAttribute('aria-pressed')==='true',n);assert.equal(await buttons.nth(n).getAttribute('aria-pressed'),'true');assert((await p.locator('#experiment-question').innerText()).length>10);}report.flows.push('circuit: all real examples load, selection and experiment question');
      }
     }
     console.log('PASS layout '+name+' '+viewport.width);
     if(errors.length)report.errors.push({name,viewport,errors});
    }catch(error){console.log('FAIL '+name+' '+viewport.width+': '+error.message);report.errors.push({name,viewport,error:error.message});await p.screenshot({path:`${out}/${name}-${viewport.width}-failure.png`}).catch(()=>{});}
    await p.close();
   }
   await context.close();
  }
 }finally{await browser.close();await fixture.close();await fs.writeFile(`${out}/desktop-report-${report.at.replace(/[:.]/g,'-')}.json`,JSON.stringify(report,null,2));}
 console.log(JSON.stringify({layouts:report.layouts.length,flows:report.flows,errors:report.errors},null,2));
 if(report.errors.length)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1});
