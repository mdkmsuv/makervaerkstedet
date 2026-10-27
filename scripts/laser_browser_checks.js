// Run with the Playwright browser tool after `npm run build:laser` and
// `npx esbuild web/laser-geometry.js --bundle --format=iife --global-name=LaserGeometry --outfile=.runtime/laser-geometry-test.js`.
// The preview must be served at 127.0.0.1:8766. Uses only this prototype.
async (page) => {
  const checks=[],errors=[];
  const assert=(value,message)=>{if(!value)throw Error(message);};
  const check=async(name,fn)=>{try{await fn();checks.push({name,status:'PASS'});}catch(e){checks.push({name,status:'FAIL',message:e.message});}};
  const onError=e=>errors.push(e.message);page.on('pageerror',onError);
  await page.goto('http://127.0.0.1:8766/laser.html');
  await page.waitForFunction(()=>document.querySelector('#items').children.length>0);
  const backup=await page.evaluate(()=>({drawing:localStorage.getItem('kloster-laservaerksted-v1'),level:localStorage.getItem('kloster-laservaerksted-v1-level')}));
  try{
    await page.evaluate(()=>{localStorage.removeItem('kloster-laservaerksted-v1');localStorage.removeItem('kloster-laservaerksted-v1-level');});
    await page.reload();await page.waitForFunction(()=>document.querySelector('#items').children.length===3);
    await page.addScriptTag({path:'D:/Bambuslicer made easy/.runtime/laser-geometry-test.js'});
    const geometry=await page.evaluate(async()=>{
      const G=LaserGeometry;await G.loadFont();const result=[];
      const assert=(v,msg)=>{if(!v)throw Error(msg);};
      const near=(v,e)=>assert(Math.abs(v-e)<.001,`${v} != ${e}`);
      const check=(name,fn)=>{try{fn();result.push({name,status:'PASS'});}catch(e){result.push({name,status:'FAIL',message:e.message});}};
      const svg=(body,attrs='width="100mm" height="60mm" viewBox="0 0 100 60"')=>`<svg xmlns="http://www.w3.org/2000/svg" ${attrs}>${body}</svg>`;
      check('transformed SVG uses millimetres',()=>{
        const s=G.importSVG(svg('<g transform="translate(10 5)"><g transform="scale(2)"><rect x="3" y="4" width="12" height="8"/></g></g><circle cx="60" cy="30" r="5"/>'));
        assert(s.items.length===2,'shape count');let b=G.bounds(s.items[0].d);near(b.x,16);near(b.y,13);near(b.width,24);near(b.height,16);
        assert(s.items.every(o=>o.op==='undecided'),'import must require explicit process choice');
      });
      check('pixels and offset viewBox',()=>{
        const s=G.importSVG(svg('<rect x="10" y="20" width="96" height="48"/>','width="192" height="96" viewBox="10 20 192 96"'));
        near(s.width,50.8);const b=G.bounds(s.items[0].d);near(b.x,0);near(b.y,0);near(b.width,25.4);near(b.height,12.7);
      });
      check('SVG aspect ratio letterboxing',()=>{
        const s=G.importSVG(svg('<rect width="100" height="100"/>','width="100mm" height="60mm" viewBox="0 0 100 100"'));
        const b=G.bounds(s.items[0].d);near(b.x,20);near(b.y,0);near(b.width,60);near(b.height,60);
      });
      check('Danish text contains closed editable outlines',()=>{
        const d=G.textPath('Æble Ørn Åse æøå',8);assert(G.closed(d),'open text');assert(G.bounds(d).height>0,'empty text');
        assert((d.match(/z/gi)||[]).length>15,'letter holes/contours missing');
        const rotated=G.rotate(d,30,0,0);assert(G.closed(rotated),'rotation opened contours');
      });
      check('object IDs also work on ordinary local HTTP',()=>{
        const original=crypto.randomUUID;
        try{crypto.randomUUID=undefined;const first=G.uid(),second=G.uid();assert(/^[a-f0-9-]{36}$/.test(first)&&first!==second,'invalid local HTTP ids');}finally{crypto.randomUUID=original;}
      });
      check('roundtrip preserves sizes, process, and text metadata',()=>{
        const d=G.center(G.textPath('ÅSE',8),30,30);
        const s={name:'Prøve',width:100,height:60,items:[{id:'a',name:'Kant',op:'cut',d:G.shape('rect')},{id:'b',name:'ÅSE',op:'engrave-fill',d,text:{value:'ÅSE',size:8}},{id:'c',name:'Streg',op:'engrave-line',d:'M10 50L80 50'},{id:'d',name:'Udeladt',op:'skip',d:G.shape('circle')}]};
        const output=G.exportSVG(s),doc=new DOMParser().parseFromString(output,'image/svg+xml'),r=G.importSVG(output);
        assert(doc.documentElement.getAttribute('width')==='100mm','no physical units');assert(!doc.querySelector('text,image'),'text/raster exported');
        assert(r.items.length===3,'skipped part included');assert(r.items.map(o=>o.op).join(',')==='engrave-fill,engrave-line,cut','operation order');
        const t=r.items.find(o=>o.text);assert(t.text.value==='ÅSE','text metadata');near(G.bounds(t.d).width,G.bounds(d).width);
      });
      check('unsafe and unsupported SVG is rejected',()=>{
        for(const body of ['<script>alert(1)</script>','<foreignObject/>','<image href="https://example.com/x.png"/>','<text>Hej</text>','<rect width="5" height="5" onclick="alert(1)"/>','<rect class="red" width="5" height="5"/>','<g style="stroke-dasharray:2 2"><path d="M0 0L20 20"/></g>']){
          let rejected=false;try{G.importSVG(svg(body));}catch{rejected=true;}assert(rejected,'accepted '+body);
        }
      });
      check('gear extremes preserve teeth, hole and module',()=>{
        for(const t of [17,20,48]){const d=G.gear(t,4);assert(G.validPath(d)&&G.closed(d),'invalid gear '+t);assert((d.match(/M/g)||[]).length===2,'missing hole');near(Math.max(...G.nodes(d).map(p=>Math.hypot(p.x,p.y))),t*.75+1.5);assert(G.bounds(d).width<=t*1.5+3.001,'gear exceeds nominal diameter');}
        assert((G.gear(20,0).match(/M/g)||[]).length===1,'hole=0 still has hole');
      });
      check('boolean union and subtraction retain geometry',()=>{
        const a={d:'M0 0H20V20H0Z'},b={d:'M10 0H30V20H10Z'};
        const union=G.combine([a,b],'unite');assert(G.closed(union),'open union');near(G.bounds(union).width,30);
        const sub=G.combine([a,{d:'M5 5H15V15H5Z'}],'subtract');assert(G.closed(sub),'open difference');assert((sub.match(/m/gi)||[]).length===2,'hole missing');
      });
      check('export checks undecided, empty, open fill and off-paper',()=>{
        const s={width:100,height:60,items:[]};assert(G.drawingIssues(s).length>0,'empty');
        for(const item of [{op:'undecided',d:'M5 5H20V20H5Z'},{op:'engrave-fill',d:'M5 5L20 20'},{op:'cut',d:'M-10 5H20V20H-10Z'}])assert(G.drawingIssues({...s,items:[{name:'Prøve',...item}]}).length>0,'invalid passed');
      });
      return result;
    });checks.push(...geometry);
    await check('levels reveal tools without changing the drawing',async()=>{
      const before=await page.locator('#items').innerHTML();
      assert(await page.locator('#import-button').isVisible(),'beginner cannot reopen a project');assert(await page.locator('#add-gear').isVisible(),'beginner gear missing');
      await page.locator('[data-level="1"]').click();assert(await page.locator('#import-button').isVisible(),'level1 import hidden');assert(await page.locator('[data-tool="freehand"]').isVisible(),'freehand hidden');
      await page.locator('[data-level="2"]').click();assert(await page.locator('#unite').isVisible(),'union hidden');
      await page.locator('[data-level="3"]').click();assert(await page.locator('[data-tool="nodes"]').isVisible(),'nodes hidden');assert(await page.locator('#board-width').isVisible(),'dimensions hidden');
      await page.locator('[data-level="0"]').click();assert(await page.locator('#items').innerHTML()===before,'level switch changed drawing');
    });
    await check('edit Danish text and export a Beam test file',async()=>{
      await page.locator('#text-value').fill('Åse Ørn');await page.locator('#text-value').press('Tab');
      await page.locator('#object-width').fill('50');await page.locator('#object-width').press('Tab');assert(await page.locator('#text-value').isVisible(),'proportional resize made text uneditable');
      assert(await page.locator('#export-button').isEnabled(),'export blocked');
      const downloadPromise=page.waitForEvent('download');await page.locator('#export-button').click();const file=await downloadPromise;
      await file.saveAs('D:/Bambuslicer made easy/.runtime/laser-beam-test/editor.svg');
      const state=await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')));
      assert(state.items.find(o=>o.text).text.value==='Åse Ørn','text update missing');
    });
    await check('add gear, resize through tooth count, undo and redo',async()=>{
      await page.locator('#add-gear').click();await page.locator('#gear-teeth').fill('24');await page.locator('#gear-teeth').press('Tab');
      assert(await page.locator('#object-width').isDisabled(),'gear allows incompatible scaling');
      await page.locator('#undo').click();assert(await page.locator('#object-count').textContent()==='4','undo removed wrong change');
      await page.locator('#redo').click();const s=await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')));assert(s.items.find(o=>o.gear).gear.teeth===24,'redo lost teeth');
    });
    await check('drag moves selected geometry and undo restores it',async()=>{
      await page.locator('#object-list button').filter({hasText:'Nøgleringens kant'}).click();
      const before=await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')).items[0].d);
      const p=await page.evaluate(()=>{const e=document.querySelector('#items .design-item path'),b=e.getBBox(),m=document.querySelector('#drawing').getScreenCTM();const p=new DOMPoint(b.x+b.width/2,b.y).matrixTransform(m);return {x:p.x,y:p.y};});
      await page.mouse.move(p.x,p.y);await page.mouse.down();await page.mouse.move(p.x+15,p.y+10,{steps:5});await page.mouse.up();
      const after=await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')).items[0].d);assert(before!==after,'no movement');
      await page.locator('#undo').click();assert(await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')).items[0].d)===before,'undo geometry mismatch');
    });
    await check('SVG opening is undoable and invalid import preserves work',async()=>{
      await page.locator('[data-level="1"]').click();const before=await page.evaluate(()=>localStorage.getItem('kloster-laservaerksted-v1'));
      await page.locator('#svg-file').setInputFiles({name:'invalid.svg',mimeType:'image/svg+xml',buffer:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')});
      await page.waitForFunction(()=>document.querySelector('#notice').classList.contains('error'));
      assert(await page.evaluate(()=>localStorage.getItem('kloster-laservaerksted-v1'))===before,'invalid import destroyed state');
      await page.locator('#svg-file').setInputFiles('D:/Bambuslicer made easy/.runtime/laser-beam-test/editor.svg');
      await page.waitForFunction(()=>document.querySelector('#object-count').textContent==='3');
      assert(await page.locator('#export-button').isEnabled(),'roundtrip export blocked');
      await page.locator('#undo').click();assert(await page.evaluate(()=>localStorage.getItem('kloster-laservaerksted-v1'))===before,'opening undo mismatch');
    });
    await check('preview separates cutting and engraving',async()=>{
      await page.locator('#preview-button').click();await page.locator('[data-preview="cut"]').click();assert(await page.locator('#preview-area g[data-operation="cut"]').count()>0,'cut missing');assert(await page.locator('#preview-area g[data-operation^="engrave"]').count()===0,'engraving included');
      await page.locator('[data-preview="engrave"]').click();assert(await page.locator('#preview-area g[data-operation="cut"]').count()===0,'cut included');
      await page.locator('[data-close="preview-dialog"]').first().click();
    });
    await check('no browser exceptions',async()=>assert(errors.length===0,errors.join('\n')));
  }finally{
    page.off('pageerror',onError);
    await page.evaluate(backup=>{for(const [key,value] of [['kloster-laservaerksted-v1',backup.drawing],['kloster-laservaerksted-v1-level',backup.level]])if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,value);},backup);
    await page.reload();
  }
  return checks;
}
