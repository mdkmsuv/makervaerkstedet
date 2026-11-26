import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spurGearLimits, spurGearMesh} from '../apps/3dprint/web/model3d/spur-gear.js';

const {default: Module} = await import(process.env.MODEL3D_MANIFOLD_MODULE || 'manifold-3d');
const wasm = await Module();
wasm.setup();
const {Manifold, Mesh} = wasm;
const near = (actual, expected, tolerance) => {
  assert(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected} (tolerance ${tolerance})`);
};

function solid(gear) {
  assert(gear.positions instanceof Float32Array);
  const mesh = new Mesh({
    numProp: 3,
    vertProperties: gear.positions.slice(),
    triVerts: Uint32Array.from({length: gear.positions.length / 3}, (_, i) => i),
  });
  mesh.merge();
  const manifold = new Manifold(mesh);
  assert.equal(manifold.status(), 'NoError');
  assert(manifold.volume() > 0);
  return manifold;
}

test('diameters and pitch-circle tooth thickness follow the standard module and specified backlash', () => {
  const gear = spurGearMesh({teeth: 24, module: 1.5, backlash: .15});
  assert.equal(gear.pitchDiameter, 36);
  assert.equal(gear.outsideDiameter, 39);
  assert.match(gear.label, /^Tandhjul/);
  const halfToothAngles = [];
  for (let i = 0; i < gear.positions.length; i += 3) {
    const x = gear.positions[i], y = gear.positions[i + 1];
    const angle = Math.atan2(y, x);
    if (Math.abs(Math.hypot(x, y) - 18) < 1e-5 && Math.abs(angle) < Math.PI / 24) halfToothAngles.push(angle);
  }
  near((Math.max(...halfToothAngles) - Math.min(...halfToothAngles)) * 18, 1.5 * Math.PI / 2 - .075, .0001);
});

test('the bore remains concentric in a closed mesh with an odd tooth count', () => {
  const options = {teeth: 25, module: 1.5, thickness: 6, bore: 8};
  const gear = spurGearMesh(options), bored = solid(gear), full = solid(spurGearMesh({...options, bore: 0}));
  const shaft = Manifold.cylinder(6, 3.99, 3.99, 48);
  const intersection = Manifold.intersection([bored, shaft]);
  try {
    assert(intersection.isEmpty(), 'an axial shaft fits through the entire thickness');
    near(full.volume() - bored.volume(), Math.PI * 4 ** 2 * 6, .9);
    const {min, max} = bored.boundingBox();
    gear.size.forEach((value, axis) => near(value, max[axis] - min[axis], .0001));
    gear.axisOffset.forEach((value, axis) => near(value, (max[axis] + min[axis]) / 2, .0001));
    assert.equal(min[2], 0);
    assert.equal(max[2], 6);
  } finally {
    for (const shape of [intersection, shaft, full, bored]) shape.delete();
  }
});

test('only supported full-depth teeth, plate sizes and valid clearances generate geometry', () => {
  assert.equal(spurGearLimits({pressureAngle: 20, module: 1.5, teeth: 24}).minTeeth, 18);
  assert.equal(spurGearLimits({pressureAngle: 25, module: 1.5, teeth: 24}).minTeeth, 12);
  for (const options of [null, {teeth: '24'}, {teeth: 24.5}, {teeth: 17}, {teeth: 11, pressureAngle: 25},
    {teeth: 81}, {module: .49}, {module: 4.1}, {teeth: 44, module: 4}, {pressureAngle: 19},
    {thickness: NaN}, {thickness: 1}, {thickness: 21}, {backlash: -.01}, {backlash: .7}, {bore: -1}, {bore: 35}]) {
    assert.equal(spurGearMesh(options), null, JSON.stringify(options));
  }
  assert(spurGearMesh({teeth: 18}));
  assert(spurGearMesh({teeth: 12, pressureAngle: 25}));
  assert(spurGearMesh({teeth: 43, module: 4, bore: 0}));
});

for (const [pressureAngle, leftTeeth, rightTeeth] of [[20, 18, 37], [25, 12, 31]]) {
  test(`${leftTeeth} and ${rightTeeth} teeth mesh at ${pressureAngle} degrees across a full tooth pitch`, () => {
    const module = 1.5, backlash = .12;
    const left = solid(spurGearMesh({teeth: leftTeeth, module, pressureAngle, backlash}));
    const right = solid(spurGearMesh({teeth: rightTeeth, module, pressureAngle, backlash}));
    const centerDistance = module * (leftTeeth + rightTeeth) / 2;
    const rightGapFacingLeft = 180 - 180 / rightTeeth;
    function overlap(angle, phaseShift = 0) {
      const pinion = left.rotate([0, 0, angle]);
      const rotated = right.rotate([0, 0, rightGapFacingLeft - angle * leftTeeth / rightTeeth + phaseShift]);
      const gear = rotated.translate([centerDistance, 0, 0]);
      const intersection = Manifold.intersection([pinion, gear]);
      try { return intersection.volume(); }
      finally { for (const shape of [intersection, gear, rotated, pinion]) shape.delete(); }
    }
    try {
      for (let step = 0; step <= 24; step++) {
        const angle = 360 * step / (24 * leftTeeth);
        near(overlap(angle), 0, .0001);
      }
      assert(overlap(0, 180 / rightTeeth) > .25, 'a half-tooth phase error causes real tooth interference');
      const backlashLimit = backlash / 2 / (module * rightTeeth / 2) * 180 / Math.PI;
      near(overlap(0, backlashLimit * .9), 0, .0001);
      assert(overlap(0, backlashLimit * 1.5) > .0001, 'the flanks engage once the designed backlash is taken up');
    } finally {
      left.delete();
      right.delete();
    }
  });
}
