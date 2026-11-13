// Browser test of the INSTALLED foil workshop (127.0.0.1:8796) on a test machine.
// Saves a synthetic project, reopens it in a fresh browser and checks that the
// next pupil gets a clean start.
// Usage: node installed-folie.cjs <label> <output-folder>
// Playwright resolves from produkt/node_modules.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const label=process.argv[2],outputArgument=process.argv[3];
if(!label||!outputArgument){console.error('Brug: node installed-folie.cjs <label> <outputmappe>');process.exit(2);}
const output=path.resolve(outputArgument);
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  let context=await browser.newContext({viewport:{width:1024,height:688}}),page=await context.newPage();
  await page.goto('http://127.0.0.1:8796/folie.html');
  await page.locator('#project-classroom').fill('Testklasse');await page.locator('#project-student').fill('Testelev');
  const title='SYNTHETIC FOLIE '+label+' '+Date.now();await page.locator('#project-title').fill(title);
  await page.locator('#project-start-confirm').click();await page.locator('[data-add="rect"]').click();
  const finish=page.evaluate(()=>ElevhubProjectSession.finish());await page.locator('#project-finish-save').click();assert(await finish);
  assert.equal(await page.locator('#items > *').count(),0);
  const list=await(await page.request.get('http://127.0.0.1:8796/_elevhub/projekter/api/list')).json();
  const meta=list.projects.find(p=>p.name===title);assert(meta);assert.equal(meta.kind,'folie');
  await context.close();context=await browser.newContext({viewport:{width:1024,height:688}});page=await context.newPage();
  await page.goto('http://127.0.0.1:8796/folie.html?elevhub_project='+meta.id);
  await page.waitForFunction(()=>window.ElevhubProjectSession?.naming()?.student==='Testelev',null,{timeout:10000});
  assert.equal(await page.locator('#items > *').count(),1);
  assert.equal(await page.locator('#elevhub-project-dialog').isVisible(),false);
  await page.screenshot({path:path.join(output,'folie-'+label+'-reopened.png')});
  assert(await page.evaluate(()=>ElevhubProjectSession.finish()));
  await page.reload();await page.locator('#project-classroom').waitFor();
  assert.equal(await page.locator('#project-student').inputValue(),'');
  const result={label,installedFolieSave:true,freshBrowserDirectReopen:true,cleanFinish:true,projectId:meta.id};
  fs.writeFileSync(path.join(output,'folie-'+label+'-verified.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
