import {ShapeUtils, Vector2} from 'three';

// Workshop sizes in mm. The first pitch is coarse; the others are fine options from ISO 261.
// Hex widths across flats and default heights use the ISO 4032 dimensions.
export const THREAD_SIZES = [
  {diameter: 4, pitches: [.7, .5], acrossFlats: 7, thickness: 3.2},
  {diameter: 5, pitches: [.8, .5], acrossFlats: 8, thickness: 4.7},
  {diameter: 6, pitches: [1, .75, .5], acrossFlats: 10, thickness: 5.2},
  {diameter: 8, pitches: [1.25, 1, .75, .5], acrossFlats: 13, thickness: 6.8},
  {diameter: 10, pitches: [1.5, 1.25, 1, .75], acrossFlats: 16, thickness: 8.4},
  {diameter: 12, pitches: [1.75, 1.5, 1.25, 1], acrossFlats: 18, thickness: 10.8},
];

const ANGLES = 96;
const STEPS_PER_TURN = 16;
const FLANK_SLOPE = Math.sqrt(3);
const SIXTH_TURN = Math.PI / 3;
const spokes = Array.from({length: ANGLES}, (_, index) => {
  const turn = index / ANGLES;
  const angle = 2 * Math.PI * turn;
  const faceNormal = (Math.floor(index / (ANGLES / 6)) + .5) * SIXTH_TURN;
  return {turn, x: Math.cos(angle), y: Math.sin(angle), hexFactor: 1 / Math.cos(angle - faceNormal)};
});
const rodSpokes = spokes.filter((_, index) => index % 2 === 0);
export const ROD_LENGTH = {min: 5, max: 100};

// ISO 68-1 and ISO 724 basic internal thread: H = sqrt(3)/2 · P, minor radius = D/2 - 5/8 · H.
// The major-radius flat spans P/8, the minor-radius flat P/4, and the flanks have a 60° included angle.
// See https://www.bossard.com/-/media/bossard-group/website/documents/technical-resources/en/f-079-en.pdf
function profileInset(turn, pitch) {
  const fromRoot = Math.abs(turn - Math.round(turn));
  const flank = Math.min(5 / 16, Math.max(0, fromRoot - 1 / 16));
  return FLANK_SLOPE * pitch * flank;
}

// Returns an outward-wound triangle soup, centred on XY and based at Z=0.
// Clearance is added to the radius, not the diameter. A size's coarse pitch is the default.
export function nutMesh(options = {}) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) return null;
  const {diameter = 8, hand = 'right', clearance = .2} = options;
  const standard = THREAD_SIZES.find(thread => thread.diameter === diameter);
  if (!standard) return null;
  const {pitch = standard.pitches[0], thickness = standard.thickness} = options;
  if (!standard.pitches.includes(pitch) || !['right', 'left'].includes(hand) || !Number.isFinite(clearance)
      || clearance < 0 || clearance > .5 || !Number.isFinite(thickness) || thickness < Math.max(2, 2 * pitch) || thickness > 24) return null;

  const steps = Math.ceil(STEPS_PER_TURN * thickness / pitch);
  const majorRadius = diameter / 2 + clearance;
  const direction = hand === 'right' ? 1 : -1;
  const apothem = standard.acrossFlats / 2;
  const outerRing = z => spokes.map(({x, y, hexFactor}) => [x * apothem * hexFactor, y * apothem * hexFactor, z]);
  function innerRing(z) {
    const chamferRadius = majorRadius + pitch / 4 - FLANK_SLOPE * Math.min(z, thickness - z);
    return spokes.map(({x, y, turn}) => {
      const threadRadius = majorRadius - profileInset(z / pitch - direction * turn, pitch);
      const radius = Math.max(chamferRadius, threadRadius);
      return [x * radius, y * radius, z];
    });
  }

  // Each pair of rings makes two triangles per segment. Reuse common vertices to close the seam exactly.
  const positions = new Float32Array((steps + 3) * ANGLES * 18);
  let offset = 0;
  function connect(first, second) {
    for (let i = 0; i < ANGLES; i++) {
      const next = (i + 1) % ANGLES;
      for (const vertex of [first[i], first[next], second[next], first[i], second[next], second[i]]) {
        positions.set(vertex, offset);
        offset += 3;
      }
    }
  }
  const base = outerRing(0), top = outerRing(thickness);
  let inner = innerRing(0);
  connect(base, top);
  connect(inner, base);
  for (let step = 1; step <= steps; step++) {
    const next = innerRing(step === steps ? thickness : thickness * step / steps);
    connect(next, inner);
    inner = next;
  }
  connect(top, inner);
  const pitchText = String(pitch).replace('.', ',');
  const handText = hand === 'right' ? 'højregevind' : 'venstregevind';
  return {positions, size: [2 * standard.acrossFlats / FLANK_SLOPE, standard.acrossFlats, thickness], label: `Møtrik M${diameter} × ${pitchText} · ${handText}`};
}

