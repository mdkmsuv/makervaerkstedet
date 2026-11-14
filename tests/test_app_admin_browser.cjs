'use strict';
// Render the actual teacher entry and APIs. Reports are synthetic, never apt/hardware.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..'),canonical=path.resolve(root,'../..');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(canonical,'node_modules/playwright'));
const {AxeBuilder}=require(path.join(canonical,'node_modules/@axe-core/playwright'));
const out=process.env.MKV_BROWSER_REPORT||path.join(root,'output/app-admin-qa');fs.mkdirSync(out,{recursive:true});
async function main(){
 const python=process.env.MKV_QA_PYTHON||path.join(canonical,'.venv/Scripts/python.exe');
 const child=spawn(python,['-B','-u','tests/fixtures/app_admin_server.py'],{cwd:root,stdio:['ignore','pipe','pipe']});let stderr='',urls,browser;child.stderr.on('data',d=>stderr+=d);
 const ready=new Promise((resolve,reject)=>{let buf='';child.stdout.on('data',d=>{buf+=d;try{resolve(JSON.parse(buf.split('\n')[0]));}catch{}});child.once('exit',code=>reject(Error('Fixture '+code+': '+stderr)));});
 const timer=setTimeout(()=>child.kill(),30000);const results={flows:[],errors:[],axe:[]};
 try{
  urls=await ready;clearTimeout(timer);browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'light'});const page=await context.newPage();
  page.on('pageerror',e=>results.errors.push(e.message));
  await page.goto(urls.origin);await page.locator('#password').fill('synthetic-only-code');await page.locator('#login-submit').click();await page.locator('#nav').waitFor({state:'visible'});
  await page.getByRole('link',{name:'Appkatalog',exact:true}).click();await page.locator('[data-catalog-app=noter]').waitFor();
  await page.locator('[name=app-device][value=pc1]').check();await page.locator('[name=app-device][value=pc2]').check();
  const noter=page.locator('[data-catalog-app=noter]');await assert.doesNotReject(()=>noter.getByText('1 af 2 valgte computere har appen.').waitFor());
  assert.equal(await noter.getByRole('button',{name:'Installér på manglende (1)',exact:true}).count(),1);assert.equal(await noter.getByRole('button',{name:'Afinstallér (1)',exact:true}).count(),1);
  await page.screenshot({path:path.join(out,'catalog-mixed.png'),fullPage:true});
  await noter.getByRole('button',{name:'Installér på manglende (1)',exact:true}).click();await page.locator('#apps-confirm').waitFor({state:'visible'});assert.match(await page.locator('#apps-confirm-detail').textContent(),/pc2/);await page.locator('#apps-confirm-submit').click();
  await page.waitForFunction(()=>document.querySelector('#apps-status').textContent.includes('Bestillingen er gemt'));
  assert.match(await noter.textContent(),/1 af 2/);await page.request.post(urls.report,{data:{}});await page.locator('#apps-refresh').click();await page.waitForFunction(()=>document.querySelector('[data-catalog-app=noter]')?.textContent.includes('2 af 2'));
  results.flows.push('mixed selection; missing-only order; queued does not claim installed; observed report updates inventory');
  const axe1=await new AxeBuilder({page}).include('#app-catalog').analyze();results.axe.push({view:'catalog',violations:axe1.violations});
  await page.route('**/api/admin/apps',async route=>{const response=await route.fetch();const value=await response.json();const device=value.devices.find(d=>d.device==='pc2');device.inventory_known=false;device.capable=false;device.installed=[];await route.fulfill({response,json:value});});
  await page.locator('[name=app-device][value=pc1]').uncheck();await page.locator('#apps-refresh').click();await page.waitForFunction(()=>document.querySelector('[data-catalog-app=noter]')?.textContent.includes('Appstatus er ukendt på 1'));
  assert.equal(await page.locator('[data-catalog-app=noter] button').first().isDisabled(),true);await page.screenshot({path:path.join(out,'catalog-unknown.png'),fullPage:true});results.flows.push('unknown app inventory is explicit and action blocked');
  await page.unroute('**/api/admin/apps');await page.locator('#apps-refresh').click();await page.waitForFunction(()=>!document.querySelector('[data-catalog-app=noter]')?.textContent.includes('Appstatus er ukendt'));
  await page.getByRole('link',{name:'Skoleprofil',exact:true}).click();await page.locator('#profile-name').waitFor({state:'visible'});await page.waitForFunction(()=>document.querySelector('#profile-name').value==='Syntetisk skole');assert.equal(await page.locator('#profile-legacy').isVisible(),true);
  await page.locator('#profile-name').fill('QA Eksempelskolen');await page.locator('#profile-colour-text').fill('#e8c534');await page.locator('#profile-font').selectOption('manrope');
  await page.locator('#profile-logo').setInputFiles({name:'logo.png',mimeType:'image/png',buffer:Buffer.from(urls.logo_png,'base64')});
  await page.waitForFunction(()=>!document.querySelector('#profile-preview-logo').hidden);await page.screenshot({path:path.join(out,'profile-preview.png'),fullPage:true});
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/admin/school-profile')&&r.request().method()==='POST');await page.getByRole('button',{name:'Gem skoleprofil',exact:true}).click();const response=await saved;assert.equal(response.status(),200,await response.text());await page.waitForFunction(()=>document.querySelector('#profile-status').textContent.includes('Skoleprofilen er gemt'));
  const appearance=await(await page.request.get(urls.origin+'/api/school-appearance')).json();assert.equal(appearance.school_name,'QA Eksempelskolen');assert.equal(appearance.font,'manrope');assert.ok(appearance.style.logo);
  const axe2=await new AxeBuilder({page}).include('#school-profile-admin').analyze();results.axe.push({view:'profile',violations:axe2.violations});results.flows.push('legacy preserved until explicit save; PNG preview, light accent readability, bundled font, actual saved appearance');
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'profile-mobile.png'),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.emulateMedia({colorScheme:'dark'});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:path.join(out,'profile-dark.png'),fullPage:true});
  assert.deepEqual(results.errors,[]);assert.deepEqual(results.axe.flatMap(x=>x.violations),[]);console.log('PASS: real teacher catalog/profile flows; desktop/mobile/dark; axe');
 }finally{clearTimeout(timer);fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(results,null,2));await browser?.close();if(urls){await fetch(urls.cleanup,{method:'POST'});await new Promise(resolve=>child.once('exit',resolve));}else child.kill();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
