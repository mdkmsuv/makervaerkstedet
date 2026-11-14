// Editable scene and imported geometry only; no browser/session state or rendered mesh.
const FORMAT = 'makervaerkstedet-3d-project';
const MAX_BYTES = 50 * 1024 * 1024;
const KINDS = new Set(['box','cylinder','sphere','cone','wedge','tube','roof','halfsphere','pyramid','star','heart','text','mesh','group']);
const NODE_FIELDS = new Set(['id','kind','mode','size','position','rotation','matrix','params','asset','name','color','hidden','locked','children','op']);
const ID = /^[a-zA-Z0-9_-]{1,80}$/;
const ASSET_ID = /^(stl|svg)-[a-z0-9-]{1,40}$/;
function need(condition) { if (!condition) throw new Error('3D-projektet er ugyldigt eller for stort. Åbn en gyldig projektfil.'); }
function record(value) { return value !== null && typeof value === 'object' && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype; }
function keys(value, allowed) { need(record(value) && Object.keys(value).every(key => allowed.has(key))); }
function vector(value, length, positive = false) {
  need(Array.isArray(value) && value.length === length && Array.from(value).every(n => Number.isFinite(n) && Math.abs(n) <= 1_000_000 && (!positive || n >= .01)));
}
function validateScene(scene) {
  keys(scene,new Set(['format','version','units','plate','objects','roots']));
  need(scene.format === 'makervaerkstedet-3d' && scene.version === 2 && scene.units === 'mm');
  keys(scene.plate,new Set(['w','d'])); need(scene.plate.w === 180 && scene.plate.d === 180);
  need(Array.isArray(scene.objects) && scene.objects.length <= 512 && Array.isArray(scene.roots) && scene.roots.length <= 512);
  const nodes = new Map(), parents = new Set();
  for (const node of scene.objects) {
    keys(node,NODE_FIELDS);
    need(typeof node.id === 'string' && ID.test(node.id) && !nodes.has(node.id) && KINDS.has(node.kind));
    need(node.mode === undefined || ['solid','hole'].includes(node.mode));
    for (const flag of ['hidden','locked']) need(node[flag] === undefined || typeof node[flag] === 'boolean');
    need(node.color === undefined || (typeof node.color === 'string' && /^#[0-9a-f]{6}$/i.test(node.color)));
    need(node.name === undefined || (typeof node.name === 'string' && node.name.length <= 80));
    for (const field of ['position','rotation']) if (node[field] !== undefined) vector(node[field],3);
    if (node.size !== undefined) vector(node.size,3,true);
    if (node.matrix !== undefined) {
      vector(node.matrix,16); const m = node.matrix;
      need(m[3] === 0 && m[7] === 0 && m[11] === 0 && m[15] === 1);
      need(Math.abs(m[0]*(m[5]*m[10]-m[9]*m[6])-m[4]*(m[1]*m[10]-m[9]*m[2])+m[8]*(m[1]*m[6]-m[5]*m[2])) >= 1e-9);
    }
    if (!['group','text'].includes(node.kind)) vector(node.size,3,true);
    if (node.kind === 'group') {
      need(['union','difference'].includes(node.op) && Array.isArray(node.children) && node.children.length > 0 && node.children.length <= 512);
    } else need(node.children === undefined && node.op === undefined);
    if (node.kind === 'text') {
      keys(node.params,new Set(['text','font','height','depth'])); const p = node.params;
      need(typeof p.text === 'string' && p.text.trim() && [...p.text.normalize('NFC')].length <= 40 && !/[\x00-\x1f\x7f]/.test(p.text));
      need(p.font === 'mkv-sans' && [p.height,p.depth].every(n => Number.isFinite(n) && n >= .01 && n <= 350));
    } else if (node.params !== undefined) {
      const field = {box:'round',cylinder:'sides',cone:'sides',tube:'wall'}[node.kind];
      need(field !== undefined); keys(node.params,new Set([field])); const n = node.params[field];
      need(Number.isFinite(n) && (field === 'sides' ? Number.isInteger(n) && n >= 3 && n <= 64 : n >= (field === 'wall' ? Number.MIN_VALUE : 0) && n <= 500));
    }
    if (node.kind === 'mesh') need(typeof node.asset === 'string' && ASSET_ID.test(node.asset));
    else need(node.asset === undefined);
    nodes.set(node.id,node);
  }
  for (const node of nodes.values()) if (node.kind === 'group') for (const id of node.children) {
    need(typeof id === 'string' && nodes.has(id) && !parents.has(id)); parents.add(id);
  }
  const seen = new Set();
  function visit(id, depth) {
    need(typeof id === 'string' && nodes.has(id) && !seen.has(id) && depth <= 32);
    seen.add(id); const node = nodes.get(id);
    if (node.kind === 'group') node.children.forEach(child => visit(child,depth+1));
  }
  for (const id of scene.roots) { need(!parents.has(id)); visit(id,0); }
  need(seen.size === nodes.size);
}
function validateAsset(id, values) {
  need(typeof id === 'string' && ASSET_ID.test(id));
  need((Array.isArray(values) || values instanceof Float32Array) && values.length <= 4_500_000);
  for (const n of values) need(Number.isFinite(n) && Math.abs(n) <= 3.4028234663852886e38);
  if (id.startsWith('stl-')) {
    need(values.length >= 36 && values.length % 9 === 0);
  } else {
    need(values.length >= 6 && values.length <= 2_000_000);
    let at = 0, segments = 0;
    while (at < values.length) {
      const count = values[at++]; need(Number.isInteger(count) && (count >= 3 || count <= -2));
      if (count < 0) { need(values[at++] > 0); segments += -count-1; need(segments <= 20_000); }
      at += Math.abs(count)*2; need(at <= values.length);
    }
  }
}
function validate(scene, assets) {
  validateScene(scene); need(assets instanceof Map && assets.size <= 512);
  let numbers = 0;
  for (const [id,values] of assets) { validateAsset(id,values); numbers += values.length; need(numbers <= 4_500_000); }
  for (const node of scene.objects) if (node.kind === 'mesh') need(assets.has(node.asset));
}
export function encodeProject(scene, assets) {
  validate(scene,assets);
  const text = JSON.stringify({format:FORMAT,version:1,scene,assets:[...assets].map(([id,values]) => [id,Array.from(values)])});
  need(new TextEncoder().encode(text).byteLength <= MAX_BYTES);
  return text;
}
export function decodeProject(text) {
  need(typeof text === 'string' && text.length <= MAX_BYTES && new TextEncoder().encode(text).byteLength <= MAX_BYTES);
  let document;
  try { document = JSON.parse(text); } catch { need(false); }
  keys(document,new Set(['format','version','scene','assets']));
  need(document.format === FORMAT && document.version === 1 && Array.isArray(document.assets) && document.assets.length <= 512);
  const assets = new Map();
  for (const entry of document.assets) {
    need(Array.isArray(entry) && entry.length === 2 && !assets.has(entry[0]));
    validateAsset(entry[0],entry[1]); assets.set(entry[0],entry[1]);
  }
  validate(document.scene,assets);
  return {scene:document.scene,assets:new Map([...assets].map(([id,values]) => [id,Float32Array.from(values)]))};
}
