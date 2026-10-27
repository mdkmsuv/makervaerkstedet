import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,channel:'chrome'});
try{
  const page=await browser.newPage({viewport:{width:1440,height:900},bypassCSP:true});
  const source=await readFile('scripts/laser_browser_checks.js','utf8');
  const run=eval('('+source+')');
  const checks=await run(page);
  await writeFile('.runtime/laser-browser-results.json',JSON.stringify(checks,null,2));
  checks.forEach(c=>console.log(c.status,c.name,c.message||''));
  process.exitCode=checks.some(c=>c.status==='FAIL')?1:0;
}finally{await browser.close();}
