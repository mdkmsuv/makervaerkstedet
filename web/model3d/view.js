import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {LineSegments2} from 'three/addons/lines/LineSegments2.js';
import {LineSegmentsGeometry} from 'three/addons/lines/LineSegmentsGeometry.js';
import {LineMaterial} from 'three/addons/lines/LineMaterial.js';
import {objectMatrix, hasShear, PLATE_SIZE, COLORS} from './scene.js';
import {resizeHandles, handlePivot, resizeFactors, snapAngle, rotationAbout, lowestZ, highestZ, ROTATION_AXES, planeAngle, unwrap} from './handles.js';

const teal = 0x38798c;
const round = value => Math.round(value * 100) / 100;
const decimal = value => String(round(value)).replace('.', ',');
// Directions point from the target towards the camera. Z is up.
const VIEWS = {home: [0.42, -1, 0.9], top: [0, -0.12, 1], front: [0, -1, 0.24], right: [1, 0, 0.24], back: [0, 1, 0.24], left: [-1, 0, 0.24]};
// The direction cube: each face is a button. right/up span the face as the pupil reads it; normal points out of the cube.
const CUBE_FACES = [
  {view: 'top', label: 'Oven', normal: [0, 0, 1], right: [1, 0, 0], up: [0, 1, 0]},
  {view: 'front', label: 'Foran', normal: [0, -1, 0], right: [1, 0, 0], up: [0, 0, 1]},
  {view: 'right', label: 'Højre', normal: [1, 0, 0], right: [0, 1, 0], up: [0, 0, 1]},
  {view: 'back', label: 'Bag', normal: [0, 1, 0], right: [-1, 0, 0], up: [0, 0, 1]},
  {view: 'left', label: 'Venstre', normal: [-1, 0, 0], right: [0, -1, 0], up: [0, 0, 1]},
  {view: null, label: 'Bund', normal: [0, 0, -1], right: [1, 0, 0], up: [0, -1, 0]},
];
const CUBE_SIZE = 68;
const AXES = [{letter: 'L', name: 'Længde'}, {letter: 'B', name: 'Bredde'}, {letter: 'H', name: 'Højde'}];
// Corner i has x from bit 0, y from bit 1 and z from bit 2.
const BOX_EDGES = [[0, 1], [1, 3], [3, 2], [2, 0], [4, 5], [5, 7], [7, 6], [6, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
const boxCorners = (min, max) => Array.from({length: 8}, (_, i) => new THREE.Vector3(i & 1 ? max[0] : min[0], i & 2 ? max[1] : min[1], i & 4 ? max[2] : min[2]));
const translation = point => new THREE.Matrix4().makeTranslation(point[0], point[1], point[2]);
function gridGeometry(step, skip) {
  const half = PLATE_SIZE / 2, points = [], count = Math.round(PLATE_SIZE / step);
  for (let i = 0; i <= count; i++) {
    const offset = i * step; if (skip && offset % skip === 0) continue;
    const value = -half + offset; points.push(value, -half, 0, value, half, 0, -half, value, 0, half, value, 0);
  }
  const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3)); return geometry;
}
const SVG = 'http://www.w3.org/2000/svg';
function svg(name, attributes = {}) { const element = document.createElementNS(SVG, name); for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value)); return element; }
const point2 = p => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
function arrowTip(from, to, size = 9) {
  const dx = to.x - from.x, dy = to.y - from.y, length = Math.hypot(dx, dy) || 1, ux = dx / length, uy = dy / length, bx = to.x - ux * size, by = to.y - uy * size;
  return `M${point2({x: to.x + ux * 3, y: to.y + uy * 3})} L${point2({x: bx - uy * size * .6, y: by + ux * size * .6})} L${point2({x: bx + uy * size * .6, y: by - ux * size * .6})} Z`;
}
const ARC_SPAN = 38; // Degrees on each side of the point that faces the camera.
function handleTitle(spec) {
  if (spec.type === 'lift') return 'Træk op eller ned for at løfte eller sænke figuren.';
  if (spec.type === 'rotate') return ['Træk for at vippe figuren forover eller bagover (om X).', 'Træk for at vippe figuren til siden (om Y).', 'Træk for at dreje figuren rundt på pladen (om Z).'][spec.axis] + ' Trin på 15°. Shift giver 45°, Alt drejer frit.';
  if (spec.corner) return 'Træk for at ændre længde og bredde. Shift bevarer formen.';
  return `Træk for at ændre ${['længden', 'bredden', 'højden'][spec.scale.findIndex(Boolean)]}. Shift bevarer formen.`;
}

