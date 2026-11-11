import test from 'node:test';
import assert from 'node:assert/strict';
import {createGeometryKernel,exportBinaryStl,GeometryError} from '../web/model3d/geometry.js';
import {validateSTL} from '../web/stl-validation.mjs';

// CI/local callers may supply an already verified package URL; never a network URL.
const dependency=process.env.MODEL3D_MANIFOLD_MODULE || 'manifold-3d';
assert.ok(!/^https?:/.test(dependency));
const {default:Module}=await import(dependency), wasm=await Module(); wasm.setup();
const kernel=createGeometryKernel(wasm);
const I=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];
const primitive=(id,kind='box',extra={})=>({id,kind,mode:'solid',size:[10,10,10],position:[0,0,5],rotation:[0,0,0],...extra});
const scene=(objects,roots=objects.map(x=>x.id))=>({format:'makervaerkstedet-3d',version:2,units:'mm',plate:{w:180,d:180},objects,roots});
const combined=objects=>scene([...objects,{id:'combined',kind:'group',op:'difference',mode:'solid',children:objects.map(x=>x.id)}],['combined']);
const near=(actual,expected,tolerance=1e-5)=>assert.ok(Math.abs(actual-expected)<=tolerance,`${actual} != ${expected}`);
function watertight(buffer) {
  const data=new DataView(buffer), edges=new Map(), triangles=data.getUint32(80,true);
  let volume=0;
  for(let face=0;face<triangles;face++){
    const points=[0,1,2].map(corner=>[0,1,2].map(axis=>data.getFloat32(84+50*face+12+corner*12+axis*4,true)));
    const [a,b,c]=points;
    volume+=(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6;
    for(let edge=0;edge<3;edge++){
      const start=points[edge].join(','), end=points[(edge+1)%3].join(',');
      const key=[start,end].sort().join('|');
      const value=edges.get(key)||{count:0,balance:0};value.count++;value.balance+=start<end?1:-1;edges.set(key,value);
    }
  }
  for(const edge of edges.values()){assert.equal(edge.count,2);assert.equal(edge.balance,0);}
  return volume;
}

test('inside hole subtracts exact volume and binary STL is closed with correct orientation',()=>{
  const model=combined([primitive('a'),primitive('hole','box',{mode:'hole',size:[2,2,2]})]);
  const result=kernel.evaluateScene(model);near(result.volume,992);
  const stl=exportBinaryStl(result),checked=validateSTL(stl);
  assert.deepEqual(checked.size,[10,10,10]);near(watertight(stl),992);
  assert.equal(new DataView(stl).getUint32(80,true),result.merged.indices.length/3);
});

test('overlapping solids are unioned, while a through hole cuts the exported merged mesh',()=>{
  const result=kernel.evaluateScene(scene([primitive('a'),primitive('b','box',{position:[5,0,5]})]));
  near(result.volume,1500);near(watertight(exportBinaryStl(result)),1500);
  const cut=kernel.evaluateScene(combined([primitive('a'),primitive('h','box',{mode:'hole',size:[2,2,12]})]));
  near(cut.volume,960);near(watertight(exportBinaryStl(cut)),960);
});

test('all five actual WASM primitives are closed, Z-up, finite and nonuniformly sized',()=>{
  for(const kind of ['box','cylinder','sphere','cone','wedge']){
    const result=kernel.evaluateScene(scene([primitive('a',kind,{size:[12,8,6],position:[0,0,3]})]));
    assert.ok(result.volume>0);near(result.bounds.min[2],0);near(result.bounds.max[2],6);
    assert.deepEqual(validateSTL(exportBinaryStl(result)).size,[12,8,6]);
    near(watertight(exportBinaryStl(result)),result.volume,0.001);
  }
});

test('intrinsic XYZ degrees and centre position produce correct transformed bounds',()=>{
  const result=kernel.evaluateScene(scene([primitive('a','box',{size:[2,4,6],position:[10,20,30],rotation:[90,90,0]})]));
  for(let axis=0;axis<3;axis++)near(result.bounds.max[axis]-result.bounds.min[axis],[6,2,4][axis]);
  assert.deepEqual(result.bounds.min.map(x=>Math.round(x)),[7,19,28]);near(result.volume,48);
});

test('nonuniform group scale plus rotated child survives exact affine ungroup with shear',()=>{
  const child=primitive('a','box',{size:[10,5,4],position:[0,0,2],rotation:[0,0,45]});
  const group={id:'g',kind:'group',op:'union',mode:'solid',children:['a'],matrix:[2,0,0,0,0,1,0,0,0,0,1,0,7,-3,0,1]};
  const model=scene([child,group],['g']),before=JSON.stringify(model),grouped=kernel.evaluateScene(model);
  const t=Math.SQRT1_2;
  const flat=primitive('a','box',{size:[10,5,4],matrix:[2*t,t,0,0,-2*t,t,0,0,0,0,1,0,7,-3,2,1]});
  const ungrouped=kernel.evaluateScene(scene([flat]));
  near(grouped.volume,400);near(ungrouped.volume,400);
  for(const edge of ['min','max'])for(let axis=0;axis<3;axis++)near(grouped.bounds[edge][axis],ungrouped.bounds[edge][axis]);
  near(watertight(exportBinaryStl(ungrouped)),400,0.001);
  assert.equal(JSON.stringify(model),before);
});

test('editable nested groups respect hole mode changes and group mode at outer level',()=>{
  const a=primitive('a'),hole=primitive('h','box',{size:[2,2,2]});
  const group={id:'g',kind:'group',op:'union',children:['a','h'],mode:'solid',matrix:I};
  const model=scene([a,hole,group],['g']);near(kernel.evaluateScene(model).volume,1000);
  hole.mode='hole';near(kernel.evaluateScene(model).volume,992);
  group.mode='hole';model.objects.push(primitive('outer','box',{size:[20,20,20]}));model.roots.push('outer');
  const container={id:'container',kind:'group',op:'difference',mode:'solid',children:['outer','g']};model.objects.push(container);model.roots=['container'];
  near(kernel.evaluateScene(model).volume,8000-992);
});

test('hidden figures are omitted and empty or pure holes cannot produce export geometry',()=>{
  const result=kernel.evaluateScene(scene([primitive('a'),primitive('h','box',{mode:'hole',hidden:true})]));
  near(result.volume,1000);assert.equal(result.meshes.length,1);
  const hiddenGroup=scene([primitive('a','box',{hidden:true}),{id:'g',kind:'group',op:'union',mode:'solid',children:['a']}],['g']);
  for(const model of [scene([]),scene([primitive('h','box',{mode:'hole'})]),hiddenGroup]){
    const empty=kernel.evaluateScene(model);assert.equal(empty.merged,null);assert.deepEqual(empty.warnings,['tom_model']);
  }
});

test('invalid dimensions, nonfinite or singular matrices and cyclic graphs fail without mutation',()=>{
  for(const size of [[0,1,1],[-1,1,1],[Infinity,1,1],[NaN,1,1]])
    assert.throws(()=>kernel.evaluateScene(scene([primitive('a','box',{size})])),e=>e instanceof GeometryError&&e.code==='ugyldige_maal');
  const singular=I.slice();singular[0]=0;
  assert.throws(()=>kernel.evaluateScene(scene([primitive('a','box',{matrix:singular})])),GeometryError);
  assert.throws(()=>kernel.evaluateScene(scene([{id:'g',kind:'group',op:'union',mode:'solid',children:['g']}],['g'])),GeometryError);
  const tooMany=Array.from({length:513},(_,i)=>primitive('a'+i));
  assert.throws(()=>kernel.evaluateScene(scene(tooMany)),GeometryError);
});

test('empty subtraction gives a safe error; a later valid scene still works',()=>{
  const group={id:'g',kind:'group',op:'difference',mode:'solid',children:['a','h']};
  const invalid=scene([primitive('a'),primitive('h','box',{mode:'hole',size:[12,12,12]}),group],['g']);
  assert.throws(()=>kernel.evaluateScene(invalid),e=>e.code==='tomt_resultat'&&!/WASM|Manifold|stack/i.test(e.message));
  near(kernel.evaluateScene(scene([primitive('a')])).volume,1000);
});

test('every owned WASM solid is deleted on success and on boolean failure',()=>{
  const live=new Set();
  function track(solid){
    if(live.has(solid))return solid;
    live.add(solid);
    const remove=solid.delete.bind(solid),transform=solid.transform.bind(solid),decompose=solid.decompose.bind(solid);
    solid.delete=()=>{assert.ok(live.delete(solid));remove();};
    solid.transform=(...args)=>track(transform(...args));solid.decompose=()=>decompose().map(track);
    return solid;
  }
  function Tracked(...args){return track(new wasm.Manifold(...args));}
  for(const name of ['cube','sphere','cylinder','union','difference'])Tracked[name]=(...args)=>track(wasm.Manifold[name](...args));
  const tracked=createGeometryKernel({...wasm,Manifold:Tracked});
  tracked.evaluateScene(scene([primitive('a'),primitive('b','sphere')]));assert.equal(live.size,0);
  assert.throws(()=>tracked.evaluateScene(combined([primitive('a'),primitive('h','box',{mode:'hole',size:[20,20,20]})])));
  assert.equal(live.size,0);
});
