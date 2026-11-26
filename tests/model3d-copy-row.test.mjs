import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import path from 'node:path';
import {test} from 'node:test';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const [dependencies = root, source = root] = process.argv.slice(2);
const require = createRequire(path.join(dependencies, 'package.json'));
const {build} = require('esbuild');
const {outputFiles} = await build({
  stdin: {
    contents: "export {SceneModel, objectMatrix} from './scene.js'; export * from './copy-row.js'; export {encodeProject,decodeProject} from './project.js';",
    resolveDir: path.join(source, 'apps/3dprint/web/model3d'),
  },
  nodePaths: [path.join(dependencies, 'node_modules')], bundle: true, platform: 'node', format: 'esm', write: false,
});
const {SceneModel, objectMatrix, prepareCopyRow, findHoleCopyOffset, fitsInside, repeatCopyMatrix, encodeProject, decodeProject} = await import(
  'data:text/javascript;base64,' + Buffer.from(outputFiles[0].text).toString('base64'),
);

const holeBounds = {min: [-24, -1, -2], max: [-16, 7, 8]};
const row = {count: 4, spacing: 12, direction: 'x+', bounds: holeBounds};

test('copies one 8 mm hole to four exact centres and restores the whole row in one history step', () => {
  const model = new SceneModel();
  model.add('box'); model.copy();
  const original = model.add('cylinder', {position: [-20, 3], bottom: -2, size: [8, 8, 10], mode: 'hole'});
  const before = model.snapshot(), clipboard = structuredClone(model.clipboard);
  prepareCopyRow(model, row).apply();

  assert.equal(model.data.roots.length, 5);
  assert.equal(model.selected[0], original);
  assert.deepEqual(model.selected.map(id => objectMatrix(model.object(id)).elements.slice(12, 15)),
    [[-20, 3, 3], [-8, 3, 3], [4, 3, 3], [16, 3, 3]]);
  for (const id of model.selected) {
    assert.equal(model.object(id).mode, 'hole');
    assert.deepEqual(model.object(id).size, [8, 8, 10]);
  }
  assert.deepEqual(model.clipboard, clipboard);
  const after = model.snapshot(), selection = [...model.selected];
  model.undo();
  assert.equal(model.snapshot(), before);
  assert.deepEqual(model.selected, [original]);
  model.redo();
  assert.equal(model.snapshot(), after);
  assert.deepEqual(model.selected, selection);
});

test('copies a group in negative Y and keeps its children, colour and shared project asset', () => {
  const model = new SceneModel();
  const base = model.add('box', {color: '#2f6fb5', size: [12, 10, 6]});
  const mesh = model.add('mesh', {asset: 'stl-part'});
  model.selected = [base, mesh];
  model.group();
  const original = model.selected[0];
  prepareCopyRow(model, {count: 2, spacing: 17.5, direction: 'y-', bounds: {min: [-10, -10, 0], max: [10, 10, 20]}}).apply();

  const assets = new Map([['stl-part', new Float32Array(36)]]);
  const saved = decodeProject(encodeProject(model.data, assets));
  const [copy] = saved.scene.objects.filter(node => node.kind === 'group' && node.id !== original);
  assert.deepEqual(objectMatrix(copy).elements.slice(12, 15), [0, -17.5, 0]);
  assert.equal(copy.op, 'difference');
  assert.deepEqual(copy.children.map(id => saved.scene.objects.find(node => node.id === id).color), ['#2f6fb5', undefined]);
  assert.deepEqual(saved.scene.objects.filter(node => node.kind === 'mesh').map(node => node.asset), ['stl-part', 'stl-part']);
  assert.deepEqual(model.object(original).children, [base, mesh]);
  assert(!copy.children.includes(base));
  assert(!copy.children.includes(mesh));
});

