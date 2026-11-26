import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Matrix4, Vector3} from 'three';
import {buildConnector} from '../apps/3dprint/web/model3d/connector-profiles.js';
import {decodeProject, encodeProject} from '../apps/3dprint/web/model3d/project.js';
import {objectMatrix, scaledMatrix, SceneModel} from '../apps/3dprint/web/model3d/scene.js';

const connectorSize = [10, 8, 6];
const connectorBounds = {min: [-5, -4, -3], max: [5, 4, 3]};
const origin = [1.2, -.7, 3];

function addConnector(model, matrix) {
  const id = model.add('mesh', {size: connectorSize, asset: 'stl-connector-test', matrix: matrix.toArray()});
  model.object(id).generator = {kind: 'connector', settings: {profile: 'test', role: 'plug'}, origin};
  return id;
}
function world(model, id) {
  const parent = model.data.objects.find(node => node.children?.includes(id));
  return (parent ? world(model, parent.id) : new Matrix4()).multiply(objectMatrix(model.object(id)));
}
function anchor(matrix) {
  return new Vector3(...origin.map(value => -value)).applyMatrix4(matrix);
}
function close(actual, expected) {
  assert.equal(actual.length, expected.length);
  actual.forEach((n, i) => assert.ok(Math.abs(n - expected[i]) < 1e-9, `element ${i}: ${n} != ${expected[i]}`));
}
function rigid(matrix) {
  const axes = [0, 1, 2].map(axis => new Vector3().setFromMatrixColumn(matrix, axis));
  close(axes.map(axis => axis.length()), [1, 1, 1]);
  close([axes[0].dot(axes[1]), axes[0].dot(axes[2]), axes[1].dot(axes[2])], [0, 0, 0]);
}

test('a connector refuses a direct resize but remains movable and rotatable', () => {
  const model = new SceneModel(), initial = objectMatrix({position: [6, -7, 5], rotation: [21, 4, 37]});
  const connector = addConnector(model, initial), before = model.snapshot(), history = model.undoStack.length;
  assert.equal(model.resize(connector, 0, 16, connectorBounds), false);
  assert.equal(model.scaleLocal(connector, [2, 1, .5], [0, 0, -3]), false);
  model.mode('hole');
  assert.equal(model.snapshot(), before);
  assert.equal(model.undoStack.length, history);
  model.applyTransform(new Matrix4().makeTranslation(6, -4, 11));
  assert.equal(model.rotate(connector, [5, 9, 75], connectorBounds), true);
  const after = world(model, connector);
  rigid(after);
  close(after.elements.slice(12, 15), [12, -11, 16]);
  assert.deepEqual(model.object(connector).size, connectorSize);
});

test('resizing a rotated nested assembly moves the connector anchor and preserves its rigid frame', () => {
  const model = new SceneModel();
  const connector = addConnector(model, objectMatrix({position: [2, 8, 9], rotation: [37, -14, 50]}));
  const body = model.add('box', {position: [-3, 4], size: [18, 24, 12]});
  model.selected = [connector, body];
  model.group();
  const inner = model.selected[0];
  model.applyTransform(objectMatrix({position: [5, -3, 11], rotation: [22, 45, -11]}));
  model.scaleLocal(inner, [1.2, .75, 1.7], [0, 0, 0]);
  const brace = model.add('cylinder', {position: [2, -6]});
  model.selected = [inner, brace];
  model.group();
  const outer = model.selected[0];
  model.applyTransform(objectMatrix({position: [10, 9, -4], rotation: [-30, 7, 42]}));
  model.scaleLocal(outer, [.7, 1.15, 1.4], [0, -8, 1]);
  const outerBefore = world(model, outer);
  const connectorBefore = world(model, connector);
  const bodyBefore = world(model, body);
  rigid(connectorBefore);
  const factor = [1.75, .48, 1.3], pivot = [6, -4, 1], lift = 2;
  const outerAfter = scaledMatrix(outerBefore, factor, pivot);
  outerAfter.elements[14] += lift;
  const delta = outerAfter.clone().multiply(outerBefore.clone().invert());
  const before = model.snapshot(), history = model.undoStack.length;

  assert.equal(model.scaleLocal(outer, factor, pivot, lift), true);
  const connectorAfter = world(model, connector);
  close(connectorAfter.elements.slice(0, 12), connectorBefore.elements.slice(0, 12));
  close(anchor(connectorAfter).toArray(), anchor(connectorBefore).applyMatrix4(delta).toArray());
  close(world(model, body).elements, delta.clone().multiply(bodyBefore).elements);
  assert.equal(model.undoStack.length, history + 1);
  const after = model.snapshot();
  model.undo();
  assert.equal(model.snapshot(), before);
  model.redo();
  assert.equal(model.snapshot(), after);
  const turn = objectMatrix({position: [4, 1, -7], rotation: [54, 12, -30]});
  model.select(outer);
  model.applyTransform(turn);
  const rotated = turn.clone().multiply(connectorAfter);
  rigid(world(model, connector));
  close(world(model, connector).elements, rotated.elements);
  model.ungroup();
  model.select(inner);
  model.ungroup();
  close(world(model, connector).elements, rotated.elements);
});

