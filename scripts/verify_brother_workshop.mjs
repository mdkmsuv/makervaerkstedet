import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,channel:'chrome'});
const base=process.env.WORKSHOP_BASE||'http://127.0.0.1:8766';
const teacherBase=process.env.WORKSHOP_TEACHER_BASE||'http://127.0.0.1:8767',checks=[];
const assert=(ok,message)=>{if(!ok)throw Error(message);};
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
  const ready=()=>page.waitForFunction(()=>window.ElevhubProjectAdapter&&document.querySelector('#save-status')?.textContent==='Kladde på denne computer');
  const state=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-folievaerksted-v1')));
  async function load(s){await page.evaluate(s=>localStorage.setItem('kloster-folievaerksted-v1',JSON.stringify(s)),s);await page.reload();await ready();}
  async function setLevel(level){const details=page.locator('.vinyl-tool-levels');if(await details.count()&&!await details.getAttribute('open'))await details.locator('summary').click();await page.locator(`[data-level="${level}"]`).click();}
  async function openVinylSetup(){const setup=page.locator('#vinyl-setup');if(await setup.count()&&!await setup.getAttribute('open'))await setup.locator('summary').click();await page.waitForFunction(()=>document.querySelector('#vinyl-setup')?.open===true);}
  async function download(){const event=page.waitForEvent('download');await page.locator('#export-button').click();const file=await event;return readFile(await file.path(),'utf8');}
  async function check(name,fn){await fn();checks.push(name);console.log('PASS',name);}
  await check('Front page offers two student workshops; teacher pages stay separated',async()=>{
    await page.goto(base);assert(await page.locator('#choose-vinyl').getAttribute('href')==='/folie.html','vinyl link');assert(await page.locator('#choose-laser').getAttribute('href')==='/laser.html','laser link');
    assert(await page.locator('a[href*="teacher"]').count()===0,'teacher link on student homepage');
    for(const url of ['/laser-teacher.html','/api/laser/jobs'])assert((await page.request.get(base+url)).status()===404,'student exposes teacher route');
    for(const url of ['/folie.html','/workshop.html'])assert((await page.request.get(teacherBase+url)).status()===404,'teacher exposes student route');
    if(process.env.CAPTURE_WORKSHOP_SCREENSHOTS==='1')await page.screenshot({path:'.runtime/design-workshop-home.png',fullPage:true});
  });
  let laserDraft,starter;
  await check('Existing laser draft and separate vinyl draft survive switching',async()=>{
    await page.locator('#choose-laser').click();await ready();await page.locator('#project-name').fill('Min laserkladde');await page.locator('#project-name').blur();
    laserDraft=await page.evaluate(()=>localStorage.getItem('kloster-laservaerksted-v1'));
    await page.locator('#workshop-home').click();await page.locator('#choose-vinyl').click();await ready();
    starter=await state();assert(starter.items.length===1&&starter.items[0].op==='cut','vinyl starter');assert(starter.width===296&&starter.height===298,'actual cutting area');
    assert(await page.locator('[data-op="engrave"]').isHidden()&&await page.locator('#send-button').isHidden(),'laser-only choices visible');
    await page.locator('#project-name').fill('Foliekladden');await page.locator('#project-name').blur();
    await page.locator('#workshop-home').click();await page.locator('#choose-laser').click();await ready();
    assert(await page.locator('#project-name').inputValue()==='Min laserkladde','lost laser draft');
    assert(await page.locator('[data-op="engrave"]').isVisible(),'lost laser options');
    await page.goto(base+'/folie.html');await ready();assert((await state()).name==='Foliekladden','lost vinyl draft');
    assert(await page.evaluate(()=>localStorage.getItem('kloster-laservaerksted-v1'))===laserDraft,'vinyl changed laser storage');
  });
  await check('Brother SVG has physical units, outlined text and no machine metadata',async()=>{
    const source=await download();await writeFile('.runtime/brother-text.svg',source);
    const result=await page.evaluate(source=>{const root=new DOMParser().parseFromString(source,'image/svg+xml').documentElement;return {width:root.getAttribute('width'),height:root.getAttribute('height'),box:root.getAttribute('viewBox'),paths:root.querySelectorAll('path').length,unsupported:root.querySelectorAll('image,text,g,style,script,metadata').length};},source);
    assert(result.width==='296mm'&&result.height==='298mm'&&result.box==='0 0 296 298','SVG dimensions');
    assert(result.paths===1&&!result.unsupported&&!source.includes('data-operation'),'not plain cut paths');
    await setLevel(1);await page.locator('#svg-file').setInputFiles({name:'Brother.svg',mimeType:'image/svg+xml',buffer:Buffer.from(source)});
    await page.waitForFunction(()=>document.querySelector('#notice').textContent.startsWith('SVG åbnet'));
    assert((await state()).items.every(o=>o.op==='cut'),'SVG reimport left unassigned operations');
  });
  await check('Mirror changes the visible geometry and is undoable',async()=>{
    await openVinylSetup();
    const before=JSON.stringify((await state()).items);
    await page.locator('#vinyl-mirror').click();const after=JSON.stringify((await state()).items);assert(after!==before,'mirror did nothing');
    await page.locator('#undo').click();assert(JSON.stringify((await state()).items)===before,'mirror undo lost geometry');
  });
  await check('Tracing exports holes and transformed contours, without bitmap or frame',async()=>{
    const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=c.height=100;const x=c.getContext('2d');x.fillStyle='black';x.fillRect(10,15,70,65);x.clearRect(30,35,20,20);return c.toDataURL();});
    await load({...starter,name:'Contours',items:[{id:'motif',name:'Tracet motiv',op:'cut',d:'M150 50L150 150L70 150L70 50Z',fillRule:'evenodd',image:{src:png,width:100,height:100,trace:'M10 15H80V80H10Z M30 35H50V55H30Z',show:'trace',threshold:150,smoothing:1}},{id:'omit',name:'Udeladt',op:'skip',d:'M2 2H20V20H2Z'}]});
    assert((await state()).items[1].op==='undecided','old excluded shape automatically became a cut');
    assert(await page.locator('#export-button').isDisabled(),'unreviewed legacy shape can export');
    await page.locator('#object-list button[data-id="omit"]').click();await page.locator('#delete').click();
    const source=await download();await writeFile('.runtime/brother-contours.svg',source);
    const result=await page.evaluate(source=>{const svg=new DOMParser().parseFromString(source,'image/svg+xml').documentElement;document.body.append(svg);svg.style.position='absolute';svg.style.visibility='hidden';const paths=svg.querySelectorAll('path'),p=paths[0],b=p.getBBox();const result={count:paths.length,x:b.x,y:b.y,w:b.width,h:b.height,subpaths:(p.getAttribute('d').match(/m/gi)||[]).length,rule:p.getAttribute('fill-rule'),transform:p.hasAttribute('transform')};svg.remove();return result;},source);
    assert(!source.includes('image')&&!source.includes('base64')&&!result.transform,'bitmap or transform in export');
    assert(result.count===1&&result.subpaths===2&&result.rule==='evenodd','lost hole or included skipped path');
    assert(Math.abs(result.x-86)<.001&&Math.abs(result.y-60)<.001&&Math.abs(result.w-52)<.001&&Math.abs(result.h-70)<.001,'exported frame or incorrect transformed contour');
    assert((await state()).items[0].image.src===png,'export destroyed editable browser source');
    const missing=await state();missing.items[0].image.trace='';await load(missing);
    assert(await page.locator('#export-button').isDisabled(),'untraced image can be exported');
    await page.locator('#preview-button').click();assert((await page.locator('#preview-issues').innerText()).includes('Trace'),'missing trace not explained in preview');await page.locator('[data-close="preview-dialog"]').first().click();
  });
  await check('Mat limits prevent accidental resizing; long mat survives save and reimport',async()=>{
    await load({...starter,items:[{id:'long',name:'Lang figur',op:'cut',d:'M10 350H60V450H10Z'}]});
    await openVinylSetup();
    assert(await page.locator('#export-button').isDisabled(),'out-of-mat export allowed');
    await page.locator('#vinyl-mat').selectOption('long');assert(!(await page.locator('#export-button').isDisabled()),'long mat export blocked');
    const source=await download();assert(source.includes('height="603mm"'),'long mat dimensions');
    await page.reload();await ready();assert((await state()).height===603,'long mat restore failed');
    await page.locator('#svg-file').setInputFiles({name:'Long.svg',mimeType:'image/svg+xml',buffer:Buffer.from(source)});await page.waitForFunction(()=>document.querySelector('#notice').textContent.startsWith('SVG åbnet'));
    await openVinylSetup();assert((await state()).mat==='long','long SVG reimport failed');
    await page.locator('#vinyl-mat').selectOption('standard');assert(await page.locator('#export-button').isDisabled(),'shrinking mat silently resized design');
  });
  await check('Laser SVG becomes cut paths, free drawing is cut-only and four levels remain available',async()=>{
    const svg='<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60" data-laservaerksted="1"><path d="M10 10H50V40H10Z" data-operation="engrave-fill"/><path d="M60 10L90 40" data-operation="engrave-line"/></svg>';
    await page.locator('#svg-file').setInputFiles({name:'Laser.svg',mimeType:'image/svg+xml',buffer:Buffer.from(svg)});await page.waitForFunction(()=>document.querySelector('#items').children.length===2);
    assert((await state()).items.every(o=>o.op==='cut'),'engraving survived vinyl import');
    await page.locator('#vinyl-tool-tab-draw').click();await page.locator('[data-tool="polyline"]').click();
    for(const [x,y] of [[90,90],[140,95]]){const p=await page.evaluate(({x,y})=>{const p=new DOMPoint(x,y).matrixTransform(document.querySelector('#drawing').getScreenCTM());return {x:p.x,y:p.y};},{x,y});await page.mouse.click(p.x,p.y);}
    await page.locator('#point-finish').click();assert((await state()).items.at(-1).op==='cut','point stroke became engraving');
    await page.locator('#preview-button').click();assert((await page.locator('#preview-issues').innerText()).includes('Åbne streger'),'open-cut explanation absent');await page.locator('[data-close="preview-dialog"]').first().click();
    for(const level of ['0','1','2','3']){await setLevel(level);assert(await page.locator('[data-op="engrave"]').isHidden(),'level exposes engraving');assert(await page.locator('.board-settings').isHidden(),'level exposes custom laser board');}
    await page.locator('#vinyl-tool-tab-draw').click();
    assert(await page.locator('[data-tool="nodes"]').isVisible(),'superuser tools missing');
    if(process.env.CAPTURE_WORKSHOP_SCREENSHOTS==='1')await page.screenshot({path:'.runtime/brother-editor-desktop.png',fullPage:true});
  });
  await check('Desktop homepage, narrow editor and production CSP have no runtime errors',async()=>{
    await page.setViewportSize({width:1024,height:844});await page.goto(base);if(process.env.CAPTURE_WORKSHOP_SCREENSHOTS==='1')await page.screenshot({path:'.runtime/design-workshop-mobile.png',fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'home overflows supported desktop width');
    await page.locator('#choose-vinyl').click();await ready();await page.setViewportSize({width:390,height:844});await setLevel(0);if(process.env.CAPTURE_WORKSHOP_SCREENSHOTS==='1')await page.screenshot({path:'.runtime/brother-editor-mobile.png',fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'editor overflows mobile');assert(!errors.length,errors.join('\n'));
  });
  await writeFile('.runtime/brother-workshop-results.json',JSON.stringify(checks,null,2));
}finally{await browser.close();}