test('rejects invalid values, multiple or protected selections, and a row beyond the plate without changing history', () => {
  const model = new SceneModel();
  const other = model.add('box');
  const original = model.add('cylinder', {position: [-20, 3], size: [8, 8, 10], mode: 'hole'});
  function rejects(values, field, message) {
    const before = model.snapshot(), steps = model.undoStack.length;
    const rejected = prepareCopyRow(model, {...row, ...values});
    assert.equal(rejected.field, field);
    assert.match(rejected.error, message);
    assert.equal(model.snapshot(), before);
    assert.equal(model.undoStack.length, steps);
  }
  rejects({count: 2.5}, 'count', /helt tal/);
  rejects({spacing: 0}, 'spacing', /afstand/i);
  rejects({direction: 'z+'}, 'direction', /retning/i);
  rejects({count: 8, spacing: 12, direction: 'x-'}, null, /pladens kant/);
  model.selected.push(other);
  rejects({}, null, /én synlig/);
  model.selected = [original];
  for (const flag of ['hidden', 'locked']) {
    model.flag(flag, true);
    rejects({}, null, /én synlig/);
    model.flag(flag, false);
  }
});

test('counts the entire subtree towards the editable project capacity', () => {
  const model = new SceneModel();
  const first = model.add('box'), second = model.add('cylinder', {mode: 'hole'});
  model.selected = [first, second];
  model.group();
  const group = model.selected[0], box = model.object(first);
  for (let n = 0; n < 506; n++) {
    const id = `existing-${n}`;
    model.data.objects.push({...box, id, hidden: true});
    model.data.roots.push(id);
  }
  assert.equal(typeof prepareCopyRow(model, {...row, count: 2}).apply, 'function');
  model.data.objects.push({...box, id: 'existing-506', hidden: true});
  model.data.roots.push('existing-506');
  const before = model.snapshot(), result = prepareCopyRow(model, {...row, count: 2});
  assert.match(result.error, /plads til flere figurer/);
  assert.equal(model.snapshot(), before);
  assert.deepEqual(model.selected, [group]);
});

test('warns before copying when holes overlap or run off the model while still on the printer bed', () => {
  const model = new SceneModel();
  model.add('cylinder', {position: [-20, 3], size: [8, 8, 10], mode: 'hole'});
  const before = model.snapshot();
  const plate = {min: [-30, -15, 0], max: [0, 15, 4]};
  const prepared = prepareCopyRow(model, {...row, count: 5, spacing: 6, holeContext: {plate, occupied: []}});
  assert.equal(prepared.span, 24);
  assert.equal(prepared.warnings.length, 2);
  assert.match(prepared.warnings[0], /overlappe/);
  assert.match(prepared.warnings[1], /kanten på modellen/);
  assert.equal(typeof prepared.apply, 'function');
  assert.equal(model.snapshot(), before);
  assert.deepEqual(prepareCopyRow(model, row).warnings, []);
});

test('ordinary hole copy prefers a free same-axis position and refuses an overcrowded model plate', () => {
  const bounds = {min: [-4, -4, 0], max: [4, 4, 8]};
  const plate = {min: [-25, -11, 0], max: [25, 11, 4]};
  const right = {min: [6, -4, 0], max: [14, 4, 8]};
  const ownHole = {bounds, hole: true};
  assert.deepEqual(findHoleCopyOffset({bounds, plate, occupied: [ownHole]}), [10, 0, 0]);
  assert.deepEqual(findHoleCopyOffset({bounds, plate, occupied: [ownHole, {bounds: right, hole: true}]}), [-10, 0, 0]);
  const smallPlate = {min: [-8, -8, 0], max: [8, 8, 4]};
  assert.equal(findHoleCopyOffset({bounds, plate: smallPlate, occupied: [ownHole]}), null);
});

