// Isolated functional browser checks. No VM, physical machines or visible browser.
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {build} from 'esbuild';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const root=process.cwd(),checks=[],errors=[];
const assert=(v,m)=>{if(!v)throw Error(m);};
await mkdir(path.join(root,'.runtime'),{recursive:true});
await build({entryPoints:['web/laser-geometry.js'],bundle:true,format:'iife',globalName:'LaserGeometry',outfile:'.runtime/laser-geometry-test.js'});
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.ttf':'font/ttf','.webp':'image/webp'};
let online=true;
const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/api/laser/health'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({surface:'student',inbox:online}));return;}
  const route=url.pathname==='/folie.html'?'/laser.html':url.pathname;
  const file=path.resolve(root,'web','.'+route);
  if(!file.startsWith(path.join(root,'web')+path.sep)){res.writeHead(403).end();return;}
  try{const body=await readFile(file);res.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');res.end(body);}catch{res.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000},bypassCSP:true});
page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
const state=()=>page.evaluate(()=>JSON.parse(localStorage.getItem(location.pathname==='/folie.html'?'kloster-folievaerksted-v1':'kloster-laservaerksted-v1')));
const ready=()=>page.waitForFunction(()=>window.ElevhubProjectAdapter&&document.querySelector('#save-status').textContent==='Kladde på denne computer');
async function go(mode='laser'){await page.goto(base+'/'+mode+'.html');await ready();}
async function start(title,mode='replace'){
  await page.locator('.studio-ideas').click();
  const card=page.locator('.studio-project-card').filter({has:page.getByRole('heading',{name:title,exact:true})});
  await card.getByRole('button',{name:mode==='insert'?'Indsæt i tegning':'Start projekt',exact:true}).click();
  if(await page.locator('#starter-dialog').isVisible())await page.locator('#confirm-starter').click();
}
async function open(source){await page.locator('#svg-file').setInputFiles({name:'test.projekt.svg',mimeType:'image/svg+xml',buffer:Buffer.from(source)});await page.waitForFunction(()=>document.querySelector('#notice').textContent.startsWith('Projekt åbnet'));}
async function exportProject(){return page.evaluate(async()=>await (await window.ElevhubProjectAdapter.export()).text());}
async function download(selector){const pending=page.waitForEvent('download');await page.locator(selector).click();return readFile(await (await pending).path(),'utf8');}
async function check(name,fn){await fn();checks.push(name);console.log('PASS '+name);}
try{
  await go();
  await check('Project catalogue has real SVG previews and mode-specific starters',async()=>{
    await page.locator('.studio-ideas').click();
    assert(await page.locator('.studio-project-card').count()>=8,'laser catalogue incomplete');
    assert(await page.locator('.studio-project-card svg').first().evaluate(n=>n instanceof SVGSVGElement),'preview is not an SVG element');
    assert(await page.locator('.studio-project-card svg path').count()>10,'preview geometry absent');
    await page.locator('.studio-close').click();
  });
  await check('Starting and inserting projects is undoable, unique and retains guidance',async()=>{
    const before=await state();await start('Navneskilt');const a=await state();
    assert(a.learning.id==='nameplate'&&a.items.some(o=>o.text),'starter not editable');
    await page.locator('#undo').click();assert(JSON.stringify((await state()).items)===JSON.stringify(before.items),'replacement lost original');
    assert(await page.locator('.studio-guidance').isHidden(),'stale guidance survived undo');
    await page.locator('#redo').click();await start('Navneskilt','insert');const b=await state();
    assert(b.items.length===a.items.length*2,'insert replaced work');assert(new Set(b.items.map(o=>o.id)).size===b.items.length,'duplicate ids');
    await page.locator('#undo').click();assert((await state()).items.length===a.items.length,'insert undo failed');
  });
  let saved;
  await check('Guidance, reflection and helper relationships survive save/open',async()=>{
    await page.locator('.studio-guidance textarea').fill('Jeg vil gøre navnet lettere at læse.');
    await page.locator('.studio-guidance-help').click();
    assert(!(await page.locator('.studio-guidance-steps').evaluate(n=>n.open)),'help did not hide');
    saved=await exportProject();assert(saved.includes('data-learning='),'learning not archived');
    await page.locator('#new-button').click();await page.locator('#confirm-new').click();
    assert(await page.locator('.studio-guidance').isHidden(),'blank project retains old help');
    await open(saved);const s=await state();assert(s.learning.reflection.includes('læse'),'reflection lost');
    assert(s.items.some(o=>o.id===s.learning.frameId),'frame ids lost');
    assert(await page.locator('.studio-guidance textarea').inputValue()===s.learning.reflection,'reflection not displayed');
    await page.locator('.studio-guidance-help').click();
    assert(await page.locator('.studio-guidance-steps').evaluate(n=>n.open),'help did not reopen');
  });
  await check('Early tools and everyday copy shortcuts work before advanced level',async()=>{
    const s=await state(),item=s.items.find(o=>o.text);
    await page.locator(`#object-list [data-id="${item.id}"]`).click();
    await page.locator('[data-level="1"]').click();
    assert(await page.locator('#group-objects').isVisible(),'grouping hidden at stage2');
    assert(await page.locator('#rotate-right').isVisible(),'rotation hidden at stage2');
    await page.locator('[data-level="0"]').click();assert(await page.locator('#center-on-page').isVisible(),'centring hidden at stage1');
    await page.locator(`#object-list [data-id="${item.id}"]`).click();await page.keyboard.press('Control+c');await page.keyboard.press('Control+v');
    assert((await state()).items.length===s.items.length+1,'copy paste requires highest level');await page.locator('#undo').click();
    await page.locator('[data-level="2"]').click();assert(await page.locator('[data-tool="nodes"]').isVisible(),'cannot edit created curves');
  });
  await check('Rotated and mirrored text remains editable after saving and reopening',async()=>{
    await start('Navneskilt');await page.locator('[data-level="1"]').click();
    let s=await state(),text=s.items.find(o=>o.text);
    await page.locator(`#object-list [data-id="${text.id}"]`).click();await page.locator('#rotate-right').click();await page.locator('#flip-x').click();
    await page.locator('#text-value').fill('ÅSE');await page.locator('#text-value').press('Tab');
    s=await state();text=s.items.find(o=>o.id===text.id);assert(text.text.value==='ÅSE'&&text.text.matrix?.length===4,'transform destroyed editable text');
    const matrix=[...text.text.matrix],source=await exportProject();await open(source);
    await page.locator(`#object-list [data-id="${text.id}"]`).click();await page.locator('#text-value').fill('ØRN');await page.locator('#text-value').press('Tab');
    await page.locator('#object-width').fill('20');await page.locator('#object-width').press('Tab');
    const after=(await state()).items.find(o=>o.id===text.id);assert(JSON.stringify(after.text.matrix)===JSON.stringify(matrix),'orientation lost after reopening');
    await page.addScriptTag({path:path.join(root,'.runtime/laser-geometry-test.js')});
    const error=await page.evaluate(async o=>{const G=LaserGeometry;await G.loadFont();const actual=G.bounds(o.d),expected=G.bounds(G.transform(G.textPath(o.text.value,o.text.size),[...o.text.matrix,0,0]));return Math.max(Math.abs(actual.width-expected.width),Math.abs(actual.height-expected.height));},after);
    assert(error<.001,'text outline no longer matches transformed text');
  });
  await check('Project-specific feedback identifies overflow and excludes learning data from production',async()=>{
    await start('Navneskilt');const s=await state(),text=s.items.find(o=>o.text);
    await page.locator(`#object-list [data-id="${text.id}"]`).click();await page.locator('#text-size').fill('60');await page.locator('#text-size').press('Tab');
    assert(await page.locator('#export-button').isDisabled(),'overflow can export');
    const issue=page.locator('#drawing-issues button').filter({hasText:'går ud over projektets kant'});assert(await issue.count()>0,'no actionable frame warning');await issue.first().click();
    assert(await page.locator('#text-value').inputValue()===text.text.value,'feedback did not select text');
    await page.locator('#undo').click();const machine=await download('#export-button');
    assert(!machine.includes('data-learning')&&!machine.includes('Jeg vil'),'guidance leaked into production');
  });
  await check('Connection retry recovers without losing a drawing',async()=>{
    online=false;await page.locator('#retry-connection').click();
    await page.waitForFunction(()=>!document.querySelector('#retry-connection').disabled);
    assert(await page.locator('#send-button').isDisabled(),'offline submission enabled');
    assert((await page.locator('#connection-message').textContent()).includes('Ingen forbindelse'),'offline explanation missing');
    const before=await state();online=true;await page.locator('#retry-connection').click();await page.waitForFunction(()=>!document.querySelector('#retry-connection').disabled);assert(await page.locator('#send-button').isEnabled(),'retry failed');
    assert(JSON.stringify(await state())===JSON.stringify(before),'retry altered design');
  });
  await check('Vinyl guidance, mirror/edit and machine export use the correct mode',async()=>{
    await go('folie');await start('Mærke til drikkedunk');assert(await page.locator('.studio-guidance').isVisible(),'vinyl guidance missing');
    let s=await state();assert(s.items.every(o=>o.op==='cut'),'vinyl contains engraving');
    const text=s.items.find(o=>o.text);await page.locator('.vinyl-setup summary').click();await page.locator('#vinyl-mirror').click();await page.locator(`#object-list [data-id="${text.id}"]`).click();
    await page.locator('#text-value').fill('MIA');await page.locator('#text-value').press('Tab');s=await state();assert(s.items.find(o=>o.id===text.id).text.value==='MIA','mirror made text uneditable');
    const source=await exportProject();await open(source);assert((await state()).learning.id==='bottle-label','vinyl project mode not preserved');
    const machine=await download('#export-button');assert(!machine.includes('data-learning')&&!machine.includes('data-text')&&!machine.includes('engrave'),'vinyl output contains project internals');
    assert(await page.locator('#connection-state').isHidden(),'vinyl shows laser connection');
  });
  await check('Save and start downloads current project before replacement',async()=>{
    const before=await state();await page.locator('.studio-ideas').click();
    const card=page.locator('.studio-project-card').filter({has:page.getByRole('heading',{name:'Hold- eller klublogo',exact:true})});await card.getByRole('button',{name:'Start projekt',exact:true}).click();
    const saved=await download('#save-and-starter');assert(saved.includes(before.name),'saved wrong project');assert((await state()).learning.id==='club-logo','new project not started');
  });
  assert(!errors.length,'Browser errors: '+errors.join('; '));
  await mkdir(path.join(root,'output','svg-progression'),{recursive:true});await writeFile(path.join(root,'output','svg-progression','checks.json'),JSON.stringify({checks,errors},null,2));
  console.log(JSON.stringify({passed:checks.length,errors}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