test('copying an assembly with an affine repeat keeps fixed parts nominal and translates their anchors', () => {
  const model = new SceneModel();
  const connector = addConnector(model, objectMatrix({position: [-3, 5, 9], rotation: [25, 13, -55]}));
  const body = model.add('box');
  model.selected = [connector, body];
  model.group();
  const group = model.selected[0];
  const connectorBefore = world(model, connector), bodyBefore = world(model, body);
  const turn = objectMatrix({position: [17, -8, -2], rotation: [58, 29, 39]});
  const repeat = turn.clone().multiply(new Matrix4().makeScale(.6, 2.4, .8));
  model.copy();
  model.pasteTransformed(repeat);
  const copy = model.selected[0], [connectorCopy, bodyCopy] = model.object(copy).children;
  assert.notEqual(copy, group);
  assert.notEqual(connectorCopy, connector);
  close(world(model, connectorCopy).elements.slice(0, 12), turn.clone().multiply(connectorBefore).elements.slice(0, 12));
  close(anchor(world(model, connectorCopy)).toArray(), anchor(connectorBefore).applyMatrix4(repeat).toArray());
  close(world(model, bodyCopy).elements, repeat.clone().multiply(bodyBefore).elements);
  close(world(model, connector).elements, connectorBefore.elements);
  assert.deepEqual(model.object(connectorCopy).generator, model.object(connector).generator);
});

test('generated assemblies store and can update their intended Boolean operation', () => {
  const model = new SceneModel(), generator = {kind: 'game-piece', settings: [0, 0, 0, 0], origin: [0, 0, 5]};
  const parts = suffix => [{id: 'main-' + suffix, kind: 'box', size: [10, 10, 10], mode: 'solid'},
    {id: 'detail-' + suffix, kind: 'box', size: [4, 4, 4], mode: 'hole'}];
  const group = model.addMeshGroup('Assembly', parts('initial'), [0, 0, 5], generator, 'difference');
  assert.equal(model.object(group).op, 'difference');
  assert.equal(model.replaceGeneratedGroup(group, {children: parts('next'), generator}), true);
  assert.equal(model.object(group).op, 'difference');
  assert.equal(model.replaceGeneratedGroup(group, {children: parts('final'), generator, op: 'union'}), true);
  assert.equal(model.object(group).op, 'union');
  const before = model.snapshot();
  assert.equal(model.replaceGeneratedGroup(group, {children: parts('invalid'), generator, op: 'unknown'}), false);
  assert.equal(model.snapshot(), before);
  model.undo();
  assert.equal(model.object(group).op, 'difference');
});

test('an assembly scale moves the opening of a rotated socket and saves its fixed cutting geometry', () => {
  const model = new SceneModel(), settings = {profile: 'round-6', role: 'socket'};
  const {positions, size} = buildConnector(settings), asset = 'stl-connector-socket';
  const matrix = objectMatrix({position: [2, 0, 6], rotation: [27, 12, 5]});
  const generator = {kind: 'connector', settings, origin: [0, 0, -size[2] / 2]};
  const options = {asset, size, generator, matrix: matrix.toArray()};
  assert.equal(model.add('mesh', {...options, generator: {...generator, origin: [0, 0, size[2] / 2]}}), null);
  assert.equal(model.add('mesh', {...options, size: [16, 16, 12]}), null);
  const stretched = matrix.clone().multiply(new Matrix4().makeScale(1, 2, .8));
  assert.equal(model.add('mesh', {...options, matrix: stretched.toArray()}), null);
  const socket = model.add('mesh', options), body = model.add('box');
  assert.ok(socket);
  assert.equal(model.object(socket).mode, 'hole');
  model.selected = [socket, body];
  model.group();
  const group = model.selected[0];
  const factor = [1.8, .7, 1.5], pivot = [0, 0, -1], mouth = new Vector3(0, 0, size[2] / 2);
  const expectedMouth = mouth.clone().applyMatrix4(matrix).applyMatrix4(scaledMatrix(new Matrix4(), factor, pivot));
  assert.equal(model.scaleLocal(group, factor, pivot), true);
  const compensated = world(model, socket);
  rigid(compensated);
  close(mouth.applyMatrix4(compensated).toArray(), expectedMouth.toArray());
  const assets = new Map([[asset, positions]]);
  const saved = decodeProject(encodeProject(model.data, assets));
  model.data = saved.scene;
  assert.deepEqual(saved.assets.get(asset), positions);
  model.ungroup();
  const restored = model.object(socket);
  assert.equal(restored.mode, 'hole');
  assert.deepEqual(restored.generator, generator);
  assert.deepEqual(restored.size, size);
  close(world(model, socket).elements, compensated.elements);
  const tampered = structuredClone(saved.scene);
  tampered.objects.find(node => node.id === socket).matrix[0] *= 2;
  assert.throws(() => encodeProject(tampered, saved.assets));
});
