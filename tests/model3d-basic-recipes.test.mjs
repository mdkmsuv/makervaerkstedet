import assert from 'node:assert/strict';
import {test} from 'node:test';
import Module from 'manifold-3d';
import {BASIC_RECIPES} from '../apps/3dprint/web/model3d/basic-recipes.js';
import {createGeometryKernel, exportBinaryStl} from '../apps/3dprint/web/model3d/geometry.js';
import {SceneModel} from '../apps/3dprint/web/model3d/scene.js';
import {validateSTL} from '../sdk/web/stl-validation.mjs';

const wasm = await Module();
wasm.setup();
const kernel = createGeometryKernel(wasm);
const placement = [9, -11], bottom = 7, id = 'stl-recipe';

function evaluate(shape) {
  assert(shape.positions instanceof Float32Array);
  const scene = new SceneModel();
  scene.add('mesh', {asset: id, size: shape.size, position: placement, bottom});
  const result = kernel.evaluateScene(scene.data, new Map([[id, shape.positions]]));
  assert.equal(result.parts, 1);
  assert(result.volume > 0);
  assert(Math.abs(result.bounds.min[2] - bottom) < .0001);
  validateSTL(exportBinaryStl(result));
  return result.volume;
}

test('17 distinct configurable shapes supplement the original basic library', () => {
  const labels = BASIC_RECIPES.map(recipe => recipe.label);
  assert.equal(labels.length, 17);
  assert.equal(new Set(labels).size, labels.length);
  assert.equal(new Set(BASIC_RECIPES.map(recipe => recipe.id)).size, BASIC_RECIPES.length);
});

test('a narrow staircase with many steps keeps a watertight outline and matching caps', () => {
  const stairs = BASIC_RECIPES.find(recipe => recipe.id === 'stairs');
  evaluate(stairs.build({width: 3, height: 51.5, depth: 3, count: 12}));
});

for (const recipe of BASIC_RECIPES) {
  test(`${recipe.label} creates one closed solid and responds to its defining parameters`, () => {
    assert(recipe.fields.length >= 2);
    const normal = recipe.build(recipe.defaults);
    evaluate(normal);
    for (const field of recipe.fields) {
      const changed = {...recipe.defaults, [field.key]: recipe.defaults[field.key] + field.step};
      assert.equal(recipe.validate(changed), null, field.label);
      const variant = recipe.build(changed);
      assert.notDeepEqual(variant.positions, normal.positions, field.label);
    }
    const primary = recipe.fields[0];
    const changed = recipe.build({...recipe.defaults, [primary.key]: recipe.defaults[primary.key] + primary.step});
    evaluate(changed);
    assert.equal(recipe.build({...recipe.defaults, [primary.key]: primary.max + primary.step}), null);
    assert.equal(recipe.build({...recipe.defaults, invalid: 1}), null);
  });
}
