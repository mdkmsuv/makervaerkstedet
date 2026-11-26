import {Matrix4, Vector3} from 'three';

const OVERLAP = .5;
const MIN_SIZE = .01;
const MAX_VALUE = 1_000_000;

function projectedRadius(halfSize, axis) {
  return halfSize.x * Math.abs(axis.x) + halfSize.y * Math.abs(axis.y) + halfSize.z * Math.abs(axis.z);
}

// Both a cylinder and a box use local Z for depth and are centred on their local origin.
export function faceHolePlacement(point, normal, bounds) {
  if (!point?.isVector3 || !normal?.isVector3 || !bounds?.isBox3 || bounds.isEmpty()) return null;
  const normalLength = normal.length();
  if (!(normalLength > 0 && Number.isFinite(normalLength))) return null;

  const z = normal.clone().divideScalar(normalLength);
  // Keep the box upright on walls and aligned to the plate axes on horizontal faces.
  const up = Math.abs(z.z) > .95 ? new Vector3(0, 1, 0) : new Vector3(0, 0, 1);
  const x = new Vector3().crossVectors(up, z).normalize();
  const y = new Vector3().crossVectors(z, x).normalize();

  const halfSize = bounds.getSize(new Vector3()).multiplyScalar(.5);
  const length = 2 * (projectedRadius(halfSize, z) + OVERLAP);
  const span = 2 * Math.min(projectedRadius(halfSize, x), projectedRadius(halfSize, y));
  const diameter = Math.min(10, span / 2, Math.max(.1, span / 4));
  if (!(diameter >= MIN_SIZE && length <= MAX_VALUE)) return null;

  // Shift the cutter only along the normal, keeping the axis on the hit point.
  const offset = bounds.getCenter(new Vector3()).sub(point).dot(z);
  const center = point.clone().addScaledVector(z, offset);
  const matrix = new Matrix4().makeBasis(x, y, z).setPosition(center).toArray();
  if (!matrix.every(value => Number.isFinite(value) && Math.abs(value) <= MAX_VALUE)) return null;
  return {matrix, size: [diameter, diameter, length]};
}
