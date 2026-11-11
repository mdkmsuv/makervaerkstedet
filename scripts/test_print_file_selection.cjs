const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id)};
let failPreview=false;
const context=vm.createContext({
  $:element, console,
  window:{modelPreview:{validate(buffer){if(new Uint8Array(buffer)[0]!==1)throw Error('Bad STL');return [10,10,10]},load(){if(failPreview)throw Error('No WebGL')},clear(){}}},
  uuid:()=>String(Math.random()),showError:(id,message)=>element(id).textContent=message,
  renderSend:()=>{},
});
vm.runInContext("let submitting=false,file=null,fileData=null,requestId=null,fileSelection=0,colour='any';",context);
const source=fs.readFileSync('web/app.js','utf8');
vm.runInContext(source.slice(source.indexOf('async function selectFile('),source.indexOf('window.ElevhubImportFile=')),context);
const selected=(name,value=1)=>({name,size:1,arrayBuffer:async()=>new Uint8Array([value]).buffer});
const value=expression=>vm.runInContext(expression,context);

(async()=>{
  await context.selectFile(selected('valid.stl'));
  assert.equal(value('file.name'),'valid.stl');
  await context.selectFile(selected('fake.stl',0));
  assert.equal(value('file'),null,'an invalid replacement cannot leave the previous model selected');
  assert.equal(value('fileData'),null);
  assert.match(element('upload-error').textContent,/kan ikke bruges/);
  await assert.rejects(context.selectFile(selected('fake.stl',0),true),/kan ikke bruges/);
  failPreview=true;
  await context.selectFile(selected('valid-no-webgl.stl'));
  assert.equal(value('file.name'),'valid-no-webgl.stl','valid models remain usable without WebGL');
  assert.match(element('model-canvas').textContent,/Filen er klar/);
  let finishRead;
  const pending=context.selectFile({name:'slow.stl',size:1,arrayBuffer:()=>new Promise(resolve=>finishRead=resolve)});
  await context.selectFile(selected('latest.stl'));
  finishRead(new Uint8Array([1]).buffer);await pending;
  assert.equal(value('file.name'),'latest.stl','a slow earlier read cannot replace the latest selection');
  const cleared=context.selectFile({name:'cancelled.stl',size:1,arrayBuffer:()=>new Promise(resolve=>finishRead=resolve)});
  context.clearFile();finishRead(new Uint8Array([1]).buffer);await cleared;
  assert.equal(value('file'),null,'clearing the file cancels pending reads');
  console.log('Print file selection: 6 scenarios OK');
})().catch(error=>{console.error(error);process.exitCode=1});
