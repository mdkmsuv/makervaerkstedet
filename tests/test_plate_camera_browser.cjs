// Real isolated HTTP services, generated camera JPEGs, fresh headless Chromium.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const fixture = spawn(path.join(root,'.venv','Scripts','python.exe'), ['-X','utf8','-u',path.join(__dirname,'plate_camera_browser_fixture.py')], {windowsHide:true,stdio:['pipe','pipe','pipe']});
let stderr=''; fixture.stderr.on('data', chunk => {stderr+=chunk;});
let browser;
const connection = new Promise((resolve,reject)=>{
 let output=''; const timer=setTimeout(()=>reject(new Error('Fixture startup timeout: '+stderr)),20000);
 fixture.stdout.on('data', chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('{'));if(line){clearTimeout(timer);try{resolve(JSON.parse(line));}catch(error){reject(error);}}});
 fixture.on('exit',code=>{clearTimeout(timer);if(code)reject(new Error('Fixture failed '+stderr));});
});
async function main(){
 const info=await connection;
 browser=await chromium.launch({headless:true,channel:'chrome'});
 const context=await browser.newContext({viewport:{width:1366,height:900}});
 const page=await context.newPage(); const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(info.url+'/laerer');
 await page.locator('#admin-token').fill(info.code);
 await page.locator('#login-form button').click();
 await page.locator('[data-camera="one"]').click();
 await page.waitForFunction(()=>{const im=document.getElementById('camera-photo');return !im.hidden&&im.complete&&im.naturalWidth===640;});
 assert.equal(await page.locator('#camera-setup').isVisible(),false);
 assert.equal(await page.locator('#camera-check').isVisible(),true);
 assert.match(await page.locator('#camera-updated').innerText(),/Billede taget/);
 const first=await page.locator('#camera-photo').getAttribute('src');
 await page.locator('#camera-capture').click();
 await page.waitForFunction(old=>{const im=document.getElementById('camera-photo');return !im.hidden&&im.complete&&im.src!==old;}, first);
 await page.locator('#camera-check').click();
 await page.waitForFunction(()=>document.getElementById('camera-state').textContent.includes('manuel godkendelse'));
 assert.equal(await page.locator('#camera-photo').isVisible(),false,'fresh analysis must not be placed beside an older image');
 assert(!((await page.locator('#camera-panel').innerText()).includes('undefined')));
 const state=await context.request.get(info.url+'/api/state');
 assert.equal((await state.json()).printers[0].bed_clear,false,'camera viewing/inspection must not clear the plate');
 await page.locator('#camera-capture').click();
 await page.waitForFunction(()=>!document.getElementById('camera-photo').hidden&&document.getElementById('camera-photo').complete);
 await page.locator('#camera-close').click();
 assert.equal(await page.locator('#camera-panel').isVisible(),false);
 await page.locator('[data-camera="one"]').click();
 await page.waitForFunction(()=>!document.getElementById('camera-photo').hidden&&document.getElementById('camera-photo').complete);
 assert.deepEqual(errors,[]);
 console.log('PASS real main/sidecar HTTP: external fresh preview, refresh, advisory inspection, close/reopen, no automatic clearance');
 await context.close();
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
 if(browser)await browser.close();
 fixture.stdin.end();
 await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();fixture.once('exit',resolve);setTimeout(()=>{fixture.kill();resolve();},10000).unref();});
});
