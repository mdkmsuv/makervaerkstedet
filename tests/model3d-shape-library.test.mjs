import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createGeometryKernel, exportBinaryStl} from '../apps/3dprint/web/model3d/geometry.js';
import {decodeProject, encodeProject, MAX_PROJECT_OBJECTS} from '../apps/3dprint/web/model3d/project.js';
import {SceneModel} from '../apps/3dprint/web/model3d/scene.js';
import {insertShapePreset, SHAPE_LIBRARY} from '../apps/3dprint/web/model3d/shape-library.js';
import {validateSTL} from '../sdk/web/stl-validation.mjs';

const dependency = process.env.MODEL3D_MANIFOLD_MODULE || 'manifold-3d';
assert.ok(!/^https?:/.test(dependency));
const {default: Module} = await import(dependency), wasm = await Module();
wasm.setup();
const kernel = createGeometryKernel(wasm);
const near = (actual, expected, tolerance = .001) => assert(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

function checkPlaced(model, preset, [x, y], bottom) {
  const result = kernel.evaluateScene(model.data);
  assert.equal(result.parts, 1);
  assert(result.volume > 0);
  assert.equal(result.exportBlocked, false);
  assert.deepEqual(result.edgeHoles, []);
  assert.deepEqual(result.missedHoles, []);
  near(result.bounds.min[0], x - preset.size[0] / 2);
  near(result.bounds.max[0], x + preset.size[0] / 2);
  near(result.bounds.min[1], y - preset.size[1] / 2);
  near(result.bounds.max[1], y + preset.size[1] / 2);
  near(result.bounds.min[2], bottom);
  near(result.bounds.max[2], bottom + preset.size[2]);
  validateSTL(exportBinaryStl(result));
  const mesh = new wasm.Mesh({numProp: 3, vertProperties: result.merged.positions, triVerts: result.merged.indices});
  const reconstructed = new wasm.Manifold(mesh);
  try { assert.equal(reconstructed.status(), 'NoError'); }
  finally { reconstructed.delete(); }
  return result;
}

for (const preset of SHAPE_LIBRARY.everyday) {
  test(`${preset.id} is a centered, closed, editable object at the chosen bottom`, () => {
    const model = new SceneModel(), position = [12, -25], bottom = 13;
    const id = insertShapePreset(model, preset, position, bottom, '#2f6fb5');
    assert.deepEqual(model.data.roots, [id]);
    assert.deepEqual(model.selected, [id]);
    const root = model.object(id);
    assert.equal(root.name, preset.label);
    assert(root.children.every(child => model.object(child).kind !== 'mesh'));
    assert.deepEqual(
      root.children.map(child => model.object(child)).filter(child => child.mode !== 'hole').map(child => child.color),
      preset.parts.filter(child => child.mode !== 'hole').map(child => child.color || '#2f6fb5'),
    );
    checkPlaced(model, preset, position, bottom);
    assert.deepEqual(decodeProject(encodeProject(model.data, new Map())).scene, model.data);
    model.undo();
    assert.deepEqual(model.data.objects, []);
    model.redo();
    assert.deepEqual(model.data.roots, [id]);
  });
}

test('the toilet roll has both a visible core and a continuous opening', () => {
  const roll = SHAPE_LIBRARY.everyday.find(item => item.id === 'toilet-roll');
  assert.deepEqual(roll?.size, [115, 115, 95]);
  const model = new SceneModel();
  insertShapePreset(model, roll, [0, 0]);
  const result = checkPlaced(model, roll, [0, 0], 0);
  const nominalVolume = Math.PI * (57.5 ** 2 - 21 ** 2) * 95;
  near(result.volume, nominalVolume, nominalVolume * .005);
  assert.equal(result.meshes[0].paints.length, 2);
});

test('configurable hardware and a full project cannot be inserted as fixed presets', () => {
  const model = new SceneModel();
  for (const preset of SHAPE_LIBRARY.hardware.filter(item => item.configure)) {
    assert.equal(insertShapePreset(model, preset, [0, 0]), null);
  }
  for (let i = 0; i < MAX_PROJECT_OBJECTS - 1; i++) {
    const id = `existing-${i}`;
    model.data.objects.push({id, kind: 'box', size: [1, 1, 1]});
    model.data.roots.push(id);
  }
  const before = model.snapshot();
  assert.equal(insertShapePreset(model, SHAPE_LIBRARY.everyday[0], [0, 0], 0), null);
  assert.equal(model.snapshot(), before);
  assert.equal(model.undoStack.length, 0);
});
