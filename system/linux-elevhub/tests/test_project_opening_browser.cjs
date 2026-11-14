const {startFixture,loadPlaywright,loadJSZip}=require('./student_journey_fixture.js');
const path=require('node:path');
// Uses real product services with an ephemeral, disposable project store.
const {chromium}=loadPlaywright();
const assert=require('node:assert/strict'),fs=require('node:fs'),crypto=require('node:crypto');

const runId=crypto.randomUUID().slice(0,8);
(async()=>{
 const fixture=await startFixture();
 const base=fixture.urls.projekter,api=base+'/_elevhub/projekter/api/';
 const screenshot=name=>path.join(fixture.output,'open-'+name+'.png');
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  async function save(filename,data,kind='andet',name='Åbneprøve '+filename+' '+runId){
   const query=new URLSearchParams({id:crypto.randomUUID().replaceAll('-',''),name,folder:'QA · filåbning',kind,filename,expected:0});
   const r=await page.request.post(api+'save?'+query,{headers:{Origin:base,'X-Elevhub-Project':'1','Content-Type':'application/octet-stream'},data});
   assert.equal(r.status(),200,await r.text());return await r.json();
  }
  async function acceptFreshImport(){
   await page.waitForFunction(()=>document.querySelector('#project-finish-discard')?.getClientRects().length||['#save-status','#statusMessage','#elevhub-project-bar'].some(selector=>document.querySelector(selector)?.textContent.includes('Åbnet')));
   if(await page.locator('#project-finish-discard').isVisible())await page.locator('#project-finish-discard').click();
  }
  async function open(p){await page.goto(base+'/_elevhub/projekter/open?id='+p.id+'&revision='+p.revision);}
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF1sAAAAASUVORK5CYII=','base64');
  for(const [file,data,program,selector] of [
   ['data.csv','navn;antal\n"Kasser; blå";7','Tabelviseren','td'],
   ['noter.txt','Byg et rumskib.','Tekstviseren','pre'],
   ['tal.json','{"antal":7}','Dataviseren','pre'],
   ['billede.png',png,'Billedviseren','.file-view img'],
   ['pixels.piskel',JSON.stringify({piskel:{layers:[JSON.stringify({name:'Figur',chunks:[{base64PNG:'data:image/png;base64,'+png.toString('base64')}]})]}}),'Pixelviseren','.pixel-frames img'],
   ['lyd.wav',Buffer.concat([Buffer.from('524946462600000057415645666d74201000000001000100401f0000803e0000020010006461746102000000','hex'),Buffer.from([0,0])]),'Lydafspilleren','audio']
  ]){
   const p=await save(file,data);await open(p);await page.locator(selector).first().waitFor();
   await page.getByText(program+' · version 1',{exact:true}).waitFor();
   assert.equal(await page.locator('#retry').isVisible(),false);
   console.log('PASS viewer '+file);
  }
  const JSZip=loadJSZip();
  const zip=new JSZip();zip.file('byg.txt','Rumskib');
  const z=await save('mappe.zip',await zip.generateAsync({type:'nodebuffer'}));await open(z);await page.getByText('byg.txt',{exact:true}).waitFor();
  console.log('PASS archive viewer');
  const svg='<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100" data-workshop="vinyl"><rect x="10" y="10" width="35" height="20" fill="black"/></svg>';
  const s=await save('skilt.svg',svg,'laser');
  await page.goto(base);await page.locator('#search').fill(s.name);
  await page.getByRole('button',{name:'Åbn projekt i Folieværkstedet',exact:true}).click();
  await page.waitForURL(fixture.urls.folie+'**');
  await page.waitForFunction(()=>window.ElevhubProjectAdapter);
  await acceptFreshImport();
  await page.waitForFunction(()=>document.querySelector('#save-status')?.textContent.includes('Åbnet'));
  const design=page;
  const exported=await design.evaluate(async()=>await(await window.ElevhubProjectAdapter.export()).text());
  assert.match(exported,/35/);assert.match(exported,/20/);
  await page.screenshot({path:screenshot('folie')});console.log('PASS saved SVG chooses Folie and imports geometry');
  // SVG files from the retired laser editor remain downloadable. The current
  // editor imports its editable JSON format and must report the old format.
  const legacySvg=svg.replace('vinyl','laser');
  const legacyLaser=await save('laser.svg',legacySvg);await open(legacyLaser);
  await page.waitForURL(fixture.urls.laser+'**');
  await page.waitForFunction(()=>document.querySelector('#statusMessage')?.textContent.includes('gamle lasereditor'));
  assert.equal(await(await page.request.get(api+'file?id='+legacyLaser.id)).text(),legacySvg,'unsupported legacy file stays intact');
  console.log('PASS legacy Laser SVG reports its format and remains downloadable without data loss');
  await page.goto(fixture.urls.laser);await page.waitForFunction(()=>window.ElevhubProjectAdapter);
  await page.locator('#project-classroom').fill('5A');await page.locator('#project-student').fill('Testelev');await page.locator('#project-title').fill('Laserimport');await page.locator('#project-start-confirm').click();
  await page.locator('[data-add-type="rect"]').click();
  await page.locator('#objectWidth').fill('35');await page.locator('#objectWidth').press('Tab');
  await page.locator('#objectHeight').fill('20');await page.locator('#objectHeight').press('Tab');
  const laserPayload=await page.evaluate(async()=>JSON.parse(await(await window.ElevhubProjectAdapter.export()).text()));
  const laser=await save('design.laser.json',JSON.stringify(laserPayload));await open(laser);
  await page.waitForURL(fixture.urls.laser+'**');await acceptFreshImport();
  const reopenedLaser=await page.evaluate(async()=>JSON.parse(await(await window.ElevhubProjectAdapter.export()).text()));
  const normalize=objects=>objects.map(object=>({...object,...(object.type==='rect'?{rounded:object.rounded??false}:{}),...(object.children?{children:normalize(object.children)}:{})}));
  assert.deepEqual(normalize(reopenedLaser.project.objects),normalize(laserPayload.project.objects));
  assert.deepEqual(reopenedLaser.workspace,laserPayload.workspace);
  assert(reopenedLaser.project.objects.some(object=>object.type==='rect'&&object.width===35&&object.height===20));
  console.log('PASS current editable Laser JSON navigation and geometry roundtrip');
  // A genuine Scratch file, including the editor's own costume and sound assets.
  await page.goto(fixture.urls.scratch+'/');await page.waitForFunction(()=>window.ElevhubProjectAdapter);
  await page.locator('#project-classroom').fill('5A');await page.locator('#project-student').fill('Testelev');await page.locator('#project-title').fill('Importprøve');await page.locator('#project-start-confirm').click();
  const sprite=page.getByPlaceholder('Navn',{exact:true});await sprite.fill('Importkat');await sprite.press('Enter');
  await page.waitForFunction(()=>window.ElevhubProjectAdapter.signature().includes('Importkat'));
  const sb3=await page.evaluate(async()=>Array.from(new Uint8Array(await(await window.ElevhubProjectAdapter.export()).arrayBuffer())));
  const scratch=await save('spil.sb3',Buffer.from(sb3),'laser');await open(scratch);
  await acceptFreshImport();
  await page.waitForFunction(()=>window.ElevhubProjectAdapter?.signature().includes('Importkat'),{},{timeout:45000});
  await page.locator('#elevhub-project-bar').getByText(/Åbnet Åbneprøve spil/).waitFor();
  await page.screenshot({path:screenshot('scratch')});console.log('PASS SB3 opens Scratch with actual saved sprite');
  const hex=await save('pilot.hex',fs.readFileSync('system/linux-elevhub/tests/fixtures/makecode-source.hex'));
  const info=await(await page.request.get(api+'open-info?id='+hex.id)).json();
  assert.equal(info.program,'microbit');assert.equal(info.mode,'local');
  assert.equal(info.editable.project.header.name,'Elevhub-pilot');
  // The real local controller requires external MakeCode. Verify honest offline
  // guidance here; remote editor import is an explicitly separate integration.
  await page.route('https://makecode.microbit.org/**',route=>route.abort());
  await open(hex);await page.waitForURL(fixture.urls.microbit+'/**');
  await page.getByText(/micro:bit kunne ikke åbnes/).first().waitFor({timeout:25000});
  console.log('PASS HEX recognition/extraction and real offline MakeCode guidance (external editor not exercised)');
  const {BoxGeometry,Mesh}=await import('three');
  const {STLExporter}=await import('three/examples/jsm/exporters/STLExporter.js');
  const cubeGeometry=new BoxGeometry(20,20,20);
  const cubeStl=new STLExporter().parse(new Mesh(cubeGeometry),{binary:true});
  const stl=await save('qa-cube-20mm.stl',Buffer.from(cubeStl.buffer,cubeStl.byteOffset,cubeStl.byteLength));
  cubeGeometry.dispose();
  await open(stl);
  await page.getByText('Åbnet i 3D-værkstedet · version 1',{exact:true}).waitFor({timeout:45000});
  const print=page.frames().find(f=>f.url().startsWith(fixture.urls.print+'/'));
  assert(print,'STL did not open the local Printværksted preview');
  await print.locator('#file-preview').waitFor({state:'visible',timeout:45000});
  assert.equal(await print.locator('#file-name').textContent(),'qa-cube-20mm.stl');
  await print.locator('#model-canvas canvas').waitFor({timeout:45000});
  await page.screenshot({path:screenshot('stl')});console.log('PASS STL opens Printværksted preview with rendered model');
  const untrusted=await browser.newPage();
  const rejection=untrusted.waitForEvent('console',{predicate:message=>message.text().includes('frame-ancestors')});
  await untrusted.goto(fixture.urls.untrusted+'/');
  await rejection;
  assert.deepEqual(await untrusted.evaluate(()=>window.received),[],'untrusted parent cannot receive an import-ready handshake');
  await untrusted.close();console.log('PASS real Chromium CSP rejects untrusted loopback parent; no import-ready message');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(fixture.output,'opening-browser-errors.json'),JSON.stringify(errors,null,2));
 }catch(error){console.error('Opening failure:',await browser.contexts()[0]?.pages()[0]?.locator('body').innerText().catch(()=>''));throw error;}finally{await browser.close();await fixture.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