export class View {
  constructor(host, onSelect, onTransform, onEdit, onDragState) {
    Object.assign(this, {host, onSelect, onTransform, onEdit, onDragState, selectedIds: [], metadata: new Map(), nodes: new Map(), snap: 1, enabled: true, labels: [], labelIds: [], handleItems: []});
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color('#e8edef');
    this.camera = new THREE.PerspectiveCamera(38, 1, .5, 4000); this.camera.up.set(0, 0, 1); this.camera.position.set(100, -150, 145);
    this.renderer = new THREE.WebGLRenderer({antialias: true}); this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.domElement.setAttribute('aria-label', '3D-model. Træk i en figur for at flytte den. Træk i den tomme flade for at dreje visningen.'); host.append(this.renderer.domElement);
    // Handles are SVG and measurements HTML, so both stay sharp, keep their size on screen and can be styled.
    this.svg = svg('svg', {class: 'handle-layer', 'aria-hidden': 'true'}); this.protractor = svg('path', {class: 'protractor'}); this.svg.append(this.protractor); host.append(this.svg);
    this.overlay = document.createElement('div'); this.overlay.className = 'measure-layer'; host.append(this.overlay);
    // A quiet label on the plate's front edge, so "Forfra" and "For" on the cube have something to point at.
    this.frontLabel = document.createElement('span'); this.frontLabel.className = 'plate-front'; this.frontLabel.textContent = 'Forside'; this.frontLabel.setAttribute('aria-hidden', 'true'); host.append(this.frontLabel);
    this.orbit = new OrbitControls(this.camera, this.renderer.domElement); this.orbit.target.set(0, 0, 0); this.orbit.enableDamping = true;
    this.orbit.mouseButtons = {LEFT: null, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.ROTATE}; this.orbit.rotateSpeed = .6; this.orbit.zoomSpeed = .6; this.orbit.zoomToCursor = true; this.orbit.minDistance = 12; this.orbit.maxDistance = 900; this.orbit.maxPolarAngle = Math.PI / 2 - .2; this.orbit.update();
    this.orbit.addEventListener('end', () => this.refreshLabels()); this.orbit.addEventListener('change', () => { this.dirty = true; });
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xa6b6c0, 2.3));
    const light = new THREE.DirectionalLight(0xffffff, 2.5); light.position.set(-70, -120, 220); this.scene.add(light);
    this.buildPlate(); this.buildCube();
    this.roots = new THREE.Group(); this.selection = new THREE.Group(); this.preview = new THREE.Group(); this.preview.matrixAutoUpdate = false;
    this.scene.add(this.roots, this.selection, this.preview); this.ray = new THREE.Raycaster();
    const canvas = this.renderer.domElement;
    canvas.addEventListener('contextmenu', event => event.preventDefault());
    canvas.addEventListener('wheel', () => this.finishGlide(), {capture: true, passive: true});
    // The wheel also zooms when the pointer rests on a measure or a handle above the picture.
    host.addEventListener('wheel', event => { if (event.target === canvas || event.target.closest?.('input')) return; event.preventDefault(); canvas.dispatchEvent(new WheelEvent('wheel', event)); }, {passive: false});
    // Capture on the host runs before the camera control, so a press on a part can switch the camera off first.
    host.addEventListener('pointerdown', event => this.pointerDown(event), {capture: true}); canvas.addEventListener('pointermove', event => this.pointerMove(event));
    canvas.addEventListener('pointerup', event => this.pointerUp(event)); canvas.addEventListener('pointercancel', () => { if (this.band) { this.band.element?.remove(); this.band = null; } this.cancelDrag(); });
    canvas.addEventListener('lostpointercapture', () => { if (this.drag) this.cancelDrag(); });
    // A press on a part that started no drag (locked, Shift, busy) can end over the panel; the camera must come back anyway.
    window.addEventListener('pointerup', event => { if (this.orbitBlocked === event.pointerId && !this.drag) { this.orbitBlocked = null; if (!this.pendingPreview) this.orbit.enabled = true; } });
    new ResizeObserver(() => this.resize()).observe(host); this.resize(); this.animate();
  }
  updateHover(x, y) {
    if (this.panMode) { this.renderer.domElement.style.cursor = 'grab'; return; }
    const busy = this.drag?.active || this.pendingPreview || !this.enabled, hit = busy ? null : this.rayAt(x, y).intersectObjects(this.roots.children, false)[0];
    const id = hit?.object.userData.id ?? null;
    this.renderer.domElement.style.cursor = this.drag?.active ? 'grabbing' : id ? (this.nodes.get(id)?.locked ? 'not-allowed' : 'grab') : 'default';
    if (id === this.hovered) return;
    this.hovered = id; this.paintHover(); this.dirty = true;
  }
  paintHover() {
    for (const object of this.roots.children) {
      const on = object.userData.id === this.hovered && !this.selectedIds.includes(object.userData.id), material = object.material;
      if (material.emissive) material.emissive.setHex(on ? 0x2a2a2a : 0x000000);
      else material.opacity = on ? .55 : .38;
    }
  }
  // Arrow keys move in the directions the pupil sees: right on screen and away from the camera, each snapped to the nearest plate axis.
  screenDirections() {
    const snap = vector => Math.abs(vector.x) >= Math.abs(vector.y) ? [Math.sign(vector.x) || 1, 0] : [0, Math.sign(vector.y) || 1];
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0), away = this.orbit.target.clone().sub(this.camera.position);
    const awayFlat = Math.hypot(away.x, away.y) < 1e-6 ? new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1) : away;
    return {right: snap(right), away: snap(awayFlat)};
  }
  // A small cube in the corner turns with the camera. Clicking a face shows the whole plate from that side.
  buildCube() {
    this.cube = document.createElement('div'); this.cube.className = 'view-cube'; this.cube.setAttribute('role', 'group'); this.cube.setAttribute('aria-label', 'Vælg hvilken side du ser modellen fra');
    const body = document.createElement('div'); body.className = 'cube-body'; this.cube.append(body);
    this.cubeFaces = CUBE_FACES.map(face => {
      const element = document.createElement(face.view ? 'button' : 'div'); element.className = 'cube-face'; element.textContent = face.label;
      if (face.view) { element.type = 'button'; element.title = `Se pladen fra ${{top: 'oven', front: 'forsiden', right: 'højre side', back: 'bagsiden', left: 'venstre side'}[face.view]}`; element.addEventListener('click', () => this.cameraTo(face.view)); }
      body.append(element);
      // Element axes: x to the right, y down the face and z out of it. One CSS pixel is one cube unit.
      const half = CUBE_SIZE / 2, matrix = new THREE.Matrix4().makeBasis(new THREE.Vector3(...face.right), new THREE.Vector3(...face.up).negate(), new THREE.Vector3(...face.normal)).setPosition(new THREE.Vector3(...face.normal).multiplyScalar(half));
      return {element, matrix};
    });
    this.host.append(this.cube);
  }
  placeFrontLabel() {
    const p = this.screenMapper()(new THREE.Vector3(0, -PLATE_SIZE / 2 - 7, 0)), facing = this.camera.position.y < 0;
    this.frontLabel.hidden = p.behind || !facing; if (!this.frontLabel.hidden) this.frontLabel.style.transform = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px) translate(-50%, -50%)`;
  }
  updateCube() {
    // World to camera rotation, then flip y because CSS y points down.
    const view = new THREE.Matrix4().makeRotationFromQuaternion(this.camera.quaternion.clone().invert()).premultiply(new THREE.Matrix4().makeScale(1, -1, 1));
    for (const face of this.cubeFaces) face.element.style.transform = `matrix3d(${view.clone().multiply(face.matrix).elements.map(value => value.toFixed(5)).join(',')})`;
  }
  buildPlate() {
    const half = PLATE_SIZE / 2, band = 2.5, plate = new THREE.Group();
    const surface = new THREE.Mesh(new THREE.PlaneGeometry(PLATE_SIZE, PLATE_SIZE), new THREE.MeshStandardMaterial({color: 0xfdfefe, roughness: 1, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 4}));
    surface.position.z = -.05;
    const square = size => [new THREE.Vector2(-size, -size), new THREE.Vector2(size, -size), new THREE.Vector2(size, size), new THREE.Vector2(-size, size)];
    const outline = new THREE.Shape(square(half + band)); outline.holes.push(new THREE.Path(square(half)));
    const frame = new THREE.Mesh(new THREE.ShapeGeometry(outline), new THREE.MeshBasicMaterial({color: 0xa3b5be, side: THREE.DoubleSide})); frame.position.z = -.04;
    const grid = (step, skip, color, opacity) => {
      const lines = new THREE.LineSegments(gridGeometry(step, skip), new THREE.LineBasicMaterial({color, transparent: true, opacity, depthWrite: false}));
      lines.position.z = .02; lines.renderOrder = -2; lines.userData.opacity = opacity; return lines;
    };
    // 1, 5 and 10 mm. The finer grids fade out before they turn into moiré.
    this.grids = [grid(1, 5, 0xc6d1d7, .6), grid(5, 10, 0xb1c0c7, .8), grid(10, 0, 0x96a9b2, .9)];
    const axis = (from, to) => { const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([from, to]), new THREE.LineBasicMaterial({color: 0x7c929c, transparent: true, depthWrite: false})); line.position.z = .03; line.renderOrder = -1; return line; };
    plate.add(surface, frame, ...this.grids, axis(new THREE.Vector3(-half, 0, 0), new THREE.Vector3(half, 0, 0)), axis(new THREE.Vector3(0, -half, 0), new THREE.Vector3(0, half, 0)));
    this.scene.add(plate);
  }
  // Draws only when something changed: the camera moved, a result arrived, the selection or a drag changed or the size changed.
  // An idle workshop then costs almost nothing on an old pupil computer.
  animate() {
    if (this.glide) this.stepGlide();
    this.orbit.update();
    if (this.hoverAt) { this.updateHover(...this.hoverAt); this.hoverAt = null; }
    if (this.dirty) { this.dirty = false; this.updateGrid(); this.renderer.render(this.scene, this.camera); this.placeHandles(); this.placeLabels(); this.updateCube(); this.placeFrontLabel(); }
    requestAnimationFrame(() => this.animate());
  }
  resize() {
    this.dirty = true;
    const w = this.host.clientWidth, h = this.host.clientHeight; if (!w || !h) return;
    // The property panel covers the right edge. A view offset centres the picture in the free area instead.
    this.inset = Math.min(w * .4, this.insetRight ? this.insetRight(w) : 0); this.insetLeftPx = Math.min(w * .3, this.insetLeft || 0);
    this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.setViewOffset(w, h, (this.inset - this.insetLeftPx) / 2, 0, w, h); this.camera.updateProjectionMatrix();
    for (const group of [this.selection, this.looseGroup, this.guideGroup]) group?.traverse(child => child.material?.resolution?.set(w, h));
    if (!this.homed) { this.homed = true; this.cameraTo('home'); }
  }
  updateGrid() {
    const distance = this.camera.position.distanceTo(this.orbit.target), pixelsPerMm = Math.max(1, this.host.clientHeight) / 2 / (Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * distance);
    const ramp = (low, high) => THREE.MathUtils.clamp((pixelsPerMm - low) / (high - low), 0, 1);
    [[this.grids[0], ramp(3, 7)], [this.grids[1], ramp(.7, 1.6)]].forEach(([lines, amount]) => { lines.material.opacity = lines.userData.opacity * amount; lines.visible = amount > .01; });
  }
  clear(group) { for (const object of [...group.children]) { group.remove(object); object.traverse(child => { if (!child.geometry?.userData.shared) child.geometry?.dispose(); if (Array.isArray(child.material)) child.material.forEach(material => material.dispose()); else child.material?.dispose(); }); } }
  geometry(mesh) { const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.BufferAttribute(mesh.positions, 3)); geometry.setIndex(new THREE.BufferAttribute(mesh.indices, 1)); geometry.computeVertexNormals(); return geometry; }
  rayAt(x, y) {
    const rect = this.renderer.domElement.getBoundingClientRect(); this.ray.setFromCamera(new THREE.Vector2((x - rect.left) / rect.width * 2 - 1, -(y - rect.top) / rect.height * 2 + 1), this.camera); return this.ray;
  }
  planePoint(x, y) { return this.rayAt(x, y).ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), new THREE.Vector3()); }
  // The first solid surface under the pointer, used to stack a dropped shape on top of another.
  surfacePoint(x, y) { const hit = this.rayAt(x, y).intersectObjects(this.roots.children, false).find(item => item.object.userData.mode !== 'hole'); return hit && hit.face?.normal.z > .3 ? hit.point : null; }
  // The solid part under the pointer and the point hit, if any.
  partAt(x, y) {
    const hit = this.rayAt(x, y).intersectObjects(this.roots.children, false).find(item => item.object.userData.mode !== 'hole');
    return hit ? {id: hit.object.userData.id, point: hit.point, normal: hit.face?.normal.clone().normalize() ?? new THREE.Vector3(0, 0, 1)} : null;
  }
  // Height of the top surface of one part straight above (x, y), so a text sits on a cone or a dome too.
  topAt(id, x, y) {
    const objects = this.roots.children.filter(item => item.userData.id === id); if (!objects.length) return null;
    this.ray.set(new THREE.Vector3(x, y, 5000), new THREE.Vector3(0, 0, -1)); return this.ray.intersectObjects(objects, false)[0]?.point.z ?? null;
  }
  project(point) { const rect = this.renderer.domElement.getBoundingClientRect(), p = point.clone().project(this.camera); return new THREE.Vector2((p.x + 1) * rect.width / 2, (1 - p.y) * rect.height / 2); }
  screenMapper() { const w = this.host.clientWidth, h = this.host.clientHeight; return point => { const p = point.clone().project(this.camera); return {x: (p.x + 1) / 2 * w, y: (1 - p.y) / 2 * h, behind: p.z > 1}; }; }
  render(result, selected, nodes) {
    this.dirty = true;
    this.cancelDrag(true); this.clear(this.roots); this.nodes = new Map(nodes.map(node => [node.id, node])); this.metadata = new Map((result?.meshes || []).map(mesh => [mesh.id, mesh]));
    for (const mesh of result?.meshes || []) {
      const material = mesh.mode === 'hole' ? new THREE.MeshBasicMaterial({color: teal, transparent: true, opacity: .38, depthWrite: false, side: THREE.DoubleSide})
        : new THREE.MeshStandardMaterial({color: this.colorOf(mesh.id), roughness: .7, metalness: .015, flatShading: true});
      if (mesh.mode === 'hole') material.onBeforeCompile = shader => { shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', '#include <color_fragment>\n float stripe = step(0.76, fract((gl_FragCoord.x + gl_FragCoord.y) / 9.0)); diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.12,0.32,0.40),stripe); diffuseColor.a = mix(diffuseColor.a,0.8,stripe);'); };
      // A combined part of several colours arrives as coloured pieces with the same id; it behaves as one part.
      for (const piece of mesh.paints || [mesh]) {
        const color = mesh.paints ? piece.color : this.colorOf(mesh.id), pieceMaterial = mesh.paints ? new THREE.MeshStandardMaterial({color, roughness: .7, metalness: .015, flatShading: true}) : material;
        const object = new THREE.Mesh(this.geometry(piece), pieceMaterial); object.userData = {id: mesh.id, mode: mesh.mode}; object.renderOrder = mesh.mode === 'hole' ? 2 : 0; this.roots.add(object);
        if (mesh.mode !== 'hole') object.add(new THREE.LineSegments(this.edgesFor(piece, object.geometry), new THREE.LineBasicMaterial({color: new THREE.Color(color).multiplyScalar(.55), transparent: true, opacity: .65})));
      }
      if (mesh.paints) material.dispose();
    }
    this.select(selected); this.paintHover();
  }
  updateBand(event) {
    const band = this.band; if (!band.element && Math.hypot(event.clientX - band.x, event.clientY - band.y) < 5) return;
    if (!band.element) { band.element = document.createElement('div'); band.element.className = 'select-band'; this.host.append(band.element); }
    const host = this.host.getBoundingClientRect(), left = Math.min(band.x, event.clientX), top = Math.min(band.y, event.clientY);
    Object.assign(band.element.style, {left: `${left - host.left}px`, top: `${top - host.top}px`, width: `${Math.abs(event.clientX - band.x)}px`, height: `${Math.abs(event.clientY - band.y)}px`});
  }
  // The parts whose outline on screen touches a rectangle given in page coordinates.
  idsIn(rect) {
    const host = this.host.getBoundingClientRect(), toScreen = this.screenMapper(), found = [];
    const inside = (x, y) => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom, point = new THREE.Vector3();
    for (const object of this.roots.children) {
      const id = object.userData.id, positions = object.geometry.attributes.position; if (found.includes(id) || !positions) continue;
      for (let i = 0, step = Math.max(1, Math.floor(positions.count / 400)); i < positions.count; i += step) {
        const p = toScreen(point.fromBufferAttribute(positions, i).applyMatrix4(object.matrixWorld)); if (!p.behind && inside(p.x + host.left, p.y + host.top)) { found.push(id); break; }
      }
    }
    // A frame drawn inside a large face has no corner in it: rays through the frame find that part.
    for (let i = 0; i <= 8; i++) for (let j = 0; j <= 8; j++) {
      const x = rect.left + rect.width * i / 8, y = rect.top + rect.height * j / 8;
      for (const hit of this.rayAt(x, y).intersectObjects(this.roots.children, false)) if (!found.includes(hit.object.userData.id)) found.push(hit.object.userData.id);
    }
    return found;
  }
  // The hand: while it is on, the left button moves the view.
  setPanMode(on) {
    this.panMode = on; this.orbit.mouseButtons.LEFT = on ? THREE.MOUSE.PAN : null;
    // The hand does not choose parts, so no part stays lit up under the pointer.
    if (on && this.hovered) { this.hovered = null; this.paintHover(); this.dirty = true; }
    this.renderer.domElement.style.cursor = on ? 'grab' : 'default';
  }
  // A part's own colour; a combined part without one shows its first solid part's colour.
  colorOf(id, depth = 0) {
    const node = this.nodes.get(id); if (!node || depth > 32) return COLORS[0];
    if (node.color) return node.color;
    const child = node.kind === 'group' ? node.children.find(child => this.nodes.get(child)?.mode !== 'hole') : null;
    return child ? this.colorOf(child, depth + 1) : COLORS[0];
  }
  // Outline edges are slow for a long text, so an unchanged mesh reuses them. The key samples the mesh's numbers.
  edgesFor(mesh, geometry) {
    const values = mesh.positions; let sum = values.length * 31 + mesh.indices.length, mix = 7;
    for (let i = 0; i < values.length; i++) { const v = Math.round(values[i] * 1000); sum = (sum * 33 + v) % 2147483647; mix = (mix * 31 + v * (i % 3 + 1)) % 1000000007; }
    const key = `${values.length}:${mesh.indices.length}:${sum}:${mix}`, cache = this.edgeCache ??= new Map();
    let edges = cache.get(key);
    if (edges) { cache.delete(key); cache.set(key, edges); return edges; }
    edges = new THREE.EdgesGeometry(geometry, 30); edges.userData.shared = true; cache.set(key, edges);
    // The cache is small; edges no longer drawn are freed. A geometry still on screen is from this or the last result.
    while (cache.size > 48) { const [oldest, old] = cache.entries().next().value; cache.delete(oldest); (this.orphanEdges ??= new Set()).add(old); }
    // Edges pushed out of the cache are freed as soon as no part on screen uses them any more.
    for (const old of this.orphanEdges || []) if (![...cache.values()].includes(old) && !this.roots.children.some(object => object.children.some(child => child.geometry === old))) { old.dispose(); this.orphanEdges.delete(old); }
    return edges;
  }
  bounds(ids = this.selectedIds) {
    const box = new THREE.Box3(); let any = false;
    for (const object of this.roots.children) if (ids.includes(object.userData.id)) { if (!object.geometry.boundingBox) object.geometry.computeBoundingBox(); box.union(object.geometry.boundingBox); any = true; }
    return any ? box : null;
  }
  // Parts standing on `id`, and on those, and so on: their bottom is at most 1,2 mm from the top below and they overlap it from above.
  riders(id) {
    const found = [], queue = [id], seen = new Set([id]);
    while (queue.length && found.length < 64) {
      const base = this.bounds([queue.shift()]); if (!base) continue;
      for (const object of this.roots.children) {
        const other = object.userData.id; if (seen.has(other) || object.userData.mode === 'hole' || this.nodes.get(other)?.locked) continue;
        const box = this.bounds([other]);
        if (box && box.min.z > base.min.z + .1 && Math.abs(box.min.z - base.max.z) < 1.2 && box.min.x < base.max.x && box.max.x > base.min.x && box.min.y < base.max.y && box.max.y > base.min.y) { seen.add(other); found.push(other); queue.push(other); }
      }
    }
    return found;
  }
  // How much the top of `id` moves under a world transform.
  topShift(id, delta) {
    const box = this.bounds([id]); if (!box) return 0;
    const high = Math.max(...this.roots.children.filter(object => object.userData.id === id).map(object => highestZ(object.geometry.getAttribute('position').array, delta)));
    return Number.isFinite(high) ? high - box.max.z : 0;
  }
  // How far a world transform must lift the parts so their lowest point stays at the same height.
  keepBottomShift(ids, delta) {
    const box = this.bounds(ids); if (!box) return 0;
    const low = Math.min(...this.roots.children.filter(object => ids.includes(object.userData.id)).map(object => lowestZ(object.geometry.getAttribute('position').array, delta)));
    return Number.isFinite(low) ? box.min.z - low : 0;
  }
  localState(id = this.selectedIds[0]) {
    const node = this.nodes.get(id), metadata = this.metadata.get(id); if (!node || !metadata?.localBounds) return null;
    const matrix = objectMatrix(node), scale = new THREE.Vector3().setFromMatrixScale(matrix), quaternion = new THREE.Quaternion(), position = new THREE.Vector3(); matrix.decompose(position, quaternion, new THREE.Vector3());
    const euler = new THREE.Euler().setFromQuaternion(quaternion, 'XYZ'), box = metadata.localBounds;
    return {matrix, box, sheared: hasShear(matrix), scale: scale.toArray(), center: new THREE.Vector3(...box.min.map((v, i) => (v + box.max[i]) / 2)), size: box.min.map((v, i) => (box.max[i] - v) * scale.getComponent(i)), angles: [euler.x, euler.y, euler.z].map(THREE.MathUtils.radToDeg)};
  }
  fatLines(points, color, width, opacity = 1) {
    const geometry = new LineSegmentsGeometry().setPositions(points.flatMap(point => point.toArray()));
    const material = new LineMaterial({color, linewidth: width, transparent: true, opacity, depthWrite: false}); material.resolution.set(this.host.clientWidth || 1, this.host.clientHeight || 1);
    const lines = new LineSegments2(geometry, material); lines.renderOrder = 4; return lines;
  }
  select(ids) {
    this.dirty = true;
    this.selectedIds = [...ids]; if (this.drag?.active || this.pendingPreview) return;
    this.clear(this.selection);
    for (const id of ids) {
      const state = this.localState(id); if (!state) continue;
      // The outline sits just outside the part, so it never fights with the part's own faces and edges.
      const pad = state.scale.map(value => .45 / Math.max(value, 1e-6)), b = state.box;
      const corners = boxCorners(b.min.map((v, i) => v - pad[i]), b.max.map((v, i) => v + pad[i])).map(point => point.applyMatrix4(state.matrix));
      this.selection.add(this.fatLines(BOX_EDGES.flatMap(([a, c]) => [corners[a], corners[c]]), teal, 2.2));
    }
    const world = this.bounds(ids); if (world) this.addFootprint(world);
    this.buildHandles(); this.refreshLabels();
  }
  // A short-lived see-through wall shows the line the parts were lined up on, also when the camera looks at it at a slant.
  showGuide(axis, value, box) {
    if (!this.guideGroup) { this.guideGroup = new THREE.Group(); this.scene.add(this.guideGroup); }
    this.clear(this.guideGroup); clearTimeout(this.guideTimer);
    const lo = box.min.clone().subScalar(4), hi = box.max.clone().addScalar(4), [u, v] = [0, 1, 2].filter(i => i !== axis); lo.z = Math.max(0, lo.z);
    const corner = (a, b) => { const p = new THREE.Vector3(); p.setComponent(axis, value); p.setComponent(u, (a ? hi : lo).getComponent(u)); p.setComponent(v, (b ? hi : lo).getComponent(v)); return p; };
    const [a, b, c, d] = [corner(0, 0), corner(1, 0), corner(1, 1), corner(0, 1)];
    this.guideGroup.add(this.fatLines([a, b, b, c, c, d, d, a], 0x38798c, 2, .9));
    this.guideGroup.add(new THREE.Mesh(new THREE.BufferGeometry().setFromPoints([a, b, c, a, c, d]), new THREE.MeshBasicMaterial({color: 0x38798c, transparent: true, opacity: .13, side: THREE.DoubleSide, depthWrite: false})));
    this.dirty = true; this.guideTimer = setTimeout(() => { this.clear(this.guideGroup); this.dirty = true; }, 1600);
  }
  // Parts that do not touch the rest get an orange frame, so the pupil sees which ones the status line means.
  markLoose(ids) {
    if (!this.looseGroup) { this.looseGroup = new THREE.Group(); this.scene.add(this.looseGroup); }
    this.clear(this.looseGroup); this.dirty = true;
    for (const id of ids) {
      const box = this.bounds([id]); if (!box) continue;
      const corners = boxCorners([box.min.x - 1, box.min.y - 1, box.min.z], [box.max.x + 1, box.max.y + 1, box.max.z + 1]);
      this.looseGroup.add(this.fatLines(BOX_EDGES.flatMap(([a, c]) => [corners[a], corners[c]]), 0xe8772e, 1.6, .8));
    }
  }
  // A light shadow on the plate shows where the selection stands; a dashed line shows how far it floats.
  addFootprint(box) {
    const pad = .6, width = box.max.x - box.min.x + 2 * pad, depth = box.max.y - box.min.y + 2 * pad, center = box.getCenter(new THREE.Vector3());
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(width, depth), new THREE.MeshBasicMaterial({color: teal, transparent: true, opacity: .14, depthWrite: false}));
    shadow.position.set(center.x, center.y, .04); shadow.renderOrder = -1; this.selection.add(shadow);
    if (Math.abs(box.min.z) > .05) {
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(center.x, center.y, box.min.z), new THREE.Vector3(center.x, center.y, 0)]), new THREE.LineDashedMaterial({color: teal, dashSize: 1.6, gapSize: 1.2}));
      line.computeLineDistances(); this.selection.add(line);
    }
  }
  // Size handles for one part, a lift arrow and three rotation arcs. A group of parts gets the arrow and the arcs.
  buildHandles() {
    this.svg.replaceChildren(this.protractor); this.handleItems = [];
    const ids = this.selectedIds, world = this.bounds(ids); if (!ids.length || !world || ids.some(id => this.nodes.get(id)?.locked)) return;
    const state = ids.length === 1 ? this.localState(ids[0]) : null;
    if (state) for (const spec of resizeHandles(state.box)) this.addHandle({...spec, point: new THREE.Vector3(...spec.at).applyMatrix4(state.matrix)});
    const center = world.getCenter(new THREE.Vector3()), size = world.getSize(new THREE.Vector3());
    this.addHandle({type: 'lift', point: new THREE.Vector3(center.x, center.y, world.max.z)});
    if (state?.sheared) { this.placeHandles(); return; }
    for (const axis of [0, 1, 2]) {
      const plane = ROTATION_AXES[axis], radius = Math.hypot(size.dot(plane.e1), size.dot(plane.e2)) / 2 + 6;
      // The arc for turning on the plate lies at the bottom, like a turntable.
      const planeCenter = axis === 2 ? new THREE.Vector3(center.x, center.y, world.min.z + .3) : center.clone();
      this.addHandle({type: 'rotate', axis, center: center.clone(), planeCenter, radius});
    }
    // Position the new handles now; waiting for the next frame would flash them in the corner.
    this.placeHandles();
  }
  addHandle(spec) {
    const side = spec.type === 'resize' && !spec.corner && !spec.scale[2], tilt = spec.type === 'rotate' && spec.axis !== 2;
    const group = svg('g', {class: `handle ${spec.type}${spec.corner ? ' corner' : ''}${side || tilt ? ' extra' : ''}${tilt ? ' tilt' : ''}`}), title = svg('title');
    if (spec.type === 'rotate') { spec.parts = {hit: svg('path', {class: 'hit'}), arc: svg('path', {class: 'arc'}), tip: svg('path', {class: 'tip'})}; group.append(spec.parts.hit, spec.parts.arc, spec.parts.tip); }
    else if (spec.type === 'lift') group.append(svg('circle', {class: 'hit', r: 17}), svg('path', {class: 'knob', d: 'M0 -11 L8 1 L2.5 1 L2.5 10 L-2.5 10 L-2.5 1 L-8 1 Z'}));
    else { const size = spec.corner ? 13 : 11; group.append(svg('rect', {class: 'hit', x: -13, y: -13, width: 26, height: 26}), svg('rect', {class: 'knob', x: -size / 2, y: -size / 2, width: size, height: size, rx: 2})); }
    title.textContent = handleTitle(spec); group.append(title);
    group.addEventListener('pointerdown', event => this.beginHandle(spec, event));
    group.addEventListener('pointerenter', () => this.highlight(spec, true)); group.addEventListener('pointerleave', () => this.highlight(spec, false));
    spec.element = group; this.svg.append(group); this.handleItems.push(spec);
  }
  // Pointing at a handle lights up exactly the measures it changes.
  highlight(spec, on) {
    for (const label of this.labels) label.element.classList.toggle('hot', on && (spec.type === 'resize' ? label.type === 'resize' && spec.scale[label.axis] !== 0 : spec.type === 'lift' && label.type === 'lift'));
  }
  arcPoint(item, degrees, radius = item.radius) {
    const plane = ROTATION_AXES[item.axis], angle = THREE.MathUtils.degToRad(degrees);
    return item.planeCenter.clone().addScaledVector(plane.e1, Math.cos(angle) * radius).addScaledVector(plane.e2, Math.sin(angle) * radius);
  }
  placeHandles() {
    const drag = this.drag, active = Boolean(drag?.active || this.pendingPreview), rotating = Boolean(drag?.active && drag.handle?.type === 'rotate');
    if (this.looseGroup) this.looseGroup.visible = !active;
    this.svg.classList.toggle('dragging', active); this.svg.classList.toggle('rotating', rotating); this.svg.classList.toggle('tilt-on', Boolean(this.showTilt)); this.svg.classList.toggle('rotate-on', Boolean(this.showRotate));
    const toScreen = this.screenMapper();
    if (rotating) {
      // A fixed protractor with marks every 15° (longer every 45°) shows the turn while the part turns.
      const item = drag.handle; let d = '';
      for (let i = 0; i <= 48; i++) d += `${i ? ' L' : 'M'}${point2(toScreen(this.arcPoint(item, i * 7.5)))}`;
      for (let a = 0; a < 360; a += 15) d += ` M${point2(toScreen(this.arcPoint(item, a)))} L${point2(toScreen(this.arcPoint(item, a, item.radius * (a % 45 ? 1.06 : 1.13))))}`;
      this.protractor.setAttribute('d', d);
    }
    if (active || !this.handleItems.length) return;
    const view = this.camera.getWorldDirection(new THREE.Vector3());
    // A part that is small on screen keeps only its corners, its top, the arrow and the turntable arc.
    const spots = this.handleItems.filter(item => item.type === 'resize').map(item => toScreen(item.point));
    const extent = spots.length ? Math.max(Math.max(...spots.map(p => p.x)) - Math.min(...spots.map(p => p.x)), Math.max(...spots.map(p => p.y)) - Math.min(...spots.map(p => p.y))) : Infinity;
    this.svg.classList.toggle('compact', extent < 110);
    // On a very small part the handles shrink, so most of the part can still be grabbed and moved.
    this.svg.classList.toggle('tiny', extent < 55);
    // The measures shrink too and let most of a small part show through, so a click on the part reaches the part.
    this.overlay.classList.toggle('tiny', extent < 55);
    for (const item of this.handleItems) {
      if (item.type === 'rotate') {
        const plane = ROTATION_AXES[item.axis], eye = this.camera.position.clone().sub(item.planeCenter), middle = THREE.MathUtils.radToDeg(Math.atan2(eye.dot(plane.e2), eye.dot(plane.e1)));
        const screen = Array.from({length: 17}, (_, i) => toScreen(this.arcPoint(item, middle - ARC_SPAN + i * ARC_SPAN / 8)));
        // Seen almost edge-on, an arc is a line that cannot be dragged sensibly.
        const off = Math.abs(view.dot(plane.axis)) < .22 || screen.some(p => p.behind); item.element.classList.toggle('off', off); if (off) continue;
        const d = 'M' + screen.map(point2).join(' L'); item.parts.hit.setAttribute('d', d); item.parts.arc.setAttribute('d', d);
        item.parts.tip.setAttribute('d', arrowTip(screen[1], screen[0]) + ' ' + arrowTip(screen[15], screen[16]));
        continue;
      }
      const vertical = item.type === 'lift' || item.scale?.[2], p = toScreen(item.point), off = p.behind || (vertical && Math.abs(view.z) > .9);
      item.element.classList.toggle('off', off); if (off) continue;
      item.element.setAttribute('transform', `translate(${p.x.toFixed(1)} ${(p.y + (item.type === 'lift' ? -40 : 0)).toFixed(1)})`);
    }
  }
  refreshLabels() {
    const ids = this.selectedIds, same = ids.length === this.labelIds.length && ids.every((id, i) => id === this.labelIds[i]);
    if (same && this.labels.some(label => label.editing)) return;
    if (!same) this.activeEdit?.(true);
    this.overlay.replaceChildren(); this.labels = []; this.labelIds = [...ids];
    const world = this.bounds(ids); if (!ids.length || !world) return;
    const single = ids.length === 1 ? this.localState(ids[0]) : null;
    if (single) {
      // Each length is written on the edge nearest the camera.
      const {box: b, matrix, center: c} = single, eye = this.camera.position.clone().applyMatrix4(matrix.clone().invert());
      const x = eye.x >= c.x ? b.max[0] : b.min[0], y = eye.y >= c.y ? b.max[1] : b.min[1];
      [[c.x, y, b.min[2]], [x, c.y, b.min[2]], [x, y, c.z]].forEach((point, axis) => this.addLabel({type: 'resize', axis, id: ids[0], value: single.size[axis], point: new THREE.Vector3(...point).applyMatrix4(matrix)}));
    } else {
      const size = world.getSize(new THREE.Vector3()), c = world.getCenter(new THREE.Vector3()), eye = this.camera.position;
      const x = eye.x >= c.x ? world.max.x : world.min.x, y = eye.y >= c.y ? world.max.y : world.min.y;
      [[c.x, y, world.min.z], [x, c.y, world.min.z], [x, y, c.z]].forEach((point, axis) => this.addLabel({type: 'static', axis, value: size.getComponent(axis), point: new THREE.Vector3(...point)}));
      if (ids.length === 2) this.addGap(ids);
    }
    if (Math.abs(world.min.z) > .05) { const c = world.getCenter(new THREE.Vector3()); this.addLabel({type: 'lift', ids: [...ids], value: world.min.z, point: new THREE.Vector3(c.x, c.y, world.min.z / 2)}); }
    this.labelCenter = world.getCenter(new THREE.Vector3()); this.placeLabels();
  }
  // The free space between two parts along the axis where they stand apart. The reference is the part chosen first.
  addGap([first, second]) {
    const a = this.bounds([first]), b = this.bounds([second]); if (!a || !b) return;
    let best = null;
    for (const axis of [0, 1]) {
      const key = 'xy'[axis], right = b.min[key] - a.max[key], left = a.min[key] - b.max[key], gap = Math.max(right, left);
      if (gap >= 0 && (!best || gap > best.value)) best = {axis, value: gap, direction: right >= left ? 1 : -1};
    }
    if (!best) return;
    const key = 'xy'[best.axis], other = 'xy'[1 - best.axis], point = new THREE.Vector3();
    point[key] = best.direction > 0 ? (a.max[key] + b.min[key]) / 2 : (b.max[key] + a.min[key]) / 2;
    point[other] = (Math.max(a.min[other], b.min[other]) + Math.min(a.max[other], b.max[other])) / 2; point.z = Math.min(a.min.z, b.min.z);
    this.addLabel({type: 'gap', axis: best.axis, id: second, value: best.value, direction: best.direction, point});
  }
  addLabel(label) {
    if (this.selectedIds.some(id => this.nodes.get(id)?.locked)) label.type = 'static';
    const editable = label.type !== 'static', element = document.createElement(editable ? 'button' : 'span'), name = label.type === 'lift' ? 'Afstand til pladen' : label.type === 'gap' ? 'Afstand mellem figurerne' : AXES[label.axis].name;
    const tag = document.createElement('span'), value = document.createElement('span');
    tag.className = 'axis'; tag.textContent = label.type === 'lift' ? '↕' : label.type === 'gap' ? '↔' : AXES[label.axis].letter; value.textContent = `${decimal(label.value)} mm`;
    if (label.type !== 'lift' && label.type !== 'gap') element.dataset.axis = AXES[label.axis].letter;
    element.className = `measure${editable ? '' : ' static'}${label.type === 'lift' ? ' lift' : ''}${label.type === 'gap' ? ' gap' : ''}`; element.append(tag, value); element.title = editable ? `${name}: ${decimal(label.value)} mm. Klik for at skrive et nyt mål.` : name;
    if (editable) { element.type = 'button'; element.setAttribute('aria-label', element.title); element.addEventListener('click', () => this.editLabel(label)); }
    label.element = element; this.overlay.append(element); this.labels.push(label);
  }
  placeLabels() {
    if (!this.labels.length || !this.labelCenter) return;
    const hidden = Boolean(this.drag?.active || this.pendingPreview); this.overlay.hidden = hidden; if (hidden) return;
    const screen = this.screenMapper(), center = screen(this.labelCenter), placed = [];
    for (const label of this.labels) {
      const p = screen(label.point); label.element.hidden = p.behind; if (p.behind) continue;
      // Each label moves out from the selection's centre, and further out while it would cover an earlier label.
      let dx = p.x - center.x, dy = p.y - center.y; const length = Math.hypot(dx, dy);
      if (length < 1) { dx = 0; dy = 1; } else { dx /= length; dy /= length; }
      if (label.axis === 2 && label.type !== 'lift') { dx = dx < 0 ? -1 : 1; dy = 0; }
      if (label.type === 'gap') { dx = 0; dy = 0; }
      const width = label.element.offsetWidth || 70, height = label.element.offsetHeight || 22; let push = label.type === 'lift' ? 0 : 18, x, y;
      for (let tries = 0; tries < 24; tries++, push += 7) {
        x = p.x + dx * push; y = p.y + dy * push;
        if (!placed.some(other => Math.abs(other.x - x) < (other.width + width) / 2 + 3 && Math.abs(other.y - y) < (other.height + height) / 2 + 2)) break;
      }
      placed.push({x, y, width, height});
      label.element.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, -50%)`;
    }
  }
  editLabel(label) {
    if (!this.enabled || this.drag || this.pendingPreview || label.editing) return;
    label.editing = true; const input = document.createElement('input');
    Object.assign(input, {type: 'number', step: 'any', value: String(round(label.value)), className: 'measure-input'});
    input.setAttribute('aria-label', label.type === 'lift' ? 'Afstand til pladen i mm' : `${AXES[label.axis].name} i mm`);
    label.element.replaceChildren(input); input.focus(); input.select();
    let finished = false;
    const finish = commit => {
      if (finished) return; finished = true; label.editing = false; this.activeEdit = null;
      const value = Number(input.value), changed = input.value.trim() !== '' && Number.isFinite(value) && Math.abs(value - label.value) > 1e-6;
      this.refreshLabels();
      if (commit && changed) this.onEdit(label.type === 'lift' ? {type: 'lift', value, ids: label.ids} : label.type === 'gap' ? {type: 'gap', id: label.id, axis: label.axis, move: (value - label.value) * label.direction} : {type: 'resize', axis: label.axis, value, id: label.id});
    };
    input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); finish(true); } else if (event.key === 'Escape') { event.preventDefault(); finish(false); } });
    input.addEventListener('blur', () => finish(true)); this.activeEdit = finish;
  }
  pointerDown(event) {
    this.finishGlide();
    if (event.target !== this.renderer.domElement || event.button !== 0) return;
    this.activeEdit?.(true);
    if (document.activeElement?.matches?.('input, select')) document.activeElement.blur();
    if (this.drag) return;
    // With the hand on, the left button moves the view, also over parts.
    if (this.panMode && event.pointerType !== 'touch') return;
    const hits = this.rayAt(event.clientX, event.clientY).intersectObjects(this.roots.children, false), hit = hits[0];
    // Every part under the pointer, nearest first: clicking the chosen part again picks the one behind it.
    const stack = [...new Set(hits.map(item => item.object.userData.id))], again = this.selectedIds.length === 1 && stack.includes(this.selectedIds[0]);
    // On the empty plate the camera control turns the view; a click without movement clears the selection on release.
    if (!hit) {
      this.emptyPress = {pointer: event.pointerId, x: event.clientX, y: event.clientY, shift: event.shiftKey};
      // A left drag on the empty plate draws a frame; every part it touches is chosen. A finger still turns the view.
      if (event.pointerType !== 'touch' && this.enabled) { this.band = {pointer: event.pointerId, x: event.clientX, y: event.clientY, shift: event.shiftKey, element: null}; this.renderer.domElement.setPointerCapture(event.pointerId); }
      return;
    }
    this.orbit.enabled = false; this.orbitBlocked = event.pointerId;
    if (!this.enabled) return;
    // When the chosen part is somewhere under the pointer, a press holds on to it: a drag moves it and a click goes on to the next one.
    const id = hit.object.userData.id, later = again && !event.shiftKey && !this.selectedIds.includes(id);
    if (this.nodes.get(id)?.locked) {
      if (again && !event.shiftKey && stack.length > 1) this.onSelect(stack[(stack.indexOf(id) + 1) % stack.length], false); else this.onSelect(id, event.shiftKey);
      return;
    }
    // Shift-click toggles selection; it deliberately never starts a move.
    if (event.shiftKey) { this.onSelect(id, true); return; }
    // With the chosen part behind this one, the choice waits: a click picks the next part, a drag takes this one.
    if (!this.selectedIds.includes(id) && !later) this.onSelect(id, false);
    const grip = new THREE.Plane(new THREE.Vector3(0, 0, 1), -hit.point.z), ids = this.selectedIds.includes(id) ? this.selectedIds : [id], world = this.bounds(ids);
    const others = this.roots.children.filter(object => !ids.includes(object.userData.id) && object.userData.mode !== 'hole');
    // Only parts that stand on the plate or on another part land on things; a part lifted on purpose keeps its height.
    let below = null;
    if (world) { this.ray.set(new THREE.Vector3((world.min.x + world.max.x) / 2, (world.min.y + world.max.y) / 2, world.min.z + .6), new THREE.Vector3(0, 0, -1)); below = this.ray.intersectObjects(others, false)[0]; }
    const landing = Boolean(world) && ids.every(item => this.nodes.get(item)?.mode !== 'hole') && (Math.abs(world.min.z) < .05 || (below && below.distance < 1.2));
    this.drag = {pointer: event.pointerId, start: [event.clientX, event.clientY], grip, point: hit.point.clone(), active: false, landing, others, stack, again, takes: later ? id : null, bottom: world?.min.z ?? 0, center: world?.getCenter(new THREE.Vector3()) ?? hit.point.clone()};
    this.renderer.domElement.setPointerCapture(event.pointerId);
  }
  // How many screen pixels one millimetre along `axis` covers at `point`, and in which screen direction.
  screenAxis(point, axis) {
    const screen = this.project(point), unit = this.project(point.clone().add(axis)).sub(screen), pixelsPerMm = unit.length();
    return {screenUnit: pixelsPerMm > 1e-6 ? unit.normalize() : new THREE.Vector2(0, -1), pixelsPerMm: Math.max(.3, pixelsPerMm)};
  }
  beginHandle(spec, event) {
    if (event.button !== 0) return; this.activeEdit?.(true);
    if (!this.enabled || this.drag || this.pendingPreview) return;
    event.preventDefault(); event.stopPropagation();
    const world = this.bounds(), state = this.selectedIds.length === 1 ? this.localState() : null; if (!world || (spec.type === 'resize' && !state)) return;
    const drag = {pointer: event.pointerId, start: [event.clientX, event.clientY], handle: spec, state, world, active: false};
    drag.meshes = this.roots.children.filter(object => this.selectedIds.includes(object.userData.id)).map(object => object.geometry.getAttribute('position').array);
    if (spec.type === 'resize') {
      // The base plane is spanned by the local X and Y columns, also when a group has skewed the part.
      const up = new THREE.Vector3().setFromMatrixColumn(state.matrix, 0).cross(new THREE.Vector3().setFromMatrixColumn(state.matrix, 1)).normalize(); drag.inverse = state.matrix.clone().invert();
      // Bottom handles follow the pointer in the part's base plane; the top handle follows the screen.
      if (spec.scale[2]) Object.assign(drag, this.screenAxis(spec.point, up)); else drag.plane = new THREE.Plane().setFromNormalAndCoplanarPoint(up, spec.point);
    } else if (spec.type === 'lift') Object.assign(drag, this.screenAxis(spec.point, new THREE.Vector3(0, 0, 1)));
    else {
      const plane = ROTATION_AXES[spec.axis]; drag.plane = new THREE.Plane().setFromNormalAndCoplanarPoint(plane.axis, spec.planeCenter);
      const hit = this.rayAt(event.clientX, event.clientY).ray.intersectPlane(drag.plane, new THREE.Vector3());
      drag.flat = !hit || Math.abs(this.ray.ray.direction.dot(plane.axis)) < .12; drag.first = drag.last = hit ? planeAngle(plane, spec.planeCenter, hit) : 0;
    }
    this.drag = drag; this.highlight(spec, false); this.renderer.domElement.setPointerCapture(event.pointerId); this.orbit.enabled = false;
  }
  beginPreview() {
    this.clear(this.preview); this.preview.matrix.identity();
    for (const object of this.roots.children) if (this.selectedIds.includes(object.userData.id) && !this.nodes.get(object.userData.id)?.locked) {
      const ghost = new THREE.Mesh(object.geometry.clone(), object.material.clone()); ghost.material.onBeforeCompile = object.material.onBeforeCompile; ghost.material.transparent = true; ghost.material.opacity = .72; this.preview.add(ghost); object.visible = false;
    }
  }
  pointerMove(event) {
    if (this.band && event.pointerId === this.band.pointer) { this.updateBand(event); return; }
    const drag = this.drag;
    if (!drag) { this.hoverAt = [event.clientX, event.clientY]; this.dirty = true; return; }
    if (event.pointerId !== drag.pointer) return;
    const dx = event.clientX - drag.start[0], dy = event.clientY - drag.start[1];
    if (!drag.active && Math.hypot(dx, dy) < (drag.handle ? 3 : 5)) return;
    if (!drag.active) { drag.active = true; if (drag.takes) this.onSelect(drag.takes, false); this.beginPreview(); }
    const snap = event.altKey ? 0 : this.snap, handle = drag.handle;
    let delta, message;
    if (!handle) {
      const point = this.rayAt(event.clientX, event.clientY).ray.intersectPlane(drag.grip, new THREE.Vector3()); if (!point || !drag.point) return;
      const move = point.sub(drag.point); move.z = 0; if (snap) { move.x = Math.round(move.x / snap) * snap; move.y = Math.round(move.y / snap) * snap; }
      // The part's centre stays over the plate, so a drag at a flat camera angle cannot throw it far away.
      const half = PLATE_SIZE / 2; move.x = THREE.MathUtils.clamp(drag.center.x + move.x, -half, half) - drag.center.x; move.y = THREE.MathUtils.clamp(drag.center.y + move.y, -half, half) - drag.center.y;
      let lift = 0, onTop = false, centred = false;
      if (drag.landing) {
        const ray = this.rayAt(event.clientX, event.clientY), first = ray.intersectObjects(drag.others, false)[0];
        const aimed = first && first.face?.normal.z > .3 && first.distance < ray.ray.origin.distanceTo(point) ? first : null;
        if (aimed) { move.x = THREE.MathUtils.clamp(aimed.point.x, -half, half) - drag.center.x; move.y = THREE.MathUtils.clamp(aimed.point.y, -half, half) - drag.center.y; }
        // What is straight below the part's centre carries it, sunk 0,5 mm so they fuse. Close to the middle of
        // the part below, it clicks onto that middle, which makes a snowman or a tower easy to stack.
        this.ray.set(new THREE.Vector3(drag.center.x + move.x, drag.center.y + move.y, 5000), new THREE.Vector3(0, 0, -1));
        const support = this.ray.intersectObjects(drag.others, false).find(item => item.face?.normal.z > .05);
        if (support) {
          const box = this.bounds([support.object.userData.id]), middle = box.getCenter(new THREE.Vector3());
          const reach = THREE.MathUtils.clamp(.45 * Math.min(box.max.x - box.min.x, box.max.y - box.min.y), 3, 10), cx = drag.center.x + move.x, cy = drag.center.y + move.y;
          let top = support.point.z;
          if (Math.hypot(middle.x - cx, middle.y - cy) <= reach) {
            move.x += middle.x - cx; move.y += middle.y - cy; centred = true;
            top = this.topAt(support.object.userData.id, middle.x, middle.y) ?? top;
          }
          onTop = true; lift = top - .5 - drag.bottom;
        } else lift = -drag.bottom;
      }
      delta = new THREE.Matrix4().makeTranslation(move.x, move.y, lift); message = `Flyt ${decimal(move.x)}, ${decimal(move.y)} mm${onTop ? (centred ? ' · ovenpå, midt på' : ' · ovenpå') : ''}${snap ? ` · trin ${decimal(snap)} mm` : ' · frit'}`;
      drag.action = {type: 'move', delta};
    } else if (handle.type === 'resize') {
      const {state} = drag; let local;
      if (handle.scale[2]) local = [0, 0, (dx * drag.screenUnit.x + dy * drag.screenUnit.y) / drag.pixelsPerMm / state.scale[2]];
      else { const hit = this.rayAt(event.clientX, event.clientY).ray.intersectPlane(drag.plane, new THREE.Vector3()); if (!hit) return; hit.applyMatrix4(drag.inverse); local = [0, 1, 2].map(i => hit.getComponent(i) - handle.at[i]); }
      const kind = this.nodes.get(this.selectedIds[0])?.kind, round = ['sphere', 'halfsphere'].includes(kind), uniform = round ? !event.shiftKey : event.shiftKey;
      const resized = resizeFactors(state.box, state.scale, handle.scale, local, {snap, uniform}), {factor} = resized, pivot = handlePivot(state.box, handle.scale);
      if (!uniform && !event.shiftKey && ['cylinder', 'cone', 'tube'].includes(kind) && !handle.scale[2]) { const common = handle.scale[0] ? factor[0] : factor[1]; factor[0] = factor[1] = common; pivot[0] = handle.scale[0] ? pivot[0] : (state.box.min[0] + state.box.max[0]) / 2; pivot[1] = handle.scale[1] ? pivot[1] : (state.box.min[1] + state.box.max[1]) / 2; }
      const size = state.size.map((value, i) => value * factor[i]);
      delta = state.matrix.clone().multiply(translation(pivot)).multiply(new THREE.Matrix4().makeScale(...factor)).multiply(translation(pivot.map(v => -v))).multiply(drag.inverse);
      const lift = drag.world.min.z - Math.min(...drag.meshes.map(positions => lowestZ(positions, delta)));
      delta = new THREE.Matrix4().makeTranslation(0, 0, lift).multiply(delta);
      drag.action = {type: 'scale', id: this.selectedIds[0], factor, pivot, lift};
      message = [0, 1, 2].filter(i => uniform || handle.scale[i]).map(i => `${AXES[i].name} ${decimal(size[i])} mm`).join(' · ') + (uniform ? ' · samme form' : '') + (snap ? '' : ' · frit');
    } else if (handle.type === 'lift') {
      const bottom = drag.world.min.z; let next = bottom + (dx * drag.screenUnit.x + dy * drag.screenUnit.y) / drag.pixelsPerMm; if (snap) next = Math.round(next / snap) * snap;
      delta = new THREE.Matrix4().makeTranslation(0, 0, next - bottom); drag.action = {type: 'move', delta}; message = `Afstand til pladen ${decimal(next)} mm${snap ? '' : ' · frit'}`;
    } else {
      const plane = ROTATION_AXES[handle.axis], step = event.altKey ? 0 : event.shiftKey ? 45 : 15; let angle = dx * .6;
      if (!drag.flat) { const hit = this.rayAt(event.clientX, event.clientY).ray.intersectPlane(drag.plane, new THREE.Vector3()); if (hit) drag.last = unwrap(drag.last, planeAngle(plane, handle.planeCenter, hit)); angle = drag.last - drag.first; }
      angle = snapAngle(angle, step);
      // The part turns about its centre, and its lowest point keeps its height, so a part on the plate stays on it.
      const rotation = rotationAbout(handle.center, plane.axis, angle), low = Math.min(...drag.meshes.map(positions => lowestZ(positions, rotation)));
      delta = new THREE.Matrix4().makeTranslation(0, 0, drag.world.min.z - low).multiply(rotation); drag.action = {type: 'move', delta};
      message = `Drejning om ${plane.name}: ${decimal(angle)}°${step ? ` · trin ${step}°` : ' · frit'}`;
    }
    this.dirty = true; this.preview.matrix.copy(delta); this.preview.matrixWorldNeedsUpdate = true; this.selection.matrixAutoUpdate = false; this.selection.matrix.copy(delta); this.selection.matrixWorldNeedsUpdate = true;
    this.onDragState(message);
  }
  pointerUp(event) {
    const band = this.band;
    if (band && band.pointer === event.pointerId) {
      this.band = null; if (this.renderer.domElement.hasPointerCapture(event.pointerId)) this.renderer.domElement.releasePointerCapture(event.pointerId);
      if (band.element) { const rect = band.element.getBoundingClientRect(); band.element.remove(); this.emptyPress = null; this.onSelectMany?.(this.idsIn(rect), band.shift); return; }
    }
    const press = this.emptyPress; this.emptyPress = null;
    if (press?.pointer === event.pointerId && Math.hypot(event.clientX - press.x, event.clientY - press.y) < 5) this.onSelect(null, press.shift);
    if (this.orbitBlocked === event.pointerId && !this.drag?.active) { this.orbitBlocked = null; if (!this.pendingPreview) this.orbit.enabled = true; }
    const drag = this.drag; if (!drag || drag.pointer !== event.pointerId) return;
    this.orbitBlocked = null;
    const action = drag.active && drag.action;
    if (action) {
      // The worker may take a moment. Keep the last preview until an accepted render replaces it.
      this.pendingPreview = true; this.drag = null; this.orbit.enabled = true; this.onDragState('');
      if (this.renderer.domElement.hasPointerCapture(drag.pointer)) this.renderer.domElement.releasePointerCapture(drag.pointer);
    } else {
      this.cancelDrag();
      if (drag.again && drag.stack.length > 1) this.onSelect(drag.stack[(drag.stack.indexOf(this.selectedIds[0]) + 1) % drag.stack.length], false);
      else if (drag.stack.length > 1) this.onHint?.('Der ligger en figur bag den valgte. Klik igen for at vælge den.');
    }
    if (action?.type === 'move') this.onTransform(action.delta); else if (action) this.onEdit(action);
  }
  cancelDrag(clearPending = false) {
    this.dirty = true;
    if (this.pendingPreview && !clearPending || !this.drag && !this.pendingPreview) return;
    const pointer = this.drag?.pointer; this.drag = null; this.pendingPreview = false; this.clear(this.preview); this.preview.matrix.identity(); this.selection.matrix.identity(); this.selection.matrixWorldNeedsUpdate = true;
    for (const object of this.roots.children) object.visible = true; this.orbit.enabled = true;
    if (pointer !== undefined && this.renderer.domElement.hasPointerCapture(pointer)) this.renderer.domElement.releasePointerCapture(pointer);
    this.onDragState('');
  }
  cameraTo(name, minimum = 36) {
    this.dirty = true;
    // Finish and clear accumulated orbit/pan inertia before placing an explicit view.
    // update() without damping consumes the remaining deltas through the public API.
    this.finishGlide();
    const damping = this.orbit.enableDamping;
    this.orbit.enableDamping = false; this.orbit.update(); this.orbit.enableDamping = damping;
    if (name === 'in' || name === 'out') { const offset = this.camera.position.clone().sub(this.orbit.target); offset.multiplyScalar(name === 'in' ? .8 : 1.25); this.camera.position.copy(this.orbit.target).add(offset); this.orbit.update(); this.refreshLabels(); return; }
    // Named views always show the whole plate. "fit" frames the model from the current direction.
    const half = PLATE_SIZE / 2; let points = boxCorners([-half, -half, 0], [half, half, 10]), target = new THREE.Vector3(), direction = VIEWS[name] || VIEWS.home;
    if (name === 'fit') {
      const box = this.bounds(this.roots.children.map(object => object.userData.id)); direction = this.camera.position.clone().sub(this.orbit.target).toArray();
      if (box) { const center = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3()).max(new THREE.Vector3(minimum, minimum, 12)); target = center; points = boxCorners(center.clone().addScaledVector(size, -.5).toArray(), center.clone().addScaledVector(size, .5).toArray()); }
    }
    // The camera glides to the new view, so the pupil keeps track of the model; with reduced motion it jumps.
    const fromPosition = this.camera.position.clone(), fromTarget = this.orbit.target.clone();
    this.frame(points, target, direction);
    const still = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!still && fromPosition.distanceTo(this.camera.position) + fromTarget.distanceTo(this.orbit.target) > 1) {
      this.glide = {fromPosition, fromTarget, toPosition: this.camera.position.clone(), toTarget: this.orbit.target.clone(), start: performance.now(), duration: 350};
      this.camera.position.copy(fromPosition); this.orbit.target.copy(fromTarget); this.orbit.update(); this.stepGlide();
    } else { this.glide = null; this.refreshLabels(); }
  }
  finishGlide() {
    const glide = this.glide; if (!glide) return; this.glide = null;
    this.camera.position.copy(glide.toPosition); this.orbit.target.copy(glide.toTarget); this.orbit.update(); this.refreshLabels();
  }
  stepGlide() {
    const glide = this.glide, t = Math.min(1, (performance.now() - glide.start) / glide.duration), ease = 1 - (1 - t) ** 3;
    this.camera.position.lerpVectors(glide.fromPosition, glide.toPosition, ease); this.orbit.target.lerpVectors(glide.fromTarget, glide.toTarget, ease); this.dirty = true;
    if (t >= 1) { this.glide = null; this.orbit.update(); this.refreshLabels(); }
  }
  // Whether every part is inside the free part of the view, and how tall the model is.
  modelInView() {
    const box = this.bounds(this.roots.children.map(object => object.userData.id)); if (!box) return {visible: true, top: 0};
    const width = Math.max(1, this.host.clientWidth), right = 1 - 2 * (this.inset || 0) / width, left = -1 + 2 * (this.insetLeftPx || 0) / width;
    const visible = boxCorners(box.min.toArray(), box.max.toArray()).every(point => { const p = point.clone().project(this.camera); return p.z < 1 && p.x >= left && p.x <= right && p.y <= 1 && p.y >= -1; });
    return {visible, top: box.max.z};
  }
  // Finds the nearest camera distance at which every point fits inside the free part of the view.
  frame(points, target, direction) {
    const dir = new THREE.Vector3(...direction).normalize(), camera = this.camera; let near = this.orbit.minDistance, far = this.orbit.maxDistance;
    const inside = distance => {
      camera.position.copy(target).addScaledVector(dir, distance); camera.lookAt(target); camera.updateMatrixWorld(true);
      const right = 1 - 2 * (this.inset || 0) / Math.max(1, this.host.clientWidth) - .06, left = -1 + 2 * (this.insetLeftPx || 0) / Math.max(1, this.host.clientWidth) + .06;
      return points.every(point => { const p = point.clone().project(camera); return p.z < 1 && p.x >= left && p.x <= right && p.y <= .9 && p.y >= -.78; });
    };
    for (let i = 0; i < 30; i++) { const middle = (near + far) / 2; if (inside(middle)) far = middle; else near = middle; }
    camera.position.copy(target).addScaledVector(dir, far); this.orbit.target.copy(target); this.orbit.update();
  }
}
