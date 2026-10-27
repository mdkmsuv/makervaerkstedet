/* Exact parent/source/request handshake against the real bridge source. */
'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../web/project-import.js'),'utf8');
test('configured parent imports once; alien origins, source, and request cannot import',async()=>{
 const replies=[],files=[]; let listener;
 const parent={postMessage:(data,origin)=>replies.push({data,origin})};
 const window={parent,ElevhubProjectOrigins:['http://127.0.0.1:30401'],addEventListener:(_,handler)=>listener=handler,
   ElevhubImportFile:async file=>files.push(file)};
 const context=vm.createContext({window,location:{href:'http://127.0.0.1:30402/?elevhub_import=1'},URL,Set,ArrayBuffer,File,Date,setTimeout});
 vm.runInContext(code,context);
 const hello=(origin,source=parent,request='fixture-request')=>listener({origin,source,data:{type:'elevhub-file-hello',request}});
 await hello('http://127.0.0.1:8791');
 await hello('http://127.0.0.1:30401',{});
 assert.equal(replies.length,0);
 await hello('http://127.0.0.1:30401');
 assert.equal(replies[0].data.type,'elevhub-file-ready');
 const data={type:'elevhub-file-import',request:'fixture-request',filename:'cube.stl',bytes:new ArrayBuffer(8)};
 await listener({origin:'http://127.0.0.1:30401',source:parent,data:{...data,request:'wrong'}});
 await listener({origin:'http://127.0.0.1:8791',source:parent,data});
 assert.equal(files.length,0);
 await listener({origin:'http://127.0.0.1:30401',source:parent,data});
 assert.equal(files.length,1); assert.equal(files[0].name,'cube.stl');
 assert.equal(replies[1].data.type,'elevhub-file-opened');
 assert.ok(replies.every(reply=>reply.origin==='http://127.0.0.1:30401'));
 await listener({origin:'http://127.0.0.1:30401',source:parent,data});
 assert.equal(files.length,1);
});
test('older backend missing configuration trusts only the two exact historical parents',()=>{
 let listener;const replies=[],parent={postMessage:(data,origin)=>replies.push({data,origin})};const window={parent,addEventListener:(_,handler)=>listener=handler};
 vm.runInNewContext(code,{window,location:{href:'http://localhost:30402/?elevhub_import=1'},URL,Set});
 for(const origin of ['http://localhost:8792','http://127.0.0.1:87910','http://localhost:8791.evil.test','null','http://evil.test:8791'])
  listener({origin,source:parent,data:{type:'elevhub-file-hello',request:'x'}});
 assert.equal(replies.length,0);
 for(const origin of ['http://localhost:8791','http://127.0.0.1:8791'])
  listener({origin,source:parent,data:{type:'elevhub-file-hello',request:'x'}});
 assert.equal(replies.length,2);assert(replies.every(reply=>reply.data.type==='elevhub-file-ready'));
});
test('modern explicit empty or invalid configuration never falls back',()=>{
 for(const config of [[],null,'http://localhost:8791']){
  let listener;const replies=[],parent={postMessage:data=>replies.push(data)};
  const window={parent,ElevhubProjectOrigins:config,addEventListener:(_,handler)=>listener=handler};
  vm.runInNewContext(code,{window,location:{href:'http://localhost:30402/?elevhub_import=1'},URL,Set});
  for(const origin of ['http://localhost:8791','http://127.0.0.1:8791'])listener({origin,source:parent,data:{type:'elevhub-file-hello',request:'x'}});
  assert.deepEqual(replies,[]);
 }
});
