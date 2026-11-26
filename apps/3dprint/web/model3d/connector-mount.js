import {Matrix4} from 'three';

export function connectorMount(shape, settings, face) {
  const hole = shape.mode === 'hole';
  const anchor = (hole ? -1 : 1) * shape.size[2] / 2;
  const axisOffset = shape.axisOffset || [0, 0];
  // A solid overlaps its host for union. A cutter extends past the opening.
  const depth = anchor + (hole ? .05 : -.35);
  return {
    kind: 'mesh', name: shape.label, size: shape.size, mode: shape.mode,
    generator: {kind: 'connector', settings, origin: [...axisOffset, anchor]},
    matrix: face.clone().multiply(new Matrix4().makeTranslation(...axisOffset, depth)).toArray(),
  };
}
