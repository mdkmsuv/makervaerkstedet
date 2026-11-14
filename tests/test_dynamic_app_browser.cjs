/* Real HTTP project/session flow; synthetic apps, no school or hardware. */
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
async function main(){
 const child=process.platform==='win32'?spawn('wsl.exe',['-u','root','--cd',root,'--','python3','-B','tests/fixtures/dynamic_app_server.py'],{stdio:['ignore','pipe','pipe']}):spawn('python3',['-B','tests/fixtures/dynamic_app_server.py'],{cwd:root,stdio:['ignore','pipe','pipe']});
 let stderr='';child.stderr.on('data',d=>stderr+=d);
 const ready=new Promise((resolve,reject)=>{let buf='';child.stdout.on('data',d=>{buf+=d;const line=buf.split('\n')[0];try{resolve(JSON.parse(line));}catch{}});child.on('exit',code=>reject(Error('fixture exit '+code+' '+stderr)));});
 const timeout=setTimeout(()=>child.kill(),20000);let browser,urls;
 try{
  urls=await ready;clearTimeout(timeout);browser=await chromium.launch({headless:true});
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(urls.quiz);await page.locator('h1').waitFor();
  assert.equal(await page.locator('#elevhub-project-bar').count(),0);
  assert.equal(await page.locator('dialog[open]').count(),0);
  await page.goto(urls.noter);await page.locator('#project-start-confirm').waitFor();
  await page.locator('#project-classroom').fill('6.A');await page.locator('#project-student').fill('Test');await page.locator('#project-title').fill('Mine noter');
  await page.locator('#project-start-confirm').click();await page.locator('dialog[open]').waitFor({state:'hidden'});
  await page.locator('#notes').fill('Denne note skal bevares');await page.locator('#elevhub-project-save').click();
  await page.waitForFunction(()=>document.querySelector('#elevhub-project-bar')?.textContent.includes('Gemt'));
  const list=await(await page.request.get(urls.shelf+'api/list')).json();assert.equal(list.projects.length,1);assert.equal(list.projects[0].kind,'noter');
  await page.goto(urls.noter+'?elevhub_project='+list.projects[0].id);await page.waitForFunction(()=>document.querySelector('#notes')?.value==='Denne note skal bevares');
  await page.evaluate(()=>window.ElevhubProjectSession.reset());assert.equal(await page.locator('#notes').inputValue(),'');
  await page.goto(urls.shelf+'?pick=noter');await page.getByRole('heading',{name:'Dine projekter · Noter',exact:true}).first().waitFor();
  assert.equal(await page.locator('article.project').count(),1);assert.deepEqual(errors,[]);
  const out=process.env.MKV_BROWSER_REPORT;
  if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'noter-project-shelf.png'),fullPage:true});}
  console.log('PASS: stateless app, new-format naming/save/reopen/reset and typed project picker over real HTTP');
 }finally{clearTimeout(timeout);await browser?.close();if(urls){await fetch(urls.cleanup,{method:'POST'});await new Promise(resolve=>child.once('exit',resolve));}else child.kill();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
