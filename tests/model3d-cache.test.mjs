// The worker's cache must give exactly the same results as a fresh calculation, stay within its size and make moves cheap.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parse} from 'opentype.js/dist/opentype.mjs';
import {createGeometryKernel, exportBinaryStl} from '../web/model3d/geometry.js';

const dependency = process.env.MODEL3D_MANIFOLD_MODULE || 'manifold-3d';
assert.ok(!/^https?:/.test(dependency));
const {default: Module} = await import(dependency), wasm = await Module(); wasm.setup();
const bytes = await readFile(new URL('../web/assets/model3d/Metrophobic-Regular.ttf', import.meta.url));
const font = parse(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
const fresh = createGeometryKernel(wasm, {fonts: {'mkv-sans': font}}), cached = createGeometryKernel(wasm, {fonts: {'mkv-sans': font}, cache: 6});
const scene = (objects, roots) => ({format: 'makervaerkstedet-3d', version: 2, units: 'mm', plate: {w: 180, d: 180}, objects, roots});
const at = (x, y = 0, z = 0) => [1,0,0,0, 0,1,0,0, 0,0,1,0, x,y,z,1];
function nameplate(id, x, text = 'Klostermarksskolen') {
  return [
    {id: `${id}p`, kind: 'box', mode: 'solid', size: [160, 40, 4], position: [0, 0, 2], rotation: [0, 0, 0], color: '#c92e35'},
    {id: `${id}t`, kind: 'text', mode: 'solid', params: {text, font: 'mkv-sans', height: 12, depth: 1.5}, position: [0, 0, 4.25], rotation: [0, 0, 0], color: '#e9ecee'},
    {id: `${id}h`, kind: 'cylinder', mode: 'hole', size: [6, 6, 20], position: [-70, 0, 5], rotation: [0, 0, 0]},
    {id, kind: 'group', op: 'difference', mode: 'solid', children: [`${id}p`, `${id}t`, `${id}h`], matrix: at(x)},
  ];
}
const summary = result => ({volume: Math.round(result.volume * 1000) / 1000, parts: result.parts, stl: exportBinaryStl(result).byteLength,
  meshes: result.meshes.map(mesh => ({id: mesh.id, n: mesh.positions.length, first: [...mesh.positions.slice(0, 6)].map(v => Math.round(v * 1000) / 1000),
    paints: mesh.paints?.map(paint => [paint.color, paint.positions.length]) ?? null}))});

test('cached and fresh results are identical through moves, copies and a changed text', () => {
  const steps = [
    scene(nameplate('a', 0), ['a']),
    scene(nameplate('a', 7), ['a']),
    scene(nameplate('a', 7).map(node => node.id === 'a' ? {...node, matrix: [-1,0,0,0, 0,1,0,0, 0,0,1,0, 7,0,0,1]} : node), ['a']),
    scene([...nameplate('a', 0), ...nameplate('b', 0).map(node => node.id === 'b' ? {...node, matrix: at(0, 50)} : node)], ['a', 'b']),
    scene(nameplate('a', 0, 'Ida'), ['a']),
  ];
  for (const step of steps) assert.deepEqual(summary(cached.evaluateScene(step)), summary(fresh.evaluateScene(step)));
  assert(cached.cacheSize <= 6);
  cached.clearCache(); assert.equal(cached.cacheSize, 0);
});

test('moving a combined part reuses its booleans and is clearly faster', () => {
  const kernel = createGeometryKernel(wasm, {fonts: {'mkv-sans': font}, cache: 12}), time = x => { const t = performance.now(); kernel.evaluateScene(scene(nameplate('a', x), ['a'])); return performance.now() - t; };
  const first = time(0), moved = Math.min(time(5), time(10), time(15));
  assert(moved < first * .6, `a move took ${moved.toFixed(0)} ms after a first calculation of ${first.toFixed(0)} ms`);
  kernel.clearCache();
});
