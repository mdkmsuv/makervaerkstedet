import Module from 'manifold-3d';
import {parse} from 'opentype.js/dist/opentype.mjs';
import { createGeometryKernel, GeometryError } from './geometry.js';

const ready = Module({ locateFile: name => name.endsWith('.wasm')
  ? new URL('./model3d-manifold.wasm', import.meta.url).href
  : new URL(name, import.meta.url).href,
}).then(async module => {
  module.setup();
  const response=await fetch(new URL('../assets/model3d/Metrophobic-Regular.ttf',import.meta.url));
  if(!response.ok)throw new Error('font');
  const bytes=await response.arrayBuffer();
  if(bytes.byteLength!==79772)throw new Error('font');
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
  if(hash!=='34e4ac3257307c28e6dd14f5d77bb296211f0ddd217607228a5375416ed8ac58')throw new Error('font');
  return createGeometryKernel(module,{fonts:{'mkv-sans':parse(bytes)},cache:48});
}).catch(() => null);
// Keep a single latest pending edit. The UI also rejects obsolete request IDs.
let pending = null, running = false;
// Imported files arrive once and stay here for the session; scenes only refer to them by id.
const assets = new Map();
self.addEventListener('message', event => {
  if (event.data?.type !== 'evaluate') return;
  for (const [id, positions] of Object.entries(event.data.assets || {})) if (/^(stl|svg)-[a-z0-9-]{1,40}$/.test(id) && positions instanceof Float32Array) assets.set(id, positions);
  pending = event.data;
  if (!running) void drain();
});
async function drain() {
  running = true;
  try {
    const kernel = await ready;
    if (!kernel) throw new GeometryError('geometri_fejlede');
    while (pending) {
      const request = pending; pending = null;
      try {
        if (!Number.isSafeInteger(request.requestId) || request.requestId < 0) throw new GeometryError('ugyldig_scene');
        const result = kernel.evaluateScene(request.scene, assets);
        const meshes = [...result.meshes, ...(result.merged ? [result.merged] : [])];
        const transfer = meshes.flatMap(mesh => [mesh.positions.buffer,mesh.indices.buffer,...(mesh.paints||[]).flatMap(paint=>[paint.positions.buffer,paint.indices.buffer])]);
        self.postMessage({type:'result',requestId:request.requestId,...result}, transfer);
      } catch (error) {
        const failure = error instanceof GeometryError ? error : new GeometryError('geometri_fejlede');
        self.postMessage({type:'error',requestId:request.requestId,code:failure.code,message:failure.message});
      }
      await new Promise(resolve => setTimeout(resolve, 0));
    }
  } catch {
    if (pending) self.postMessage({type:'error',requestId:pending.requestId,code:'geometri_fejlede',message:'3D-værkstedet kunne ikke starte. Prøv at åbne siden igen.'});
    pending = null;
  } finally {
    running = false;
  }
}
