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

export function setupGamePiece({opener, dialog, insertGroup}) {
  const selectors = dialog.querySelector('[data-piece-options]');
  const status = dialog.querySelector('[role="status"]');
  const canvas = dialog.querySelector('canvas');
  const insert = dialog.querySelector('[data-piece-insert]');
  const shuffle = dialog.querySelector('[data-piece-shuffle]');
  const indexes = groupIds.map(() => 0);
  let data, sections, current, opening = 0;

  function update() {
    const parts = groupIds.map((group, i) => data.categories[group][indexes[i]]);
    sections.forEach(({summary, buttons}, i) => {
      summary.textContent = `${groups[groupIds[i]]} · ${parts[i].label}`;
      buttons.forEach((button, index) => button.setAttribute('aria-pressed', String(index === indexes[i])));
    });
    const number = indexes.reduce((total, index, i) => total * data.categories[groupIds[i]].length + index, 0) + 1;
    const title = `${namesA[indexes[1]]}-${namesB[indexes[2]]} ${number}`;
    const skewer = Math.floor(data.spyd.sokkel + parts[1].anchors.top[2] + parts[2].anchors.top[2] + data.spyd.hat - 1);
    current = {title, parts};
    drawPreview(canvas, parts);
    status.textContent = `${title}. Samles med et grillspyd på ca. ${skewer} mm.`;
  }

  opener.addEventListener('click', async () => {
    const attempt = ++opening;
    dialog.showModal();
    if (data) {
      sections.forEach(({details}) => { details.open = false; });
      sections[0].summary.focus();
      return;
    }
    status.textContent = 'Henter brikkernes dele …';
    try {
      const loaded = await loadCatalog();
      if (!dialog.open || attempt !== opening) return;
      sections = groupIds.map((group, groupIndex) => {
        // Keep choices inside Chromium's page in the kiosk session.
        const details = document.createElement('details');
        details.name = 'game-piece-part';
        const summary = document.createElement('summary');
        const options = document.createElement('div');
        options.className = 'game-piece-choices';
        const buttons = loaded.categories[group].map((part, index) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = part.label;
          button.addEventListener('click', () => {
            indexes[groupIndex] = index;
            update();
            details.open = false;
            summary.focus();
          });
          return button;
        });
        options.append(...buttons);
        details.append(summary, options);
        return {details, summary, buttons};
      });
      selectors.replaceChildren(...sections.map(({details}) => details));
      data = loaded;
      update();
      shuffle.disabled = insert.disabled = false;
      if (dialog.contains(document.activeElement)) sections[0].summary.focus();
    } catch {
      if (dialog.open && attempt === opening) status.textContent = 'Delene kunne ikke hentes. Luk vinduet, og prøv igen.';
    }
  });

  dialog.addEventListener('cancel', event => {
    const section = sections?.find(({details}) => details.open);
    if (!section) return;
    event.preventDefault();
    section.details.open = false;
    section.summary.focus();
  });

  shuffle.addEventListener('click', () => {
    groupIds.forEach((group, i) => { indexes[i] = Math.floor(Math.random() * data.categories[group].length); });
    update();
  });

  insert.addEventListener('click', () => {
    if (!current || !dialog.open) return;
    try {
      insertGroup(`Spillebrik ${current.title}`, printLayout(current.parts));
      dialog.close();
    } catch (error) {
      status.textContent = error.message || 'Brikken kunne ikke sættes på pladen.';
    }
  });
}
