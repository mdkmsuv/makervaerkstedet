import {textContours,TextGeometryError} from './text-geometry.js';
/* Pure millimetre geometry. Rendering, history and project storage live elsewhere. */
const DEFAULT_COLOR = '#c92e35';
const KINDS = new Set(['box', 'cylinder', 'sphere', 'cone', 'wedge', 'tube', 'roof', 'halfsphere', 'pyramid', 'star', 'heart', 'text', 'mesh', 'group']);
const MESSAGES = {
  tom_tekst: 'Skriv noget tekst til modellen.',
  for_lang_tekst: 'Brug højst 40 tegn i teksten.',
  ugyldig_tekst: 'Teksten indeholder tegn, som ikke kan laves til figurer.',
  for_detaljeret_tekst: 'Teksten er for detaljeret. Brug færre tegn eller mindre tekst.',
  ukendt_skrifttype: 'Skrifttypen er ikke klar. Prøv at åbne værkstedet igen.',
  ukombinerede_huller: 'Kombinér hullerne med de solide figurer, før du henter STL.',
  ugyldig_scene: 'Modellen kunne ikke læses. Fortryd den seneste ændring.',
  ugyldige_maal: 'Brug positive mål og gyldige tal til figuren.',
  tom_gruppe: 'Gruppen skal indeholde mindst én solid figur.',
  tomt_resultat: 'Hullerne fjerner hele figuren. Gør hullet mindre eller flyt det.',
  geometri_fejlede: 'Figurerne kunne ikke smeltes sammen. Fortryd eller flyt dem lidt.',
  for_stor_model: 'Modellen er for detaljeret. Brug færre figurer.',
  stl_ikke_lukket: 'STL-filen er ikke en lukket figur, så den kan ikke bruges her. Prøv en anden fil.',
  ukendt_fil: 'En hentet fil mangler. Hent den ind igen.',
  svg_tom: 'Tegningen har ingen flader, der kan blive til en figur.',
  svg_for_stor: 'Tegningen har for mange detaljer. Prøv en enklere tegning.',
};

