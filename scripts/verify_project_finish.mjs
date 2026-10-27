import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {once} from 'node:events';
import {mkdir, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const {chromium} = require('playwright');
const fixture = spawn('python', ['linux-elevhub/tests/finish_fixture.py'], {stdio:['pipe','pipe','pipe'], windowsHide:true});
let stderr = ''; fixture.stderr.on('data', b => { stderr += b; });
let output = '';
const ports = await new Promise((resolve, reject) => {
  fixture.stdout.on('data', b => { output += b; if(output.includes('\n'))resolve(JSON.parse(output.split('\n')[0])); });
  fixture.on('exit', code => reject(Error('Fixture exited ' + code + ': ' + stderr)));
});
const browser = await chromium.launch({headless:true, channel:'chrome'});
const context = await browser.newContext({viewport:{width:1366,height:768}});
const page = await context.newPage(); const checks = [], errors = [];
page.on('pageerror', e => errors.push(e.message));
const pass = text => { checks.push(text); console.log('PASS ' + text); };
const local = `http://127.0.0.1:${ports.local}`;
const projectList = () => fetch(local + '/_elevhub/projekter/api/list').then(r=>r.json()).then(d=>d.projects);
async function start(name = 'Min raket') {
  await page.locator('#project-classroom').fill('5.B');
  await page.locator('#project-student').fill('Alma K.');
  await page.locator('#project-title').fill(name);
  await page.locator('#project-start-confirm').click();
  await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
}
async function nativeRequest() {
  await page.waitForTimeout(900);
  const result = await fetch(local + '/_elevhub/session/request', {method:'POST', headers:{'Content-Type':'application/json','X-Elevhub-Navigation':'1'},body:'{}'}).then(r=>r.json());
  assert.equal(result.state, 'pending'); return result.id;
}
const status = id => fetch(local + '/_elevhub/session/status?id=' + id).then(r=>r.json());
async function waitState(id, expected) {
  for(let i=0;i<40;i++){if((await status(id)).state===expected)return;await page.waitForTimeout(100);}
  assert.equal((await status(id)).state,expected);
}
try {
  await page.goto(`http://127.0.0.1:${ports.laser}/laser.html`);
  await page.locator('#project-start-confirm').waitFor();
  assert.equal((await projectList()).length, 0);
  await start();
  assert.equal(await page.locator('#project-name').inputValue(), 'Min raket');
  assert.equal((await projectList()).length,0);
  pass('entry requires class, name and title without creating empty archive files');
  await page.locator('[data-add="rect"]').click();
  let id=await nativeRequest();
  await page.locator('#project-finish-continue').click(); await waitState(id,'cancelled');
  assert.equal(await page.locator('#items > *').count(),1);
  pass('native Til start shows the three choices; continue keeps the exact project');
  id=await nativeRequest();
  await page.locator('#project-finish-save').click(); await waitState(id,'completed');
  const saved=(await projectList())[0];
  assert.equal(saved.folder,'5.B · Alma K.'); assert.equal(saved.name,'Min raket');
  assert.match(saved.filename,/5.B - Alma K. - Min raket.*projekt.svg$/);
  assert.equal(await page.locator('#items > *').count(),0);
  await page.keyboard.press('Control+z'); assert.equal(await page.locator('#items > *').count(),0);
  assert.equal(await page.locator('#student-name').inputValue(),'');
  pass('save-and-finish confirms a real archive save then clears model, pupil details and undo');
  const savedFile=await fetch(local+'/_elevhub/projekter/api/file?id='+saved.id).then(r=>r.text());
  assert.match(savedFile,/Min raket/);
  await page.reload(); await page.locator('#project-start-confirm').waitFor();
  assert.equal(await page.locator('#project-student').inputValue(),'');
  assert.equal(await page.locator('#items > *').count(),0);
  await page.locator('#project-start-open').click();
  await page.evaluate(id=>{
    const frame=document.querySelector('#elevhub-project-dialog iframe');
    window.dispatchEvent(new MessageEvent('message',{origin:location.origin,source:frame.contentWindow,data:{type:'elevhub-open-project',id}}));
  },saved.id);
  await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('#items > *').count(),1);
  await page.locator('[data-add="circle"]').click();
  id=await nativeRequest(); await page.locator('#project-finish-discard').click(); await waitState(id,'completed');
  assert.equal((await projectList())[0].revision,saved.revision);
  assert.equal(await fetch(local+'/_elevhub/projekter/api/file?id='+saved.id).then(r=>r.text()),savedFile);
  pass('reopening is explicit; discard preserves the last saved version byte-for-byte');
  await page.reload(); await start('Mislykket gemning'); await page.locator('[data-add="rect"]').click();
  await page.route('**/_elevhub/projekter/api/save?*',route=>route.fulfill({status:503,contentType:'application/json',body:'{"error":"Prøve: fuld disk"}'}));
  id=await nativeRequest(); await page.locator('#project-finish-save').click(); await waitState(id,'cancelled');
  assert.equal(await page.locator('#items > *').count(),1);
  assert.match(await page.locator('#save-status').innerText(),/fuld disk/);
  await page.unroute('**/_elevhub/projekter/api/save?*');
  pass('failed save blocks navigation and preserves editable work');
  id=await nativeRequest();
  await fetch(local+'/_elevhub/session/cancel',{method:'POST',headers:{'Content-Type':'application/json','X-Elevhub-Navigation':'1'},body:JSON.stringify({id})});
  await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('#items > *').count(),1);
  pass('cancelled native request cannot later clear an open project');
  id=await nativeRequest(); await page.locator('#project-finish-discard').click(); await waitState(id,'completed');
  await page.goto(`http://127.0.0.1:${ports.folie}/folie.html`); await start('Mit foliemærke');
  await page.locator('[data-add="rect"]').click();
  id=await nativeRequest(); await page.locator('#project-finish-save').click(); await waitState(id,'completed');
  assert.ok((await projectList()).some(p=>p.kind==='folie'&&p.folder==='5.B · Alma K.'));
  pass('the same entry, naming and native finish flow works for Folie');
  await page.goto(local+'/_elevhub/projekter/open?id='+saved.id);
  await page.waitForURL('**/laser.html?elevhub_project=*');
  await page.waitForFunction(()=>window.ElevhubProjectSession?.naming()?.student==='Alma K.');
  assert.equal(await page.locator('#items > *').count(),1);
  assert.equal(await page.locator('#elevhub-project-dialog').isVisible(),false);
  await page.keyboard.press('Control+z');
  assert.equal(await page.locator('#items > *').count(),1);
  id=await nativeRequest(); await waitState(id,'completed');
  assert.equal(await page.locator('#items > *').count(),0);
  pass('overview opens the saved project with its identity; clean finish needs no repeated question');
  assert.deepEqual(errors,[]);
  await mkdir('.runtime/project-finish-20261009',{recursive:true});
  await writeFile('.runtime/project-finish-20261009/browser-verification.json',JSON.stringify({checks,errors},null,2));
} finally {
  await browser.close(); fixture.stdin.end('\n'); await once(fixture,'exit');
}
