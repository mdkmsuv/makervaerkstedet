import assert from 'node:assert/strict';
import {test} from 'node:test';
import Module from 'manifold-3d';
import {Matrix4} from 'three';
import {catalogChildren} from '../apps/3dprint/web/model3d/catalog-children.js';
import {connectorMount} from '../apps/3dprint/web/model3d/connector-mount.js';
import {buildConnector, FIGURE_SNAP_PROFILE} from '../apps/3dprint/web/model3d/connector-profiles.js';
import {createGeometryKernel, exportBinaryStl} from '../apps/3dprint/web/model3d/geometry.js';
import {INSPIRATION_RECIPES} from '../apps/3dprint/web/model3d/inspiration-recipes.js';
import {decodeProject, encodeProject} from '../apps/3dprint/web/model3d/project.js';
import {SceneModel, objectMatrix} from '../apps/3dprint/web/model3d/scene.js';
import {hasRigidAxes} from '../apps/3dprint/web/model3d/connector-transform.js';
import {validateSTL} from '../sdk/web/stl-validation.mjs';

const wasm = await Module();
wasm.setup();
const kernel = createGeometryKernel(wasm);

function insert(recipe, values = recipe.defaults) {
  const shape = recipe.build(values), model = new SceneModel(), assets = new Map();
  const half = shape.size[2] / 2;
  const generator = {kind: 'catalog-shape', settings: {recipe: recipe.id, values}, origin: [0, 0, half]};
  const id = model.addMeshGroup(recipe.label, catalogChildren(shape, recipe.color, assets), [0, 0, half], generator, shape.op);
  assert(id);
  return {shape, model, assets, id};
}

function resultOf({model, assets}) {
  const {scene, assets: restored} = decodeProject(encodeProject(model.data, assets));
  return kernel.evaluateScene(scene, restored);
}

function solidOf(result) {
  return new wasm.Manifold(new wasm.Mesh({numProp: 3, vertProperties: result.merged.positions, triVerts: result.merged.indices}));
}

for (const recipe of INSPIRATION_RECIPES) {
  test(`${recipe.label} is an editable separate module with a fixed snap fitting`, () => {
    assert(recipe.fields.length > 0);
    const field = recipe.fields[0];
    const edited = {...recipe.defaults, [field.key]: field.max};
    const {shape, model, assets, id} = insert(recipe);
    assert(shape.connectors.length > 0);
    const initial = resultOf({model, assets});
    assert.equal(initial.parts, 1);
    assert.deepEqual(initial.missedHoles, []);
    assert(Math.abs(initial.bounds.min[2]) < .001);
    validateSTL(exportBinaryStl(initial));

    assert(model.scaleLocal(id, [1.12, .95, 1.21], [0, 0, -shape.size[2] / 2]));
    const changed = recipe.build(edited), origin = [0, 0, changed.size[2] / 2];
    const generator = {kind: 'catalog-shape', settings: {recipe: recipe.id, values: edited}, origin};
    assert(model.replaceGeneratedGroup(id, {children: catalogChildren(changed, recipe.color, assets), generator, op: changed.op}));
    const parent = objectMatrix(model.object(id));
    for (const child of model.object(id).children.map(cid => model.object(cid)).filter(node => node.generator?.kind === 'connector')) {
      assert.match(child.generator.settings.profile, /^snap-9(?:$|-roof-)/);
      assert(hasRigidAxes(parent.clone().multiply(objectMatrix(child)).elements));
    }
    const after = resultOf({model, assets});
    assert.equal(after.parts, 1);
    assert.deepEqual(after.missedHoles, []);
    assert.notEqual(after.volume, initial.volume);
    validateSTL(exportBinaryStl(after));
  });
}

test('the actual robot arm seats in the actual robot shoulder and catches when pulled back', () => {
  const recipe = id => INSPIRATION_RECIPES.find(item => item.id === id);
  const torso = insert(recipe('robot-torso')), arm = insert(recipe('robot-arm'));
  const socket = torso.shape.connectors.find(port => port.rotation[1] === 90);
  const plug = arm.shape.connectors[0];
  const pose = ({at, rotation}) => objectMatrix({position: at, rotation});
  const shoulder = pose(socket), orientation = new Matrix4().makeRotationY(Math.PI).multiply(pose(plug).invert());
  const torsoSolid = solidOf(resultOf(torso)), armSolid = solidOf(resultOf(arm));
  try {
    for (const [offset, shouldCatch] of [[0, false], [2.5, true]]) {
      const matrix = shoulder.clone().multiply(new Matrix4().makeTranslation(0, 0, offset)).multiply(orientation);
      const positioned = armSolid.transform(matrix.toArray());
      const overlap = torsoSolid.intersect(positioned);
      try { assert.equal(overlap.isEmpty() || overlap.volume() < 1e-5, !shouldCatch, `withdrawal ${offset} mm, overlap ${overlap.volume()} mm³`); }
      finally { overlap.delete(); positioned.delete(); }
    }
  } finally { armSolid.delete(); torsoSolid.delete(); }
});

