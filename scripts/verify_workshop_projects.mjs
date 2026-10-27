import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright'),browser=await chromium.launch({headless:true,channel:'chrome'});
const base=process.env.WORKSHOP_BASE||'http://127.0.0.1:8766',checks=[],errors=[];
const assert=(ok,msg)=>{if(!ok)throw Error(msg);};
try{
  const math=await browser.newPage({bypassCSP:true});await math.goto(base+'/laser.html');await math.addScriptTag({path:process.cwd()+'/.runtime/laser-geometry-test.js'});
  const fixture=await math.evaluate(async()=>{
    const G=LaserGeometry;await G.loadFont();
    const canvas=document.createElement('canvas');canvas.width=canvas.height=40;const c=canvas.getContext('2d');c.fillStyle='#222';c.fillRect(2,2,36,36);c.clearRect(15,15,10,10);
    const item=(id,d,op,extra={})=>({id,name:id,d,op,fillRule:'nonzero',...extra});
    return {version:1,name:'Åses projekt',width:180,height:120,items:[
      item('skaerekant','M10 10H170V110H10Z','cut',{groupId:'gruppe-1'}),
      item('tekst',G.center(G.textPath('Åse Ørn',8),60,30),'engrave-fill',{text:{value:'Åse Ørn',size:8}}),
      item('afventer','M20 80H40V100H20Z','undecided',{groupId:'gruppe-1'}),
      item('billede',G.rotate('M90 50H130V90H90Z',30,110,70),'engrave-fill',{fillRule:'evenodd',image:{src:canvas.toDataURL(),width:40,height:40,trace:'M2 2H38V38H2Z M15 15H25V25H15Z',show:'trace',threshold:160,smoothing:2}}),
      item('streg','M20 45C30 30 50 60 60 45','engrave-line'),
      item('ufærdig','M190 20H210V40H190Z','undecided'),
      item('tandhjul',G.center(G.gear(20,4),55,80),'cut',{gear:{teeth:20,hole:4}})
    ]};
  });
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
  const ready=()=>page.waitForFunction(()=>window.ElevhubProjectAdapter&&document.querySelector('#save-status')?.textContent==='Kladde på denne computer');
  const state=(key='kloster-laservaerksted-v1')=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
  async function open(source,name='test.projekt.svg'){await page.locator('#svg-file').setInputFiles({name,mimeType:'image/svg+xml',buffer:Buffer.from(source)});}
  async function download(action=()=>page.locator('#save-project').click()){const p=page.waitForEvent('download');await action();const file=await p;return {name:file.suggestedFilename(),source:await readFile(await file.path(),'utf8')};}
  async function check(name,fn){await fn();checks.push(name);console.log('PASS',name);}
  await page.goto(base+'/laser.html');await ready();
  await page.evaluate(f=>{localStorage.setItem('kloster-laservaerksted-v1',JSON.stringify(f));localStorage.setItem('kloster-laservaerksted-v1-level','2');},fixture);await page.reload();await ready();
  let project;
  await check('Default save preserves unfinished work in named SVG layers',async()=>{
    assert(await page.locator('#export-button').isDisabled(),'unfinished machine export allowed');assert(await page.locator('#save-project').isEnabled(),'unfinished project cannot save');
    project=await download();assert(project.name==='Åses projekt.projekt.svg','wrong project extension');await writeFile('.runtime/full-project.projekt.svg',project.source);
    const layers=await math.evaluate(source=>{const d=new DOMParser().parseFromString(source,'image/svg+xml');if(d.querySelector('parsererror'))throw Error('invalid SVG');return [...d.documentElement.children].filter(n=>n.localName==='g').map(g=>({op:g.dataset.operation,label:g.getAttributeNS(LaserGeometry.INKSCAPE_NS,'label'),mode:g.getAttributeNS(LaserGeometry.INKSCAPE_NS,'groupmode'),hidden:g.getAttribute('display')}));},project.source);
    assert(layers.length===4&&layers.every(g=>g.mode==='layer'),'missing layer declarations');assert(layers.find(g=>g.op==='cut').label==='Skæring','cut layer missing');assert(layers.find(g=>g.op==='engrave-fill').label.includes('Gravering'),'engraving layer missing');assert(!layers.some(g=>g.op==='skip'||g.hidden==='none'),'hidden excluded layer still saved');
    assert(project.source.includes('data:image/png;base64,')&&project.source.includes('data-text=')&&project.source.includes('data-gear='),'editable sources missing');
  });
  await check('Reopening restores operations, stacking, groups, bitmap and editable generators',async()=>{
    await page.locator('#new-button').click();await page.locator('#confirm-new').click();await open(project.source);await page.waitForFunction(()=>document.querySelector('#notice').textContent.startsWith('Projekt åbnet'));
    const reopened=await state();assert(reopened.name===fixture.name&&reopened.items.length===fixture.items.length,'missing project data');
    assert(await page.locator('[data-level="2"]').getAttribute('aria-pressed')==='true','level not restored');
    for(let i=0;i<fixture.items.length;i++){
      const before=fixture.items[i],after=reopened.items[i];assert(after.name===before.name&&after.op===before.op,'operation or z-order changed '+i);assert(after.groupId===before.groupId,'group changed');assert(JSON.stringify(after.text)===JSON.stringify(before.text),'text metadata lost');assert(JSON.stringify(after.gear)===JSON.stringify(before.gear),'gear metadata lost');
      if(before.image)assert(JSON.stringify(after.image)===JSON.stringify(before.image),'bitmap metadata lost');
      const delta=await math.evaluate(({a,b})=>{const G=LaserGeometry,x=G.bounds(a),y=G.bounds(b);return Math.max(...['x','y','width','height'].map(k=>Math.abs(x[k]-y[k])));},{a:after.d,b:before.d});assert(delta<.0001,'geometry changed '+i);
    }
    await page.locator('#object-list button[data-id="tekst"]').click();assert(await page.locator('#text-value').inputValue()==='Åse Ørn','text not editable');
    await page.locator('#text-value').fill('Åse igen');await page.locator('#text-value').press('Tab');assert((await state()).items.find(o=>o.id==='tekst').text.value==='Åse igen','text edit failed');
    await page.locator('#object-list button[data-id="billede"]').click();assert(await page.locator('#edit-image-trace').isVisible(),'trace editing missing');
  });
  await check('Ctrl+S commits an edited name and saves a project on beginner level',async()=>{
    await page.locator('[data-level="0"]').click();assert(await page.locator('#import-button').isVisible(),'beginner cannot reopen');
    await page.locator('#project-name').fill('Ny titel');const result=await download(()=>page.locator('#project-name').press('Control+s'));assert(result.name==='Ny titel.projekt.svg','Ctrl+S did not commit name');assert(result.source.includes('<title>Ny titel</title>'),'title not saved');
    const chooser=page.waitForEvent('filechooser');await page.keyboard.press('Control+o');const fileChooser=await chooser;assert(!fileChooser.isMultiple(),'unexpected multi-file picker');
    if(process.env.CAPTURE_WORKSHOP_SCREENSHOTS==='1')await page.screenshot({path:'.runtime/project-beginner-desktop.png',fullPage:true});
  });
  await check('Empty projects and unfinished choices survive reopening',async()=>{
    const variants=[[],[fixture.items[2]],[fixture.items[5]]];
    for(const items of variants){const source=await math.evaluate(({fixture,items})=>LaserGeometry.exportSVG({...fixture,items},'all',{project:true,workshop:'laser',level:0}),{fixture,items});await open(source);await page.waitForFunction(n=>document.querySelector('#items').children.length===n,items.length);assert((await state()).items.length===items.length,'lost unfinished item');const saved=await download();assert(saved.source.includes('data-designvaerksted-project="1"'),'not a project');}
  });
  await check('Invalid project input leaves current work intact and legacy SVG still opens',async()=>{
    const before=JSON.stringify(await state());
    for(const source of [project.source.replace('data-designvaerksted-project="1"','data-designvaerksted-project="99"'),project.source.replace('</svg>','<script>alert(1)</script></svg>'),project.source.replace('data-workshop="laser"','data-workshop="other"')]){
      await open(source);await page.waitForFunction(()=>document.querySelector('#notice').classList.contains('error'));assert(JSON.stringify(await state())===before,'invalid import modified drawing');
    }
    const old=await math.evaluate(f=>LaserGeometry.exportSVG({...f,items:f.items.filter(o=>['cut','engrave-fill','engrave-line'].includes(o.op))}),fixture);await open(old,'old.svg');await page.waitForFunction(()=>document.querySelector('#notice').textContent.startsWith('SVG åbnet'));assert((await state()).items.some(o=>o.op==='engrave-line'),'legacy process choices lost');
  });
  await check('Files open in their own workshop; both previous drafts can be recovered',async()=>{
    const laserBefore=JSON.stringify(await state());
    await page.goto(base+'/folie.html');await ready();await page.locator('#project-name').fill('Min tidligere foliekladde');await page.locator('#project-name').blur();
    const vinylBefore=await state('kloster-folievaerksted-v1');
    await page.goto(base+'/laser.html');await ready();
    const longProject=await math.evaluate(()=>LaserGeometry.exportSVG({version:1,name:'Langt foliedesign',width:296,height:603,mat:'long',items:[{id:'folie-tekst',name:'Foliefigur',d:'M20 350H80V400H20Z',op:'cut',fillRule:'nonzero'}]},'all',{project:true,workshop:'vinyl',level:1}));
    await open(longProject);await page.waitForURL('**/folie.html');await page.waitForFunction(()=>document.querySelector('#project-name').value==='Langt foliedesign');
    assert((await state('kloster-folievaerksted-v1')).height===603,'long mat was lost');assert(JSON.stringify(await state())===laserBefore,'cross-workshop import overwrote laser');
    await page.locator('#undo').click();assert(JSON.stringify((await state('kloster-folievaerksted-v1')).items)===JSON.stringify(vinylBefore.items),'old target draft not undoable');
    await open(project.source);await page.waitForURL('**/laser.html');await page.waitForFunction(()=>document.querySelector('#project-name').value==='Åses projekt');
    assert((await state()).items.some(o=>o.op==='engrave-fill'),'laser project became cuts');
  });
  await check('Brother project retains source data while USB export remains pure vectors',async()=>{
    await page.goto(base+'/folie.html');await ready();
    const source=await math.evaluate(f=>LaserGeometry.exportSVG({...f,width:296,height:298,mat:'standard',items:f.items.filter(o=>['tekst','billede','afventer'].includes(o.id)).map(o=>({...o,op:'cut'}))},'all',{project:true,workshop:'vinyl',level:1}),fixture);
    await open(source);await page.waitForFunction(()=>document.querySelector('#notice').textContent.startsWith('Projekt åbnet'));
    const editable=await download();assert(editable.source.includes('base64,')&&editable.source.includes('data-text'),'Brother project lost originals');
    const machine=await download(()=>page.locator('#export-button').click());assert(!machine.source.includes('base64,')&&!machine.source.includes('data-text')&&!machine.source.includes('data-designvaerksted-project'),'Brother export contains project data');
    assert((await state('kloster-folievaerksted-v1')).items.length===3,'Brother item lost');
  });
  await check('Legacy excluded shapes and images require a new choice in both workshops',async()=>{
    for(const mode of ['laser','folie']){
      const key=mode==='laser'?'kloster-laservaerksted-v1':'kloster-folievaerksted-v1';
      await page.goto(base+'/'+mode+'.html');await ready();
      const legacy={...fixture,width:mode==='folie'?296:180,height:mode==='folie'?298:120,items:fixture.items.filter(o=>['skaerekant','billede'].includes(o.id)).map(o=>({...o,op:'skip'}))};
      await page.evaluate(({key,legacy})=>localStorage.setItem(key,JSON.stringify(legacy)),{key,legacy});await page.reload();await ready();
      assert((await state(key)).items.every(o=>o.op==='undecided'),'draft legacy shapes activated or lost');
      assert(await page.locator('#export-button').isDisabled(),'legacy draft exports before review');
      const legacyFile=await math.evaluate(({legacy,mode})=>{
        const source=LaserGeometry.exportSVG({...legacy,items:legacy.items.map(o=>({...o,op:'undecided'}))},'all',{project:true,workshop:mode==='laser'?'laser':'vinyl',level:0});
        const d=new DOMParser().parseFromString(source,'image/svg+xml');
        for(const n of d.querySelectorAll('[data-operation="undecided"]'))n.setAttribute('data-operation','skip');
        d.querySelector('g[data-operation="skip"]').setAttribute('display','none');
        return new XMLSerializer().serializeToString(d);
      },{legacy,mode});
      await open(legacyFile);await page.waitForFunction(()=>document.querySelector('#notice').textContent.startsWith('Projekt åbnet'));
      const reopened=await state(key);assert(reopened.items.length===2&&reopened.items.every(o=>o.op==='undecided'),'legacy hidden layer not recovered for review');
      assert(reopened.items[1].image.src===legacy.items[1].image.src,'legacy image lost');
      assert(await page.locator('#export-button').isDisabled(),'legacy project exports before review');
      assert(await page.locator('#skip-object, #offset-skip-original').count()===0,'exclude controls remain');
      for(const o of reopened.items){await page.locator(`#object-list button[data-id="${o.id}"]`).click();await page.locator('button[data-op="cut"]').click();}
      assert(await page.locator('#export-button').isEnabled(),'reviewed project cannot export');
      const saved=await download();assert(!saved.source.includes('data-operation="skip"'),'saved project still has excluded parts');
    }
  });
  await check('Save/open controls fit desktop and mobile without browser errors',async()=>{
    for(const mode of ['folie','laser']){
      await page.goto(base+'/'+mode+'.html');await ready();
      for(const width of [1440,1024,390]){
        await page.setViewportSize({width,height:900});await page.locator('[data-level="0"]').click();
        assert(await page.locator('#save-project').isVisible()&&await page.locator('#import-button').isVisible(),'beginner file controls hidden');assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page overflow '+mode+' '+width);
        if(width===390)if(process.env.CAPTURE_WORKSHOP_SCREENSHOTS==='1')await page.screenshot({path:`.runtime/${mode}-project-mobile.png`,fullPage:true});
      }
    }
    assert(!errors.length,errors.join('\n'));
  });
  await writeFile('.runtime/workshop-project-results.json',JSON.stringify(checks,null,2));
}finally{await browser.close();}
