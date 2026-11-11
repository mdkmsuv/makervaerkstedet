import * as THREE from 'three';

const identity = () => new THREE.Matrix4();
export function objectMatrix(object) {
  if (object.matrix) return new THREE.Matrix4().fromArray(object.matrix);
  return new THREE.Matrix4().compose(new THREE.Vector3(...(object.position || [0, 0, 0])),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...(object.rotation || [0, 0, 0]).map(THREE.MathUtils.degToRad), 'XYZ')), new THREE.Vector3(1, 1, 1));
}
export function hasShear(matrix) {
  const axes = [0, 1, 2].map(axis => new THREE.Vector3().setFromMatrixColumn(matrix, axis).normalize());
  return Math.abs(axes[0].dot(axes[1])) > 1e-6 || Math.abs(axes[0].dot(axes[2])) > 1e-6 || Math.abs(axes[1].dot(axes[2])) > 1e-6;
}
// The matrix with absolute XYZ angles (degrees) about the centre of the local box, keeping scale and that centre.
export function rotatedMatrix(old, angles, localBounds) {
  const position = new THREE.Vector3(), quaternion = new THREE.Quaternion(), scale = new THREE.Vector3(); old.decompose(position, quaternion, scale);
  const center = new THREE.Vector3(...localBounds.min.map((v, i) => (v + localBounds.max[i]) / 2)), worldCenter = center.clone().applyMatrix4(old);
  const next = new THREE.Matrix4().compose(new THREE.Vector3(), new THREE.Quaternion().setFromEuler(new THREE.Euler(...angles.map(THREE.MathUtils.degToRad), 'XYZ')), scale);
  return next.setPosition(worldCenter.sub(center.applyMatrix4(next)));
}
export function scaledMatrix(matrix, factor, pivot) {
  return matrix.clone().multiply(new THREE.Matrix4().makeTranslation(...pivot)).multiply(new THREE.Matrix4().makeScale(...factor)).multiply(new THREE.Matrix4().makeTranslation(...pivot.map(v => -v)));
}
// Factor and pivot that give one local axis `value` mm; null when that is impossible.
export function resizeStep(matrix, axis, value, localBounds, pivot = null) {
  if (!localBounds || !(value > 0)) return null;
  const scale = new THREE.Vector3().setFromMatrixScale(matrix), natural = localBounds.max[axis] - localBounds.min[axis];
  if (natural < 1e-8 || scale.getComponent(axis) < 1e-8) return null;
  const factor = [1, 1, 1]; factor[axis] = value / (natural * scale.getComponent(axis));
  return {factor, pivot: pivot || localBounds.min.map((v, i) => i === 2 ? v : (v + localBounds.max[i]) / 2)};
}
export const PLATE_SIZE = 180;
// The 20 mm size of a regular polygon with one flat side to the front, drawn as the geometry draws it.
function regularSize(kind, params) {
  const n = params?.sides; if (!['cylinder', 'cone'].includes(kind) || !Number.isInteger(n) || n < 3 || n > 64) return null;
  const points = Array.from({length: n}, (_, k) => (270 - 180 / n + 360 * k / n) * Math.PI / 180).map(a => [Math.cos(a), Math.sin(a)]);
  const w = Math.max(...points.map(p => p[0])) - Math.min(...points.map(p => p[0])), d = Math.max(...points.map(p => p[1])) - Math.min(...points.map(p => p[1])), most = Math.max(w, d);
  return [Math.round(20 * w / most * 100) / 100, Math.round(20 * d / most * 100) / 100, 20];
}
// Solid colours a pupil can choose. The first is the default.
export const COLORS = ['#c92e35', '#e8772e', '#f0bf2c', '#3f9a4c', '#2f6fb5', '#7b51ad', '#e2689c', '#e9ecee', '#3b4248'];
// Finds the free plate position nearest the centre for a new footprint. Boxes are world-space {min:[x,y], max:[x,y]}.
export function findFreeSpot(size, occupied, {plate = PLATE_SIZE, gap = 4, step = 5} = {}) {
  const half = [size[0] / 2, size[1] / 2], limit = plate / 2 - 2;
  const free = (x, y) => occupied.every(box => x + half[0] + gap <= box.min[0] || x - half[0] - gap >= box.max[0]
    || y + half[1] + gap <= box.min[1] || y - half[1] - gap >= box.max[1]);
  const nx = Math.floor((limit - half[0]) / step), ny = Math.floor((limit - half[1]) / step), candidates = [];
  for (let i = -nx; i <= nx; i++) for (let j = -ny; j <= ny; j++) candidates.push([i * step, j * step]);
  // Prefer a spot beside the existing work. Behind comes next and in front last, because a new part
  // in front would hide the work from the usual camera angle.
  const cost = ([x, y]) => Math.hypot(x, y * (y < 0 ? 2.2 : 1.6));
  candidates.sort((a, b) => cost(a) - cost(b) || b[0] - a[0] || b[1] - a[1]);
  return candidates.find(([x, y]) => free(x, y)) || [0, 0];
}
// Geometry is local millimetres; matrices never silently include node.size.
export class SceneModel {
  constructor() {
    this.data = {format: 'makervaerkstedet-3d', version: 2, units: 'mm', plate: {w: 180, d: 180}, objects: [], roots: []};
    this.selected = []; this.undoStack = []; this.redoStack = []; this.clipboard = null; this.dirty = false;
  }
  snapshot() { return JSON.stringify(this.data); }
  change(action) {
    const before = this.snapshot(); action(); if (before === this.snapshot()) return false;
    this.undoStack.push({data: before, selected: [...this.selected]}); this.redoStack = []; this.dirty = true; return true;
  }
  restore(entry) { this.data = JSON.parse(entry.data); this.selected = entry.selected.filter(id => this.data.roots.includes(id)); this.dirty = true; }
  undo() { if (!this.undoStack.length) return; this.redoStack.push({data: this.snapshot(), selected: [...this.selected]}); this.restore(this.undoStack.pop()); }
  redo() { if (!this.redoStack.length) return; this.undoStack.push({data: this.snapshot(), selected: [...this.selected]}); this.restore(this.redoStack.pop()); }
  object(id) { return this.data.objects.find(object => object.id === id); }
  selectedObjects() { return this.selected.map(id => this.object(id)).filter(Boolean); }
  select(id, extend = false) {
    if (!extend) this.selected = []; if (!id) return;
    if (extend && this.selected.includes(id)) this.selected = this.selected.filter(value => value !== id);
    else if (!this.selected.includes(id)) this.selected.push(id);
  }
  // options: legacy [x, y] or {position: [x, y], bottom: z, params: {...} for a text, mode: 'hole' for a ready-made hole}.
  add(kind, options = {}) {
    const id = crypto.randomUUID(), {position = [0, 0], bottom = 0, params = {}, mode = 'solid', matrix = null, color = null, size = null, asset = null, name = null} = Array.isArray(options) ? {position: options} : options;
    this.change(() => {
      const node = {id, kind, rotation: [0, 0, 0], mode: mode === 'hole' ? 'hole' : 'solid'};
      if (kind === 'text') node.params = {text: 'Navn', font: 'mkv-sans', height: 12, depth: 1, ...params}; else node.size = size?.length === 3 && size.every(value => value > 0) ? [...size] : ['star', 'heart'].includes(kind) ? [20, 20, 5] : regularSize(kind, params) || [20, 20, 20];
      // A preset with flat sides, such as the hexagon in the palette.
      if (['cylinder', 'cone'].includes(kind) && Number.isInteger(params.sides) && params.sides >= 3 && params.sides <= 64) node.params = {sides: params.sides};
      if (kind === 'mesh') { node.asset = asset; node.name = String(name || 'Hentet figur').slice(0, 40); }
      // Every primitive is centred on its local origin, so the centre sits half its height above the bottom.
      node.position = [position[0], position[1], bottom + (kind === 'text' ? node.params.depth : node.size[2]) / 2];
      // A full placement (from a drop on a sloping or upright face) replaces position and rotation.
      if (matrix?.length === 16 && matrix.every(Number.isFinite)) node.matrix = [...matrix];
      if (COLORS.includes(color) && node.mode === 'solid') node.color = color;
      this.data.objects.push(node); this.data.roots.push(id);
    }); this.selected = [id]; return id;
  }
  applyTransform(delta) { this.change(() => this.selectedObjects().filter(object => !object.locked).forEach(object => { object.matrix = delta.clone().multiply(objectMatrix(object)).toArray(); })); }
  // Scales one local axis to `value` mm. The local pivot defaults to the bottom centre, so a part on the plate stays on it.
  // `lift` moves the result up afterwards; the caller uses it to keep the lowest point of a tilted part at its height.
  resize(id, axis, value, localBounds, pivot = null, lift = 0) {
    const object = this.object(id), step = object && resizeStep(objectMatrix(object), axis, value, localBounds, pivot); if (!step) return false;
    return this.scaleLocal(id, step.factor, step.pivot, lift);
  }
  // `riders` are parts standing on this one; they move up or down with its top in the same undo step.
  scaleLocal(id, factor, pivot, lift = 0, riders = [], rise = 0) {
    const object = this.object(id); if (!object || object.locked || !factor.every(value => Number.isFinite(value) && value > 0) || !Number.isFinite(lift)) return false;
    const next = scaledMatrix(objectMatrix(object), factor, pivot); next.elements[14] += lift;
    return this.change(() => {
      object.matrix = next.toArray();
      if (Number.isFinite(rise) && Math.abs(rise) > 1e-6) for (const rider of riders) { const node = this.object(rider); if (node && !node.locked) node.matrix = new THREE.Matrix4().makeTranslation(0, 0, rise).multiply(objectMatrix(node)).toArray(); }
    });
  }
  // Sets absolute angles about the part's centre. `lift` moves it up afterwards, so the caller can keep its bottom height.
  rotate(id, angles, localBounds, lift = 0) {
    const object = this.object(id); if (!object || object.locked) return false;
    const old = objectMatrix(object); if (hasShear(old)) return false;
    const next = rotatedMatrix(old, angles, localBounds); next.elements[14] += lift;
    return this.change(() => { object.matrix = next.toArray(); });
  }
  // Rounds the upright edges of a box; 0 gives sharp corners again.
  setRound(id, radius) {
    const node = this.object(id); if (node?.kind !== 'box' || node.locked || !(radius >= 0 && radius <= 500)) return false;
    return this.change(() => { if (radius > 0) node.params = {...node.params, round: radius}; else delete node.params; });
  }
  // Gives a cylinder or a cone flat sides (3 is a triangle, 6 a hexagon); 0 makes it round again.
  setSides(id, sides) {
    const node = this.object(id); if (!['cylinder', 'cone'].includes(node?.kind) || node.locked || !(sides === 0 || (Number.isInteger(sides) && sides >= 3 && sides <= 64))) return false;
    return this.change(() => { if (sides) node.params = {...node.params, sides}; else delete node.params; });
  }
  // Sets the wall of a tube in millimetres; 0 gives the standard wall again.
  setWall(id, wall) {
    const node = this.object(id); if (node?.kind !== 'tube' || node.locked || !(wall >= 0 && wall <= 500)) return false;
    return this.change(() => { if (wall > 0) node.params = {...node.params, wall}; else delete node.params; });
  }
  // Changes text parameters. A new thickness keeps the text's bottom where it was, so a text on a plate stays on it.
  setText(id, params) {
    const node = this.object(id); if (node?.kind !== 'text' || node.locked) return false;
    const shift = (params.depth ?? node.params.depth) - node.params.depth, matrix = objectMatrix(node);
    return this.change(() => { node.params = {...node.params, ...params}; if (shift) node.matrix = matrix.multiply(new THREE.Matrix4().makeTranslation(0, 0, shift / 2)).toArray(); });
  }
  // Moves several parts, each by its own world offset, in one undo step.
  moveEach(offsets) {
    return this.change(() => { for (const [id, offset] of offsets) { const object = this.object(id); if (object && !object.locked) object.matrix = new THREE.Matrix4().makeTranslation(...offset).multiply(objectMatrix(object)).toArray(); } });
  }
  // True when a part or anything inside it is locked.
  lockedTree(id, depth = 0) { const node = this.object(id); return Boolean(node && depth < 40 && (node.locked || node.children?.some(child => this.lockedTree(child, depth + 1)))); }
  // Moves every part, locked or not: used for a deliberate move of the whole model, such as putting it on the plate.
  translateAll(delta) { return this.change(() => this.data.roots.forEach(id => { const object = this.object(id); object.matrix = delta.clone().multiply(objectMatrix(object)).toArray(); })); }
  // Mirrors the selection in a world axis through `center`. A text becomes mirror writing, as on a stamp.
  mirror(axis, center) {
    const flip = [1, 1, 1]; flip[axis] = -1;
    const transform = new THREE.Matrix4().makeTranslation(...center).multiply(new THREE.Matrix4().makeScale(...flip)).multiply(new THREE.Matrix4().makeTranslation(...center.map(v => -v)));
    return this.change(() => this.selectedObjects().filter(node => !node.locked).forEach(node => { node.matrix = transform.clone().multiply(objectMatrix(node)).toArray(); }));
  }
  remove() {
    const ids = new Set(); const collect = id => { ids.add(id); this.object(id)?.children?.forEach(collect); }; this.selectedObjects().filter(node => !this.lockedTree(node.id)).forEach(node => collect(node.id));
    this.change(() => { this.data.objects = this.data.objects.filter(object => !ids.has(object.id)); this.data.roots = this.data.roots.filter(id => !ids.has(id)); }); this.selected = [];
  }
  copy() {
    const nodes = []; const collect = id => { const node = this.object(id); if (!node) return; nodes.push(structuredClone(node)); node.children?.forEach(collect); };
    this.selected.forEach(collect); if (nodes.length) this.clipboard = {roots: [...this.selected], nodes};
  }
  // offset: world move of the copy in mm. Without one the copy lands 5 mm diagonally from the original.
  paste(offset = [5, 5, 0]) { return this.pasteTransformed(new THREE.Matrix4().makeTranslation(...offset)); }
  // Pastes with a world transform applied to the copied parts, e.g. the move a pupil gave the previous copy.
  pasteTransformed(transform) {
    if (!this.clipboard) return; const map = new Map(this.clipboard.nodes.map(node => [node.id, crypto.randomUUID()]));
    this.change(() => {
      for (const raw of this.clipboard.nodes) {
        const node = structuredClone(raw); node.id = map.get(raw.id); delete node.locked; delete node.hidden; if (node.children) node.children = node.children.map(id => map.get(id));
        if (this.clipboard.roots.includes(raw.id)) node.matrix = transform.clone().multiply(objectMatrix(node)).toArray(); this.data.objects.push(node);
      } this.data.roots.push(...this.clipboard.roots.map(id => map.get(id)));
    }); this.selected = this.clipboard.roots.map(id => map.get(id));
  }
  group() {
    const chosen = this.selectedObjects().filter(node => !node.hidden);
    if (chosen.length < 2 || !chosen.some(node => node.mode !== 'hole')) return;
    const id = crypto.randomUUID(), children = chosen.map(node => node.id), locked = chosen.some(node => this.lockedTree(node.id));
    this.change(() => { this.data.roots = this.data.roots.filter(value => !children.includes(value)); this.data.roots.push(id);
      // Without a colour of its own, the combined part shows each part's colour; choosing a colour paints it all.
      this.data.objects.push({id, kind: 'group', op: 'difference', children, mode: 'solid', matrix: identity().toArray(), ...(locked ? {locked} : {})}); }); this.selected = [id];
  }
  ungroup() {
    if (this.selected.length !== 1) return; const group = this.object(this.selected[0]); if (group?.kind !== 'group' || group.mode === 'hole' || group.locked) return;
    this.change(() => {
      const parent = objectMatrix(group); for (const id of group.children) { const child = this.object(id); child.matrix = parent.clone().multiply(objectMatrix(child)).toArray(); }
      this.data.roots = this.data.roots.flatMap(id => id === group.id ? group.children : [id]); this.data.objects = this.data.objects.filter(node => node.id !== group.id);
    }); this.selected = [...group.children];
  }
  // Lock or hide the selection (value true/false). Hidden parts are not drawn and do not go into the file.
  flag(name, value) { if (!['locked', 'hidden'].includes(name)) return false; return this.change(() => this.selectedObjects().forEach(node => { if (value) node[name] = true; else delete node[name]; })); }
  mode(value) { this.change(() => this.selectedObjects().filter(node => !node.locked).forEach(node => { node.mode = value; })); }
  color(value) { if (!COLORS.includes(value)) return false; return this.change(() => this.selectedObjects().filter(node => !node.locked).forEach(node => { node.color = value; })); }
}
