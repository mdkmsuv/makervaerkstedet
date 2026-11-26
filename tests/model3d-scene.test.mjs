import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
const [dependencies=root, source=root] = process.argv.slice(2);
const require = createRequire(path.join(dependencies, 'package.json'));
const {build} = require('esbuild');
const bundle = await build({entryPoints:[path.join(source,'apps/3dprint/web/model3d/scene.js')],nodePaths:[path.join(dependencies,'node_modules')],bundle:true,platform:'node',format:'esm',write:false});
const {SceneModel,objectMatrix,findFreeSpot,PLATE_SIZE} = await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const model = new SceneModel(), id=model.add('box'), box={min:[-10,-10,-10],max:[10,10,10]};
assert.equal(model.data.version,2);
assert.deepEqual([0,5,10].map(i=>objectMatrix(model.object(id)).elements[i]),[1,1,1]);
for(const [axis,size] of [[0,70],[1,26],[2,4]])model.resize(id,axis,size,box);
model.rotate(id,[0,0,37],box); model.resize(id,0,90,box);
const elements=objectMatrix(model.object(id)).elements;
const columns=[0,1,2].map(i=>elements.slice(i*4,i*4+3));
const length=v=>Math.hypot(...v), dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
assert(Math.abs(dot(columns[0],columns[1]))<1e-10,'local resize introduced shear');
assert(Math.abs(Math.atan2(columns[0][1],columns[0][0])*180/Math.PI-37)<1e-9,'resize changed rotation');
assert.deepEqual(columns.map(v=>Math.round(length(v)*2000)/100),[90,26,4]);
const cutter=model.add('cylinder');model.mode('hole');model.selected=[id,cutter];model.group();
const group=model.selected[0];model.applyTransform(objectMatrix({position:[7,9,2],rotation:[0,0,13]}));
const parent=objectMatrix(model.object(group)), expected=parent.clone().multiply(objectMatrix(model.object(cutter))).toArray();
model.ungroup();assert.equal(model.object(cutter).mode,'hole');assert.deepEqual(model.object(cutter).matrix,expected);
model.undo();assert.equal(model.object(group).kind,'group');assert.deepEqual(model.selected,[group]);
model.redo();assert.equal(model.object(group),undefined);assert.equal(model.object(cutter).mode,'hole');
model.object(cutter).matrix=[1,0,0,0,.5,1,0,0,0,0,1,0,0,0,0,1];
const sheared=model.snapshot();assert.equal(model.rotate(cutter,[0,0,45],box),false);assert.equal(model.snapshot(),sheared,'Rotation silently removed existing shear');
// New parts land on a free spot beside the work, never inside it.
assert.equal(PLATE_SIZE,180);
assert.deepEqual(findFreeSpot([20,20],[]),[0,0]);
assert.deepEqual(findFreeSpot([20,20],[{min:[-10,-10],max:[10,10]}]),[25,0]);
const crowded=[{min:[-10,-10],max:[10,10]},{min:[15,-10],max:[35,10]}], spot=findFreeSpot([20,20],crowded);
assert(crowded.every(b=>spot[0]+14<=b.min[0]||spot[0]-14>=b.max[0]||spot[1]+14<=b.min[1]||spot[1]-14>=b.max[1]),'free spot overlaps an existing part');
assert.deepEqual(findFreeSpot([200,200],[{min:[-10,-10],max:[10,10]}]),[0,0],'a part larger than the plate falls back to the centre');
assert.deepEqual(findFreeSpot([20,20],[{min:[-35,-13],max:[35,13]}]),[0,30],'a new part goes behind a name plate, never in front of it');
// add() keeps the legacy [x, y] form and can stand a text on a surface.
const placed=new SceneModel(), legacy=placed.add('box',[5,6]); assert.deepEqual(placed.object(legacy).position,[5,6,10]);
const label=placed.add('text',{position:[3,4],bottom:3.5,params:{height:10,depth:1.5}}), text=placed.object(label);
assert.deepEqual(text.position,[3,4,4.25]); assert.deepEqual(text.params,{text:'Navn',font:'mkv-sans',height:10,depth:1.5});
const unchanged=structuredClone(text);
assert.equal(placed.setText(label,{font:'bungee'}),true);
assert.deepEqual(placed.object(label),{...unchanged,params:{...unchanged.params,font:'bungee'}});
assert.equal(placed.setText(label,{font:'../example.ttf'}),false);
assert.equal(placed.add('text',{params:{font:'https://example.test/font.ttf'}}),null);
placed.undo(); assert.deepEqual(placed.object(label),unchanged);
placed.redo(); assert.equal(placed.object(label).params.font,'bungee');
// Typing a height keeps the bottom on the plate; a handle pivot keeps the opposite side in place.
const sized=new SceneModel(), part=sized.add('box'), local={min:[-10,-10,-10],max:[10,10,10]};
assert.equal(sized.resize(part,2,40,local),true); let m=objectMatrix(sized.object(part)).elements;
assert.equal(m[10],2); assert.equal(m[14]-2*10,0,'resized part left the plate');
assert.equal(sized.resize(part,0,30,local,[-10,0,-10]),true); m=objectMatrix(sized.object(part)).elements;
assert.equal(m[12]-1.5*10,-10,'left side moved while the right handle was dragged');
assert.equal(sized.resize(part,0,0,local),false); assert.equal(sized.resize(part,0,Number.NaN,local),false);

