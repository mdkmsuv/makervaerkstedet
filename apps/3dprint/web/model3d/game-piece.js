// Meshes, assembly dimensions and print layout adapted from Skoleværkstedet's
// Brætspilsfabrik by Mathias Kusk Damgaard. See the app README for the source.
import {Color, Vector3} from 'three';
import {stlAssetId} from './stl-import.js';

const groups = {base: 'Sokkel', body: 'Krop', head: 'Hoved', hat: 'Hat'};
const groupIds = Object.keys(groups);
const namesA = ['Kegle', 'Prinsesse', 'Robo', 'Tønde', 'Trylle', 'Raket', 'Sne'];
const namesB = ['Sol', 'Bot', 'Bamse', 'Stjerne', 'Monster', 'Græskar', 'Ridder'];
const meshCache = new WeakMap();

function decode(text) {
  return Uint8Array.from(atob(text), char => char.charCodeAt(0)).buffer;
}

function meshOf(part) {
  if (!meshCache.has(part)) {
    const positions = new Float32Array(decode(part.mesh.p));
    const bytes = decode(part.mesh.i);
    const indices = part.mesh.i32 ? new Uint32Array(bytes) : Uint32Array.from(new Uint16Array(bytes));
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < positions.length; i++) {
      const axis = i % 3;
      min[axis] = Math.min(min[axis], positions[i]);
      max[axis] = Math.max(max[axis], positions[i]);
    }
    meshCache.set(part, {positions, indices, min, max});
  }
  return meshCache.get(part);
}

let catalog;
function loadCatalog() {
  if (!catalog) catalog = fetch('/assets/model3d/game-piece-data.json').then(response => {
    if (!response.ok) throw Error('Dele mangler.');
    return response.json();
  }).catch(error => { catalog = null; throw error; });
  return catalog;
}

// Bed coordinates match the print layout used by the original generator.
function printLayout(parts) {
  let x = 0, y = 0, rowHeight = 0;
  const size = [0, 0, 0];
  const items = parts.map((part, i) => {
    const mesh = meshOf(part);
    const extent = mesh.max.map((n, axis) => n - mesh.min[axis]);
    if (x && x + extent[0] > 170) {
      x = 0;
      y += rowHeight + 6;
      rowHeight = 0;
    }
    const origin = [x, y, 0];
    extent.forEach((n, axis) => { size[axis] = Math.max(size[axis], origin[axis] + n); });
    x += extent[0] + 6;
    rowHeight = Math.max(rowHeight, extent[1]);
    return {mesh, extent, origin, name: `${groups[groupIds[i]]} · ${part.label}`};
  });
  return {items, size};
}

export function gamePieceChildren(layout, assets, size = layout.size, color) {
  const scale = size.map((n, axis) => n / layout.size[axis]);
  return layout.items.map(({mesh, extent, origin, name}) => {
    // Repeated choices share the same editable project asset.
    if (!mesh.asset) {
      const positions = new Float32Array(mesh.indices.length * 3);
      mesh.indices.forEach((vertex, i) => {
        positions.set(mesh.positions.subarray(vertex * 3, vertex * 3 + 3), i * 3);
      });
      mesh.asset = {id: stlAssetId(new Uint8Array(positions.buffer)), positions};
    }
    assets.set(mesh.asset.id, mesh.asset.positions);
    return {id: crypto.randomUUID(), kind: 'mesh', mode: 'solid', name, asset: mesh.asset.id,
      size: extent.map((n, axis) => n * scale[axis]),
      position: extent.map((n, axis) => (origin[axis] + n / 2) * scale[axis] - size[axis] / 2),
      ...(color ? {color} : {})};
  });
}

// Old .mkv3d projects stored one flattened STL. Recognise its exact triangles
// before offering to split it. Looking at a project never changes its contents.
export async function findLegacyGamePieces({scene, assets}, recognised) {
  const candidates = scene.objects.filter(node => node.kind === 'mesh' && /^Spillebrik .+ [1-9]\d{0,3}$/.test(node.name));
  if (!candidates.length) return;
  let data;
  try { data = await loadCatalog(); } catch { return; }
  for (const node of candidates) {
    const triangles = assets.get(node.asset);
    if (recognised.has(triangles)) continue;
    const number = Number(node.name.split(' ').at(-1));
    let value = number - 1;
    const indexes = groupIds.map(() => 0);
    for (let i = indexes.length - 1; i >= 0; i--) {
      const count = data.categories[groupIds[i]].length;
      indexes[i] = value % count;
      value = Math.floor(value / count);
    }
    if (value || node.name !== `Spillebrik ${namesA[indexes[1]]}-${namesB[indexes[2]]} ${number}`) continue;
    const layout = printLayout(groupIds.map((id, i) => data.categories[id][indexes[i]]));
    if (triangles.length !== layout.items.reduce((n, {mesh}) => n + mesh.indices.length * 3, 0)) continue;
    let at = 0;
    const matches = layout.items.every(({mesh, origin}) => {
      const offset = origin.map((n, axis) => n - mesh.min[axis]);
      return mesh.indices.every(index => offset.every((shift, axis) =>
        triangles[at++] === Math.fround(mesh.positions[index * 3 + axis] + shift)));
    });
    if (matches) recognised.set(triangles, layout);
  }
}

