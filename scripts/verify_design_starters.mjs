// Isolated functional browser checks for the level-aware starter sidebar.
// No VM, physical machines, screenshots or visible browser UI.
import {createServer} from 'node:http';
import {readFile,mkdir,rm,mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const root=process.cwd(),checks=[],errors=[];
const assert=(value,message)=>{if(!value)throw Error(message)};
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.ttf':'font/ttf','.webp':'image/webp'};

await mkdir(path.join(root,'.runtime'),{recursive:true});
let online=true;
const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/api/laser/health'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({surface:'student',inbox:online}));return;}
  const route=url.pathname==='/folie.html'?'/laser.html':url.pathname;
  const file=path.resolve(root,'web','.'+route),webRoot=path.join(root,'web')+path.sep;
  if(!file.startsWith(webRoot)){res.writeHead(403).end();return;}
  try{const body=await readFile(file);res.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');res.end(body)}catch{res.writeHead(404).end()}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const profile=await mkdtemp(path.join(root,'.runtime','starter-check-'));
const browser=await chromium.launchPersistentContext(profile,{headless:true,channel:'chrome'});
const page=browser.pages()[0]||await browser.newPage();await page.setViewportSize({width:1440,height:1000});
page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
const storageKey=mode=>mode==='folie'?'kloster-folievaerksted-v1':'kloster-laservaerksted-v1';
const expectedStarters={
  laser:[['nameplate','key-tag','room-sign','medal'],['pattern-bookmark','club-logo'],['own-silhouette','stencil'],['table-sign','box-for-content','place-sign']],
  folie:[['bottle-label','name-sticker'],['club-logo','workshop-labels'],['own-silhouette','vinyl-motif'],['place-sign']]
};
const state=mode=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),storageKey(mode));
const ready=()=>page.waitForFunction(()=>window.ElevhubProjectAdapter&&document.querySelector('#save-status')?.textContent==='Kladde på denne computer');
async function go(mode='laser'){await page.goto(`${base}/${mode}.html`);await ready();await page.setViewportSize({width:1440,height:1000});}
async function setLevel(level){await page.locator(`.level-bar [data-level="${level}"]`).click();await page.waitForFunction(n=>document.querySelector(`.level-bar [data-level="${n}"]`)?.getAttribute('aria-pressed')==='true',level);}
async function check(name,fn){await fn();checks.push(name);console.log('PASS '+name)}
function cardData(){return page.evaluate(()=>[...document.querySelectorAll('.studio-starter-card')].filter(n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'}).map(n=>({id:n.dataset.projectId,level:n.closest('.starter')?.dataset.starterLevel,mode:n.dataset.mode,text:n.textContent.trim()})))}
function galleryData(){return page.evaluate(()=>{const levels=['Gør den til din','Byg med former','Tegn og kombinér','Design og afprøv'];return [...document.querySelectorAll('.studio-project-card')].map(n=>({id:n.dataset.projectId,level:n.dataset.level==null?levels.indexOf(n.querySelector('.studio-project-level')?.textContent.trim()):Number(n.dataset.level),mode:n.dataset.mode,text:n.textContent.trim()}))})}
async function closeDialog(){const close=page.locator('[data-close="starter-dialog"]');if(await close.count())await close.click();else await page.locator('#starter-dialog button').filter({hasText:'Tilbage'}).click()}
async function clickStarter(index=0){await page.locator('.studio-starter-card').nth(index).click()}

try{
  for(const mode of ['laser','folie']){
    await go(mode);
    await check(`${mode} starters are level- and mode-filtered`,async()=>{
      const expected=mode==='folie'?'vinyl':'laser';
      for(let level=0;level<4;level++){
        await setLevel(level);const cards=await cardData();
        assert(JSON.stringify(cards.map(c=>c.id).sort())===JSON.stringify([...expectedStarters[mode][level]].sort()),`${mode} level ${level} contains wrong projects`);
        assert(cards.length>0,`${mode} level ${level} has no starter cards`);
        assert(new Set(cards.map(c=>c.id)).size===cards.length,`${mode} level ${level} has duplicate starter ids`);
        cards.forEach(card=>{assert(card.id,`${mode} starter is missing data-project-id`);assert(String(card.level)===String(level),`${mode} level ${level} shows starter from level ${card.level}`);assert(!card.mode||card.mode===expected,`${mode} starter has wrong mode ${card.mode}`)});
        if(level===0)assert(cards.length>=(expected==='laser'?4:2),`${mode} beginner level has too few options`);
        const generator=page.locator('.studio-starter-card[data-project-id="box-for-content"]');
        assert(await generator.count()===(level===3&&mode==='laser'?1:0),`generator card visible at wrong level or mode (${mode}, ${level})`);
      }
    });
    await check(`${mode} switching levels preserves the drawing and reload restores level`,async()=>{
      await setLevel(0);const before=await state(mode);await setLevel(3);await setLevel(1);const after=await state(mode);assert(JSON.stringify(before.items)===JSON.stringify(after.items),'level switch changed drawing');
      await setLevel(2);await page.reload();await ready();assert(await page.locator('.level-bar [data-level="2"]').getAttribute('aria-pressed')==='true','chosen level was not restored after reload');
    });
    await check(`${mode} sidebar library filters current level while top ideas opens all`,async()=>{
      await setLevel(1);await page.locator('#starter-library').click();assert(await page.locator('.studio-idea-dialog').isVisible(),'starter library did not open gallery');
      const current=await galleryData();assert(current.length>0,'current-level gallery is empty');assert(current.every(c=>c.level===1),`starter library is not filtered to current level: ${JSON.stringify(current)}`);
      await page.locator('.studio-close').click();await page.locator('.studio-ideas').click();const all=await galleryData();assert(all.length>current.length,'top ideas button did not open all projects');assert(new Set(all.filter(c=>c.level).map(c=>c.level)).size>1,'top ideas gallery is still level filtered');await page.locator('.studio-close').click();
    });
    await check(`${mode} starter replacement confirms, cancel preserves work, text stays editable and undo restores`,async()=>{
      await setLevel(0);const original=await state(mode);await clickStarter();assert(await page.locator('#starter-dialog').isVisible(),'nonempty starter did not request confirmation');await closeDialog();assert(JSON.stringify((await state(mode)).items)===JSON.stringify(original.items),'cancel changed existing work');
      await clickStarter();await page.locator('#confirm-starter').click();await page.waitForFunction(()=>document.querySelector('#starter-dialog')?.open===false);assert((await state(mode)).items.length>0,'starter replacement produced no drawing');
      const text=page.locator('#text-value');assert(await text.isVisible(),'starter text is not editable');const replaced=await state(mode);await text.fill('TEST');await text.press('Tab');const edited=await state(mode);assert(edited.items.some(item=>item.text?.value==='TEST'),'starter text edit was not persisted');await page.locator('#undo').click();await page.locator('#undo').click();assert(JSON.stringify((await state(mode)).items)===JSON.stringify(original.items),'undo did not restore existing drawing');assert(replaced.items.length!==original.items.length||JSON.stringify(replaced.items)!==JSON.stringify(original.items),'replacement was indistinguishable from original');
      const levelZeroIds=(await cardData()).map(card=>card.id);for(const id of levelZeroIds){await page.locator('#new-button').click();await page.locator('#confirm-new').click();await page.locator(`.studio-starter-card[data-project-id="${id}"]`).click();const field=page.locator('#text-value');assert(await field.isVisible(),`level 0 starter ${id} is not text-editable`);await field.fill(`${id}-edited`);await field.press('Tab');assert((await state(mode)).items.some(item=>item.text?.value===`${id}-edited`),`level 0 starter ${id} lost editable text`)}
    });
    await check(`${mode} starter sidebar fits desktop and narrow viewport`,async()=>{
      await setLevel(0);await page.setViewportSize({width:1440,height:1000});assert(await page.locator('.starter').isVisible(),'starter sidebar is hidden on desktop');
      await page.setViewportSize({width:390,height:844});const geometry=await page.evaluate(()=>{const n=document.querySelector('.starter'),r=n?.getBoundingClientRect();return{visible:!!n&&r.width>0&&r.height>0,overflow:document.documentElement.scrollWidth>window.innerWidth+1,within:r? r.right<=window.innerWidth+1:false}});assert(geometry.visible,'starter sidebar is hidden at 390px');assert(!geometry.overflow,'390px layout has horizontal overflow');assert(geometry.within,'starter sidebar exceeds viewport');
    });
  }
  assert(!errors.length,'Browser errors: '+errors.join('; '));
  await mkdir(path.join(root,'output','svg-progression'),{recursive:true});
  const result={passed:checks.length,checks,errors};
  await writeFile(path.join(root,'output','svg-progression','starter-checks.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
}finally{
  await browser.close();await new Promise(resolve=>server.close(resolve));
  const runtimeRoot=path.resolve(root,'.runtime')+path.sep,profilePath=path.resolve(profile);
  if(!profilePath.startsWith(runtimeRoot)||!path.basename(profilePath).startsWith('starter-check-'))throw Error('Refusing to remove a profile outside the test folder');
  await rm(profilePath,{recursive:true,force:true});
}
