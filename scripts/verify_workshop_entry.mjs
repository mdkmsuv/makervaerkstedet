import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {once} from 'node:events';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const fixture=spawn('python',['linux-elevhub/tests/finish_fixture.py','--adapters'],{stdio:['pipe','pipe','pipe'],windowsHide:true});
let output='',err=''; fixture.stderr.on('data',b=>err+=b);
const ports=await new Promise((resolve,reject)=>{
  fixture.stdout.on('data',b=>{output+=b;if(output.includes('\n'))resolve(JSON.parse(output.split('\n')[0]));});
  fixture.on('exit',code=>reject(Error('Fixture '+code+': '+err)));
});
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1366,height:900}});
const checks=[];
const base=`http://127.0.0.1:${ports.local}`;
async function projects(){return (await fetch(base+'/_elevhub/projekter/api/list').then(r=>r.json())).projects;}
async function finish(choice){
  await page.waitForTimeout(850);
  const result=await fetch(base+'/_elevhub/session/request',{method:'POST',headers:{'Content-Type':'application/json','X-Elevhub-Navigation':'1'},body:'{}'}).then(r=>r.json());
  assert.equal(result.state,'pending');
  await page.locator('#project-finish-'+choice).click();
  for(let i=0;i<50;i++){
    const state=await fetch(base+'/_elevhub/session/status?id='+result.id).then(r=>r.json());
    if(state.state==='completed')return;
    await page.waitForTimeout(100);
  }
  throw Error('Finish did not complete: '+JSON.stringify(await fetch(base+'/_elevhub/session/status?id='+result.id).then(r=>r.json()))+' '+await page.locator('#elevhub-project-dialog').innerText());
}
const signature=()=>page.evaluate(()=>window.ElevhubProjectAdapter.signature());
try{
  for(const kind of (process.env.WORKSHOPS || 'scratch,datatraener,kredsloeb').split(',')){
    await page.goto(`http://127.0.0.1:${ports[kind]}/`,{waitUntil:'domcontentloaded'});
    await page.locator('#project-start-confirm').waitFor({timeout:60000});
    await page.locator('#project-classroom').fill('4.A');
    await page.locator('#project-student').fill('Bo P.');
    await page.locator('#project-title').fill('Mit '+kind+' projekt');
    await page.locator('#project-start-confirm').click();
    await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
    const baseline=await signature();
    if(kind==='scratch'){
      const field=page.getByPlaceholder('Navn',{exact:true});
      await field.fill('Månekat');await field.press('Enter');
    }else if(kind==='datatraener'){
      await page.evaluate(async()=>window.ElevhubProjectAdapter.import(new Blob([JSON.stringify({
        format:'elevhub-datatraener-project',version:1,type:'datatraener',name:'Bevægelse',
        gestureData:[{ID:1,name:'Hop',recordings:[{ID:2,data:{x:[1,2,3],y:[2,3,4],z:[3,4,5]}}],output:{}}]
      })])));
    }else{
      await page.evaluate(async()=>window.ElevhubProjectAdapter.import(new Blob([window.KREDSLOEB_EKSEMPLER.find(e=>e.id==='kondensator').circuit]),'Prøve'));
      const simulationSignature=await signature();
      const runtimeBefore=await page.evaluate(async()=>await(await window.ElevhubProjectAdapter.export()).text());
      await page.waitForTimeout(500);
      assert.equal(await signature(),simulationSignature,'capacitor simulation is not a design edit');
      const runtimeAfter=await page.evaluate(async()=>await(await window.ElevhubProjectAdapter.export()).text());
      assert.match(runtimeAfter,/vd=/,'normal save still includes simulator state');
      assert.notEqual(runtimeAfter,runtimeBefore,'the simulation has actually advanced');
      await page.evaluate(async()=>window.ElevhubProjectAdapter.import(new Blob([window.KREDSLOEB_EKSEMPLER[1].circuit]),'Mit kredsløb'));
      await page.locator('[data-example-id="kontakt"]').click();
      await page.locator('#project-finish-continue').click();
      assert.equal(await page.evaluate(()=>window.ElevhubProjectSession.naming().student),'Bo P.');
    }
    assert.notEqual(await signature(),baseline);
    const edited=await signature();
    if(kind==='kredsloeb')await page.evaluate(()=>{document.querySelector('#simulator').contentWindow.previousPupilMarker=true;});
    await finish('save');
    const saved=(await projects()).find(p=>p.kind===kind);
    assert.ok(saved);assert.equal(saved.folder,'4.A · Bo P.');
    assert.equal(await page.evaluate(()=>window.ElevhubProjectAdapter.hasContent()),false,'reset baseline before undo');
    if(kind==='kredsloeb')assert.equal(await page.evaluate(()=>document.querySelector('#simulator').contentWindow.previousPupilMarker),undefined,'previous simulator and its undo history were disposed');
    if(kind==='kredsloeb')await page.locator('#simulator').focus();
    await page.keyboard.press('Control+z');await page.waitForTimeout(200);
    assert.equal(await page.evaluate(()=>window.ElevhubProjectAdapter.hasContent()),false);
    assert.notEqual(await signature(),edited);
    assert.equal(await page.evaluate(()=>window.ElevhubProjectSession.naming()),null);
    await page.goto(`http://127.0.0.1:${ports[kind]}/?elevhub_project=${saved.id}`,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>window.ElevhubProjectSession?.naming()?.student==='Bo P.',null,{timeout:60000});
    assert.equal(await signature(),edited);
    if(kind==='kredsloeb'){
      await page.locator('[data-example-id="kontakt"]').click();
      await page.waitForFunction(()=>window.ElevhubProjectSession?.naming()?.student==='Bo P.'&&document.querySelector('.example-button.active')?.dataset.exampleId==='kontakt');
      assert.equal(await page.evaluate(()=>window.ElevhubProjectSession.naming().classroom),'4.A');
    }else await page.evaluate(()=>window.ElevhubProjectAdapter.setName('Ændret titel'));
    await finish('discard');
    assert.equal((await projects()).find(p=>p.id===saved.id).revision,1);
    checks.push(`${kind}: required naming, real save, clear identity/undo, exact reopen and discard`);
    console.log('PASS '+checks.at(-1));
  }
  const reportSuffix = process.env.WORKSHOPS ? '-'+process.env.WORKSHOPS.replace(/[^a-z0-9-]/gi,'-') : '';
  await writeFile('.runtime/project-finish-20261009/adapter-lifecycle'+reportSuffix+'.json',JSON.stringify({checks},null,2));
}finally{await browser.close();fixture.stdin.end('\n');await once(fixture,'exit');}