// Orthographic preview of the actual meshes assembled at their connector points.
function drawPreview(canvas, parts) {
  const context = canvas.getContext('2d');
  if (!context) return;
  const forward = new Vector3(0.85, -1.5, 0.7).normalize();
  const right = new Vector3(-forward.y, forward.x, 0).normalize();
  const up = new Vector3().crossVectors(forward, right);
  const light = new Vector3(0.4, -0.6, 1).normalize();
  const offset = new Vector3(), normal = new Vector3(), edge = new Vector3();
  const point = new Vector3(), faces = [];
  let left = Infinity, rightmost = -Infinity, low = Infinity, high = -Infinity;
  for (const part of parts) {
    const {positions, indices} = meshOf(part);
    const vertices = [];
    for (let i = 0; i < positions.length; i += 3) {
      const v = new Vector3().fromArray(positions, i).add(offset);
      const px = v.dot(right), py = v.dot(up);
      left = Math.min(left, px);
      rightmost = Math.max(rightmost, px);
      low = Math.min(low, py);
      high = Math.max(high, py);
      vertices.push({v, x: px, y: py, depth: v.dot(forward)});
    }
    const color = new Color(part.color);
    for (let i = 0; i < indices.length; i += 3) {
      const a = vertices[indices[i]], b = vertices[indices[i + 1]], c = vertices[indices[i + 2]];
      normal.subVectors(b.v, a.v).cross(edge.subVectors(c.v, a.v)).normalize();
      if (normal.dot(forward) <= 0) continue;
      const shade = 0.62 + 0.38 * Math.max(0, normal.dot(light));
      faces.push({a, b, c, depth: (a.depth + b.depth + c.depth) / 3, color: color.clone().multiplyScalar(shade).getStyle()});
    }
    if (part.anchors.top) offset.add(point.fromArray(part.anchors.top));
  }
  const {width, height} = canvas;
  const scale = Math.min((width - 28) / (rightmost - left), (height - 28) / (high - low));
  const x = v => width / 2 + (v.x - (left + rightmost) / 2) * scale;
  const y = v => height / 2 - (v.y - (low + high) / 2) * scale;
  context.clearRect(0, 0, width, height);
  context.lineWidth = 0.6;
  for (const {a, b, c, color} of faces.sort((a, b) => a.depth - b.depth)) {
    context.beginPath();
    context.moveTo(x(a), y(a));
    context.lineTo(x(b), y(b));
    context.lineTo(x(c), y(c));
    context.closePath();
    context.fillStyle = context.strokeStyle = color;
    context.fill();
    context.stroke();
  }
}

function describePiece(data, indexes) {
  const parts = groupIds.map((group, i) => data.categories[group][indexes[i]]);
  if (parts.some(part => !part)) return null;
  const number = indexes.reduce((total, index, i) => total * data.categories[groupIds[i]].length + index, 0) + 1;
  const title = `${namesA[indexes[1]]}-${namesB[indexes[2]]} ${number}`;
  const skewer = Math.floor(data.spyd.sokkel + parts[1].anchors.top[2] + parts[2].anchors.top[2] + data.spyd.hat - 1);
  return {title, skewer, parts};
}

export function defaultGamePiece(data) {
  const settings = groupIds.map(() => 0);
  const {title, parts} = describePiece(data, settings);
  return {name: `Spillebrik ${title}`, layout: printLayout(parts), settings};
}