test('each horizontal inspiration fork has bed contact from root to tip', () => {
  const settings = {profile: FIGURE_SNAP_PROFILE, role: 'plug'};
  const fork = buildConnector(settings), half = fork.size[2] / 2;
  for (const recipe of INSPIRATION_RECIPES) for (const mount of recipe.build().connectors) {
    if (mount.role !== 'plug') continue;
    const face = objectMatrix({position: mount.at, rotation: mount.rotation});
    if (Math.abs(face.elements[10]) > .001) continue;
    const m = connectorMount(fork, settings, face).matrix;
    const heights = [], bed = [];
    for (let i = 0; i < fork.positions.length; i += 3) {
      const x = fork.positions[i], y = fork.positions[i + 1], axis = fork.positions[i + 2];
      const z = m[2] * x + m[6] * y + m[10] * (axis - half) + m[14];
      heights.push(z);
      if (Math.abs(z) < .005) bed.push(axis);
    }
    assert(Math.abs(Math.min(...heights)) < .005, `${recipe.label}: fork must meet the build plate`);
    assert(Math.max(...bed) - Math.min(...bed) > fork.size[2] - .02, `${recipe.label}: fork must lie flat along its length`);
  }
});

test('sideways and downward inspiration sockets carry a fixed printable roof', () => {
  for (const recipe of INSPIRATION_RECIPES) {
    const {shape, model, id} = insert(recipe);
    const cutters = model.object(id).children.map(child => model.object(child)).filter(node => node.generator?.kind === 'connector' && node.mode === 'hole');
    const sockets = shape.connectors.filter(mount => mount.role === 'socket');
    sockets.forEach((mount, index) => {
      const face = objectMatrix({position: mount.at, rotation: mount.rotation});
      const cutter = cutters[index];
      if (face.elements[10] < -.99) assert(cutter.size[2] > 12.8, `${recipe.label}: downward socket has a tapered blind end`);
      else if (Math.abs(face.elements[10]) < .01) {
        const alongX = Math.abs(face.elements[2]) > .99;
        assert(cutter.size[alongX ? 0 : 1] > (alongX ? 9.4 : 6.4), `${recipe.label}: cavity roof extends above its snap profile`);
      }
    });
  }
});

test('all eight dragon attachments seat in different ports without colliding', () => {
  const recipe = id => INSPIRATION_RECIPES.find(item => item.id === id);
  const torso = insert(recipe('dragon-torso'));
  const sockets = new Map(torso.shape.connectors.map(mount => [mount.port, mount]));
  const pose = mount => objectMatrix({position: mount.at, rotation: mount.rotation});
  const fittings = [
    ['head', 'dragon-head'], ['left-wing', 'dragon-wing'], ['right-wing', 'dragon-wing'], ['tail', 'dragon-tail'],
    ...['front-left', 'front-right', 'rear-left', 'rear-right'].map(port => [port, 'dragon-foot']),
  ];
  assert.equal(sockets.size, fittings.length);
  const solids = [solidOf(resultOf(torso))];
  try {
    for (const [port, id] of fittings) {
      const part = insert(recipe(id));
      const peg = solidOf(resultOf(part));
      const transform = pose(sockets.get(port)).multiply(new Matrix4().makeRotationX(Math.PI))
        .multiply(pose(part.shape.connectors[0]).invert());
      if (port === 'left-wing') transform.multiply(new Matrix4().makeScale(1, -1, 1));
      try { solids.push(peg.transform(transform.toArray())); }
      finally { peg.delete(); }
    }
    assert(solids.every(solid => solid.status() === 'NoError'));
    solids.forEach((solid, index) => {
      for (const other of solids.slice(index + 1)) {
        const overlap = solid.intersect(other);
        try { assert(overlap.volume() < 1e-4, `part ${index} overlaps a neighbouring piece`); }
        finally { overlap.delete(); }
      }
    });
  } finally { solids.forEach(solid => solid.delete()); }
});
