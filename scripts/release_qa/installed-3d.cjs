// Browser test of the INSTALLED 3D workshop (127.0.0.1:8798) on a test machine.
// Two stages, run in this order, with a restart of the workshop in between:
//   save    saves a synthetic project and checks the next pupil starts clean
//   reopen  (any other value) reopens it, edits it, resaves it
// Usage: node installed-3d.cjs <label> <save|reopen> <output-folder>
// Playwright resolves from produkt/node_modules.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const label=process.argv[2],stage=process.argv[3],outputArgument=process.argv[4];
if(!label||!stage||!outputArgument){console.error('Brug: node installed-3d.cjs <label> <save|reopen> <outputmappe>');process.exit(2);}
const root=path.resolve(outputArgument);
fs.mkdirSync(root,{recursive:true});
const file=path.join(root,'3d-project-'+label+'.json');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1024,height:688}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const ready=()=>page.locator('#download:enabled').waitFor();
  if(stage==='save'){
   await page.goto('http://127.0.0.1:8798/model3d.html');
   await page.locator('#project-classroom').fill('Testklasse');
   await page.locator('#project-student').fill('Testelev');
   const title='SYNTHETIC '+label+' '+Date.now();
   await page.locator('#project-title').fill(title);
   await page.getByRole('button',{name:'Start projekt',exact:true}).click();
   await page.getByRole('button',{name:'Kasse',exact:true}).click();await ready();
   await page.locator('#elevhub-project-save').click();
   await page.locator('#elevhub-project-bar').filter({hasText:'Gemt på denne computer'}).waitFor();
   const listing=await (await page.request.get('http://127.0.0.1:8798/_elevhub/projekter/api/list')).json();
   const meta=listing.projects.find(p=>p.name===title);assert(meta);
   const signature=await page.evaluate(()=>ElevhubProjectAdapter.signature());
   const data=await (await page.request.get('http://127.0.0.1:8798/_elevhub/projekter/api/file?id='+meta.id)).body();
   assert.equal(JSON.stringify(JSON.parse(data).scene),signature);
   assert(await page.evaluate(()=>ElevhubProjectSession.finish()));
   await page.reload();await page.locator('#project-classroom').waitFor();
   for(const id of ['project-classroom','project-student','project-title'])assert.equal(await page.locator('#'+id).inputValue(),'');
   fs.writeFileSync(file,JSON.stringify({id:meta.id,title,signature,revision:meta.revision,saved:true,cleanNextPupil:true},null,2));
  }else{
   const saved=JSON.parse(fs.readFileSync(file));
   await page.goto('http://127.0.0.1:8798/model3d.html?elevhub_project='+saved.id);await ready();
   assert.equal(await page.evaluate(()=>ElevhubProjectAdapter.signature()),saved.signature);
   assert.equal(await page.evaluate(()=>ElevhubProjectSession.naming().student),'Testelev');
   await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
   await page.locator('[data-size="0"]').fill('45');await page.locator('[data-size="0"]').press('Enter');await ready();
   assert.notEqual(await page.evaluate(()=>ElevhubProjectAdapter.signature()),saved.signature);
   const finish=page.evaluate(()=>ElevhubProjectSession.finish());
   await page.locator('#project-finish-save').click();assert(await finish);
   const meta=await (await page.request.get('http://127.0.0.1:8798/_elevhub/projekter/api/open-info?id='+saved.id)).json();
   assert.equal(meta.project.revision,saved.revision+1);
   await page.reload();await page.locator('#project-classroom').waitFor();
   assert.equal(await page.locator('#project-student').inputValue(),'');
   await page.screenshot({path:path.join(root,'3d-project-'+label+'-clean-next-pupil.png')});
   fs.writeFileSync(file,JSON.stringify({...saved,reopenedAfterRestart:true,editableResave:true,newRevision:meta.project.revision},null,2));
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({label,stage,result:'PASS'}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
