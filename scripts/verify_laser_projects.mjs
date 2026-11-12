// Actual new laser editor and shared lifecycle on disposable Windows loopback services.
// No machine submission; legacy .projekt.svg compatibility is a separate known gap.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
const fixture = spawn(process.env.ELEVHUB_TEST_PYTHON || 'python', ['linux-elevhub/tests/finish_fixture.py'], {stdio:['pipe','pipe','pipe'],windowsHide:true});
let stderr = ''; fixture.stderr.on('data', bytes => stderr += bytes);
const ports = await new Promise((resolve,reject) => {
  let output=''; const timeout=setTimeout(()=>{fixture.kill();reject(Error('Fixture startup: '+stderr));},10000);
  fixture.stdout.on('data',bytes=>{output+=bytes;if(output.includes('\n')){clearTimeout(timeout);resolve(JSON.parse(output.split('\n')[0]));}});
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code+': '+stderr));});
});
const local='http://127.0.0.1:'+ports.local, editor='http://127.0.0.1:'+ports.laser+'/laser.html';
const output=process.env.LASER_PROJECT_REVIEW_OUTPUT || path.join(os.tmpdir(),'mkv-laser-project-review');
await mkdir(output,{recursive:true});
let browser;
try {
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1024,height:688}}), errors=[], dialogs=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('dialog',dialog=>{dialogs.push(dialog.type());dialog.dismiss();});
  const projects=async()=>(await(await fetch(local+'/_elevhub/projekter/api/list')).json()).projects;
  const signature=()=>page.evaluate(()=>window.ElevhubProjectAdapter.signature());
  const scene = signature => {
    const data=JSON.parse(signature);
    // Import canonicalizes property order and the optional false rectangle flag.
    data.objects=data.objects.map(object=>object.type==='rect'?{...object,rounded:Boolean(object.rounded)}:object);
    return data;
  };
  const currentMeta=async()=> (await projects()).find(p=>p.name==='Min laserfigur');
  async function finish(choice) {
    await page.waitForTimeout(900);
    const request=await(await fetch(local+'/_elevhub/session/request',{method:'POST',headers:{'Content-Type':'application/json','X-Elevhub-Navigation':'1'},body:'{}'})).json();
    assert.equal(request.state,'pending');
    await page.locator('#project-finish-'+choice).click();
    for(let i=0;i<80;i++){
      const status=await(await fetch(local+'/_elevhub/session/status?id='+request.id)).json();
      if(status.state!=='pending')return status.state;
      await page.waitForTimeout(50);
    }
    throw Error('Finish acknowledgement timed out');
  }
  const previousPage=new URL('/laservaerksted/PAPER-LICENSE.txt',editor).href;
  await page.goto(previousPage);
  await page.goto(editor);
  await page.locator('#project-start-confirm').waitFor();
  await page.screenshot({path:path.join(output,'initial-metadata.png')});
  await page.locator('#project-classroom').fill('4A');
  await page.locator('#project-student').fill('Testelev');
  await page.locator('#project-title').fill('Min laserfigur');
  await page.locator('#project-start-confirm').click();
  await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
  assert.equal((await projects()).length,0,'metadata must not create an empty saved file');
  assert.equal(await page.locator('#projectName').inputValue(),'Min laserfigur');
  const initial=JSON.parse(await signature()).objects.length;
  await page.locator('[data-add-type=rect]').click();
  assert.equal(JSON.parse(await signature()).objects.length,initial+1);
  const first=await signature();
  await page.locator('#saveProjectButton').click();
  await page.waitForFunction(()=>document.querySelector('#statusMessage').textContent.includes('Gemt på denne computer'));
  let meta=await currentMeta();
  assert.equal(meta.folder,'4A · Testelev'); assert.match(meta.filename,/\.laser\.json$/);
  assert.equal(meta.revision,1);
  const file=await(await fetch(local+'/_elevhub/projekter/api/file?id='+meta.id)).json();
  assert.equal(file.project.objects.length,initial+1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'new editor must fit 1024px without horizontal scrolling');
  for(const id of ['saveProjectButton','openProjectButton'])assert.equal(await page.locator('#'+id).isVisible(),true);
  await page.screenshot({path:path.join(output,'saved-1024x688.png')});
  console.log('PASS new laser metadata, visible shape creation, real .laser.json save and 1024x688 UI');
  await page.locator('[data-add-type=circle]').click();
  const edited=await signature(); assert.notEqual(edited,first);
  assert.equal(await finish('continue'),'cancelled'); assert.equal(await signature(),edited);
  assert.equal(await finish('save'),'completed');
  meta=await currentMeta(); assert.equal(meta.revision,2);
  assert.equal(await page.evaluate(()=>window.ElevhubProjectAdapter.hasContent()),false);
  assert.equal(await page.evaluate(()=>window.ElevhubProjectSession.naming()),null);
  await page.keyboard.press('Control+z');
  assert.equal(await page.evaluate(()=>window.ElevhubProjectAdapter.hasContent()),false,'finish must clear old undo');
  const cleared=await signature();
  await page.evaluate(()=>{
    window.addEventListener('keydown',event=>{
      if(event.altKey&&event.key==='ArrowLeft') sessionStorage.setItem('laser-native-return-prevented',String(event.defaultPrevented));
    });
    window.addEventListener('beforeunload',()=>{
      sessionStorage.setItem('laser-native-return-state',window.ElevhubProjectAdapter.signature());
    });
  });
  await page.locator('#designCanvas').focus();
  await page.keyboard.press('Alt+ArrowLeft');
  assert.equal(await signature(),cleared,'Alt+ArrowLeft must not move a selected starter figure');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('laser-native-return-prevented')),'false','editor must allow native browser navigation');
  // CDP key events do not invoke Chromium's native window accelerator in headless mode.
  // Exercise the browser history transition separately, after checking the actual DOM key event.
  await page.goBack();
  await page.waitForURL(previousPage,{timeout:4000});
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('laser-native-return-state')),cleared,'native browser return must not move the selected starter figure');
  assert.deepEqual(dialogs,[],'native return after approved finish must not trigger Leave app');
  console.log('PASS actual Alt+ArrowLeft preserves the selected figure and allows browser history return without a warning');
  await page.goto(editor); await page.locator('#project-start-confirm').waitFor();
  for(const id of ['project-classroom','project-student'])assert.equal(await page.locator('#'+id).inputValue(),'');
  assert.notEqual(await page.locator('#project-title').inputValue(),'Min laserfigur','next pupil must not inherit the previous title');
  await page.locator('#project-start-open').click();
  await page.frameLocator('iframe[title="Projektarkiv"]').locator('.project[data-id="'+meta.id+'"] .open-project').click();
  await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
  assert.deepEqual(scene(await signature()),scene(edited),'picker must reopen all editable objects');
  assert.equal(await page.evaluate(()=>window.ElevhubProjectSession.naming().student),'Testelev');
  await page.screenshot({path:path.join(output,'reopened-1024x688.png')});
  await page.locator('[data-add-type=rect]').click();
  assert.equal(await finish('discard'),'completed');
  assert.equal((await currentMeta()).revision,2,'discard must preserve the last saved revision');
  await page.goto(editor+'?elevhub_project='+meta.id);
  await page.waitForFunction(()=>window.ElevhubProjectSession?.naming()?.student==='Testelev');
  assert.deepEqual(scene(await signature()),scene(edited),'direct project opening must also preserve identity and scene');
  await page.locator('[data-add-type=circle]').click();
  assert.equal(await finish('discard'),'completed');
  await page.reload(); await page.locator('#project-start-confirm').waitFor();
  assert.equal(await page.locator('#project-student').inputValue(),'');
  assert.equal(await page.evaluate(()=>window.ElevhubProjectAdapter.hasContent()),false);
  assert.deepEqual(dialogs,[],'approved finish must not trigger an extra browser warning');
  assert.deepEqual(errors,[]);
  console.log('PASS new laser continue/save/discard, picker/direct reopen, identity+undo clearing and clean next pupil');
  console.log('Screenshots: '+output);
} finally {
  await browser?.close();
  fixture.stdin.end('\n');
  await once(fixture,'exit');
}
