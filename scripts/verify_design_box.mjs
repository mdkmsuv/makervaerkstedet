import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,channel:'chrome'});
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await page.goto((process.env.WORKSHOP_BASE||'http://127.0.0.1:8766')+'/laser.html');
  await page.waitForFunction(()=>window.ElevhubProjectAdapter);
  await page.locator('.studio-ideas').click();
  await page.locator('.studio-project-card').filter({has:page.getByRole('heading',{name:'Æske til et bestemt indhold',exact:true})}).getByRole('button',{name:'Start projekt',exact:true}).click();
  await page.locator('#confirm-starter').click();
  await page.waitForFunction(()=>document.querySelector('#box-dialog').open);
  await page.waitForFunction(()=>!document.querySelector('#box-insert').disabled||document.querySelector('#box-status').classList.contains('error'),{},{timeout:15000});
  if(await page.locator('#box-insert').isDisabled())throw Error(await page.locator('#box-status').textContent());
  await page.locator('#box-insert').click();
  const s=await page.evaluate(()=>JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')));
  if(s.learning.id!=='box-for-content'||!s.items.length||s.items.some(o=>o.op!=='cut'))throw Error('Generated starter not inserted with correct guidance');
  const source=await page.evaluate(async()=>await (await window.ElevhubProjectAdapter.export()).text());
  if(!source.includes('data-learning')||!source.includes('Kasse'))throw Error('Generated project cannot be saved');
  console.log('PASS box project opens real local generator and inserts editable cut paths with its learning task');
}finally{await browser.close();}
