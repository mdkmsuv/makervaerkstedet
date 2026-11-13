// Browser test of the INSTALLED laser workshop (127.0.0.1:8795) on a test machine.
// Saves a synthetic project, reopens it in a fresh browser, resaves it and
// checks that the next pupil gets a clean start.
// Usage: node installed-laser.cjs <label> <output-folder>
// Playwright resolves from produkt/node_modules.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:8795',label=process.argv[2],outputArgument=process.argv[3];
if(!label||!outputArgument){console.error('Brug: node installed-laser.cjs <label> <outputmappe>');process.exit(2);}
const output=path.resolve(outputArgument);
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  let context=await browser.newContext({viewport:{width:1024,height:688}}),page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/laser.html');
  await page.locator('#project-classroom').fill('Testklasse');await page.locator('#project-student').fill('Testelev');
  const title='SYNTHETIC LASER '+label+' '+Date.now();await page.locator('#project-title').fill(title);
  await page.locator('#project-start-confirm').click();await page.locator('[data-add-type=rect]').click();
  await page.locator('#saveProjectButton').click();
  await page.waitForFunction(()=>document.querySelector('#statusMessage').textContent.includes('Gemt på denne computer'));
  const list=await(await page.request.get(base+'/_elevhub/projekter/api/list')).json();
  const meta=list.projects.find(p=>p.name===title);assert(meta);assert.match(meta.filename,/\.laser\.json$/);
  const before=JSON.parse(await page.evaluate(()=>ElevhubProjectAdapter.signature())).objects.length;
  assert(before>0);await context.close();
  context=await browser.newContext({viewport:{width:1024,height:688}});page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/laser.html?elevhub_project='+meta.id);
  await page.waitForFunction(()=>window.ElevhubProjectSession?.naming()?.student==='Testelev');
  assert.equal(JSON.parse(await page.evaluate(()=>ElevhubProjectAdapter.signature())).objects.length,before);
  await page.locator('[data-add-type=circle]').click();
  const finish=page.evaluate(()=>ElevhubProjectSession.finish());
  await page.locator('#project-finish-save').click();assert(await finish);
  const saved=await(await page.request.get(base+'/_elevhub/projekter/api/open-info?id='+meta.id)).json();
  assert.equal(saved.project.revision,meta.revision+1);
  await page.reload();await page.locator('#project-classroom').waitFor();
  assert.equal(await page.locator('#project-student').inputValue(),'');
  assert.equal(await page.evaluate(()=>ElevhubProjectAdapter.hasContent()),false);
  await page.screenshot({path:path.join(output,'laser-'+label+'-next-pupil.png')});
  assert.deepEqual(errors,[]);
  const result={label,installedLaserSave:true,freshBrowserDirectReopen:true,editableResave:true,cleanNextPupil:true,projectId:meta.id};
  fs.writeFileSync(path.join(output,'laser-'+label+'.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
