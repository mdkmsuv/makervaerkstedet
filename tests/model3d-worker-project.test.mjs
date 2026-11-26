// Exercise the actual source worker + WASM in Chrome, without writing vendor bundles.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {TEXT_FONTS} from '../apps/3dprint/web/model3d/fonts.js';
const root=fileURLToPath(new URL('../',import.meta.url));
const require=createRequire(path.join(root,'package.json'));
const {build}=require('esbuild'), {chromium}=require('playwright');
const fontRoutes=new Set(TEXT_FONTS.map(font=>`/assets/model3d/${font.file}`));

async function withWorkerPage(run, asset = (_route, bytes) => bytes) {
  const bundle=await build({entryPoints:[path.join(root,'apps/3dprint/web/model3d/geometry-worker.js')],bundle:true,
    platform:'browser',format:'esm',target:'es2022',external:['node:*'],write:false});
  const server=http.createServer(async (request,response) => {
    const route=new URL(request.url,'http://local').pathname;
    response.setHeader('Cache-Control','no-store');
    try {
      if(route==='/') return response.writeHead(200,{'Content-Type':'text/html'}).end('<!doctype html><title>Isolated worker test</title>');
      if(route==='/vendor/model3d-geometry-worker.js') return response.writeHead(200,{'Content-Type':'text/javascript'}).end(bundle.outputFiles[0].contents);
      const file=route==='/vendor/model3d-manifold.wasm' ? 'apps/3dprint/web/vendor/model3d-manifold.wasm'
        : fontRoutes.has(route) ? `apps/3dprint/web${route}` : null;
      if(!file) return response.writeHead(404).end();
      // Hold WASM startup long enough for evaluate/reset/evaluate to be queued first.
      if(route.endsWith('.wasm')) await new Promise(resolve=>setTimeout(resolve,250));
      const bytes=await asset(route,await readFile(path.join(root,file)));
      response.writeHead(200,{'Content-Type':route.endsWith('.wasm')?'application/wasm':'font/ttf'}).end(bytes);
    } catch { response.writeHead(500).end(); }
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser=await chromium.launch({headless:true,channel:'chrome'});
    const page=await browser.newPage();
    await page.goto('http://127.0.0.1:'+server.address().port+'/');
    await run(page);
  } finally {
    await browser?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  }
}

test('project reset clears real worker assets/cache, including before asynchronous startup', {timeout:60000}, async () => {
  const fontRequests=[];
  await withWorkerPage(async page => {
    const result=await page.evaluate(async () => {
      const worker=new Worker('/vendor/model3d-geometry-worker.js',{type:'module'});
      const pending=new Map(), received=[];
      worker.onmessage=event=>{received.push(event.data.requestId);pending.get(event.data.requestId)?.(event.data);};
      const scene={format:'makervaerkstedet-3d',version:2,units:'mm',plate:{w:180,d:180},
        objects:[{id:'mesh',kind:'mesh',asset:'stl-shared',size:[10,10,10],mode:'solid'}],roots:['mesh']};
      function tetrahedron(z) {
        const points=[[0,0,z],[10,0,0],[0,10,0],[0,0,10]], faces=[[0,2,1],[0,1,3],[0,3,2],[1,2,3]];
        return Float32Array.from(faces.flatMap(face=>face.flatMap(index=>points[index])));
      }
      const evaluate=(id,assets={})=>{
        const promise=new Promise((resolve,reject)=>{
          const timer=setTimeout(()=>reject(Error('Worker lost request '+id)),15000);
          pending.set(id,message=>{clearTimeout(timer);pending.delete(id);resolve(message);});
        });
        worker.postMessage({type:'evaluate',requestId:id,scene,assets}); return promise;
      };
      try {
        // No wait for ready: reset must discard the old pending project, retain the new request.
        worker.postMessage({type:'evaluate',requestId:1,scene,assets:{'stl-shared':tetrahedron(5)}});
        worker.postMessage({type:'reset'});
        const first=await evaluate(2,{'stl-shared':tetrahedron(0)});
        worker.postMessage({type:'reset'});
        const second=await evaluate(3,{'stl-shared':tetrahedron(5)});
        worker.postMessage({type:'reset'});
        const missing=await evaluate(4);
        return {first:{type:first.type,volume:first.volume},second:{type:second.type,volume:second.volume},
          missing:{type:missing.type,code:missing.code},received};
      } finally { worker.terminate(); }
    });
    assert.equal(result.first.type,'result');assert.equal(result.second.type,'result');
    assert.ok(Math.abs(result.first.volume-1000/6)<1e-5);
    assert.ok(Math.abs(result.second.volume-500/6)<1e-5,'second project reused first project geometry cache');
    assert.deepEqual(result.missing,{type:'error',code:'ukendt_fil'},'reset must clear imported assets as well as cached solids');
    assert.deepEqual(result.received,[2,3,4],'reset before ready must discard stale request and keep following evaluations');
    assert.deepEqual(fontRequests,[],'scenes without visible text do not fetch fonts');
  },(route,bytes)=>{if(fontRoutes.has(route))fontRequests.push(route);return bytes;});
});

test('real worker lazily loads fonts, skips obsolete edits and retries a corrupt local file', {timeout:60000}, async () => {
  const requests = [];
  const route = key => `/assets/model3d/${TEXT_FONTS.find(font=>font.key===key).file}`;
  const arrival=Promise.withResolvers(), unblock=Promise.withResolvers();
  await withWorkerPage(async page => {
    try {
      await page.evaluate(() => {
        const worker=new Worker('/vendor/model3d-geometry-worker.js',{type:'module'});
        const waiting=new Map(), received=[];
        worker.onmessage=({data})=>{received.push(data.requestId);waiting.get(data.requestId)?.(data);};
        function send(id,font,hidden=false) {
          const text={id:'text',kind:'text',mode:'solid',params:{font,text:'ÆØÅ æøå',height:12,depth:2}};
          const group={id:'g',kind:'group',mode:'solid',op:'union',hidden,children:['text']};
          worker.postMessage({type:'evaluate',requestId:id,scene:{format:'makervaerkstedet-3d',version:2,units:'mm',
            plate:{w:180,d:180},objects:[text,group],roots:['g']}});
        }
        function evaluate(id,font,hidden=false) {
          const reply=new Promise((resolve,reject)=>{
            const timer=setTimeout(()=>reject(Error('Worker lost request '+id)),12000);
            waiting.set(id,value=>{clearTimeout(timer);waiting.delete(id);resolve({type:value.type,code:value.code,volume:value.volume});});
          });
          send(id,font,hidden);
          return reply;
        }
        window.fontProbe={send,evaluate,received,reset:()=>worker.postMessage({type:'reset'})};
      });
      const hidden=await page.evaluate(()=>window.fontProbe.evaluate(1,'space-mono-bold',true));
      assert.equal(hidden.type,'result');assert.deepEqual(requests,[]);

      await page.evaluate(()=>window.fontProbe.send(2,'bungee'));
      const waitForRequest=setTimeout(()=>arrival.reject(Error('Worker did not request the first font')),8000);
      try { await arrival.promise; } finally { clearTimeout(waitForRequest); }
      const fallback=await page.evaluate(()=>window.fontProbe.evaluate(3,'mkv-sans'));
      assert.equal(fallback.type,'result','a new edit proceeds while an older font request is still pending');
      await page.evaluate(()=>{window.bungee=window.fontProbe.evaluate(4,'bungee');});
      unblock.resolve();
      const alternate=await page.evaluate(()=>window.bungee);
      assert.equal(alternate.type,'result');assert.notEqual(alternate.volume,fallback.volume);
      assert.equal(requests.filter(url=>url===route('bungee')).length,1,'the pending font is shared by edits');
      await page.evaluate(()=>window.fontProbe.reset());
      assert.equal((await page.evaluate(()=>window.fontProbe.evaluate(5,'bungee'))).type,'result');
      assert.equal(requests.filter(url=>url===route('bungee')).length,1,'verified catalogue fonts are reused after project reset');

      const corrupt=await page.evaluate(()=>window.fontProbe.evaluate(6,'anton'));
      assert.equal(corrupt.code,'skrifttype_ikke_indlaest');
      assert.equal((await page.evaluate(()=>window.fontProbe.evaluate(7,'anton'))).type,'result');
      assert.equal(requests.filter(url=>url===route('anton')).length,2);
      const unknown=await page.evaluate(()=>window.fontProbe.evaluate(8,'https://example.test/a.ttf'));
      assert.equal(unknown.code,'ukendt_skrifttype');
      assert.deepEqual(await page.evaluate(()=>window.fontProbe.received),[1,3,4,5,6,7,8]);
      assert.deepEqual(requests,[route('bungee'),route('mkv-sans'),route('anton'),route('anton')]);
    } finally {
      unblock.resolve();
    }
  }, async (url,bytes)=>{
    if (!fontRoutes.has(url)) return bytes;
    requests.push(url);
    if (url===route('bungee')) {arrival.resolve();await unblock.promise;}
    if (url===route('anton')&&requests.filter(item=>item===url).length===1) return Buffer.from('bad font');
    return bytes;
  });
});
