import {validateSTL} from '../stl-validation.mjs';
/* Reads an STL file into a plain triangle list in millimetres. The geometry worker later joins it into a solid. */

// A short content fingerprint, so the same file imported twice shares one stored copy.
function fingerprint(bytes) {
  // Every byte counts, so a file changed in one place gets its own id and is not mixed up with the old one.
  let hash = 2166136261, mix = 7; for (let i = 0; i < bytes.length; i++) { hash = Math.imul(hash ^ bytes[i], 16777619) >>> 0; mix = (mix * 31 + bytes[i]) % 1000000007; }
  return `stl-${bytes.length.toString(36)}-${hash.toString(36)}-${mix.toString(36)}`;
}

export function readStl(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer), info = validateSTL(bytes);
  let positions = null;
  if (bytes.byteLength >= 84) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), count = view.getUint32(80, true);
    if (count && 84 + count * 50 === bytes.byteLength) {
      positions = new Float32Array(count * 9);
      for (let face = 0; face < count; face++) for (let corner = 0; corner < 3; corner++) for (let axis = 0; axis < 3; axis++)
        positions[face * 9 + corner * 3 + axis] = view.getFloat32(84 + face * 50 + 12 + corner * 12 + axis * 4, true);
    }
  }
  if (!positions) {
    const values = [...new TextDecoder('ascii').decode(bytes).matchAll(/\bvertex\s+(\S+)\s+(\S+)\s+(\S+)/gi)].flatMap(match => match.slice(1, 4).map(Number));
    positions = Float32Array.from(values);
  }
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < positions.length; i++) { const axis = i % 3; min[axis] = Math.min(min[axis], positions[i]); max[axis] = Math.max(max[axis], positions[i]); }
  return {id: fingerprint(bytes), positions, triangles: info.triangles, size: max.map((value, i) => value - min[i])};
}
