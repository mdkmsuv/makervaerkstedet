'use strict';
const {startFixture,loadPlaywright}=require('./student_journey_fixture.js');
const fs=require('node:fs/promises'),path=require('node:path');
const {chromium}=loadPlaywright(),assert=require('node:assert/strict');
// Historical filename retained for the suite. The old combined editor's box
// dialog is retired and the school HTTPS gateway carries queue traffic only.
// Verify that boundary and the current local pupil editor with editable
// grouped SVG-library geometry, size changes, drawing preservation and undo.
// Disposable storage and test CA only; no request is submitted to a queue.
(async()=>{
 const fixture=await startFixture();let browser,page;
 const output=process.env.ELEVHUB_LASER_GATEWAY_OUTPUT||fixture.output;
 const report={flows:[],errors:[],retired:'The old box dialog is not part of the current Laser editor.'};
 const project=()=>page.evaluate(async()=>JSON.parse(await(await window.ElevhubProjectAdapter.export()).text()));
 try{
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL||(process.platform==='win32'?'chrome':undefined)});
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1366,height:900}});
  page=await context.newPage();const submissions=[];
  page.on('pageerror',e=>report.errors.push(e.message));
  page.on('request',request=>{if(new URL(request.url()).pathname==='/api/laser/submit')submissions.push(request.url());});
  const gatewayHealth=await context.request.get(fixture.urls.gateway+'api/laser/health');
  assert.equal(gatewayHealth.status(),200);
  for(const route of ['laser.html','api/laser/jobs']){
    const denied=await context.request.get(fixture.urls.gateway+route);assert.equal(denied.status(),403,'school entrance must not expose '+route);
  }
  const deniedBox=await context.request.post(fixture.urls.gateway+'api/laser/boxes',{data:{width:100,depth:80,height:60}});
  assert.equal(deniedBox.status(),403,'retired remote box generation must remain unavailable');
  report.flows.push('HTTPS school entrance exposes health but rejects editor, teacher jobs and remote box generation');
  const response=await page.goto(fixture.urls.laser);
  assert.equal(response.status(),200);assert.equal(new URL(page.url()).hostname,'127.0.0.1');
  await page.locator('#designCanvas').waitFor();await page.waitForFunction(()=>window.ElevhubProjectAdapter);
  await page.locator('#project-classroom').fill('6.B');await page.locator('#project-student').fill('Prøveelev');
  await page.locator('#project-title').fill('SVG fra biblioteket');await page.locator('#project-start-confirm').click();
  await page.waitForFunction(()=>!document.querySelector('#elevhub-project-dialog').open);
  assert.equal(await page.locator('#designCanvas').getAttribute('viewBox'),'0 0 250 167');
  const before=await project();
  await page.locator('#libraryButton').click();
  await page.locator('#librarySearch').fill('Raket');
  assert.equal(await page.locator('#libraryGrid button').count(),1);
  await page.locator('#libraryGrid [data-motif-id="rocket"]').click();
  await page.locator('#finishLibraryButton').click();
  assert.equal(await page.locator('#libraryDialog').evaluate(node=>node.open),false);
  const after=await project();
  assert.deepEqual(after.project.objects.slice(0,before.project.objects.length),before.project.objects,'SVG-library insertion must preserve the original drawing');
  const added=after.project.objects.at(-1);
  assert.equal(after.project.objects.length,before.project.objects.length+1);
  assert.equal(added.type,'group');assert.equal(added.process,'engrave');assert.equal(added.children.length,3);
  assert(added.children.every(o=>o.type==='path'&&o.process==='engrave'&&o.pathData.length>0));
  assert(added.x>=0&&added.y>=0&&added.x+added.width<=250&&added.y+added.height<=167);
  report.flows.push('Current local pupil entrypoint inserts grouped editable library vectors without replacing the drawing');
  await page.locator('#objectWidth').fill('45');await page.locator('#objectWidth').press('Tab');
  const resized=await project();assert.equal(resized.project.objects.at(-1).width,45);
  assert.deepEqual(resized.project.objects.slice(0,before.project.objects.length),before.project.objects);
  await page.locator('#undoButton').click();assert.deepEqual((await project()).project.objects,after.project.objects,'undo size change must restore exact geometry');
  await page.locator('#undoButton').click();assert.deepEqual((await project()).project.objects,before.project.objects,'undo insertion must restore the complete original drawing');
  await page.locator('#redoButton').click();assert.deepEqual((await project()).project.objects,after.project.objects);
  report.flows.push('Dimensions remain editable; undo restores resizing and insertion, redo restores the grouped motif');
  assert.deepEqual(submissions,[]);assert.deepEqual(report.errors,[]);
  await fs.mkdir(output,{recursive:true});await page.screenshot({path:path.join(output,'https-current-library.png'),fullPage:true});
  report.status='passed';console.log('PASS local Laser and HTTPS queue boundary: grouped vectors, dimensions, original drawing preserved, undo/redo, no queue requests.');
 }catch(error){report.status='failed';report.failure=error.stack||String(error);console.error(report.failure);process.exitCode=1;
  if(page){await fs.mkdir(output,{recursive:true});await page.screenshot({path:path.join(output,'gateway-failure.png'),fullPage:true}).catch(()=>{});}
 }finally{if(browser)await browser.close();await fixture.close();await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'gateway-result.json'),JSON.stringify(report,null,2));}
})().catch(e=>{console.error(e);process.exitCode=1;});