export class GeometryError extends Error {
  constructor(code) { super(MESSAGES[code] || MESSAGES.geometri_fejlede); this.code = code; }
}
function need(condition, code = 'ugyldig_scene') { if (!condition) throw new GeometryError(code); }
function vector(value, positive = false) {
  need(Array.isArray(value) && value.length === 3 && value.every(x => Number.isFinite(x)
    && Math.abs(x) <= 1_000_000 && (!positive || x >= 0.01)), 'ugyldige_maal');
  return value;
}
function transform(node, naturalSize=[1,1,1]) {
  if (node.size !== undefined) vector(node.size, true);
  if (node.position !== undefined) vector(node.position);
  if (node.rotation !== undefined) vector(node.rotation);
  if (node.matrix !== undefined) {
    const m = node.matrix;
    need(Array.isArray(m) && m.length === 16 && m.every(x => Number.isFinite(x) && Math.abs(x) <= 1_000_000), 'ugyldige_maal');
    need(m[3] === 0 && m[7] === 0 && m[11] === 0 && m[15] === 1, 'ugyldige_maal');
    const determinant = m[0]*(m[5]*m[10]-m[9]*m[6]) - m[4]*(m[1]*m[10]-m[9]*m[2]) + m[8]*(m[1]*m[6]-m[5]*m[2]);
    need(Math.abs(determinant) >= 1e-9, 'ugyldige_maal');
    return m.slice();
  }
  const size=[1,1,1], p=vector(node.position || [0,0,node.kind==='group'?0:naturalSize[2]/2]);
  const angles = vector(node.rotation || [0,0,0]).map(x => x*Math.PI/180);
  const [x,y,z] = angles, a=Math.cos(x), b=Math.sin(x), c=Math.cos(y), d=Math.sin(y), e=Math.cos(z), f=Math.sin(z);
  // Intrinsic XYZ, matching Three Euler('XYZ'): T * Rx * Ry * Rz * S.
  return [c*e*size[0], (a*f+b*e*d)*size[0], (b*f-a*e*d)*size[0], 0,
    -c*f*size[1], (a*e-b*f*d)*size[1], (b*e+a*f*d)*size[1], 0,
    d*size[2], -b*c*size[2], a*c*size[2], 0, p[0],p[1],p[2],1];
}
function sceneGraph(scene) {
  need(scene && scene.format === 'makervaerkstedet-3d' && scene.version === 2 && scene.units === 'mm');
  need(scene.plate?.w === 180 && scene.plate?.d === 180);
  need(Array.isArray(scene.objects) && scene.objects.length <= 512 && Array.isArray(scene.roots));
  const nodes = new Map(), parents = new Set();
  for (const node of scene.objects) {
    need(node && typeof node.id === 'string' && /^[a-zA-Z0-9_-]{1,80}$/.test(node.id) && !nodes.has(node.id));
    need(KINDS.has(node.kind) && ['solid','hole'].includes(node.mode || 'solid'));
    need(node.hidden === undefined || typeof node.hidden === 'boolean'); need(node.locked === undefined || typeof node.locked === 'boolean');
    need(node.color === undefined || (typeof node.color === 'string' && /^#[0-9a-f]{6}$/i.test(node.color)));
    transform(node);
    if(node.kind!=='group'&&node.kind!=='text')vector(node.size,true);
    if(node.kind==='box'&&node.params!==undefined)need(node.params&&Number.isFinite(node.params.round)&&node.params.round>=0&&node.params.round<=500,'ugyldige_maal');
    if((node.kind==='cylinder'||node.kind==='cone')&&node.params!==undefined)need(node.params&&Number.isInteger(node.params.sides)&&node.params.sides>=3&&node.params.sides<=64,'ugyldige_maal');
    if(node.kind==='tube'&&node.params!==undefined)need(node.params&&Number.isFinite(node.params.wall)&&node.params.wall>0&&node.params.wall<=500,'ugyldige_maal');
    if(node.kind==='mesh')need(typeof node.asset==='string'&&/^(stl|svg)-[a-z0-9-]{1,40}$/.test(node.asset));
    nodes.set(node.id, node);
  }
  for (const node of nodes.values()) if (node.kind === 'group') {
    need(['union','difference'].includes(node.op) && Array.isArray(node.children) && node.children.length > 0 && node.children.length <= 512);
    for (const id of node.children) { need(nodes.has(id) && !parents.has(id)); parents.add(id); }
  }
  const roots = new Set(scene.roots); need(roots.size === scene.roots.length);
  for (const id of roots) need(nodes.has(id) && !parents.has(id));
  const visited = new Set();
  function visit(id, depth) {
    need(depth <= 32 && !visited.has(id)); visited.add(id);
    const node = nodes.get(id);
    if (node.kind === 'group') node.children.forEach(child => visit(child, depth+1));
  }
  roots.forEach(id => visit(id, 0)); need(visited.size === nodes.size);
  return { nodes, roots };
}

// How much a node's own matrix stretches its local X, Y and Z axes.
function stretch(node) {
  const m = node.matrix; if (!m) return [1, 1, 1];
  return [Math.hypot(m[0], m[1], m[2]), Math.hypot(m[4], m[5], m[6]), Math.hypot(m[8], m[9], m[10])];
}
// Applies a 4×4 column-major matrix to mesh arrays. A mirroring matrix also turns the triangles, so they keep facing out.
function transformArrays({positions, indices}, m) {
  const out = new Float32Array(positions.length);
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i], y = positions[i + 1], z = positions[i + 2];
    out[i] = m[0] * x + m[4] * y + m[8] * z + m[12]; out[i + 1] = m[1] * x + m[5] * y + m[9] * z + m[13]; out[i + 2] = m[2] * x + m[6] * y + m[10] * z + m[14];
  }
  const det = m[0] * (m[5] * m[10] - m[9] * m[6]) - m[4] * (m[1] * m[10] - m[9] * m[2]) + m[8] * (m[1] * m[6] - m[5] * m[2]), turned = new Uint32Array(indices);
  if (det < 0) for (let i = 0; i < turned.length; i += 3) { const t = turned[i + 1]; turned[i + 1] = turned[i + 2]; turned[i + 2] = t; }
  return {positions: out, indices: turned};
}