export function setupGamePiece({panel, insertGroup, updateGroup}) {
  const selectors = panel.querySelector('[data-piece-options]');
  selectors.classList.add('mechanical-fields');
  const status = panel.querySelector('[role="status"]');
  const canvas = panel.querySelector('canvas');
  const shuffle = panel.querySelector('[data-piece-shuffle]');
  let data, loading, current, activeUpdate, indexes;
  let selects = [], selectedId = null, savedKey = '', ready = false, revision = 0, insertion = 0;

  function controls() {
    const unavailable = !current || !ready || Boolean(activeUpdate);
    shuffle.setAttribute('aria-disabled', String(unavailable));
    selects.forEach((select, i) => {
      if (indexes) select.value = String(indexes[i]);
      // Keep the focused field on screen while the worker calculates the new piece.
      select.disabled = unavailable && select !== document.activeElement;
      select.setAttribute('aria-disabled', String(unavailable));
    });
  }

  function preview() {
    try {
      current = indexes && describePiece(data, indexes);
      if (current) drawPreview(canvas, current.parts);
    } catch {
      current = null;
    }
    if (!current) {
      status.textContent = 'Denne briks dele kunne ikke læses.';
      canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
      controls();
      return;
    }
    const {title, skewer} = current;
    status.textContent = `${title}. Samles med et grillspyd på ca. ${skewer} mm.`;
    controls();
  }

  async function apply(next) {
    if (!current || !ready || activeUpdate || next.join(',') === savedKey) return;
    let piece, layout;
    try {
      piece = describePiece(data, next);
      layout = printLayout(piece.parts);
    }
    catch {
      status.textContent = 'Delen kunne ikke læses. Vælg en anden del.';
      return;
    }
    const update = {id: selectedId};
    activeUpdate = update;
    controls();
    try {
      await updateGroup(update.id, `Spillebrik ${piece.title}`, layout, next);
    } catch (error) {
      if (activeUpdate === update) status.textContent = error?.message || 'Spillebrikken kunne ikke opdateres.';
    } finally {
      if (activeUpdate === update) {
        activeUpdate = null;
        controls();
      }
    }
  }

  function loadOptions() {
    if (loading) return;
    loading = loadCatalog().then(loaded => {
      data = loaded;
      selects = groupIds.map((group, groupIndex) => {
        const select = document.createElement('select');
        select.append(...data.categories[group].map((part, index) => new Option(part.label, String(index))));
        select.addEventListener('change', () => {
          if (!indexes || select.selectedIndex < 0) return;
          const next = indexes.slice();
          next[groupIndex] = select.selectedIndex;
          // Show the applied choice if a damaged asset or busy worker rejects the change.
          select.value = String(indexes[groupIndex]);
          void apply(next);
        });
        select.addEventListener('blur', controls);
        return select;
      });
      selectors.replaceChildren(...selects.map((select, i) => {
        const label = document.createElement('label');
        label.textContent = groups[groupIds[i]];
        label.append(select);
        return label;
      }));
      if (indexes) preview();
      else controls();
    }).catch(() => {
      if (indexes) status.textContent = 'Delene kunne ikke hentes. Vælg brikken igen for at prøve på ny.';
    }).finally(() => { loading = null; });
  }

  function render(node, canEdit) {
    ready = canEdit;
    const settings = node?.generator?.kind === 'game-piece' ? node.generator.settings : null;
    const valid = Array.isArray(settings) && settings.length === groupIds.length && settings.every(Number.isInteger);
    const id = node?.id ?? null, key = valid ? settings.join(',') : '';
    if (id === selectedId && key === savedKey) { controls(); return; }
    revision++;
    selectedId = id;
    savedKey = key;
    indexes = valid ? settings.slice() : null;
    current = activeUpdate = null;
    panel.hidden = !indexes;
    controls();
    if (!indexes) return;
    if (data) preview();
    else {
      status.textContent = 'Henter brikkernes dele …';
      loadOptions();
    }
  }

  async function insert(placement) {
    const started = revision, attempt = ++insertion;
    let loaded;
    try { loaded = await loadCatalog(); }
    catch {
      if (revision === started && attempt === insertion) throw Error('Spillebrikkens dele kunne ikke hentes. Prøv igen.');
      return null;
    }
    if (revision !== started || attempt !== insertion) return null;
    const {name, layout, settings} = defaultGamePiece(loaded);
    return insertGroup(name, layout, settings, placement);
  }

  function reset() {
    revision++;
    render(null, false);
  }

  shuffle.addEventListener('click', () => {
    if (shuffle.getAttribute('aria-disabled') === 'true') return;
    const next = groupIds.map(group => Math.floor(Math.random() * data.categories[group].length));
    if (next.join(',') === savedKey) next[0] = (next[0] + 1) % data.categories[groupIds[0]].length;
    void apply(next);
  });

  panel.hidden = true;
  controls();
  return {insert, render, reset};
}
