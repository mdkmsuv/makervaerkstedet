'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {spawn}=require('node:child_process'),{createHash}=require('node:crypto');
const {loadPlaywright}=require('../linux-elevhub/tests/student_journey_fixture.js');
const {chromium}=loadPlaywright(),root=path.resolve(__dirname,'..');
(async()=>{
 const report={startedAt:new Date().toISOString(),reason:'Preserve current HTML/shared receiver on older running backends returning 404 for the config script, with only historical exact parents and authoritative modern config.',flows:[],inputs:{},errors:[]};
 for(const name of ['web/project-import.js','app/project_origins.py','app/laser_server.py','web/laser.html','tests/test_print_project_import.cjs','tests/project_import_legacy_fixture.py','tests/test_project_import_legacy_browser.cjs'])report.inputs[name]=createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');
 let child,browser;let stderr='';
 try{
  child=spawn(path.join(root,'.venv/Scripts/python.exe'),['-B',path.join(__dirname,'project_import_legacy_fixture.py')],{cwd:root,stdio:['pipe','pipe','pipe'],windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});child.stderr.on('data',chunk=>stderr+=chunk);
  const urls=await new Promise((resolve,reject)=>{let data='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),15000);child.stdout.on('data',chunk=>{data+=chunk;try{const result=JSON.parse(data.split('\n')[0]);clearTimeout(timer);resolve(result);}catch{}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr));});});
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||(process.platform==='win32'?'chrome':undefined)});report.browserVersion=browser.version();report.urls=urls;
  const context=await browser.newContext(),page=await context.newPage();page.on('pageerror',error=>report.errors.push(error.message));
  // Every request to these parent origins is intercepted. No listener or pilot
  // request is created on either historical port.
  const svg='<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100"><rect x="10" y="10" width="35" height="20" fill="black"/></svg>';
  let target=urls.legacy;
  function parentHTML(){return '<html><body><script>window.received=[];const childOrigin='+JSON.stringify(new URL(target).origin)+';addEventListener("message",e=>{if(e.source!==document.querySelector("iframe").contentWindow||e.origin!==childOrigin)return;received.push(e.data.type);if(e.data.type==="elevhub-file-ready"){const bytes=new TextEncoder().encode('+JSON.stringify(svg)+').buffer;e.source.postMessage({type:"elevhub-file-import",request:"legacy-fixture",filename:"saved.svg",name:"Gemte mål",bytes},childOrigin,[bytes]);}});</script><iframe src="'+target+'/laser.html?elevhub_import=1" onload="this.contentWindow.postMessage({type:\'elevhub-file-hello\',request:\'legacy-fixture\'},'+JSON.stringify(new URL(target).origin).replaceAll('"','&quot;')+')"></iframe></body></html>';}
  for(const origin of ['http://127.0.0.1:8791','http://localhost:8791','http://127.0.0.1:8792']){
   // Route-fulfilled parent documents have no socket address classification.
   // Grant only this isolated browser's synthetic parents local-network access;
   // actual child CSP and origin checks remain in effect.
   await context.grantPermissions(['local-network-access'],{origin});
   await context.route(origin+'/**',route=>route.fulfill({status:200,contentType:'text/html',body:parentHTML()}));
  }
  assert.equal((await page.request.get(urls.legacy+'/project-import-config.js')).status(),404);
  for(const origin of ['http://127.0.0.1:8791','http://localhost:8791']){
   await page.goto(origin+'/__legacy-fixture');await page.waitForFunction(()=>window.received.includes('elevhub-file-opened'));
   const frame=page.frames().find(frame=>frame.url().startsWith(urls.legacy));assert(frame);
   const exported=await frame.evaluate(async()=>await(await window.ElevhubProjectAdapter.export()).text());assert.match(exported,/35/);assert.match(exported,/20/);assert.equal(await frame.evaluate(()=>typeof window.ElevhubProjectOrigins),'undefined');
  }
  report.flows.push('Both historical 8791 parents import real SVG bytes through actual child HTTP/CSP while the config script returns 404');
  async function blocked(parent,childURL){target=childURL;await page.goto(parent+'/__blocked-fixture');assert(page.frames().some(frame=>frame.url()==='chrome-error://chromewebdata/'));assert.deepEqual(await page.evaluate(()=>window.received),[]);}
  await blocked('http://127.0.0.1:8792',urls.legacy);report.flows.push('Another port is rejected by the older exact CSP and receives no fallback handshake');
  const config=await page.request.get(urls.disabled+'/project-import-config.js');assert.equal(config.status(),200);assert.match(await config.text(),/Object\.freeze\(\[\]\)/);
  await blocked('http://127.0.0.1:8791',urls.disabled);report.flows.push('Modern explicit empty list keeps embedding disabled');
  target=urls.custom;await page.goto('http://127.0.0.1:8792/__custom-fixture');await page.waitForFunction(()=>window.received.includes('elevhub-file-opened'));
  assert.deepEqual(await page.frames().find(frame=>frame.url().startsWith(urls.custom)).evaluate(()=>window.ElevhubProjectOrigins),['http://127.0.0.1:8792']);
  await blocked('http://127.0.0.1:8791',urls.custom);report.flows.push('Modern custom list wins and rejects the historical fallback parent');
  assert.deepEqual(report.errors,[]);report.status='passed';console.log('PASS mixed-revision project import: '+report.flows.length+' real flow groups');
 }catch(error){report.status='failed';report.failure=error.stack||String(error);console.error(report.failure);process.exitCode=1;}
 finally{
  if(browser)await browser.close();if(child){const exited=new Promise(resolve=>{if(child.exitCode!==null)return resolve();child.once('exit',resolve);});child.stdin.end();const timer=setTimeout(()=>child.kill(),15000);await exited;clearTimeout(timer);}
  report.finishedAt=new Date().toISOString();const output=path.join(root,'output/maturity-final-20261013/teacher/laserbridge');await fs.mkdir(output,{recursive:true});const basename='mixed-revision-import-'+report.startedAt.replaceAll(/[:.]/g,'-');report.raw_log=basename+'.log';
  await fs.writeFile(path.join(output,report.raw_log),report.flows.map(name=>'PASS '+name).join('\n')+'\n'+(report.failure||'')+'\n'+stderr);await fs.writeFile(path.join(output,basename+'.json'),JSON.stringify(report,null,2)+'\n');
 }
})();
