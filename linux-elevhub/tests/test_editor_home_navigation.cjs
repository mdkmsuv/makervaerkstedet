// Real keyboard events and browser history; isolated storage, no pupil files.
const { chromium }=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  for(const kind of ['laser','folie']){
   const context=await browser.newContext();const p=await context.newPage();
   await p.goto('http://127.0.0.1:8766/');
   await p.goto(`http://127.0.0.1:8766/${kind}.html`);
   await p.getByRole('button',{name:'Firkant',exact:true}).click();
   const key=kind==='laser'?'kloster-laservaerksted-v1':'kloster-folievaerksted-v1';
   const initial=await p.evaluate(key=>localStorage.getItem(key),key);
   // Ordinary arrows must continue to edit the selected shape.
   await p.keyboard.press('ArrowLeft');
   const edited=await p.evaluate(key=>localStorage.getItem(key),key);
   assert.notEqual(edited,initial,'plain arrow must still move the figure');
   // Match the pupil toolbar: close an open preview, then go back.
   await p.locator('#preview-button').click();
   await p.keyboard.press('Escape');
   await p.evaluate(()=>window.addEventListener('keydown',e=>{if(e.altKey&&e.key==='ArrowLeft')window.altNavigationPrevented=e.defaultPrevented;}));
   await p.keyboard.press('Alt+ArrowLeft');
   assert.equal(await p.evaluate(()=>window.altNavigationPrevented),false,'the editor must allow the native browser accelerator');
   assert.equal(await p.evaluate(key=>localStorage.getItem(key),key),edited,'Alt+Left must preserve the figure');
   // Headless Chromium does not execute the native window accelerator.
   // Exercise the same history traversal here; actual Alt+Left is checked in VM.
   await p.goBack();
   await p.waitForURL('http://127.0.0.1:8766/',{timeout:5000});
   assert.equal(await p.evaluate(key=>localStorage.getItem(key),key),edited,'home navigation must not alter the saved shape');
   console.log(`${kind}: plain arrow edits; Alt+Left remains unconsumed and preserves geometry; history returns home`);
   await context.close();
  }
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
