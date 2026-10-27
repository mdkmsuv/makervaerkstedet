import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,channel:'chrome'}),checks=[];
const assert=(v,msg)=>{if(!v)throw Error(msg);};
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},bypassCSP:true});page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto((process.env.WORKSHOP_BASE||'http://127.0.0.1:8766')+'/laser.html');
  const item=(id,d,extra={})=>({id,name:id,d,op:'cut',fillRule:'nonzero',...extra});
  const state=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')));
  async function load(items){
    await page.evaluate(items=>{localStorage.setItem('kloster-laservaerksted-v1',JSON.stringify({version:1,name:'Kurvetest',width:180,height:120,items}));localStorage.setItem('kloster-laservaerksted-v1-level','3');},items);
    await page.reload();await page.waitForFunction(()=>document.querySelector('[data-level="3"]').getAttribute('aria-pressed')==='true');
    await page.addScriptTag({path:'D:/Bambuslicer made easy/.runtime/laser-geometry-test.js'});
  }
  async function pos(x,y){return page.evaluate(({x,y})=>{const p=new DOMPoint(x,y).matrixTransform(document.querySelector('#drawing').getScreenCTM());return {x:p.x,y:p.y};},{x,y});}
  async function click(x,y,options){const p=await pos(x,y);await page.mouse.click(p.x,p.y,options);}
  async function drag(from,to){const a=await pos(...from),b=await pos(...to);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:7});await page.mouse.up();}
  async function select(id,shift=false){await page.locator(`#object-list button[data-id="${id}"]`).click(shift?{modifiers:['Shift']}:{});}
  async function inspect(){return page.evaluate(()=>{const s=JSON.parse(localStorage.getItem('kloster-laservaerksted-v1'));return s.items.map(o=>({...o,box:LaserGeometry.bounds(o.d),nodes:LaserGeometry.nodes(o.d),closed:LaserGeometry.closed(o.d)}));});}
  async function check(name,fn){try{await fn();checks.push({name,status:'PASS'});}catch(e){checks.push({name,status:'FAIL',message:e.message});}}
  await check('curve math: bend keeps endpoints; insert preserves existing curve',async()=>{
    await load([item('Streg','M20 30L80 30')]);
    const result=await page.evaluate(()=>{
      const G=LaserGeometry,d='M20 30L80 30',hit=G.nearestCurve(d,50,30),curved=G.bendCurve(d,hit,50,50),split=G.insertCurveNode(curved,G.nearestCurve(curved,50,50));
      const a=G.path(curved),b=G.path(split.d);let error=0;for(let i=0;i<=20;i++){const p=a.children[0].getPointAt(a.children[0].length*i/20);error=Math.max(error,b.children[0].getNearestPoint(p).getDistance(p));}a.remove();b.remove();
      return {curved,points:G.nodes(curved),split:G.nodes(split.d),error,distance:G.nearestCurve(curved,50,50).distance};
    });
    assert(result.points[0].x===20&&result.points[0].y===30&&result.points[1].x===80&&result.points[1].y===30,'bend moved endpoints');assert(result.distance<.001,'grabbed point not moved to pointer');assert(result.split.length===3&&result.error<.001,'inserting point changed curve');
  });
  await check('draw curved pen path with click-drag and retain SVG cubic segments',async()=>{
    await load([item('Reference','M130 80H145V95H130Z')]);await page.locator('[data-tool="polyline"]').click();
    await click(20,45);await drag([60,45],[60,20]);await click(100,45);await page.keyboard.press('Enter');
    let s=await state();assert(s.items.at(-1).d.includes('C')&&s.items.at(-1).op==='engrave-line','pen curve not created');
    const before=s.items.at(-1).d;await page.locator('#undo').click();assert((await state()).items.length===1,'pen undo');await page.locator('#redo').click();assert((await state()).items.at(-1).d===before,'pen redo');
    const promise=page.waitForEvent('download');await page.locator('#export-button').click();await (await promise).saveAs('.runtime/laser-curves.svg');const svg=await readFile('.runtime/laser-curves.svg','utf8');assert(svg.includes('C')&&svg.includes('width="180mm"'),'SVG lost curves or mm');
    await page.locator('#svg-file').setInputFiles('D:/Bambuslicer made easy/.runtime/laser-curves.svg');await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('SVG åbnet'));const objects=await inspect();assert(objects.some(o=>o.name==='Punktstreg'&&o.nodes.some(n=>Math.hypot(n.hin.x-n.x,n.hin.y-n.y)>1)),'curve handles lost on reimport');
  });
  await check('bend existing line, expose handles, smooth/corner and point insertion/deletion',async()=>{
    await load([item('Streg','M20 35L100 35')]);await select('Streg');await page.locator('[data-tool="nodes"]').click();await drag([60,35],[60,55]);
    assert((await inspect())[0].box.height>15,'existing line stayed straight');assert(await page.locator('.curve-handle[data-handle]').count()>=2,'curve handles hidden');
    await page.locator('#curve-straight').click();assert((await inspect())[0].box.height<.001,'straighten did not restore straight line');
    await click(60,35,{clickCount:2});assert((await inspect())[0].nodes.length===3,'double click did not insert point');await page.locator('#node-smooth').click();assert(await page.locator('.curve-handle[data-handle]').count()===2,'smooth did not add handles');
    const h=await page.locator('.curve-handle[data-handle="handleOut"]').getAttribute('cy');assert(Number(h)===35,'unexpected handle position');
    await drag([73.33333,35],[80,50]);let middle=(await inspect())[0].nodes[1];assert(Math.abs(middle.hout.y-50)<.01&&Math.abs(middle.hin.y-20)<.01,'linked handle drag');
    await page.locator('#handle-link').uncheck();await drag([80,50],[85,55]);middle=(await inspect())[0].nodes[1];assert(Math.abs(middle.hin.y-20)<.01&&Math.abs(middle.hout.y-55)<.01,'independent handle changed both sides');
    await page.locator('#node-corner').click();middle=(await inspect())[0].nodes[1];assert(middle.hin.y===middle.y&&middle.hout.y===middle.y,'corner handles remain');
    await page.locator('#node-delete').click();assert((await inspect())[0].nodes.length===2,'delete point removed wrong part');await page.locator('#undo').click();assert((await inspect())[0].nodes.length===3,'node undo did not restore');
  });
  await check('open/close a selected contour and adapt filled engraving',async()=>{
    await load([item('Trekant','M20 20L60 20L40 60Z',{op:'engrave-fill'})]);await select('Trekant');await page.locator('[data-tool="nodes"]').click();await page.locator('#contour-open').click();let o=(await inspect())[0];assert(!o.closed&&o.op==='engrave-line','opening left invalid fill');await page.locator('#contour-close').click();assert((await inspect())[0].closed,'closing failed');
  });
  await check('add-point button, grouped node editing, undo/redo after a click and cancelled drag',async()=>{
    await load([item('A','M20 35L100 35',{groupId:'test-group'}),item('B','M20 80L100 80',{groupId:'test-group'})]);
    await select('A');await page.locator('[data-tool="nodes"]').click();await click(60,35);assert(await page.locator('#object-list button[aria-pressed="true"]').count()===1,'node mode did not select one group member');
    await page.locator('#node-add').click();await click(60,35);assert((await inspect())[0].nodes.length===3,'add-point button failed');
    await page.locator('#undo').click();await click(50,35);assert(await page.locator('#redo').isEnabled(),'click discarded redo');await page.locator('#redo').click();assert((await inspect())[0].nodes.length===3,'redo after selection failed');
    await select('A');const before=(await state()).items[0].d;const a=await pos(60,35),b=await pos(60,55);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:4});
    await page.evaluate(()=>document.querySelector('#drawing').dispatchEvent(new PointerEvent('pointercancel',{pointerId:1,bubbles:true})));await page.mouse.up();assert((await state()).items[0].d===before,'cancelled node drag changed saved path');
    await click(60,35);assert((await inspect())[0].nodes[1].y===35,'cancelled node drag changed active geometry');
  });
  await check('marquee, grouping, independent copied groups and group SVG roundtrip',async()=>{
    await load([item('A','M20 20H40V40H20Z'),item('B','M55 25H75V45H55Z'),item('C','M110 75H130V95H110Z')]);
    await drag([10,10],[90,55]);await page.locator('#group-objects').click();let s=await state();const group=s.items[0].groupId;assert(group&&s.items[1].groupId===group&&!s.items[2].groupId,'grouping changed wrong figures');
    await select('C');await select('A');assert(await page.locator('#object-list button[aria-pressed="true"]').count()===2,'click did not select group');
    await page.locator('#copy-objects').click();await page.locator('#paste-objects').click();s=await state();assert(s.items.length===5&&s.items[3].groupId===s.items[4].groupId&&s.items[3].groupId!==group,'copied group attached to original');
    const roundtrip=await page.evaluate(()=>{const G=LaserGeometry,s=JSON.parse(localStorage.getItem('kloster-laservaerksted-v1'));return G.importSVG(G.exportSVG(s)).items.map(o=>o.groupId||'');});assert(roundtrip.filter(id=>id===group).length===2,'group metadata lost');
    await page.locator('#ungroup-objects').click();s=await state();assert(!s.items[3].groupId&&!s.items[4].groupId&&s.items[0].groupId===group,'ungroup changed original');
  });
  await check('alignment, equal spacing, precise rotation, mirror and display order',async()=>{
    await load([item('A','M20 20H40V35H20Z'),item('B','M65 30H80V45H65Z'),item('C','M120 40H145V55H120Z')]);
    await page.locator('#workspace').focus();await page.keyboard.press('Control+a');await page.locator('[data-align="top"]').click();let objects=await inspect();assert(objects.every(o=>Math.abs(o.box.y-20)<.001),'top alignment');
    await page.locator('[data-distribute="x"]').click();objects=await inspect();const gap1=objects[1].box.x-objects[0].box.x-objects[0].box.width,gap2=objects[2].box.x-objects[1].box.x-objects[1].box.width;assert(Math.abs(gap1-gap2)<.001,'unequal gaps');
    await select('A');await page.locator('#flip-x').click();assert(Math.abs((await inspect())[0].nodes[0].x-40)<.001,'horizontal mirror');
    await page.locator('#rotation-angle').fill('90');await page.locator('#rotate-exact').click();objects=await inspect();assert(Math.abs(objects[0].box.width-15)<.001&&Math.abs(objects[0].box.height-20)<.001,'90 degree rotation');
    await page.locator('#bring-front').click();assert((await state()).items.at(-1).id==='A','bring front');await page.locator('#send-back').click();assert((await state()).items[0].id==='A','send back');
  });
  await check('intersection and exclusion preserve expected outlines',async()=>{
    for(const kind of ['intersect','exclude']){
      await load([item('A','M20 20H60V60H20Z'),item('B','M40 20H80V60H40Z')]);await select('A');await select('B',true);await page.locator('#'+kind).click();const result=await inspect();assert(result.length===1&&result[0].closed,'boolean did not create closed shape');assert(Math.abs(result[0].box.width-(kind==='intersect'?20:60))<.001,'boolean bounds incorrect');
    }
  });
  await load([item('Kurve','M25 60C45 10 85 110 115 45')]);await select('Kurve');await page.locator('[data-tool="nodes"]').click();await click(25,60);if(process.env.CAPTURE_WORKSHOP_SCREENSHOTS==='1')await page.screenshot({path:'.runtime/laser-superuser-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.locator('#node-toolbar').scrollIntoViewIfNeeded();if(process.env.CAPTURE_WORKSHOP_SCREENSHOTS==='1')await page.screenshot({path:'.runtime/laser-superuser-mobile.png',fullPage:true});
  await check('mobile layout and browser errors',async()=>{assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile overflow');assert(!errors.length,errors.join('\n'));});
}finally{await browser.close();await writeFile('.runtime/laser-superuser-results.json',JSON.stringify(checks,null,2));checks.forEach(c=>console.log(c.status,c.name,c.message||''));process.exitCode=checks.some(c=>c.status==='FAIL')?1:0;}
