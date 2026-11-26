import {spurGearMesh} from './spur-gear.js';
import {THREAD_SIZES, nutMesh, rodMesh} from './threaded-nut.js';

const defaults = {
  gear: {teeth: 24, module: 1.5, pressureAngle: 20, backlash: .15, thickness: 5, bore: 5},
  nut: {diameter: 8, pitch: 1.25, hand: 'right', clearance: .2, thickness: THREAD_SIZES.find(size => size.diameter === 8).thickness},
  rod: {diameter: 8, pitch: 1.25, hand: 'right', length: 30},
};
export const mechanicalMeshes = {gear: spurGearMesh, nut: nutMesh, rod: rodMesh};

export function defaultMechanicalPart(kind) {
  const settings = defaults[kind];
  return {settings, mesh: mechanicalMeshes[kind](settings)};
}