// `cache` (number of entries, 0 = off) keeps finished, unplaced results between calls, keyed by what a part is made of.
// Moving or turning a combined part then reuses its booleans. Cached WASM objects stay alive until they are evicted
// or `clearCache()` is called; without a cache nothing outlives a call.
export function createGeometryKernel(module, {fonts={}, cache: cacheSize=0}={}) {
  const { Manifold, Mesh, CrossSection } = module;
  need(typeof Manifold?.cube === 'function' && typeof Mesh === 'function');
  const cache = new Map();
  const drop = entry => entry.objects.forEach(solid => solid.delete());
  return {
    clearCache() { cache.forEach(drop); cache.clear(); },
    get cacheSize() { return cache.size; },
    // `assets` maps an imported file's id to its triangle list (9 numbers per triangle).
    evaluateScene(scene, assets = new Map()) {
      const { nodes, roots } = sceneGraph(scene), owned = new Set(), missed = new Set(), edge = new Set();
      let primitiveTriangles=0;
      const own = solid => { owned.add(solid); return solid; };
      // A kept object leaves this call's clean-up and belongs to a cache entry instead.
      const keep = (entry, solid) => { if (cacheSize) { owned.delete(solid); entry.objects.add(solid); } return solid; };
      const remember = (key, entry) => { if (cacheSize && key) { cache.delete(key); cache.set(key, entry); } return entry; };
      const recall = key => { if (!cacheSize || !key || !cache.has(key)) return null; const entry = cache.get(key); cache.delete(key); cache.set(key, entry); return entry; };
      function checked(solid, emptyCode = 'tomt_resultat') {
        need(solid.status() === 'NoError', 'geometri_fejlede');
        need(solid.numTri()<=500_000,'for_stor_model');
        need(!solid.isEmpty(), emptyCode); return solid;
      }
      function unite(solids) {
        need(solids.length > 0, 'tom_gruppe');
        return solids.length === 1 ? solids[0] : own(Manifold.union(solids));
      }
      function material(entries, found) {
        const positive = entries.filter(x => x.mode !== 'hole').map(x => x.solid);
        const negative = entries.filter(x => x.mode === 'hole').map(x => x.solid);
        const result = unite(positive);
        for (const hole of entries.filter(x => x.mode === 'hole')) {
          if (!positive.length) continue;
          if (own(result.intersect(hole.solid)).isEmpty()) { found.missed.push(hole.id); continue; }
          const solidBox = result.boundingBox(), holeBox = hole.solid.boundingBox();
          if ([0, 1].some(i => holeBox.min[i] < solidBox.min[i] - 1e-6 || holeBox.max[i] > solidBox.max[i] + 1e-6)) found.edge.push(hole.id);
        }
        return checked(negative.length ? own(Manifold.difference([result, unite(negative)])) : result);
      }
      function primitive(node) {
        const kind=node.kind;
        if(kind==='text'){
          need(node.params?.font==='mkv-sans' && fonts['mkv-sans'], 'ukendt_skrifttype');
          const section=own(new CrossSection(textContours(fonts['mkv-sans'],node.params),'NonZero'));
          return own(section.extrude(node.params.depth,0,0,[1,1],true));
        }
        if (kind === 'tube' && node.params?.wall > 0) {
          // A wall in millimetres. The node's own stretch is divided out, so the wall is as thick as asked, also on a stretched tube.
          const [sx, sy] = stretch(node), [w, d, h] = node.size, tx = node.params.wall / sx, ty = node.params.wall / sy;
          const outer = own(own(CrossSection.circle(1, 64)).scale([w / 2, d / 2]));
          if (tx >= w / 2 - 1e-3 || ty >= d / 2 - 1e-3) return own(outer.extrude(h, 0, 0, [1, 1], true));
          const inner = own(own(CrossSection.circle(1, 64)).scale([w / 2 - tx, d / 2 - ty]));
          return own(own(outer.subtract(inner)).extrude(h, 0, 0, [1, 1], true));
        }
        if (kind === 'box' && node.params?.round > 0) {
          // Rounded upright edges with a radius in millimetres. The node's own stretch is divided out, so corners stay round.
          const [sx, sy] = stretch(node), [w, d, h] = node.size, rx = Math.min(node.params.round / sx, w / 2 - 1e-4), ry = Math.min(node.params.round / sy, d / 2 - 1e-4);
          if (rx > 1e-4 && ry > 1e-4) {
            const corner = (x, y) => own(own(own(CrossSection.circle(1, 24)).scale([rx, ry])).translate([x, y]));
            const outline = own(CrossSection.hull([corner(w / 2 - rx, d / 2 - ry), corner(rx - w / 2, d / 2 - ry), corner(rx - w / 2, ry - d / 2), corner(w / 2 - rx, ry - d / 2)]));
            return own(outline.extrude(h, 0, 0, [1, 1], true));
          }
        }
        if (kind === 'box') return own(Manifold.cube(node.size, true));
        const scaled=solid=>own(solid.transform([node.size[0],0,0,0,0,node.size[1],0,0,0,0,node.size[2],0,0,0,0,1]));
        if (kind === 'mesh' && node.asset.startsWith('svg-')) {
          // A drawing: outlines packed as [count, x, y, x, y, …, count, …]. Outer outlines turn one way and holes the other,
          // so the parts with a positive winding are filled. A line is packed as [-count, width, x, y, …] and becomes a band
          // with round ends and corners. The result fills the unit square and is pulled up to the unit height.
          const data = assets.get(node.asset); need(data instanceof Float32Array && data.length >= 6, 'ukendt_fil'); need(data.length <= 2_000_000, 'svg_for_stor');
          const contours = [], pieces = []; let at = 0, segments = 0;
          while (at < data.length) {
            const count = data[at++]; need(Number.isInteger(count) && Math.abs(count) >= 2, 'ukendt_fil');
            if (count > 0) {
              need(count >= 3 && at + 2 * count <= data.length, 'ukendt_fil');
              contours.push(Array.from({length: count}, (_, k) => [data[at + 2 * k], data[at + 2 * k + 1]])); at += 2 * count; continue;
            }
            const n = -count, radius = data[at++] / 2; need(radius > 0 && at + 2 * n <= data.length, 'ukendt_fil'); segments += n - 1; need(segments <= 20_000, 'svg_for_stor');
            const dot = k => own(own(CrossSection.circle(radius, 12)).translate([data[at + 2 * k], data[at + 2 * k + 1]]));
            for (let k = 0; k + 1 < n; k++) pieces.push(own(CrossSection.hull([dot(k), dot(k + 1)])));
            at += 2 * n;
          }
          if (contours.length) pieces.push(own(new CrossSection(contours, 'Positive')));
          need(pieces.length > 0, 'svg_tom');
          const section = pieces.length === 1 ? pieces[0] : own(CrossSection.union(pieces)); need(section.area() > 1e-9, 'svg_tom');
          const b = section.bounds(), w = b.max[0] - b.min[0], d = b.max[1] - b.min[1]; need(w > 1e-9 && d > 1e-9, 'svg_tom');
          const unit = own(own(section.translate([-(b.min[0] + b.max[0]) / 2, -(b.min[1] + b.max[1]) / 2])).scale([1 / w, 1 / d]));
          return scaled(own(unit.extrude(1, 0, 0, [1, 1], true)));
        }
        if (kind === 'mesh') {
          // An imported file is joined into a closed solid and fitted to the unit box like the other shapes.
          const positions = assets.get(node.asset);
          need(positions instanceof Float32Array && positions.length >= 36 && positions.length % 9 === 0 && positions.length <= 4_500_000, 'ukendt_fil');
          const mesh = new Mesh({numProp: 3, vertProperties: positions.slice(), triVerts: Uint32Array.from({length: positions.length / 3}, (_, i) => i)}); mesh.merge();
          let solid; try { solid = own(new Manifold(mesh)); } catch { throw new GeometryError('stl_ikke_lukket'); }
          need(solid.status() === 'NoError' && !solid.isEmpty() && solid.volume() > 0, 'stl_ikke_lukket');
          const box = solid.boundingBox(), size = [0, 1, 2].map(i => box.max[i] - box.min[i]), center = [0, 1, 2].map(i => (box.min[i] + box.max[i]) / 2);
          need(size.every(value => value > 1e-6), 'stl_ikke_lukket');
          const unit = own(solid.transform([1 / size[0], 0, 0, 0, 0, 1 / size[1], 0, 0, 0, 0, 1 / size[2], 0, -center[0] / size[0], -center[1] / size[1], -center[2] / size[2], 1]));
          return own(unit.transform([node.size[0],0,0,0,0,node.size[1],0,0,0,0,node.size[2],0,0,0,0,1]));
        }
        if (kind === 'sphere') return scaled(own(Manifold.sphere(0.5, 48)));
        if ((kind === 'cylinder' || kind === 'cone') && node.params?.sides) {
          // A prism or a pointed pyramid with this many sides; one flat side faces the front, and the outline fills the unit square.
          const outline = own(own(CrossSection.circle(.5, node.params.sides)).rotate(270 - 180 / node.params.sides)), b = outline.bounds(), w = b.max[0] - b.min[0], d = b.max[1] - b.min[1];
          const unit = own(own(outline.translate([-(b.min[0] + b.max[0]) / 2, -(b.min[1] + b.max[1]) / 2])).scale([1 / w, 1 / d]));
          return scaled(own(unit.extrude(1, 0, 0, kind === 'cone' ? [0, 0] : [1, 1], true)));
        }
        if (kind === 'cylinder' || kind === 'cone') return scaled(own(Manifold.cylinder(1, 0.5, kind === 'cone' ? 0 : 0.5, 32, true)));
        // Every shape fills the unit box centred on the origin before it is scaled to node.size.
        if (kind === 'tube') return scaled(own(Manifold.difference([own(Manifold.cylinder(1, .5, .5, 48, true)), own(Manifold.cylinder(1.2, .32, .32, 48, true))])));
        if (kind === 'halfsphere') return scaled(own(own(own(Manifold.sphere(.5, 48)).trimByPlane([0, 0, 1], 0)).transform([1,0,0,0, 0,1,0,0, 0,0,2,0, 0,0,-.5,1])));
        if (kind === 'roof') return scaled(own(Manifold.hull([[-.5,-.5,-.5],[.5,-.5,-.5],[.5,.5,-.5],[-.5,.5,-.5],[-.5,0,.5],[.5,0,.5]])));
        if (kind === 'pyramid') return scaled(own(Manifold.hull([[-.5,-.5,-.5],[.5,-.5,-.5],[.5,.5,-.5],[-.5,.5,-.5],[0,0,.5]])));
        // Flat outlines: drawn in 2D, stretched to fill the unit square and pulled up to the unit height.
        const filled = section => {
          const b = section.bounds(), w = b.max[0] - b.min[0], d = b.max[1] - b.min[1];
          const unit = own(own(section.translate([-(b.min[0] + b.max[0]) / 2, -(b.min[1] + b.max[1]) / 2])).scale([1 / w, 1 / d]));
          return scaled(own(unit.extrude(1, 0, 0, [1, 1], true)));
        };
        if (kind === 'star') {
          // Five points; the inner corners lie at 45 % of the outer radius.
          const points = Array.from({length: 10}, (_, i) => { const r = i % 2 ? .45 : 1, a = Math.PI / 2 + i * Math.PI / 5; return [r * Math.cos(a), r * Math.sin(a)]; });
          return filled(own(new CrossSection([points], 'Positive')));
        }
        if (kind === 'heart') {
          // Two round lobes, each pulled down to one shared tip.
          const lobe = x => own(own(CrossSection.circle(.3, 48)).translate([x, .18])), tip = own(own(CrossSection.circle(.01, 8)).translate([0, -.62]));
          return filled(own(own(CrossSection.hull([lobe(-.26), tip])).add(own(CrossSection.hull([lobe(.26), tip])))));
        }
        const mesh = new Mesh({ numProp:3,
          vertProperties:new Float32Array([-.5,-.5,-.5, .5,-.5,-.5, .5,.5,-.5, -.5,.5,-.5, -.5,-.5,.5, .5,-.5,.5]),
          triVerts:new Uint32Array([0,2,1, 0,3,2, 0,1,5, 0,5,4, 4,5,2, 4,2,3, 0,4,3, 1,2,5]) });
        return scaled(own(new Manifold(mesh)));
      }
      // What a part is made of, without where it is: the cache key. A group includes its parts' places.
      function shape(id) {
        const node = nodes.get(id);
        if (node.kind !== 'group') return {k: node.kind, s: node.size, p: ['text', 'box', 'cylinder', 'cone', 'tube'].includes(node.kind) ? node.params : undefined, a: node.asset, st: (node.kind === 'box' && node.params?.round > 0) || (node.kind === 'tube' && node.params?.wall > 0) ? stretch(node).map(v => Math.round(v * 1e6) / 1e6) : undefined};
        return {k: 'group', op: node.op, c: node.color, ch: node.children.filter(child => !nodes.get(child).hidden).map(child => {
          const part = nodes.get(child); return {d: shape(child), m: part.matrix, pos: part.position, rot: part.rotation, mode: part.mode || 'solid', c: part.color};
        })};
      }
      // The unplaced result of a part: its solid, its coloured pieces and what its holes did.
      function unplaced(id) {
        const node = nodes.get(id), key = cacheSize ? JSON.stringify(shape(id)) : null, hit = recall(key);
        if (hit) { primitiveTriangles += hit.tris; need(primitiveTriangles <= 500_000, 'for_stor_model'); return {...hit, key}; }
        const entry = {objects: new Set(), missed: [], edge: [], tris: 0, pieces: null, paints: undefined}, before = primitiveTriangles;
        if (node.kind === 'group') {
          const entries = node.children.map(evaluate).filter(Boolean);
          if (!entries.length) return null;
          entry.solid = keep(entry, material(entries, entry));
          for (const child of entries) { entry.missed.push(...child.missed); entry.edge.push(...child.edge); }
          entry.pieces = entries.filter(x => x.mode !== 'hole').flatMap(x => x.pieces).map(piece => ({color: piece.color, solid: keep(entry, piece.solid)}));
        } else {
          entry.solid = keep(entry, checked(primitive(node))); primitiveTriangles += entry.solid.numTri();
          need(primitiveTriangles <= 500_000, 'for_stor_model');
        }
        entry.tris = primitiveTriangles - before;
        const local = entry.solid.boundingBox(); entry.localBounds = {min: [...local.min], max: [...local.max]};
        return {...remember(key, entry), key};
      }
      function evaluate(id) {
        const node = nodes.get(id); if (node.hidden) return null;
        const part = unplaced(id); if (!part) return null;
        const naturalSize = part.localBounds.max.map((value, i) => value - part.localBounds.min[i]), matrix = transform(node, naturalSize);
        const placed = checked(own(part.solid.transform(matrix)));
        // Leaves keep their colour; a group with its own colour is one piece; otherwise the parts keep theirs.
        const pieces = node.kind === 'group' && !node.color ? part.pieces.map(piece => ({color: piece.color, solid: own(piece.solid.transform(matrix))})) : [{color: node.color || DEFAULT_COLOR, solid: placed}];
        return {id, mode: node.mode || 'solid', localBounds: part.localBounds, naturalSize, solid: placed, pieces, missed: part.missed, edge: part.edge, matrix, part, color: node.color};
      }
      function arrays(solid) {
        const mesh = solid.getMesh(), count = mesh.vertProperties.length/mesh.numProp;
        need(count <= 500_000 && mesh.triVerts.length/3 <= 500_000, 'for_stor_model');
        const positions = new Float32Array(count*3);
        for (let i = 0; i < count; i++) for (let axis=0; axis<3; axis++) positions[i*3+axis] = mesh.vertProperties[i*mesh.numProp+axis];
        need(positions.every(Number.isFinite), 'geometri_fejlede');
        return { positions, indices:new Uint32Array(mesh.triVerts) };
      }
      try {
        const entries = [...roots].map(evaluate).filter(Boolean);
        for (const entry of entries) { entry.missed.forEach(id => missed.add(id)); entry.edge.forEach(id => edge.add(id)); }
        // Display-only paint: disjoint pieces of a combined part, each cut to the part's real shape, worked out
        // unplaced and cached with the part, then placed. The STL stays one solid.
        function paints(entry) {
          const part = entry.part, pieces = part.pieces;
          if (entry.mode === 'hole' || entry.color || !pieces || pieces.length < 2 || pieces.length > 16 || new Set(pieces.map(x => x.color)).size < 2) return undefined;
          if (part.paints === undefined) {
            const result = []; let covered = null;
            for (const piece of pieces) {
              let cut = own(part.solid.intersect(piece.solid)); if (covered) cut = own(cut.subtract(covered));
              covered = covered ? own(covered.add(piece.solid)) : piece.solid;
              if (!cut.isEmpty()) result.push({color: piece.color, ...arrays(cut)});
            }
            part.paints = result.length > 1 ? result : null;
            if (part.key && cache.has(part.key)) cache.get(part.key).paints = part.paints;
          }
          return part.paints ? part.paints.map(paint => ({color: paint.color, ...transformArrays(paint, entry.matrix)})) : undefined;
        }
        const meshes = entries.map(entry => ({id: entry.id, mode: entry.mode, localBounds: entry.localBounds, naturalSize: entry.naturalSize, ...arrays(entry.solid), paints: paints(entry)}));
        const uncombinedHoles=entries.filter(x=>x.mode==='hole').map(x=>x.id),exportBlocked=uncombinedHoles.length>0;
        if (!entries.some(x => x.mode !== 'hole')) return {meshes,merged:null,bounds:null,volume:0,parts:0,warnings:['tom_model'],uncombinedHoles,exportBlocked};
        const solids = entries.filter(x => x.mode !== 'hole');
        // The printable model: every solid part melted together. Cached by the parts and their places, so adding a loose
        // hole or changing a colour does not melt a long text again.
        const totalKey = cacheSize ? JSON.stringify(solids.map(x => [x.id, x.part.key, x.matrix])) : null;
        let total = recall(totalKey);
        if (!total) {
          const solid = unite(solids.map(x => x.solid)), volume = solid.volume();
          need(Number.isFinite(volume) && volume > 0, 'geometri_fejlede');
          const parts = solid.decompose(); parts.forEach(own);
          const box = solid.boundingBox();
          let looseRoots = [];
          if (parts.length > 1 && solids.length > 1) {
            // A part is loose when the printed piece it belongs to holds no other part. Parts that touch share a piece.
            const overlaps = (a, b) => [0, 1, 2].every(i => a.min[i] <= b.max[i] + 1e-6 && b.min[i] <= a.max[i] + 1e-6);
            const pieceOf = solids.map(entry => { const bounds = entry.solid.boundingBox(); return parts.findIndex(piece => overlaps(bounds, piece.boundingBox()) && !own(entry.solid.intersect(piece)).isEmpty()); });
            looseRoots = solids.filter((entry, i) => pieceOf.every((piece, j) => j === i || piece !== pieceOf[i])).map(entry => entry.id);
          }
          total = remember(totalKey, {objects: new Set(), merged: arrays(solid), volume, parts: parts.length, bounds: {min: [...box.min], max: [...box.max]}, looseRoots});
        }
        // Fresh copies: the worker hands the arrays over to the page, which empties them here.
        const merged = {positions: total.merged.positions.slice(), indices: total.merged.indices.slice()};
        return {meshes,uncombinedHoles,exportBlocked, merged, bounds: {min: [...total.bounds.min], max: [...total.bounds.max]}, volume: total.volume, parts: total.parts, missedHoles:[...missed], edgeHoles:[...edge], looseRoots: [...total.looseRoots], warnings:[]};
      } catch (error) {
        if (error instanceof GeometryError) throw error;
        if (error instanceof TextGeometryError) throw new GeometryError(error.code);
        throw new GeometryError('geometri_fejlede');
      } finally {
        // Typed arrays above are independent copies. Only cache entries keep WASM objects, and only the newest ones.
        [...owned].reverse().forEach(solid => solid.delete());
        while (cache.size > cacheSize) { const [oldest, entry] = cache.entries().next().value; cache.delete(oldest); drop(entry); }
      }
    },
  };
}

