import {ShapeUtils, Vector2} from 'three';

const TAU = 2 * Math.PI;

export function arc(radius, start, sweep, segments) {
  return Array.from({length: segments + 1}, (_, index) => {
    const angle = start + sweep * index / segments;
    return [radius * Math.cos(angle), radius * Math.sin(angle)];
  });
}

function addFace(faces, a, b, c) {
  const ab = b.map((value, axis) => value - a[axis]);
  const ac = c.map((value, axis) => value - a[axis]);
  if (Math.hypot(ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]) > 1e-10) {
    faces.push(a, b, c);
  }
}

function joinRings(faces, low, high) {
  for (let i = 0; i < low.length; i++) {
    const next = (i + 1) % low.length;
    addFace(faces, low[i], low[next], high[next]);
    addFace(faces, low[i], high[next], high[i]);
  }
}

function finish(faces, transform = point => point) {
  const points = faces.map(transform), min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (const point of points) for (let axis = 0; axis < 3; axis++) {
    min[axis] = Math.min(min[axis], point[axis]); max[axis] = Math.max(max[axis], point[axis]);
  }
  if (!points.length) return null;
  const size = max.map((value, axis) => value - min[axis]);
  if (!size.every(value => value >= .01)) return null;
  const center = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, min[2]];
  const positions = new Float32Array(points.length * 3);
  points.forEach((point, index) => point.forEach((value, axis) => { positions[index * 3 + axis] = value - center[axis]; }));
  return {size, positions};
}

// Sections have the same number of vertices, in counterclockwise order from above.
export function loftedShape(sections) {
  const faces = [];
  for (let i = 1; i < sections.length; i++) joinRings(faces, sections[i - 1], sections[i]);
  const low = sections[0], high = sections.at(-1);
  for (let i = 1; i < low.length - 1; i++) {
    addFace(faces, low[0], low[i + 1], low[i]);
    addFace(faces, high[0], high[i], high[i + 1]);
  }
  return finish(faces);
}

export function extrudedProfile(points, height, standing = false, triangulated = null) {
  let outline = points.map(([x, y]) => new Vector2(x, y));
  if (ShapeUtils.isClockWise(outline)) outline = outline.reverse();
  const caps = triangulated ?? ShapeUtils.triangulateShape(outline, []).map(face => face.map(index => outline[index].toArray()));
  const low = outline.map(({x, y}) => [x, y, 0]), high = outline.map(({x, y}) => [x, y, height]), faces = [];
  for (const [a, b, c] of caps) {
    const sign = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    const [second, third] = sign >= 0 ? [b, c] : [c, b];
    addFace(faces, [...a, 0], [...third, 0], [...second, 0]);
    addFace(faces, [...a, height], [...second, height], [...third, height]);
  }
  joinRings(faces, low, high);
  return finish(faces, standing ? ([x, y, z]) => [x, -z, y] : undefined);
}

// Section order is bottom to top. A zero-radius section joins to a single pole.
export function revolvedShape(sections, sides = 48) {
  const unit = arc(1, 0, TAU, sides).slice(0, -1);
  const rings = sections.map(([radiusX, radiusY, height]) => unit.map(([x, y]) => [x * radiusX, y * radiusY, height]));
  const faces = [];
  for (let i = 1; i < rings.length; i++) joinRings(faces, rings[i - 1], rings[i]);
  const low = rings[0], high = rings.at(-1);
  for (let i = 0; i < sides; i++) {
    const next = (i + 1) % sides;
    addFace(faces, [0, 0, low[0][2]], low[next], low[i]);
    addFace(faces, [0, 0, high[0][2]], high[i], high[next]);
  }
  return finish(faces);
}

export function torusShape(diameter, tube, height, degrees = 360) {
  const outer = diameter / 2, crossRadius = tube / 2, main = outer - crossRadius;
  const radial = arc(1, 0, TAU, 24).slice(0, -1);
  const sweep = degrees * Math.PI / 180, segments = Math.ceil(degrees / 5);
  const center = arc(1, degrees === 360 ? 0 : -sweep / 2, sweep, segments);
  if (degrees === 360) center.pop();
  const rings = center.map(([x, y]) => radial.map(([radialX, z]) => [x * (main + crossRadius * radialX), y * (main + crossRadius * radialX), height / 2 * z]));
  const faces = [];
  for (let i = 1; i < rings.length; i++) joinRings(faces, rings[i], rings[i - 1]);
  if (degrees === 360) joinRings(faces, rings[0], rings.at(-1));
  else {
    const first = rings[0], last = rings.at(-1);
    const start = [center[0][0] * main, center[0][1] * main, 0], end = [center.at(-1)[0] * main, center.at(-1)[1] * main, 0];
    for (let i = 0; i < radial.length; i++) {
      const next = (i + 1) % radial.length;
      addFace(faces, start, first[i], first[next]);
      addFace(faces, end, last[next], last[i]);
    }
  }
  return finish(faces);
}
