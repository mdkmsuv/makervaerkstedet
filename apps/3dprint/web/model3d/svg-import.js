// Reads an SVG drawing into flat outlines for the 3D workshop. Filled areas count as areas, and drawn lines become bands as wide as the line.
import {SVGLoader} from 'three/addons/loaders/SVGLoader.js';

// How much the transforms around an element scale a line drawn in it. The parsed drawing is put on the page for a moment,
// out of sight, so the browser can work the transforms out; without that, lines keep their written width.
function lineScales(root) {
  if (!root || typeof document === 'undefined') return () => 1;
  const holder = document.createElement('div'); holder.style.cssText = 'position:fixed;left:-10000px;top:0;width:100px;height:100px;overflow:hidden;visibility:hidden';
  const copy = document.importNode(root, true), sources = [root, ...root.querySelectorAll('*')], copies = [copy, ...copy.querySelectorAll('*')];
  holder.append(copy); document.body.append(holder);
  const scales = new Map();
  try {
    const base = copy.getScreenCTM()?.inverse();
    sources.forEach((source, i) => {
      const matrix = base && copies[i].getScreenCTM?.(); if (!matrix) return;
      const m = base.multiply(matrix), scale = Math.sqrt(Math.abs(m.a * m.d - m.b * m.c));
      if (Number.isFinite(scale) && scale > 0) scales.set(source, scale);
    });
  } catch { /* keep the written widths */ }
  holder.remove();
  return node => scales.get(node) ?? 1;
}

const area = points => points.reduce((sum, [x, y], i) => { const [nx, ny] = points[(i + 1) % points.length]; return sum + x * ny - nx * y; }, 0) / 2;

// Returns {id, data, width, height}: data packs every outline as [count, x, y, x, y, …]; outer outlines turn
// counter-clockwise and holes clockwise. A line is packed as [-count, width, x, y, …]. Throws an Error with a Danish message for the pupil.
export function readSvg(text) {
  if (typeof text !== 'string' || !/<svg[\s>]/i.test(text)) throw new Error('Filen er ikke en SVG-tegning.');
  let parsed; try { parsed = new SVGLoader().parse(text); } catch { throw new Error('SVG-tegningen kunne ikke læses.'); }
  const contours = [], lines = [], scaleOf = lineScales(parsed.xml);
  for (const path of parsed.paths) {
    const style = path.userData?.style || {}, painted = value => value && value !== 'none' && value !== 'transparent';
    const lineWidth = style.strokeWidth * scaleOf(path.userData?.node);
    if (painted(style.stroke) && lineWidth > 0) for (const sub of path.subPaths) {
      const points = sub.getPoints(16).map(p => [p.x, -p.y]).filter((p, i, all) => i === 0 || Math.hypot(p[0] - all[i - 1][0], p[1] - all[i - 1][1]) > 1e-6);
      if (sub.autoClose && points.length > 2) points.push(points[0]);
      if (points.length >= 2) lines.push({width: lineWidth, points});
    }
    if (!painted(style.fill)) continue;
    for (const shape of SVGLoader.createShapes(path)) {
      const {shape: outer, holes} = shape.extractPoints(16);
      // SVG counts y downwards; the plate counts it away from the pupil.
      const add = (points, outward) => {
        const flat = points.map(p => [p.x, -p.y]).filter((p, i, all) => i === 0 || Math.hypot(p[0] - all[i - 1][0], p[1] - all[i - 1][1]) > 1e-6);
        if (flat.length > 3 && Math.hypot(flat[0][0] - flat.at(-1)[0], flat[0][1] - flat.at(-1)[1]) < 1e-6) flat.pop();
        if (flat.length < 3 || Math.abs(area(flat)) < 1e-9) return;
        if ((area(flat) > 0) !== outward) flat.reverse(); contours.push(flat);
      };
      add(outer, true); holes.forEach(hole => add(hole, false));
    }
  }
  if (!contours.length && !lines.length) throw new Error('Tegningen har ingen flader eller streger, der kan blive til en figur. Tekst skal laves om til stier i tegneprogrammet.');
  const points = [...contours.flat(), ...lines.flatMap(line => line.points)];
  // The size is that of what will be built: filled outlines as they are, and every line widened by half its width.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const reach = (x, y, pad) => { minX = Math.min(minX, x - pad); maxX = Math.max(maxX, x + pad); minY = Math.min(minY, y - pad); maxY = Math.max(maxY, y + pad); };
  for (const contour of contours) for (const [x, y] of contour) reach(x, y, 0);
  for (const line of lines) for (const [x, y] of line.points) reach(x, y, line.width / 2);
  const width = maxX - minX, height = maxY - minY;
  if (!(width > 1e-6 && height > 1e-6)) throw new Error('Tegningen er for lille eller helt flad.');
  // The same limits as the geometry kernel, so a drawing that is read can also be built.
  const segments = lines.reduce((sum, line) => sum + line.points.length - 1, 0), numbers = contours.length + lines.length * 2 + points.length * 2;
  if (numbers > 2_000_000 || segments > 20_000) throw new Error('Tegningen har for mange detaljer. Prøv en enklere tegning.');
  const data = new Float32Array(contours.length + lines.length * 2 + points.length * 2); let at = 0;
  for (const contour of contours) { data[at++] = contour.length; for (const [x, y] of contour) { data[at++] = x; data[at++] = y; } }
  for (const line of lines) { data[at++] = -line.points.length; data[at++] = line.width; for (const [x, y] of line.points) { data[at++] = x; data[at++] = y; } }
  // Every number counts in the fingerprint, so a drawing changed in one place gets its own id.
  let hash = 2166136261, mix = 7; for (let i = 0; i < data.length; i++) { const v = Math.round(data[i] * 1000); hash = Math.imul(hash ^ v, 16777619) >>> 0; mix = (mix * 31 + v) % 1000000007; }
  return {id: `svg-${data.length.toString(36)}-${hash.toString(36)}-${mix.toString(36)}`, data, width, height};
}