// External profile with the same flank and phase as the bore. The circular
// root joins the flank tangentially and clears the crest of the internal thread.
function rodInset(turn, pitch) {
  const phase = Math.abs(turn - Math.round(turn));
  if (phase <= 3 / 8) return profileInset(turn, pitch);
  const height = FLANK_SLOPE * pitch / 2;
  const rootRadius = height / 6;
  const fromRootCenter = (1 / 2 - phase) * pitch;
  return 13 * height / 24 + Math.sqrt(rootRadius ** 2 - fromRootCenter ** 2);
}

function flatCap(contour, triangle) {
  const faces = ShapeUtils.triangulateShape(contour.map(([x, y]) => new Vector2(x, y)), []);
  if (!faces.length) return false;
  const used = new Set(faces.flat()), extra = new Map();
  // Earcut may omit collinear boundary points. Put them back so each cap edge
  // meets the matching edge of the original clipped facets without a T-junction.
  for (const first of used) {
    const chain = [];
    let last = (first + 1) % contour.length;
    while (!used.has(last)) { chain.push(last); last = (last + 1) % contour.length; }
    if (!chain.length) continue;
    extra.set(`${first},${last}`, chain);
    extra.set(`${last},${first}`, [...chain].reverse());
  }
  for (const face of faces) {
    const [a, b, c] = face.map(index => contour[index]);
    const orientation = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    if (orientation > 0) [face[1], face[2]] = [face[2], face[1]];
    const edges = face.flatMap((start, i) => [start, ...(extra.get(`${start},${face[(i + 1) % 3]}`) || [])]);
    if (edges.length === 3) { triangle(...edges.map(index => contour[index])); continue; }
    const midpoint = [Math.fround((a[0] + b[0] + c[0]) / 3), Math.fround((a[1] + b[1] + c[1]) / 3), 0];
    edges.forEach((start, i) => triangle(midpoint, contour[start], contour[edges[(i + 1) % edges.length]]));
  }
  return true;
}

