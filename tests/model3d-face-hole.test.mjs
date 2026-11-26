import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import path from 'node:path';
import {test} from 'node:test';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const [dependencies = root, source = root] = process.argv.slice(2);
const require = createRequire(path.join(dependencies, 'package.json'));
const {build} = require('esbuild');
const {Box3, Matrix4, Vector3} = require('three');
const {outputFiles} = await build({
  stdin: {
    contents: "export {faceHolePlacement} from './face-hole.js'; export {SceneModel, objectMatrix} from './scene.js';",
    resolveDir: path.join(source, 'apps/3dprint/web/model3d'),
  },
  nodePaths: [path.join(dependencies, 'node_modules')], bundle: true, platform: 'node', format: 'esm', write: false,
});
const {faceHolePlacement, SceneModel, objectMatrix} = await import(
  'data:text/javascript;base64,' + Buffer.from(outputFiles[0].text).toString('base64'),
);

const box = (min, max) => new Box3(new Vector3(...min), new Vector3(...max));
const near = (actual, expected) => assert(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
function nearVector(vector, expected) {
  vector.toArray().forEach((value, index) => near(value, expected[index]));
}

test('a drop on top stays at the clicked XY point and spans the full depth', () => {
  const placement = faceHolePlacement(new Vector3(6, -3, 9), new Vector3(0, 0, 1), box([-10, -10, 1], [10, 18, 9]));
  const scene = new SceneModel();
  const id = scene.add('box', {...placement, mode: 'hole'});
  const hole = scene.object(id), matrix = objectMatrix(hole);

  assert.deepEqual(hole.size, [5, 5, 9]);
  nearVector(new Vector3().setFromMatrixColumn(matrix, 0), [1, 0, 0]);
  nearVector(new Vector3().setFromMatrixColumn(matrix, 1), [0, 1, 0]);
  nearVector(new Vector3(0, 0, -4.5).applyMatrix4(matrix), [6, -3, .5]);
  nearVector(new Vector3(0, 0, 4.5).applyMatrix4(matrix), [6, -3, 9.5]);
});

test('an off-centre drop on a vertical side keeps the clicked height and lateral position', () => {
  const {matrix, size} = faceHolePlacement(new Vector3(25, 5, 13), new Vector3(3, 0, 0), box([5, -10, 6], [25, 14, 18]));
  const transform = new Matrix4().fromArray(matrix);

  assert.deepEqual(size, [3, 3, 21]);
  nearVector(new Vector3().setFromMatrixColumn(transform, 1), [0, 0, 1]);
  nearVector(new Vector3().setFromMatrixColumn(transform, 2), [1, 0, 0]);
  nearVector(new Vector3(0, 0, -10.5).applyMatrix4(transform), [4.5, 5, 13]);
  nearVector(new Vector3(0, 0, 10.5).applyMatrix4(transform), [25.5, 5, 13]);
});

test('an oblique drop has unscaled right-handed axes and covers the projected target bounds', () => {
  const hit = new Vector3(8, 5, 22), normal = new Vector3(0, 3, 4);
  const {matrix, size} = faceHolePlacement(hit, normal, box([-10, -5, 2], [10, 5, 22]));
  const transform = new Matrix4().fromArray(matrix), axis = new Vector3().setFromMatrixColumn(transform, 2);

  nearVector(axis, [0, .6, .8]);
  nearVector(new Vector3().setFromMatrixScale(transform), [1, 1, 1]);
  near(transform.determinant(), 1);
  nearVector(new Vector3().setFromMatrixPosition(transform), [8, -1.6, 13.2]);
  near(size[0], 5);
  near(size[1], 5);
  near(new Vector3(0, 0, -size[2] / 2).applyMatrix4(transform).dot(axis), -1.9);
  near(new Vector3(0, 0, size[2] / 2).applyMatrix4(transform).dot(axis), 21.1);
  assert.deepEqual(hit.toArray(), [8, 5, 22]);
  assert.deepEqual(normal.toArray(), [0, 3, 4]);
});

test('the default diameter stays usable on a thin shape and capped on a large shape', () => {
  const normal = new Vector3(0, 0, 1);
  const small = faceHolePlacement(new Vector3(.1, .15, .1), normal, box([0, 0, 0], [.2, .3, .1]));
  near(small.size[0], .1);
  near(small.size[1], .1);
  near(small.size[2], 1.1);
  const large = faceHolePlacement(new Vector3(0, 0, 60), normal, box([-40, -45, 0], [40, 45, 60]));
  assert.deepEqual(large.size, [10, 10, 61]);
});

test('invalid ray data and unrepresentable geometry produce no placement', () => {
  const hit = new Vector3(0, 0, 10), normal = new Vector3(0, 0, 1);
  const bounds = box([-10, -10, 0], [10, 10, 10]);

  assert.equal(faceHolePlacement(new Vector3(Infinity, 0, 10), normal, bounds), null);
  assert.equal(faceHolePlacement(hit, new Vector3(), bounds), null);
  assert.equal(faceHolePlacement(hit, normal, new Box3()), null);
  assert.equal(faceHolePlacement(hit, normal, box([-10, -10, -1_000_000], [10, 10, 10])), null);
});
