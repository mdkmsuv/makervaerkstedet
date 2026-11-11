import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
// Same bundling as model3d-scene.test.mjs: optional dependency root and source root as arguments.
const root = fileURLToPath(new URL('../', import.meta.url));
const [dependencies = root, source = root] = process.argv.slice(2);
const require = createRequire(path.join(dependencies, 'package.json'));
const {build} = require('esbuild');
const load = async file => {
  const bundle = await build({entryPoints: [path.join(source, file)], nodePaths: [path.join(dependencies, 'node_modules')], bundle: true, platform: 'node', format: 'esm', write: false});
  return import('data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].text).toString('base64'));
};
const H = await load('web/model3d/handles.js'), S = await load('web/model3d/scene.js');
const checks = [], check = (name, fn) => { fn(); checks.push(name); };
const box = {min: [-10, -10, -10], max: [10, 10, 10]}, near = (a, b, t = 1e-9) => assert(Math.abs(a - b) < t, `${a} != ${b}`);

check('nine size handles: four bottom corners, four bottom sides and the top', () => {
  const handles = H.resizeHandles(box);
  assert.equal(handles.length, 9); assert.equal(handles.filter(h => h.corner).length, 4);
  assert(handles.filter(h => !h.scale[2]).every(h => h.at[2] === -10)); assert.deepEqual(handles.at(-1).at, [0, 0, 10]);
});
check('the opposite side and the bottom stay put', () => {
  assert.deepEqual(H.handlePivot(box, [1, 0, 0]), [-10, 0, -10]);
  assert.deepEqual(H.handlePivot(box, [1, -1, 0]), [-10, 10, -10]);
  assert.deepEqual(H.handlePivot(box, [0, 0, 1]), [0, 0, -10]);
});
check('a side handle follows the pointer in whole millimetres', () => {
  assert.deepEqual(H.resizeFactors(box, [1, 1, 1], [1, 0, 0], [5.4, 0, 0], {snap: 1}).size, [25, 20, 20]);
  assert.deepEqual(H.resizeFactors(box, [1, 1, 1], [-1, 0, 0], [-5.4, 0, 0], {snap: 1}).size, [25, 20, 20]);
  assert.deepEqual(H.resizeFactors(box, [1, 1, 1], [1, 0, 0], [3, 0, 0], {snap: 5}).size, [25, 20, 20]);
  // A stretched part: one local unit is 2 mm.
  assert.deepEqual(H.resizeFactors(box, [2, 1, 1], [1, 0, 0], [5, 0, 0]).size, [50, 20, 20]);
});
check('Shift keeps the shape: the axis that changed most sets one factor', () => {
  const {factor, size} = H.resizeFactors(box, [1, 1, 1], [1, 1, 0], [10, 2, 0], {snap: 1, uniform: true});
  assert.deepEqual(factor, [1.5, 1.5, 1.5]); assert.deepEqual(size, [30, 30, 30]);
});
check('sizes stay within 0,1 and 1000 mm', () => {
  near(H.resizeFactors(box, [1, 1, 1], [1, 0, 0], [-500, 0, 0]).size[0], 1);
  near(H.resizeFactors(box, [1, 1, 1], [0, 0, 1], [0, 0, 5000]).size[2], 1000);
  const uniform = H.resizeFactors({min: [0, 0, 0], max: [500, 10, 10]}, [1, 1, 1], [0, 1, 0], [0, 100, 0], {uniform: true});
  assert(uniform.size.every(value => value <= 1000 + 1e-9), 'uniform growth must stop at 1000 mm');
});
check('angles snap and stay continuous across ±180°', () => {
  assert.equal(H.snapAngle(37, 15), 30); assert.equal(H.snapAngle(37, 45), 45); assert.equal(H.snapAngle(37, 0), 37);
  assert.equal(H.unwrap(170, -170), 190); assert.equal(H.unwrap(-170, 170), -190);
  const z = H.ROTATION_AXES[2];
  for (const plane of H.ROTATION_AXES) assert.equal(plane.e1.clone().cross(plane.e2).dot(plane.axis), 1, 'e1 × e2 must be the axis');
  assert.equal(z.name, 'Z');
});
check('turning a cube 45° about X lowers its corners; the shift keeps the bottom', () => {
  const positions = new Float32Array([-10, -10, 0, 10, -10, 0, -10, 10, 0, 10, 10, 0, -10, -10, 20, 10, -10, 20, -10, 10, 20, 10, 10, 20]);
  const center = {x: 0, y: 0, z: 10}, turn = H.rotationAbout({...center}, H.ROTATION_AXES[0].axis, 45);
  near(H.lowestZ(positions, turn), 10 - 10 * Math.SQRT2, 1e-5);
});
check('typed angles keep scale and centre; the lift moves the result up', () => {
  const model = new S.SceneModel(), id = model.add('box'), local = {min: [-10, -10, -10], max: [10, 10, 10]};
  model.resize(id, 0, 40, local);
  assert.equal(model.rotate(id, [0, 0, 90], local, 3), true);
  const m = S.objectMatrix(model.object(id)).elements;
  near(Math.hypot(m[0], m[1], m[2]), 2, 1e-9); near(m[14], 13, 1e-9);
});
check('a thicker text keeps its bottom where it was', () => {
  const model = new S.SceneModel(), id = model.add('text', {position: [0, 0], bottom: 3.5, params: {depth: 1.5}});
  const bottom = () => S.objectMatrix(model.object(id)).elements[14] - model.object(id).params.depth / 2;
  near(bottom(), 3.5); assert.equal(model.setText(id, {depth: 4}), true); near(bottom(), 3.5);
  assert.equal(model.setText(id, {text: 'Ida'}), true); assert.equal(model.object(id).params.text, 'Ida'); near(bottom(), 3.5);
  assert.equal(model.setText(model.add('box'), {depth: 3}), false);
});
check('Spejlvend mirrors about the given centre and keeps locked parts; a transformed paste repeats a step', () => {
  const model = new S.SceneModel(), id = model.add('wedge', [10, 0]);
  assert.equal(model.mirror(0, [0, 0, 10]), true);
  const m = S.objectMatrix(model.object(id)); assert(m.determinant() < 0, 'a mirror flips the handedness'); near(m.elements[12], -10);
  model.flag('locked', true); assert.equal(model.mirror(0, [0, 0, 10]), false, 'a locked part is not mirrored');
  model.flag('locked', false); model.copy(); model.pasteTransformed(S.objectMatrix({position: [25, 0, 0], rotation: [0, 0, 0]}));
  near(S.objectMatrix(model.object(model.selected[0])).elements[12], 15);
});
console.log(JSON.stringify({result: 'PASS', checks, count: checks.length}));
