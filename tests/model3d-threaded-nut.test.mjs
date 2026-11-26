import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {test} from 'node:test';
import {createGeometryKernel, exportBinaryStl} from '../apps/3dprint/web/model3d/geometry.js';
import {nutMesh, THREAD_SIZES} from '../apps/3dprint/web/model3d/threaded-nut.js';
import * as threadedParts from '../apps/3dprint/web/model3d/threaded-nut.js';
import {decodeProject, encodeProject} from '../apps/3dprint/web/model3d/project.js';
import {SceneModel} from '../apps/3dprint/web/model3d/scene.js';
import {validateSTL} from '../sdk/web/stl-validation.mjs';

const dependency = process.env.MODEL3D_MANIFOLD_MODULE || 'manifold-3d';
assert.ok(!/^https?:/.test(dependency));
const {default: Module} = await import(dependency), wasm = await Module();
wasm.setup();
const near = (actual, expected, tolerance = .004) => assert(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

function withSolid(nut, check) {
  const mesh = new wasm.Mesh({numProp: 3, vertProperties: nut.positions, triVerts: Uint32Array.from({length: nut.positions.length / 3}, (_, i) => i)});
  mesh.merge();
  const solid = new wasm.Manifold(mesh);
  try {
    assert.equal(solid.status(), 'NoError');
    assert(solid.volume() > 0);
    check(solid);
  } finally { solid.delete(); }
}

function radiusAt(solid, z, angle = 0) {
  const hit = solid.rayCast([0, 0, z], [20 * Math.cos(angle), 20 * Math.sin(angle), z])[0];
  assert(hit);
  return Math.hypot(...hit.position.slice(0, 2));
}

test('every offered metric diameter and pitch generates a single closed hex nut', () => {
  assert.deepEqual(THREAD_SIZES.map(({diameter, acrossFlats}) => [diameter, acrossFlats]), [[4, 7], [5, 8], [6, 10], [8, 13], [10, 16], [12, 18]]);
  for (const {diameter, pitches, acrossFlats, thickness} of THREAD_SIZES) {
    assert(pitches.length > 1);
    for (const pitch of pitches) {
      const nut = nutMesh({diameter, pitch});
      withSolid(nut, solid => {
        const pieces = solid.decompose();
        try { assert.equal(pieces.length, 1); }
        finally { pieces.forEach(piece => piece.delete()); }
        const {min, max} = solid.boundingBox();
        near(min[0], -acrossFlats / Math.sqrt(3));
        near(max[0], acrossFlats / Math.sqrt(3));
        near(max[1] - min[1], acrossFlats);
        near(min[2], 0);
        near(max[2], thickness);
        assert.deepEqual(solid.rayCast([0, 0, -1], [0, 0, thickness + 1]), []);
      });
    }
  }
});

test('M8 uses the ISO basic major and minor diameters and a 60 degree flank', () => {
  const p = 1.25, minorRadius = 3.323417653;
  withSolid(nutMesh({clearance: 0, thickness: 5}), solid => {
    near(radiusAt(solid, 2 * p), 4);
    near(radiusAt(solid, 2.5 * p), minorRadius);
    near(radiusAt(solid, 2.25 * p), 3.594050591);
    near(radiusAt(solid, 2.125 * p) - radiusAt(solid, 2.25 * p), Math.sqrt(3) * p / 8);
  });
});

test('one right or left turn advances the respective thread by one pitch', () => {
  for (const pitch of [1.25, .75]) {
    const base = 3 * pitch, quarter = pitch / 4;
    const root = 4.2, crest = root - Math.sqrt(3) * 5 * pitch / 16;
    for (const hand of ['right', 'left']) {
      const direction = hand === 'right' ? 1 : -1;
      withSolid(nutMesh({pitch, hand, thickness: 6 * pitch}), solid => {
        near(radiusAt(solid, base + direction * quarter, Math.PI / 2), root);
        near(radiusAt(solid, base + direction * quarter + pitch, Math.PI / 2), root);
        near(radiusAt(solid, base - direction * quarter, Math.PI / 2), crest);
      });
    }
  }
});

test('clearance is radial, and both entries provide an enlarged lead-in', () => {
  const options = {clearance: .3, thickness: 5};
  withSolid(nutMesh(options), solid => {
    near(radiusAt(solid, 2.5), 4.3);
    near(radiusAt(solid, 3.125), 3.623417653);
    assert(radiusAt(solid, .01, Math.PI / 2) > 4.5);
    assert(radiusAt(solid, 4.99, Math.PI / 2) > 4.5);
  });
});

test('the generated asset survives the editable project format and STL export', () => {
  const nut = nutMesh({diameter: 4, pitch: .5, hand: 'left'});
  assert.match(nut.label, /M4 × 0,5 · venstregevind/);
  const model = new SceneModel(), asset = 'stl-test-nut';
  model.add('mesh', {size: nut.size, asset, name: nut.label, position: [7, -4], bottom: 12});
  const saved = decodeProject(encodeProject(model.data, new Map([[asset, nut.positions]])));
  assert.deepEqual(saved.scene, model.data);
  assert.deepEqual(saved.assets.get(asset), nut.positions);
  const result = createGeometryKernel(wasm).evaluateScene(saved.scene, saved.assets);
  assert.equal(result.parts, 1);
  near(result.bounds.min[2], 12);
  near(result.bounds.max[2], 15.2);
  validateSTL(exportBinaryStl(result));
});

test('unsupported values are rejected and the largest mesh fits existing asset limits', () => {
  assert.match(nutMesh().label, /M8 × 1,25 · højregevind/);
  assert.match(nutMesh({diameter: 4}).label, /M4 × 0,7/);
  for (const options of [{diameter: 7}, {pitch: .9}, {hand: 'clockwise'}, {clearance: -.1}, {clearance: .6}, {thickness: 2.4}, {thickness: 25}, {thickness: NaN}]) {
    assert.equal(nutMesh(options), null);
  }
  assert(nutMesh({diameter: 8, pitch: .5, thickness: 24}).positions.length <= 4_500_000);
});

test('the rod has an external metric thread, closed ends and lead-in at both ends', () => {
  assert.equal(typeof threadedParts.rodMesh, 'function');
  const p = 1.25, length = 13;
  const rod = threadedParts.rodMesh({diameter: 8, pitch: p, length});
  assert.deepEqual(rod.size, [8, 8, length]);
  withSolid(rod, solid => {
    const {min, max} = solid.boundingBox();
    [min[0], min[1], min[2], max[0], max[1], max[2]].forEach((n, i) => near(n, [-4, -4, 0, 4, 4, length][i], .015));
    near(radiusAt(solid, 4 * p), 4, .03);
    near(radiusAt(solid, 4.5 * p), 3.233, .04);
    assert(radiusAt(solid, .01) < 3.4);
    assert(radiusAt(solid, length - .01) < 3.4);
    const cuts = solid.rayCast([0, 0, -1], [0, 0, length + 1]);
    assert.equal(cuts.length, 2);
  });
  assert.equal(threadedParts.rodMesh({length: 101}), null);
  assert.equal(threadedParts.rodMesh({length: 4}), null);
  assert.equal(threadedParts.rodMesh({diameter: 10, pitch: .5}), null);
  assert(threadedParts.rodMesh({diameter: 8, pitch: .5, length: 100}).positions.length <= 4_500_000);
});

test('matching nuts turn along rods without overlap for right and left threads', () => {
  assert.equal(typeof threadedParts.rodMesh, 'function');
  for (const [diameter, pitch, hand] of [[8, 1.25, 'right'], [6, .75, 'left'], [12, 1.75, 'right']]) {
    const spec = {diameter, pitch, hand}, direction = hand === 'right' ? 1 : -1;
    withSolid(threadedParts.rodMesh({...spec, length: 11}), rod => {
      withSolid(nutMesh({...spec, thickness: 4}), nut => {
        for (let step = 0; step <= 12; step++) {
          const fraction = step / 12;
          const rotated = nut.rotate([0, 0, direction * 360 * fraction]);
          const placed = rotated.translate([0, 0, pitch * (2 + fraction)]);
          const overlap = rod.intersect(placed);
          try { assert(overlap.isEmpty(), `M${diameter}, ${hand}, turn ${fraction}: ${overlap.volume()} mm³`); }
          finally { overlap.delete(); placed.delete(); rotated.delete(); }
        }
      });
    });
  }
});

test('the original upright rod geometry is unchanged', () => {
  const ordinary = threadedParts.rodMesh({diameter: 8, pitch: 1.25, length: 30});
  const explicitUpright = threadedParts.rodMesh({diameter: 8, pitch: 1.25, length: 30, lying: false});
  assert.deepEqual(explicitUpright, ordinary);
  assert.equal(createHash('sha256').update(Buffer.from(ordinary.positions.buffer)).digest('hex'),
    'd9db5848ddcb66e943aea5c08d94efcb9b2f6a33a20e576f9bf8b22ebd57f9e2');
});

test('a lying rod is an exact closed trim with a bed face extending through both chamfers', () => {
  const spec = {diameter: 8, pitch: 1.25, length: 13}, height = 7.0625;
  const flat = threadedParts.rodMesh({...spec, lying: true});
  assert.deepEqual(flat.size, [13, 8, height]);
  withSolid(flat, horizontal => {
    const pieces = horizontal.decompose();
    try { assert.equal(pieces.length, 1); }
    finally { pieces.forEach(piece => piece.delete()); }
    const {min, max} = horizontal.boundingBox();
    assert.deepEqual(min, [-6.5, -4, 0]);
    assert.deepEqual(max, [6.5, 4, height]);
    for (let i = 0; i <= 40; i++) {
      const x = -6.49 + 12.98 * i / 40;
      const [hit] = horizontal.rayCast([x, .1, -1], [x, .1, height + 1]);
      near(hit.position[2], 0);
      near(hit.normal[2], -1);
    }
    withSolid(threadedParts.rodMesh(spec), original => {
      const rotated = original.rotate([0, 90, 0]);
      const placed = rotated.translate([-6.5, 0, 3.0625]);
      const bigBox = wasm.Manifold.cube([15, 10, 10], true);
      const aboveBed = bigBox.translate([0, 0, 5]);
      const expected = placed.intersect(aboveBed);
      try {
        assert(horizontal.volume() < original.volume());
        near(horizontal.volume(), expected.volume(), .0002);
      } finally { [expected, aboveBed, bigBox, placed, rotated].forEach(solid => solid.delete()); }
    });
  });
});

test('existing round nuts turn along the horizontal rod in both directions', () => {
  const length = 11;
  for (const [diameter, pitch, hand] of [[8, 1.25, 'right'], [6, .75, 'left']]) {
    const spec = {diameter, pitch, hand}, direction = hand === 'right' ? 1 : -1;
    const cut = diameter / 2 - .75 * pitch;
    withSolid(threadedParts.rodMesh({...spec, length, lying: true}), rod => {
      withSolid(nutMesh({...spec, thickness: 4}), nut => {
        for (const fraction of [0, .15, .37, .53, .79, 1]) {
          const turned = nut.rotate([0, 0, direction * 360 * fraction]);
          const slid = turned.translate([0, 0, pitch * (2 + fraction)]);
          const horizontal = slid.rotate([0, 90, 0]);
          const placed = horizontal.translate([-length / 2, 0, cut]);
          const overlap = rod.intersect(placed);
          try { assert(overlap.isEmpty(), `M${diameter}, ${hand}, turn ${fraction}: ${overlap.volume()} mm³`); }
          finally { [overlap, placed, horizontal, slid, turned].forEach(solid => solid.delete()); }
        }
      });
    });
  }
});

test('the longest fine lying rod fits the existing mesh payload limit', () => {
  const rod = threadedParts.rodMesh({diameter: 8, pitch: .5, length: 100, lying: true});
  assert.deepEqual(rod.size, [100, 8, 7.625]);
  assert(rod.positions.length <= 4_500_000);
});

test('the geometry kernel preserves the horizontal footprint and flat height in exported STL', () => {
  const {positions, size, label} = threadedParts.rodMesh({diameter: 6, pitch: .75, length: 9.5, lying: true});
  const model = new SceneModel(), asset = 'stl-flat-rod';
  model.add('mesh', {asset, size, name: label, position: [7, -4], bottom: 12});
  const result = createGeometryKernel(wasm).evaluateScene(model.data, new Map([[asset, positions]]));
  assert.equal(result.parts, 1);
  [2.25, -7, 12].forEach((value, axis) => near(result.bounds.min[axis], value));
  [11.75, -1, 17.4375].forEach((value, axis) => near(result.bounds.max[axis], value));
  [9.5, 6, 5.4375].forEach((value, axis) => near(validateSTL(exportBinaryStl(result)).size[axis], value));
});
