import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {LineSegments2} from 'three/addons/lines/LineSegments2.js';
import {LineSegmentsGeometry} from 'three/addons/lines/LineSegmentsGeometry.js';
import {LineMaterial} from 'three/addons/lines/LineMaterial.js';
import {objectMatrix, hasShear, isConnector, uprightCylinderSection, PLATE_SIZE, COLORS} from './scene.js';
import {resizeHandles, handlePivot, resizeFactors, snapAngle, rotationAbout, lowestZ, highestZ, ROTATION_AXES, planeAngle, unwrap} from './handles.js';
import {DIAMETER_NAME, parseDiameter} from './diameter.js';

const teal = 0x38798c;
const round = value => Math.round(value * 100) / 100;
const decimal = value => String(round(value)).replace('.', ',');
// Directions point from the target towards the camera. Z is up.
const VIEWS = {home: [0.42, -1, 0.9], top: [0, -0.12, 1], bottom: [0, -0.12, -1], front: [0, -1, 0.24], right: [1, 0, 0.24], back: [0, 1, 0.24], left: [-1, 0, 0.24]};
// The direction cube: each face is a button. right/up span the face as the pupil reads it; normal points out of the cube.
const CUBE_FACES = [
  {view: 'top', label: 'Oven', normal: [0, 0, 1], right: [1, 0, 0], up: [0, 1, 0]},
  {view: 'front', label: 'Foran', normal: [0, -1, 0], right: [1, 0, 0], up: [0, 0, 1]},
  {view: 'right', label: 'Højre', normal: [1, 0, 0], right: [0, 1, 0], up: [0, 0, 1]},
  {view: 'back', label: 'Bag', normal: [0, 1, 0], right: [-1, 0, 0], up: [0, 0, 1]},
  {view: 'left', label: 'Venstre', normal: [-1, 0, 0], right: [0, -1, 0], up: [0, 0, 1]},
  {view: 'bottom', label: 'Nedefra', normal: [0, 0, -1], right: [1, 0, 0], up: [0, -1, 0]},
];
const CUBE_SIZE = 68;
const AXES = [{letter: 'L', name: 'Længde'}, {letter: 'B', name: 'Bredde'}, {letter: 'H', name: 'Højde'}];
// Corner i has x from bit 0, y from bit 1 and z from bit 2.
const BOX_EDGES = [[0, 1], [1, 3], [3, 2], [2, 0], [4, 5], [5, 7], [7, 6], [6, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
const boxCorners = (min, max) => Array.from({length: 8}, (_, i) => new THREE.Vector3(i & 1 ? max[0] : min[0], i & 2 ? max[1] : min[1], i & 4 ? max[2] : min[2]));
const translation = point => new THREE.Matrix4().makeTranslation(point[0], point[1], point[2]);
function gridGeometry(step, skip, complete = false) {
  const half = PLATE_SIZE / 2, points = [], count = Math.round(PLATE_SIZE / step);
  for (let i = 0; i <= count; i++) {
    const offset = i * step;
    if ((!complete && (i === 0 || i === count || offset === half)) || (skip && offset % skip === 0)) continue;
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
  if (spec.corner) return 'Klik for at se målene. Træk for at ændre længde og bredde. Shift bevarer formen.';
  return `Klik for at se målene. Træk for at ændre ${['længden', 'bredden', 'højden'][spec.scale.findIndex(Boolean)]}. Shift bevarer formen.`;
}

function holeMaterial(pixelRatio) {
  const material = new THREE.MeshBasicMaterial({color: 0xc4bed9, transparent: true, opacity: .24, depthWrite: false});
  material.onBeforeCompile = shader => {
    shader.uniforms.hatchPitch = {value: 11 * pixelRatio};
    shader.fragmentShader = 'uniform float hatchPitch;\n' + shader.fragmentShader.replace('#include <color_fragment>', `#include <color_fragment>
      float hatch = fract((gl_FragCoord.x + gl_FragCoord.y) / hatchPitch);
      float dark = 1.0 - smoothstep(0.20, 0.29, hatch);
      float light = smoothstep(0.30, 0.36, hatch) * (1.0 - smoothstep(0.42, 0.50, hatch));
      diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.075, 0.038, 0.14), dark);
      diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.89, 0.86, 0.95), light);
      diffuseColor.a = max(diffuseColor.a, max(dark * 0.86, light * 0.73));`);
  };
  return material;
}

export class View {
  constructor(host, onSelect, onTransform, onEdit, onDragState) {
    Object.assign(this, {host, onSelect, onTransform, onEdit, onDragState, selectedIds: [], metadata: new Map(), nodes: new Map(), snap: 1, enabled: true, labels: [], labelIds: [], handleItems: [], showSizes: false});
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color('#e8edef');
    this.camera = new THREE.PerspectiveCamera(38, 1, .5, 4000); this.camera.up.set(0, 0, 1); this.camera.position.set(100, -150, 145);
    this.renderer = new THREE.WebGLRenderer({antialias: true}); this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.domElement.setAttribute('aria-label', '3D-model. Træk i en figur for at flytte den. Træk en ramme på den tomme flade for at vælge flere. Træk med højre museknap for at dreje visningen.'); host.append(this.renderer.domElement);
    // Handles are SVG and measurements HTML, so both stay sharp, keep their size on screen and can be styled.
    this.svg = svg('svg', {class: 'handle-layer', 'aria-hidden': 'true'}); this.protractor = svg('path', {class: 'protractor'});
    this.holeGuide = svg('g', {class: 'hole-offset-guide', 'pointer-events': 'none', hidden: ''});
    this.holeGuideProjection = svg('path', {fill: 'none', stroke: '#fff', 'stroke-width': 2, 'stroke-dasharray': '2 3', hidden: ''});
    this.holeGuide.append(this.holeGuideProjection);
    this.holeGuideAxes = [0, 1].map(axis => {
      const group = svg('g', {'data-axis': axis});
      const outline = svg('path', {fill: 'none', stroke: '#fff', 'stroke-width': 5, 'stroke-linecap': 'round'});
      const line = svg('path', {fill: 'none', stroke: '#276171', 'stroke-width': 2, 'stroke-linecap': 'round'});
      const value = svg('text', {fill: '#1f4f5e', stroke: '#fff', 'stroke-width': 5, 'stroke-linejoin': 'round', 'paint-order': 'stroke', 'font-size': 13, 'font-weight': 600, 'text-anchor': 'middle', 'dominant-baseline': 'central'});
      group.append(outline, line, value); this.holeGuide.append(group);
      return {group, outline, line, value};
    });
    this.holeGuideCenter = svg('circle', {r: 4, fill: '#276171', stroke: '#fff', 'stroke-width': 2});
    this.holeGuide.append(this.holeGuideCenter);
    this.svg.append(this.holeGuide, this.protractor); host.append(this.svg);
    this.overlay = document.createElement('div'); this.overlay.className = 'measure-layer'; host.append(this.overlay);
    // A quiet label on the plate's front edge, so "Forfra" and "For" on the cube have something to point at.
    this.frontLabel = document.createElement('span'); this.frontLabel.className = 'plate-front'; this.frontLabel.textContent = 'Forside'; this.frontLabel.setAttribute('aria-hidden', 'true'); host.append(this.frontLabel);
    this.orbit = new OrbitControls(this.camera, this.renderer.domElement); this.orbit.target.set(0, 0, 0); this.orbit.enableDamping = true;
    this.orbit.mouseButtons = {LEFT: null, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.ROTATE}; this.orbit.rotateSpeed = .6; this.orbit.zoomSpeed = .6; this.orbit.zoomToCursor = true; this.orbit.minDistance = 12; this.orbit.maxDistance = 900; this.orbit.maxPolarAngle = Math.PI - .02; this.orbit.update();
    this.orbit.addEventListener('end', () => this.refreshLabels()); this.orbit.addEventListener('change', () => { this.dirty = true; });
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xa6b6c0, 2.3));
    const light = new THREE.DirectionalLight(0xffffff, 2.5); light.position.set(-70, -120, 220); this.scene.add(light);
    this.buildPlate(); this.buildCube();
    this.roots = new THREE.Group(); this.selection = new THREE.Group(); this.cutTargets = new THREE.Group(); this.preview = new THREE.Group(); this.preview.matrixAutoUpdate = false;
    this.scene.add(this.roots, this.selection, this.cutTargets, this.preview); this.ray = new THREE.Raycaster();
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
  // A small cube in the corner turns with the camera. Clicking a face turns around the current focus.
  buildCube() {
    this.cube = document.createElement('div'); this.cube.className = 'view-cube'; this.cube.setAttribute('role', 'group'); this.cube.setAttribute('aria-label', 'Vælg hvilken side du ser modellen fra');
    const body = document.createElement('div'); body.className = 'cube-body'; this.cube.append(body);
    this.cubeFaces = CUBE_FACES.map(face => {
      const element = document.createElement('button'); element.className = 'cube-face'; element.textContent = face.label;
      element.type = 'button'; element.title = face.view === 'bottom' ? 'Se modellen nedefra' : `Se modellen fra ${{top: 'oven', front: 'forsiden', right: 'højre side', back: 'bagsiden', left: 'venstre side'}[face.view]}`;
      element.addEventListener('click', () => this.cameraTo(face.view));
      body.append(element);
      // Element axes: x to the right, y down the face and z out of it. One CSS pixel is one cube unit.
      const half = CUBE_SIZE / 2, matrix = new THREE.Matrix4().makeBasis(new THREE.Vector3(...face.right), new THREE.Vector3(...face.up).negate(), new THREE.Vector3(...face.normal)).setPosition(new THREE.Vector3(...face.normal).multiplyScalar(half));
      return {element, matrix};
    });
    this.host.append(this.cube);
  }
  placeFrontLabel() {
    const p = this.screenMapper()(new THREE.Vector3(0, -PLATE_SIZE / 2 - 7, 0)), facing = this.camera.position.y < 0;
    this.frontLabel.hidden = !this.plate.visible || p.behind || !facing; if (!this.frontLabel.hidden) this.frontLabel.style.transform = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px) translate(-50%, -50%)`;
  }
  updateCube() {
    // World to camera rotation, then flip y because CSS y points down.
    const view = new THREE.Matrix4().makeRotationFromQuaternion(this.camera.quaternion.clone().invert()).premultiply(new THREE.Matrix4().makeScale(1, -1, 1));
    for (const face of this.cubeFaces) face.element.style.transform = `matrix3d(${view.clone().multiply(face.matrix).elements.map(value => value.toFixed(5)).join(',')})`;
  }
  buildPlate() {
    const half = PLATE_SIZE / 2, band = 1.4, plate = new THREE.Group();
    const surface = new THREE.Mesh(new THREE.PlaneGeometry(PLATE_SIZE, PLATE_SIZE), new THREE.MeshStandardMaterial({color: 0xfdfefe, roughness: 1, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 4}));
    surface.position.z = -.05;
    const square = size => [new THREE.Vector2(-size, -size), new THREE.Vector2(size, -size), new THREE.Vector2(size, size), new THREE.Vector2(-size, size)];
    const outline = new THREE.Shape(square(half + band)); outline.holes.push(new THREE.Path(square(half)));
    const frame = new THREE.Mesh(new THREE.ShapeGeometry(outline), new THREE.MeshBasicMaterial({color: 0xa5b6be, side: THREE.DoubleSide})); frame.position.z = -.04;
    const linesOnPlate = (geometry, color, opacity) => {
      const material = new THREE.LineBasicMaterial({color, transparent: true, opacity, depthWrite: false});
      const lines = new THREE.LineSegments(geometry, material);
      lines.position.z = .02; lines.renderOrder = -2; return lines;
    };
    // Show the 10 mm reference at a distance. Reveal finer steps as their spacing grows on screen.
    const gridColor = 0xa0b3bc;
    const majorGrid = linesOnPlate(gridGeometry(10, 0), gridColor, .62);
    this.gridDetails = [
      {step: 5, skip: 10, opacity: .35, fadeStart: 9, fadeEnd: 15},
      {step: 1, skip: 5, opacity: .29, fadeStart: 4, fadeEnd: 7},
    ].map(detail => ({...detail, lines: linesOnPlate(gridGeometry(detail.step, detail.skip), gridColor, 0)}));
    const centerGeometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-half, 0, 0), new THREE.Vector3(half, 0, 0),
      new THREE.Vector3(0, -half, 0), new THREE.Vector3(0, half, 0),
    ]);
    const centerLines = linesOnPlate(centerGeometry, 0x8198a2, .76); centerLines.renderOrder = -1;
    plate.add(surface, frame, majorGrid, ...this.gridDetails.map(detail => detail.lines), centerLines);
    this.workplaneGrid = linesOnPlate(gridGeometry(10, 0, true), teal, .36);
    this.workplaneGrid.visible = false;
    this.plate = plate;
    this.scene.add(plate, this.workplaneGrid);
  }
  setWorkplane(height) {
    const grid = this.workplaneGrid, raised = Number.isFinite(height) && height > 0;
    if (grid.visible === raised && (!raised || grid.position.z === height)) return;
    grid.visible = raised;
    if (raised) grid.position.z = height;
    this.dirty = true;
  }
  // Draws only when something changed: the camera moved, a result arrived, the selection or a drag changed or the size changed.
  // An idle workshop then costs almost nothing on an old pupil computer.
  animate() {
    if (this.glide) this.stepGlide();
    this.orbit.update();
    if (this.hoverAt) { this.updateHover(...this.hoverAt); this.hoverAt = null; }
    if (this.dirty) { this.dirty = false; this.updateGrid(); this.renderer.render(this.scene, this.camera); this.placeHandles(); this.placeLabels(); this.placeHoleOffsets(); this.updateCube(); this.placeFrontLabel(); }
    requestAnimationFrame(() => this.animate());
  }
  resize({preservePosition = false, recenter = false} = {}) {
    const w = this.host.clientWidth, h = this.host.clientHeight; if (!w || !h) return;
    const {left, top} = this.host.getBoundingClientRect(), previous = this.viewport;
    const inset = Math.min(w * .4, this.insetRight ? this.insetRight(w) : 0), insetLeft = Math.min(w * .3, this.insetLeft || 0);
    const sameSize = previous?.w === w && previous?.h === h;
    if (!recenter && sameSize && previous.left === left && previous.top === top && this.inset === inset && this.insetLeftPx === insetLeft) return;
    this.viewport = {left, top, w, h};
    this.inset = inset; this.insetLeftPx = insetLeft;
    this.dirty = true;
    let x = (inset - insetLeft) / 2, y = 0, fullHeight = h;
    if (preservePosition && previous) {
      // A panel can make the canvas smaller while the pupil is holding a part.
      // Keep the old projection's origin and scale in screen pixels on the new canvas.
      const old = this.camera.view;
      fullHeight = old.fullHeight;
      x = old.offsetX + left - previous.left + (w - previous.w) / 2;
      y = old.offsetY + top - previous.top;
      const selection = this.bounds();
      if (selection) {
        const p = selection.getCenter(new THREE.Vector3()).project(this.camera);
        const oldX = previous.left + (p.x + 1) / 2 * previous.w, oldY = previous.top + (1 - p.y) / 2 * previous.h;
        // If the panel takes over the selected part's position, move it just inside the new edge.
        if (p.z >= -1 && p.z <= 1) {
          const right = left + w, bottom = top + h;
          if (oldX >= right && oldX <= previous.left + previous.w) x += oldX - right + Math.min(40, w / 4);
          if (oldY >= bottom && oldY <= previous.top + previous.h) y += oldY - bottom + Math.min(40, h / 4);
        }
      }
    }
    if (!sameSize) this.renderer.setSize(w, h, false);
    this.camera.setViewOffset(w, fullHeight, x, y, w, h);
    for (const group of [this.selection, this.cutTargets, this.looseGroup, this.guideGroup]) group?.traverse(child => child.material?.resolution?.set(w, h));
    if (!this.homed) { this.homed = true; this.cameraTo('home'); }
  }
  updateGrid() {
    const {position, projectionMatrix} = this.camera, target = this.orbit.target;
    this.plate.visible = position.z >= 0;
    if (!this.plate.visible) return;
    const distance = position.distanceTo(target);
    // At a low angle, grid lines crowd together even before the camera zooms out.
    const inclination = Math.abs(position.z - target.z) / distance;
    const pixelsPerMm = this.host.clientHeight * projectionMatrix.elements[5] * inclination / (2 * distance);
    for (const {lines, step, opacity, fadeStart, fadeEnd} of this.gridDetails) {
      const amount = THREE.MathUtils.smoothstep(pixelsPerMm * step, fadeStart, fadeEnd);
      lines.material.opacity = opacity * amount;
      lines.visible = amount > .01;
    }
  }
  clear(group) { for (const object of [...group.children]) { group.remove(object); object.traverse(child => { if (!child.geometry?.userData.shared) child.geometry?.dispose(); if (Array.isArray(child.material)) child.material.forEach(material => material.dispose()); else child.material?.dispose(); }); } }
  geometry(mesh) { const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.BufferAttribute(mesh.positions, 3)); geometry.setIndex(new THREE.BufferAttribute(mesh.indices, 1)); geometry.computeVertexNormals(); return geometry; }
  rayAt(x, y) {
    const rect = this.renderer.domElement.getBoundingClientRect(); this.ray.setFromCamera(new THREE.Vector2((x - rect.left) / rect.width * 2 - 1, -(y - rect.top) / rect.height * 2 + 1), this.camera); return this.ray;
  }
  planePoint(x, y, height = 0) { return this.rayAt(x, y).ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), -height), new THREE.Vector3()); }
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
      const hole = mesh.mode === 'hole';
      const material = hole ? holeMaterial(this.renderer.getPixelRatio())
        : new THREE.MeshStandardMaterial({color: this.colorOf(mesh.id), roughness: .7, metalness: .015, flatShading: true});
      // A combined part of several colours arrives as coloured pieces with the same id; it behaves as one part.
      for (const piece of mesh.paints || [mesh]) {
        const color = mesh.paints ? piece.color : this.colorOf(mesh.id), pieceMaterial = mesh.paints ? new THREE.MeshStandardMaterial({color, roughness: .7, metalness: .015, flatShading: true}) : material;
        const object = new THREE.Mesh(this.geometry(piece), pieceMaterial); object.userData = {id: mesh.id, mode: mesh.mode}; object.renderOrder = hole ? 2 : 0; this.roots.add(object);
        const edgeColor = hole ? 0x483763 : new THREE.Color(color).multiplyScalar(.55);
        const outline = new THREE.LineSegments(this.edgesFor(piece, object.geometry), new THREE.LineBasicMaterial({color: edgeColor, transparent: true, opacity: hole ? .87 : .65}));
        outline.renderOrder = hole ? 3 : 0;
        object.add(outline);
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
  outlinePoints(id, padding) {
    const state = this.localState(id); if (!state) return null;
    const pad = state.scale.map(value => padding / Math.max(value, 1e-6)), b = state.box;
    const corners = boxCorners(b.min.map((v, i) => v - pad[i]), b.max.map((v, i) => v + pad[i])).map(point => point.applyMatrix4(state.matrix));
    return BOX_EDGES.flatMap(([a, c]) => [corners[a], corners[c]]);
  }
  select(ids) {
    this.dirty = true;
    this.selectedIds = [...ids]; if (this.drag?.active || this.pendingPreview) return;
    this.clear(this.selection);
    for (const id of ids) {
      // The outline sits just outside the part, so it never fights with the part's own faces and edges.
      const points = this.outlinePoints(id, .45);
      if (points) this.selection.add(this.fatLines(points, teal, 2.2));
    }
    const world = this.bounds(ids); if (world) this.addFootprint(world);
    this.buildHandles(); this.refreshLabels();
  }
  // A dashed outline marks the proposed cut targets without selecting them or adding handles.
  markCutTargets(ids) {
    if (!ids.length && !this.cutTargets.children.length) return;
    this.clear(this.cutTargets); this.dirty = true;
    for (const id of ids) {
      const points = this.outlinePoints(id, .8);
      if (!points) continue;
      const line = this.fatLines(points, 0x25477c, 2.8);
      Object.assign(line.material, {dashed: true, dashSize: 2, gapSize: 1.3, depthTest: false});
      line.computeLineDistances(); this.cutTargets.add(line);
    }
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
    this.svg.replaceChildren(this.holeGuide, this.protractor); this.handleItems = [];
    const ids = this.selectedIds, world = this.bounds(ids); if (!ids.length || !world || ids.some(id => this.nodes.get(id)?.locked)) return;
    const state = ids.length === 1 ? this.localState(ids[0]) : null;
    if (state && !isConnector(this.nodes.get(ids[0]))) {
      for (const spec of resizeHandles(state.box)) this.addHandle({...spec, center: state.center.clone().applyMatrix4(state.matrix), point: new THREE.Vector3(...spec.at).applyMatrix4(state.matrix)});
    }
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
    else {
      const size = spec.corner ? 15 : 13;
      spec.leader = svg('line', {class: 'resize-leader', x1: 0, y1: 0});
      group.append(spec.leader, svg('rect', {class: 'hit', x: -14, y: -14, width: 28, height: 28}), svg('rect', {class: 'knob', x: -size / 2, y: -size / 2, width: size, height: size, rx: 2}));
    }
    title.textContent = handleTitle(spec); group.append(title);
    group.addEventListener('pointerdown', event => this.beginHandle(spec, event));
    group.addEventListener('pointerenter', () => this.highlight(spec, true)); group.addEventListener('pointerleave', () => this.highlight(spec, false));
    spec.element = group; this.svg.append(group); this.handleItems.push(spec);
  }
  // Pointing at a handle lights up exactly the measures it changes.
  highlight(spec, on) {
    for (const label of this.labels) label.element.classList.toggle('hot', on && (spec.type === 'resize'
      ? label.type === 'resize' && (label.diameter ? spec.scale[0] !== 0 || spec.scale[1] !== 0 : spec.scale[label.axis] !== 0)
      : spec.type === 'lift' && label.type === 'lift'));
  }
  arcPoint(item, degrees, radius = item.radius) {
    const plane = ROTATION_AXES[item.axis], angle = THREE.MathUtils.degToRad(degrees);
    return item.planeCenter.clone().addScaledVector(plane.e1, Math.cos(angle) * radius).addScaledVector(plane.e2, Math.sin(angle) * radius);
  }
  placeHandles() {
    const drag = this.drag, active = Boolean(drag?.active || this.pendingPreview), rotating = Boolean(drag?.active && drag.handle?.type === 'rotate');
    if (this.looseGroup) this.looseGroup.visible = !active;
    this.cutTargets.visible = !active;
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
      item.element.classList.toggle('off', off); item.screenPoint = null; if (off) continue;
      let dx = 0, dy = item.type === 'lift' ? -40 : 0;
      if (item.type === 'resize') {
        const center = toScreen(item.center);
        const direction = vertical ? new THREE.Vector2(0, -1) : new THREE.Vector2(p.x - center.x, p.y - center.y).normalize();
        const offset = extent < 55 ? 8 : 12;
        dx = direction.x * offset; dy = direction.y * offset;
        item.leader.setAttribute('x2', -dx); item.leader.setAttribute('y2', -dy);
        const cursor = item.corner ? (dx * dy < 0 ? 'nesw-resize' : 'nwse-resize') : (Math.abs(dx) > Math.abs(dy) ? 'ew-resize' : 'ns-resize');
        item.element.style.cursor = cursor;
        if (extent >= 110 || !item.element.classList.contains('extra')) item.screenPoint = {x: p.x + dx, y: p.y + dy};
      }
      item.element.setAttribute('transform', `translate(${(p.x + dx).toFixed(1)} ${(p.y + dy).toFixed(1)})`);
    }
  }
  refreshLabels() {
    const ids = this.selectedIds, same = ids.length === this.labelIds.length && ids.every((id, i) => id === this.labelIds[i]);
    if (same && this.labels.some(label => label.editing)) return;
    if (!same) { this.showSizes = false; this.activeEdit?.(true); }
    this.overlay.replaceChildren(); this.labels = []; this.labelIds = [...ids];
    const world = this.bounds(ids); if (!ids.length || !world) return;
    const single = ids.length === 1 ? this.localState(ids[0]) : null;
    if (single) {
      // Each length is written on the edge nearest the camera.
      const {box: b, matrix, center: c} = single, eye = this.camera.position.clone().applyMatrix4(matrix.clone().invert());
      const x = eye.x >= c.x ? b.max[0] : b.min[0], y = eye.y >= c.y ? b.max[1] : b.min[1];
      const section = uprightCylinderSection(this.nodes.get(ids[0]), matrix);
      const freeCylinder = Boolean(section && (section.diameter === null || ids[0] === this.freeCylinderId));
      const diameter = freeCylinder ? null : section?.diameter ?? null;
      const fixedSize = isConnector(this.nodes.get(ids[0]));
      [[c.x, y, b.min[2]], [x, c.y, b.min[2]], [x, y, c.z]].forEach((point, axis) => {
        const radial = diameter !== null && axis === 0;
        if (axis < 2 && !radial && (this.holeOffsets || diameter !== null)) return;
        const aboveOffsets = radial && Boolean(this.holeOffsets);
        const anchor = aboveOffsets ? [c.x, c.y, b.max[2]] : point;
        this.addLabel({type: fixedSize ? 'static' : 'resize', axis, id: ids[0], diameter: radial, freeCylinder: freeCylinder && axis < 2, aboveOffsets,
          value: radial ? diameter : single.size[axis], point: new THREE.Vector3(...anchor).applyMatrix4(matrix)});
      });
    } else {
      const size = world.getSize(new THREE.Vector3()), c = world.getCenter(new THREE.Vector3()), eye = this.camera.position;
      const x = eye.x >= c.x ? world.max.x : world.min.x, y = eye.y >= c.y ? world.max.y : world.min.y;
      [[c.x, y, world.min.z], [x, c.y, world.min.z], [x, y, c.z]].forEach((point, axis) => this.addLabel({type: 'static', axis, value: size.getComponent(axis), point: new THREE.Vector3(...point)}));
      if (ids.length === 2) this.addGap(ids);
    }
    if (Math.abs(world.min.z) > .05) {
      const c = world.getCenter(new THREE.Vector3()), outline = boxCorners(world.min.toArray(), world.max.toArray());
      this.addLabel({type: 'lift', ids: [...ids], value: world.min.z, point: new THREE.Vector3(c.x, c.y, world.min.z / 2), outline});
    }
    this.labelCenter = world.getCenter(new THREE.Vector3()); this.placeLabels();
  }
  setHoleOffsets(reference) {
    const changed = Boolean(this.holeOffsets) !== Boolean(reference);
    this.holeOffsets = reference ? {...reference, holeTop: this.bounds()?.max.z ?? reference.bounds.max.z} : null;
    if (changed) this.refreshLabels();
    this.placeHoleOffsets();
  }
  // The rulers are in the plane of the reference's top face, through the projected centre of the hole.
  placeHoleOffsets() {
    const hidden = !this.holeOffsets || this.drag?.active || this.pendingPreview;
    this.holeGuide.toggleAttribute('hidden', Boolean(hidden));
    if (hidden) return;
    const {bounds, center, holeTop, axis: focused} = this.holeOffsets, map = this.screenMapper();
    const hole = center.clone().setZ(bounds.max.z), to = map(hole);
    this.holeGuide.toggleAttribute('hidden', to.behind);
    if (to.behind) return;
    this.holeGuideCenter.setAttribute('cx', to.x); this.holeGuideCenter.setAttribute('cy', to.y);
    // At different heights a short drop line connects the visible hole to the reference plane.
    const top = map(center.clone().setZ(holeTop));
    const showProjection = !top.behind && holeTop > bounds.max.z && Math.hypot(top.x - to.x, top.y - to.y) >= 8;
    this.holeGuideProjection.toggleAttribute('hidden', !showProjection);
    if (showProjection) this.holeGuideProjection.setAttribute('d', `M${point2(top)} L${point2(to)}`);
    const {clientWidth: width, clientHeight: height} = this.host;
    this.holeGuideAxes.forEach(({group, outline, line, value}, axis) => {
      const edge = bounds.min.getComponent(axis), start = hole.clone().setComponent(axis, edge);
      const from = map(start), inside = map(start.clone().setComponent(axis, edge + 1));
      const direction = new THREE.Vector2(inside.x - from.x, inside.y - from.y);
      const skip = (focused !== null && focused !== axis) || from.behind || direction.lengthSq() < .01;
      group.toggleAttribute('hidden', skip);
      if (skip) return;
      direction.normalize();
      const dx = direction.y * 5, dy = -direction.x * 5;
      const path = `M${point2({x: from.x - dx, y: from.y - dy})} L${point2({x: from.x + dx, y: from.y + dy})} M${point2(from)} L${point2(to)}`;
      outline.setAttribute('d', path); line.setAttribute('d', path); line.setAttribute('stroke-width', focused === axis ? 3 : 2);
      const gap = 16 + 19 * Math.abs(direction.x);
      value.setAttribute('x', THREE.MathUtils.clamp(from.x - direction.x * gap, 30, width - 30));
      value.setAttribute('y', THREE.MathUtils.clamp(from.y - direction.y * gap, 12, height - 12));
      value.textContent = `${decimal(center.getComponent(axis) - edge)} mm`;
    });
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
    const editable = label.type !== 'static' && !this.selectedIds.some(id => this.nodes.get(id)?.locked);
    const axis = label.diameter ? {letter: 'Ø', name: DIAMETER_NAME} : AXES[label.axis];
    const element = document.createElement(editable ? 'button' : 'span'), name = label.type === 'lift' ? 'Afstand til pladen' : label.type === 'gap' ? 'Afstand mellem figurerne' : axis.name;
    const tag = document.createElement('span'), value = document.createElement('span');
    tag.className = 'axis'; tag.textContent = label.type === 'lift' ? '↕' : label.type === 'gap' ? '↔' : axis.letter; value.textContent = `${decimal(label.value)} mm`;
    if (label.type !== 'lift' && label.type !== 'gap') element.dataset.axis = axis.letter;
    element.className = `measure${editable ? '' : ' static'}${label.type === 'lift' ? ' lift' : ''}${label.type === 'gap' ? ' gap' : ''}`;
    element.append(tag, ' ', value); element.title = editable ? `${name}: ${value.textContent}. Klik for at skrive et nyt mål.` : name;
    if (editable) { element.type = 'button'; element.setAttribute('aria-label', `${element.textContent}. ${name}`); element.addEventListener('click', () => this.editLabel(label)); }
    Object.assign(label, {element, valueElement: value}); this.overlay.append(element); this.labels.push(label);
  }
  placeLabels() {
    if (!this.labels.length || !this.labelCenter) return;
    const resize = this.drag?.active && this.drag.action?.type === 'scale' ? this.drag.action : null;
    const hidden = Boolean((this.drag?.active && !resize) || this.pendingPreview);
    this.overlay.hidden = hidden; this.overlay.classList.toggle('resizing', Boolean(resize)); if (hidden) return;
    const screen = this.screenMapper(), delta = resize && this.preview.matrix;
    const project = point => screen(delta ? point.clone().applyMatrix4(delta) : point);
    const center = project(this.labelCenter);
    const placed = resize ? [] : this.handleItems.flatMap(({screenPoint}) => screenPoint ? [{...screenPoint, width: 23, height: 23}] : []);
    const viewZ = Math.abs(this.camera.getWorldDirection(new THREE.Vector3()).z);
    for (const label of this.labels) {
      const size = label.type === 'resize' || label.type === 'static';
      const p = project(label.point);
      label.element.hidden = p.behind || (resize && !size) || (size && !this.showSizes);
      if (label.element.hidden) continue;
      if (size && !label.editing) label.valueElement.textContent = `${decimal(label.value * (resize?.factor[label.axis] ?? 1))} mm`;
      // Each label moves out from the selection's centre, and further out while it would cover an earlier label.
      let dx = p.x - center.x, dy = p.y - center.y; const length = Math.hypot(dx, dy);
      if (length < 1) { dx = 0; dy = 1; } else { dx /= length; dy /= length; }
      if (label.aboveOffsets) { dx = 0; dy = -1; }
      if (label.axis === 2 && label.type !== 'lift') { dx = dx < 0 ? -1 : 1; dy = 0; }
      if (label.type === 'gap') { dx = 0; dy = 0; }
      const width = label.element.offsetWidth || 70, height = label.element.offsetHeight || 22; let push = label.type === 'lift' ? 0 : label.aboveOffsets ? 30 : 18, x, y;
      if (label.type === 'lift' && viewZ > .9) {
        // From above or below the drop line projects onto the model. Keep the measurement beside it.
        const edges = label.outline.map(point => screen(point).x), left = Math.min(...edges), right = Math.max(...edges);
        dx = left >= width + 10 ? -1 : 1; dy = 0;
        p.x = dx < 0 ? left : right; push = width / 2 + 10;
      }
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
    const cylinder = label.diameter || label.freeCylinder;
    if (cylinder) Object.assign(input, {type: 'text', inputMode: 'decimal', value: decimal(label.value)});
    const initial = input.value;
    const name = label.type === 'lift' ? 'Afstand til pladen' : label.diameter ? DIAMETER_NAME : AXES[label.axis].name;
    input.setAttribute('aria-label', `${name} i mm`);
    const editor = document.createElement('div');
    editor.className = label.element.className; editor.style.transform = label.element.style.transform;
    if (label.element.dataset.axis) editor.dataset.axis = label.element.dataset.axis;
    editor.append(input); label.element.replaceWith(editor); label.element = editor;
    input.focus(); input.select();
    let finished = false;
    const finish = commit => {
      if (finished) return; finished = true; label.editing = false; this.activeEdit = null;
      const value = cylinder ? parseDiameter(input.value) : Number(input.value);
      const changed = input.value.trim() !== '' && Number.isFinite(value) && Math.abs(value - label.value) > 1e-6;
      this.refreshLabels();
      if (cylinder) {
        if (commit && input.value !== initial && (changed || !Number.isFinite(value))) this.onEdit({type: label.diameter ? 'diameter' : 'cylinder-axis', axis: label.axis, id: label.id, value});
        return;
      }
      if (commit && changed) this.onEdit(label.type === 'lift' ? {type: 'lift', value, ids: label.ids} : label.type === 'gap' ? {type: 'gap', id: label.id, axis: label.axis, move: (value - label.value) * label.direction} : {type: 'resize', axis: label.axis, value, id: label.id});
    };
    input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); finish(true); } else if (event.key === 'Escape') { event.preventDefault(); finish(false); } });
    input.addEventListener('blur', () => finish(true)); this.activeEdit = finish;
  }
  pointerDown(event) {
    this.finishGlide();
    const gripPadding = event.pointerType === 'touch' && event.target.matches('.handle.resize .hit');
    if ((event.target !== this.renderer.domElement && !gripPadding) || event.button !== 0) return;
    const hits = this.rayAt(event.clientX, event.clientY).intersectObjects(this.roots.children, false), hit = hits[0];
    if (gripPadding) {
      if (!hit) return;
      event.preventDefault(); event.stopPropagation();
    }
    this.activeEdit?.(true);
    if (document.activeElement?.matches?.('input, select')) document.activeElement.blur();
    if (this.drag) return;
    // With the hand on, the left button moves the view, also over parts.
    if (this.panMode && event.pointerType !== 'touch') return;
    this.showSizes = false; this.placeLabels();
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
    // Keep the full selection while the pointer is down so a drag can move it together.
    const selectOnClick = this.selectedIds.length > 1 && this.selectedIds.includes(id) ? id : null;
    const grip = new THREE.Plane(new THREE.Vector3(0, 0, 1), -hit.point.z), ids = this.selectedIds.includes(id) ? this.selectedIds : [id], world = this.bounds(ids);
    // Selection may have moved the projection in from the screen edge. Start the drag in that projection.
    const point = this.rayAt(event.clientX, event.clientY).ray.intersectPlane(grip, new THREE.Vector3()) ?? hit.point.clone();
    const others = this.roots.children.filter(object => !ids.includes(object.userData.id) && object.userData.mode !== 'hole');
    // Only parts that stand on the plate or on another part land on things; a part lifted on purpose keeps its height.
    let below = null;
    if (world) { this.ray.set(new THREE.Vector3((world.min.x + world.max.x) / 2, (world.min.y + world.max.y) / 2, world.min.z + .6), new THREE.Vector3(0, 0, -1)); below = this.ray.intersectObjects(others, false)[0]; }
    const landing = Boolean(world) && ids.every(item => this.nodes.get(item)?.mode !== 'hole') && (Math.abs(world.min.z) < .05 || (below && below.distance < 1.2));
    this.drag = {pointer: event.pointerId, start: [event.clientX, event.clientY], grip, point, active: false, landing, others, stack, again, selectOnClick, takes: later ? id : null, bottom: world?.min.z ?? 0, center: world?.getCenter(new THREE.Vector3()) ?? hit.point.clone()};
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
      this.showSizes = true;
      // The base plane is spanned by the local X and Y columns, also when a group has skewed the part.
      const up = new THREE.Vector3().setFromMatrixColumn(state.matrix, 0).cross(new THREE.Vector3().setFromMatrixColumn(state.matrix, 1)).normalize(); drag.inverse = state.matrix.clone().invert();
      // Bottom handles follow the pointer in the part's base plane; the top handle follows the screen.
      if (spec.scale[2]) Object.assign(drag, this.screenAxis(spec.point, up));
      else {
        drag.plane = new THREE.Plane().setFromNormalAndCoplanarPoint(up, spec.point);
        const start = this.rayAt(event.clientX, event.clientY).ray.intersectPlane(drag.plane, new THREE.Vector3());
        drag.localStart = start ? start.applyMatrix4(drag.inverse) : new THREE.Vector3(...spec.at);
      }
    } else if (spec.type === 'lift') Object.assign(drag, this.screenAxis(spec.point, new THREE.Vector3(0, 0, 1)));
    else {
      const plane = ROTATION_AXES[spec.axis]; drag.plane = new THREE.Plane().setFromNormalAndCoplanarPoint(plane.axis, spec.planeCenter);
      const hit = this.rayAt(event.clientX, event.clientY).ray.intersectPlane(drag.plane, new THREE.Vector3());
      drag.flat = !hit || Math.abs(this.ray.ray.direction.dot(plane.axis)) < .12; drag.first = drag.last = hit ? planeAngle(plane, spec.planeCenter, hit) : 0;
    }
    this.drag = drag; this.placeLabels(); this.highlight(spec, false); this.renderer.domElement.setPointerCapture(event.pointerId); this.orbit.enabled = false;
  }
  hasConnector(id) {
    const node = this.nodes.get(id);
    return isConnector(node) || Boolean(node?.children?.some(child => this.hasConnector(child)));
  }
  beginPreview() {
    this.clear(this.preview); this.preview.matrix.identity();
    for (const object of this.roots.children) if (this.selectedIds.includes(object.userData.id) && !this.nodes.get(object.userData.id)?.locked) {
      // The boolean preview is one mesh. Keep it still and preview only the new envelope when links retain their size.
      if (this.drag?.handle?.type === 'resize' && this.hasConnector(object.userData.id)) {
        const {box, matrix} = this.drag.state;
        const corners = boxCorners(box.min, box.max).map(point => point.applyMatrix4(matrix));
        this.preview.add(this.fatLines(BOX_EDGES.flatMap(([a, b]) => [corners[a], corners[b]]), teal, 2, .9));
        continue;
      }
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
      else { const hit = this.rayAt(event.clientX, event.clientY).ray.intersectPlane(drag.plane, new THREE.Vector3()); if (!hit) return; local = hit.applyMatrix4(drag.inverse).sub(drag.localStart).toArray(); }
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
      if (drag.active || drag.handle) return;
      if (drag.selectOnClick) this.onSelect(drag.selectOnClick, false);
      else if (drag.again && drag.stack.length > 1) this.onSelect(drag.stack[(drag.stack.indexOf(this.selectedIds[0]) + 1) % drag.stack.length], false);
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
    const wasVisible = this.modelInView().visible;
    this.resize({recenter: true});
    // Only "home" resets to the whole plate. Named angles keep the pupil's working focus and zoom.
    const half = PLATE_SIZE / 2; let points = boxCorners([-half, -half, 0], [half, half, 10]), target = new THREE.Vector3(), direction = VIEWS[name] || VIEWS.home;
    const box = this.bounds(this.roots.children.map(object => object.userData.id));
    const keepFocus = box && name !== 'home' && Boolean(VIEWS[name]);
    if (name === 'fit') {
      direction = this.camera.position.clone().sub(this.orbit.target).toArray();
      if (box) { const center = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3()).max(new THREE.Vector3(minimum, minimum, 12)); target = center; points = boxCorners(center.clone().addScaledVector(size, -.5).toArray(), center.clone().addScaledVector(size, .5).toArray()); }
    }
    // The camera glides to the new view, so the pupil keeps track of the model; with reduced motion it jumps.
    const fromPosition = this.camera.position.clone(), fromTarget = this.orbit.target.clone();
    if (keepFocus) {
      target = fromTarget.clone();
      const distance = fromPosition.distanceTo(fromTarget), dir = new THREE.Vector3(...direction).normalize();
      this.camera.position.copy(target).addScaledVector(dir, distance); this.orbit.update(); this.camera.updateMatrixWorld(true);
      // A long or tall model may need a little more space from its new side. Do not disturb a deliberate close-up.
      if (wasVisible && !this.modelInView().visible) {
        this.frame(boxCorners(box.min.toArray(), box.max.toArray()), target, direction);
        const fittedDistance = this.camera.position.distanceTo(target);
        this.camera.position.copy(target).addScaledVector(dir, Math.max(distance, fittedDistance)); this.orbit.update();
      }
    } else this.frame(points, target, direction);
    const still = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!still && fromPosition.distanceTo(this.camera.position) + fromTarget.distanceTo(this.orbit.target) > 1) {
      const toPosition = this.camera.position.clone(), toTarget = this.orbit.target.clone();
      const fromOffset = fromPosition.clone().sub(fromTarget), toOffset = toPosition.clone().sub(toTarget);
      const fromDistance = fromOffset.length(), toDistance = toOffset.length();
      const fromDirection = fromOffset.normalize(), rotation = new THREE.Quaternion().setFromUnitVectors(fromDirection, toOffset.normalize());
      this.glide = {fromTarget, toPosition, toTarget, fromDistance, toDistance, fromDirection, rotation, start: performance.now(), duration: 350};
      this.camera.position.copy(fromPosition); this.orbit.target.copy(fromTarget); this.orbit.update(); this.stepGlide();
    } else { this.glide = null; this.refreshLabels(); }
  }
  finishGlide() {
    const glide = this.glide; if (!glide) return; this.glide = null;
    this.camera.position.copy(glide.toPosition); this.orbit.target.copy(glide.toTarget); this.orbit.update(); this.refreshLabels();
    this.dirty = true; // OrbitControls can suppress the change event for the small final step.
  }
  stepGlide() {
    const glide = this.glide, t = Math.min(1, (performance.now() - glide.start) / glide.duration), ease = 1 - (1 - t) ** 3;
    if (t >= 1) { this.finishGlide(); return; }
    this.orbit.target.lerpVectors(glide.fromTarget, glide.toTarget, ease);
    // Go around the model so turning overhead to underneath does not move the camera through it.
    this.camera.position.copy(glide.fromDirection).applyQuaternion(new THREE.Quaternion().slerp(glide.rotation, ease))
      .multiplyScalar(THREE.MathUtils.lerp(glide.fromDistance, glide.toDistance, ease)).add(this.orbit.target);
    this.dirty = true;
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
