import assert from 'node:assert/strict';
import {validateSTL} from '../web/stl-validation.mjs';

function binarySTL(points) {
  const buffer = new ArrayBuffer(84 + points.length / 3 * 50), view = new DataView(buffer);
  view.setUint32(80, points.length / 3, true);
  points.forEach((point, i) => {
    const offset = 84 + Math.floor(i / 3) * 50 + 12 + (i % 3) * 12;
    point.forEach((value, axis) => view.setFloat32(offset + axis * 4, value, true));
  });
  return buffer;
}
const tetra = [
  [0,0,0],[10,0,0],[0,10,0], [0,0,0],[0,10,0],[0,0,10],
  [0,0,0],[0,0,10],[10,0,0], [10,0,0],[0,0,10],[0,10,0]
];
const ascii = `solid tetra\n${Array.from({length:4}, (_, i) => `facet normal 0 0 0\n outer loop\n vertex ${tetra[i*3][0]} ${tetra[i*3][1]} ${tetra[i*3][2]}\n vertex ${tetra[i*3+1][0]} ${tetra[i*3+1][1]} ${tetra[i*3+1][2]}\n vertex ${tetra[i*3+2][0]} ${tetra[i*3+2][1]} ${tetra[i*3+2][2]}\n endloop\nendfacet`).join('\n')}\nendsolid tetra`;

assert.deepEqual(validateSTL(binarySTL(tetra)).size, [10, 10, 10]);
assert.equal(validateSTL(new TextEncoder().encode(ascii)).triangles, 4);
const truncatedBinary = new Uint8Array(binarySTL(tetra)).slice(0, -1);
const fakePng = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const highBitAscii = new TextEncoder().encode(ascii.replace('solid tetra', 'solid t\u0080tra'));
const hexAscii = new TextEncoder().encode(ascii.replace('vertex 0 0 0', 'vertex 0x0 0 0'));
for (const bad of [
  new TextEncoder().encode('this is a renamed text file'),
  new TextEncoder().encode(ascii.slice(0, -10)),
  fakePng,
  truncatedBinary,
  highBitAscii,
  hexAscii,
  binarySTL(tetra.map((point, i) => i === 0 ? [NaN, ...point.slice(1)] : point)),
  binarySTL(tetra.map((point, i) => i === 0 ? [Infinity, ...point.slice(1)] : point)),
]) assert.throws(() => validateSTL(bad), /STL|koordinater|gyldig|ufuldstændig/);
console.log('STL validation tests: OK');
