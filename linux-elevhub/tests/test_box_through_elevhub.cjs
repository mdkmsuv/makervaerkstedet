const {startFixture,loadPlaywright,loadJSZip}=require('./student_journey_fixture.js');
const path=require('node:path');
// End-to-end regression through the pupil HTTPS gateway, not the HTTP backend.
// Isolated browser storage. Generates geometry only; never submits to a queue.
const {chromium}=loadPlaywright();
const assert=require('node:assert/strict');


(async()=>{
 const fixture=await startFixture();
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  // The isolated pilot uses its own CA. Actual TLS/Origin validation is also
  // covered by test_sikkerhed with a verified temporary test certificate.
  const page=await browser.newPage({ignoreHTTPSErrors:true,viewport:{width:1366,height:900}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(fixture.urls.gateway+'laser.html');
  await page.locator('.laser-tool-levels summary').click();
  await page.locator('[data-level="3"]').click();
  await page.locator('#laser-tool-tab-more').click();
  const before=await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')));
  const initialResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/laser/boxes');
  await page.locator('#box-button').click();
  assert.equal((await initialResponse).status(),200,'Default box must work through pupil gateway');
  await page.waitForFunction(()=>!document.querySelector('#box-insert').disabled);
  assert.equal(await page.locator('#box-status').evaluate(n=>n.classList.contains('error')),false);
  assert(await page.locator('#box-preview svg path').count()>0);

  await page.locator('#box-width').fill('100');
  await page.locator('#box-depth').fill('80');
  await page.locator('#box-height').fill('60');
  const updatedResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/laser/boxes');
  await page.locator('#box-generate').click();
  const response=await updatedResponse;
  assert.equal(response.status(),200,'Changed box dimensions must work through pupil gateway');
  const result=await response.json();
  await page.waitForFunction(()=>!document.querySelector('#box-insert').disabled);
  await page.screenshot({path:path.join(fixture.output,'box-tool-fixed.png')});
  await page.locator('#box-insert').click();
  assert.equal(await page.locator('#box-dialog').evaluate(n=>n.open),false);
  const after=await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')));
  assert.deepEqual(after.items.slice(0,before.items.length),before.items,'Existing pupil drawing must be preserved');
  const added=after.items.slice(before.items.length);
  assert(added.length>0&&added.every(o=>o.name.startsWith('Kasse')&&o.op==='cut'&&o.d.length>0));
  assert(after.width>=result.details.width&&after.height>=result.details.height);
  await page.keyboard.press('Control+z');
  assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1'))),before,'Undo must restore the complete original drawing');
  assert.deepEqual(errors,[]);
  console.log('PASS HTTPS pupil gateway: default and changed dimensions, preview, editable cut lines, original drawing preserved, undo, no browser errors.');
 }finally{await browser.close();await fixture.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
