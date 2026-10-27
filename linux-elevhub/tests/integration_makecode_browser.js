// Opt-in, bounded real external integration. No login, USB or physical device.
// Separate from the six deterministic local browser journeys.
'use strict';
const {startFixture,loadPlaywright}=require('./student_journey_fixture.js');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
(async()=>{
 const at=new Date().toISOString(),fixture=await startFixture();
 const output=path.join(fixture.output,'makecode-'+at.replace(/[:.]/g,'-'));
 fs.mkdirSync(output,{recursive:true});
 const report={at,command:'node linux-elevhub/tests/integration_makecode_browser.js',
  reason:'Explicit final software acceptance: actual official MakeCode controller handshake/import/export of synthetic embedded HEX source.',
  inputSha256:Object.fromEntries([__filename,path.join(__dirname,'../server/microbit-controller/app.js'),path.join(__dirname,'../server/microbit-controller/index.html')]
   .map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')])),
  status:'running',requests:[],resources:[],signals:[],boundaries:['No USB, device, account login, publication or sharing.']};
 const browser=await loadPlaywright().chromium.launch({headless:true,channel:'chrome'});
 const deadline=setTimeout(()=>browser.close(),120000);
 let failure,page;
 try{
  page=await browser.newPage();
  page.setDefaultTimeout(90000);
  await page.addInitScript(()=>{window.makecodeSignals=[];addEventListener('message',e=>{
   if(e.origin==='https://makecode.microbit.org'&&e.source===document.querySelector('#editor')?.contentWindow)
    window.makecodeSignals.push({origin:e.origin,type:e.data?.type,action:e.data?.action,id:e.data?.id,success:e.data?.success,
     name:e.data?.project?.header?.name,error:e.data?.error});
  });});
  const pending=[];
  page.on('response',response=>{
   if(!response.url().startsWith('https://makecode.microbit.org/'))return;
   report.requests.push({url:response.url(),status:response.status()});
   if(response.request().resourceType()==='document')pending.push(response.body().then(body=>{
    report.resources.push({url:response.url(),bytes:body.length,sha256:crypto.createHash('sha256').update(body).digest('hex')});
   }).catch(()=>{}));
  });
  const hex=fs.readFileSync(path.join(__dirname,'fixtures/makecode-source.hex'));
  report.hexSha256=crypto.createHash('sha256').update(hex).digest('hex');
  const id=crypto.randomUUID().replaceAll('-','');
  const api=fixture.urls.projekter+'/_elevhub/projekter/api/';
  const query=new URLSearchParams({id,name:'MakeCode integrationsprøve',folder:'QA · Testelev',kind:'microbit',filename:'syntetisk.hex',expected:0});
  const saved=await page.request.post(api+'save?'+query,{headers:{Origin:fixture.urls.projekter,'X-Elevhub-Project':'1','Content-Type':'application/octet-stream'},data:hex});
  assert.equal(saved.status(),200);
  const info=await(await page.request.get(api+'open-info?id='+id)).json();
  assert.equal(info.program,'microbit');assert.equal(info.mode,'local');
  await page.goto(fixture.urls.projekter+'/_elevhub/projekter/open?id='+id);
  await page.waitForFunction(()=>window.makecodeSignals?.some(e=>e.type==='pxthost'&&e.action==='workspacesync'));
  // Follow the product's own redirect/import. Starting a second import while
  // its first request is in flight would make PXT reject concurrent imports.
  await page.waitForFunction(()=>document.querySelector('#elevhub-project-bar')?.textContent.includes('Åbnet MakeCode integrationsprøve'));
  const exported=await page.evaluate(async()=>JSON.parse(await(await window.ElevhubProjectAdapter.export()).text()));
  assert.equal(exported.project.header.name,'Elevhub-pilot');
  assert.equal(exported.project.text['main.ts'],info.editable.project.text['main.ts']);
  // Blockly uniformly translates imported blocks to the canvas origin. Keep
  // exact XML and relative block positions, allowing only that translation.
  const normalizedBlocks=text=>{
   for(const axis of ['x','y']){
    const pattern=new RegExp('\\b'+axis+'="(-?[0-9.]+)"','g');
    const positions=[...text.matchAll(pattern)].map(match=>Number(match[1]));
    const minimum=Math.min(...positions);
    if(positions.length)text=text.replace(pattern,(_match,value)=>axis+'="'+(Number(value)-minimum)+'"');
   }
   return text;
  };
  assert.equal(normalizedBlocks(exported.project.text['main.blocks']),normalizedBlocks(info.editable.project.text['main.blocks']));
  const frame=page.frames().find(f=>f.url().startsWith('https://makecode.microbit.org/'));
  assert(frame,'Actual official MakeCode iframe loaded');
  report.officialFrame=frame.url();
  report.officialVersions=await frame.evaluate(()=>window.pxt?.appTarget?.versions||null);
  report.signals=await page.evaluate(()=>window.makecodeSignals);
  assert(report.signals.some(s=>s.type==='pxthost'&&s.action==='workspacesave'&&s.name==='Elevhub-pilot'));
  assert(report.signals.some(s=>s.type==='pxteditor'&&s.success===true));
  await Promise.allSettled(pending);
  assert(report.resources.length>0,'Retain actual official page resource hash');
  report.importedName=exported.project.header.name;
  report.mainTsSha256=crypto.createHash('sha256').update(exported.project.text['main.ts']).digest('hex');
  report.blocksCheck='Exact XML and relative positions after uniform canvas-origin translation';
  report.status='pass';console.log('PASS actual official MakeCode iframe: workspacesync, importproject response, workspacesave, exact embedded HEX TypeScript/blocks export.');
 }catch(error){failure=error;report.status='fail';report.error=error.stack||String(error);
  report.signals=await page?.evaluate(()=>window.makecodeSignals).catch(()=>[]);
  report.ui=await page?.locator('body').innerText().catch(()=>'');console.error(report.error);}
 finally{
  clearTimeout(deadline);await browser.close().catch(()=>{});await fixture.close();
  report.completedAt=new Date().toISOString();fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(report,null,2));
  console.log(output);
 }
 if(failure)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});
