import * as THREE from 'three';
import {SceneModel, findFreeSpot, objectMatrix, rotatedMatrix, resizeStep, scaledMatrix, PLATE_SIZE, COLORS} from './scene.js';
import {View} from './view.js';
import {validateSTL} from '../stl-validation.mjs';
import {exportBinaryStl} from './geometry.js';
import {GUIDES} from './guides.js';
import {readStl} from './stl-import.js';
import {readSvg} from './svg-import.js';
const $ = id => document.getElementById(id), model = new SceneModel();
const labels = {box: 'Kasse', cylinder: 'Cylinder', sphere: 'Kugle', cone: 'Kegle', wedge: 'Kile', tube: 'Rør', roof: 'Tag', halfsphere: 'Halvkugle', pyramid: 'Pyramide', star: 'Stjerne', heart: 'Hjerte', text: 'Tekst', mesh: 'Hentet figur', group: 'Samlet figur'};
const colorNames = ['Rød', 'Orange', 'Gul', 'Grøn', 'Blå', 'Lilla', 'Lyserød', 'Hvid', 'Sort'];
const status = text => { $('status').textContent = text; }, round = value => Math.round(value * 100) / 100;
let sequence = 0, result = null, rendered = -1, previous = null, workerReady = false, failed = false, contextLost = false, stickyError = '';
// A text's visible size is its parameters times any stretch from the size handles.
const textScale = node => new THREE.Vector3().setFromMatrixScale(objectMatrix(node));
const worker = new Worker(new URL('./model3d-geometry-worker.js', import.meta.url), {type: 'module'});
let view, select, transform, editObject;
try {
  view = new View($('canvas'), (...args) => select(...args), delta => transform(delta), edit => editObject(edit),
    text => { $('drag-readout').hidden = !text; $('drag-readout').textContent = text; });
} catch {
  worker.terminate(); status('Din browser kan ikke vise 3D. Bed en voksen om hjælp.'); document.querySelectorAll('main button, main input, main select').forEach(element => { element.disabled = true; });
}
if (view) {
  // Matches the panel widths in model3d.css; on a phone the panel floats over the picture instead.
  view.insetRight = width => innerWidth <= 620 ? 0 : (innerWidth <= 850 ? 246 : innerWidth <= 1150 ? 262 : 294) + 34;
  view.resize(); view.cameraTo('home');
  select = (id, extend) => { if (rendered !== sequence || contextLost) return; model.select(id, extend); renderUI(); if (extend) manyTip(); };
  view.onHint = text => status(text);
  // A frame drawn with the left button chooses every part it touches; Shift adds them to what is chosen.
  view.onSelectMany = (ids, extend) => {
    if (rendered !== sequence || contextLost) return;
    model.selected = extend ? [...new Set([...model.selected, ...ids])] : ids.filter(id => model.data.roots.includes(id));
    renderUI(); if (model.selected.length >= 2) manyTip(); else if (!model.selected.length) status('Rammen rørte ingen figurer.');
  };
  transform = delta => { const before = sequence; mutate(() => model.applyTransform(delta)); if (sequence === before) view.cancelDrag(true); };
  editObject = edit => { const before = sequence; editSelection(edit); if (sequence === before) view.cancelDrag(true); };
  view.renderer.domElement.addEventListener('webglcontextlost', event => {
    event.preventDefault(); contextLost = true; view.cancelDrag(true); status('3D-visningen er gået tabt. Hent STL-filen, hvis knappen er tilgængelig, og genindlæs siden.'); renderUI();
  });
  function evaluate(before = null) {
    const requestId = ++sequence; previous = before; rendered = -1; failed = false; $('download').disabled = true; status(workerReady ? 'Beregner modellen …' : 'Klargør 3D-værkstedet …');
    // Each imported file goes to the worker once; later scenes only name it.
    const fresh = {}; for (const node of model.data.objects) if (node.kind === 'mesh' && !sentAssets.has(node.asset) && assets.has(node.asset)) { fresh[node.asset] = assets.get(node.asset); sentAssets.add(node.asset); }
    worker.postMessage({type: 'evaluate', requestId, scene: structuredClone(model.data), assets: fresh}); renderUI();
  }
  worker.addEventListener('message', event => {
    const message = event.data; if (message.requestId !== sequence) return;
    if (message.type === 'error') {
      if (previous !== null) {
        model.data = JSON.parse(previous.data); model.undoStack = previous.undo; model.redoStack = previous.redo; model.selected = previous.selected; queued = [];
        if (document.activeElement?.closest('#text-controls, #selection-controls')) document.activeElement.blur();
        const error = message.message || 'Modellen kunne ikke beregnes.'; evaluate(); stickyError = error + ' Den seneste ændring blev fortrudt.'; status(stickyError);
      } else { failed = true; workerReady = false; rendered = sequence; status(message.message || 'Modellen kunne ikke beregnes.'); renderUI(); }
      return;
    }
    if (message.type !== 'result') return;
    result = message; workerReady = true; rendered = sequence;

    if (!contextLost) { view.render(result, model.selected, model.data.objects); const loose = (result.looseRoots || []).filter(id => model.object(id) && !model.object(id).hidden), shown = model.data.roots.filter(id => !model.object(id)?.hidden && model.object(id)?.mode !== 'hole').length;
      // Only when some parts already hang together: at the start every part stands alone, and frames on all of them would be noise.
      view.markLoose(loose.length && loose.length < shown ? loose : []); }
    const warning = result.missedHoles?.length ? 'Et hul rører ikke din figur, så det skærer ikke noget. Fortryd, og flyt hullet ind over figuren.' : result.edgeHoles?.length ? 'Et hul går ud over kanten af figuren, så det bliver et hak og ikke et hul. Er det meningen?' : '';
    const newWarning = warning && warning !== lastWarning ? warning : ''; lastWarning = warning;
    status(contextLost ? '3D-visningen er gået tabt. Hent STL-filen, hvis knappen er tilgængelig, og genindlæs siden.' : stickyError || (newWarning || (result.missedHoles?.length && !notice ? warning : '') || notice || (result.exportBlocked ? 'Hullet er ikke skåret ud endnu. Tryk på Skær hullet ud.' : result.parts > 1 ? looseText() : result.merged ? 'Modellen er klar. Træk i en figur for at flytte den, eller træk en ramme på pladen for at vælge flere.' : 'Begynd med en form eller din egen tekst. Højre museknap drejer synet, og rullehjulet zoomer.')));
    notice = '';
    if (frameFirst && result.meshes?.length) { frameFirst = false; view.cameraTo('fit', 90); }
    // A model that grows up out of the picture is followed, but only when all of it was in view before.
    else if (!contextLost) { const now = view.modelInView(); if (seen.visible && !now.visible && now.top > seen.top + .5) view.cameraTo('fit'); }
    seen = view.modelInView();
    if (focusText) { focusText = false; const node = model.selectedObjects()[0]; if (model.selected.length === 1 && node?.kind === 'text') setTimeout(() => { $('text-value').focus(); $('text-value').select(); }); }
    renderUI();
    // Changes made while the model was being calculated run now, in order, against the new result.
    drain();
  });
  worker.addEventListener('error', () => { failed = true; workerReady = false; rendered = sequence; status('3D-værkstedet kunne ikke starte. Genindlæs siden eller bed en voksen om hjælp.'); renderUI(); });
  // The last request made while the worker is busy waits and runs again when the result arrives,
  // so it is worked out against the new model instead of being lost.
  let queued = [], panelFor = null, textPause = 0, lastColor = null, notice = '', seen = {visible: true, top: 0}, frameFirst = false, focusText = false, lastWarning = '', modelName = '';
  const assets = new Map(), sentAssets = new Set();
  const busy = () => !contextLost && !failed && rendered !== sequence;
  function later(request) { if (queued.length < 10) queued.push(request); }
  function drain() { while (queued.length && !busy()) queued.shift()(); }
  function mutate(action, history = false) {
    if (busy()) { later(() => mutate(action, history)); return; }
    if (contextLost || (failed && !history)) { status(failed ? 'Brug Fortryd, eller genindlæs siden.' : 'Vent, mens modellen beregnes.'); return; }
    stickyError = ''; const before = {data: model.snapshot(), undo: [...model.undoStack], redo: [...model.redoStack], selected: [...model.selected]}; action();
    if (before.data !== model.snapshot()) evaluate(before); else { notice = ''; renderUI(); }
  }
  function listName(id) {
    const plain = name(model.object(id)), same = model.data.roots.filter(other => model.object(other) && name(model.object(other)) === plain);
    return same.length > 1 ? `${plain} ${same.indexOf(id) + 1}` : plain;
  }
  // With several parts chosen, the status line names the two tools that work on them together.
  function manyTip() { if (model.selected.length >= 2) status('Tip: Sæt midt på (ikonet med den lille snemand) stabler figurerne. Ret ind stiller dem på linje.'); }
  function looseText() {
    const loose = (result.looseRoots || []).map(id => model.object(id)).filter(Boolean);
    const text = loose.find(node => node.kind === 'text');
    if (text) return `Teksten "${text.params.text}" rører ikke de andre figurer. Læg den på en plade, hvis den skal printes i ét stykke.`;
    const apart = loose.length >= model.data.roots.filter(id => !model.object(id)?.hidden && model.object(id)?.mode !== 'hole').length;
    if (loose.length === 1) return `${listName(loose[0].id)} rører ikke de andre figurer.${apart ? '' : ' Den har en orange ramme.'}`;
    if (loose.length > 1) return apart ? `${loose.length} figurer står hver for sig. De bliver printet som løse stykker.` : `${loose.length} figurer rører ikke de andre. De har en orange ramme og bliver printet som løse stykker.`;
    return model.data.roots.some(id => model.object(id)?.kind === 'text') ? 'Bogstaverne hænger ikke sammen. Læg teksten på en plade.' : 'Nogle dele hænger ikke sammen.';
  }
  function name(node) { return (node.kind === 'text' ? node.params.text : node.kind === 'mesh' ? node.name : labels[node.kind] || 'Figur') + (node.mode === 'hole' ? ' · hul' : ''); }
  function renderUI() {
    const selected = model.selectedObjects(), box = view.bounds(model.selected), single = selected.length === 1, state = single ? view.localState(selected[0].id) : null;
    const historyReady = rendered === sequence && !contextLost, ready = historyReady && !failed;
    view.enabled = ready; document.querySelectorAll('[data-add], #solid, #hole').forEach(element => { element.disabled = contextLost || failed; });
    // Fields stay open while the model is calculated: what the pupil types meanwhile waits in the queue, so fast typing with Tab is kept.
    const editable = !contextLost && !failed;
    document.querySelectorAll('[data-size], [data-rotation]').forEach(element => { element.readOnly = !editable || !single || !state; });
    document.querySelectorAll('[data-rotation]').forEach(element => { element.readOnly = !editable || !single || !state || state.sheared; });
    document.querySelectorAll('[data-position]').forEach(element => { element.readOnly = !editable; });
    document.querySelectorAll('#text-value, #text-height, #text-depth').forEach(element => { element.disabled = !editable; });
    if (!model.data.roots.length) modelName = '';
    $('empty').hidden = model.data.roots.length > 0; $('selection-controls').hidden = !selected.length;
    $('selection-label').textContent = single ? listName(selected[0].id) : `${selected.length} figurer valgt`;
    $('solid').setAttribute('aria-pressed', String(selected.length > 0 && selected.every(node => node.mode === 'solid')));
    $('hole').setAttribute('aria-pressed', String(selected.length > 0 && selected.every(node => node.mode === 'hole')));
    $('group').disabled = !ready || selected.length < 2 || !selected.some(node => node.mode !== 'hole'); $('center').disabled = !ready || selected.length < 2;
    $('ungroup').disabled = !ready || !single || selected[0]?.kind !== 'group' || selected[0]?.mode === 'hole';
    $('drop').disabled = !ready || !box; $('drop').hidden = !box || Math.abs(box.min.z) < .05; $('undo').disabled = !historyReady || !model.undoStack.length; $('redo').disabled = !historyReady || !model.redoStack.length;
    $('rotate-mode').disabled = !ready || !selected.length;
    // Turning ends when nothing is chosen any more, so the arcs are not waiting on the next part the pupil picks.
    if (!selected.length && $('rotate-mode').getAttribute('aria-pressed') === 'true') { $('rotate-mode').setAttribute('aria-pressed', 'false'); view.showRotate = view.showTilt = false; }
    const lining = selected.length >= 2; $('align-menu').classList.toggle('off', !lining); if (!lining) $('align-menu').open = false;
    document.querySelectorAll('[data-align]').forEach(button => { button.disabled = !ready || !lining; });
    document.querySelectorAll('[data-spread]').forEach(button => { button.disabled = !ready || selected.length < 3; button.dataset.tipOff = 'Vælg mindst tre figurer'; });
    $('select-all').disabled = !ready || !model.data.roots.length;
    $('show-hidden').disabled = !ready || !model.data.roots.some(id => model.object(id)?.hidden); $('mirror').disabled = !ready || !selected.length; $('duplicate').disabled = !ready || !selected.length; $('delete').disabled = !ready || !selected.length;
    $('download').disabled = !workerReady || rendered !== sequence || !result?.merged || result?.exportBlocked;
    $('download').dataset.tipOff = !model.data.roots.length ? 'Byg noget først' : result?.exportBlocked ? 'Skær hullet ud først' : 'Vent lidt, modellen bliver beregnet';
    $('combine-note').hidden = !result?.exportBlocked || rendered !== sequence;
    const locked = selected.length > 0 && selected.every(node => node.locked), hidden = selected.length > 0 && selected.every(node => node.hidden);
    $('lock').setAttribute('aria-pressed', String(locked)); $('lock').dataset.tip = locked ? 'Låst. Klik for at låse op' : 'Lås: figuren kan ikke flyttes, ændres eller slettes';
    $('hide').setAttribute('aria-pressed', String(hidden)); $('hide').dataset.tip = hidden ? 'Skjult. Klik for at vise den igen' : 'Skjul: figuren kommer ikke med i filen';
    $('lock').disabled = $('hide').disabled = !ready;
    if (selected.some(node => node.locked)) document.querySelectorAll('[data-size], [data-rotation], [data-position]').forEach(element => { element.readOnly = true; });
    $('rotation-controls').hidden = !single; $('multiple-hint').hidden = single;
    // Tools for several parts sit where the pupil looks after choosing them, as well as in the toolbar.
    // A new part opens with Drejning and Placering closed, so the fields stand in the same place for every part.
    if (single && selected[0].id !== panelFor) { if (panelFor) { $('rotation-controls').open = false; $('position-details').open = false; } panelFor = selected[0].id; }
    $('many-actions').hidden = selected.length < 2;
    // With several parts chosen their sizes stand on the model, so the empty size fields are left out.
    $('size-controls').hidden = selected.length > 1;
    document.querySelectorAll('[data-proxy]').forEach(button => { button.disabled = button.dataset.proxy === 'align' ? !ready : $(button.dataset.proxy).disabled; });
    const solids = selected.filter(node => node.mode !== 'hole'), shared = solids.length && solids.every(node => view.colorOf(node.id) === view.colorOf(solids[0].id)) ? view.colorOf(solids[0].id) : null;
    $('color-row').classList.toggle('muted-row', !solids.length);
    document.querySelectorAll('#color-row button').forEach(button => { button.disabled = !ready; button.setAttribute('aria-pressed', String(button.dataset.color === shared)); });
    $('text-controls').hidden = !single || selected[0].kind !== 'text';
    const sided = single && ['cylinder', 'cone'].includes(selected[0].kind); $('sides-row').hidden = !sided; $('sides').disabled = !editable || Boolean(selected[0]?.locked);
    if (sided && $('sides') !== document.activeElement) $('sides').value = selected[0].params?.sides ?? 0;
    const tube = single && selected[0].kind === 'tube'; $('wall-row').hidden = !tube; $('wall').disabled = !editable || Boolean(selected[0]?.locked);
    // Without its own wall a tube's wall is 18 % of its width, as the standard shape is drawn.
    if (tube && $('wall') !== document.activeElement) { const tubeState = view.localState(selected[0].id); $('wall').value = selected[0].params?.wall ?? (tubeState ? round(Math.min(tubeState.size[0], tubeState.size[1]) * .18) : ''); }
    $('round-row').hidden = !single || selected[0].kind !== 'box'; $('box-round').disabled = !editable || Boolean(selected[0]?.locked);
    if (single && selected[0].kind === 'box' && $('box-round') !== document.activeElement) $('box-round').value = selected[0].params?.round ?? 0;
    $('selection-hint').hidden = !state?.sheared;
    if (state?.sheared) $('selection-hint').textContent = 'Denne del har en skæv transformation fra en gruppe. Drej hele gruppen, eller fortryd gruppens skalering; formen bevares.';
    if (single && selected[0].kind === 'text') {
      const params = selected[0].params, scale = textScale(selected[0]);
      for (const [id, value] of [['text-value', params.text], ['text-height', round(params.height * scale.y)], ['text-depth', round(params.depth * scale.z)]]) if ($(id) !== document.activeElement) $(id).value = value;
    }
    if (box) {
      const center = box.getCenter(new THREE.Vector3()).toArray(); center[2] = box.min.z;
      const typing = element => element === document.activeElement && element.dataset.typing === 'true';
      document.querySelectorAll('[data-position]').forEach(element => { if (!typing(element)) element.value = round(center[Number(element.dataset.position)]); });
      document.querySelectorAll('[data-size]').forEach(element => { if (!typing(element)) element.value = state ? round(state.size[Number(element.dataset.size)]) : ''; });
      document.querySelectorAll('[data-rotation]').forEach(element => { if (!typing(element)) element.value = state ? Math.round(state.angles[Number(element.dataset.rotation)]) : ''; });
    }
    view.select(model.selected); $('part-count').textContent = model.data.roots.length;
    const list = $('object-list'), focusId = document.activeElement?.dataset?.object; list.replaceChildren();
    const counts = {}, seen = {}; for (const id of model.data.roots) { const plain = name(model.object(id)); counts[plain] = (counts[plain] || 0) + 1; }
    for (const id of model.data.roots) {
      const node = model.object(id), plain = name(node), shown = counts[plain] > 1 ? `${plain} ${seen[plain] = (seen[plain] || 0) + 1}` : plain, button = document.createElement('button'), icon = document.createElement('span'), label = document.createElement('span');
      button.dataset.object = id; icon.className = 'part-icon' + (node.mode === 'hole' ? ' hole' : node.kind === 'text' ? ' text' : ''); icon.setAttribute('aria-hidden', 'true');
      if (node.mode !== 'hole') icon.style[node.kind === 'text' ? 'color' : 'background'] = view.colorOf(id);
      if (node.hidden) button.classList.add('hidden-part'); if (node.locked) button.classList.add('locked-part');
      // Parts that do not touch the rest are marked, so the pupil can see which ones the status line means.
      const loose = rendered === sequence && result?.looseRoots?.includes(id); if (loose) { button.classList.add('loose-part'); button.dataset.tip = `${shown} rører ikke de andre figurer`; }
      if (node.kind === 'text' && node.mode !== 'hole') icon.textContent = 'Aa'; label.className = 'part-name'; label.textContent = shown + (node.hidden ? ' · skjult' : '') + (node.locked ? ' · låst' : ''); button.append(icon, label);
      button.setAttribute('aria-pressed', String(model.selected.includes(id))); if (!loose) button.title = `${shown}. Hold Shift for at vælge flere.`;
      button.onclick = event => { if (!ready) return; model.select(id, event.shiftKey); renderUI(); }; list.append(button);
      if (id === focusId) button.focus({preventScroll: true});
    }
  }
  // A copy lands beside what was copied: right, left, behind or in front, on the plate and clear of other parts.
  function copyOffset() {
    const box = model.clipboard && model.clipboard.roots.length ? model.clipboard.box : null; if (!box) return [5, 5, 0];
    const width = box.max[0] - box.min[0], depth = box.max[1] - box.min[1], half = PLATE_SIZE / 2, gap = 5;
    const others = model.data.roots.map(id => view.bounds([id])).filter(Boolean);
    for (const [dx, dy] of [[width + gap, 0], [-(width + gap), 0], [0, depth + gap], [0, -(depth + gap)]]) {
      const min = [box.min[0] + dx, box.min[1] + dy], max = [box.max[0] + dx, box.max[1] + dy];
      if (min[0] < -half || min[1] < -half || max[0] > half || max[1] > half) continue;
      if (others.some(other => min[0] < other.max.x && max[0] > other.min.x && min[1] < other.max.y && max[1] > other.min.y)) continue;
      return [round(dx), round(dy), 0];
    }
    return [5, 5, 0];
  }
  function copySelection() { const box = view.bounds(); model.copy(); if (model.clipboard && box) model.clipboard.box = {min: box.min.toArray(), max: box.max.toArray()}; }
  function move(dx, dy, dz) { if (model.selected.length) mutate(() => model.applyTransform(new THREE.Matrix4().makeTranslation(dx, dy, dz))); }
  // A new text lands on top of the selected part (sunk 0,5 mm so it fuses). Other shapes take the nearest free spot.
  // A new hole in a chosen part is about a quarter of its width, 3–10 mm: the size of a hole for a key ring or a screw.
  function placement(kind, mode = 'solid') {
    const selected = model.selectedObjects(), target = selected.length === 1 && selected[0].mode !== 'hole' ? view.bounds([selected[0].id]) : null;
    if (mode === 'hole' && target && kind !== 'text') {
      const center = target.getCenter(new THREE.Vector3()), size = target.getSize(new THREE.Vector3()), width = Math.max(3, Math.min(10, Math.round(Math.min(size.x, size.y) / 4)));
      let bottom = target.min.z;
      for (const id of model.data.roots) {
        const node = model.object(id), box = node && node.mode !== 'hole' && !node.hidden ? view.bounds([id]) : null;
        if (box && box.min.x <= center.x && box.max.x >= center.x && box.min.y <= center.y && box.max.y >= center.y && box.max.z >= target.min.z - .5) bottom = Math.min(bottom, box.min.z);
      }
      return {position: [round(center.x), round(center.y)], bottom: round(bottom), size: [width, width, round(target.max.z - bottom + 1)], inside: true};
    }
    if (kind === 'text' && target && target.max.x - target.min.x >= 25 && target.max.y - target.min.y >= 8) {
      // The default word is about 3,2 times as wide as it is high.
      const center = target.getCenter(new THREE.Vector3()), height = Math.max(4, Math.min(12, Math.round((target.max.y - target.min.y) * .55), Math.floor((target.max.x - target.min.x) * .85 / 3.2)));
      const top = view.topAt(selected[0].id, center.x, center.y) ?? target.max.z;
      return {position: [round(center.x), round(center.y)], bottom: round(top - .5), params: {height, depth: 1.5}, color: view.colorOf(selected[0].id) === COLORS[7] ? COLORS[0] : COLORS[7]};
    }
    const occupied = model.data.roots.map(id => view.bounds([id])).filter(Boolean).map(box => ({min: [box.min.x, box.min.y], max: [box.max.x, box.max.y]}));
    // A star and a heart start flat, like a charm for a key ring.
    return {position: findFreeSpot(kind === 'text' ? [40, 14] : [20, 20], occupied), size: ['star', 'heart'].includes(kind) ? [20, 20, 5] : null};
  }
  function editSelection(edit) {
    if (busy()) { later(() => editSelection(edit)); return; }
    if (edit.type === 'gap') {
      if (!model.selected.includes(edit.id) || !Number.isFinite(edit.move)) return;
      const offset = [0, 0, 0]; offset[edit.axis] = round(edit.move); notice = 'Afstanden er rettet.'; mutate(() => model.moveEach([[edit.id, offset]])); return;
    }
    if (edit.type === 'lift') {
      if (edit.ids && (edit.ids.length !== model.selected.length || edit.ids.some(id => !model.selected.includes(id)))) return;
      const box = view.bounds(); if (box) move(0, 0, edit.value - box.min.z); return;
    }
    if (model.selected.length !== 1) return; const id = model.selected[0], state = view.localState(id); if (!state || (edit.id && edit.id !== id)) return;
    if (edit.type === 'resize' && !(edit.value >= .1 && edit.value <= 1000)) { status('Målet skal være mellem 0,1 og 1000 mm.'); return; }
    if (edit.type === 'resize') {
      const step = resizeStep(state.matrix, edit.axis, edit.value, state.box, edit.pivot || null); if (!step) return;
      if (['sphere', 'halfsphere'].includes(model.object(id)?.kind)) step.factor.fill(step.factor[edit.axis]);
      else if (['cylinder', 'cone', 'tube'].includes(model.object(id)?.kind) && edit.axis < 2) { step.factor[0] = step.factor[1] = step.factor[edit.axis]; step.pivot[0] = (state.box.min[0] + state.box.max[0]) / 2; step.pivot[1] = (state.box.min[1] + state.box.max[1]) / 2; }
      const delta = scaledMatrix(state.matrix, step.factor, step.pivot).multiply(state.matrix.clone().invert()), lift = view.keepBottomShift([id], delta);
      // What stands on the part follows its top, so a stack or a name on a plate stays together.
      const riders = view.riders(id), rise = view.topShift(id, new THREE.Matrix4().makeTranslation(0, 0, lift).multiply(delta));
      mutate(() => model.scaleLocal(id, step.factor, step.pivot, lift, riders, rise));
    }
    if (edit.type === 'scale') {
      const delta = new THREE.Matrix4().makeTranslation(0, 0, edit.lift || 0).multiply(scaledMatrix(state.matrix, edit.factor, edit.pivot).multiply(state.matrix.clone().invert()));
      const riders = view.riders(id), rise = view.topShift(id, delta);
      mutate(() => model.scaleLocal(id, edit.factor, edit.pivot, edit.lift || 0, riders, rise));
    }
    if (edit.type === 'rotate') {
      if (state.sheared) { status('Denne del er skæv fra en gruppe. Drej hele gruppen, eller fortryd gruppens skalering; formen er bevaret.'); return; }
      // Typed angles turn the part about its centre; its lowest point keeps its height, like the rotation arcs.
      const delta = rotatedMatrix(state.matrix, edit.angles, state.box).multiply(state.matrix.clone().invert());
      mutate(() => model.rotate(id, edit.angles, state.box, view.keepBottomShift([id], delta)));
    }
  }
  document.querySelectorAll('[data-add]').forEach(button => {
    const mode = button.dataset.mode === 'hole' ? 'hole' : 'solid';
    button.onclick = function add() {
      if (busy()) { later(add); return; }
      const kind = button.dataset.add, options = {...placement(kind, mode), mode, ...(button.dataset.sides ? {params: {sides: Number(button.dataset.sides)}} : {})}; if (!model.data.roots.length) frameFirst = true; focusText = kind === 'text';
      if (options.inside) notice = 'Hullet står midt i figuren. Træk det derhen, hvor det skal være.';
      // A new part takes the colour the pupil chose last, unless it stands on a part and needs a contrasting colour.
      if (mode === 'solid' && !options.color && lastColor) options.color = lastColor;
      mutate(() => model.add(kind, options));
    };
    button.addEventListener('dragstart', event => { if (button.disabled) { event.preventDefault(); return; } event.dataTransfer.setData('application/x-makervaerkstedet-shape', button.dataset.add + ':' + mode + ':' + (button.dataset.sides || '')); event.dataTransfer.effectAllowed = 'copy'; });
  });
  $('workspace').addEventListener('dragover', event => { if ([...event.dataTransfer.types].some(type => type === 'application/x-makervaerkstedet-shape' || type === 'Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } });
  $('workspace').addEventListener('drop', event => {
    if (event.dataTransfer.files?.length) { event.preventDefault(); importFile(event.dataTransfer.files[0]); return; }
    const [kind, mode, sides] = event.dataTransfer.getData('application/x-makervaerkstedet-shape').split(':'), shapeParams = sides ? {sides: Number(sides)} : {}; if (!Object.hasOwn(labels, kind) || kind === 'group') return;
    event.preventDefault(); const target = view.partAt(event.clientX, event.clientY), point = target?.point || view.planePoint(event.clientX, event.clientY); if (!point) return; const step = event.altKey ? 0 : view.snap;
    const limit = PLATE_SIZE / 2 - 5, position = [point.x, point.y].map(value => Math.max(-limit, Math.min(limit, step ? Math.round(value / step) * step : value)));
    // Dropped on a part, the new shape stands on that surface; a text sinks 0,5 mm into it so they fuse.
    // On a face that does not point up, the new shape stands out from the face, like a nose on a head.
    if (target && mode !== 'hole') {
      // A shape put on another part gets a contrasting colour, so it can be seen.
      const under = view.colorOf(target.id), color = kind === 'text' ? (under === COLORS[7] ? COLORS[0] : COLORS[7]) : (under === COLORS[1] ? COLORS[4] : COLORS[1]);
      const box = view.bounds([target.id]), extent = box ? box.getSize(new THREE.Vector3()) : new THREE.Vector3(40, 40, 40);
      if (target.normal.z < .3) {
        // On a side it points straight out: the direction lies flat and snaps to whole 45° turns.
        // It gets at most half the size of the part it sits on, so a nose suits the head.
        const size = kind === 'text' ? 1 : Math.max(.2, Math.min(1, Math.round(Math.min(extent.x, extent.y, extent.z) / 2) / 20));
        const turn = Math.round(Math.atan2(target.normal.y, target.normal.x) / (Math.PI / 4)) * (Math.PI / 4), normal = new THREE.Vector3(Math.cos(turn), Math.sin(turn), 0);
        const height = kind === 'text' ? 1.5 : (['star', 'heart'].includes(kind) ? 5 : 20) * size, center = target.point.clone().addScaledVector(normal, height / 2 - .5);
        const across = new THREE.Vector3(0, 0, 1).cross(normal).normalize(), up = normal.clone().cross(across);
        const matrix = new THREE.Matrix4().makeBasis(across.multiplyScalar(size), up.multiplyScalar(size), normal.clone().multiplyScalar(size)).setPosition(center);
        // A text gets a height that fits the face: at most 55 % of the part's height and about as wide as the part.
        const textHeight = Math.max(3, Math.min(12, Math.round(extent.z * .55), Math.floor(Math.max(extent.x, extent.y) * .85 / 3.2)));
        focusText = kind === 'text'; mutate(() => model.add(kind, {matrix: matrix.toArray(), color, params: kind === 'text' ? {depth: 1.5, height: textHeight} : shapeParams})); return;
      }
      // On a top it stands upright and is never wider than the part below, so a hat does not hang over.
      const top = view.topAt(target.id, position[0], position[1]) ?? box?.max.z ?? 0, size = kind === 'text' ? 1 : Math.max(.2, Math.min(1, Math.floor(Math.min(extent.x, extent.y)) / 20));
      const height = kind === 'text' ? 1.5 : (['star', 'heart'].includes(kind) ? 5 : 20) * size, center = new THREE.Vector3(position[0], position[1], Math.max(0, top - .5) + height / 2);
      const matrix = new THREE.Matrix4().compose(center, new THREE.Quaternion(), new THREE.Vector3(size, size, size));
      const textHeight = Math.max(3, Math.min(12, Math.round(extent.y * .55), Math.floor(extent.x * .85 / 3.2)));
      focusText = kind === 'text'; mutate(() => model.add(kind, {matrix: matrix.toArray(), color, params: kind === 'text' ? {depth: 1.5, height: textHeight} : shapeParams})); return;
    }
    const top = target && mode !== 'hole' ? view.topAt(target.id, position[0], position[1]) : null, bottom = top !== null ? round(Math.max(0, top - .5)) : 0;
    focusText = kind === 'text'; mutate(() => model.add(kind, {position, bottom, mode, color: mode === 'solid' && top === null ? lastColor : null, params: top !== null && kind === 'text' ? {depth: 1.5} : shapeParams}));
  });
  COLORS.forEach((color, index) => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'swatch'; button.dataset.color = color; button.title = colorNames[index]; button.setAttribute('aria-label', colorNames[index]);
    button.style.background = color; button.onclick = () => { lastColor = color; mutate(() => model.color(color)); }; $('color-row').append(button);
  });
  document.querySelectorAll('[data-camera]').forEach(button => { button.onclick = () => view.cameraTo(button.dataset.camera); });
  function setPan(on) { view.setPanMode(on); $('pan-mode').setAttribute('aria-pressed', String(on)); if (on) status('Træk med venstre museknap for at flytte synet. Tryk på hånden igen, når du er færdig.'); }
  $('pan-mode').onclick = () => setPan($('pan-mode').getAttribute('aria-pressed') !== 'true');
  $('snap').onchange = () => { view.snap = Number($('snap').value); };
  $('undo').onclick = () => { notice = 'Fortrudt.'; mutate(() => model.undo(), true); }; $('redo').onclick = () => { notice = 'Gentaget.'; mutate(() => model.redo(), true); };
  // One button cuts every loose hole out of the parts: it melts the whole model together.
  // The largest selected part stays; the others move so their centres sit over its centre (height unchanged).
  // The largest solid part among the chosen ones stays put; the others are placed after it.
  const area = box => (box.max.x - box.min.x) * (box.max.y - box.min.y);
  function largest(boxes) { const solids = boxes.filter(([id]) => model.object(id)?.mode !== 'hole'); return (solids.length ? solids : boxes).reduce((best, item) => area(item[1]) > area(best[1]) ? item : best); }
  // Ret ind: one side (-1 start, 0 middle, 1 end) of every chosen part lines up with the same side of the largest part.
  function align(axis, side) {
    const boxes = model.selected.map(id => [id, view.bounds([id])]).filter(([, box]) => box); if (boxes.length < 2) return;
    const [anchor, reference] = largest(boxes), key = 'xyz'[axis], at = box => side < 0 ? box.min[key] : side > 0 ? box.max[key] : (box.min[key] + box.max[key]) / 2;
    const move = ([id, box]) => { const offset = [0, 0, 0]; offset[axis] = round(at(reference) - at(box)); return [id, offset]; }, apart = ([, offset]) => offset[axis] !== 0;
    const others = boxes.filter(([id]) => id !== anchor), locked = others.filter(([id]) => model.object(id)?.locked).map(move).filter(apart).length;
    const offsets = others.filter(([id]) => !model.object(id)?.locked).map(move).filter(apart);
    const all = boxes.reduce((sum, [, box]) => sum.union(box), boxes[0][1].clone()); view.showGuide(axis, at(reference), all);
    // Locked parts stay where they are, and the status line says so.
    const lockedText = locked ? ` ${locked === 1 ? 'En låst figur' : locked + ' låste figurer'} blev ikke flyttet.` : '';
    if (!offsets.length) { status(locked ? (locked === 1 ? 'Den låste figur kan ikke flyttes. Lås den op først.' : `${locked} låste figurer kan ikke flyttes. Lås dem op først.`) : 'Figurerne står allerede på linje.'); return; }
    notice = `Figurerne er rettet ind.${lockedText}`; mutate(() => model.moveEach(offsets));
  }
  document.querySelectorAll('[data-proxy]').forEach(button => { button.onclick = () => { if (button.dataset.proxy === 'align') { $('align-menu').open = true; $('align-menu').querySelector('[data-align]')?.focus(); } else $(button.dataset.proxy).click(); }; });
  // Fordel: the outermost parts stay, and those between get equal gaps along the axis.
  function spread(axis) {
    const key = 'xyz'[axis], boxes = model.selected.map(id => [id, view.bounds([id])]).filter(([, box]) => box).sort((a, b) => a[1].min[key] - b[1].min[key]);
    if (boxes.length < 3) { status('Vælg mindst tre figurer for at fordele dem.'); return; }
    const span = boxes.at(-1)[1].max[key] - boxes[0][1].min[key], used = boxes.reduce((sum, [, box]) => sum + box.max[key] - box.min[key], 0), gap = (span - used) / (boxes.length - 1);
    if (gap < 0) { status('Figurerne fylder for meget til at få mellemrum. Flyt de yderste længere fra hinanden først.'); return; }
    if (boxes.slice(1, -1).some(([id]) => model.object(id)?.locked)) { status('En låst figur står i vejen. Lås den op, så kan figurerne fordeles.'); return; }
    let at = boxes[0][1].max[key] + gap; const offsets = [];
    for (const [id, box] of boxes.slice(1, -1)) {
      const offset = [0, 0, 0]; offset[axis] = round(at - box.min[key]); at += box.max[key] - box.min[key] + gap;
      if (offset[axis] && !model.object(id)?.locked) offsets.push([id, offset]);
    }
    if (!offsets.length) { status('Figurerne har allerede lige store mellemrum.'); return; }
    notice = `Figurerne har nu ${String(round(gap)).replace('.', ',')} mm mellemrum.`; mutate(() => model.moveEach(offsets));
  }
  document.querySelectorAll('[data-spread]').forEach(button => { button.onclick = () => spread(Number(button.dataset.spread)); });
  // Pointing at a Ret ind button shows the wall the parts would line up on, before anything moves.
  document.querySelectorAll('[data-align]').forEach(button => button.addEventListener('pointerenter', () => {
    const boxes = model.selected.map(id => [id, view.bounds([id])]).filter(([, box]) => box); if (boxes.length < 2 || button.disabled) return;
    const axis = Number(button.dataset.axis), side = Number(button.dataset.align), key = 'xyz'[axis], [, reference] = largest(boxes);
    const at = side < 0 ? reference.min[key] : side > 0 ? reference.max[key] : (reference.min[key] + reference.max[key]) / 2;
    view.showGuide(axis, at, boxes.reduce((sum, [, box]) => sum.union(box), boxes[0][1].clone()));
  }));
  document.querySelectorAll('[data-align]').forEach(button => { button.onclick = () => align(Number(button.dataset.axis), Number(button.dataset.align)); });
  $('align-menu').querySelector('summary').addEventListener('click', event => { if ($('align-menu').classList.contains('off')) event.preventDefault(); });
  $('center').onclick = () => {
    const boxes = model.selected.map(id => [id, view.bounds([id])]).filter(([, box]) => box); if (boxes.length < 2) return;
    const [anchor, reference] = largest(boxes);
    const center = reference.getCenter(new THREE.Vector3()), others = boxes.filter(([id]) => id !== anchor && !model.object(id)?.locked), offsets = [];
    let top = view.topAt(anchor, center.x, center.y) ?? reference.max.z, stacked = false;
    // Larger parts go lower in the stack, like the body, head and hat of a snowman.
    others.sort((a, b) => area(b[1]) - area(a[1]));
    for (const [id, box] of others) {
      const c = box.getCenter(new THREE.Vector3()), hole = model.object(id)?.mode === 'hole';
      const dz = hole ? 0 : round(top - .5 - box.min.z);
      offsets.push([id, [round(center.x - c.x), round(center.y - c.y), dz]]);
      if (!hole) { top += box.max.z - box.min.z - .5; stacked = true; }
    }
    notice = stacked ? 'Figurerne står oven på hinanden, midt på.' : 'Hullet står midt i figuren.'; mutate(() => model.moveEach(offsets));
  };
  // Spejlvend left-right (Shift: front-back) through the middle of the selection.
  // Turning is a mode: the arcs only show while Drej is pressed, so a part is not turned a little by accident.
  function setRotate(on) { view.showRotate = view.showTilt = on; $('rotate-mode').setAttribute('aria-pressed', String(on)); view.select(model.selected); }
  $('rotate-mode').onclick = () => setRotate($('rotate-mode').getAttribute('aria-pressed') !== 'true');
  document.querySelectorAll('.more-items button').forEach(button => button.addEventListener('click', () => { button.closest('details').open = false; }));
  // An open menu closes when the pupil clicks somewhere else.
  document.addEventListener('pointerdown', event => { document.querySelectorAll('details.more-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; }); }, true);
  // Pictograms first: the words come in a short tip when the pointer rests on a button, or when the keyboard reaches it.
  const tip = document.createElement('div'); tip.className = 'tip'; tip.id = 'tip'; tip.setAttribute('role', 'tooltip'); tip.hidden = true; document.body.append(tip);
  let tipFor = null, tipTimer = 0;
  const tipped = '[data-tip], [title]:not([title=""])';
  const tipText = element => { if (element.title) { element.dataset.tip = element.title; element.removeAttribute('title'); } return element.dataset.tip; };
  function showTip(element) {
    const text = tipText(element); if (!text || !element.isConnected || element.closest('[hidden]')) return;
    const grey = element.disabled || element.closest('.off'); tip.textContent = grey && element.dataset.tipOff ? `${text}\n${element.dataset.tipOff}` : text; tip.hidden = false;
    const box = element.getBoundingClientRect(), own = tip.getBoundingClientRect();
    let top = box.bottom + 8; if (top + own.height > innerHeight - 4) top = box.top - 8 - own.height;
    tip.style.left = `${Math.max(4, Math.min(innerWidth - own.width - 4, box.left + box.width / 2 - own.width / 2))}px`; tip.style.top = `${Math.max(4, top)}px`;
  }
  function hideTip() { clearTimeout(tipTimer); tipFor = null; tip.hidden = true; }
  document.addEventListener('pointerover', event => {
    if (event.pointerType === 'touch') return;
    const element = event.target.closest?.(tipped); if (element === tipFor) return;
    hideTip(); if (!element) return; tipFor = element; tipTimer = setTimeout(() => showTip(element), 300);
  });
  document.documentElement.addEventListener('pointerleave', hideTip);
  for (const type of ['pointerdown', 'input', 'focusout', 'wheel']) document.addEventListener(type, hideTip, true);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hideTip(); });
  document.addEventListener('focusin', event => { const element = event.target.closest?.(tipped); if (element && event.target.matches(':focus-visible')) { hideTip(); tipFor = element; showTip(element); } });
  $('mirror').onclick = event => {
    const box = view.bounds(); if (!box) return; const axis = event.shiftKey ? 1 : 0;
    notice = axis ? 'Figuren er spejlvendt forfra og bagud.' : 'Figuren er spejlvendt fra venstre til højre.'; mutate(() => model.mirror(axis, box.getCenter(new THREE.Vector3()).toArray()));
  };
  // Hent en STL-fil ind som en fast figur. A model larger than the plate is shrunk to fit, keeping its shape.
  async function importStl(file) {
    if (!file) return; if (!/\.stl$/i.test(file.name)) { status('Vælg en STL-fil.'); return; }
    if (file.size > 25 * 1024 * 1024) { status('Filen er for stor. Vælg en STL-fil på højst 25 MB.'); return; }
    let info; try { info = readStl(await file.arrayBuffer()); } catch (error) { status(`Filen kunne ikke læses: ${error.message}`); return; }
    const biggest = Math.max(...info.size), fit = biggest > 170 ? 170 / biggest : 1, size = info.size.map(value => Math.max(.1, Math.round(value * fit * 100) / 100));
    assets.set(info.id, info.positions);
    const options = {...placement('mesh'), size, asset: info.id, name: file.name.replace(/\.stl$/i, '')};
    options.position = findFreeSpot([size[0], size[1]], model.data.roots.map(id => view.bounds([id])).filter(Boolean).map(box => ({min: [box.min.x, box.min.y], max: [box.max.x, box.max.y]})));
    notice = fit < 1 ? `Figuren var ${Math.round(biggest)} mm og er gjort mindre, så den passer på pladen.` : `${options.name} er hentet ind som en fast figur.`;
    if (lastColor) options.color = lastColor;
    if (!model.data.roots.length) frameFirst = true; mutate(() => model.add('mesh', options));
  }
  // Hent en SVG-tegning ind: its filled areas become a flat part, 40 mm across and 3 mm high.
  async function importSvg(file) {
    if (!file) return; if (file.size > 5 * 1024 * 1024) { status('Tegningen er for stor. Vælg en SVG-fil på højst 5 MB.'); return; }
    let info; try { info = readSvg(await file.text()); } catch (error) { status(error.message); return; }
    // With a solid part chosen, the drawing lies on top of it, 80 % of its size and sunk 0,5 mm so they fuse, like a logo on a plate.
    const chosen = model.selectedObjects(), target = chosen.length === 1 && chosen[0].mode !== 'hole' ? view.bounds([chosen[0].id]) : null;
    const across = target ? Math.min(.8 * (target.max.x - target.min.x) / info.width, .8 * (target.max.y - target.min.y) / info.height) : 40 / Math.max(info.width, info.height);
    const size = [info.width * across, info.height * across, target ? 2 : 3].map(value => Math.max(.5, Math.round(value * 100) / 100));
    assets.set(info.id, info.data);
    const options = {size, asset: info.id, name: file.name.replace(/\.svg$/i, '')};
    if (target) {
      const c = target.getCenter(new THREE.Vector3()), top = view.topAt(chosen[0].id, c.x, c.y) ?? target.max.z;
      options.position = [round(c.x), round(c.y)]; options.bottom = round(top - .5); options.color = view.colorOf(chosen[0].id) === COLORS[7] ? COLORS[0] : COLORS[7];
    } else {
      options.position = findFreeSpot([size[0], size[1]], model.data.roots.map(id => view.bounds([id])).filter(Boolean).map(box => ({min: [box.min.x, box.min.y], max: [box.max.x, box.max.y]})));
      if (lastColor) options.color = lastColor;
    }
    notice = `${options.name} er hentet ind som en flad figur, ${String(size[0]).replace('.', ',')} × ${String(size[1]).replace('.', ',')} mm${target ? ', oven på figuren' : ''}.`;
    if (!model.data.roots.length) frameFirst = true; mutate(() => model.add('mesh', options));
  }
  const importFile = file => /\.svg$/i.test(file?.name || '') || file?.type === 'image/svg+xml' ? importSvg(file) : importStl(file);
  $('import-svg').onclick = () => $('svg-file').click();
  $('svg-file').onchange = () => { importSvg($('svg-file').files[0]); $('svg-file').value = ''; };
  $('import-stl').onclick = () => $('stl-file').click();
  $('stl-file').onchange = () => { importStl($('stl-file').files[0]); $('stl-file').value = ''; };
  $('lock').onclick = () => { const lock = !model.selectedObjects().every(node => node.locked); notice = lock ? 'Figuren er låst. Den kan ikke flyttes eller ændres.' : 'Figuren er låst op.'; mutate(() => model.flag('locked', lock)); };
  $('show-hidden').onclick = () => {
    const hidden = model.data.roots.filter(id => model.object(id)?.hidden); if (!hidden.length) return;
    notice = hidden.length === 1 ? 'Den skjulte figur vises igen og er valgt.' : `${hidden.length} skjulte figurer vises igen og er valgt.`;
    mutate(() => { model.selected = hidden; model.flag('hidden', false); });
  };
  $('hide').onclick = () => { const hide = !model.selectedObjects().every(node => node.hidden); notice = hide ? 'Figuren er skjult. Vis skjulte under Mere henter den frem igen.' : 'Figuren vises igen.'; mutate(() => model.flag('hidden', hide)); };
  $('cut-holes').onclick = () => { notice = 'Hullet er skåret ud, og figurerne er nu én. Del op under Mere skiller dem ad igen.'; mutate(() => { model.selected = model.data.roots.filter(id => !model.object(id)?.hidden); model.group(); }); };
  function removeSelection() {
    if (model.selectedObjects().some(node => node.locked)) { status('En låst figur kan ikke slettes. Lås den op først.'); }
    const gone = model.selectedObjects().filter(node => !node.locked); if (!gone.length) return;
    notice = `${gone.length === 1 ? name(gone[0]) : gone.length + ' figurer'} er slettet. Tryk på Fortryd, hvis det var en fejl.`; mutate(() => model.remove());
  }
  let lastCopy = null;
  function duplicate() {
    if (busy()) { later(duplicate); return; }
    const chosen = [...model.selected], same = lastCopy && chosen.length === lastCopy.copies.length && chosen.every(id => lastCopy.copies.includes(id));
    copySelection();
    let step = null;
    if (same) {
      const source = model.object(lastCopy.sources[0]), copy = model.object(lastCopy.copies[0]);
      if (source && copy) step = objectMatrix(copy).multiply(objectMatrix(source).invert());
    }
    const offset = copyOffset();
    mutate(() => { if (step) model.pasteTransformed(step); else model.paste(offset); });
    if (model.selected.length && !model.selected.every(id => chosen.includes(id))) {
      lastCopy = {sources: chosen, copies: [...model.selected]}; if (step) notice = 'Kopien gentager det, du gjorde med den forrige kopi.';
    }
  }
  function pasteCopy() { if (busy()) { later(pasteCopy); return; } mutate(() => model.paste(copyOffset())); }
  $('duplicate').onclick = duplicate; $('delete').onclick = removeSelection;
  // A text standing on a part sinks 1 mm into it when it becomes a hole, so the letters are cut as a clear groove;
  // made solid again, it stands on the part once more. Both happen in one undo step.
  function textShifts(mode) {
    const shifts = [];
    for (const node of model.selectedObjects()) {
      if (node.kind !== 'text' || node.locked || node.mode === mode) continue;
      const box = view.bounds([node.id]); if (!box) continue; const c = box.getCenter(new THREE.Vector3());
      const under = model.data.roots.filter(id => !model.selected.includes(id) && model.object(id)?.mode !== 'hole').map(id => view.bounds([id]))
        .filter(other => other && other.min.x <= c.x && other.max.x >= c.x && other.min.y <= c.y && other.max.y >= c.y);
      // As a hole: the part the text stands on, or lies inside (made before the plate was drawn over it). Made solid: the part it was sunk into.
      const support = mode === 'hole' ? under.filter(other => other.min.z <= box.min.z + .6 && other.max.z >= box.min.z + .3).sort((a, b) => b.max.z - a.max.z)[0]
        : under.find(other => Math.abs(other.max.z - (box.min.z + 1)) < .3);
      if (support) shifts.push([node.id, [0, 0, round(mode === 'hole' ? support.max.z - 1 - box.min.z : support.max.z - .5 - box.min.z)]]);
    }
    return shifts;
  }
  function setMode(mode) {
    if (busy()) { later(() => setMode(mode)); return; }
    const shifts = textShifts(mode); if (shifts.length) notice = mode === 'hole' ? 'Teksten er sænket 1 mm ned i figuren. Skær hullet ud, så bliver bogstaverne fordybet.' : 'Teksten står på figuren igen.';
    mutate(() => { const mark = model.undoStack.length; model.mode(mode); if (shifts.length) model.moveEach(shifts); model.undoStack.splice(mark + 1); });
  }
  $('solid').onclick = () => setMode('solid'); $('hole').onclick = () => setMode('hole'); $('group').onclick = () => { notice = 'Figurerne er smeltet sammen.'; mutate(() => model.group()); }; $('ungroup').onclick = () => mutate(() => model.ungroup());
  $('drop').onclick = () => { const box = view.bounds(); if (box) move(0, 0, -box.min.z); };
  document.querySelectorAll('[data-position], [data-size], [data-rotation]').forEach(input => input.addEventListener('change', function changed() {
    if (busy()) { const value = input.value; later(() => { input.value = value; changed(); }); return; }
    const box = view.bounds(), value = Number(input.value); if (!box || input.value.trim() === '' || !Number.isFinite(value)) { invalid(input, 'Skriv et tal.'); return; }
    if (input.dataset.position !== undefined) {
      const axis = Number(input.dataset.position), center = box.getCenter(new THREE.Vector3()).toArray(); center[2] = box.min.z; const delta = [0, 0, 0]; delta[axis] = value - center[axis]; move(...delta);
    } else if (input.dataset.size !== undefined) {
      if (value < .1 || value > PLATE_SIZE) { invalid(input, value > PLATE_SIZE ? `Det tal er for stort. Skriv højst ${PLATE_SIZE} mm, så passer det på printeren.` : 'Det tal er for lille. Skriv mindst 0,1 mm.'); return; }
      editSelection({type: 'resize', axis: Number(input.dataset.size), value});
    } else { const state = view.localState(); if (!state) return; const angles = [...state.angles]; angles[Number(input.dataset.rotation)] = value; editSelection({type: 'rotate', angles}); }
  }));
  // Every click selects the whole number or word, so typing replaces it instead of making "204".
  document.querySelectorAll('.fields input').forEach(input => { input.addEventListener('input', () => { input.dataset.typing = 'true'; }); input.addEventListener('change', () => { input.dataset.typing = 'false'; }); input.addEventListener('blur', () => { input.dataset.typing = 'false'; }); });
  // The whole value is selected on focus and on click, but never once the pupil has started typing.
  document.querySelectorAll('.fields input, #text-value, #text-height, #text-depth').forEach(input => {
    let typed = false;
    input.addEventListener('focus', () => { typed = false; setTimeout(() => { if (document.activeElement === input && !typed) input.select(); }); });
    input.addEventListener('beforeinput', () => { typed = true; });
    input.addEventListener('mouseup', event => { if (!typed) { event.preventDefault(); input.select(); } });
    input.addEventListener('blur', () => { typed = false; });
  });
  // A wrong number gets a red frame right at the field and a plain message, then the field shows the model again.
  function invalid(input, message) {
    status(message); input.setAttribute('aria-invalid', 'true');
    setTimeout(() => { input.removeAttribute('aria-invalid'); }, 2500); input.blur(); renderUI(); input.focus(); input.select();
  }
  $('box-round').addEventListener('change', function rounded() {
    if (busy()) { const value = $('box-round').value; later(() => { $('box-round').value = value; rounded(); }); return; }
    const node = model.selectedObjects()[0], value = Number($('box-round').value); if (model.selected.length !== 1 || node?.kind !== 'box') return;
    const state = view.localState(node.id), most = state ? Math.min(state.size[0], state.size[1]) / 2 : 90;
    if (!Number.isFinite(value) || value < 0) { invalid($('box-round'), 'Skriv et tal fra 0 og op.'); return; }
    const radius = round(Math.min(value, most)); $('box-round').value = radius; if (radius < value) status(`Hjørnerne kan højst være ${String(radius).replace('.', ',')} mm runde på denne kasse.`);
    mutate(() => model.setRound(node.id, radius));
  });
  $('sides').addEventListener('change', function sided() {
    if (busy()) { const value = $('sides').value; later(() => { $('sides').value = value; sided(); }); return; }
    const node = model.selectedObjects()[0], value = Number($('sides').value); if (model.selected.length !== 1 || !['cylinder', 'cone'].includes(node?.kind)) return;
    if (!Number.isInteger(value) || value < 0 || value > 64 || value === 1 || value === 2) { invalid($('sides'), 'Skriv 0 for rund, eller et helt tal fra 3 til 64.'); return; }
    mutate(() => model.setSides(node.id, value));
  });
  $('wall').addEventListener('change', function walled() {
    if (busy()) { const value = $('wall').value; later(() => { $('wall').value = value; walled(); }); return; }
    const node = model.selectedObjects()[0], value = Number($('wall').value); if (model.selected.length !== 1 || node?.kind !== 'tube') return;
    const state = view.localState(node.id), most = state ? Math.min(state.size[0], state.size[1]) / 2 - .5 : 40;
    if (!Number.isFinite(value) || value < 0) { invalid($('wall'), 'Skriv et tal fra 0 og op. 0 giver den almindelige væg.'); return; }
    const wall = value ? round(Math.max(.4, Math.min(value, most))) : 0; if (wall && wall !== value) status(`Væggen er sat til ${String(wall).replace('.', ',')} mm, så der stadig er et hul i røret.`);
    mutate(() => model.setWall(node.id, wall));
  });
  function updateText() {
    clearTimeout(textPause);
    if (busy()) { const typed = ['text-value', 'text-height', 'text-depth'].map(id => $(id).value); later(() => { ['text-value', 'text-height', 'text-depth'].forEach((id, i) => { if ($(id) !== document.activeElement) $(id).value = typed[i]; }); updateText(); }); return; }
    const node = model.selectedObjects()[0]; if (model.selected.length !== 1 || node?.kind !== 'text') return;
    const text = $('text-value').value, height = Number($('text-height').value), depth = Number($('text-depth').value);
    if (!text.trim() || [...text].length > 40 || !Number.isFinite(height) || height < .1 || height > 150 || !Number.isFinite(depth) || depth < .1 || depth > 30) {
      status('Skriv 1–40 tegn, en teksthøjde på 0,1–150 mm og en tykkelse på 0,1–30 mm.'); renderUI(); return;
    }
    // The fields show the visible size, so typed values are divided by any stretch before they become parameters.
    const scale = textScale(node), visible = value => round(value);
    const params = {text, height: visible(height) === visible(node.params.height * scale.y) ? node.params.height : height / scale.y,
      depth: visible(depth) === visible(node.params.depth * scale.z) ? node.params.depth : depth / scale.z};
    if (params.height > 350 || params.depth > 350) { status('Teksten er strakt så meget, at den ikke kan blive så stor. Skriv et mindre tal.'); renderUI(); return; }
    mutate(() => model.setText(node.id, params));
  }
  for (const id of ['text-value', 'text-height', 'text-depth']) $(id).addEventListener('change', updateText);
  $('text-value').addEventListener('input', () => { clearTimeout(textPause); textPause = setTimeout(() => { if (document.activeElement === $('text-value') && $('text-value').value.trim()) updateText(); }, 600); });
  $('text-value').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); updateText(); } });
  // Before the file is made, the pupil sees what a printer would have trouble with, and a button that fixes it where possible.
  const mm = value => String(round(value)).replace('.', ',');
  function printIssues() {
    const issues = [], min = result.bounds.min, max = result.bounds.max, size = max.map((value, i) => value - min[i]), half = PLATE_SIZE / 2;
    const everything = delta => mutate(() => model.translateAll(delta));
    if (min[2] > .05) issues.push({text: `Modellen svæver ${mm(min[2])} mm over pladen. Printeren kan ikke bygge i luften.`, fix: 'Læg modellen på pladen', action: () => everything(new THREE.Matrix4().makeTranslation(0, 0, -min[2]))});
    if (min[2] < -.05) issues.push({text: 'En del af modellen er under pladen.', fix: 'Løft modellen op på pladen', action: () => everything(new THREE.Matrix4().makeTranslation(0, 0, -min[2]))});
    if (size[0] > PLATE_SIZE || size[1] > PLATE_SIZE) issues.push({text: `Modellen er ${mm(size[0])} × ${mm(size[1])} mm og større end pladen på ${PLATE_SIZE} × ${PLATE_SIZE} mm. Gør den mindre.`});
    else if (min[0] < -half || max[0] > half || min[1] < -half || max[1] > half) issues.push({text: 'Modellen går ud over kanten af pladen.', fix: 'Flyt modellen ind på pladen', action: () => everything(new THREE.Matrix4().makeTranslation(-(min[0] + max[0]) / 2, -(min[1] + max[1]) / 2, 0))});
    if (size[2] > PLATE_SIZE) issues.push({text: `Modellen er ${mm(size[2])} mm høj. Det er højere, end printerne kan lave (${PLATE_SIZE} mm).`});
    if (result.missedHoles?.length) issues.push({text: 'Et hul rører ikke din figur, så det skærer ikke noget. Fortryd, og flyt hullet ind over figuren.'});
    const unseen = model.data.roots.filter(id => model.object(id)?.hidden).length;
    if (unseen) issues.push({text: `${unseen === 1 ? 'En skjult figur' : unseen + ' skjulte figurer'} kommer ikke med i filen.`});
    if (result.edgeHoles?.length) issues.push({text: 'Et hul går ud over kanten af figuren. Det bliver et hak og ikke et hul.'});
    if (result.parts > 1) issues.push({text: looseText()});
    if (Math.min(...size) < .8) issues.push({text: 'Modellen er tyndere end 0,8 mm. Den kan blive for svag eller forsvinde i printet.'});
    else {
      // A single thin part on a thicker model, such as a name only 0,4 mm thick on a plate, gets its own line and a fix.
      let shown = 0;
      for (const id of model.data.roots) {
        const node = model.object(id), state = node && node.mode !== 'hole' && !node.hidden && !node.locked ? view.localState(id) : null; if (!state || shown >= 3) continue;
        const axis = [0, 1, 2].reduce((best, i) => state.size[i] < state.size[best] ? i : best, 0); if (!(state.size[axis] < .8)) continue;
        shown++; issues.push({text: `${listName(id)} er kun ${mm(state.size[axis])} mm ${axis === 2 ? 'tyk' : 'bred'}. Den kan forsvinde i printet.`, fix: 'Gør den 1 mm', action: () => {
          model.selected = [id]; renderUI(); const b = state.box, factor = [1, 1, 1]; factor[axis] = 1 / state.size[axis];
          editSelection({type: 'scale', id, factor, pivot: [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, b.min[2]], lift: 0});
        }});
      }
    }
    // A text standing on a part but wider or deeper than that part hangs over the edge, and the letters there have nothing under them.
    for (const id of model.data.roots) {
      const node = model.object(id); if (node?.kind !== 'text' || node.mode === 'hole' || node.hidden || node.locked) continue;
      const box = view.bounds([id]); if (!box) continue; const c = box.getCenter(new THREE.Vector3());
      const support = model.data.roots.filter(other => other !== id && model.object(other)?.mode !== 'hole' && !model.object(other)?.hidden).map(other => view.bounds([other]))
        .find(other => other && other.min.x <= c.x && other.max.x >= c.x && other.min.y <= c.y && other.max.y >= c.y && Math.abs(other.max.z - (box.min.z + .5)) < .6);
      if (!support || (box.min.x >= support.min.x - .5 && box.max.x <= support.max.x + .5 && box.min.y >= support.min.y - .5 && box.max.y <= support.max.y + .5)) continue;
      const fit = Math.min(1, .9 * (support.max.x - support.min.x) / (box.max.x - box.min.x), .9 * (support.max.y - support.min.y) / (box.max.y - box.min.y));
      const middle = support.getCenter(new THREE.Vector3()), shift = [round(middle.x - c.x), round(middle.y - c.y), 0];
      issues.push({text: `Teksten "${node.params.text}" stikker ud over kanten af figuren under den.`, fix: fit < .999 ? 'Gør teksten mindre' : 'Flyt teksten ind', action: () => {
        const state = view.localState(id); if (!state) return; model.selected = [id]; renderUI(); const b = state.box;
        if (fit < .999) editSelection({type: 'scale', id, factor: [fit, fit, 1], pivot: [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, b.min[2]], lift: 0});
        // Scaled about its own centre, the text keeps that centre; then it moves to the middle of its part.
        if (shift[0] || shift[1]) mutate(() => model.moveEach([[id, shift]]));
      }});
    }
    return issues;
  }
  function fileName() {
    const text = model.data.objects.find(node => node.kind === 'text' && !node.hidden && node.mode !== 'hole')?.params.text || modelName;
    const clean = text.normalize('NFC').replace(/[^\p{L}\p{N} _-]/gu, '').trim().replace(/\s+/g, '-').slice(0, 40);
    return (clean || 'min-model') + '.stl';
  }
  function saveStl() {
    try { const buffer = exportBinaryStl(result), info = validateSTL(buffer), blob = new Blob([buffer], {type: 'model/stl'}), url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = fileName(); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); status(`Din fil er hentet: ${link.download} (${info.size.map(mm).join(' × ')} mm).`); }
    catch (error) { status('STL kunne ikke hentes: ' + error.message); }
  }
  $('download').onclick = () => {
    if (!result?.merged || result.exportBlocked || rendered !== sequence) return;
    if (!modelName && !model.data.objects.some(node => node.kind === 'text' && !node.hidden && node.mode !== 'hole')) {
      // A word written in the model, such as the pupil's name on a tag, is offered as the file name; Enter takes it.
      const words = model.data.objects.filter(node => node.kind === 'text' && node.params?.text?.trim()).map(node => node.params.text.trim());
      $('model-name').value = words.length ? words[0].slice(0, 40) : ''; $('name-dialog').showModal(); $('model-name').focus(); $('model-name').select(); return;
    }
    checkAndSave();
  };
  $('name-form').addEventListener('submit', event => {
    event.preventDefault(); modelName = $('model-name').value.trim().slice(0, 40) || 'min-model'; $('name-dialog').close(); checkAndSave();
  });
  $('name-cancel').onclick = () => $('name-dialog').close();
  function checkAndSave() {
    const issues = printIssues(); if (!issues.length) { saveStl(); return; }
    const list = $('print-issues'), actions = $('print-actions'); list.replaceChildren(); actions.replaceChildren();
    for (const issue of issues) {
      const item = document.createElement('li'), text = document.createElement('p'); text.textContent = issue.text; item.append(text);
      if (issue.action) { const fix = document.createElement('button'); fix.type = 'button'; fix.textContent = issue.fix; fix.onclick = () => { $('print-check').close(); issue.action(); }; item.append(fix); }
      list.append(item);
    }
    const anyway = document.createElement('button'), back = document.createElement('button'); anyway.type = back.type = 'button';
    anyway.textContent = 'Hent STL alligevel'; back.textContent = 'Tilbage til modellen'; back.className = 'primary';
    anyway.onclick = () => { $('print-check').close(); saveStl(); }; back.onclick = () => $('print-check').close(); actions.append(anyway, back);
    $('print-check').showModal(); back.focus();
  }
  // Idékort: a small step panel points at the button to use next. It never builds anything by itself.
  let guide = null, guideStep = 0;
  function guideContext() {
    const parts = model.data.roots.map(id => model.object(id)).filter(node => node && !node.hidden).map(node => ({kind: node.kind, mode: node.mode, size: view.localState(node.id)?.size || [0, 0, 0]}));
    return {parts, selected: model.selected.length, count: (kind, mode = 'solid') => parts.filter(part => part.kind === kind && part.mode === mode).length};
  }
  let guideNudged = false, guideTimer = null;
  function pointGuide() {
    const step = guide?.steps[guideStep], target = step?.target ? document.querySelector(step.target) : null;
    document.querySelectorAll('.guide-target').forEach(element => { if (element !== target) element.classList.remove('guide-target'); });
    target?.classList.add('guide-target');
    if (step) { const done = Boolean(step.done?.(guideContext())); $('guide').classList.toggle('done', done); if (done) $('guide-nudge').hidden = true; }
  }
  function showGuide() {
    document.querySelectorAll('.guide-target').forEach(element => element.classList.remove('guide-target'));
    const was = view.insetLeft || 0; view.insetLeft = guide ? 330 : 0; if (was !== view.insetLeft) { view.resize(); view.cameraTo(model.data.roots.length ? 'fit' : 'home'); }
    clearInterval(guideTimer); guideTimer = guide ? setInterval(pointGuide, 400) : null; guideNudged = false; $('guide-nudge').hidden = true;
    $('guide').hidden = !guide; if (!guide) return;
    const step = guide.steps[guideStep];
    $('guide-name').textContent = guide.title; $('guide-count').textContent = `Trin ${guideStep + 1} af ${guide.steps.length}`;
    $('guide-title').textContent = step.title; $('guide-text').textContent = step.text;
    $('guide-hint').textContent = step.hint || ''; $('guide-hint').hidden = !step.hint; $('guide-check').textContent = step.check || ''; $('guide-check').hidden = !step.check;
    $('guide-back').disabled = guideStep === 0; $('guide-next').textContent = guideStep === guide.steps.length - 1 ? 'Færdig' : 'Næste';
    pointGuide();
  }
  for (const item of GUIDES) {
    const card = document.createElement('button'), title = document.createElement('strong'), intro = document.createElement('span'), meta = document.createElement('small');
    card.type = 'button'; card.className = 'idea-card'; card.dataset.guide = item.id; title.textContent = item.title; intro.textContent = item.intro; meta.textContent = `${item.steps.length} trin · ca. ${item.minutes} min`;
    card.append(title, intro, meta); card.onclick = () => { $('ideas-dialog').close(); startGuide(item); }; $('idea-cards').append(card);
  }
  // "Øv dig" is the one other way in: it opens the exercises. The workshop itself always starts the same way.
  $('ideas').onclick = () => $('ideas-dialog').showModal();
  $('guide-close').onclick = () => { guide = null; showGuide(); };
  $('guide-back').onclick = () => { guideStep = Math.max(0, guideStep - 1); showGuide(); };
  $('guide-next').onclick = () => {
    // Pressing Next before a step is done gives one friendly reminder; a second press moves on anyway.
    const step = guide.steps[guideStep];
    if (step.done && !step.done(guideContext()) && !guideNudged) { guideNudged = true; $('guide-nudge').hidden = false; return; }
    if (guideStep < guide.steps.length - 1) { guideStep++; showGuide(); } else { guide = null; showGuide(); status('Godt gået! Prøv en ny idé, eller byg videre på din egen.'); }
  };
  // A new idea starts on an empty plate if the pupil wants; building on keeps the parts.
  let pendingGuide = null;
  function startGuide(item) {
    if (model.data.roots.length) { pendingGuide = item; $('clear-dialog').showModal(); return; }
    guide = item; guideStep = 0; showGuide();
  }
  $('clear-yes').onclick = () => { $('clear-dialog').close(); notice = 'Pladen er tom. Fortryd henter dine figurer tilbage.'; mutate(() => { model.selected = [...model.data.roots]; model.remove(); }); guide = pendingGuide; guideStep = 0; showGuide(); };
  $('clear-no').onclick = () => { $('clear-dialog').close(); guide = pendingGuide; guideStep = 0; showGuide(); };
  $('select-all').onclick = () => { model.selected = model.data.roots.filter(id => !model.object(id)?.hidden); renderUI(); manyTip(); };
  $('help-open').onclick = () => { $('help').showModal(); $('help').scrollTop = 0; $('help').querySelector('h2').focus(); };
  document.addEventListener('keydown', event => {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.closest('dialog')) return;
    if (event.key === 'Escape' && $('pan-mode').getAttribute('aria-pressed') === 'true') { setPan(false); return; }
    if (event.key === 'Escape') { const dragging = Boolean(view.drag?.active); view.cancelDrag(true); if (!dragging && model.selected.length) { model.selected = []; renderUI(); } return; }
    if (view.drag || view.pendingPreview) return;
    const command = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
    if (command && ['z', 'y', 'd', 'c', 'v', 'x', 'g', 'a'].includes(key)) {
      event.preventDefault(); if (key === 'z') mutate(() => event.shiftKey ? model.redo() : model.undo(), true); if (key === 'y') mutate(() => model.redo(), true);
      if (key === 'd') duplicate(); if (key === 'c') copySelection(); if (key === 'v') pasteCopy(); if (key === 'x') { copySelection(); mutate(() => model.remove()); }
      if (key === 'g') mutate(() => model.group()); if (key === 'a' && !$('select-all').disabled) $('select-all').click(); return;
    }
    if (event.key === 'Delete') { event.preventDefault(); removeSelection(); }
    if (event.key.toLowerCase() === 'r' && !event.altKey && model.selected.length) { event.preventDefault(); setRotate($('rotate-mode').getAttribute('aria-pressed') !== 'true'); return; }
    const step = event.shiftKey ? 10 : 1, {right, away} = view.screenDirections();
    const arrows = {ArrowLeft: [-right[0] * step, -right[1] * step, 0], ArrowRight: [right[0] * step, right[1] * step, 0], ArrowUp: [away[0] * step, away[1] * step, 0], ArrowDown: [-away[0] * step, -away[1] * step, 0], PageUp: [0, 0, step], PageDown: [0, 0, -step]};
    if (arrows[event.key] && model.selected.length) { event.preventDefault(); move(...arrows[event.key]); }
  });
  window.addEventListener('beforeunload', event => { if (model.dirty) { event.preventDefault(); event.returnValue = ''; } });
  evaluate();
}
