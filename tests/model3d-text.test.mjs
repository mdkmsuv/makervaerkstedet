import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import crypto from 'node:crypto';
import {createGeometryKernel,exportBinaryStl} from '../apps/3dprint/web/model3d/geometry.js';
import {DEFAULT_FONT,TEXT_FONTS} from '../apps/3dprint/web/model3d/fonts.js';
import {validateSTL} from '../sdk/web/stl-validation.mjs';
const {default:Module}=await import(process.env.MODEL3D_MANIFOLD_MODULE||'manifold-3d');
const {parse}=await import(process.env.MODEL3D_OPENTYPE_MODULE||'opentype.js/dist/opentype.mjs');
const fonts=Object.fromEntries(TEXT_FONTS.map(({key,file,sha256})=>{
 const local=new URL(`../apps/3dprint/web/assets/model3d/${file}`,import.meta.url);
 const bytes=fs.readFileSync(key===DEFAULT_FONT && process.env.MODEL3D_FONT_FILE || local);
 assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),sha256,key);
 return [key,parse(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength))];
}));
const font=fonts[DEFAULT_FONT],wasm=await Module();wasm.setup();
const kernel=createGeometryKernel(wasm,{fonts});
const scene=(objects,roots=objects.map(x=>x.id))=>({format:'makervaerkstedet-3d',version:2,units:'mm',plate:{w:180,d:180},objects,roots});
const text=(value,extra={})=>({id:'text',kind:'text',mode:'solid',position:[0,0,1],params:{text:value,font:'mkv-sans',height:12,depth:2},...extra});
const group=(children)=>({id:'g',kind:'group',op:'difference',mode:'solid',children});
const near=(a,b,t=1e-4)=>assert.ok(Math.abs(a-b)<t,`${a} != ${b}`);
function closedSTL(result){const buffer=exportBinaryStl(result),valid=validateSTL(buffer),v=new DataView(buffer),edges=new Map();let volume=0;
 for(let i=0;i<valid.triangles;i++){const p=[0,1,2].map(c=>[0,1,2].map(a=>v.getFloat32(84+i*50+12+c*12+a*4,true)));const [a,b,c]=p;
 volume+=(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6;
 for(let k=0;k<3;k++){const a=p[k].join(','),b=p[(k+1)%3].join(','),key=[a,b].sort().join('|'),entry=edges.get(key)||[0,0];entry[0]++;entry[1]+=a<b?1:-1;edges.set(key,entry);}}
 for(const [count,balance]of edges.values()){assert.equal(count,2);assert.equal(balance,0);}near(volume,result.volume,0.01);return valid;}

test('real Danish glyphs have physical centred bounds, positive closed STL and preserved counters',()=>{
 const result=kernel.evaluateScene(scene([text('ÆØÅ æøå')]));assert.ok(result.volume>0);near(result.meshes[0].naturalSize[1],12);near(result.meshes[0].naturalSize[2],2);
 // This footprint and volume come from a project rendered before the font catalogue was added.
 near(result.meshes[0].naturalSize[0],57.67741935);near(result.volume,308.66784989);
 const bounds=result.meshes[0].localBounds;near(bounds.min[1],-6);near(bounds.max[1],6);near(bounds.min[2],-1);near(bounds.max[2],1);closedSTL(result);
 const o=text('O'),before=kernel.evaluateScene(scene([o]));
 const cutter={id:'cut',kind:'cylinder',mode:'hole',size:[1,1,4],position:[0,0,1]};
 const after=kernel.evaluateScene(scene([o,cutter,group(['text','cut'])],['g']));
 near(after.volume,before.volume); // The central counter is already empty; NonZero must not fill it.
 closedSTL(after);
});

test('changing text changes measured natural width without guessing or stretching height',()=>{
 const a=kernel.evaluateScene(scene([text('I')])),b=kernel.evaluateScene(scene([text('MMMM')]));
 assert.ok(b.meshes[0].naturalSize[0]>a.meshes[0].naturalSize[0]*4);near(a.meshes[0].naturalSize[1],12);near(b.meshes[0].naturalSize[1],12);
});

test('all bundled typefaces yield distinct Danish STL geometry at an identical height and preserve counters',()=>{
 const markers=new Set(), cached=createGeometryKernel(wasm,{fonts,cache:48});
 assert.equal(TEXT_FONTS.length,11);
 const label=(value,key)=>text(value,{params:{text:value,font:key,height:12,depth:2}});
 const cutter={id:'cut',kind:'cylinder',mode:'hole',size:[1,1,4],position:[0,0,1]};
 try {
  for(const {key} of TEXT_FONTS){
   const result=cached.evaluateScene(scene([label('ÆØÅ æøå',key)]));
   near(result.meshes[0].naturalSize[1],12);near(result.meshes[0].naturalSize[2],2);
   closedSTL(result);
   markers.add(crypto.createHash('sha256').update(Buffer.from(exportBinaryStl(result))).digest('hex'));
   const o=label('O',key), before=cached.evaluateScene(scene([o]));
   const after=cached.evaluateScene(scene([o,cutter,group(['text','cut'])],['g']));
   assert.ok(Math.abs(after.volume-before.volume)<1e-4,`${key} closes the O counter`);
   closedSTL(after);
  }
  assert.equal(markers.size,11,'every actual STL uses different outlines, even through the scene cache');
 } finally { cached.clearCache(); }
});

test('kerning follows the typeface and Space Mono keeps its real space advance after its shaping fallback',()=>{
 const width=(value,key,engine=kernel)=>engine.evaluateScene(scene([text(value,{params:{text:value,font:key,height:12,depth:2}})])).meshes[0].naturalSize[0];
 const kerned=['mkv-sans','archivo-black','titan-one','anton','alfa-slab-one','bungee','righteous','bree-serif'];
 const reductions=[];
 for(const key of kerned){
  const real=fonts[key], noKerning=Object.create(real);
  noKerning.getPath=(text,x,y,size,options)=>real.getPath(text,x,y,size,{...options,kerning:false});
  const unkerned=createGeometryKernel(wasm,{fonts:{[key]:noKerning}});
  const reduction=width('AV',key,unkerned)-width('AV',key);
  assert.ok(reduction>.01,`${key} did not apply its AV kerning`);
  reductions.push(Math.round(reduction*100));
 }
 assert.ok(new Set(reductions).size>=5,'the pair adjustment depends on the typeface');
 const mono='space-mono-bold', face=fonts[mono], letter=face.charToGlyph('A').getPath(0,0,1).getBoundingBox();
 const expected=12*face.charToGlyph(' ').advanceWidth/face.unitsPerEm/(letter.y2-letter.y1);
 near(width('A A',mono)-width('AA',mono),expected,.025);
 near(width('A\u030A',mono),width('Å',mono));
 assert.throws(()=>width('A😀',mono),error=>error.code==='ugyldig_tekst');
});

test('name badge unions real text then cuts a six millimetre hole, all one closed solid',()=>{
 const plate={id:'plate',kind:'box',size:[70,24,3],position:[0,0,1.5],mode:'solid'};
 const label=text('ÆØÅ',{position:[3,0,3.6],params:{text:'ÆØÅ',font:'mkv-sans',height:8,depth:2}});
 const hole={id:'hole',kind:'cylinder',size:[6,6,8],position:[-28,0,2],mode:'hole'};
 const base=kernel.evaluateScene(scene([plate,label,group(['plate','text'])],['g']));
 const model=scene([plate,label,hole,group(['plate','text','hole'])],['g']),before=JSON.stringify(model),result=kernel.evaluateScene(model);
 assert.equal(result.parts,1);assert.ok(result.volume>4000);near(base.volume-result.volume,3*9*16*Math.sin(2*Math.PI/32),0.01);closedSTL(result);assert.equal(JSON.stringify(model),before);
});

test('local millimetre dimensions survive rotation/nonuniform matrix with exact metadata',()=>{
 const original=kernel.evaluateScene(scene([text('Æøå')]));
 const node=text('Æøå',{matrix:[0,2,0,0,-1,0,0,0,0,0,1,0,3,4,1,1]});
 const result=kernel.evaluateScene(scene([node]));near(result.volume,original.volume*2);assert.deepEqual(result.meshes[0].naturalSize,original.meshes[0].naturalSize);
 near(result.bounds.max[0]-result.bounds.min[0],12);near(result.bounds.max[1]-result.bounds.min[1],original.meshes[0].naturalSize[0]*2);closedSTL(result);
});

test('loose root holes do not cut preview and both metadata and exporter block download',()=>{
 const plate={id:'p',kind:'box',size:[10,10,10],position:[0,0,5],mode:'solid'},hole={id:'h',kind:'cylinder',size:[6,6,12],position:[0,0,5],mode:'hole'};
 const result=kernel.evaluateScene(scene([plate,hole]));near(result.volume,1000);assert.deepEqual(result.uncombinedHoles,['h']);assert.equal(result.exportBlocked,true);
 assert.throws(()=>exportBinaryStl(result),e=>e.code==='ukombinerede_huller');
 const combined=kernel.evaluateScene(scene([plate,hole,group(['p','h'])],['g']));assert.equal(combined.exportBlocked,false);assert.ok(combined.volume<1000);closedSTL(combined);
});

test('blank, long, unsupported and invalid text fail safely; version one is explicitly rejected',()=>{
 for(const [value,code]of [[' ','tom_tekst'],['x'.repeat(41),'for_lang_tekst'],['😀','ugyldig_tekst'],['a\nb','ugyldig_tekst']])assert.throws(()=>kernel.evaluateScene(scene([text(value)])),e=>e.code===code);
 for(const params of [{text:'A',font:'mkv-sans',height:0,depth:2},{text:'A',font:'mkv-sans',height:NaN,depth:2}])assert.throws(()=>kernel.evaluateScene(scene([text('A',{params})])),e=>e.code==='ugyldige_maal');
 assert.throws(()=>kernel.evaluateScene({...scene([]),version:1}),e=>e.code==='ugyldig_scene');
});

test('actual text CrossSections and Manifolds are disposed on success and downstream failure',()=>{
 const live=new Set();function track(object){if(live.has(object))return object;live.add(object);const remove=object.delete.bind(object);object.delete=()=>{assert.ok(live.delete(object));remove();};
 for(const method of ['transform','extrude'])if(object[method]){const call=object[method].bind(object);object[method]=(...args)=>track(call(...args));}
 if(object.decompose){const call=object.decompose.bind(object);object.decompose=()=>call().map(track);}return object;}
 function Manifold(...args){return track(new wasm.Manifold(...args));}for(const name of ['cube','sphere','cylinder','union','difference'])Manifold[name]=(...args)=>track(wasm.Manifold[name](...args));
 function CrossSection(...args){return track(new wasm.CrossSection(...args));}
 const tracked=createGeometryKernel({...wasm,Manifold,CrossSection},{fonts:{'mkv-sans':font}});
 tracked.evaluateScene(scene([text('Æøå')]));assert.equal(live.size,0);
 const cut={id:'cut',kind:'box',mode:'hole',size:[200,200,20],position:[0,0,1]};assert.throws(()=>tracked.evaluateScene(scene([text('Æøå'),cut,group(['text','cut'])],['g'])));assert.equal(live.size,0);
});
