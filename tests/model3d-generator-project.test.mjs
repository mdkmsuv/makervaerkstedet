import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Matrix4, Vector3} from 'three';
import {decodeProject, encodeProject, MAX_PROJECT_OBJECTS} from '../apps/3dprint/web/model3d/project.js';
import {COLORS, objectMatrix, SceneModel} from '../apps/3dprint/web/model3d/scene.js';

const gear = (teeth = 25, thickness = 5, origin = [.2, 0, thickness / 2]) => ({
  kind: 'gear', settings: {teeth, module: 1.5, pressureAngle: 20, backlash: .15, thickness, bore: 5}, origin,
});
const nut = {kind: 'nut', settings: {diameter: 8, pitch: 1, hand: 'left', clearance: .25, thickness: 6.8}, origin: [0, 0, 3.4]};
const piece = (settings = [1, 2, 4, 6], height = 12) => ({kind: 'game-piece', settings, origin: [0, 0, height / 2]});
const child = (id, kind = 'mesh') => ({id, kind, mode: 'solid', size: [10, 10, 10], position: [0, 0, 0], ...(kind === 'mesh' && {asset: 'stl-original'})});
const meshes = (prefix, count = 4) => Array.from({length: count}, (_, i) => child(`${prefix}-${i}`));
const assets = new Map([['stl-original', new Float32Array(36)], ['stl-updated', new Float32Array(36)]]);
const near = (a, b) => assert(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

function baseAnchor(node) {
  return new Vector3(...node.generator.origin.map(value => -value)).applyMatrix4(objectMatrix(node));
}

test('project retains generator settings and opens older imported meshes without generator metadata', () => {
  const model = new SceneModel(), generator = gear();
  const gearId = model.add('mesh', {asset: 'stl-original', size: [40, 40, 5], generator});
  const nutId = model.add('mesh', {asset: 'stl-original', size: [15, 13, 6.8], generator: nut});
  const groupId = model.addMeshGroup('Spillebrik', meshes('piece'), [4, 5, 6], piece());
  const oldId = model.add('mesh', {asset: 'stl-original', name: 'Gammel import'});
  generator.settings.teeth = 29;
  const restored = decodeProject(encodeProject(model.data, assets)).scene;
  const nodes = new Map(restored.objects.map(node => [node.id, node]));
  assert.deepEqual(nodes.get(gearId).generator, gear());
  assert.deepEqual(nodes.get(nutId).generator, nut);
  assert.deepEqual(nodes.get(groupId).generator, piece());
  assert.equal(Object.hasOwn(nodes.get(oldId), 'generator'), false);
});

test('regenerating a rotated and scaled mesh keeps the shaft at the original base and preserves state', () => {
  const model = new SceneModel();
  const matrix = new Matrix4().makeRotationX(.6).multiply(new Matrix4().makeScale(1.4, .8, 2)).setPosition(10, -7, 20);
  const id = model.add('mesh', {asset: 'stl-original', size: [40, 40, 5], color: COLORS[2], name: 'Tandhjul (25 tænder)', generator: gear(), matrix: matrix.toArray()});
  model.mode('hole');
  const before = model.snapshot(), steps = model.undoStack.length;
  const oldAnchor = baseAnchor(model.object(id));
  const updated = {asset: 'stl-updated', size: [45, 43, 8], name: 'Tandhjul (28 tænder)', generator: gear(28, 8, [0, .1, 4])};
  assert.equal(model.replaceGeneratedMesh(id, updated), true);
  const node = model.object(id);
  assert.deepEqual(model.selected, [id]);
  assert.equal(node.mode, 'hole');
  assert.equal(node.color, COLORS[2]);
  assert.equal(node.asset, 'stl-updated');
  assert.equal(node.name, updated.name);
  assert.deepEqual(node.size, updated.size);
  assert.deepEqual(node.generator, updated.generator);
  assert.deepEqual(objectMatrix(node).elements.slice(0, 12), matrix.elements.slice(0, 12));
  near(baseAnchor(node).distanceTo(oldAnchor), 0);
  assert.equal(model.undoStack.length, steps + 1);
  const after = model.snapshot();
  model.undo();
  assert.equal(model.snapshot(), before);
  assert.deepEqual(model.selected, [id]);
  model.redo();
  assert.equal(model.snapshot(), after);
  assert.equal(model.replaceGeneratedMesh(id, updated), false, 'identical settings should not add a history step');
});

test('regenerating a group updates only that subtree and keeps its transformed base and attributes', () => {
  const model = new SceneModel(), unrelated = model.add('box');
  const groupId = model.addMeshGroup('Før', meshes('before'), [3, 4, 6], piece());
  const transform = new Matrix4().makeRotationY(.5).multiply(new Matrix4().makeScale(2, 1.2, .7));
  model.applyTransform(transform);
  model.color(COLORS[4]);
  const before = model.snapshot(), oldMatrix = objectMatrix(model.object(groupId)), oldAnchor = baseAnchor(model.object(groupId));
  const children = meshes('after');
  assert.equal(model.replaceGeneratedGroup(groupId, {children, name: 'Efter', generator: piece([1, 2, 4, 0], 20)}), true);
  const root = model.object(groupId);
  assert.deepEqual(model.data.roots, [unrelated, groupId]);
  assert.deepEqual(model.selected, [groupId]);
  assert.equal(root.color, COLORS[4]);
  assert.equal(root.mode, 'solid');
  assert.equal(root.name, 'Efter');
  assert.deepEqual(root.children, children.map(child => child.id));
  assert.equal(model.object('before-0'), undefined);
  assert.equal(model.object(unrelated).kind, 'box');
  assert.deepEqual(objectMatrix(root).elements.slice(0, 12), oldMatrix.elements.slice(0, 12));
  near(baseAnchor(root).distanceTo(oldAnchor), 0);
  const after = model.snapshot();
  model.undo();
  assert.equal(model.snapshot(), before);
  model.redo();
  assert.equal(model.snapshot(), after);
});

test('invalid foreign settings, node kinds and sparse or unbounded values cannot be saved or opened', () => {
  const model = new SceneModel(), id = model.add('mesh', {asset: 'stl-original', generator: gear()});
  const valid = encodeProject(model.data, assets);
  const invalid = [
    node => { node.generator.settings.comment = 'unrecognized'; },
    node => { delete node.generator.settings.teeth; },
    node => { node.generator.settings.module = 41; },
    node => { node.generator.origin[0] = 1e7; },
    node => { node.generator.origin = new Array(3); },
    node => { node.generator = piece(); },
  ];
  for (const invalidate of invalid) {
    const document = JSON.parse(valid), node = document.scene.objects.find(node => node.id === id);
    invalidate(node);
    assert.throws(() => encodeProject(document.scene, assets));
    assert.throws(() => decodeProject(JSON.stringify(document)));
  }
  assert.equal(model.add('box', {generator: gear()}), null);
  assert.equal(model.add('mesh', {asset: 'stl-original', generator: {...nut, settings: {...nut.settings, pitch: 1.5}}}), null);
  assert.equal(model.addMeshGroup('Brik', meshes('bad'), [0, 0, 6], piece([0, 0, 0, 999])), null);
});

test('regeneration rejects locks, unrelated IDs, a different generator and excess capacity without changing history', () => {
  const model = new SceneModel(), unrelated = model.add('box');
  const groupId = model.addMeshGroup('Før', meshes('old', 1), [3, 4, 6], piece());
  const meshId = model.add('mesh', {asset: 'stl-original', generator: gear()});
  const meshUpdate = {asset: 'stl-updated', size: [40, 40, 5], name: 'Opdateret', generator: gear(26)};
  const groupUpdate = {children: meshes('new'), name: 'Efter', generator: piece()};
  function rejects(action) {
    const before = model.snapshot(), history = model.undoStack.length, selected = [...model.selected];
    assert.equal(action(), false);
    assert.equal(model.snapshot(), before);
    assert.equal(model.undoStack.length, history);
    assert.deepEqual(model.selected, selected);
  }
  model.object(meshId).locked = true;
  rejects(() => model.replaceGeneratedMesh(meshId, meshUpdate));
  delete model.object(meshId).locked;
  rejects(() => model.replaceGeneratedMesh(meshId, {...meshUpdate, generator: nut}));
  model.object('old-0').locked = true;
  rejects(() => model.replaceGeneratedGroup(groupId, groupUpdate));
  delete model.object('old-0').locked;
  rejects(() => model.replaceGeneratedGroup(groupId, {...groupUpdate, children: [child(unrelated)]}));
  for (let i = model.data.objects.length; i < MAX_PROJECT_OBJECTS; i++) {
    const node = child(`filler-${i}`, 'box');
    model.data.objects.push(node); model.data.roots.push(node.id);
  }
  rejects(() => model.replaceGeneratedGroup(groupId, groupUpdate));
});
