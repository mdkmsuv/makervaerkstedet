import * as THREE from 'three';
import {connectorDimensions} from './connector-profiles.js';
import {hasRigidAxes, isConnector} from './connector-transform.js';
import {DEFAULT_FONT, FONT_BY_KEY} from './fonts.js';
import {MAX_PROJECT_OBJECTS, validGenerator, validateScene} from './project.js';

export {isConnector};
const identity = () => new THREE.Matrix4();
export function objectMatrix(object) {
  if (object.matrix) return new THREE.Matrix4().fromArray(object.matrix);
  return new THREE.Matrix4().compose(new THREE.Vector3(...(object.position || [0, 0, 0])),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...(object.rotation || [0, 0, 0]).map(THREE.MathUtils.degToRad), 'XYZ')), new THREE.Vector3(1, 1, 1));
}
function rigidLinearPart(matrix) {
  // Polar decomposition separates the rotation or reflection from stretch and
  // shear. This also handles repeating a copy that was both resized and turned.
  const linear = new THREE.Matrix3().setFromMatrix4(matrix);
  for (let step = 0; step < 32; step++) {
    const inverse = linear.clone().invert().transpose();
    const norm = Math.hypot(...linear.elements), inverseNorm = Math.hypot(...inverse.elements);
    const balance = Math.sqrt(inverseNorm / norm);
    if (!(balance > 0) || !Number.isFinite(balance)) return null;
    linear.elements = linear.elements.map((value, i) => (value * balance + inverse.elements[i] / balance) / 2);
    const result = new THREE.Matrix4().setFromMatrix3(linear);
    if (hasRigidAxes(result.elements, 1e-10)) return result;
  }
  return null;
}
function affine(matrix) {
  const values = matrix.toArray();
  // General matrix inversion can round the last row of an affine transform.
  values[3] = values[7] = values[11] = 0;
  values[15] = 1;
  return values;
}
function transformPlan(root, next, parent, findNode, preserveOrientation = false) {
  const changes = new Map([[root, affine(next)]]);
  if (!root.children && !isConnector(root)) return changes;
  const before = parent.clone().multiply(objectMatrix(root)), after = parent.clone().multiply(next);
  const delta = after.clone().multiply(before.clone().invert());
  if (hasRigidAxes(delta.elements, 1e-10)) return changes;

  let orientation = preserveOrientation ? identity() : null;
  function compensate(node, oldWorld, newWorld, newParent) {
    if (isConnector(node)) {
      orientation ||= rigidLinearPart(delta);
      if (!orientation || !newParent.determinant()) return false;
      const anchor = new THREE.Vector3(...node.generator.origin).negate();
      const placed = orientation.clone().multiply(oldWorld).setPosition(0, 0, 0);
      placed.setPosition(anchor.clone().applyMatrix4(newWorld).sub(anchor.applyMatrix4(placed)));
      const local = affine(newParent.clone().invert().multiply(placed));
      if (!local.every(Number.isFinite)) return false;
      changes.set(node, local);
    }
    for (const id of node.children || []) {
      const child = findNode(id), local = objectMatrix(child);
      if (!compensate(child, oldWorld.clone().multiply(local), newWorld.clone().multiply(local), newWorld)) return false;
    }
    return true;
  }
  return compensate(root, before, after, parent) ? changes : null;
}
function applyMatrixPlan(changes) {
  if (changes) for (const [node, matrix] of changes) node.matrix = matrix;
}
function regenerated(node, generator) {
  const replacement = {...node, generator: structuredClone(generator)};
  const shift = generator.origin.map((value, axis) => value - node.generator.origin[axis]);
  // Keep the original anchor under the same local rotation and scale.
  if (shift.some(value => value !== 0)) replacement.matrix = objectMatrix(node).multiply(new THREE.Matrix4().makeTranslation(...shift)).toArray();
  return replacement;
}
function validReplacement(data, objects) {
  try { validateScene({...data, objects}); return true; }
  catch { return false; }
}
export function hasShear(matrix) {
  const axes = [0, 1, 2].map(axis => new THREE.Vector3().setFromMatrixColumn(matrix, axis).normalize());
  return Math.abs(axes[0].dot(axes[1])) > 1e-6 || Math.abs(axes[0].dot(axes[2])) > 1e-6 || Math.abs(axes[1].dot(axes[2])) > 1e-6;
}
export function uprightCylinderSection(node, matrix = node && objectMatrix(node)) {
  if (node?.kind !== 'cylinder' || node.params?.sides) return null;
  const axis = new THREE.Vector3().setFromMatrixColumn(matrix, 2);
  if (Math.hypot(axis.x, axis.y) > Math.abs(axis.z) * 1e-6 || hasShear(matrix)) return null;
  const scale = new THREE.Vector3().setFromMatrixScale(matrix);
  const length = node.size[0] * scale.x, width = node.size[1] * scale.y;
  if (!(length > 0 && width > 0 && scale.z > 0)) return null;
  const diameter = Math.abs(length - width) <= Math.max(length, width) * 1e-6 ? (length + width) / 2 : null;
  return {length, width, diameter};
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
export function findFreeSpot(size, occupied, {plate = PLATE_SIZE, gap = 4, step = 5, origin = [0, 0], requireFree = false} = {}) {
  const half = [size[0] / 2, size[1] / 2], limit = plate / 2 - 2;
  const free = (x, y) => occupied.every(box => x + half[0] + gap <= box.min[0] || x - half[0] - gap >= box.max[0]
    || y + half[1] + gap <= box.min[1] || y - half[1] - gap >= box.max[1]);
  const preferred = origin.map((value, axis) => {
    const room = Math.max(0, limit - half[axis]);
    return Math.max(-room, Math.min(room, value));
  });
  if (free(...preferred)) return preferred;
  const nx = Math.floor((limit - half[0]) / step), ny = Math.floor((limit - half[1]) / step), candidates = [];
  for (let i = -nx; i <= nx; i++) for (let j = -ny; j <= ny; j++) candidates.push([i * step, j * step]);
  // Prefer a spot beside the existing work. Behind comes next and in front last, because a new part
  // in front would hide the work from the usual camera angle.
  const cost = ([x, y]) => {
    const offsetY = y - preferred[1];
    return Math.hypot(x - preferred[0], offsetY * (offsetY < 0 ? 2.2 : 1.6));
  };
  candidates.sort((a, b) => cost(a) - cost(b) || b[0] - a[0] || b[1] - a[1]);
  return candidates.find(([x, y]) => free(x, y)) ?? (requireFree ? null : preferred);
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
    const id = crypto.randomUUID(), {position = [0, 0], bottom = 0, params = {}, mode = 'solid', matrix = null, color = null, size = null, asset = null, name = null, generator = null} = Array.isArray(options) ? {position: options} : options;
    if (kind === 'text' && params && Object.hasOwn(params, 'font') && !FONT_BY_KEY.has(params.font)) return null;
    if (generator !== null && (kind !== 'mesh' || !validGenerator(kind, generator) || this.data.objects.length >= MAX_PROJECT_OBJECTS)) return null;
    const connector = generator?.kind === 'connector';
    if (connector) {
      const nominal = connectorDimensions(generator.settings.profile, generator.settings.role);
      if (size?.length !== 3 || !nominal.every((value, axis) => size[axis] === value)) return null;
      if (matrix !== null && (!Array.isArray(matrix) || matrix.length !== 16 || !matrix.every(Number.isFinite)
          || matrix[3] !== 0 || matrix[7] !== 0 || matrix[11] !== 0 || matrix[15] !== 1
          || !hasRigidAxes(matrix))) return null;
    }
    const requestedHole = connector ? generator.settings.role === 'socket' : mode === 'hole';
    this.change(() => {
      const node = {id, kind, rotation: [0, 0, 0], mode: requestedHole ? 'hole' : 'solid'};
      if (kind === 'text') node.params = {text: 'Navn', font: DEFAULT_FONT, height: 12, depth: 1, ...params}; else node.size = size?.length === 3 && size.every(value => value > 0) ? [...size] : ['star', 'heart'].includes(kind) ? [20, 20, 5] : regularSize(kind, params) || [20, 20, 20];
      // A preset with flat sides, such as the hexagon in the palette.
      if (['cylinder', 'cone'].includes(kind) && Number.isInteger(params.sides) && params.sides >= 3 && params.sides <= 64) node.params = {sides: params.sides};
      if (kind === 'mesh') { node.asset = asset; node.name = String(name || 'Hentet figur').slice(0, 40); }
      if (generator !== null) node.generator = structuredClone(generator);
      // Every primitive is centred on its local origin, so the centre sits half its height above the bottom.
      node.position = [position[0], position[1], bottom + (kind === 'text' ? node.params.depth : node.size[2]) / 2];
      // A full placement (from a drop on a sloping or upright face) replaces position and rotation.
      if (matrix?.length === 16 && matrix.every(Number.isFinite)) node.matrix = [...matrix];
      if (COLORS.includes(color) && node.mode === 'solid') node.color = color;
      this.data.objects.push(node); this.data.roots.push(id);
    }); this.selected = [id]; return id;
  }
  replaceGeneratedMesh(id, {asset, size, name, generator}) {
    const node = this.object(id);
    if (node?.kind !== 'mesh' || !this.data.roots.includes(id) || node.locked || !validGenerator('mesh', generator)
        || node.generator?.kind !== generator.kind || isConnector(node) || !Array.isArray(size)) return false;
    const replacement = {...regenerated(node, generator), asset, size: [...size], name: name ?? node.name};
    const objects = this.data.objects.map(object => object === node ? replacement : object);
    if (!validReplacement(this.data, objects)) return false;
    return this.change(() => { Object.assign(node, replacement); });
  }
  #parentMatrix(id) {
    if (this.data.roots.includes(id)) return identity();
    const parent = this.data.objects.find(node => node.children?.includes(id));
    return parent ? this.#parentMatrix(parent.id).multiply(objectMatrix(parent)) : identity();
  }
  #transformPlan(node, matrix, preserveOrientation = false) {
    return transformPlan(node, matrix, this.#parentMatrix(node.id), id => this.object(id), preserveOrientation);
  }
  #transformBy(node, delta) {
    applyMatrixPlan(this.#transformPlan(node, delta.clone().multiply(objectMatrix(node))));
  }
  applyTransform(delta) { return this.change(() => this.selectedObjects().filter(object => !object.locked).forEach(object => this.#transformBy(object, delta))); }
  // Scales one local axis to `value` mm. The local pivot defaults to the bottom centre, so a part on the plate stays on it.
  // `lift` moves the result up afterwards; the caller uses it to keep the lowest point of a tilted part at its height.
  resize(id, axis, value, localBounds, pivot = null, lift = 0) {
    const object = this.object(id), step = object && resizeStep(objectMatrix(object), axis, value, localBounds, pivot); if (!step) return false;
    return this.scaleLocal(id, step.factor, step.pivot, lift);
  }
  setDiameter(id, value) {
    const section = uprightCylinderSection(this.object(id));
    if (!section || section.diameter === null || Math.abs(value - section.diameter) <= section.diameter * 1e-9) return false;
    return this.setCylinderSize(id, {length: value, width: value});
  }
  setCylinderSize(id, {length, width}) {
    const object = this.object(id), section = uprightCylinderSection(object);
    if (!section || object.locked || [length, width].some(size => size !== undefined && !(size >= .1 && size <= PLATE_SIZE))) return false;
    const factor = [(length ?? section.length) / section.length, (width ?? section.width) / section.width, 1];
    if (factor.every(scale => Math.abs(scale - 1) <= 1e-9)) return false;
    // The cylinder is centred on its local origin. Both radial axes change while its height and position stay fixed.
    return this.scaleLocal(id, factor, [0, 0, 0]);
  }
  // `riders` are parts standing on this one; they move up or down with its top in the same undo step.
  scaleLocal(id, factor, pivot, lift = 0, riders = [], rise = 0) {
    const object = this.object(id); if (!object || object.locked || isConnector(object) || !factor.every(value => Number.isFinite(value) && value > 0) || !Number.isFinite(lift)) return false;
    const next = scaledMatrix(objectMatrix(object), factor, pivot); next.elements[14] += lift;
    const changes = this.#transformPlan(object, next, true);
    if (!changes) return false;
    return this.change(() => {
      applyMatrixPlan(changes);
      if (Number.isFinite(rise) && Math.abs(rise) > 1e-6) for (const rider of riders) { const node = this.object(rider); if (node && !node.locked) node.matrix = new THREE.Matrix4().makeTranslation(0, 0, rise).multiply(objectMatrix(node)).toArray(); }
    });
  }
  // Sets absolute angles about the part's centre. `lift` moves it up afterwards, so the caller can keep its bottom height.
  rotate(id, angles, localBounds, lift = 0) {
    const object = this.object(id); if (!object || object.locked) return false;
    const old = objectMatrix(object); if (hasShear(old)) return false;
    const next = rotatedMatrix(old, angles, localBounds); next.elements[14] += lift;
    const changes = this.#transformPlan(object, next);
    return changes ? this.change(() => applyMatrixPlan(changes)) : false;
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
  // Sets the circumference segment count of a sphere or hemisphere. 48 is the original smooth shape.
  setFacets(id, facets) {
    const node = this.object(id);
    if (!['sphere', 'halfsphere'].includes(node?.kind) || node.locked || !Number.isInteger(facets)
        || facets < 8 || facets > 96 || facets % 4 !== 0) return false;
    return this.change(() => { if (facets === 48) delete node.params; else node.params = {facets}; });
  }
  // Sets the wall of a tube in millimetres; 0 gives the standard wall again.
  setWall(id, wall) {
    const node = this.object(id); if (node?.kind !== 'tube' || node.locked || !(wall >= 0 && wall <= 500)) return false;
    return this.change(() => { if (wall > 0) node.params = {...node.params, wall}; else delete node.params; });
  }
  // Changes text parameters. A new thickness keeps the text's bottom where it was, so a text on a plate stays on it.
  setText(id, params) {
    const node = this.object(id); if (node?.kind !== 'text' || node.locked) return false;
    if (Object.hasOwn(params, 'font') && !FONT_BY_KEY.has(params.font)) return false;
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
  translateAll(delta) { return this.change(() => this.data.roots.forEach(id => this.#transformBy(this.object(id), delta))); }
  // Mirrors the selection in a world axis through `center`. A text becomes mirror writing, as on a stamp.
  mirror(axis, center) {
    const flip = [1, 1, 1]; flip[axis] = -1;
    const transform = new THREE.Matrix4().makeTranslation(...center).multiply(new THREE.Matrix4().makeScale(...flip)).multiply(new THREE.Matrix4().makeTranslation(...center.map(v => -v)));
    return this.change(() => this.selectedObjects().filter(node => !node.locked).forEach(node => this.#transformBy(node, transform)));
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
    const nodes = this.clipboard.nodes.map(raw => {
      const node = structuredClone(raw); node.id = map.get(raw.id); delete node.locked; delete node.hidden;
      if (node.children) node.children = node.children.map(id => map.get(id));
      return node;
    });
    const roots = this.clipboard.roots.map(id => map.get(id)), byId = new Map(nodes.map(node => [node.id, node]));
    for (const id of roots) {
      const node = byId.get(id), next = transform.clone().multiply(objectMatrix(node));
      const changes = transformPlan(node, next, identity(), child => byId.get(child));
      if (!changes) return;
      applyMatrixPlan(changes);
    }
    this.change(() => {
      this.data.objects.push(...nodes);
      this.data.roots.push(...roots);
    }); this.selected = roots;
  }
  group() {
    const chosen = this.selectedObjects().filter(node => !node.hidden);
    if (chosen.length < 2 || !chosen.some(node => node.mode !== 'hole')) return;
    const id = crypto.randomUUID(), children = chosen.map(node => node.id), locked = chosen.some(node => this.lockedTree(node.id));
    this.change(() => { this.data.roots = this.data.roots.filter(value => !children.includes(value)); this.data.roots.push(id);
      // Without a colour of its own, the combined part shows each part's colour; choosing a colour paints it all.
      this.data.objects.push({id, kind: 'group', op: 'difference', children, mode: 'solid', matrix: identity().toArray(), ...(locked ? {locked} : {})}); }); this.selected = [id];
  }
  addMeshGroup(name, children, position, generator = null, op = 'union') {
    if (!['union', 'difference'].includes(op) || !Array.isArray(children) || (generator != null && (!children.length || !validGenerator('group', generator)))
        || this.data.objects.length + children.length + 1 > MAX_PROJECT_OBJECTS) return null;
    const id = crypto.randomUUID();
    const group = {id, kind: 'group', op, mode: 'solid', name, position,
      children: children.map(node => node.id), ...(generator != null ? {generator: structuredClone(generator)} : {}),
    };
    this.change(() => {
      this.data.objects.push(...children, group);
      this.data.roots.push(id);
    });
    this.selected = [id];
    return id;
  }
  addImportedMeshes(nodes) {
    if (!nodes.length || this.data.objects.length + nodes.length > MAX_PROJECT_OBJECTS) return false;
    const ids = nodes.map(node => node.id);
    this.change(() => {
      this.data.objects.push(...nodes);
      this.data.roots.push(...ids);
    });
    this.selected = ids;
    return true;
  }
  replaceGeneratedGroup(id, {children, name, generator, op}) {
    const node = this.object(id);
    if (node?.kind !== 'group' || !this.data.roots.includes(id) || this.lockedTree(id) || !validGenerator('group', generator)
        || node.generator?.kind !== generator.kind || !Array.isArray(children) || !children.length) return false;
    const removed = new Set(node.children);
    for (const child of removed) for (const descendant of this.object(child)?.children || []) removed.add(descendant);
    const retained = this.data.objects.filter(object => !removed.has(object.id));
    if (retained.length + children.length > MAX_PROJECT_OBJECTS) return false;
    const replacement = {...regenerated(node, generator), name: name ?? node.name, op: op ?? node.op, children: Array.from(children, child => child?.id)};
    const newChildren = structuredClone(children);
    if (newChildren.some(isConnector)) {
      const byId = new Map(newChildren.map(child => [child.id, child]));
      const seed = {...replacement, matrix: identity().toArray()};
      const rebased = transformPlan(seed, objectMatrix(replacement), identity(), child => byId.get(child));
      if (!rebased) return false;
      rebased.delete(seed);
      applyMatrixPlan(rebased);
    }
    const objects = retained.map(object => object === node ? replacement : object).concat(newChildren);
    if (!validReplacement(this.data, objects)) return false;
    return this.change(() => {
      Object.assign(node, replacement);
      this.data.objects = retained.concat(newChildren);
    });
  }
  ungroup(restoredParts = null) {
    if (this.selected.length !== 1) return;
    const group = this.object(this.selected[0]);
    if (!group || group.mode === 'hole' || group.locked || !(group.kind === 'group' || (group.kind === 'mesh' && restoredParts))) return;
    const children = restoredParts ? restoredParts.map(node => node.id) : group.children;
    this.change(() => {
      if (restoredParts) this.data.objects.push(...restoredParts);
      const parent = objectMatrix(group); for (const id of children) { const child = this.object(id); child.matrix = parent.clone().multiply(objectMatrix(child)).toArray(); }
      this.data.roots = this.data.roots.flatMap(id => id === group.id ? children : [id]); this.data.objects = this.data.objects.filter(node => node.id !== group.id);
    }); this.selected = [...children];
  }
  // Lock or hide the selection (value true/false). Hidden parts are not drawn and do not go into the file.
  flag(name, value) { if (!['locked', 'hidden'].includes(name)) return false; return this.change(() => this.selectedObjects().forEach(node => { if (value) node[name] = true; else delete node[name]; })); }
  mode(value) { this.change(() => this.selectedObjects().filter(node => !node.locked && !isConnector(node)).forEach(node => { node.mode = value; })); }
  color(value) { if (!COLORS.includes(value)) return false; return this.change(() => this.selectedObjects().filter(node => !node.locked).forEach(node => { node.color = value; })); }
}
