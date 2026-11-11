'use strict';
// Run only after the owner has explicitly started the installed DEMO runtime.
// Uses real installed HTTP services; no fixture, process stop, code edit or hardware.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {createHash,randomUUID}=require('node:crypto'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),data=process.env.INSTALLED_DEMO_DATA||'D:/Makervaerkstedet/Demo-data-20261013';
const teacherOrigin=process.env.INSTALLED_TEACHER_ORIGIN||'https://127.0.0.1:8780';
const printOrigin='http://127.0.0.1:8765',laserOrigin='http://127.0.0.1:8766';
const output=path.join(root,'output/release-20261013/installed-journey');
const runId=process.env.INSTALLED_RESUME_RUN||randomUUID().slice(0,8),report={startedAt:new Date().toISOString(),reason:'Actual sealed installed DEMO teacher/pupil UI journey with synthetic STL/SVG and persistent authoritative print/laser queues.',flows:[],screenshots:[],errors:[]};
let teacherCode='',browser;
function cube(){
 const points=[[0,0,0],[20,0,0],[20,20,0],[0,20,0],[0,0,20],[20,0,20],[20,20,20],[0,20,20]],faces=[[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]];
 const bytes=Buffer.alloc(84+faces.length*50);bytes.write('DEMO synthetic cube - no physical production');bytes.writeUInt32LE(faces.length,80);
 faces.forEach((face,index)=>face.forEach((vertex,corner)=>points[vertex].forEach((value,axis)=>bytes.writeFloatLE(value,84+index*50+12+corner*12+axis*4))));return bytes;
}
(async()=>{
 try{
  const origin=new URL(teacherOrigin);assert(['127.0.0.1','localhost'].includes(origin.hostname)&&origin.port==='8780');
  const marker=JSON.parse(await fs.readFile(path.join(data,'demo-workshop.json'),'utf8'));
  assert.equal(marker.schema,1);assert.equal(marker.mode,'simulation');assert.deepEqual(marker.components,{print:'printvaerksted',laser:'designvaerksted',hub:'hub'});
  const loginFile=await fs.readFile(path.join(data,'hub/LAERERKODE.txt'),'utf8');teacherCode=(loginFile.match(/^Kode:\s*(.+)$/m)?.[1]||loginFile).trim();assert(teacherCode.length>=8);
  report.scriptSha256=createHash('sha256').update(await fs.readFile(__filename)).digest('hex');report.dataRoot=data;report.teacherOrigin=teacherOrigin;
  await fs.mkdir(output,{recursive:true});
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||'chrome'});report.browserVersion=browser.version();
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1366,height:900}}),teacher=await context.newPage(),pupil=await context.newPage();
  for(const page of [teacher,pupil])page.on('pageerror',error=>report.errors.push(error.message));teacher.on('dialog',dialog=>dialog.accept());
  const state=async()=>{const response=await context.request.get(teacherOrigin+'/api/makerspace/state');assert.equal(response.status(),200);return response.json();};
  const shot=async(name,page=teacher)=>{const filename=runId+'-'+name+'.png';await page.screenshot({path:path.join(output,filename),fullPage:true});report.screenshots.push(filename);};
  await teacher.goto(teacherOrigin+'/kontrolpanel');await teacher.locator('#login-kode').fill(teacherCode);await teacher.locator('#login-form button').click();await teacher.locator('#panel').waitFor();
  const system=await context.request.get(teacherOrigin+'/api/laerer/system');assert.equal(system.status(),200);report.server=(await system.json()).server;
  await teacher.goto(teacherOrigin+'/makerspace');await teacher.locator('#status').filter({hasText:'opdateret'}).waitFor();
  let current=await state();assert(current.printer_administration.printers.length>0);assert(current.printer_administration.printers.every(printer=>printer.name.startsWith('DEMO · ')&&printer.id.startsWith('demo-')));
  assert(current.laser_administration.stations.some(station=>station.id==='demo-laser'&&station.name.startsWith('DEMO · ')));
  const printState=await context.request.get(printOrigin+'/api/state');assert.equal(printState.status(),200);assert.equal((await printState.json()).demo,true);
  await shot('teacher-demo-equipment');report.flows.push('Actual teacher UI login and installed backend state confirm explicitly marked DEMO equipment');
  if(!process.env.INSTALLED_RESUME_LASER){const filename='DEMO-print-'+runId+'.stl';let printJob;if(process.env.INSTALLED_RESUME_RUN){printJob=(await state()).printer_administration.workflow_jobs.find(job=>job.file_name===filename||job.filename===filename||job.name===filename);assert(printJob,'Previously accepted synthetic DEMO job must exist');report.flows.push('Resumed previously accepted actual UI upload after correcting test HTTP 201 expectation');}else{await pupil.goto(printOrigin+'/');await pupil.locator('#file-input').setInputFiles({name:filename,mimeType:'application/sla',buffer:cube()});
  await pupil.waitForFunction(()=>!document.querySelector('#send-button').disabled);
  const accepted=pupil.waitForResponse(response=>response.url()===printOrigin+'/api/jobs'&&response.request().method()==='POST');await pupil.locator('#send-button').click();const upload=await accepted;assert.equal(upload.status(),201);printJob=await upload.json();report.printJobId=printJob.id;
  await pupil.locator('#print-confirmation').waitFor();assert(await pupil.locator('#print-confirmation-demo').isVisible());await shot('print-pupil-receipt',pupil);}report.printJobId=printJob.id;
  const card=()=>teacher.locator('#print-workflow-jobs article').filter({has:teacher.getByRole('heading',{name:filename,exact:true})});
  const refresh=()=>teacher.locator('#print-workflow-refresh').click();
  async function waitPrint(predicate,timeout=15000){const until=Date.now()+timeout;while(Date.now()<until){const value=(await state()).printer_administration.workflow_jobs.find(job=>job.id===printJob.id);if(value&&predicate(value))return value;await new Promise(resolve=>setTimeout(resolve,500));}throw Error('Installed print job did not reach the expected queue/workflow state');}
  await waitPrint(job=>job.status==='workflow_wait');
  for(let index=0;index<3;index++){
   const before=await waitPrint(job=>job.status==='workflow_wait');await refresh();const done=teacher.waitForResponse(response=>response.url()===teacherOrigin+'/api/makerspace/commands');
   await card().getByRole('button',{name:index===2?'Tillad printstart':'Bekræft næste trin',exact:true}).click();assert.equal((await done).status(),200);await waitPrint(job=>job.version>before.version);
  }
  await waitPrint(job=>job.status==='printing');await refresh();await shot('print-running');report.flows.push('Synthetic STL submitted through actual installed pupil UI; teacher workflow confirmations start the simulated print');
  console.log('Waiting for the installed simulated printer timer; no printer command is sent.');
  await waitPrint(job=>job.status==='done',85000);
  let printDone;
  for(const label of ['Bekræft afhentning','Jeg har ryddet pladen']){const before=(await state()).printer_administration.workflow_jobs.find(job=>job.id===printJob.id);await refresh();const done=teacher.waitForResponse(response=>response.url()===teacherOrigin+'/api/makerspace/commands');await card().getByRole('button',{name:label,exact:true}).click();const response=await done;assert.equal(response.status(),200);const receipt=await response.json();if(label==='Jeg har ryddet pladen')printDone=receipt.result;else await waitPrint(job=>job.version>before.version);}
  assert.equal(printDone.status,'done');assert.equal(printDone.step,null);assert(!(await state()).printer_administration.workflow_jobs.some(job=>job.id===printJob.id));report.printResult={id:printDone.id,status:printDone.status,revision:printDone.workflow_revision,step:printDone.step};await refresh();await shot('print-completed-cleared');report.flows.push('Actual simulated printer finishes; teacher collects and clears the plate through installed UI');
  }else{const prior=JSON.parse(await fs.readFile(process.env.INSTALLED_RESUME_LASER,'utf8'));assert.equal(prior.printResult.status,'done');report.printResult=prior.printResult;report.flows.push(...prior.flows.slice(1));}const project='DEMO-laser-'+runId;let laserJob;if(process.env.INSTALLED_RESUME_LASER){laserJob=(await state()).laser_administration.jobs.find(job=>job.project===project);assert(laserJob,'Previously accepted synthetic laser job must exist');report.flows.push('Resumed actual pupil laser upload after correcting test HTTP 200 expectation');}else{await pupil.goto(laserOrigin+'/laser.html');await pupil.locator('[data-add="rect"]').click();await pupil.waitForFunction(()=>!document.querySelector('#send-button').disabled);await pupil.locator('#send-button').click();
  await pupil.locator('#student-name').fill('DEMO-elev');await pupil.locator('#student-class').fill('DEMO');await pupil.locator('#student-project').fill(project);
  const laserAccepted=pupil.waitForResponse(response=>response.url()===laserOrigin+'/api/laser/submit'&&response.request().method()==='POST');await pupil.locator('#confirm-send').click();const laserUpload=await laserAccepted;assert.equal(laserUpload.status(),200);laserJob=await laserUpload.json();report.laserJobId=laserJob.id;
  await pupil.locator('#send-success-title').waitFor();await shot('laser-pupil-receipt',pupil);}report.laserJobId=laserJob.id;
  const laserCard=()=>teacher.locator('#laser-jobs article').filter({has:teacher.getByRole('heading',{name:project,exact:true})});
  async function laserClick(label){await teacher.locator('#laser-refresh').click();const done=teacher.waitForResponse(response=>response.url()===teacherOrigin+'/api/makerspace/commands');await laserCard().getByRole('button',{name:label,exact:true}).click();const response=await done;assert.equal(response.status(),200);const receipt=await response.json();await new Promise(resolve=>setTimeout(resolve,200));return receipt.result;}
  await laserClick('Knyt til laserstation');
  let laserDone;for(const label of ['Bekræft næste trin','Bekræft næste trin','Tillad klargøring til laser','Lærer: arbejdet er fysisk færdigt','Bekræft afhentning','Jeg har ryddet laserområdet']){laserDone=await laserClick(label);if(label==='Tillad klargøring til laser')await shot('laser-ready-for-simulated-finish');}
  current=await state();assert(!current.laser_administration.jobs.some(job=>job.id===laserJob.id));assert.equal(laserDone.status,'done');assert.equal(laserDone.step,null);report.laserResult={id:laserDone.id,status:laserDone.status,revision:laserDone.workflow_revision,step:laserDone.step};await teacher.locator('#laser-refresh').click();await shot('laser-completed-cleared');
  report.flows.push('Actual SVG pupil submission enters installed laser Inbox; teacher binds DEMO station, approves, finishes and clears its persistent workflow');
  assert.deepEqual(report.errors,[]);report.status='passed';console.log('PASS installed DEMO journey: '+report.flows.length+' actual UI flow groups');
 }catch(error){report.status='failed';report.failure=String(error.stack||error).replaceAll(teacherCode||'__no_code__','[redacted]');console.error(report.failure);process.exitCode=1;}
 finally{teacherCode='';if(browser)await browser.close();report.finishedAt=new Date().toISOString();await fs.mkdir(output,{recursive:true});const basename='installed-demo-'+report.startedAt.replaceAll(/[:.]/g,'-');await fs.writeFile(path.join(output,basename+'.json'),JSON.stringify(report,null,2)+'\n');await fs.writeFile(path.join(output,basename+'.log'),report.flows.map(flow=>'PASS '+flow).join('\n')+'\n'+(report.failure||''));}
})();
