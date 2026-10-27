// WebGL-independent STL validation shared by the upload flow and the preview.
// The rules mirror app/stl.py so a file rejected in the browser is rejected
// for the same reason when it reaches the print service.
export const MAX_BYTES = 25 * 1024 * 1024;
export const MAX_TRIANGLES = 500_000;
const MAX_COORDINATE = 1_000_000;
const MAX_SIZE = 350;

const error = message => { throw new Error(message); };

function finiteBounds(points, triangles) {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const point of points) {
    for (let axis = 0; axis < 3; axis++) {
      const value = point[axis];
      if (!Number.isFinite(value) || Math.abs(value) > MAX_COORDINATE) error('Modellen indeholder ugyldige koordinater.');
      lo[axis] = Math.min(lo[axis], value);
      hi[axis] = Math.max(hi[axis], value);
    }
  }
  const size = hi.map((value, axis) => value - lo[axis]);
  if (triangles < 4 || Math.min(...size) < 0.01) error('Modellen skal være en rumlig figur med tykkelse.');
  if (Math.max(...size) > MAX_SIZE) error('Modellen er for stor til skolens printere. Gør den mindre i dit tegneprogram.');
  return size;
}

function* binaryPoints(bytes, count) {
  if (count > MAX_TRIANGLES) error('Modellen er for detaljeret. Eksportér en enklere STL-fil.');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let offset = 84; offset < bytes.byteLength; offset += 50) {
    for (let vertex = 0; vertex < 3; vertex++) {
      const base = offset + 12 + vertex * 12;
      yield [view.getFloat32(base, true), view.getFloat32(base + 4, true), view.getFloat32(base + 8, true)];
    }
  }
}

function asciiPoints(bytes) {
  if (bytes.some(value => value > 0x7f)) error('Filen kunne ikke læses som STL. Eksportér modellen som STL igen.');
  let source;
  try { source = new TextDecoder('ascii', {fatal:true}).decode(bytes); }
  catch { error('Filen kunne ikke læses som STL. Eksportér modellen som STL igen.'); }
  if (!/^\s*solid\b/i.test(source) || !/endsolid\b/i.test(source)) error('Filen er ikke en gyldig STL-fil.');
  const matches = [...source.matchAll(/\bvertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)/gi)];
  if (matches.length < 12 || matches.length % 3 || matches.length > MAX_TRIANGLES * 3) error('STL-filen har ugyldige eller for mange trekanter.');
  const count = matches.length / 3;
  if ([...source.matchAll(/\bendfacet\b/gi)].length !== count) error('STL-filen er ufuldstændig.');
  const decimal = /^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$/;
  if (matches.some(match => match.slice(1, 4).some(value => !decimal.test(value)))) error('STL-filen indeholder ugyldige koordinater.');
  const points = matches.map(match => match.slice(1, 4).map(Number));
  return {points, count};
}

export function validateSTL(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  if (!bytes.byteLength || bytes.byteLength > MAX_BYTES) error('Vælg en STL-fil på højst 25 MB.');
  let count = 0, points;
  if (bytes.byteLength >= 84) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    count = view.getUint32(80, true);
    if (count && 84 + count * 50 === bytes.byteLength) points = binaryPoints(bytes, count);
  }
  if (!points) {
    const parsed = asciiPoints(bytes);
    points = parsed.points;
    count = parsed.count;
  }
  const size = finiteBounds(points, count);
  return {size: size.map(value => Math.round(value * 100) / 100), triangles: count, bytes: bytes.byteLength};
}