export function exportBinaryStl(result) {
  need(result && result.exportBlocked===false, 'ukombinerede_huller');
  const mesh=result.merged;
  need(mesh && mesh.positions instanceof Float32Array && mesh.indices instanceof Uint32Array, 'geometri_fejlede');
  const {positions,indices} = mesh, triangles=indices.length/3;
  need(positions.length%3 === 0 && indices.length%3 === 0 && triangles >= 4 && triangles <= 500_000, 'geometri_fejlede');
  need(positions.every(x => Number.isFinite(x) && Math.abs(x) <= 1_000_000), 'geometri_fejlede');
  need(indices.every(x => x < positions.length/3), 'geometri_fejlede');
  const buffer = new ArrayBuffer(84+50*triangles), view = new DataView(buffer);
  new Uint8Array(buffer).set(new TextEncoder().encode('Makervaerkstedet 3D'));
  view.setUint32(80, triangles, true);
  for (let face=0; face<triangles; face++) {
    const a=indices[face*3]*3, b=indices[face*3+1]*3, c=indices[face*3+2]*3;
    const u=[0,1,2].map(i=>positions[b+i]-positions[a+i]), v=[0,1,2].map(i=>positions[c+i]-positions[a+i]);
    const normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]], length=Math.hypot(...normal);
    // A sliver can lose its area in float32; it then gets a zero normal, which slicers recompute.
    let offset=84+face*50;
    normal.forEach(x=>{view.setFloat32(offset,length>0?x/length:0,true);offset+=4;});
    for (const index of [a,b,c]) for (let axis=0; axis<3; axis++) {view.setFloat32(offset,positions[index+axis],true);offset+=4;}
  }
  return buffer;
}
