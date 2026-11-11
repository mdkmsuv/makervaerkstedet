// Every shape in the palette is a closed solid that fills its 20 mm box, also when it is used as a hole.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createGeometryKernel, exportBinaryStl} from '../web/model3d/geometry.js';

const dependency = process.env.MODEL3D_MANIFOLD_MODULE || 'manifold-3d';
assert.ok(!/^https?:/.test(dependency));
const {default: Module} = await import(dependency), wasm = await Module(); wasm.setup();
const kernel = createGeometryKernel(wasm);
const scene = (objects, roots = objects.map(x => x.id)) => ({format: 'makervaerkstedet-3d', version: 2, units: 'mm', plate: {w: 180, d: 180}, objects, roots});
const part = (kind, extra = {}) => ({id: 'a', kind, mode: 'solid', size: [20, 20, 20], position: [0, 0, 10], rotation: [0, 0, 0], ...extra});

for (const kind of ['box', 'cylinder', 'sphere', 'cone', 'wedge', 'tube', 'roof', 'halfsphere', 'pyramid', 'star', 'heart']) {
  test(`${kind} fills a 20 mm box, stands on the plate and stays closed`, () => {
    const result = kernel.evaluateScene(scene([part(kind)]));
    assert.deepEqual(result.bounds.min.map(v => Math.round(v * 100) / 100), [-10, -10, 0]);
    assert.deepEqual(result.bounds.max.map(v => Math.round(v * 100) / 100), [10, 10, 20]);
    assert.equal(result.parts, 1); assert(result.volume > 0 && result.volume <= 8000 + 1e-6);
    assert(exportBinaryStl(result).byteLength > 84);
  });
}
test('a tube is hollow: about 60 % of a cylinder', () => {
  const volume = kind => kernel.evaluateScene(scene([part(kind)])).volume, ratio = volume('tube') / volume('cylinder');
  assert(ratio > .55 && ratio < .65, `ratio ${ratio}`);
});
test('a half sphere is half a sphere', () => {
  const volume = kind => kernel.evaluateScene(scene([part(kind, kind === 'sphere' ? {size: [20, 20, 40], position: [0, 0, 20]} : {})])).volume;
  const ratio = volume('halfsphere') / volume('sphere'); assert(Math.abs(ratio - .5) < .02, `ratio ${ratio}`);
});
test('a hole pyramid cuts a plate', () => {
  const objects = [{id: 'p', kind: 'box', mode: 'solid', size: [40, 40, 10], position: [0, 0, 5], rotation: [0, 0, 0]}, {...part('pyramid'), id: 'h', mode: 'hole'}];
  const result = kernel.evaluateScene(scene([...objects, {id: 'g', kind: 'group', op: 'difference', mode: 'solid', children: ['p', 'h']}], ['g']));
  assert(result.volume < 16000 - 100, `volume ${result.volume}`); assert.equal(result.exportBlocked, false); assert.equal(result.parts, 1);
});
test('a hole that misses the solid parts is reported; one that cuts is not', () => {
  const plate = {id: 'p', kind: 'box', mode: 'solid', size: [40, 40, 10], position: [0, 0, 5], rotation: [0, 0, 0]};
  const combined = hole => kernel.evaluateScene(scene([plate, hole, {id: 'g', kind: 'group', op: 'difference', mode: 'solid', children: ['p', 'h']}], ['g']));
  assert.deepEqual(combined({...part('cylinder'), id: 'h', mode: 'hole', position: [60, 0, 10]}).missedHoles, ['h']);
  assert.deepEqual(combined({...part('cylinder'), id: 'h', mode: 'hole', position: [0, 0, 10]}).missedHoles, []);
});
test('a combined part keeps the colours of its parts for display; its own colour paints it all', () => {
  const plate = {id: 'p', kind: 'box', mode: 'solid', size: [40, 20, 4], position: [0, 0, 2], rotation: [0, 0, 0], color: '#c92e35'};
  const knob = {id: 'k', kind: 'cylinder', mode: 'solid', size: [10, 10, 6], position: [0, 0, 6], rotation: [0, 0, 0], color: '#e9ecee'};
  const hole = {id: 'h', kind: 'cylinder', mode: 'hole', size: [4, 4, 20], position: [15, 0, 5], rotation: [0, 0, 0]};
  const group = extra => kernel.evaluateScene(scene([plate, knob, hole, {id: 'g', kind: 'group', op: 'difference', mode: 'solid', children: ['p', 'k', 'h'], ...extra}], ['g']));
  const result = group({}), paints = result.meshes[0].paints;
  assert.deepEqual(paints.map(x => x.color), ['#c92e35', '#e9ecee']);
  assert.equal(result.parts, 1); assert(exportBinaryStl(result).byteLength > 84);
  assert.equal(group({color: '#2f6fb5'}).meshes[0].paints, undefined);
  assert.throws(() => group({color: 'red; x'}));
});
test('a hole past the edge is a notch; parts that touch nothing are named', () => {
  const plate = {id: 'p', kind: 'box', mode: 'solid', size: [40, 40, 10], position: [0, 0, 5], rotation: [0, 0, 0]};
  const cut = x => kernel.evaluateScene(scene([plate, {...part('cylinder'), id: 'h', mode: 'hole', position: [x, 0, 10]}, {id: 'g', kind: 'group', op: 'difference', mode: 'solid', children: ['p', 'h']}], ['g']));
  assert.deepEqual(cut(15).edgeHoles, ['h']); assert.deepEqual(cut(0).edgeHoles, []);
  const apart = kernel.evaluateScene(scene([plate, part('box', {id: 'b', position: [60, 0, 10]}), part('box', {id: 'c', position: [20, 0, 15]})]));
  assert.deepEqual(apart.looseRoots, ['b']);
});
test('an imported STL triangle list becomes a closed part of the size asked for; an open one is refused', () => {
  const tube = kernel.evaluateScene(scene([part('tube')])), stl = new DataView(exportBinaryStl(tube)), count = stl.getUint32(80, true), soup = new Float32Array(count * 9);
  for (let f = 0; f < count; f++) for (let c = 0; c < 3; c++) for (let a = 0; a < 3; a++) soup[f * 9 + c * 3 + a] = stl.getFloat32(84 + 50 * f + 12 + c * 12 + a * 4, true);
  const assets = new Map([['stl-tube', soup], ['stl-open', soup.slice(0, soup.length - 9)]]);
  const imported = kernel.evaluateScene(scene([{id: 'm', kind: 'mesh', asset: 'stl-tube', mode: 'solid', size: [40, 40, 20], position: [0, 0, 10], rotation: [0, 0, 0]}]), assets);
  assert(Math.abs(imported.volume - tube.volume * 4) < 1, `volume ${imported.volume} vs ${tube.volume * 4}`);
  assert.deepEqual(imported.bounds.max.map(v => Math.round(v)), [20, 20, 20]);
  assert.throws(() => kernel.evaluateScene(scene([{id: 'm', kind: 'mesh', asset: 'stl-open', mode: 'solid', size: [20, 20, 20], position: [0, 0, 10], rotation: [0, 0, 0]}]), assets), /ikke en lukket figur/);
  assert.throws(() => kernel.evaluateScene(scene([{id: 'm', kind: 'mesh', asset: 'stl-none', mode: 'solid', size: [20, 20, 20], position: [0, 0, 10], rotation: [0, 0, 0]}]), assets), /mangler/);
});
test('a box with round corners keeps a true radius when it is stretched', () => {
  const plain = part('box', {size: [20, 20, 4], matrix: [3.5,0,0,0, 0,1.3,0,0, 0,0,1,0, 0,0,2,1]});
  const rounded = {...plain, params: {round: 5}};
  const a = kernel.evaluateScene(scene([plain])), b = kernel.evaluateScene(scene([rounded]));
  assert.deepEqual(b.bounds.max.map(v => Math.round(v * 100) / 100), [35, 13, 4]);
  // Four corners of radius 5 remove (4 - π) · 5² mm² of the 70 × 26 outline, times the height 4.
  const expected = (70 * 26 - (4 - Math.PI) * 25) * 4; assert(Math.abs(b.volume - expected) < 4, `volume ${b.volume} vs ${expected}`);
  assert(a.volume > b.volume); assert.throws(() => kernel.evaluateScene(scene([{...plain, params: {round: -1}}])));
});
test('a star and a heart are flat outlines with less material than their box', () => {
  for (const [kind, low, high] of [['star', .3, .55], ['heart', .6, .85]]) {
    const ratio = kernel.evaluateScene(scene([part(kind)])).volume / 8000; assert(ratio > low && ratio < high, `${kind} ratio ${ratio}`);
  }
});
test('a cylinder and a cone with flat sides fill their box; a triangle has about half the area', () => {
  for (const sides of [3, 4, 6, 5]) for (const kind of ['cylinder', 'cone']) {
    const result = kernel.evaluateScene(scene([part(kind, {params: {sides}})]));
    assert.deepEqual(result.bounds.min.map(v => Math.round(v * 100) / 100), [-10, -10, 0], `${kind} ${sides}`);
    assert.deepEqual(result.bounds.max.map(v => Math.round(v * 100) / 100), [10, 10, 20], `${kind} ${sides}`);
  }
  const prism = kernel.evaluateScene(scene([part('cylinder', {params: {sides: 3}})])).volume; assert(Math.abs(prism - 4000) < 1, `triangle ${prism}`);
  assert.throws(() => kernel.evaluateScene(scene([part('cylinder', {params: {sides: 2}})])));
});
test('a tube wall is given in millimetres, also when the tube is stretched', () => {
  const tube = part('tube', {size: [20, 20, 10], matrix: [2,0,0,0, 0,2,0,0, 0,0,1,0, 0,0,5,1], params: {wall: 2}});
  const result = kernel.evaluateScene(scene([tube])), expected = Math.PI * (20 * 20 - 18 * 18) * 10;
  assert(Math.abs(result.volume - expected) / expected < .01, `volume ${result.volume} vs ${expected}`);
  assert.throws(() => kernel.evaluateScene(scene([{...tube, params: {wall: 0}}])));
});
test('a drawing becomes a flat part: outer outlines filled, holes cut', () => {
  // A 10 × 10 square with a 4 × 4 square hole (turning the other way), packed as [count, x, y, …].
  const data = new Float32Array([4, 0,0, 10,0, 10,10, 0,10, 4, 3,3, 3,7, 7,7, 7,3]);
  const part = {id: 'd', kind: 'mesh', asset: 'svg-test', mode: 'solid', size: [40, 40, 3], position: [0, 0, 1.5], rotation: [0, 0, 0]};
  const result = kernel.evaluateScene(scene([part]), new Map([['svg-test', data]]));
  assert(Math.abs(result.volume - (1600 - 16 * 16) * 3) < 1, `volume ${result.volume}`);
  assert.deepEqual(result.bounds.max.map(v => Math.round(v * 100) / 100), [20, 20, 3]);
  assert.throws(() => kernel.evaluateScene(scene([part]), new Map([['svg-test', new Float32Array([2, 0, 0, 1, 1])]])));
});
test('a drawn line becomes a band as wide as the line, with round ends', () => {
  // One straight line 10 long and 2 wide: a 10 × 2 band with two half-circle ends, so 12 × 2 in all.
  const data = new Float32Array([-2, 2, 0, 0, 10, 0]);
  const part = {id: 'l', kind: 'mesh', asset: 'svg-line', mode: 'solid', size: [12, 2, 1], position: [0, 0, .5], rotation: [0, 0, 0]};
  const result = kernel.evaluateScene(scene([part]), new Map([['svg-line', data]])), expected = 10 * 2 + Math.PI;
  assert(Math.abs(result.volume - expected) < .3, `volume ${result.volume} vs ${expected}`);
});
