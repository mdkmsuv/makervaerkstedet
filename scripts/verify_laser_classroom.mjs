import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,channel:'chrome'});
const checks=[];
const assert=(v,message)=>{if(!v)throw Error(message);};
try{
  const studentContext=await browser.newContext({viewport:{width:1440,height:940}});
  const student=await studentContext.newPage();student.setDefaultTimeout(15000);const errors=[];student.on('pageerror',e=>errors.push(e.message));
  // Stable request number also verifies retries when this development check is rerun.
  await student.route('**/api/laser/submit',route=>{const data=route.request().postDataJSON();return route.continue({postData:JSON.stringify({...data,requestId:'ui-classroom-test-20260915'})});});
  await student.goto('http://127.0.0.1:8766/laser.html');await student.waitForFunction(()=>!document.querySelector('#send-button').disabled);
  await student.locator('#text-value').fill('ÅSE');await student.locator('#text-value').press('Tab');
  await student.locator('#send-button').click();await student.locator('#student-name').fill('Demo-elev');await student.locator('#student-class').fill('3.A');await student.locator('#student-project').fill('Prøveaflevering · nøglering');
  await student.locator('#confirm-send').click();await student.waitForFunction(()=>document.querySelector('#send-status').textContent.startsWith('Modtaget hos læreren'));
  const receipt=await student.locator('#send-status').textContent();checks.push({name:'student receives server confirmation',status:'PASS',receipt});
  await student.locator('[data-close="send-dialog"]').first().click();
  await student.screenshot({path:'.runtime/laser-student-desktop.png',fullPage:true});
  assert(await student.locator('a[href*="8767"],a[href*="laerer"],a[href*="teacher"]').count()===0,'teacher link on student page');
  for(const route of ['/laser-teacher.html','/api/laser/jobs'])assert((await student.request.get('http://127.0.0.1:8766'+route)).status()===404,'student exposed '+route);
  checks.push({name:'student page has no teacher surface',status:'PASS'});
  await student.setViewportSize({width:390,height:844});await student.screenshot({path:'.runtime/laser-student-mobile.png',fullPage:true});
  assert(await student.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile horizontal page overflow');
  checks.push({name:'mobile layout fits viewport',status:'PASS'});
  const teacherContext=await browser.newContext({viewport:{width:1440,height:940}});
  const teacher=await teacherContext.newPage();teacher.setDefaultTimeout(15000);teacher.on('pageerror',e=>errors.push(e.message));
  assert((await teacher.request.get('http://127.0.0.1:8767/api/laser/jobs')).status()===401,'teacher data before login');
  await teacher.goto('http://127.0.0.1:8767/');await teacher.screenshot({path:'.runtime/laser-teacher-login.png',fullPage:true});
  const code=(await readFile('data/laser/laererkode.txt','utf8')).trim();await teacher.locator('#teacher-code').fill(code);await teacher.locator('#login-form button').click();
  await teacher.locator('#job-list .job-card').filter({hasText:'Prøveaflevering · nøglering'}).first().waitFor();
  await teacher.locator('#job-list .job-card').filter({hasText:'Prøveaflevering · nøglering'}).first().click();
  await teacher.waitForFunction(()=>document.querySelector('.preview-image img')?.complete&&document.querySelector('.preview-image img').naturalWidth>0);
  const downloadPromise=teacher.waitForEvent('download');await teacher.locator('#download-svg').click();const file=await downloadPromise;await file.saveAs('.runtime/laser-beam-test/from-teacher.svg');
  const svg=await readFile('.runtime/laser-beam-test/from-teacher.svg','utf8');assert(svg.includes('width="180mm"')&&svg.includes('data-operation="engrave-fill"')&&svg.includes('data-operation="cut"'),'teacher file lost dimensions/operations');
  if(await teacher.locator('[data-status="ready"]').count())await teacher.locator('[data-status="ready"]').click();await teacher.waitForFunction(()=>document.querySelector('.detail-heading .status-label')?.textContent==='Gennemset');
  await teacher.screenshot({path:'.runtime/laser-teacher-inbox.png',fullPage:true});
  checks.push({name:'teacher login preview download and status',status:'PASS'});
  await teacher.locator('#logout').click();assert((await teacher.request.get('http://127.0.0.1:8767/api/laser/jobs')).status()===401,'logout failed');
  checks.push({name:'logout removes access',status:'PASS'});
  assert(errors.length===0,errors.join('\n'));checks.push({name:'no browser errors with production CSP',status:'PASS'});
}catch(e){checks.push({name:'classroom flow',status:'FAIL',message:e.message});process.exitCode=1;}
finally{await browser.close();await writeFile('.runtime/laser-classroom-results.json',JSON.stringify(checks,null,2));checks.forEach(c=>console.log(c.status,c.name,c.message||''));}
