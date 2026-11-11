import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,channel:'chrome'}),checks=[];
const assert=(value,message)=>{if(!value)throw Error(message);};
const near=(actual,expected,epsilon=.055)=>assert(Math.abs(actual-expected)<epsilon,`${actual} != ${expected}`);
const base='http://127.0.0.1:8766';
try{
  const math=await browser.newPage({bypassCSP:true});await math.goto(base+'/laser.html');
  await math.addScriptTag({path:process.cwd()+'/.runtime/laser-geometry-test.js'});
  await math.addScriptTag({path:process.cwd()+'/.runtime/laser-offset-test.js'});
  const results=await math.evaluate(async()=>{
    const G=LaserGeometry,O=LaserOffset.offsetOutline,results=[];
    const assert=(v,msg)=>{if(!v)throw Error(msg);},near=(a,b,e=.055)=>assert(Math.abs(a-b)<e,`${a} != ${b}`);
    const item=(d,extra={})=>({d,name:'Testfigur',op:'cut',fillRule:'nonzero',...extra});
    function test(name,fn){fn();results.push(name);}
    test('Constant distance on square, diamond and concave outline',()=>{
      const square=O([item('M10 10H30V30H10Z')],{distance:2,corners:'miter'}),b=G.bounds(square.d);near(b.x,8);near(b.width,24);assert(G.nodes(square.d).length===4,'miter corners');
      const diamond=O([item('M30 10L50 30L30 50L10 30Z')],{distance:2,corners:'miter'});near(G.bounds(diamond.d).y,10-Math.sqrt(8));
      const concave=O([item('M20 20H60V30H30V60H20Z')],{distance:2});const p=G.path(concave.d);assert(!p.contains({x:40,y:40}),'offset filled the concave notch');assert(p.contains({x:21,y:40}),'lost original shape');p.remove();
      assert(G.nodes(O([item('M10 10H30V30H10Z')],{distance:2}).d).length>4,'rounded corners missing');
    });
    test('Circle offset remains the requested distance around curved geometry',()=>{
      const circle='M60 50A10 10 0 1 0 40 50A10 10 0 1 0 60 50Z',out=O([item(circle)],{distance:3});
      for(const n of G.nodes(out.d))near(Math.hypot(n.x-50,n.y-50),13,.065);
    });
    test('Holes are optional and honour even-odd, winding and mirrored paths',()=>{
      const d='M10 10H50V50H10Z M20 20H40V40H20Z',donut=item(d,{fillRule:'evenodd'});
      assert(O([donut]).contours===1,'outside-only kept the hole');
      const holes=O([donut],{distance:2,holes:true,corners:'miter'});assert(holes.contours===2,'hole lost');
      const p=G.path(holes.d),boxes=p.children.map(c=>c.bounds.width).sort((a,b)=>a-b);near(boxes[0],16);near(boxes[1],44);p.remove();
      const mirrored=item(G.scale(d,-1,1,30,30),{fillRule:'evenodd'});assert(O([mirrored],{holes:true}).contours===2,'mirror lost hole');
      const nonzero=item('M10 10H50V50H10Z M20 20V40H40V20Z');assert(O([nonzero],{holes:true}).contours===2,'nonzero hole lost');
    });
    test('Multiple shapes merge without duplicate cuts; separated islands stay separate',()=>{
      const a=item('M10 10H30V30H10Z',{fillRule:'evenodd'}),b=item('M28 10H48V30H28Z',{fillRule:'evenodd'});
      const out=O([a,b],{distance:2});assert(out.contours===1,'overlap not merged');near(G.bounds(out.d).width,42);
      assert(O([a,item('M60 10H80V30H60Z')]).contours===2,'invented bridge between distant shapes');
      assert(O([a,item('M33 10H53V30H33Z')]).contours===1,'touching offsets did not merge');
    });
    test('Open line gets a closed boundary with round caps',()=>{
      const out=O([item('M20 20L40 20')],{distance:2}),b=G.bounds(out.d);assert(G.closed(out.d)&&out.hasOpenPaths,'open result');near(b.x,18);near(b.y,18);near(b.width,24);near(b.height,4);
    });
    test('Traced image uses transformed contours rather than its rectangular frame',()=>{
      const image={width:100,height:100,trace:'M10 15H80V80H10Z M30 35H50V55H30Z'};
      const motif=item('M150 50L150 150L70 150L70 50Z',{image}),out=O([motif],{distance:2,corners:'miter'}),b=G.bounds(out.d);
      near(b.x,84);near(b.y,58);near(b.width,56);near(b.height,74);assert(out.contours===1,'image hole not removed');
      assert(image.trace==='M10 15H80V80H10Z M30 35H50V55H30Z','mutated source');
    });
    await G.loadFont();test('Outlined Danish text and invalid input',()=>{
      const out=O([item(G.center(G.textPath('Åse',14),70,50))]);assert(G.validPath(out.d)&&G.closed(out.d),'text offset invalid');
      for(const options of [{distance:0},{distance:NaN},{distance:31}]){let threw=false;try{O([item('M10 10H30V30H10Z')],options);}catch{threw=true;}assert(threw,'invalid distance accepted');}
      let threw=false;try{O([item('M0 0H50V50H0Z',{image:{trace:''}})]);}catch(e){threw=e.message.includes('Trace');}assert(threw,'untraced image accepted');
    });
    return results;
  });
  checks.push(...results);results.forEach(name=>console.log('PASS',name));
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
  async function ready(){await page.waitForFunction(()=>document.querySelector('#save-status')?.textContent==='Gemt i denne browser');}
  for(const [mode,key] of [['folie','kloster-folievaerksted-v1'],['laser','kloster-laservaerksted-v1']]){
    await page.goto(base+'/'+mode+'.html');await ready();
    await page.locator('[data-level="1"]').click();
    const original={version:1,name:'Offset prøve',width:180,height:120,items:[{id:'shape',name:'Min firkant',d:'M50 40H80V70H50Z',op:mode==='folie'?'cut':'engrave-fill',fillRule:'nonzero'}]};
    await page.evaluate(({key,original})=>localStorage.setItem(key,JSON.stringify(original)),{key,original});await page.reload();await ready();
    const state=()=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
    await page.locator('#object-list button').click();await page.locator('#offset-button').click();await page.locator('#insert-offset:not(:disabled)').waitFor();
    const before=await state();await page.locator('[data-close="offset-dialog"]').first().click();assert(JSON.stringify(await state())===JSON.stringify(before),'cancel altered '+mode);
    await page.locator('#offset-button').click();await page.locator('#offset-distance').fill('3');await page.locator('#offset-corners').selectOption('miter');await page.locator('#insert-offset:not(:disabled)').waitFor();
    assert(await page.locator('#offset-skip-original, #skip-object').count()===0,'exclude controls still exist');
    await page.screenshot({path:`.runtime/${mode}-offset-preview.png`,fullPage:true});
    await page.locator('#insert-offset').click();const added=await state();
    assert(added.items.length===2&&added.items[1].op==='cut','missing cut '+mode);assert(added.items[0].d===original.items[0].d,'source geometry changed');
    assert(added.items[0].op===original.items[0].op,'wrong source operation');
    assert(added.items[1].d.includes('47')&&added.items[1].d.includes('83'),'offset position changed');
    await page.locator('#undo').click();assert(JSON.stringify((await state()).items)===JSON.stringify(before.items),'undo failed '+mode);
    await page.locator('#redo').click();assert((await state()).items.length===2,'redo failed');
    const event=page.waitForEvent('download');await page.locator('#export-button').click();const download=await event,source=await readFile(await download.path(),'utf8');
    assert(source.includes(added.items[1].d),'export omitted offset');
    assert(source.includes(original.items[0].d),'original disappeared from export');
    await page.locator('#object-list button[data-id="shape"]').click();await page.locator('#delete').click();assert((await state()).items.length===1,'original could not be deleted');
    await page.locator('#undo').click();assert((await state()).items.length===2,'delete could not be undone');
    await page.reload();await ready();assert((await state()).items[1].d===added.items[1].d,'restore changed offset');
    await page.locator('[data-level="1"]').click();await page.locator('#svg-file').setInputFiles({name:'offset.svg',mimeType:'image/svg+xml',buffer:Buffer.from(source)});await page.waitForFunction(()=>document.querySelector('#notice').textContent.startsWith('SVG åbnet'));
    const roundtrip=await math.evaluate(({items,d})=>{const canonical=d=>{const p=LaserGeometry.path(d),out=p.pathData;p.remove();return out;};return items.some(o=>canonical(o.d)===canonical(d));},{items:(await state()).items,d:added.items[1].d});
    assert(roundtrip,'roundtrip lost offset geometry');
    checks.push(mode+' UI: preview/cancel, original operation, undo/redo, export and reload');console.log('PASS',checks.at(-1));
  }
  await page.goto(base+'/folie.html');await ready();
  await page.evaluate(()=>localStorage.setItem('kloster-folievaerksted-v1',JSON.stringify({version:1,name:'Kanttest',width:296,height:298,items:[{id:'edge',name:'Ved kanten',op:'cut',d:'M1 1H20V20H1Z'}]})));await page.reload();await ready();
  await page.locator('#object-list button').click();await page.locator('#offset-button').click();
  assert(await page.locator('#insert-offset').isDisabled(),'out-of-bounds insert allowed');assert((await page.locator('#offset-status').innerText()).includes('uden for'),'missing bounds explanation');
  await page.locator('#offset-distance').fill('0.5');await page.locator('#insert-offset:not(:disabled)').waitFor();
  await page.locator('#offset-distance').fill('0');await page.waitForFunction(()=>document.querySelector('#offset-status').classList.contains('error'));assert(await page.locator('#insert-offset').isDisabled(),'invalid distance insert allowed');
  await page.locator('#offset-distance').fill('0.5');await page.locator('#insert-offset:not(:disabled)').waitFor();
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'.runtime/offset-mobile.png',fullPage:true});
  assert(await page.evaluate(()=>{const d=document.querySelector('#offset-dialog');return d.scrollWidth<=d.clientWidth+1;}),'dialog horizontal overflow');
  assert(!errors.length,errors.join('\n'));checks.push('Bounds, invalid distance, mobile layout and production CSP');console.log('PASS',checks.at(-1));
  await writeFile('.runtime/laser-offset-results.json',JSON.stringify(checks,null,2));
}finally{await browser.close();}
