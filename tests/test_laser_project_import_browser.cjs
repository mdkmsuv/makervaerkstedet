'use strict';
// Actual project shelf iframe -> central design service with exact parent policy.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {createHash,randomUUID}=require('node:crypto');
const {startFixture,loadPlaywright}=require('../linux-elevhub/tests/student_journey_fixture.js');
const {chromium}=loadPlaywright(),root=path.resolve(__dirname,'..');
(async()=>{
 const report={startedAt:new Date().toISOString(),reason:'Central laser lacked the exact-origin import configuration used by the shared receiver; verify real saved SVG import and reject another parent.',flows:[],inputs:{},errors:[]};
 const names=['app/project_origins.py','app/server.py','app/laser_server.py','web/laser.html','web/project-import.js','linux-elevhub/tests/student_journey_fixture.py','tests/test_laser_project_import_browser.cjs'];
 for(const name of names)report.inputs[name]=createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');
 let fixture,browser;
 try{
  const previous=process.env.ELEVHUB_CENTRAL_LASER_IMPORT;process.env.ELEVHUB_CENTRAL_LASER_IMPORT='1';
  try{fixture=await startFixture();}finally{if(previous===undefined)delete process.env.ELEVHUB_CENTRAL_LASER_IMPORT;else process.env.ELEVHUB_CENTRAL_LASER_IMPORT=previous;}
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||(process.platform==='win32'?'chrome':undefined),args:['--host-resolver-rules=MAP school.example.test 127.0.0.1','--proxy-bypass-list=school.example.test,127.0.0.1,localhost']});
  report.browserVersion=browser.version();
  const context=await browser.newContext(),page=await context.newPage();page.on('pageerror',error=>report.errors.push(error.message));
  const base=fixture.urls.projekter,api=base+'/_elevhub/projekter/api/';
  const svg='<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100" data-workshop="laser"><rect x="10" y="10" width="35" height="20" fill="black"/></svg>';
  const query=new URLSearchParams({id:randomUUID().replaceAll('-',''),name:'Central laser import',folder:'QA',kind:'laser',filename:'laser.svg',expected:'0'});
  const saved=await page.request.post(api+'save?'+query,{headers:{Origin:base,'X-Elevhub-Project':'1','Content-Type':'application/octet-stream'},data:svg});assert.equal(saved.status(),200,await saved.text());
  const project=await saved.json();const info=await page.request.get(api+'open-info?id='+project.id+'&revision='+project.revision);const plan=await info.json();assert.equal(plan.mode,'frame');
  await page.goto(base+'/_elevhub/projekter/open?id='+project.id+'&revision='+project.revision);
  await page.locator('#status').filter({hasText:'Åbnet i Laserværkstedet'}).waitFor({timeout:35000});
  const frame=page.frames().find(frame=>frame.url().startsWith(plan.url));assert(frame,'central iframe loaded');
  const exported=await frame.evaluate(async()=>await(await window.ElevhubProjectAdapter.export()).text());assert.match(exported,/35/);assert.match(exported,/20/);
  assert.deepEqual(await frame.evaluate(()=>window.ElevhubProjectOrigins),[base]);assert.equal(await page.locator('#retry').isVisible(),false);
  report.flows.push('A real saved SVG opens through the project shelf iframe into configured central Laser and retains geometry');
  const failedFrames=[];page.on('requestfailed',request=>{if(request.url().startsWith(plan.url))failedFrames.push(request.failure()?.errorText);});
  const policy=(await page.request.get(fixture.urls.design+'/laser.html')).headers()['content-security-policy'];assert(policy.includes('frame-ancestors '+base+';'));assert(!policy.includes(fixture.urls.untrusted));
  await page.route(fixture.urls.untrusted+'/',async route=>{const response=await route.fetch();const text=await response.text();await route.fulfill({response,body:text.replace(fixture.urls.print+'/?elevhub_import=1',plan.url+'?elevhub_import=1')});});
  await page.goto(fixture.urls.untrusted+'/');await page.waitForFunction(()=>document.querySelector('#blocked'));
  await page.waitForFunction(()=>document.querySelector('#blocked').contentDocument===null);
  assert(page.frames().some(frame=>frame.url()==='chrome-error://chromewebdata/'),'Chromium replaced the unlisted-parent frame with its blocked document');report.blockedFrameRequests=failedFrames;assert.equal(await page.frames().find(frame=>frame.url()==='chrome-error://chromewebdata/').evaluate(()=>typeof window.ElevhubProjectAdapter),'undefined');assert.deepEqual(await page.evaluate(()=>window.received),[]);
  report.flows.push('An unlisted project-parent origin is rejected by Chromium CSP and receives no import handshake');
  assert.deepEqual(report.errors,[]);report.status='passed';console.log('PASS central Laser project import: '+report.flows.length+' real flow groups');
 }catch(error){report.status='failed';report.failure=error.stack||String(error);console.error(report.failure);process.exitCode=1;}
 finally{
  if(browser)await browser.close();if(fixture)await fixture.close();report.finishedAt=new Date().toISOString();
  const output=path.join(root,'output/maturity-final-20261013/teacher/laserbridge');await fs.mkdir(output,{recursive:true});
  const basename='laser-project-import-'+report.startedAt.replaceAll(/[:.]/g,'-');report.raw_log=basename+'.log';
  await fs.writeFile(path.join(output,report.raw_log),report.flows.map(name=>'PASS '+name).join('\n')+'\n'+(report.failure||''));
  await fs.writeFile(path.join(output,basename+'.json'),JSON.stringify(report,null,2)+'\n');
 }
})();
