// Real loopback API, synthetic files, private temporary directory, no live UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'elevhub-gallery-'));
const python = process.env.PYTHON || path.join(root, '.venv/Scripts/python.exe');
const server = spawn(python, ['-u', path.join(__dirname, 'gallery_fixture.py'), temporary], {windowsHide:true});
let browser;
async function main() {
 const url = await new Promise((resolve,reject)=>{
  let output='',errors='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+errors)),30000);
  server.stdout.on('data',data=>{output+=data;const line=output.split('\n')[0];try{const value=JSON.parse(line);clearTimeout(timer);resolve(value.url);}catch{}});
  server.stderr.on('data',data=>errors+=data);
  server.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture server exited '+code+': '+errors));});
 });
 browser = await chromium.launch({headless:true,channel:'chrome'});
 const page = await browser.newPage({viewport:{width:1366,height:900}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 await page.locator('.category[data-kind=laser] .project').first().waitFor();
 assert.equal(await page.locator('.category').count(),8);
 const laser=page.locator('.category[data-kind=laser]');
 assert.equal(await laser.locator('.project').count(),4);
 await laser.getByRole('button',{name:'Vis alle 7 i Laser',exact:true}).click();
 assert.equal(await laser.locator('.project').count(),7);
 await laser.getByRole('button',{name:'Vis færre i Laser',exact:true}).click();
 assert.equal(await laser.locator('.project').count(),4);
 const first=laser.locator('.project').first();
 await page.waitForFunction(()=>document.querySelector('.category[data-kind=laser] .project img')?.naturalWidth>0);
 await page.locator('.category[data-kind=andet]').scrollIntoViewIfNeeded();
 await page.waitForFunction(()=>document.querySelectorAll('.category[data-kind=andet] img').length===0);
 assert.match(await page.locator('.project[data-id="'+ 'c'.repeat(32)+'"] h3').innerText(),/<script>/);
 assert.equal(await page.locator('.project[data-id="'+ 'c'.repeat(32)+'"] script').count(),0);
 assert.match(await page.locator('.project-expiry.soon').innerText(),/1 dag tilbage/);
 await first.locator('summary').click();
 const downloadPromise=page.waitForEvent('download');await first.getByRole('link',{name:'Hent fil',exact:true}).click();
 const download=await downloadPromise;assert.match(download.suggestedFilename(),/skilt/);
 await first.getByRole('button',{name:'Tidligere versioner',exact:true}).click();
 await page.locator('#versions .version').waitFor();assert.match(await page.locator('#versions').innerText(),/Version 1/);
 await page.locator('#close-history').click();
 await page.locator('#search').fill('gruppe 3');assert.equal(await page.locator('.category').count(),1);assert.equal(await page.locator('.project').count(),7);
 await page.locator('#search').fill('findes ikke');assert(await page.locator('#empty-projects').isVisible());
 await page.locator('#clear-search').click();
 await page.locator('#kind').selectOption('folie');assert.equal(await page.locator('.project').count(),1);
 await page.getByRole('button',{name:'Åbn i Folieværkstedet',exact:true}).click();
 await page.waitForURL('**/open?**');assert.equal(new URL(page.url()).searchParams.get('id'),'a'.repeat(32));
 await page.goto(url);
 await page.locator('#show-upload').click();
 await page.locator('#folder').fill('Test · Gruppe 7');await page.locator('#name').fill('Nyt SVG-projekt');
 await page.locator('#file').setInputFiles({name:'nyt.svg',mimeType:'image/svg+xml',buffer:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 50"><circle cx="25" cy="25" r="20"/></svg>')});
 await page.locator('#upload-submit').click();await page.locator('#upload').waitFor({state:'hidden'});
 await page.locator('#search').fill('Nyt SVG');await page.locator('.project img').waitFor({state:'visible'});
 assert.equal(await page.locator('.project').count(),1);assert.match(await page.locator('.project-expiry').innerText(),/Slettes/);
 await page.locator('#search').fill('');
 const output=path.join(root,'output/project-library-20261009/verification');fs.mkdirSync(output,{recursive:true});
 for(const width of [1366,1024,768,390]){
  await page.setViewportSize({width,height:900});
  await page.waitForFunction(w=>document.documentElement.clientWidth===w,width);
  // The resize handler rebuilds the group row after a short debounce.
  await page.waitForTimeout(200);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'horizontal overflow at '+width);
  const cards=await page.locator('.project').evaluateAll(cards=>cards.map(c=>({width:c.clientWidth,scroll:c.scrollWidth})));
  assert(cards.every(c=>c.scroll<=c.width),'card overflow at '+width);
  if(width>=1024)await page.screenshot({path:path.join(output,'gallery-'+width+'.png'),fullPage:true});
 }
 await page.setViewportSize({width:680,height:720});
 for(const kind of ['laser','folie','scratch','cadoodle','microbit','kredsloeb','datatraener']){
  await page.goto(url+'/_elevhub/projekter/?pick='+kind);
  await page.locator('.project').first().waitFor();
  assert.deepEqual(await page.locator('.category').evaluateAll(items=>items.map(item=>item.dataset.kind)),[kind]);
  assert(await page.locator('#kind').isDisabled());assert(await page.locator('#kind').isHidden());
  assert.match(await page.locator('.results-heading h2').innerText(),/Dine projekter/);
  assert.equal(await page.locator('link[rel=stylesheet]').getAttribute('href'),'projekter.css');
  await page.locator('#search').fill('findes ikke');assert(await page.locator('#empty-projects').isVisible());
  await page.getByRole('button',{name:'Ryd søgning',exact:true}).click();
  assert.deepEqual(await page.locator('.category').evaluateAll(items=>items.map(item=>item.dataset.kind)),[kind]);
  // Scope remains fixed even if a stale caller modifies the hidden selector.
  await page.locator('#kind').evaluate(select=>{select.value='';select.dispatchEvent(new Event('change'));});
  assert.deepEqual(await page.locator('.category').evaluateAll(items=>items.map(item=>item.dataset.kind)),[kind]);
  await page.getByRole('button',{name:'Opdater',exact:true}).click();
  await page.evaluate(()=>window.addEventListener('message',event=>{if(event.data.type==='elevhub-open-project')window.picked=event.data;}));
  await page.getByRole('button',{name:'Åbn her',exact:true}).first().click();
  await page.waitForFunction(()=>window.picked?.revision===1);
  if(kind==='laser')await page.screenshot({path:path.join(output,'laser-picker.png')});
 }
 await page.goto(url+'/_elevhub/projekter/?workshop=cadoodle');
 await page.locator('.project').first().waitFor();
 assert.deepEqual(await page.locator('.category').evaluateAll(items=>items.map(item=>item.dataset.kind)),['cadoodle']);
 assert.equal(await page.locator('body.picker').count(),0);
 await page.getByRole('button',{name:'Åbn i CaDoodle',exact:true}).click();
 await page.waitForURL('**/open?**');assert.equal(new URL(page.url()).searchParams.get('id'),'e'.repeat(32));
 // Older hosts can return an unfiltered list; the picker must still stay scoped.
 await page.route('**/api/list?workshop=folie',async route=>{
  const response=await route.fetch({url:url+'/_elevhub/projekter/api/list?workshop=laser'});
  await route.fulfill({response});
 });
 await page.goto(url+'/_elevhub/projekter/?pick=folie');
 await page.getByText('Ingen gemte projekter i Folie endnu',{exact:true}).waitFor();
 assert.equal(await page.locator('.project').count(),0);
 assert(await page.locator('#clear-search').isHidden());
 assert.doesNotMatch(await page.locator('#empty-text').innerText(),/andet værksted|Gem en fil/);
 await page.unroute('**/api/list?workshop=folie');
 await page.goto(url+'/_elevhub/projekter/?pick=unknown');
 await page.getByRole('status').filter({hasText:'Værkstedet kunne ikke genkendes'}).waitFor();
 assert.equal(await page.locator('.project').count(),0);
 assert.deepEqual(errors,[]);
 console.log('PASS: gallery operations, seven scoped workshop pickers with shared CSS, clear-search and selector invariants, native 3D scope, invalid scope, four responsive widths.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
 if(browser)await browser.close();
 server.kill();await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
 // Only remove the freshly-created, verified test root after its server exits.
 const resolved=path.resolve(temporary),tempRoot=path.resolve(os.tmpdir());
 if(path.dirname(resolved)===tempRoot&&path.basename(resolved).startsWith('elevhub-gallery-'))fs.rmSync(resolved,{recursive:true,force:true});
});