function horizontalRod(source, length, plane) {
  const output = [], boundary = new Map();
  const min = [Infinity, Infinity, 0], max = [-Infinity, -Infinity, 0];
  const at = (i) => [Math.fround(source[i + 2] - length / 2), source[i + 1], Math.fround(plane - source[i])];
  const same = (a, b) => a.every((value, axis) => value === b[axis]);
  const key = ([x, y]) => `${x},${y}`;
  function intersection(a, b) {
    // Use a fixed order so adjacent triangles calculate exactly the same Float32 cut point.
    if (a[2] > b[2]) [a, b] = [b, a];
    if (!b[2]) return b;
    const t = -a[2] / (b[2] - a[2]);
    return [Math.fround(a[0] + t * (b[0] - a[0])), Math.fround(a[1] + t * (b[1] - a[1])), 0];
  }
  function triangle(a, b, c) {
    if (same(a, b) || same(a, c) || same(b, c)) return;
    output.push(...a, ...b, ...c);
    for (let axis = 0; axis < 3; axis++) {
      min[axis] = Math.min(min[axis], a[axis], b[axis], c[axis]);
      max[axis] = Math.max(max[axis], a[axis], b[axis], c[axis]);
    }
  }
  for (let i = 0; i < source.length; i += 9) {
    const original = [at(i), at(i + 3), at(i + 6)], clipped = [];
    for (let j = 0; j < 3; j++) {
      const a = original[j], b = original[(j + 1) % 3];
      if (a[2] >= 0) clipped.push(a);
      if ((a[2] < 0) !== (b[2] < 0)) clipped.push(intersection(a, b));
    }
    const points = clipped.filter((point, index) => !same(point, clipped[(index + clipped.length - 1) % clipped.length]));
    if (points.length < 3) continue;
    for (let j = 1; j + 1 < points.length; j++) triangle(points[0], points[j], points[j + 1]);
    points.forEach((a, i) => {
      const b = points[(i + 1) % points.length];
      if (a[2] || b[2]) return;
      const start = key(a), end = key(b);
      if (boundary.get(end)?.next === start) boundary.delete(end);
      else boundary.set(start, {point: a, next: end});
    });
  }

  const contour = [], start = boundary.keys().next().value;
  let current = start;
  while (boundary.has(current)) {
    const {point, next} = boundary.get(current);
    contour.push(point);
    boundary.delete(current);
    current = next;
  }
  if (current !== start || boundary.size || contour.length < 3 || !flatCap(contour, triangle)) return null;
  const size = max.map((value, axis) => value - min[axis]);
  const axisOffset = min.slice(0, 2).map((value, axis) => (value + max[axis]) / 2);
  return {positions: Float32Array.from(output), size, ...(axisOffset.some(Boolean) ? {axisOffset} : {})};
}

export function rodMesh(options = {}) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) return null;
  const {diameter = 8, hand = 'right', length = 30} = options;
  const standard = THREAD_SIZES.find(thread => thread.diameter === diameter);
  if (!standard) return null;
  const {pitch = standard.pitches[0]} = options;
  if (!standard.pitches.includes(pitch) || !['right', 'left'].includes(hand) || !Number.isFinite(length)
      || length < ROD_LENGTH.min || length > ROD_LENGTH.max) return null;

  const steps = Math.ceil(length / pitch * STEPS_PER_TURN);
  const majorRadius = diameter / 2;
  const endRadius = majorRadius - 17 * FLANK_SLOPE * pitch / 48;
  const direction = hand === 'right' ? 1 : -1;
  const ring = z => {
    const chamfer = endRadius + FLANK_SLOPE * Math.min(z, length - z);
    return rodSpokes.map(({x, y, turn}) => {
      const radius = Math.min(chamfer, majorRadius - rodInset(z / pitch - direction * turn, pitch));
      return [x * radius, y * radius, z];
    });
  };

  const count = rodSpokes.length;
  const positions = new Float32Array((steps + 1) * count * 18);
  let offset = 0;
  function triangle(a, b, c) {
    positions.set(a, offset); positions.set(b, offset + 3); positions.set(c, offset + 6);
    offset += 9;
  }
  const base = [0, 0, 0], top = [0, 0, length];
  let current = ring(0);
  for (let i = 0; i < count; i++) triangle(base, current[(i + 1) % count], current[i]);
  for (let step = 1; step <= steps; step++) {
    const upper = ring(step === steps ? length : step * pitch / STEPS_PER_TURN);
    for (let i = 0; i < count; i++) {
      const next = (i + 1) % count;
      triangle(current[i], current[next], upper[next]);
      triangle(current[i], upper[next], upper[i]);
    }
    current = upper;
  }
  for (let i = 0; i < count; i++) triangle(top, current[i], current[(i + 1) % count]);
  const pitchText = String(pitch).replace('.', ',');
  const handText = hand === 'right' ? 'højregevind' : 'venstregevind';
  const label = `Gevindstang M${diameter} × ${pitchText} · ${handText}`;
  if (options.lying === true) {
    // The cut is slightly below the root, so the flat remains continuous even through the chamfered ends.
    const shape = horizontalRod(positions, length, majorRadius - .75 * pitch);
    return shape ? {...shape, label} : null;
  }
  return {positions, size: [diameter, diameter, length], label};
}