test('a four-hole selection gets one safe offset for a parallel row on the same rounded plate', () => {
  const model = new SceneModel();
  const centres = [-20, -8, 4, 16];
  model.selected = centres.map(x => model.add('cylinder', {position: [x, 0], size: [8, 8, 8], mode: 'hole'}));
  const occupied = centres.map(x => ({bounds: {min: [x - 4, -4, 0], max: [x + 4, 4, 8]}, hole: true}));
  const bounds = {min: [-24, -4, 0], max: [20, 4, 8]};
  const plate = {min: [-34, -15, 0], max: [34, 15, 6], radii: [8, 8]};
  const offset = findHoleCopyOffset({bounds, plate, occupied});
  assert.deepEqual(offset, [0, 10, 0]);
  const before = model.snapshot(), selection = [...model.selected];
  model.copy(); model.paste(offset);
  assert.deepEqual(model.selectedObjects().map(node => objectMatrix(node).elements.slice(12, 15)),
    [[-20, 10, 4], [-8, 10, 4], [4, 10, 4], [16, 10, 4]]);
  model.undo();
  assert.equal(model.snapshot(), before);
  assert.deepEqual(model.selected, selection);

  const narrow = {min: [-34, -8, 0], max: [34, 8, 6]};
  assert.equal(findHoleCopyOffset({bounds, plate: narrow, occupied}), null);
});

test('rounded plate checks the cutout footprint at the corners and redirects an ordinary copy along a clear axis', () => {
  const plate = {min: [-20, -12, 0], max: [20, 12, 4], radii: [10, 10]};
  const bounds = {min: [4, 5, 0], max: [10, 10, 8]};
  assert.equal(fitsInside(bounds, plate, [0, 0, 0], .5), true);
  assert.equal(fitsInside(bounds, plate, [8, 0, 0], .5), false);
  assert.equal(fitsInside(bounds, {...plate, radii: [0, 0]}, [8, 0, 0], .5), true);
  assert.equal(fitsInside(bounds, plate, [-8, 0, 0], .5), true);
  assert.deepEqual(findHoleCopyOffset({bounds, plate, occupied: [
    {bounds, hole: true},
    {bounds: {min: [4, -12, 0], max: [11, 3, 8]}, hole: true},
  ]}), [-8, 0, 0]);
});

test('a hole row warns when its last cutout crosses a rounded corner inside the rectangular dimensions', () => {
  const model = new SceneModel();
  model.add('cylinder', {position: [7, 7.5], size: [5, 5, 8], mode: 'hole'});
  const result = prepareCopyRow(model, {
    count: 2, spacing: 8, direction: 'x+',
    bounds: {min: [4.5, 5, 0], max: [9.5, 10, 8]},
    holeContext: {plate: {min: [-20, -12, 0], max: [20, 12, 4], radii: [10, 10]}, occupied: []},
  });
  assert.equal(typeof result.apply, 'function');
  assert.match(result.warnings[0], /kanten på modellen/);
});

test('Ctrl+D only starts repeating after a deliberate transform and can repeat it on a multi-selection', () => {
  const model = new SceneModel();
  const a = model.add('box'), b = model.add('box', {position: [10, 0]});
  model.selected = [a, b];
  model.copy(); model.paste([25, 0, 0]);
  const copies = [...model.selected];
  const lastCopy = {sources: [a, b], copies, matrices: model.selectedObjects().map(node => objectMatrix(node).toArray()), repeated: false};
  assert.equal(repeatCopyMatrix(model, copies, lastCopy), null);

  model.applyTransform(objectMatrix({position: [3, 4, 0], rotation: [0, 0, 90]}));
  const step = repeatCopyMatrix(model, [...copies].reverse(), lastCopy);
  assert.deepEqual(step.elements.slice(12, 15).map(Math.round), [3, 29, 0]);
  assert.deepEqual(step.elements.slice(0, 3).map(Math.round), [0, 1, 0]);
  model.copy(); model.pasteTransformed(step);
  assert.deepEqual(model.selectedObjects().map(node => objectMatrix(node).elements.slice(12, 15).map(Math.round)),
    [[-26, 32, 10], [-36, 32, 10]]);

  const continued = {sources: copies, copies: [...model.selected], matrices: model.selectedObjects().map(node => objectMatrix(node).toArray()), repeated: true};
  assert.deepEqual(repeatCopyMatrix(model, continued.copies, continued).elements.slice(12, 15).map(Math.round), [3, 29, 0]);
});
