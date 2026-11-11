import {createRequire} from 'node:module';
import {readFile,writeFile,mkdtemp,mkdir} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import net from 'node:net';
import path from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const checks=[],assert=(value,message)=>{if(!value)throw Error(message);};
await mkdir('.runtime',{recursive:true});
const dataFolder=await mkdtemp(path.resolve('.runtime/laser-image-inbox-'));
await writeFile(path.join(dataFolder,'laererkode.txt'),'test-only-images');
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
const studentPort=await freePort(),teacherPort=await freePort();
const server=spawn('python',['-m','app.laser_server','--port',String(studentPort),'--teacher-port',String(teacherPort),'--data',dataFolder],{cwd:process.cwd(),windowsHide:true,stdio:['ignore','pipe','pipe']});
let serverOutput='';server.stderr.on('data',b=>serverOutput+=b);server.stdout.on('data',()=>{});
const base=`http://127.0.0.1:${studentPort}`,teacherBase=`http://127.0.0.1:${teacherPort}`;
const browser=await chromium.launch({headless:true,channel:'chrome'});
try{
  for(let i=0;i<40;i++){try{if((await fetch(base+'/api/laser/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  const student=await browser.newPage({viewport:{width:1440,height:1000}}),teacher=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  for(const page of [student,teacher]){page.setDefaultTimeout(20000);page.on('pageerror',e=>errors.push(e.message));}
  const state=()=>student.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')));
  await student.goto(base+'/laser.html');await student.waitForFunction(()=>document.querySelector('#add-text').onclick!==null||document.querySelector('#save-status').textContent==='Gemt i denne browser');
  await student.evaluate(()=>{localStorage.setItem('kloster-laservaerksted-v1',JSON.stringify({version:1,name:'Billedtest',width:180,height:120,items:[{id:'cut-frame',name:'Skærekant',op:'cut',fillRule:'nonzero',d:'M10 10H170V110H10Z'}]}));localStorage.setItem('kloster-laservaerksted-v1-level','3');});await student.reload();
  const fixture=await student.evaluate(()=>{const c=document.createElement('canvas');c.width=256;c.height=192;const x=c.getContext('2d');x.fillStyle='black';x.beginPath();x.arc(80,96,55,0,Math.PI*2);x.fill();x.globalCompositeOperation='destination-out';x.beginPath();x.arc(80,96,22,0,Math.PI*2);x.fill();x.globalCompositeOperation='source-over';x.fillStyle='#bbbbbb';x.fillRect(170,60,60,75);return c.toDataURL('image/png').split(',')[1];});
  await writeFile('.runtime/laser-image-fixture.png',Buffer.from(fixture,'base64'));
  async function check(name,fn){try{await fn();checks.push({name,status:'PASS'});console.log('PASS',name);}catch(e){checks.push({name,status:'FAIL',message:e.message});console.log('FAIL',name,e.message);await student.screenshot({path:'.runtime/laser-image-failure.png',fullPage:true});}}
  async function readyTrace(){await student.waitForFunction(()=>document.querySelector('#trace-status').textContent.startsWith('Konturerne er klar')&&!document.querySelector('#insert-trace').disabled);}
  async function select(id){await student.locator(`#object-list button[data-id="${id}"]`).click();}
  async function download(page,selector,file){const event=page.waitForEvent('download');await page.locator(selector).click();await (await event).saveAs(file);return readFile(file,'utf8');}
  await check('blank image cannot be inserted without contours',async()=>{
    const blank=await student.evaluate(()=>{const c=document.createElement('canvas');c.width=c.height=40;return c.toDataURL('image/png').split(',')[1];});
    await student.locator('#image-file').setInputFiles({name:'blank.png',mimeType:'image/png',buffer:Buffer.from(blank,'base64')});
    await student.waitForFunction(()=>document.querySelector('#trace-status').textContent.startsWith('Der blev ikke fundet konturer'));
    assert(await student.locator('#insert-trace').isDisabled(),'blank image can be inserted');assert(await student.locator('#insert-original').count()===0,'original image insertion still exists');
    await student.getByRole('button',{name:'Annullér',exact:true}).click();assert((await state()).items.length===1,'cancel changed the drawing');
  });
  await check('PNG import and worker tracing with production CSP; threshold and holes',async()=>{
    await student.locator('#image-file').setInputFiles(path.resolve('.runtime/laser-image-fixture.png'));await readyTrace();
    const first=await student.locator('#image-trace-preview path').getAttribute('d');assert((first.match(/M/g)||[]).length===2,'hole or transparent background traced incorrectly');
    await student.locator('#trace-threshold').fill('220');await student.locator('#trace-threshold').dispatchEvent('input');await readyTrace();
    const second=await student.locator('#image-trace-preview path').getAttribute('d');assert((second.match(/M/g)||[]).length===3,'threshold did not include gray rectangle');
    await student.screenshot({path:'.runtime/laser-trace-dialog.png',fullPage:true});await student.locator('#insert-trace').click();
    const s=await state();assert(s.items.length===2&&s.items[1].image.trace===second&&s.items[1].image.show==='trace','trace not inserted');assert(s.items[1].op==='cut','traced figure must start as cutting');assert(await student.locator('#items image').count()===0,'student sees the original bitmap');
  });
  let imageId=(await state()).items.find(o=>o.image)?.id;
  await check('image placement, rotation, mirror, undo and SVG roundtrip',async()=>{
    assert(imageId,'missing imported image');await select(imageId);await student.locator('#object-width').fill('60');await student.locator('#object-width').press('Tab');await student.locator('#rotation-angle').fill('30');await student.locator('#rotate-exact').click();await student.locator('#flip-x').click();
    const before=JSON.stringify((await state()).items[1]);await student.locator('#undo').click();await student.locator('#redo').click();assert(JSON.stringify((await state()).items[1])===before,'image undo/redo failed');
    const svg=await download(student,'#export-button','.runtime/laser-image-student.svg');assert(svg.includes('data:image/png;base64,')&&svg.includes('data-image="1"'),'SVG lost original image');
    await student.locator('#svg-file').setInputFiles(path.resolve('.runtime/laser-image-student.svg'));await student.waitForFunction(()=>document.querySelector('#notice').textContent.includes('SVG åbnet'));
    const after=(await state()).items.find(o=>o.image);imageId=after.id;assert(after.image.trace===JSON.parse(before).image.trace&&after.image.src===JSON.parse(before).image.src,'SVG lost source or tracing');
    await student.reload();assert((await state()).items.find(o=>o.image).image.trace===after.image.trace,'browser restore lost tracing');
  });
  await check('older image display settings reopen as contours without losing the source',async()=>{
    const before=(await state()).items.find(o=>o.image);
    await student.evaluate(()=>{const key='kloster-laservaerksted-v1',s=JSON.parse(localStorage.getItem(key));s.items.find(o=>o.image).image.show='image';localStorage.setItem(key,JSON.stringify(s));});
    await student.reload();assert((await state()).items.find(o=>o.image).image.show==='trace','legacy browser draft stayed in bitmap mode');assert(await student.locator('#items image').count()===0,'legacy bitmap shown to student');
    const legacy=(await readFile('.runtime/laser-image-student.svg','utf8')).replaceAll('data-display="trace"','data-display="image"');
    await student.locator('#svg-file').setInputFiles({name:'older-drawing.svg',mimeType:'image/svg+xml',buffer:Buffer.from(legacy)});await student.waitForFunction(()=>document.querySelector('#notice').textContent.includes('SVG åbnet'));
    const after=(await state()).items.find(o=>o.image);imageId=after.id;assert(after.image.show==='trace'&&after.image.src===before.image.src&&after.image.trace===before.image.trace,'legacy SVG did not retain both representations');
  });
  await check('image cannot be warped with nodes; conversion is editable and undoable',async()=>{
    await select(imageId);await student.locator('[data-tool="nodes"]').click();assert(await student.locator('.node-dot').count()===0,'image frame exposed as nodes');assert(await student.locator('#node-add').isDisabled(),'add point enabled for image');
    await student.locator('#image-to-path').click();assert(!(await state()).items.find(o=>o.id===imageId).image,'conversion retained raster record');assert(await student.locator('.node-dot').count()>4,'converted trace not editable');
    await student.locator('#undo').click();assert(!!(await state()).items.find(o=>o.id===imageId).image,'conversion undo lost original');
  });
  await check('student submission and teacher-only method selection changes real downloads',async()=>{
    await select(imageId);await student.locator('button[data-op="engrave"]').click();
    const submissions=[];student.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/laser/submit'))submissions.push(r.postDataJSON());});
    await student.locator('#send-button').click();
    assert((await student.locator('#send-question').innerText()).includes('kun det, du vil skære eller gravere'),'missing review question');
    assert(await student.locator('#send-preview path').count()===2,'review preview is incomplete');
    assert(await student.locator('#send-preview image').isHidden(),'original bitmap is visible in review');
    await student.locator('#send-dialog button[data-close="send-dialog"]').last().click();
    assert(submissions.length===0,'cancel sent the drawing');
    await student.locator('#send-button').click();await student.locator('#confirm-send').click();assert(submissions.length===0,'empty form submitted');
    await student.locator('#student-name').fill('Billedtest-elev');await student.locator('#student-class').fill('Testklasse');
    await student.screenshot({path:'.runtime/send-review-desktop.png',fullPage:true});
    await student.setViewportSize({width:390,height:844});
    assert(await student.evaluate(()=>{const d=document.querySelector('#send-dialog');return d.scrollWidth<=d.clientWidth+1;}),'review dialog overflows mobile');
    assert(await student.locator('#confirm-send').evaluate(b=>{const r=b.getBoundingClientRect(),d=b.closest('dialog').getBoundingClientRect();return r.top>=d.top&&r.bottom<=d.bottom;}),'confirm button is outside the mobile dialog');
    await student.screenshot({path:'.runtime/send-review-mobile.png',fullPage:true});await student.setViewportSize({width:1440,height:1000});
    const reviewedPaths=await student.locator('#send-preview path').evaluateAll(paths=>paths.map(p=>p.getAttribute('d')));
    await student.route('**/api/laser/submit',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Prøv igen'})}),{times:1});
    await student.locator('#confirm-send').click();await student.waitForFunction(()=>document.querySelector('#send-status').classList.contains('error'));
    assert(await student.locator('#confirm-send').isEnabled(),'failed request cannot retry');
    await student.locator('#confirm-send').click();await student.waitForFunction(()=>document.querySelector('#send-status').textContent.startsWith('Modtaget hos læreren'));
    assert(submissions.length===2&&submissions[0].requestId===submissions[1].requestId&&submissions[0].svg===submissions[1].svg,'retry changed the drawing or request ID');
    const submittedPaths=await student.evaluate(svg=>[...new DOMParser().parseFromString(svg,'image/svg+xml').querySelectorAll('path')].map(p=>p.getAttribute('d')),submissions[1].svg);
    assert(JSON.stringify(reviewedPaths)===JSON.stringify(submittedPaths),'submitted geometry differs from preview');
    await student.locator('#student-project').press('Enter');assert(submissions.length===2,'successful form submitted twice');
    await teacher.goto(teacherBase);await teacher.locator('#teacher-code').fill('test-only-images');await teacher.locator('#login-form button').click();await teacher.locator('#download-svg').waitFor();assert(await teacher.locator('#download-svg').isDisabled(),'teacher exported before choosing a method');
    await teacher.locator('input[value="raster"]').check();await teacher.waitForFunction(()=>!document.querySelector('#download-svg').disabled);
    const raster=await download(teacher,'#download-svg','.runtime/laser-image-raster.svg');assert(raster.includes('<image')&&!raster.includes('data-image="1"'),'raster is not an embedded image');assert((raster.match(/<path /g)||[]).length===1,'raster export also contains traced lines');
    await teacher.locator('.preview-image img').evaluate(image=>image.decode());await teacher.screenshot({path:'.runtime/laser-image-teacher.png',fullPage:true});
    await teacher.locator('input[value="vector"]').check();await teacher.waitForFunction(()=>document.querySelector('input[value="vector"]').checked&&!document.querySelector('input[value="vector"]').disabled);
    const vector=await download(teacher,'#download-svg','.runtime/laser-image-vector.svg');assert(!vector.includes('<image')&&vector.includes('data-operation="engrave-line"'),'vector export includes bitmap or lacks line engraving');assert(vector.includes('data-operation="cut"'),'cutting lost');
    await teacher.reload();await teacher.locator('input[value="vector"]').waitFor();assert(await teacher.locator('input[value="vector"]').isChecked(),'teacher selection not persistent');
    const original=await download(teacher,'#download-source','.runtime/laser-image-source.svg');assert(original.includes('data:image/png;base64,')&&original.includes('data-image="1"'),'teacher original missing image data');
  });
  await check('mobile dialogs and teacher choices fit; no browser errors',async()=>{
    await student.locator('[data-close="send-dialog"]').first().click();await select(imageId);await student.locator('#edit-image-trace').click();await readyTrace();await student.setViewportSize({width:390,height:844});await student.screenshot({path:'.runtime/laser-trace-mobile.png',fullPage:true});
    assert(await student.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'student mobile overflow');await teacher.setViewportSize({width:390,height:844});await teacher.screenshot({path:'.runtime/laser-image-teacher-mobile.png',fullPage:true});assert(await teacher.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'teacher mobile overflow');assert(!errors.length,errors.join('\n'));
  });
}catch(e){checks.push({name:'setup or dependency',status:'FAIL',message:e.stack});}
finally{await browser.close();server.kill();await writeFile('.runtime/laser-image-results.json',JSON.stringify(checks,null,2));await writeFile('.runtime/laser-image-server.log',serverOutput);checks.forEach(c=>console.log(c.status,c.name,c.message||''));process.exitCode=checks.some(c=>c.status==='FAIL')?1:0;}
