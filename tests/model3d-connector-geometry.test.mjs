import assert from 'node:assert/strict';
import {test} from 'node:test';
import Module from 'manifold-3d';
import {Matrix4} from 'three';
import {BALL_CONNECTOR_CENTERS} from '../apps/3dprint/web/model3d/ball-connector.js';
import {buildConnector} from '../apps/3dprint/web/model3d/connector-profiles.js';
import {createGeometryKernel, exportBinaryStl} from '../apps/3dprint/web/model3d/geometry.js';
import {decodeProject, encodeProject} from '../apps/3dprint/web/model3d/project.js';
import {SceneModel, objectMatrix} from '../apps/3dprint/web/model3d/scene.js';
import {validateSTL} from '../sdk/web/stl-validation.mjs';

const wasm = await Module();
wasm.setup();
const kernel = createGeometryKernel(wasm);

function addConnector(model, assets, profile, role, position, bottom) {
  const shape = buildConnector({profile, role}), asset = `stl-${profile}-${role}`;
  assets.set(asset, shape.positions);
  const origin = [0, 0, (role === 'socket' ? -1 : 1) * shape.size[2] / 2];
  const id = model.add('mesh', {asset, size: shape.size, position, bottom, mode: shape.mode,
    generator: {kind: 'connector', settings: {profile, role}, origin}});
  assert(id, `${role} can be inserted`);
  return model.object(id);
}

function solidOf(result) {
  return new wasm.Manifold(new wasm.Mesh({numProp: 3, vertProperties: result.merged.positions, triVerts: result.merged.indices}));
}

test('the ball joint can turn and tilt ±25 degrees and stays captured in its cup', () => {
  const models = {};
  for (const role of ['plug', 'cup']) {
    const model = new SceneModel(), assets = new Map();
    addConnector(model, assets, 'ball-9', role, [0, 0], 0);
    const reopened = decodeProject(encodeProject(model.data, assets));
    const result = kernel.evaluateScene(reopened.scene, reopened.assets);
    assert.equal(result.parts, 1);
    assert.equal(result.bounds.min[2], 0);
    validateSTL(exportBinaryStl(result));
    models[role] = solidOf(result);
  }
  const {plug, cup} = models;
  const inverted = new Matrix4().makeRotationY(Math.PI)
    .multiply(new Matrix4().makeTranslation(0, 0, -BALL_CONNECTOR_CENTERS.plug));
  try {
    const poses = [[0, 0, 0], [25, 0, 0], [-25, 0, 0], [0, 25, 0], [0, -25, 0], [0, 0, 90], [18, 17, 37]];
    for (const [rotation, pull] of [...poses.map(rotation => [rotation, 0]), [[0, 0, 0], 3]]) {
      const transform = objectMatrix({position: [0, 0, BALL_CONNECTOR_CENTERS.cup + pull], rotation}).multiply(inverted);
      const placed = plug.transform(transform.toArray()), overlap = cup.intersect(placed);
      try {
        assert.equal(overlap.volume() > .01, pull > 0, `joint at ${rotation}, withdrawal ${pull} mm`);
      } finally { overlap.delete(); placed.delete(); }
    }
  } finally { cup.delete(); plug.delete(); }
});

test('the snap latch seats fully, then catches at the narrowed mouth on withdrawal', () => {
  const profile = 'snap-9', model = new SceneModel(), assets = new Map(), top = 24;
  const socket = buildConnector({profile, role: 'socket'}), plug = buildConnector({profile, role: 'plug'});
  assert.deepEqual(plug?.size, [8.8, 5.6, 12]);
  assert.deepEqual(socket?.size, [9.4, 6.4, 12.8]);
  const body = model.add('box', {size: [24, 20, top]});
  const cutter = addConnector(model, assets, profile, 'socket', [0, 0], top - socket.size[2] + .05);
  model.selected = [body, cutter.id]; model.group();
  const receiver = solidOf(kernel.evaluateScene(model.data, assets));
  const pegModel = new SceneModel();
  addConnector(pegModel, assets, profile, 'plug', [0, 0], 0);
  const peg = solidOf(kernel.evaluateScene(pegModel.data, assets));
  const reversed = peg.rotate([0, 180, 0]);
  try {
    for (const [pull, shouldCatch] of [[0, false], [2.5, true]]) {
      const inserted = reversed.translate([0, 0, top + .35 + pull]);
      const overlap = receiver.intersect(inserted);
      try { assert.equal(overlap.isEmpty(), !shouldCatch, `withdrawal ${pull} mm, overlap ${overlap.volume()} mm³`); }
      finally { overlap.delete(); inserted.delete(); }
    }
    assert.equal(receiver.status(), 'NoError');
    assert.equal(peg.status(), 'NoError');
  } finally { [reversed, peg, receiver].forEach(solid => solid.delete()); }
});

for (const profile of ['round-6', 'round-10', 'square-8']) {
  test(`${profile} stays open and fits its plug in a scaled, reopened and exported assembly`, () => {
    const model = new SceneModel(), assets = new Map();
    const body = model.add('box', {size: [30, 29, 24]});
    const socket = buildConnector({profile, role: 'socket'});
    const cutter = addConnector(model, assets, profile, 'socket', [3, -2], 24 - socket.size[2]);
    model.selected = [body, cutter.id]; model.group();
    const [root] = model.selected;
    assert(model.scaleLocal(root, [1.5, .8, 1.4], [0, 0, 0]));
    const restored = decodeProject(encodeProject(model.data, assets));
    const result = kernel.evaluateScene(restored.scene, restored.assets);
    assert.equal(result.parts, 1);
    assert.deepEqual(result.missedHoles, []);
    validateSTL(exportBinaryStl(result));

    const realCutter = restored.scene.objects.find(node => node.id === cutter.id);
    const realRoot = restored.scene.objects.find(node => node.id === root);
    const matrix = objectMatrix(realRoot).multiply(objectMatrix(realCutter));
    const [x, y, middle] = matrix.elements.slice(12, 15);
    const mouth = middle + socket.size[2] / 2;
    assert(Math.abs(mouth - result.bounds.max[2]) < .001);
    assert(Math.abs(x - 4.5) < .001 && Math.abs(y + 1.6) < .001);

    const fitModel = new SceneModel(), fitAssets = new Map();
    const plug = buildConnector({profile, role: 'plug'});
    addConnector(fitModel, fitAssets, profile, 'plug', [x, y], mouth - plug.size[2] - .15);
    const placed = kernel.evaluateScene(fitModel.data, fitAssets);
    const socketBody = solidOf(result), peg = solidOf(placed), overlap = socketBody.intersect(peg);
    const mouthRay = socketBody.rayCast([x, y, mouth + 1], [x, y, 0])[0];
    try {
      assert(overlap.isEmpty(), `${overlap.volume()} mm³ overlap in the socket`);
      assert(Math.abs(mouthRay.position[2] - (mouth - socket.size[2])) < .001, 'the first hit is the floor of the open socket');
    } finally { [overlap, peg, socketBody].forEach(solid => solid.delete()); }
  });
}
