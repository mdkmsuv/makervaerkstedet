import {ShapeUtils, Vector2} from 'three';

const PLATE_DIAMETER_LIMIT = 180;
const MAX_TEETH = 80;
const MIN_MODULE = .5;
const MAX_MODULE = 4;
const MIN_THICKNESS = 2;
const MAX_THICKNESS = 20;
const MIN_BORE = .1;
const TAU = 2 * Math.PI;
const FLANK_STEPS = 12;
const radians = degrees => degrees * Math.PI / 180;
const between = (value, min, max) => Number.isFinite(value) && value >= min && value <= max;

// Standard full-depth, unshifted geometry. Restrict the rack's depth at the
// base circle to avoid undercut. Root clearance is 0.25 times the module.
export function spurGearLimits(options = {}) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) return null;
  const {teeth = 24, module = 1.5, pressureAngle = 20} = options;
  if (!between(module, MIN_MODULE, MAX_MODULE) || ![20, 25].includes(pressureAngle)) return null;
  const minTeeth = Math.ceil(2 / Math.sin(radians(pressureAngle)) ** 2);
  const maxTeeth = Math.min(MAX_TEETH, Math.floor(PLATE_DIAMETER_LIMIT / module - 2 + 1e-9));
  const validTeeth = Number.isInteger(teeth) && between(teeth, minTeeth, maxTeeth);
  const pitchDiameter = validTeeth ? module * teeth : null;
  const outsideDiameter = validTeeth ? module * (teeth + 2) : null;
  const maxBore = validTeeth ? pitchDiameter - 2.5 * module - 2 * Math.max(1, module) : 0;
  return {
    minTeeth, maxTeeth, minModule: MIN_MODULE, maxModule: MAX_MODULE,
    minThickness: MIN_THICKNESS, maxThickness: MAX_THICKNESS,
    minBore: MIN_BORE, maxBore, maxBacklash: .4 * module, pitchDiameter, outsideDiameter,
  };
}

function polar(radius, angle) {
  return new Vector2(radius * Math.cos(angle), radius * Math.sin(angle));
}

function outline({teeth, module, pressureAngle, backlash}, pitchRadius, outsideRadius) {
  const pressure = radians(pressureAngle);
  const root = pitchRadius - 1.25 * module;
  const base = pitchRadius * Math.cos(pressure);
  const toothStep = TAU / teeth;
  const involute = t => t - Math.atan(t);
  const parameter = radius => Math.sqrt(Math.max(0, (radius / base) ** 2 - 1));
  const start = parameter(Math.max(root, base));
  const pitch = Math.tan(pressure);
  const tip = parameter(outsideRadius);
  // Both gears in a matching pair contribute half the requested circular
  // backlash. Divide again to distribute that reduction between both flanks.
  const baseHalfAngle = toothStep / 4 - backlash / (4 * pitchRadius) + involute(pitch);
  const flank = [start];
  for (const [from, to] of [[start, pitch], [pitch, tip]]) {
    for (let i = 1; i <= FLANK_STEPS; i++) flank.push(from + (to - from) * i / FLANK_STEPS);
  }
  const radii = flank.map(t => base * Math.hypot(1, t));
  const halfAngles = flank.map(t => baseHalfAngle - involute(t));
  const rootHalfAngle = halfAngles[0];
  const tipHalfAngle = halfAngles.at(-1);
  const vertices = [];
  for (let tooth = 0; tooth < teeth; tooth++) {
    const center = tooth * toothStep;
    // The involute ends at its base circle. Continue radially to the root
    // circle when needed, with the standard clearance below the mating tip.
    if (root < base) vertices.push(polar(root, center - rootHalfAngle));
    radii.forEach((radius, i) => vertices.push(polar(radius, center - halfAngles[i])));
    for (let i = 1; i <= 4; i++) vertices.push(polar(outsideRadius, center + tipHalfAngle * (i / 2 - 1)));
    for (let i = radii.length - 2; i >= 0; i--) vertices.push(polar(radii[i], center + halfAngles[i]));
    if (root < base) vertices.push(polar(root, center + rootHalfAngle));
    for (let i = 1; i < 6; i++) vertices.push(polar(root, center + rootHalfAngle + (toothStep - 2 * rootHalfAngle) * i / 6));
  }
  return vertices;
}

function extrude(outside, bore, thickness) {
  const boundaries = bore.length ? [outside, bore] : [outside];
  const cap = ShapeUtils.triangulateShape(outside, bore.length ? [bore] : []);
  const points = boundaries.flat();
  const positions = new Float32Array(18 * (cap.length + points.length));
  let offset = 0;
  function write(point, z) {
    positions[offset++] = point.x;
    positions[offset++] = point.y;
    positions[offset++] = z;
  }
  for (const triangle of cap) {
    const [a, b, c] = triangle.map(index => points[index]);
    const forward = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x) > 0;
    const first = forward ? b : c, second = forward ? c : b;
    write(a, thickness); write(first, thickness); write(second, thickness);
    write(a, 0); write(second, 0); write(first, 0);
  }
  for (const boundary of boundaries) {
    for (let i = 0; i < boundary.length; i++) {
      const a = boundary[i], b = boundary[(i + 1) % boundary.length];
      write(a, 0); write(b, 0); write(b, thickness);
      write(a, 0); write(b, thickness); write(a, thickness);
    }
  }
  return positions;
}

// Mesh origin is the axle in XY and the bottom in Z. axisOffset is the
// bounding box's XY midpoint relative to that axle before mesh normalization.
export function spurGearMesh(options = {}) {
  const limits = spurGearLimits(options);
  if (!limits || limits.pitchDiameter === null) return null;
  const {teeth = 24, module = 1.5, pressureAngle = 20, backlash = .15, thickness = 5, bore = 5} = options;
  if (!between(backlash, 0, limits.maxBacklash) || !between(thickness, limits.minThickness, limits.maxThickness)
      || !(bore === 0 || between(bore, limits.minBore, limits.maxBore))) return null;
  const {pitchDiameter, outsideDiameter} = limits;
  const outside = outline({teeth, module, pressureAngle, backlash}, pitchDiameter / 2, outsideDiameter / 2);
  const hole = bore ? Array.from({length: 128}, (_, i) => polar(bore / 2, -i * TAU / 128)) : [];
  const x = outside.map(point => point.x), y = outside.map(point => point.y);
  const minX = Math.min(...x), maxX = Math.max(...x), minY = Math.min(...y), maxY = Math.max(...y);
  return {
    positions: extrude(outside, hole, thickness),
    size: [maxX - minX, maxY - minY, thickness],
    pitchDiameter, outsideDiameter,
    axisOffset: [(minX + maxX) / 2, (minY + maxY) / 2],
    label: `Tandhjul (${teeth} tænder)`,
  };
}
