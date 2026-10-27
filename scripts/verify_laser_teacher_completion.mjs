// Isolated headless browser checks for the teacher completion confirmation.
// Uses a private static server and local API mocks only; never contacts a live queue.
import { createServer } from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const root=process.cwd();
const assert=(value,message)=>{if(!value)throw Error(message);};
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg'};
const job=(id='job-a',status='ready')=>({id,project:id==='job-a'?'Fuglehus':'Skilt',student:id==='job-a'?'Ada':'Bo',classroom:'8.A',status,created_at:'2026-10-09T10:00:00Z',payload_available:true,details:{width:100,height:50,operations:{cut:1,'engrave-fill':0,'engrave-line':0},images:[]},imageModes:{}});
let scenario='success';let jobs=[];let statusCalls=[];let releasePost=null;let postSeen=null;
const server=createServer(async(request,response)=>{
  const url=new URL(request.url||'/',`http://${request.headers.host}`);
  response.setHeader('Content-Type','application/json');
  if(url.pathname==='/api/laser/health'){response.end(JSON.stringify({surface:'teacher',teacher:true}));return;}
  if(url.pathname==='/api/laser/jobs'&&request.method==='GET'){response.end(JSON.stringify({jobs}));return;}
  if(/^\/api\/laser\/jobs\/[^/]+\/status$/.test(url.pathname)&&request.method==='POST'){
    const chunks=[];for await(const chunk of request)chunks.push(chunk);const id=url.pathname.split('/')[4];statusCalls.push({id,body:JSON.parse(Buffer.concat(chunks).toString()||'{}')});if(!postSeen)postSeen={id};
    if(scenario==='expired'){response.writeHead(401);response.end(JSON.stringify({error:'Login udløbet'}));return;}
    if(scenario==='error'&&statusCalls.length===1){response.writeHead(500);response.end(JSON.stringify({error:'Midlertidig fejl'}));return;}
    if(scenario==='delayed'){await new Promise(resolve=>{releasePost=resolve;});}
    jobs=jobs.map(item=>item.id===id?{...item,status:'done',payload_available:false}:item);response.end(JSON.stringify({job:jobs.find(item=>item.id===id)}));return;
  }
  const route=url.pathname==='/folie.html'?'/laser.html':url.pathname;
  const webRoot=path.join(root,'web')+path.sep;const file=path.resolve(root,'web',`.${route}`);
  if(!file.startsWith(webRoot)){response.writeHead(403).end();return;}
  try{response.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');response.end(await readFile(file));}catch{response.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;const profile=await mkdtemp(path.join(root,'.runtime','laser-teacher-completion-'));const browser=await chromium.launchPersistentContext(profile,{headless:true,channel:'chrome'});const page=browser.pages()[0]||await browser.newPage();page.setDefaultTimeout(12000);
const reset=async(name,initialJobs=[job()])=>{scenario=name;jobs=initialJobs;statusCalls=[];releasePost=null;postSeen=null;await page.goto(`${base}/laser-teacher.html`);await page.locator('#job-detail h3').waitFor();};
const clickDone=async()=>{await page.locator('#job-detail').getByRole('button',{name:'Markér færdig',exact:true}).click();await page.locator('#done-dialog[open]').waitFor();};

try{
  await reset('delayed',[job(),job('job-b')]);
  await clickDone();
  assert(await page.locator('#done-cancel').evaluate(node=>document.activeElement===node),'Cancel did not receive initial focus');
  await page.locator('#done-confirm').click();
  await page.waitForTimeout(80);
  await page.keyboard.press('Escape');
  assert(await page.locator('#done-dialog').isVisible(),'Escape closed the modal during POST');
  assert(statusCalls.length===1&&statusCalls[0].id==='job-a','POST did not stay bound to the opened job ID');
  await page.locator('#done-confirm').click({force:true});
  assert(statusCalls.length===1,'Duplicate confirmation created another POST');
  releasePost();
  await page.locator('#done-dialog').waitFor({state:'hidden'});await page.locator('#job-list button[data-id="job-a"]').waitFor({state:'detached'});
  assert(await page.locator('#job-list button[data-id="job-b"]').count()===1,'Successful completion did not refresh the filtered queue immediately');
  console.log('PASS delayed POST blocks Escape, duplicate submit, and preserves job ID');

  await reset('error');await clickDone();await page.locator('#done-confirm').click();await page.locator('#done-dialog-error').waitFor();
  assert(await page.locator('#done-dialog').isVisible(),'Error closed the completion modal');assert((await page.locator('#done-confirm').textContent()).trim()==='Prøv igen','Error did not expose retry');
  await page.getByRole('button',{name:'Prøv igen',exact:true}).click();await page.locator('#done-dialog').waitFor({state:'hidden'});assert(statusCalls.length===2,'Retry did not issue exactly one additional POST');console.log('PASS server error keeps modal available for retry');

  await reset('expired');await clickDone();await page.locator('#done-confirm').click();await page.locator('#login-panel').waitFor({state:'visible'});assert(await page.locator('#done-dialog').isHidden(),'Expired login did not close modal');assert(statusCalls.length===1,'Expired login caused extra status writes');console.log('PASS expired login closes modal and returns to login');
}finally{await browser.close();await rm(profile,{recursive:true,force:true});await new Promise(resolve=>server.close(resolve));}
