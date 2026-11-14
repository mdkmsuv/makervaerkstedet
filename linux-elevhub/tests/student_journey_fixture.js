'use strict';
const {spawn}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs/promises');
const exists=require('node:fs').existsSync;
const root=path.resolve(__dirname,'../..');
const output=process.env.ELEVHUB_TEST_OUTPUT_DIR || path.join(root,'output/maturity-final-20261013/student');
async function startFixture(){
 await fs.mkdir(output,{recursive:true});
 const python=process.env.ELEVHUB_TEST_PYTHON||process.env.PYTHON||
  [path.join(root,'.venv/Scripts/python.exe'),path.join(root,'.venv/bin/python'),'python3','python']
   .find(value=>exists(value)||!path.isAbsolute(value));
 const child=spawn(python,
  ['-B',path.join(__dirname,'student_journey_fixture.py')],{cwd:root,stdio:['pipe','pipe','pipe'],windowsHide:true});
 let stderr=''; child.stderr.on('data',chunk=>stderr+=chunk);
 const urls=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>{child.kill();reject(Error('Fixture startup timeout: '+stderr));},60000);
  child.stdout.on('data',chunk=>{text+=chunk;const line=text.split(/\r?\n/).find(x=>x.startsWith('{'));if(line){clearTimeout(timer);resolve(JSON.parse(line));}});
  child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr));});
 });
 return {urls,output,async close(){
  const closed=new Promise(resolve=>child.once('exit',resolve));child.stdin.end('\n');
  const timer=setTimeout(()=>child.kill(),15000);await closed;clearTimeout(timer);
 }};
}
function loadPlaywright(){
 for(const name of [process.env.PLAYWRIGHT_MODULE,'playwright'].filter(Boolean)){
  try{return require(name);}catch{}
 }
 throw Error('Playwright is required; install it or set PLAYWRIGHT_MODULE.');
}
function loadJSZip(){
 for(const name of [process.env.JSZIP_MODULE,'jszip'].filter(Boolean)){
  try{return require(name);}catch{}
 }
 const records=[process.env.ELEVHUB_TEST_SCRATCH_ASSETS&&path.join(process.env.ELEVHUB_TEST_SCRATCH_ASSETS,'installation.json'),
  path.join(root,'linux-elevhub/server/scratch/installation.json')].filter(Boolean);
 for(const file of records){
  if(!exists(file))continue;
  const record=JSON.parse(require('node:fs').readFileSync(file,'utf8').replace(/^\uFEFF/,''));
  if(record.sourcePath){try{return require(path.join(record.sourcePath,'node_modules/jszip'));}catch{}}
 }
 throw Error('JSZip is required for genuine SB3/ZIP checks; install it or set JSZIP_MODULE.');
}
module.exports={startFixture,output,loadPlaywright,loadJSZip};
