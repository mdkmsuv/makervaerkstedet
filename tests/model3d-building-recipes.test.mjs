import assert from 'node:assert/strict';
import {test} from 'node:test';
import {BUILDING_RECIPES, BUILDING_RECIPES_BY_ID} from '../apps/3dprint/web/model3d/building-recipes.js';
import {createGeometryKernel, exportBinaryStl} from '../apps/3dprint/web/model3d/geometry.js';
import {decodeProject, encodeProject} from '../apps/3dprint/web/model3d/project.js';
import {SceneModel} from '../apps/3dprint/web/model3d/scene.js';
import {validateSTL} from '../sdk/web/stl-validation.mjs';

const dependency = process.env.MODEL3D_MANIFOLD_MODULE || 'manifold-3d';
assert(!/^https?:/.test(dependency));
const {default: Module} = await import(dependency);
const wasm = await Module();
wasm.setup();
const kernel = createGeometryKernel(wasm);
const near = (actual, expected) => assert(Math.abs(actual - expected) < .002, `${actual} != ${expected}`);

const variants = {
  washer: {bore: 7},
  spacer: {length: 27},
  'angle-bracket': {height: 38},
  'mounting-plate': {corner: 'round'},
  pulley: {grooveDepth: 4},
  wheel: {style: 'vented'},
  flange: {holeCount: '6'},
  knob: {grip: 'hex'},
  'shaft-coupling': {otherBore: 10},
  hook: {depth: 44},
  'corner-support': {depth: 52},
};

function evaluated(recipe, settings) {
  assert.equal(recipe.validate(settings), null);
  const built = recipe.build(settings);
  assert(built?.parts.length);
  assert(built.parts.every(node => !('id' in node) && node.kind !== 'mesh'));
  assert.equal(built.op, built.parts.some(node => node.mode === 'hole') ? 'difference' : 'union');
  const model = new SceneModel(), origin = [7, -4, 6];
  const nodes = built.parts.map((part, i) => ({...part, id: `child-${i}`}));
  const id = model.addMeshGroup(recipe.label, nodes, origin);
  model.object(id).op = built.op;
  const result = kernel.evaluateScene(model.data);
  assert.equal(result.parts, 1);
  assert(result.volume > 0);
  assert.equal(result.exportBlocked, false);
  assert.deepEqual(result.edgeHoles, []);
  assert.deepEqual(result.missedHoles, []);
  const [width, depth, height] = built.size;
  near(result.bounds.min[0], origin[0] - width / 2);
  near(result.bounds.max[0], origin[0] + width / 2);
  near(result.bounds.min[1], origin[1] - depth / 2);
  near(result.bounds.max[1], origin[1] + depth / 2);
  near(result.bounds.min[2], origin[2]);
  near(result.bounds.max[2], origin[2] + height);
  validateSTL(exportBinaryStl(result));
  const mesh = new wasm.Mesh({numProp: 3, vertProperties: result.merged.positions, triVerts: result.merged.indices});
  const solid = new wasm.Manifold(mesh);
  try { assert.equal(solid.status(), 'NoError'); }
  finally { solid.delete(); }
  assert.deepEqual(decodeProject(encodeProject(model.data, new Map())).scene, model.data);
  return result.volume;
}

test('all eleven building recipes expose complete settings and icons', () => {
  assert.deepEqual(BUILDING_RECIPES.map(recipe => recipe.id), Object.keys(variants));
  for (const recipe of BUILDING_RECIPES) {
    assert.equal(BUILDING_RECIPES_BY_ID.get(recipe.id), recipe);
    assert.match(recipe.icon, /^M/);
    assert.equal(typeof recipe.label, 'string');
    assert.deepEqual(recipe.fields.map(field => field.key).sort(), Object.keys(recipe.defaults).sort());
    for (const field of recipe.fields) {
      if (field.type === 'number') {
        assert(field.step > 0);
        assert(field.min < field.max);
        assert.equal(recipe.validate({...recipe.defaults, [field.key]: field.max + 1})?.key, field.key);
      } else {
        assert.equal(field.type, 'select');
        assert(field.options.some(option => option.value === recipe.defaults[field.key]));
        assert.equal(recipe.validate({...recipe.defaults, [field.key]: ''})?.key, field.key);
      }
    }
  }
});

for (const recipe of BUILDING_RECIPES) {
  test(`${recipe.label}: closed geometry and a model-changing parameter`, () => {
    const original = evaluated(recipe, recipe.defaults);
    const changed = evaluated(recipe, {...recipe.defaults, ...variants[recipe.id]});
    assert(Math.abs(original - changed) > .05);
  });
}

test('effective limits keep holes, rims, grooves and meeting walls inside their material', () => {
  for (const [id, changes, key] of [
    ['washer', {diameter: 12, bore: 11}, 'bore'],
    ['spacer', {diameter: 10, bore: 9}, 'bore'],
    ['angle-bracket', {depth: 18, thickness: 10, hole: 8}, 'hole'],
    ['mounting-plate', {edge: 3, hole: 7}, 'edge'],
    ['pulley', {width: 7, lip: 4}, 'lip'],
    ['wheel', {diameter: 28, hub: 24, style: 'vented'}, 'style'],
    ['flange', {boltCircle: 20}, 'boltCircle'],
    ['knob', {height: 12, socketDepth: 11}, 'socketDepth'],
    ['shaft-coupling', {diameter: 14, otherBore: 12}, 'otherBore'],
    ['hook', {height: 22, thickness: 11}, 'thickness'],
    ['corner-support', {width: 20, thickness: 9, hole: 9}, 'hole'],
    ['corner-support', {height: 20, thickness: 12, hole: 7}, 'hole'],
  ]) {
    const recipe = BUILDING_RECIPES_BY_ID.get(id), settings = {...recipe.defaults, ...changes};
    assert.equal(recipe.validate(settings)?.key, key, id);
    assert.equal(recipe.build(settings), null, id);
  }
});
