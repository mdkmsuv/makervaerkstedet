/* Actual browser + teacher handler + SQLite; no desktop or pilot control. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const python = path.join(root, '.venv', 'Scripts', 'python.exe');
const fixture = path.join(__dirname, 'teacher_browser_fixture.py');
const files = ['linux-elevhub/server/elevhub/web/makerspace.js', 'linux-elevhub/server/elevhub/web/makerspace.html', 'linux-elevhub/server/elevhub/web/makerspace.css', 'linux-elevhub/server/elevhub/makerspace_api.py', 'linux-elevhub/server/elevhub/makerspace_store.py', 'linux-elevhub/server/elevhub/makerspace_workflows.py', 'linux-elevhub/server/elevhub/elevhub.py', 'linux-elevhub/tests/teacher_browser_fixture.py', 'linux-elevhub/tests/test_teacher_journey.cjs'];
const started = new Date().toISOString(), checks = [], browserErrors = [];
const report = {started, reason:'Real teacher journey covers new draft validation and connection/auth recovery, plus zero-client registration/workflow/version contracts.', command:'node linux-elevhub/tests/test_teacher_journey.cjs', inputs:Object.fromEntries(files.map(f=>[f,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,f))).digest('hex')])), checks, skips:['Physical printers/laser, admin installation, pupil devices, visual school pilot.']};
let browser, server;
async function check(name, fn) {await fn(); checks.push({name,status:'passed'});console.log('PASS '+name);}
async function main(){
  server=spawn(python,['-B',fixture],{cwd:root,stdio:['pipe','pipe','pipe']});
  let errors='';server.stderr.on('data',chunk=>{errors+=chunk;});
  const url=await new Promise((resolve,reject)=>{let data='';const timer=setTimeout(()=>reject(new Error('Fixture startup timed out: '+errors)),15000);server.stdout.on('data',chunk=>{data+=chunk;const line=data.split('\n')[0];try{const value=JSON.parse(line);clearTimeout(timer);resolve(value.url);}catch{}});server.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code+': '+errors));});});
  browser=await chromium.launch({headless:true,...(process.env.TEST_CHROMIUM?{executablePath:process.env.TEST_CHROMIUM}:{channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||(process.platform==='win32'?'chrome':undefined)})});
  report.browserVersion=browser.version();
  const context=await browser.newContext({viewport:{width:1366,height:768}}), page=await context.newPage();
  page.on('pageerror',e=>browserErrors.push(e.message));
  const snapshot=async()=>{const response=await context.request.get(url+'/api/makerspace/state');assert.equal(response.status(),200);return response.json();};
  const card=(section,title)=>page.locator('#'+section+' article').filter({has:page.getByRole('heading',{name:title,exact:true})});
  async function submit(form){const response=page.waitForResponse(r=>r.url()===url+'/api/makerspace/commands');await page.locator('#'+form+' button[type=submit]').click();const r=await response;await page.waitForFunction(()=>!document.querySelector('#'+ 'state-refresh').disabled);return r;}
  await check('Login required, wrong code explained, actual teacher login',async()=>{
    assert.equal((await context.request.get(url+'/api/makerspace/state')).status(),401);
    await page.goto(url+'/makerspace');await page.waitForURL(url+'/kontrolpanel');
    await page.locator('#login-kode').fill('wrong-password');await page.locator('#login-form button').click();await page.locator('#login-fejl').filter({hasText:'Forkert lærerkode'}).waitFor();
    await page.locator('#login-kode').fill('teacher-fixture-123');await page.locator('#login-form button').click();await page.locator('#panel').waitFor();
  });
  await check('No student clients; local school identity preserves project credit; services unknown',async()=>{
    await page.goto(url+'/makerspace');await page.locator('#status').filter({hasText:'opdateret'}).waitFor();
    const state=await snapshot();assert.deepEqual(state.computers,[]);assert.equal(state.computers_status,'ok');
    assert.equal(await page.locator('.brand-school').textContent(),'Rejseskolen');assert.equal(await page.locator('.school-logo').isVisible(),false);assert.equal(await page.locator('#retry-command').isVisible(),false);assert.match(await page.locator('.note-band').textContent(),/Kreditering: Klostermarksskolen/);
    assert.equal(state.printer_administration.status,'not_configured');assert.equal(state.laser_administration.status,'not_configured');
  });
  await check('Validate invalid and corrected workflow without saving; save and publish draft',async()=>{
    await page.locator('#workflow-name').fill('Skolens manuelle forløb');
    const approval=page.locator('#workflow-steps .step-row').nth(2);
    await approval.locator('[data-field=role]').selectOption('operator');await page.locator('#workflow-validate').click();
    await page.locator('#workflow-validation').filter({hasText:'læreren som ansvarlig'}).waitFor();assert.equal((await snapshot()).workflows.length,0);
    await approval.locator('[data-field=role]').selectOption('teacher');await page.locator('#workflow-validate').click();await page.locator('#workflow-validation').filter({hasText:'Kladden er gyldig'}).waitFor();
    assert.equal((await snapshot()).workflows.length,0);assert.equal((await submit('workflow-form')).status(),200);
    await card('workflows','Skolens manuelle forløb').getByRole('button',{name:'Aktivér',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#workflows').textContent.includes('Aktiv revision 1'));
  });
  await check('Create/edit manual station, reject duplicate, bind workflow and start actual job',async()=>{
    const workflow=(await snapshot()).workflows[0];
    await page.locator('#machine-name').fill('Loddeplads');await page.locator('#machine-location').fill('Teknikrum');await page.locator('#machine-enabled').check();await page.locator('#machine-workflow').selectOption(workflow.id);
    assert.equal((await submit('machine-form')).status(),200);await page.locator('#machine-name').fill('Loddeplads');await page.locator('#machine-location').fill('Teknikrum');assert.equal((await submit('machine-form')).status(),409);assert.equal(await page.locator('#machine-name').inputValue(),'Loddeplads');
    await card('machines','Loddeplads').getByRole('button',{name:'Redigér'}).click();await page.locator('#machine-location').fill('Teknikrum 2');assert.equal((await submit('machine-form')).status(),200);
    const machine=(await snapshot()).machines[0];await page.locator('#job-title').fill('Lod et navneskilt');await page.locator('#job-machine').selectOption(machine.id);assert.equal((await submit('job-form')).status(),200);
    assert.match(await card('jobs','Lod et navneskilt').textContent(),/workflowrevision 1/);
  });
  await check('Published edits and rollback preserve existing job revision and pinned steps',async()=>{
    await card('workflows','Skolens manuelle forløb').getByRole('button',{name:'Redigér',exact:true}).click();await page.locator('#workflow-steps [data-field=label]').nth(1).fill('Ny klargøring');assert.equal((await submit('workflow-form')).status(),200);
    await card('workflows','Skolens manuelle forløb').getByRole('button',{name:'Aktivér',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#workflows').textContent.includes('Aktiv revision 2'));
    let job=(await snapshot()).jobs[0];assert.equal(job.workflow_revision,1);assert.equal(job.definition.steps[1].label,'Forbered arbejdsplads');
    await page.locator('#job-title').fill('Anden opgave');await page.locator('#job-machine').selectOption(job.machine_id);assert.equal((await submit('job-form')).status(),200);assert.equal((await snapshot()).jobs.find(j=>j.title==='Anden opgave').workflow_revision,2);
    await card('workflows','Skolens manuelle forløb').getByRole('button',{name:'Tilbagefør til revision 1',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#workflows').textContent.includes('Aktiv revision 1'));
    job=(await snapshot()).jobs.find(j=>j.title==='Lod et navneskilt');assert.equal(job.workflow_revision,1);
    for(let i=0;i<5;i++){await card('jobs','Lod et navneskilt').getByRole('button',{name:'Bekræft næste trin'}).click();await page.waitForFunction(()=>!document.querySelector('#state-refresh').disabled);}
    assert.equal((await snapshot()).jobs.find(j=>j.title==='Lod et navneskilt').status,'done');
  });
  await check('Real version conflict preserves form; explicit refresh preserves unsaved drafts',async()=>{
    await card('machines','Loddeplads').getByRole('button',{name:'Redigér'}).click();await page.locator('#machine-name').fill('Min usikrede rettelse');
    const machine=(await snapshot()).machines[0];const changed=await context.request.post(url+'/api/makerspace/commands',{headers:{Origin:url},data:{action:'machine.update',params:{id:machine.id,name:machine.name,location:'Ændret af kollega',enabled:true,workflow_id:machine.workflow_id},expected_version:machine.version,request_id:crypto.randomBytes(16).toString('hex')}});assert.equal(changed.status(),200);
    assert.equal((await submit('machine-form')).status(),409);assert.equal(await page.locator('#machine-name').inputValue(),'Min usikrede rettelse');
    await page.locator('#state-refresh').click();assert.equal(await page.locator('#machine-name').inputValue(),'Min usikrede rettelse');await page.locator('#machine-cancel').click();
  });
  await check('Lost response applies once; same-ID retry resolves and clears only submitted form',async()=>{
    await page.locator('#machine-name').fill('Svar tabt station');await page.route('**/api/makerspace/commands',async route=>{await route.fetch();await route.abort('failed');},{times:1});
    await page.locator('#machine-form button[type=submit]').click();await page.locator('#retry-command').waitFor();assert.equal((await snapshot()).machines.filter(m=>m.name==='Svar tabt station').length,1);
    await page.locator('#retry-command').click();await page.locator('#retry-command').waitFor({state:'hidden'});assert.equal((await snapshot()).machines.filter(m=>m.name==='Svar tabt station').length,1);assert.equal(await page.locator('#machine-name').inputValue(),'');
  });
  await check('Polling failure visibly marks old status; reconnect restores status; expired session redirects',async()=>{
    await page.clock.install();await page.route('**/api/makerspace/state',route=>route.abort('failed'));await page.clock.runFor(16000);
    await page.locator('#status').filter({hasText:'sidst kendt'}).waitFor();await page.unroute('**/api/makerspace/state');await page.locator('#state-refresh').click();await page.locator('#status').filter({hasText:'opdateret'}).waitFor();
    assert.equal((await context.request.post(url+'/api/laerer/logout',{headers:{Origin:url},data:{}})).status(),200);await page.clock.runFor(16000);await page.waitForURL(url+'/kontrolpanel');await page.locator('#login').waitFor();
  });
  assert.deepEqual(browserErrors,[]);report.status='passed';
}
main().catch(error=>{report.status='failed';report.error=error.stack;console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();if(server){server.stdin.end();await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>{server.kill();resolve();},10000);server.once('exit',()=>{clearTimeout(timer);resolve();});});}
  report.finished=new Date().toISOString();report.browserErrors=browserErrors;
  const output=path.join(root,'output','maturity-final-20261013','teacher');fs.mkdirSync(output,{recursive:true});const basename='journey-'+started.replace(/[:.]/g,'-');report.raw_log=basename+'.log';fs.writeFileSync(path.join(output,report.raw_log),checks.map(c=>'PASS '+c.name).join('\n')+'\n'+(report.error||''));fs.writeFileSync(path.join(output,basename+'.json'),JSON.stringify(report,null,2));
});
