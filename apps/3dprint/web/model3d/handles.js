import * as THREE from 'three';
/* Pure handle geometry and drag maths in millimetres. The view draws the handles; nothing here touches the DOM. */

// Nine size handles in the part's local frame: four bottom corners, four bottom sides and one on top.
// `scale` tells which side of each local axis the handle drags (+1 max side, -1 min side, 0 untouched).
export function resizeHandles(box) {
  const c = box.min.map((v, i) => (v + box.max[i]) / 2), side = (i, s) => s > 0 ? box.max[i] : s < 0 ? box.min[i] : c[i], handles = [];
  for (const sy of [-1, 1]) for (const sx of [-1, 1]) handles.push({type: 'resize', corner: true, scale: [sx, sy, 0], at: [side(0, sx), side(1, sy), box.min[2]]});
  for (const s of [-1, 1]) {
    handles.push({type: 'resize', scale: [s, 0, 0], at: [side(0, s), c[1], box.min[2]]});
    handles.push({type: 'resize', scale: [0, s, 0], at: [c[0], side(1, s), box.min[2]]});
  }
  handles.push({type: 'resize', scale: [0, 0, 1], at: [c[0], c[1], box.max[2]]});
  return handles;
}
// The local point that stays put: the opposite side of each dragged axis, the centre of the others and always the bottom.
export function handlePivot(box, scale) {
  return [0, 1, 2].map(i => scale[i] > 0 ? box.min[i] : scale[i] < 0 ? box.max[i] : i === 2 ? box.min[2] : (box.min[i] + box.max[i]) / 2);
}
// New local scale factors when a handle has moved `delta` local units. Sizes snap in mm and stay within 0,1–1000 mm.
// With `uniform`, the axis that changed most sets one factor for all three axes.
export function resizeFactors(box, scaleVector, scale, delta, {snap = 0, uniform = false} = {}) {
  const size = [0, 1, 2].map(i => (box.max[i] - box.min[i]) * scaleVector[i]), factor = [1, 1, 1];
  // A drag never makes a part thinner than 1 mm (or than it already is); a typed measure can still go lower.
  const limit = (value, i) => Math.max(Math.min(1, size[i]), Math.min(1000, value));
  for (let i = 0; i < 3; i++) if (scale[i]) {
    let value = size[i] + scale[i] * delta[i] * scaleVector[i];
    if (snap) value = Math.round(value / snap) * snap;
    factor[i] = limit(value, i) / size[i];
  }
  if (uniform) {
    let common = [0, 1, 2].filter(i => scale[i]).reduce((best, i) => Math.abs(factor[i] - 1) > Math.abs(best - 1) ? factor[i] : best, 1);
    common = Math.max(...size.map(value => Math.min(1, value) / value), Math.min(common, ...size.map(value => 1000 / value)));
    factor.fill(common);
  }
  return {factor, size: size.map((value, i) => value * factor[i])};
}
export const snapAngle = (angle, step) => step ? Math.round(angle / step) * step : angle;
export function rotationAbout(center, axis, degrees) {
  return new THREE.Matrix4().makeTranslation(center.x, center.y, center.z)
    .multiply(new THREE.Matrix4().makeRotationAxis(axis, THREE.MathUtils.degToRad(degrees)))
    .multiply(new THREE.Matrix4().makeTranslation(-center.x, -center.y, -center.z));
}
// Lowest z of world-space vertices after a world transform.
export function lowestZ(positions, matrix) {
  const e = matrix.elements; let low = Infinity;
  for (let i = 0; i < positions.length; i += 3) low = Math.min(low, e[2] * positions[i] + e[6] * positions[i + 1] + e[10] * positions[i + 2] + e[14]);
  return low;
}
// Highest z of world-space vertices after a world transform.
export function highestZ(positions, matrix) {
  const e = matrix.elements; let high = -Infinity;
  for (let i = 0; i < positions.length; i += 3) high = Math.max(high, e[2] * positions[i] + e[6] * positions[i + 1] + e[10] * positions[i + 2] + e[14]);
  return high;
}
// Rotation planes around world axes. e1 × e2 is the axis, so a positive angle turns e1 towards e2.
export const ROTATION_AXES = [
  {axis: new THREE.Vector3(1, 0, 0), e1: new THREE.Vector3(0, 1, 0), e2: new THREE.Vector3(0, 0, 1), name: 'X'},
  {axis: new THREE.Vector3(0, 1, 0), e1: new THREE.Vector3(0, 0, 1), e2: new THREE.Vector3(1, 0, 0), name: 'Y'},
  {axis: new THREE.Vector3(0, 0, 1), e1: new THREE.Vector3(1, 0, 0), e2: new THREE.Vector3(0, 1, 0), name: 'Z'},
];
// Signed angle in degrees of `point` around `center` in the plane of a rotation axis.
export function planeAngle(plane, center, point) {
  const v = point.clone().sub(center); return THREE.MathUtils.radToDeg(Math.atan2(v.dot(plane.e2), v.dot(plane.e1)));
}
// Keeps a drag angle continuous when atan2 jumps between +180 and -180 degrees.
export const unwrap = (previous, next) => next + 360 * Math.round((previous - next) / 360);
