import Module from 'manifold-3d';
import {parse} from 'opentype.js/dist/opentype.mjs';
import {createGeometryKernel, GeometryError, requiredTextFonts} from './geometry.js';
import {FONT_BY_KEY} from './fonts.js';

const fonts = Object.create(null), fontLoads = new Map();
const ready = Module({ locateFile: name => name.endsWith('.wasm')
  ? new URL('./model3d-manifold.wasm', import.meta.url).href
  : new URL(name, import.meta.url).href,
}).then(module => {
  module.setup();
  return createGeometryKernel(module, {fonts, cache:48});
}).catch(() => null);
function loadFont(key) {
  if (!fontLoads.has(key)) {
    const source = FONT_BY_KEY.get(key);
    // Only catalogue entries reach this function, never a path supplied by a project.
    const load = (async () => {
      const response = await fetch(new URL(`../assets/model3d/${source.file}`, import.meta.url),
        {cache:'no-cache', signal:AbortSignal.timeout(10_000)});
      if (!response.ok) throw new Error('font');
      const bytes = await response.arrayBuffer();
      const digest = await crypto.subtle.digest('SHA-256', bytes);
      const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      if (hash !== source.sha256) throw new Error('font');
      fonts[key] = parse(bytes);
    })().catch(() => {
      fontLoads.delete(key);
      throw new GeometryError('skrifttype_ikke_indlaest');
    });
    fontLoads.set(key, load);
  }
  return fontLoads.get(key);
}

// Imported files arrive once and stay here for the session; scenes only refer to them by id.
const assets = new Map();
let latest = 0, resetGeneration = 0, appliedResetGeneration = 0;
function applyReset(kernel) {
  if (kernel && appliedResetGeneration !== resetGeneration) {
    kernel.clearCache();
    appliedResetGeneration = resetGeneration;
  }
}
self.addEventListener('message', ({data}) => {
  if (data?.type === 'reset') {
    latest++; assets.clear(); resetGeneration++;
    // Parsed catalogue fonts are immutable and shared across projects. Geometry and imported files are project-specific.
    void ready.then(applyReset);
    return;
  }
  if (data?.type !== 'evaluate') return;
  for (const [id, positions] of Object.entries(data.assets || {})) {
    if (/^(stl|svg)-[a-z0-9-]{1,40}$/.test(id) && positions instanceof Float32Array) assets.set(id, positions);
  }
  void evaluate(data, ++latest);
});
async function evaluate(request, generation) {
  const kernel = await ready;
  if (generation !== latest) return;
  if (!kernel) {
    self.postMessage({type:'error', requestId:request.requestId, code:'geometri_fejlede',
      message:'3D-værkstedet kunne ikke starte. Prøv at åbne siden igen.'});
    return;
  }
  try {
    if (!Number.isSafeInteger(request.requestId) || request.requestId < 0) throw new GeometryError('ugyldig_scene');
    await Promise.all([...requiredTextFonts(request.scene)].map(loadFont));
    // A newer edit can proceed while an older edit is waiting for a font it no longer needs.
    if (generation !== latest) return;
    applyReset(kernel);
    const result = kernel.evaluateScene(request.scene, assets);
    const meshes = [...result.meshes, ...(result.merged ? [result.merged] : [])];
    const transfer = meshes.flatMap(mesh => [mesh.positions.buffer,mesh.indices.buffer,...(mesh.paints||[]).flatMap(paint=>[paint.positions.buffer,paint.indices.buffer])]);
    self.postMessage({type:'result', requestId:request.requestId, ...result}, transfer);
  } catch (error) {
    if (generation !== latest) return;
    const failure = error instanceof GeometryError ? error : new GeometryError('geometri_fejlede');
    self.postMessage({type:'error', requestId:request.requestId, code:failure.code, message:failure.message});
  }
}