// A legacy cylinder has its own base size and rotation, without a matrix until first edited.
const circles = new SceneModel(), circle = circles.add('cylinder', {mode: 'hole', size: [20, 20, 4], position: [12, -7], bottom: 3});
circles.object(circle).rotation = [0, 0, 37];
const legacyCircle = circles.snapshot(), beforeDiameter = circles.undoStack.length;
assert.equal(circles.setDiameter(circle, 7.5), true);
const changedCircle = objectMatrix(circles.object(circle)).elements;
assert.deepEqual(changedCircle.slice(12, 15), [12, -7, 5], 'the centre and bottom remain at the same positions');
assert.equal(changedCircle[10], 1, 'height is unchanged');
assert(Math.abs(Math.hypot(changedCircle[0], changedCircle[1]) * 20 - 7.5) < 1e-9);
assert(Math.abs(Math.hypot(changedCircle[4], changedCircle[5]) * 20 - 7.5) < 1e-9);
assert(Math.abs(Math.atan2(changedCircle[1], changedCircle[0]) * 180 / Math.PI - 37) < 1e-9);
assert.equal(circles.undoStack.length, beforeDiameter + 1, 'both axes are edited in one history step');
const resizedCircle = circles.snapshot();
circles.undo(); assert.equal(circles.snapshot(), legacyCircle);
circles.redo(); assert.equal(circles.snapshot(), resizedCircle);
for (const options of [{size: [20, 13, 4]}, {params: {sides: 8}}, {matrix: objectMatrix({rotation: [24, 0, 0]}).toArray()}, {matrix: [1,0,0,0,.5,1,0,0,0,0,1,0,0,0,0,1]}]) {
  const different = circles.add('cylinder', options);
  assert.equal(circles.setDiameter(different, 10), false, 'the diameter command refuses an ellipse, polygon, angled or sheared cylinder');
}
const mirrored = circles.add('cylinder', {matrix: [-1,0,0,0,0,1,0,0,0,0,-1,0,0,0,10,1]});
assert.equal(circles.setDiameter(mirrored, 10), true, 'an upright cylinder may be mirrored or stand upside down');
assert.equal(circles.setDiameter(mirrored, 10), false, 'entering the existing diameter creates no duplicate history step');
circles.flag('locked', true);
assert.equal(circles.setDiameter(mirrored, 8), false, 'locked cylinders cannot be resized');

const oval = circles.add('cylinder', {mode: 'hole', size: [8, 8, 4]});
const roundShape = circles.snapshot();
assert.equal(circles.setCylinderSize(oval, {width: 5}), true);
let ovalMatrix = objectMatrix(circles.object(oval)).elements;
assert.deepEqual([ovalMatrix[0] * 8, ovalMatrix[5] * 8, ovalMatrix[10] * 4], [8, 5, 4], 'an edit of the width does not change the length or height');
circles.undo(); assert.equal(circles.snapshot(), roundShape, 'one undo restores the diameter');
circles.redo();
const ovalShape = circles.snapshot();
assert.equal(circles.setCylinderSize(oval, {width: 8}), true);
ovalMatrix = objectMatrix(circles.object(oval)).elements;
assert.deepEqual([ovalMatrix[0] * 8, ovalMatrix[5] * 8, ovalMatrix[10] * 4], [8, 8, 4], 'making the ellipse round keeps its length');
circles.undo(); assert.equal(circles.snapshot(), ovalShape, 'one undo restores the 8 x 5 ellipse');

const faceted = new SceneModel();
for (const kind of ['sphere', 'halfsphere']) {
  const id = faceted.add(kind), original = faceted.snapshot();
  for (const invalid of [4, 9, 100, 40.5, NaN]) assert.equal(faceted.setFacets(id, invalid), false);
  assert.equal(faceted.snapshot(), original);
  assert.equal(faceted.setFacets(id, 16), true);
  assert.deepEqual(faceted.object(id).params, {facets: 16});
  assert.equal(faceted.setFacets(id, 48), true);
  assert.equal(faceted.snapshot(), original);
  faceted.undo();
  assert.deepEqual(faceted.object(id).params, {facets: 16});
  faceted.flag('locked', true);
  assert.equal(faceted.setFacets(id, 32), false);
}
assert.equal(faceted.setFacets(faceted.add('box'), 16), false);
const checks = ['scene v2 local mm','rotated local size 90x26x4 without shear','rotation preserved','transformed ungroup retains hole and matrix','undo/redo selection and modes','rotation refuses existing shear without changing geometry','free spot beside existing parts','add keeps legacy position and stands text on a surface','typed height keeps the bottom on the plate','handle pivot keeps the opposite side','invalid sizes are refused','diameter changes both axes of a legacy upright cylinder in one undo step','locked, elliptical, tilted, sheared, and faceted cylinders refuse a diameter edit','oval width and restoration can each be undone in one step'];
console.log(JSON.stringify({result: 'PASS', checks, count: checks.length}));
