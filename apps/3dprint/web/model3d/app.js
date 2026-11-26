import * as THREE from 'three';
import {SceneModel, findFreeSpot, hasShear, isConnector, objectMatrix, rotatedMatrix, resizeStep, scaledMatrix, uprightCylinderSection, PLATE_SIZE, COLORS} from './scene.js';
import {View} from './view.js';
import {validateSTL} from '../../../../sdk/web/stl-validation.mjs';
import {exportBinaryStl} from './geometry.js';
import {GUIDES} from './guides.js';
import {readStl, stlAssetId} from './stl-import.js';
import {export3mf, MAX_3MF_BYTES, read3mf} from './three-mf.js';
import {readSvg} from './svg-import.js';
import {faceHolePlacement} from './face-hole.js';
import {SHAPE_LIBRARY, insertShapePreset} from './shape-library.js';
import {CATALOG_RECIPES} from './catalog-recipes.js';
import {buildConnector} from './connector-profiles.js';
import {connectorMount} from './connector-mount.js';
import {catalogChildren} from './catalog-children.js';
import {NATIVE_PROFILES, validProfile} from './native-profiles.js';
import {setupMechanicalEditors, setupShapeLibrary} from './shape-library-ui.js';
import {defaultMechanicalPart} from './mechanical-parts.js';
import {findLegacyGamePieces, gamePieceChildren, setupGamePiece} from './game-piece.js';
import {encodeProject, decodeProject, MAX_PROJECT_OBJECTS} from './project.js';
import {DEFAULT_FONT, TEXT_FONTS, FONT_BY_KEY} from './fonts.js';
import {mountDiameter} from './diameter.js';
import {findHoleCopyOffset, fitsInside, repeatCopyMatrix, setupCopyRow} from './copy-row.js';
const $ = id => document.getElementById(id), model = new SceneModel();
const managed = Boolean(window.ElevhubApp || document.getElementById('elevhub'));
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
  const message = 'Din browser kan ikke vise 3D. Bed en voksen om hjælp.';
  if (managed) window.ElevhubProjectStartupError = message;
  worker.terminate(); status(message); document.querySelectorAll('main button, main input, main select, #game-piece-open, #import-stl, #import-svg, #import-3mf').forEach(element => { element.disabled = true; });
}
if (view) {
  view.resize(); view.cameraTo('home');
  const textFont = $('text-font');
  textFont.replaceChildren(...TEXT_FONTS.map(({key, name, style}) => new Option(`${style} · ${name}`, key)));
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
    // A newly calculated shape needs a fresh cut confirmation. The measuring reference is tracked separately.
    cutChoiceKey = '';
    const requestId = ++sequence; previous = before; rendered = -1; failed = false; $('download').disabled = true; status(workerReady ? 'Beregner modellen …' : 'Klargør 3D-værkstedet …');
    // Each imported file goes to the worker once; later scenes only name it.
    const fresh = {}; for (const node of model.data.objects) if (node.kind === 'mesh' && !sentAssets.has(node.asset) && assets.has(node.asset)) { fresh[node.asset] = assets.get(node.asset); sentAssets.add(node.asset); }
    worker.postMessage({type: 'evaluate', requestId, scene: structuredClone(model.data), assets: fresh}); renderUI();
  }
  worker.addEventListener('message', event => {
    const message = event.data; if (message.requestId !== sequence) return;
    if (message.type === 'error') {
      if (previous !== null) {
        model.data = JSON.parse(previous.data); model.undoStack = previous.undo; model.redoStack = previous.redo; model.selected = previous.selected; model.dirty = previous.dirty; queued = [];
        for (const id of previous.newAssets || []) { assets.delete(id); sentAssets.delete(id); }
        const focused = document.activeElement;
        if (focused?.closest('#text-controls, #selection-controls') && !focused.closest('#game-piece-editor')) focused.blur();
        const error = previous.imported3mf && message.code === 'stl_ikke_lukket'
          ? '3MF-modellen skal bestå af lukkede figurer.' : (message.message || 'Modellen kunne ikke beregnes.');
        evaluate(); stickyError = error + ' Den seneste ændring blev fortrudt.'; status(stickyError);
      } else { failed = true; workerReady = false; rendered = sequence; status(message.message || 'Modellen kunne ikke beregnes.'); renderUI(); }
      return;
    }
    if (message.type !== 'result') return;
    result = message; workerReady = true; rendered = sequence;

    if (!contextLost) { view.render(result, model.selected, model.data.objects); const loose = (result.looseRoots || []).filter(id => model.object(id) && !model.object(id).hidden), shown = model.data.roots.filter(id => !model.object(id)?.hidden && model.object(id)?.mode !== 'hole').length;
      // Only when some parts already hang together: at the start every part stands alone, and frames on all of them would be noise.
      view.markLoose(loose.length && loose.length < shown ? loose : []); }
    const warning = result.missedHoles?.length ? 'Et hul rører ikke din figur, så det skærer ikke noget. Fortryd, og flyt hullet ind over figuren.' : result.edgeHoles?.length ? 'Et hul går ud over kanten af figuren, så det bliver et hak og ikke et hul. Er det meningen?' : '';
    const newWarning = warning && warning !== lastWarning && !(noticeExplainsMiss && result.missedHoles?.length) ? warning : ''; lastWarning = warning;
    const emptyStatus = model.data.roots.length ? 'Ingen synlige figurer på pladen.' : 'Pladen er tom.';
    status(contextLost ? '3D-visningen er gået tabt. Hent STL-filen, hvis knappen er tilgængelig, og genindlæs siden.' : stickyError || (newWarning || (result.missedHoles?.length && !notice ? warning : '') || notice || (result.exportBlocked ? 'Hullet er ikke skåret ud endnu. Se muligheden over pladen.' : result.parts > 1 ? looseText() : result.merged ? 'Modellen er klar. Træk i en figur for at flytte den, eller træk en ramme på pladen for at vælge flere.' : emptyStatus)));
    notice = ''; noticeExplainsMiss = false;
    if (frameFirst && result.meshes?.length) { frameFirst = false; view.cameraTo('fit', 90); }
    // A model that grows up out of the picture is followed, but only when all of it was in view before.
    else if (!contextLost) { const now = view.modelInView(); if (seen.visible && !now.visible && now.top > seen.top + .5) view.cameraTo('fit'); }
    seen = view.modelInView();
    if (focusText) { focusText = false; const node = model.selectedObjects()[0]; if (model.selected.length === 1 && node?.kind === 'text') { panelFolded = false; setTimeout(() => { $('text-value').focus(); $('text-value').select(); }); } }
    renderUI();
    // Changes made while the model was being calculated run now, in order, against the new result.
    drain();
  });
  worker.addEventListener('error', () => { failed = true; workerReady = false; rendered = sequence; status('3D-værkstedet kunne ikke starte. Genindlæs siden eller bed en voksen om hjælp.'); renderUI(); });
  // The last request made while the worker is busy waits and runs again when the result arrives,
  // so it is worked out against the new model instead of being lost.
  let queued = [], panelFor = null, textPause = 0, lastColor = null, notice = '', seen = {visible: true, top: 0}, frameFirst = false, focusText = false, lastWarning = '', modelName = '';
  let panelFolded = false, fieldError = null, positionPreference = null;
  let workplane = null;
  let noticeExplainsMiss = false;
  const assets = new Map(), sentAssets = new Set();
  function projectAssets() {
    const ids = new Set(model.data.objects.filter(node => node.kind === 'mesh').map(node => node.asset));
    return new Map([...assets].filter(([id]) => ids.has(id)));
  }
  const legacyPieces = new WeakMap();
  const openedHolePositions = new Map(), measuredPlates = new Map();
  const busy = () => !contextLost && !failed && rendered !== sequence;
  function later(request) { if (queued.length < 10) queued.push(request); }
  function drain() { while (queued.length && !busy()) queued.shift()(); }
  function mutate(action, history = false) {
    if (busy()) { later(() => mutate(action, history)); return; }
    if (contextLost || (failed && !history)) { status(failed ? 'Brug Fortryd, eller genindlæs siden.' : 'Vent, mens modellen beregnes.'); return; }
    stickyError = ''; const before = {data: model.snapshot(), undo: [...model.undoStack], redo: [...model.redoStack], selected: [...model.selected], dirty: model.dirty}; action(before);
    if (before.data !== model.snapshot()) evaluate(before); else { notice = ''; noticeExplainsMiss = false; renderUI(); }
  }
  const diameter = mountDiameter({field: $('size-controls'), model, view, busy, later, mutate, invalid, historyStep, renderUI});
  const updateCopyRow = setupCopyRow({
    model, getBounds: () => view.bounds(), getHoleContext: id => holeCopyContext([id]),
    onCopy: (action, message) => mutate(() => { action(); notice = message; }),
  });
  function listNames() {
    const nodes = new Map(model.data.objects.map(node => [node.id, node]));
    const roots = model.data.roots.map(id => [id, name(nodes.get(id))]);
    const counts = new Map(), next = new Map();
    for (const [, plain] of roots) counts.set(plain, (counts.get(plain) || 0) + 1);
    const used = new Set(counts.keys());
    return new Map(roots.map(([id, plain]) => {
      if (counts.get(plain) === 1) return [id, plain];
      let number = next.get(plain) || 1, label;
      do { label = `${plain} ${number++}`; } while (used.has(label));
      next.set(plain, number); used.add(label);
      return [id, label];
    }));
  }
  const listName = id => listNames().get(id);
  // With several parts chosen, the status line names the two tools that work on them together.
  function manyTip() { if (model.selected.length >= 2) status('Sæt midt på stabler figurerne. Ret ind stiller dem på linje.'); }
  function looseText() {
    const loose = (result.looseRoots || []).map(id => model.object(id)).filter(Boolean);
    const text = loose.find(node => node.kind === 'text');
    if (text) return `Teksten "${text.params.text}" rører ikke de andre figurer. Læg den på en plade, hvis den skal printes i ét stykke.`;
    const apart = loose.length >= model.data.roots.filter(id => !model.object(id)?.hidden && model.object(id)?.mode !== 'hole').length;
    if (loose.length === 1) return `${listName(loose[0].id)} rører ikke de andre figurer.${apart ? '' : ' Den har en orange ramme.'}`;
    if (loose.length > 1) return apart ? `${loose.length} figurer står hver for sig. De bliver printet som løse stykker.` : `${loose.length} figurer rører ikke de andre. De har en orange ramme og bliver printet som løse stykker.`;
    return model.data.roots.some(id => model.object(id)?.kind === 'text') ? 'Bogstaverne hænger ikke sammen. Læg teksten på en plade.' : 'Nogle dele hænger ikke sammen.';
  }
  function isRoundHole(node, matrix = null) {
    if (node?.mode !== 'hole' || node.kind !== 'cylinder' || node.params?.sides) return false;
    matrix ??= objectMatrix(node);
    const scale = new THREE.Vector3().setFromMatrixScale(matrix);
    return !hasShear(matrix) && Math.abs(node.size[0] * scale.x - node.size[1] * scale.y) < .001;
  }
  function name(node) {
    if (!node.name && node.mode === 'hole') {
      const section = uprightCylinderSection(node);
      if (section) return section.diameter === null ? 'Ovalt hul' : 'Rundt hul';
      if (isRoundHole(node)) return 'Rundt hul';
    }
    return (node.kind === 'text' ? node.params.text : node.name || labels[node.kind] || 'Figur') + (node.mode === 'hole' ? ' · hul' : '');
  }
  const cutLabel = document.createElement('label'), cutTarget = document.createElement('select');
  cutLabel.className = cutLabel.id = 'cut-targets'; cutLabel.hidden = true;
  cutTarget.id = 'cut-target'; cutTarget.setAttribute('aria-label', 'Skær i');
  cutTarget.setAttribute('aria-describedby', 'cut-explanation');
  cutLabel.append('Skær i', cutTarget); $('cut-holes').before(cutLabel);
  let cutChoiceKey = '';
  const allTargets = '*';
  const holeTargets = id => (result?.holeTargets?.[id] || []).filter(target => {
    const node = model.object(target);
    return node && !node.hidden && node.mode !== 'hole' && model.data.roots.includes(target);
  });
  function cutPlan(selected, names = listNames()) {
    const visible = selected.filter(node => !node.hidden);
    const loose = result?.uncombinedHoles || [];
    const picked = visible.filter(node => node.mode === 'hole').map(node => node.id);
    const holes = picked.length ? picked : loose.length === 1 ? loose : [];
    if (!holes.length) return {hint: 'Vælg et hul på pladen eller i Dele.'};
    const cutLabel = holes.length === 1 ? 'Skær hullet ud' : `Skær ${holes.length} huller ud`;
    if (holes.some(id => model.lockedTree(id))) return {label: cutLabel, hint: holes.length === 1
      ? 'Lås det valgte hul op for at skære det ud.' : 'Lås de huller op, du vil skære ud.'};
    const matches = holes.map(holeTargets), targets = [...new Set(matches.flat())];
    if (matches.some(items => !items.length)) {
      return {label: cutLabel, hint: 'Et hul rører ikke en figur. Flyt det ind over den figur, det skal skære i.'};
    }
    if (matches.some(items => items.every(id => model.lockedTree(id)))) return {label: cutLabel, hint: 'Lås den figur op, du vil skære i.'};

    const chosen = visible.filter(node => node.mode !== 'hole').map(node => node.id);
    const explicit = chosen.length && chosen.every(id => !model.lockedTree(id)) &&
      matches.every(items => items.some(id => chosen.includes(id)));
    // Saml intentionally includes all chosen parts. The cut action only groups the ones the holes actually hit.
    const joinIds = explicit && picked.length ? [...chosen, ...holes] : null;
    if (explicit) {
      const affected = chosen.filter(id => targets.includes(id));
      const titles = affected.map(id => names.get(id)).join(', ');
      const remainder = targets.length > affected.length ? ' Figurer uden for valget bevares og kan dække hullet.' : '';
      return {ids: [...affected, ...holes], confirmedTargets: affected, joinIds,
        hint: (affected.length === 1 ? `Skær i ${titles}.` : `Saml og skær i ${titles}.`) + remainder,
        label: affected.length === 1 ? `Skær kun i ${titles}` : 'Saml og skær'};
    }
    // A direct cut uses one named solid that every selected hole can cut. Several solids can be chosen with Dele.
    if (targets.length === 1) {
      const [id] = targets;
      return {ids: [id, ...holes], confirmedTargets: [id], hint: `Skær i ${names.get(id)}.`,
        label: chosen.length ? `Skær kun i ${names.get(id)}` : cutLabel};
    }
    const options = targets.filter(id => !model.lockedTree(id) && matches.every(items => items.includes(id)))
      .map(id => ({value: id, title: names.get(id), targets: [id]}));
    if (targets.every(id => !model.lockedTree(id))) {
      const titles = targets.map(id => names.get(id));
      options.push({value: allTargets, title: targets.length === 2 ? `Begge: ${titles.join(' og ')}` : `Alle: ${titles.join(', ')}`, targets});
    }
    if (!options.length) return {label: cutLabel, hint: 'Vælg ét hul ad gangen, eller vælg hullerne og figurerne i Dele.'};
    const key = JSON.stringify([selected.map(node => node.id), holes, targets, options.map(option => option.value)]);
    const choice = cutChoiceKey === key && options.find(option => option.value === cutTarget.value);
    const hint = holes.length === 1 ? 'Hullet rammer flere figurer.' : 'Hullerne rammer flere figurer.';
    const confirmedTargets = choice ? choice.targets : [];
    const where = confirmedTargets.map(id => names.get(id)).join(', ');
    const both = choice && choice.value === allTargets;
    return {key, options, ids: choice ? [...confirmedTargets, ...holes] : null, confirmedTargets,
      hint: both ? `Saml og skær i ${where}.` : choice ? `Skær i ${where}. De andre figurer bevares og kan dække hullet.` : `${hint} Vælg, hvor der skal skæres.`,
      label: both ? (targets.length === 2 ? 'Skær i begge' : 'Skær i alle') : choice ? `Skær kun i ${where}` : cutLabel};
  }
  function renderCut(cut, ready) {
    $('cut-holes').disabled = !cut?.ids;
    cutTarget.disabled = !ready;
    if (!ready && !contextLost && !failed) return;
    $('combine-note').hidden = !cut;
    $('cut-explanation').textContent = cut?.hint || '';
    $('cut-holes').textContent = cut?.label || 'Skær hullet ud';
    cutLabel.hidden = !cut?.options;
    if (cut?.options) {
      const value = cut.key === cutChoiceKey ? cutTarget.value : '';
      const options = [new Option('Vælg figur', ''), ...cut.options.map(({title, value}) => new Option(title, value))];
      if (options.length !== cutTarget.options.length || options.some((option, i) =>
        option.value !== cutTarget.options[i].value || option.text !== cutTarget.options[i].text)) cutTarget.replaceChildren(...options);
      cutTarget.value = value;
    }
    cutChoiceKey = cut?.key || '';
  }
  cutTarget.onchange = () => renderUI();
  const positionDetails = $('position-details'), holeOffsets = document.createElement('div');
  const offsetReference = document.createElement('p'), offsetFields = document.createElement('div');
  const offsetTargetLabel = document.createElement('label'), offsetTarget = document.createElement('select');
  holeOffsets.id = holeOffsets.className = 'hole-offsets'; holeOffsets.hidden = true;
  offsetReference.id = 'hole-offset-reference'; offsetFields.className = 'fields two worded';
  offsetTargetLabel.className = 'hole-offset-target'; offsetTargetLabel.hidden = true;
  offsetTarget.id = 'hole-offset-target'; offsetTarget.setAttribute('aria-label', 'Mål fra');
  offsetTargetLabel.append('Mål fra', offsetTarget);
  const offsetInputs = ['Fra venstre kant', 'Fra forkant'].map((caption, axis) => {
    const label = document.createElement('label'), input = document.createElement('input');
    input.type = 'text'; input.inputMode = 'decimal'; input.autocomplete = 'off';
    input.dataset.holeOffset = String(axis);
    input.setAttribute('aria-label', `${caption} (mm)`); input.setAttribute('aria-describedby', offsetReference.id);
    label.append(caption, input); offsetFields.append(label);
    return input;
  });
  holeOffsets.append(offsetTargetLabel, offsetReference, offsetFields); positionDetails.querySelector('.fields').before(holeOffsets);
  let shownOffset = null;
  offsetTarget.onchange = () => {
    if (busy() || !shownOffset) return;
    const plate = offsetTarget.value;
    if (shownOffset.plates.includes(plate)) measuredPlates.set(shownOffset.hole, plate);
    else measuredPlates.delete(shownOffset.hole);
    renderUI();
  };

  function alongWorldAxes(matrix, axes = [0, 1, 2]) {
    // Test the actual basis, including mirrors, instead of rounded rotation labels.
    return axes.every((axis, local) => {
      const direction = new THREE.Vector3().setFromMatrixColumn(matrix, local).normalize();
      return Math.abs(direction.getComponent(axis)) > .999999999 &&
        [0, 1, 2].every(other => other === axis || Math.abs(direction.getComponent(other)) < 1e-6);
    });
  }
  function isVertical(matrix) {
    const direction = new THREE.Vector3().setFromMatrixColumn(matrix, 2).normalize();
    return Math.abs(direction.x) <= 1e-6 && Math.abs(direction.y) <= 1e-6;
  }
  function holeOffsetReference(node, state) {
    if (!state || !isRoundHole(node, state.matrix) || node.hidden || !isVertical(state.matrix)) return null;
    const plates = holeTargets(node.id).filter(id => {
      const part = model.object(id);
      return part.kind === 'box' && !part.params?.round && alongWorldAxes(objectMatrix(part));
    });
    const previous = measuredPlates.get(node.id);
    const plate = plates.length === 1 ? plates[0] : plates.includes(previous) ? previous : null;
    if (!plate && plates.length > 1) return {hole: node.id, plates, plate: null};
    const bounds = plate && view.bounds([plate]), hole = view.bounds([node.id]);
    if (!bounds || !hole) return null;
    return {hole: node.id, plates, plate, bounds, center: hole.getCenter(new THREE.Vector3())};
  }
  const typingValue = element => element === document.activeElement && element.dataset.typing === 'true';
  const offsetNumber = value => String(round(value)).replace('.', ',');
  function renderHoleOffsets(node, state, ready, names) {
    if (ready || shownOffset?.hole !== node?.id || contextLost || failed) {
      shownOffset = ready ? holeOffsetReference(node, state) : null;
    }
    holeOffsets.hidden = !shownOffset;
    if (!shownOffset) return;
    offsetTargetLabel.hidden = shownOffset.plates.length < 2;
    offsetTarget.disabled = !ready;
    if (!offsetTargetLabel.hidden && ready) {
      const options = [new Option('Vælg kasse', ''), ...shownOffset.plates.map(id => new Option(names.get(id), id))];
      if (options.length !== offsetTarget.options.length || options.some((option, i) =>
        option.value !== offsetTarget.options[i].value || option.text !== offsetTarget.options[i].text)) offsetTarget.replaceChildren(...options);
      offsetTarget.value = shownOffset.plate ?? '';
    }
    const hasPlate = Boolean(shownOffset.plate);
    offsetFields.hidden = !hasPlate;
    offsetReference.textContent = hasPlate ? `Mål i mm fra kanten af ${names.get(shownOffset.plate)} til hullets midte.`
      : 'Vælg den kasse, du vil måle fra.';
    if (!openedHolePositions.has(node.id)) {
      openedHolePositions.set(node.id, positionPreference ?? true);
      if (positionPreference === null) positionDetails.open = true;
    }
    if (!hasPlate) return;
    offsetInputs.forEach((input, axis) => {
      input.readOnly = node.locked || contextLost || failed;
      if (!typingValue(input)) input.value = offsetNumber(shownOffset.center.getComponent(axis) - shownOffset.bounds.min.getComponent(axis));
    });
  }
  function syncHoleOffsetGuides() {
    const visible = shownOffset?.plate && positionDetails.open && !panelFolded && !busy() && !contextLost && !failed;
    const input = document.activeElement, axis = offsetInputs.indexOf(input);
    view.setHoleOffsets?.(visible ? {bounds: shownOffset.bounds, center: shownOffset.center, axis: axis < 0 ? null : axis} : null);
  }
  positionDetails.addEventListener('toggle', () => {
    if (shownOffset && shownOffset.hole === model.selected[0]) openedHolePositions.set(shownOffset.hole, positionDetails.open);
    syncHoleOffsetGuides();
  });
  positionDetails.querySelector('summary').addEventListener('click', () => {
    positionPreference = !positionDetails.open;
  });
  holeOffsets.addEventListener('focusin', syncHoleOffsetGuides);
  holeOffsets.addEventListener('focusout', () => queueMicrotask(syncHoleOffsetGuides));

  const badgeHolePanel = document.createElement('div'), badgeHoleHint = document.createElement('p'), badgeHoleActions = document.createElement('div');
  badgeHolePanel.id = 'badge-hole-panel'; badgeHolePanel.hidden = true;
  badgeHoleHint.id = 'badge-hole-hint'; badgeHoleHint.className = 'section-label';
  badgeHoleActions.className = 'flags';
  const endNames = ['venstre', 'højre'];
  const endButtons = endNames.map((end, index) => {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = `Læg til ${end}`;
    button.setAttribute('aria-describedby', badgeHoleHint.id);
    button.onclick = () => placeBadgeHoleAtEnd(index);
    badgeHoleActions.append(button);
    return button;
  });
  badgeHolePanel.append(badgeHoleHint, badgeHoleActions);
  $('selection-controls').querySelector('.selection-actions').after(badgeHolePanel);

  function visibleBounds(exclude = null) {
    return model.data.roots.flatMap(id => {
      const node = model.object(id), bounds = id !== exclude && !node.hidden && view.bounds([id]);
      return bounds ? [{node, bounds}] : [];
    });
  }
  const plainBounds = box => ({min: box.min.toArray(), max: box.max.toArray()});
  function plateFootprint(node, bounds) {
    if (node?.kind !== 'box' || node.hidden || node.mode === 'hole' || !bounds) return null;
    const matrix = objectMatrix(node);
    const direction = new THREE.Vector3().setFromMatrixColumn(matrix, 0).normalize();
    const xAxis = Math.abs(direction.x) > Math.abs(direction.y) ? 0 : 1;
    if (!alongWorldAxes(matrix, [xAxis, 1 - xAxis, 2])) return null;
    const footprint = plainBounds(bounds);
    if (node.params?.round > 0) {
      const scale = new THREE.Vector3().setFromMatrixScale(matrix);
      // Match the two local radius clamps used when the rounded box mesh is built.
      const localRadii = [0, 1].map(axis => Math.min(node.params.round / scale.getComponent(axis), node.size[axis] / 2 - 1e-4));
      if (localRadii.every(radius => radius > 1e-4)) {
        footprint.radii = [];
        localRadii.forEach((radius, axis) => { footprint.radii[axis === 0 ? xAxis : 1 - xAxis] = radius * scale.getComponent(axis); });
      }
    }
    return footprint;
  }
  function holeCopyContext(ids) {
    if (!ids.length || ids.some(id => {
      const node = model.object(id);
      return node?.mode !== 'hole' || node.hidden;
    })) return null;
    const matches = ids.map(holeTargets), [target] = matches[0];
    if (!target || !matches.every(targets => targets.length === 1 && targets[0] === target)) return null;
    const cutters = view.bounds(ids);
    return cutters && holeContextForPlate(target, plainBounds(cutters));
  }
  function holeContextForPlate(target, cutter) {
    if (!model.data.roots.includes(target)) return null;
    const plate = plateFootprint(model.object(target), view.bounds([target]));
    if (!plate || cutter.min[2] >= plate.max[2] || cutter.max[2] <= plate.min[2]) return null;
    return {target, bounds: cutter, plate,
      occupied: visibleBounds(target).map(({node, bounds}) => ({bounds: plainBounds(bounds), hole: node.mode === 'hole'}))};
  }
  function isBadgePlate({node, bounds}) {
    if (!plateFootprint(node, bounds)) return false;
    const {x, y, z} = bounds.getSize(new THREE.Vector3());
    return Math.min(x, y) >= 2 * z;
  }
  function circleClears(x, y, radius, bounds) {
    const dx = Math.max(bounds.min.x - x, x - bounds.max.x, 0);
    const dy = Math.max(bounds.min.y - y, y - bounds.max.y, 0);
    return Math.hypot(dx, dy) >= radius;
  }
  function badgeEnds(plate, radius, bottom, top, others) {
    const footprint = plateFootprint(plate.node, plate.bounds), middle = plate.bounds.getCenter(new THREE.Vector3());
    const hole = {min: [-radius, -radius, bottom], max: [radius, radius, top]};
    const fits = x => fitsInside(hole, footprint, [x, middle.y, 0], 2);
    if (!fits(middle.x)) return null;
    let left = plate.bounds.min.x + radius + 2;
    if (!fits(left)) {
      // A deep corner radius can narrow the end. Move in only as far as needed for a continuous wall.
      let outside = left, inside = middle.x;
      for (let step = 0; step < 24; step++) {
        const x = (outside + inside) / 2;
        if (fits(x)) inside = x; else outside = x;
      }
      left = inside;
    }
    if (middle.x - left < .01) return null;
    const {y} = middle;
    const obstacles = others.filter(({node, bounds}) => node.id !== plate.node.id && bounds.min.z <= top && bounds.max.z >= bottom);
    return [left, 2 * middle.x - left].map(x => ({
      position: [x, y], blockers: obstacles.filter(({bounds}) => !circleClears(x, y, radius + 1, bounds)),
    }));
  }
  function textSupport(node, text, others) {
    if (result?.looseRoots?.includes(node.id)) return null;
    const supports = others.filter(({node: other, bounds}) => other.id !== node.id && other.mode !== 'hole' &&
      bounds.min.z < text.min.z && bounds.max.z >= text.min.z - 1e-6 && bounds.max.z <= text.min.z + 1.1 &&
      bounds.min.x < text.max.x && bounds.max.x > text.min.x && bounds.min.y < text.max.y && bounds.max.y > text.min.y);
    return supports.length === 1 ? supports[0] : null;
  }
  function fitBadgeEnd(plate, slot, radius, others) {
    const {position, blockers} = slot;
    if (!blockers.length) return {position};
    if (blockers.length !== 1) return null;
    const [{node, bounds}] = blockers;
    if (node.kind !== 'text' || node.mode === 'hole' || node.locked || !alongWorldAxes(objectMatrix(node))) return null;
    if (textSupport(node, bounds, others) !== plate || others.some(part =>
      part.node.id !== node.id && part.node.kind === 'text' && part.node.mode !== 'hole' &&
      textSupport(part.node, part.bounds, others) === plate)) return null;
    const center = new THREE.Vector3(...position, 0), space = roomBesideHole(plate.bounds, center, radius, 'x');
    if (!space) return null;
    const footprint = plateFootprint(plate.node, plate.bounds), room = space.room;
    if (!fitsInside(plainBounds(room), footprint, [0, 0, 0]) && footprint.radii) {
      // The inset rectangle lies inside all four polygonal corners, including a near-capsule.
      const inset = 1 - Math.cos(Math.PI / 24) / Math.SQRT2;
      footprint.radii.forEach((radius, axis) => {
        room.min.setComponent(axis, Math.max(room.min.getComponent(axis), footprint.min[axis] + radius * inset));
        room.max.setComponent(axis, Math.min(room.max.getComponent(axis), footprint.max[axis] - radius * inset));
      });
    }
    if (room.isEmpty()) return null;
    const transform = textFitMatrix(textFit(bounds, room));
    const fitted = bounds.clone().applyMatrix4(transform);
    if (!fitsInside(plainBounds(fitted), footprint, [0, 0, 0]) ||
        others.some(part => part.node.id !== plate.node.id && part.node.id !== node.id && part.bounds.intersectsBox(fitted))) return null;
    return {position, text: node.id, transform};
  }
  function badgeHoleEnds(node, state) {
    if (!state || node?.mode !== 'hole' || node.hidden || node.locked ||
        !result?.uncombinedHoles?.includes(node.id)) return null;
    const diameter = uprightCylinderSection(node, state.matrix)?.diameter;
    if (!diameter) return null;
    const hole = view.bounds([node.id]), others = visibleBounds(node.id);
    if (!hole) return null;
    // A contacted plate takes precedence. An unattached hole can use a single identifiable plate.
    const targets = holeTargets(node.id);
    const plates = others.filter(part => (!targets.length || targets.includes(part.node.id)) && isBadgePlate(part));
    if (plates.length !== 1) return null;
    const [plate] = plates;
    if (plate.node.locked || hole.min.z > plate.bounds.min.z + 1e-6 || hole.max.z < plate.bounds.max.z - 1e-6) return null;
    const ends = badgeEnds(plate, diameter / 2, hole.min.z, hole.max.z, others)
      ?.map(slot => fitBadgeEnd(plate, slot, diameter / 2, others));
    return ends && {id: node.id, plate: plate.node.id, ends, center: new THREE.Vector3().setFromMatrixPosition(state.matrix)};
  }
  let badgeHole = null;
  function renderBadgeHole(node, state, ready, names) {
    if (ready || node?.id !== badgeHole?.id || contextLost || failed) badgeHole = ready ? badgeHoleEnds(node, state) : null;
    const active = document.activeElement;
    badgeHolePanel.hidden = !badgeHole;
    if (badgeHole) {
      const [left, right] = badgeHole.ends, plate = names.get(badgeHole.plate);
      badgeHoleHint.textContent = left?.text || right?.text ? 'Tilpas navnet for at gøre plads til hullet.'
        : left && right ? `Placér hullet i en ende af ${plate} (set forfra).`
        : left || right ? `Midten af ${left ? 'højre' : 'venstre'} ende er optaget.`
          : 'Midten af begge ender er optaget.';
      badgeHoleActions.hidden = !left && !right;
      endButtons.forEach((button, index) => {
        const end = badgeHole.ends[index], position = end?.position;
        button.hidden = !position;
        button.textContent = end?.text ? `Tilpas navn og læg hul til ${endNames[index]}` : `Læg til ${endNames[index]}`;
        button.setAttribute('aria-disabled', String(!ready));
        button.setAttribute('aria-pressed', String(Boolean(position && !end.text &&
          Math.hypot(position[0] - badgeHole.center.x, position[1] - badgeHole.center.y) < 1e-6)));
      });
    }
    if (endButtons.includes(active) && active.closest('[hidden]')) $('workspace').focus({preventScroll: true});
  }
  function placeBadgeHoleAtEnd(index) {
    if (busy() || failed || contextLost || model.selected.length !== 1) return;
    const [node] = model.selectedObjects(), fit = node && badgeHoleEnds(node, view.localState(node.id));
    const end = fit?.ends[index];
    if (!end) return;
    const [x, y] = end.position, dx = x - fit.center.x, dy = y - fit.center.y;
    const message = `Hullet står i ${endNames[index]} ende af ${listName(fit.plate)}.`;
    if (end.text) {
      const text = model.object(end.text);
      notice = `Navnet er tilpasset. ${message}`;
      mutate(() => model.change(() => {
        text.matrix = end.transform.multiply(objectMatrix(text)).toArray();
        node.matrix = new THREE.Matrix4().makeTranslation(dx, dy, 0).multiply(objectMatrix(node)).toArray();
      }));
    } else {
      if (Math.hypot(dx, dy) < 1e-6) { status(message); return; }
      notice = message;
      mutate(() => model.moveEach([[fit.id, [dx, dy, 0]]]));
    }
  }

  const fitTextPanel = document.createElement('div'), fitTextMessage = document.createElement('p'), fitTextButton = document.createElement('button');
  fitTextPanel.id = 'fit-text-panel'; fitTextPanel.hidden = true; fitTextMessage.id = 'fit-text-message';
  fitTextButton.type = 'button'; fitTextButton.id = 'fit-text'; fitTextButton.textContent = 'Tilpas tekst';
  fitTextButton.setAttribute('aria-describedby', fitTextMessage.id);
  fitTextPanel.append(fitTextMessage, fitTextButton); $('text-controls').append(fitTextPanel);
  let fitTextPart = null;
  function renderTextFit(node, ready, names) {
    if (!ready && !contextLost && !failed) {
      fitTextPanel.hidden = !fitTextPart || node?.id !== fitTextPart;
      return;
    }
    const fit = ready && textOverhang(node, true);
    fitTextPart = fit ? node.id : null;
    fitTextPanel.hidden = !fit;
    if (fit) fitTextMessage.textContent = fit.clearsHole
      ? 'Tilpas teksten ved siden af hullet, så det holdes frit.'
      : `Teksten stikker ud over kanten af ${names.get(fit.supportId)}.`;
  }
  fitTextButton.onclick = () => {
    const id = fitTextPart;
    function fitSelectedText() {
      if (!id || contextLost || failed || model.selected.length !== 1 || model.selected[0] !== id) return;
      const fit = textOverhang(model.object(id), true);
      if (!fit) return;
      notice = fit.clearsHole ? 'Teksten er tilpasset ved siden af hullet.' : `Teksten er tilpasset ${listName(fit.supportId)}.`;
      mutate(() => model.applyTransform(textFitMatrix(fit)));
      $('workspace').focus({preventScroll: true});
    }
    if (busy()) later(fitSelectedText); else fitSelectedText();
  };
  function showLibrary(parts) {
    $('forms-panel').hidden = parts; $('parts-panel').hidden = !parts;
    $('show-forms').setAttribute('aria-pressed', String(!parts)); $('show-parts').setAttribute('aria-pressed', String(parts));
    if (!parts) $('parts-multiple').setAttribute('aria-pressed', 'false');
  }
  $('show-forms').onclick = () => showLibrary(false);
  $('show-parts').onclick = () => showLibrary(true);
  $('parts-multiple').onclick = () => {
    const button = $('parts-multiple');
    button.setAttribute('aria-pressed', String(button.getAttribute('aria-pressed') !== 'true'));
  };
  function foldPanel(fold) {
    panelFolded = fold; renderUI();
    $(fold ? 'panel-open' : 'panel-close').focus({preventScroll: true});
  }
  $('panel-open').onclick = () => foldPanel(false);
  $('panel-close').onclick = () => foldPanel(true);
  const toolbar = document.querySelector('.toolbar');
  const editActions = toolbar.querySelector('.edit-actions');
  const joinActions = toolbar.querySelector('.join-actions');
  const arrangeActions = toolbar.querySelector('.arrange-actions');
  const fitCamera = document.querySelector('[data-camera=fit]');
  const buildOnTop = $('build-on-top'), dropToPlane = $('drop');
  const workplaneNote = $('workplane-note');
  function setWorkplane(next) {
    workplane = next;
    view.setWorkplane(next?.height ?? null);
    workplaneNote.hidden = !next;
    if (next) $('workplane-height').textContent = `Arbejdsplan · ${mm(next.height)} mm`;
  }
  buildOnTop.onclick = () => {
    if (busy() || buildOnTop.hidden || buildOnTop.disabled) return;
    const [node] = model.selectedObjects();
    const box = view.bounds([node.id]);
    if (!box) return;
    const center = box.getCenter(new THREE.Vector3());
    setWorkplane({height: box.max.z, center: [center.x, center.y]});
    status(`Arbejdsplanet ligger oven på ${listName(node.id)}. Vælg en form for at bygge videre.`);
    renderUI();
  };
  function clearWorkplane() {
    const restoreFocus = document.activeElement === $('reset-workplane');
    setWorkplane(null);
    status('Nye former lander på pladen igen.');
    renderUI();
    if (restoreFocus) (buildOnTop.hidden || panelFolded ? $('workspace') : buildOnTop).focus({preventScroll: true});
  }
  $('reset-workplane').onclick = clearWorkplane;
  function renderUI() {
    const selected = model.selectedObjects(), box = view.bounds(model.selected), single = selected.length === 1, state = single ? view.localState(selected[0].id) : null;
    const historyReady = rendered === sequence && !contextLost, ready = historyReady && !failed;
    updateCopyRow(ready);
    const names = listNames();
    view.enabled = ready; document.querySelectorAll('[data-add], [data-preset], #solid, #hole, #game-piece-open').forEach(element => { element.disabled = contextLost || failed; });
    // Fields stay open while the model is calculated: what the pupil types meanwhile waits in the queue, so fast typing with Tab is kept.
    const editable = !contextLost && !failed;
    document.querySelectorAll('[data-size], [data-rotation]').forEach(element => { element.readOnly = !editable || !single || !state; });
    document.querySelectorAll('[data-rotation]').forEach(element => { element.readOnly = !editable || !single || !state || state.sheared; });
    document.querySelectorAll('[data-position]').forEach(element => { element.readOnly = !editable; });
    document.querySelectorAll('#text-value, #text-height, #text-depth').forEach(element => { element.disabled = !editable; });
    if (!model.data.roots.length && !managed) modelName = '';
    if (fieldError && (fieldError.id !== selected[0]?.id || !single)) clearFieldError();
    $('empty').hidden = model.data.roots.length > 0 || Boolean(workplane) || !$('guide').hidden;
    $('selection-controls').hidden = !selected.length || panelFolded;
    $('panel-open').hidden = !selected.length || !panelFolded;
    const generatorNode = single && !selected[0].hidden && !panelFolded ? selected[0] : null;
    const connector = single && isConnector(selected[0]);
    const canConfigure = Boolean(generatorNode && editable && !model.lockedTree(generatorNode.id));
    mechanicalEditors.render(generatorNode, canConfigure, ready);
    gamePieceEditor.render(generatorNode, canConfigure && ready);
    $('connector-size-note').hidden = !connector;
    $('generator-scale-note').hidden = !generatorNode?.generator || !state?.scale.some(scale => Math.abs(Math.abs(scale) - 1) > .002);
    $('selection-label').textContent = single ? names.get(selected[0].id) : `${selected.length} figurer valgt`;
    $('solid').setAttribute('aria-pressed', String(selected.length > 0 && selected.every(node => node.mode === 'solid')));
    $('hole').setAttribute('aria-pressed', String(selected.length > 0 && selected.every(node => node.mode === 'hole')));
    if (connector) $('solid').disabled = $('hole').disabled = true;
    const visible = selected.filter(node => !node.hidden), cutting = visible.some(node => node.mode === 'hole');
    const cut = ready && result?.exportBlocked ? cutPlan(selected, names) : null;
    $('group').disabled = !ready || visible.length < 2 || (cutting && !cut?.joinIds);
    $('group').dataset.tipOff = visible.length < 2 ? 'Vælg to eller flere figurer først'
      : cutting && visible.some(node => model.lockedTree(node.id)) ? 'Lås de valgte dele op først'
      : cutting ? 'Vælg hullet sammen med den figur, det skal skære i' : 'Vent, mens modellen beregnes';
    $('center').hidden = selected.length < 2;
    $('center').disabled = !ready || selected.length < 2;
    const canSplit = single && (selected[0].kind === 'group' || legacyPieces.has(assets.get(selected[0].asset)));
    $('part-split').disabled = !ready || !canSplit || selected[0].mode === 'hole' || selected[0].locked || selected[0].hidden;
    $('part-split').dataset.tipOff = canSplit && selected[0].locked ? 'Lås figuren op først' : 'Vælg en synlig, samlet figur først';
    buildOnTop.hidden = !single || !box || selected[0].mode === 'hole' || selected[0].hidden || box.max.z <= 0;
    buildOnTop.disabled = !ready;
    const planeHeight = workplane?.height ?? 0;
    const dropLabel = workplane ? 'Læg på arbejdsplan' : 'Læg på pladen';
    dropToPlane.setAttribute('aria-label', dropLabel);
    dropToPlane.querySelector('span').textContent = dropLabel;
    dropToPlane.dataset.tip = dropLabel;
    const lockedSelection = selected.length > 0 && selected.every(node => node.locked);
    dropToPlane.disabled = !ready || !box || lockedSelection;
    dropToPlane.dataset.tipOff = lockedSelection ? 'Lås en figur op, før du flytter den.' : 'Vent, mens modellen beregnes.';
    dropToPlane.hidden = !box || Math.abs(box.min.z - planeHeight) < .05;
    const canQueueUndo = busy() && previous?.mechanicalEdit;
    $('undo').disabled = !(historyReady || canQueueUndo) || !model.undoStack.length; $('redo').disabled = !historyReady || !model.redoStack.length;
    $('rotate-mode').disabled = !ready || !selected.length;
    // Turning ends when nothing is chosen any more, so the arcs are not waiting on the next part the pupil picks.
    if (!selected.length && $('rotate-mode').getAttribute('aria-pressed') === 'true') { $('rotate-mode').setAttribute('aria-pressed', 'false'); view.showRotate = view.showTilt = false; }
    const lining = selected.length >= 2;
    $('align-menu').classList.toggle('off', !lining);
    $('align-menu').querySelector('summary').setAttribute('aria-disabled', String(!lining));
    if (!lining) $('align-menu').open = false;
    document.querySelectorAll('[data-align]').forEach(button => { button.disabled = !ready || !lining; });
    document.querySelectorAll('[data-spread]').forEach(button => { button.disabled = !ready || selected.length < 3; button.dataset.tipOff = 'Vælg mindst tre figurer'; });
    const activeTool = toolbar.contains(document.activeElement) ? document.activeElement : null;
    const hasParts = model.data.roots.length > 0, hasSelection = selected.length > 0;
    const hasHiddenParts = model.data.roots.some(id => model.object(id)?.hidden);
    const hasVisibleParts = model.data.roots.some(id => !model.object(id)?.hidden);
    fitCamera.disabled = contextLost || !hasVisibleParts || !result?.meshes?.length;
    fitCamera.dataset.tipOff = contextLost ? '3D-visningen er gået tabt' : 'Der er ingen synlige figurer at vise';
    toolbar.hidden = !hasParts && !model.undoStack.length && !model.redoStack.length;
    editActions.hidden = joinActions.hidden = !hasSelection;
    arrangeActions.hidden = !hasVisibleParts && !lining;
    $('select-all').hidden = !hasVisibleParts;
    $('select-all').disabled = !ready || !hasVisibleParts;
    $('more-menu').hidden = !hasSelection && !hasHiddenParts;
    if ($('more-menu').hidden) $('more-menu').open = false;
    $('align-menu').hidden = $('copy-row-open').hidden = $('mirror').hidden = !hasSelection;
    $('show-hidden').hidden = !hasHiddenParts;
    $('show-hidden').disabled = !ready || !hasHiddenParts;
    $('mirror').disabled = !ready || !hasSelection;
    $('duplicate').disabled = !ready || !hasSelection;
    $('delete').disabled = !ready || !hasSelection;
    if (activeTool?.closest('[hidden]')) $('workspace').focus({preventScroll: true});
    $('download').disabled = $('download-3mf').disabled = $('send-print').disabled = !workerReady || rendered !== sequence || !result?.merged || result?.exportBlocked;
    $('download').dataset.tipOff = $('download-3mf').dataset.tipOff = $('send-print').dataset.tipOff = !model.data.roots.length ? 'Byg noget først' : result?.exportBlocked ? 'Skær hullet ud først' : 'Vent lidt, modellen bliver beregnet';
    if (single && selected[0].id !== panelFor) {
      if (panelFor) {
        $('rotation-controls').open = false;
        positionDetails.open = positionPreference ?? (openedHolePositions.get(selected[0].id) === true);
      }
      panelFor = selected[0].id;
    }
    renderCut(cut, ready);
    renderHoleOffsets(single ? selected[0] : null, state, ready, names);
    renderBadgeHole(single ? selected[0] : null, state, ready, names);
    const locked = selected.length > 0 && selected.every(node => node.locked), hidden = selected.length > 0 && selected.every(node => node.hidden);
    $('lock').setAttribute('aria-pressed', String(locked)); $('lock').dataset.tip = locked ? 'Låst. Klik for at låse op' : 'Lås: figuren kan ikke flyttes, ændres eller slettes';
    $('hide').setAttribute('aria-pressed', String(hidden)); $('hide').dataset.tip = hidden ? 'Skjult. Klik for at vise den igen' : 'Skjul: figuren kommer ikke med i filen';
    $('lock').disabled = $('hide').disabled = !ready;
    if (selected.some(node => node.locked)) document.querySelectorAll('[data-size], [data-rotation], [data-position]').forEach(element => { element.readOnly = true; });
    $('rotation-controls').hidden = !single; $('multiple-hint').hidden = single;
    // With several parts chosen their sizes stand on the model, so the empty size fields are left out.
    $('size-controls').hidden = selected.length > 1;
    if (connector) document.querySelectorAll('[data-size]').forEach(input => { input.readOnly = true; });
    const solids = selected.filter(node => node.mode !== 'hole'), shared = solids.length && solids.every(node => view.colorOf(node.id) === view.colorOf(solids[0].id)) ? view.colorOf(solids[0].id) : null;
    $('color-details').hidden = !solids.length;
    $('color-row').classList.toggle('muted-row', !solids.length);
    document.querySelectorAll('#color-row button').forEach(button => { button.disabled = !ready; button.setAttribute('aria-pressed', String(button.dataset.color === shared)); });
    $('text-controls').hidden = !single || selected[0].kind !== 'text';
    textFont.disabled = !editable || Boolean(selected[0]?.locked);
    renderTextFit(single ? selected[0] : null, ready, names);
    const sided = single && ['cylinder', 'cone'].includes(selected[0].kind); $('sides-row').hidden = !sided; $('sides').disabled = !editable || Boolean(selected[0]?.locked);
    const rounded = single && ['sphere', 'halfsphere'].includes(selected[0].kind);
    $('facets-row').hidden = $('facets-hint').hidden = !rounded;
    $('facets').disabled = !editable || Boolean(selected[0]?.locked);
    if (rounded) $('facets').value = selected[0].params?.facets ?? 48;
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
      textFont.value = FONT_BY_KEY.has(params.font) ? params.font : DEFAULT_FONT;
      for (const [id, value] of [['text-value', params.text], ['text-height', round(params.height * scale.y)], ['text-depth', round(params.depth * scale.z)]]) if ($(id) !== document.activeElement) $(id).value = value;
    }
    if (box) {
      const center = box.getCenter(new THREE.Vector3()).toArray(); center[2] = box.min.z;
      document.querySelectorAll('[data-position]').forEach(element => { if (!typingValue(element)) element.value = round(center[Number(element.dataset.position)]); });
      document.querySelectorAll('[data-size]').forEach(element => { if (!typingValue(element)) element.value = state ? round(state.size[Number(element.dataset.size)]) : ''; });
      document.querySelectorAll('[data-rotation]').forEach(element => { if (!typingValue(element)) element.value = state ? Math.round(state.angles[Number(element.dataset.rotation)]) : ''; });
    }
    diameter.render(single ? selected[0] : null, state, editable);
    view.select(model.selected);
    view.resize({preservePosition: true});
    syncHoleOffsetGuides();
    view.markCutTargets?.(ready && cut?.options?.length && cut.ids ? cut.confirmedTargets : []);
    $('part-count').textContent = model.data.roots.length;
    $('parts-empty').hidden = Boolean(model.data.roots.length);
    $('parts-multiple').hidden = model.data.roots.length < 2;
    // Keep existing buttons attached. Leaving a number field can start a calculation between pointerdown and click.
    const list = $('object-list'), focusedPart = list.contains(document.activeElement) ? document.activeElement : null;
    const existing = new Map([...list.children].map(button => [button.dataset.object, button]));
    for (const [index, id] of model.data.roots.entries()) {
      const node = model.object(id), shown = names.get(id);
      let button = existing.get(id);
      if (!button) {
        button = document.createElement('button'); button.type = 'button'; button.dataset.object = id;
        const icon = document.createElement('span'), label = document.createElement('span');
        icon.setAttribute('aria-hidden', 'true'); label.className = 'part-name'; button.append(icon, label);
        button.onclick = event => {
          const extend = event.shiftKey || $('parts-multiple').getAttribute('aria-pressed') === 'true';
          const choose = () => {
            if (contextLost || failed || !model.data.roots.includes(id)) return;
            model.select(id, extend); renderUI();
          };
          if (busy()) later(choose); else choose();
        };
      }
      existing.delete(id);
      if (button !== list.children[index]) list.insertBefore(button, list.children[index] || null);
      const [icon, label] = button.children, hole = node.mode === 'hole', text = node.kind === 'text';
      icon.className = 'part-icon' + (hole ? ' hole' : text ? ' text' : '');
      icon.style.color = !hole && text ? view.colorOf(id) : '';
      icon.style.background = !hole && !text ? view.colorOf(id) : '';
      icon.textContent = text && !hole ? 'Aa' : '';
      button.classList.toggle('hidden-part', Boolean(node.hidden)); button.classList.toggle('locked-part', Boolean(node.locked));
      // Parts that do not touch the rest are marked, so the pupil can see which ones the status line means.
      const loose = rendered === sequence && result?.looseRoots?.includes(id);
      button.classList.toggle('loose-part', Boolean(loose));
      button.dataset.tip = loose ? `${shown} rører ikke de andre figurer` : `${shown}. Hold Shift for at vælge flere.`;
      label.textContent = shown + (node.hidden ? ' · skjult' : '') + (node.locked ? ' · låst' : '');
      button.setAttribute('aria-pressed', String(model.selected.includes(id)));
    }
    for (const button of existing.values()) button.remove();
    if (focusedPart?.isConnected && focusedPart !== document.activeElement) focusedPart.focus({preventScroll: true});
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
  function copySelection() {
    if (busy()) { later(copySelection); return; }
    if (!model.selected.length) return;
    const box = view.bounds();
    const context = holeCopyContext(model.selected);
    model.copy();
    if (box) model.clipboard.box = plainBounds(box);
    if (context) model.clipboard.holeTarget = context.target;
  }
  function move(dx, dy, dz) { if (model.selected.length) mutate(() => model.applyTransform(new THREE.Matrix4().makeTranslation(dx, dy, dz))); }
  const defaultHoleDiameter = bounds => Math.max(3, Math.min(10, Math.round(Math.min(bounds.max.x - bounds.min.x, bounds.max.y - bounds.min.y) / 4)));
  function badgeHolePlacement(node) {
    if (node?.kind !== 'text' || node.mode === 'hole' || node.hidden || !isVertical(objectMatrix(node))) return null;
    const text = view.bounds([node.id]);
    if (!text) return null;
    const others = visibleBounds();
    const plate = textSupport(node, text, others);
    if (!plate || !isBadgePlate(plate)) return null;
    const diameter = defaultHoleDiameter(plate.bounds);
    const bottom = plate.bounds.min.z, top = plate.bounds.max.z + 1;
    const ends = plate.node.locked ? null : badgeEnds(plate, diameter / 2, bottom, top, others);
    const index = ends?.findIndex(end => !end.blockers.length) ?? -1;
    if (index >= 0) {
      return {options: {position: ends[index].position, bottom, size: [diameter, diameter, top - bottom]},
        message: `Hullet står i ${endNames[index]} ende af ${listName(plate.node.id)}. Vælg Skær hullet ud, når det ligger rigtigt.`};
    }
    const reason = plate.node.locked ? 'Pladen under teksten er låst.' : ends ? 'Midten af begge ender er optaget.' : 'Der er for lidt plads ved enderne.';
    const position = findFreeSpot([diameter, diameter], others.map(({bounds}) => ({min: bounds.min.toArray(), max: bounds.max.toArray()})));
    if (!others.every(({bounds}) => circleClears(...position, diameter / 2 + 1, bounds))) {
      return {message: `${reason} Giv plads til hullet, og prøv igen.`};
    }
    return {options: {position, bottom, size: [diameter, diameter, top - bottom]}, detached: true,
      message: `${reason} Hullet er sat ved siden af modellen.`};
  }
  // A new text lands on top of the selected part (sunk 0,5 mm so it fuses). Other shapes take the nearest free spot.
  // A new hole in a chosen part is about a quarter of its width, 3–10 mm: the size of a hole for a key ring or a screw.
  function positionForShape(size, plane = workplane, requireFree = false, origin = plane?.center) {
    const occupied = model.data.roots.map(id => view.bounds([id]))
      .filter(box => box && (!plane || box.max.z > plane.height + 1e-6))
      .map(box => ({min: [box.min.x, box.min.y], max: [box.max.x, box.max.y]}));
    return findFreeSpot(size, occupied, {origin, requireFree});
  }
  function visibleModuleOrigin(size) {
    const box = view.bounds() ?? view.bounds(model.data.roots);
    if (!box) return undefined;
    const toward = view.camera.position.clone().sub(view.orbit.target);
    const axis = Math.abs(toward.x) > Math.abs(toward.y) ? 0 : 1;
    const sign = Math.sign(toward.getComponent(axis)) || -1;
    const edge = (sign > 0 ? box.max : box.min).getComponent(axis);
    const origin = box.getCenter(new THREE.Vector3()).toArray().slice(0, 2);
    origin[axis] = round(edge + sign * (size[axis] / 2 + 7));
    return origin;
  }

  function placement(kind, mode = 'solid') {
    if (workplane && mode === 'solid') {
      return {position: positionForShape(kind === 'text' ? [40, 14] : [20, 20]), bottom: workplane.height,
        ...(kind === 'text' ? {color: COLORS[7]} : {})};
    }
    const selected = model.selectedObjects(), target = selected.length === 1 && selected[0].mode !== 'hole' ? view.bounds([selected[0].id]) : null;
    if (mode === 'hole' && target && kind !== 'text') {
      const center = target.getCenter(new THREE.Vector3()), width = defaultHoleDiameter(target);
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
    // A star and a heart start flat, like a charm for a key ring.
    return {position: positionForShape(kind === 'text' ? [40, 14] : [20, 20], null), size: ['star', 'heart'].includes(kind) ? [20, 20, 5] : null};
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
    if (edit.type === 'diameter' || edit.type === 'cylinder-axis') {
      if (!(edit.value >= .1 && edit.value <= PLATE_SIZE)) { status(`Målet skal være mellem 0,1 og ${PLATE_SIZE} mm.`); return; }
      mutate(() => edit.type === 'diameter' ? model.setDiameter(id, edit.value)
        : model.setCylinderSize(id, {[edit.axis === 0 ? 'length' : 'width']: edit.value}));
      return;
    }
    if (isConnector(model.object(id)) && ['resize', 'scale'].includes(edit.type)) return;
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
  const workspace = $('workspace');
  const dropPreview = $('shape-drop-preview');
  const dropOutline = dropPreview.querySelector('polygon');
  const dropLabel = dropPreview.querySelector('span');
  const shapeDragType = 'application/x-makervaerkstedet-shape';
  let draggedHole = null;
  const presetsById = new Map(Object.values(SHAPE_LIBRARY).flat().map(preset => [preset.id, preset]));
  function addPreset(preset, position = null) {
    if (busy() || contextLost || failed) return 'Vent til 3D-værkstedet er klar, og prøv igen.';
    const where = position ?? positionForShape(preset.size, workplane, true);
    if (!where) return 'Der er ikke plads til figuren. Flyt nogle af de andre figurer først.';
    let id;
    mutate(() => {
      id = insertShapePreset(model, preset, where, workplane?.height ?? 0, lastColor);
      if (!id) return;
      if (model.data.roots.length === 1) frameFirst = true;
      notice = `${preset.label} er sat ${workplane ? 'på arbejdsplanet' : 'på pladen'}.`;
    });
    return id ? '' : 'Der er ikke plads til flere dele i projektet. Fjern en figur, og prøv igen.';
  }
  function generatedChange(action, {imported3mf = false, mechanicalEdit = false} = {}) {
    let error = '';
    mutate(before => {
      const knownAssets = new Set(assets.keys());
      const changed = action();
      const newAssets = [...assets.keys()].filter(id => !knownAssets.has(id));
      if (!changed) {
        for (const id of newAssets) assets.delete(id);
        return;
      }
      try { encodeProject(model.data, projectAssets()); }
      catch {
        for (const id of newAssets) assets.delete(id);
        model.data = JSON.parse(before.data);
        model.undoStack = before.undo; model.redoStack = before.redo;
        model.selected = before.selected; model.dirty = before.dirty;
        error = 'Projektet bliver for stort til at gemme. Prøv en mindre figur, eller fjern en anden model.';
        return;
      }
      before.newAssets = newAssets;
      before.imported3mf = imported3mf;
      before.mechanicalEdit = mechanicalEdit;
    });
    return error;
  }
  function generatedMesh(kind, settings, {positions, size, label, axisOffset = [0, 0]}) {
    const asset = stlAssetId(new Uint8Array(positions.buffer, positions.byteOffset, positions.byteLength));
    return {asset, size, name: label, generator: {kind, settings, origin: [...axisOffset, size[2] / 2]}};
  }
  function addMechanicalPart(kind, point = null, step = 0) {
    if (busy() || contextLost || failed) return 'Vent til 3D-værkstedet er klar, og prøv igen.';
    if (model.data.objects.length >= MAX_PROJECT_OBJECTS) return 'Der er ikke plads til flere dele. Fjern en figur, og prøv igen.';
    const {settings, mesh} = defaultMechanicalPart(kind);
    const position = point ? dropCoordinates(point, step, mesh.size) : positionForShape(mesh.size, workplane, true);
    if (!position) return 'Der er ikke plads til figuren. Flyt nogle af de andre figurer.';
    if (mesh.axisOffset) mesh.axisOffset.forEach((value, axis) => { position[axis] += value; });
    const options = generatedMesh(kind, settings, mesh);
    panelFolded = false;
    notice = `${mesh.label} er sat ${workplane ? 'på arbejdsplanet' : 'på pladen'}. Ret indstillingerne i egenskaberne.`;
    if (!model.data.roots.length) frameFirst = true;
    return generatedChange(() => {
      assets.set(options.asset, mesh.positions);
      return model.add('mesh', {...options, position, bottom: workplane?.height ?? 0, color: lastColor});
    });
  }
  function connectorFace({point, normal}, step) {
    const up = Math.abs(normal.z) > .98 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
    const across = up.cross(normal).normalize();
    const along = normal.clone().cross(across);
    const snapped = point.clone();
    if (step) for (const tangent of [across, along]) {
      const value = snapped.dot(tangent);
      snapped.addScaledVector(tangent, Math.round(value / step) * step - value);
    }
    return new THREE.Matrix4().makeBasis(across, along, normal).setPosition(snapped);
  }
  function addCatalogPart(preset, point, step, hit) {
    if (busy() || contextLost || failed) return 'Vent til 3D-værkstedet er klar, og prøv igen.';
    const connector = preset.configure === 'connector';
    const recipe = CATALOG_RECIPES.get(preset.id);
    const shape = connector ? buildConnector(preset.settings) : recipe.build(recipe.defaults);
    const origin = shape.connectors && !workplane && !point ? visibleModuleOrigin(shape.size) : undefined;
    const position = point ? dropCoordinates(point, step, shape.size) : positionForShape(shape.size, workplane, true, origin);
    if (!position) return 'Der er ikke plads til figuren. Flyt nogle figurer, og prøv igen.';
    const height = shape.size[2] / 2;
    const bottom = workplane?.height ?? 0;
    const kind = connector ? 'connector' : 'catalog-shape';
    const settings = connector ? preset.settings : {recipe: recipe.id, values: recipe.defaults};
    let id;
    const problem = generatedChange(() => {
      if (shape.parts) {
        const generator = {kind, settings, origin: [0, 0, height]};
        id = model.addMeshGroup(preset.label, catalogChildren(shape, lastColor || recipe.color, assets), [...position, bottom + height], generator, shape.op);
      } else {
        const options = generatedMesh(kind, settings, {...shape, label: preset.label});
        if (connector && shape.mode === 'hole') options.generator.origin[2] = -height;
        assets.set(options.asset, shape.positions);
        const mount = connector && hit ? connectorMount(shape, settings, connectorFace(hit, step)) : null;
        id = model.add('mesh', {...options, position, bottom, color: lastColor, mode: shape.mode, ...mount});
      }
      if (id) {
        panelFolded = false;
        if (connector && hit && shape.mode === 'solid') model.select(hit.id, true);
        if (model.data.roots.length === 1) frameFirst = true;
        notice = connector && hit ? `${preset.label} er sat på ${listName(hit.id)}. Vælg ${shape.mode === 'hole' ? 'Skær hullet ud' : 'Saml med figuren'}.`
          : `${preset.label} er sat ${workplane ? 'på arbejdsplanet' : 'på pladen'}.`;
      }
      return id;
    });
    return problem || (id ? '' : 'Der er ikke plads til flere dele i projektet.');
  }
  function applyCatalogPart(id, settings, shape) {
    const node = model.object(id);
    if (busy() || contextLost || failed || model.selected.length !== 1 || model.selected[0] !== id
        || node?.generator?.kind !== 'catalog-shape' || node.generator.settings.recipe !== settings.recipe || model.lockedTree(id)) {
      return 'Vent til figuren kan redigeres, og prøv igen.';
    }
    let changed;
    const problem = generatedChange(() => {
      const {label} = CATALOG_RECIPES.get(settings.recipe);
      if (shape.parts) {
        const color = node.children.map(child => model.object(child)).find(part => part.mode !== 'hole')?.color;
        const generator = {kind: 'catalog-shape', settings, origin: [0, 0, shape.size[2] / 2]};
        changed = model.replaceGeneratedGroup(id, {generator, op: shape.op, children: catalogChildren(shape, color, assets)});
      } else {
        const options = generatedMesh('catalog-shape', settings, {...shape, label});
        assets.set(options.asset, shape.positions);
        changed = model.replaceGeneratedMesh(id, options);
      }
      if (changed) notice = `${label} er opdateret.`;
      return changed;
    }, {mechanicalEdit: true});
    return problem || (changed ? '' : 'Figuren kunne ikke opdateres. Prøv at ændre målene.');
  }
  function applyNativeProfile(id, {profile, values}) {
    const node = model.object(id);
    if (busy() || contextLost || failed || model.selected.length !== 1 || model.selected[0] !== id || node?.kind !== profile || node.locked) {
      return 'Vent til figuren kan redigeres, og prøv igen.';
    }
    if (!validProfile(profile, values)) return 'Kontrollér figurens mål.';
    const custom = NATIVE_PROFILES[profile].filter(field => values[field.key] !== field.value);
    return generatedChange(() => model.change(() => {
      if (custom.length) node.params = Object.fromEntries(custom.map(({key}) => [key, values[key]]));
      else delete node.params;
    }), {mechanicalEdit: true});
  }
  const mechanicalEditors = setupMechanicalEditors((id, settings, mesh) => {
    const node = model.object(id);
    if (busy() || contextLost || failed || model.selected.length !== 1 || model.selected[0] !== id
        || !node?.generator || model.lockedTree(id)) return 'Vent til figuren kan redigeres, og prøv igen.';
    const previousSettings = node.generator.settings;
    if (Object.keys(settings).length === Object.keys(previousSettings).length
        && Object.entries(settings).every(([key, value]) => previousSettings[key] === value)) return '';
    const options = generatedMesh(node.generator.kind, settings, mesh);
    notice = `${mesh.label} er opdateret.`;
    return generatedChange(() => {
      assets.set(options.asset, mesh.positions);
      return model.replaceGeneratedMesh(id, options);
    }, {mechanicalEdit: true});
  }, applyCatalogPart, applyNativeProfile);
  function insertPreset(preset, point = null, step = 0, hit = null) {
    if (preset.configure === 'game-piece') {
      void addGamePiece(point ? {point, step, plane: workplane} : undefined);
      return;
    }
    const message = ['catalog-shape', 'connector'].includes(preset.configure) ? addCatalogPart(preset, point, step, hit)
      : preset.configure ? addMechanicalPart(preset.configure, point, step)
      : addPreset(preset, point ? dropCoordinates(point, step, preset.size) : undefined);
    if (message) status(message);
  }
  const resetShapeCategory = setupShapeLibrary({
    insert: insertPreset,
    startDrag: (event, preset) => {
      if (event.currentTarget.disabled) { event.preventDefault(); return; }
      draggedHole = null;
      event.dataTransfer.setData(shapeDragType, 'preset:' + preset.id);
      event.dataTransfer.effectAllowed = 'copy';
    },
    unavailable: () => contextLost || failed,
  });
  function dropCoordinates(point, step, footprint = [10, 10]) {
    return [point.x, point.y].map((value, axis) => {
      const limit = Math.max(0, (PLATE_SIZE - footprint[axis]) / 2);
      return Math.max(-limit, Math.min(limit, step ? Math.round(value / step) * step : value));
    });
  }

  function holeOnFace(target) {
    if (busy() || contextLost || failed || model.lockedTree(target.id)) return null;
    return faceHolePlacement(target.point, target.normal, view.bounds([target.id]));
  }

  function previewFaceHole(target, {matrix, size}) {
    const basis = new THREE.Matrix4().fromArray(matrix);
    const across = new THREE.Vector3().setFromMatrixColumn(basis, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(basis, 1);
    const radius = size[0] / 2;
    const corners = draggedHole === 'box' ? [[-1, -1], [1, -1], [1, 1], [-1, 1]]
      : Array.from({length: 24}, (_, i) => {
        const angle = i * Math.PI / 12;
        return [Math.cos(angle), Math.sin(angle)];
      });
    dropOutline.setAttribute('points', corners.map(([x, y]) => {
      const screen = view.project(target.point.clone().addScaledVector(across, x * radius).addScaledVector(up, y * radius));
      return `${screen.x},${screen.y}`;
    }).join(' '));
    const {x, y} = view.project(target.point);
    dropLabel.textContent = `Hul gennem ${listName(target.id)}`;
    dropPreview.hidden = false;
    dropLabel.style.left = `${Math.max(4, Math.min(x + 12, workspace.clientWidth - dropLabel.offsetWidth - 4))}px`;
    dropLabel.style.top = `${Math.max(4, Math.min(y + 14, workspace.clientHeight - dropLabel.offsetHeight - 4))}px`;
  }

  document.querySelectorAll('[data-add]').forEach(button => {
    const mode = button.dataset.mode === 'hole' ? 'hole' : 'solid';
    button.onclick = function add() {
      if (busy()) { later(add); return; }
      const kind = button.dataset.add;
      const badge = mode === 'hole' && kind === 'cylinder' && !button.dataset.sides && model.selected.length === 1
        ? badgeHolePlacement(model.object(model.selected[0])) : null;
      if (badge && !badge.options) { status(badge.message); return; }
      const options = {...(badge?.options || placement(kind, mode)), mode, ...(button.dataset.sides ? {params: {sides: Number(button.dataset.sides)}} : {})};
      if (!model.data.roots.length) frameFirst = true; focusText = kind === 'text';
      const target = model.selected[0];
      if (badge) { notice = badge.message; noticeExplainsMiss = badge.detached === true; }
      else if (options.inside) notice = `Hullet står midt i figuren (${listName(target)}). Træk det derhen, hvor det skal være.`;
      else if (kind === 'text' && options.params) notice = `Teksten står oven på ${listName(target)}.`;
      else if (workplane && mode === 'solid') notice = `${button.getAttribute('aria-label')} er sat på arbejdsplanet.`;
      else if (kind !== 'text') notice = `${button.getAttribute('aria-label')} er sat på pladen.`;
      // A new part takes the colour the pupil chose last, unless it stands on a part and needs a contrasting colour.
      if (mode === 'solid' && !options.color && lastColor) options.color = lastColor;
      mutate(() => model.add(kind, options));
    };
    button.addEventListener('dragstart', event => {
      if (button.disabled) { event.preventDefault(); return; }
      draggedHole = mode === 'hole' ? button.dataset.add : null;
      event.dataTransfer.setData(shapeDragType, button.dataset.add + ':' + mode + ':' + (button.dataset.sides || ''));
      event.dataTransfer.effectAllowed = 'copy';
    });
    button.addEventListener('dragend', () => { draggedHole = null; dropPreview.hidden = true; });
  });
  workspace.addEventListener('dragover', event => {
    const types = event.dataTransfer.types;
    if (!(types.includes(shapeDragType) || types.includes('Files'))) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    if (draggedHole && types.includes(shapeDragType) && busy()) {
      event.dataTransfer.dropEffect = 'none';
      dropPreview.hidden = true;
      return;
    }
    const target = draggedHole && types.includes(shapeDragType) ? view.partAt(event.clientX, event.clientY) : null;
    const options = target ? holeOnFace(target) : null;
    if (options) previewFaceHole(target, options);
    else dropPreview.hidden = true;
  });
  workspace.addEventListener('dragleave', event => {
    if (!workspace.contains(event.relatedTarget)) dropPreview.hidden = true;
  });
  workspace.addEventListener('drop', event => {
    draggedHole = null;
    dropPreview.hidden = true;
    if (event.dataTransfer.files?.length) { event.preventDefault(); importFile(event.dataTransfer.files[0]); return; }
    const [kind, mode, sides] = event.dataTransfer.getData(shapeDragType).split(':');
    const step = event.altKey ? 0 : view.snap;
    if (kind === 'preset') {
      const preset = presetsById.get(mode);
      if (!preset) return;
      event.preventDefault();
      const hit = preset.configure === 'connector' && !workplane ? view.partAt(event.clientX, event.clientY) : null;
      if (hit && model.lockedTree(hit.id)) { status('Lås figuren op, før du sætter en connector på den.'); return; }
      const point = hit?.point ?? view.planePoint(event.clientX, event.clientY, workplane?.height ?? 0);
      if (!point) return;
      insertPreset(preset, point, step, hit);
      return;
    }
    if (!Object.hasOwn(labels, kind) || kind === 'group') return;
    const shapeParams = sides ? {sides: Number(sides)} : {};
    if (mode === 'hole' && busy()) {
      event.preventDefault();
      status('Beregner modellen … Træk hullet igen, når modellen er klar.');
      return;
    }
    const raised = workplane && mode !== 'hole';
    event.preventDefault(); const target = raised ? null : view.partAt(event.clientX, event.clientY);
    const point = target?.point || view.planePoint(event.clientX, event.clientY, raised ? workplane.height : 0);
    if (!point) return;
    if (target && mode === 'hole') {
      if (contextLost || failed) return;
      const options = holeOnFace(target);
      if (!options) {
        status(model.lockedTree(target.id) ? 'Lås figuren op, før du laver et hul.' : 'Hullet kan ikke placeres på den flade. Prøv et andet sted.');
        return;
      }
      notice = `Hullet går gennem ${listName(target.id)}. Vælg Skær hullet ud, hvis det ligger rigtigt.`;
      mutate(() => model.add(kind, {...options, mode, params: shapeParams}));
      return;
    }
    const position = dropCoordinates(point, step, raised ? (kind === 'text' ? [40, 14] : [20, 20]) : undefined);
    // Dropped on a part, the new shape stands on that surface; a text sinks 0,5 mm into it so they fuse.
    // On a face that does not point up, the new shape stands out from the face, like a nose on a head.
    if (target && mode !== 'hole') {
      notice = `${labels[kind]} er sat ${target.normal.z < .3 ? 'på siden af' : 'oven på'} ${listName(target.id)}.`;
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
    const bottom = raised ? workplane.height : 0;
    notice = mode === 'hole' ? 'Hullet er placeret. Vælg, hvilken figur det skal skære i.'
      : `${labels[kind]} er sat ${raised ? 'på arbejdsplanet' : 'på pladen'}.`;
    focusText = kind === 'text';
    mutate(() => model.add(kind, {position, bottom, mode, color: mode === 'solid' ? lastColor : null, params: shapeParams}));
  });
  COLORS.forEach((color, index) => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'swatch'; button.dataset.color = color; button.title = colorNames[index]; button.setAttribute('aria-label', colorNames[index]);
    button.style.background = color; button.onclick = () => { lastColor = color; mutate(() => model.color(color)); }; $('color-row').append(button);
  });
  document.querySelectorAll('[data-camera]').forEach(button => {
    button.onclick = () => {
      if (button.closest('#camera-more') && !contextLost && !failed && !stickyError && !view.panMode
          && !result?.exportBlocked && !result?.missedHoles?.length) {
        status(`Du ser ${result?.meshes?.length ? 'modellen' : 'pladen'} ${button.textContent.trim().toLowerCase()}.`);
      }
      if (button.dataset.camera === 'home' && /^Du ser (modellen|pladen) /.test($('status').textContent)) {
        status('Du ser hele pladen.');
      }
      view.cameraTo(button.dataset.camera);
    };
  });
  function setPan(on) {
    view.setPanMode(on);
    $('pan-mode').setAttribute('aria-pressed', String(on));
    status(on ? 'Træk med venstre museknap for at flytte synet. Tryk på Flyt visning igen eller tryk Esc for at slå det fra.'
      : 'Du kan igen vælge og flytte figurer med venstre museknap.');
  }
  $('pan-mode').onclick = () => setPan($('pan-mode').getAttribute('aria-pressed') !== 'true');
  $('snap').onchange = () => { view.snap = Number($('snap').value); };
  function historyStep(redo) {
    mutate(() => {
      const stack = redo ? model.redoStack : model.undoStack;
      if (!stack.length) return;
      clearFieldError(); notice = redo ? 'Gentaget.' : 'Fortrudt.';
      if (redo) model.redo(); else model.undo();
    }, true);
  }
  $('undo').onclick = () => historyStep(false); $('redo').onclick = () => historyStep(true);
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
  const openMenu = 'details.more-menu[open]';
  function closeMenu(menu) {
    menu.open = false;
    menu.querySelector(':scope > summary').focus({preventScroll: true});
  }
  // Close before the action runs so native dialogs remember the visible summary as their opener.
  document.addEventListener('click', event => {
    const button = event.target.closest('.more-items button');
    if (!button || button.closest('.align-items')) return;
    const menu = button.closest(openMenu);
    if (menu) closeMenu(menu);
  }, true);
  function closeOtherMenus(event) {
    for (const menu of document.querySelectorAll(openMenu)) if (!menu.contains(event.target)) menu.open = false;
  }
  document.addEventListener('pointerdown', closeOtherMenus, true);
  document.addEventListener('focusin', closeOtherMenus);
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
  async function importStl(file, fromShelf = false) {
    const fail = message => { if (fromShelf) throw Error(message); status(message); };
    if (!file) return; if (!/\.stl$/i.test(file.name)) return fail('Vælg en STL-fil.');
    if (file.size > 25 * 1024 * 1024) return fail('Filen er for stor. Vælg en STL-fil på højst 25 MB.');
    let info; try { info = readStl(await file.arrayBuffer()); } catch (error) { return fail(`Filen kunne ikke læses: ${error.message}`); }
    const biggest = Math.max(...info.size), fit = biggest > 170 ? 170 / biggest : 1, size = info.size.map(value => Math.max(.1, Math.round(value * fit * 100) / 100));
    assets.set(info.id, info.positions);
    const options = {position: positionForShape(size), bottom: workplane?.height ?? 0,
      size, asset: info.id, name: file.name.replace(/\.stl$/i, '')};
    notice = fit < 1 ? `Figuren var ${Math.round(biggest)} mm og er gjort mindre, så den passer på pladen.`
      : `${options.name} er hentet ind ${workplane ? 'på arbejdsplanet' : 'som en fast figur'}.`;
    if (lastColor) options.color = lastColor;
    if (!model.data.roots.length) frameFirst = true; mutate(() => model.add('mesh', options));
  }
  let projectReady = false, projectGeneration = 0;
  const is3mf = file => /\.3mf$/i.test(file?.name || '');
  async function import3mf(file, fromShelf = false) {
    const fail = message => { if (fromShelf) throw Error(message); status(message); };
    if (!file) return;
    if (!is3mf(file)) return fail('Vælg en 3MF-fil.');
    if (file.size > MAX_3MF_BYTES) return fail('Vælg en 3MF-fil på højst 25 MB.');
    const generation = projectGeneration;
    let info;
    try { info = read3mf(await file.arrayBuffer()); }
    catch (error) { return fail(error.message || '3MF-filen kunne ikke læses.'); }
    if (projectGeneration !== generation) return;
    if (busy() || failed || contextLost) return fail('Vent til 3D-værkstedet er klar, og prøv igen.');
    const {parts, bounds} = info;
    if (model.data.objects.length + parts.length > MAX_PROJECT_OBJECTS) return fail('Der er ikke plads til flere dele i projektet.');
    if (parts.some(part => part.size.some(value => value < .01 || value > 1_000_000))) return fail('3MF-filen indeholder en figur uden for de understøttede mål.');
    const size = bounds.max.map((value, i) => value - bounds.min[i]);
    const center = bounds.max.map((value, i) => (value + bounds.min[i]) / 2);
    const where = positionForShape(size, workplane, true);
    if (!where) return fail('Der er ikke plads til modellen. Flyt nogle af de andre figurer først.');
    const shift = [where[0] - center[0], where[1] - center[1], (workplane?.height ?? 0) - bounds.min[2]];
    const name = file.name.replace(/\.3mf$/i, '').slice(0, 80) || '3MF-model';
    const children = parts.map(({positions, size, center: partCenter, color, name: partName}, i) => {
      for (let at = 0; at < positions.length; at++) positions[at] -= partCenter[at % 3];
      const asset = stlAssetId(new Uint8Array(positions.buffer));
      return {positions, node: {id: crypto.randomUUID(), kind: 'mesh', mode: 'solid', asset, size,
        position: partCenter.map((value, axis) => value + shift[axis]), name: parts.length === 1 ? name : partName?.slice(0, 80) || `${name} · Del ${i + 1}`.slice(0, 80),
        ...(color || lastColor ? {color: color || lastColor} : {})}};
    });
    const wasEmpty = !model.data.roots.length;
    const message = generatedChange(() => {
      for (const {positions, node} of children) assets.set(node.asset, positions);
      const inserted = model.addImportedMeshes(children.map(({node}) => node));
      if (inserted) {
        frameFirst = wasEmpty;
        notice = size.some(value => value > PLATE_SIZE) ? `${name} er importeret i original størrelse og går ud over byggepladens mål.`
          : parts.length > 1 ? `${name} er importeret med ${parts.length} dele.`
            : `${name} er importeret fra 3MF.`;
      }
      return inserted;
    }, {imported3mf: true});
    if (message) return fail(message);
  }
  window.ElevhubImportFile = async file => {
    // The shared session resets the editor once at startup. Do not let that
    // reset erase an STL delivered by the project shelf while modules load.
    const until = Date.now() + 15000;
    while (managed && !projectReady) {
      if (Date.now() > until) throw Error('Projektgemning blev ikke klar. Prøv igen.');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    return is3mf(file) ? import3mf(file, true) : importStl(file, true);
  };
  // Hent en SVG-tegning ind: its filled areas become a flat part, 40 mm across and 3 mm high.
  async function importSvg(file) {
    if (!file) return; if (file.size > 5 * 1024 * 1024) { status('Tegningen er for stor. Vælg en SVG-fil på højst 5 MB.'); return; }
    let info; try { info = readSvg(await file.text()); } catch (error) { status(error.message); return; }
    // With a solid part chosen, the drawing lies on top of it, 80 % of its size and sunk 0,5 mm so they fuse, like a logo on a plate.
    const chosen = model.selectedObjects(), target = !workplane && chosen.length === 1 && chosen[0].mode !== 'hole' ? view.bounds([chosen[0].id]) : null;
    const across = target ? Math.min(.8 * (target.max.x - target.min.x) / info.width, .8 * (target.max.y - target.min.y) / info.height) : 40 / Math.max(info.width, info.height);
    const size = [info.width * across, info.height * across, target ? 2 : 3].map(value => Math.max(.5, Math.round(value * 100) / 100));
    assets.set(info.id, info.data);
    const options = {size, asset: info.id, name: file.name.replace(/\.svg$/i, '')};
    if (target) {
      const c = target.getCenter(new THREE.Vector3()), top = view.topAt(chosen[0].id, c.x, c.y) ?? target.max.z;
      options.position = [round(c.x), round(c.y)]; options.bottom = round(top - .5); options.color = view.colorOf(chosen[0].id) === COLORS[7] ? COLORS[0] : COLORS[7];
    } else {
      options.position = positionForShape(size); options.bottom = workplane?.height ?? 0;
      if (lastColor) options.color = lastColor;
    }
    notice = `${options.name} er hentet ind som en flad figur, ${String(size[0]).replace('.', ',')} × ${String(size[1]).replace('.', ',')} mm${target ? ', oven på figuren' : workplane ? ', på arbejdsplanet' : ''}.`;
    if (!model.data.roots.length) frameFirst = true; mutate(() => model.add('mesh', options));
  }
  function importFile(file) {
    if (is3mf(file)) return import3mf(file);
    if (/\.svg$/i.test(file?.name || '') || file?.type === 'image/svg+xml') return importSvg(file);
    return importStl(file);
  }
  $('import-svg').onclick = () => $('svg-file').click();
  $('svg-file').onchange = () => { importSvg($('svg-file').files[0]); $('svg-file').value = ''; };
  $('import-stl').onclick = () => $('stl-file').click();
  $('stl-file').onchange = () => { importStl($('stl-file').files[0]); $('stl-file').value = ''; };
  $('import-3mf').onclick = () => $('3mf-file').click();
  $('3mf-file').onchange = () => { import3mf($('3mf-file').files[0]); $('3mf-file').value = ''; };
  const gamePieceEditor = setupGamePiece({
    panel: $('game-piece-editor'),
    insertGroup: (name, layout, settings, placement) => {
      if (busy() || failed || contextLost || (managed && !projectReady)) throw Error('Vent til 3D-værkstedet er klar, og prøv igen.');
      const plane = placement ? placement.plane : workplane;
      const position = placement ? dropCoordinates(placement.point, placement.step, layout.size) : positionForShape(layout.size, workplane, true);
      if (!position) throw Error('Der er ikke plads til spillebrikken. Flyt nogle af de andre figurer først.');
      const [x, y] = position;
      const pieceAssets = new Map();
      const children = gamePieceChildren(layout, pieceAssets, layout.size, lastColor);
      if (model.data.objects.length + children.length + 1 > MAX_PROJECT_OBJECTS) throw Error('Der er ikke plads til flere dele i projektet.');
      const generator = {kind: 'game-piece', settings, origin: [0, 0, layout.size[2] / 2]};
      panelFolded = false;
      notice = `Spillebrikken er sat ${plane ? 'på arbejdsplanet' : 'på pladen'}. Vælg dens dele i egenskaberne.`;
      if (!model.data.roots.length) frameFirst = true;
      const problem = generatedChange(() => {
        const inserted = model.addMeshGroup(name, children, [x, y, (plane?.height ?? 0) + generator.origin[2]], generator);
        if (inserted) for (const [asset, positions] of pieceAssets) assets.set(asset, positions);
        return inserted;
      });
      if (problem) throw Error(problem);
    },
    updateGroup: (id, name, layout, settings) => {
      const node = model.object(id);
      if (busy() || failed || contextLost || model.selected.length !== 1 || model.selected[0] !== id
          || node?.generator?.kind !== 'game-piece' || model.lockedTree(id)) throw Error('Vent til spillebrikken kan redigeres, og prøv igen.');
      if (settings.every((choice, index) => node.generator.settings[index] === choice)) return;
      const pieceAssets = new Map();
      const children = gamePieceChildren(layout, pieceAssets, layout.size, model.object(node.children[0])?.color);
      const generator = {kind: 'game-piece', settings, origin: [0, 0, layout.size[2] / 2]};
      let changed = false;
      const problem = generatedChange(() => {
        changed = model.replaceGeneratedGroup(id, {children, name, generator});
        if (changed) {
          for (const [asset, positions] of pieceAssets) assets.set(asset, positions);
          notice = 'Spillebrikken er opdateret.';
        }
        return changed;
      });
      if (problem) throw Error(problem);
      if (!changed) throw Error('Spillebrikken kunne ikke opdateres.');
    }
  });
  async function addGamePiece(placement) {
    status('Henter spillebrikkens dele …');
    try { await gamePieceEditor.insert(placement); }
    catch (error) { status(error.message || 'Spillebrikken kunne ikke sættes på pladen.'); }
  }
  $('game-piece-open').onclick = () => addGamePiece();
  $('lock').onclick = () => { const lock = !model.selectedObjects().every(node => node.locked); notice = lock ? 'Figuren er låst. Den kan ikke flyttes eller ændres.' : 'Figuren er låst op.'; mutate(() => model.flag('locked', lock)); };
  $('show-hidden').onclick = () => {
    const hidden = model.data.roots.filter(id => model.object(id)?.hidden); if (!hidden.length) return;
    notice = hidden.length === 1 ? 'Den skjulte figur vises igen og er valgt.' : `${hidden.length} skjulte figurer vises igen og er valgt.`;
    mutate(() => { model.selected = hidden; model.flag('hidden', false); });
  };
  $('hide').onclick = () => { const hide = !model.selectedObjects().every(node => node.hidden); notice = hide ? 'Figuren er skjult. Vis skjulte under Mere henter den frem igen.' : 'Figuren vises igen.'; mutate(() => model.flag('hidden', hide)); };
  function cutHoles(join = false) {
    if (busy() || contextLost || failed || !result?.exportBlocked) return;
    const names = listNames(), plan = cutPlan(model.selectedObjects(), names);
    const ids = join ? plan.joinIds : plan.ids;
    if (!ids) { status(plan.hint); return; }
    const holes = ids.filter(id => model.object(id).mode === 'hole').length;
    const where = plan.confirmedTargets.map(id => names.get(id)).join(', ');
    notice = `${holes === 1 ? 'Hullet er skåret ud' : 'Hullerne er skåret ud'} i ${where}. Vælg Del op for at redigere delene igen.`;
    mutate(() => {
      const selected = model.selected, steps = model.undoStack.length;
      model.selected = ids; model.group();
      if (model.undoStack.length > steps) model.undoStack.at(-1).selected = selected;
    });
  }
  $('cut-holes').onclick = () => { cutHoles(); $('workspace').focus({preventScroll: true}); };
  function removeSelection() {
    if (model.selectedObjects().some(node => node.locked)) { status('En låst figur kan ikke slettes. Lås den op først.'); }
    const gone = model.selectedObjects().filter(node => !node.locked); if (!gone.length) return;
    notice = `${gone.length === 1 ? name(gone[0]) : gone.length + ' figurer'} er slettet. Tryk på Fortryd, hvis det var en fejl.`; mutate(() => model.remove());
  }
  let lastCopy = null;
  const noHoleCopyRoom = count => `Der er ikke plads til en kopi af ${count === 1 ? 'hullet' : 'hullerne'} på samme plade.`;
  const copiedHolesNotice = count => count === 1 ? 'Kopien er sat på samme plade som hullet.' : 'Hullerne er kopieret på samme plade.';
  function duplicate() {
    if (busy()) { later(duplicate); return; }
    const chosen = [...model.selected], step = repeatCopyMatrix(model, chosen, lastCopy);
    const context = !step && holeCopyContext(chosen);
    const holeOffset = context && findHoleCopyOffset(context);
    if (context && !holeOffset) { status(noHoleCopyRoom(chosen.length)); return; }
    copySelection();
    mutate(() => {
      if (step) model.pasteTransformed(step);
      else model.paste(holeOffset || copyOffset());
      if (!model.selected.length || model.selected.every(id => chosen.includes(id))) return;
      lastCopy = {sources: chosen, copies: [...model.selected],
        matrices: model.selectedObjects().map(node => objectMatrix(node).toArray()), repeated: Boolean(step)};
      if (step) notice = 'Kopien gentager det, du gjorde med den forrige kopi.';
      else if (context) notice = copiedHolesNotice(chosen.length);
    });
  }
  function pasteCopy() {
    if (busy()) { later(pasteCopy); return; }
    const {holeTarget, box} = model.clipboard || {};
    if (!holeTarget || !box) { mutate(() => model.paste(copyOffset())); return; }
    const context = holeContextForPlate(holeTarget, box);
    if (!context) { status('Kopien kan ikke placeres på den oprindelige plade. Kopiér fra en synlig plade.'); return; }
    const count = model.clipboard.roots.length;
    const offset = findHoleCopyOffset(context);
    if (!offset) { status(noHoleCopyRoom(count)); return; }
    notice = copiedHolesNotice(count);
    mutate(() => model.paste(offset));
  }
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
  $('solid').onclick = () => setMode('solid'); $('hole').onclick = () => setMode('hole');
  $('group').onclick = () => {
    if (busy() || $('group').disabled) return;
    if (model.selectedObjects().some(node => node.mode === 'hole' && !node.hidden)) { cutHoles(true); return; }
    notice = 'Figurerne er smeltet sammen.'; mutate(() => model.group());
  };
  $('part-split').onclick = () => {
    if (busy() || $('part-split').disabled) return;
    const node = model.selectedObjects()[0], layout = legacyPieces.get(assets.get(node.asset));
    const restored = layout ? gamePieceChildren(layout, assets, node.size, node.color) : null;
    const count = (restored || node.children).length;
    notice = `Figuren er delt i ${count} dele. Vælg en del for at ændre den.`;
    mutate(() => model.ungroup(restored));
    $('workspace').focus({preventScroll: true});
  };
  dropToPlane.onclick = () => {
    const box = view.bounds();
    if (box) move(0, 0, (workplane?.height ?? 0) - box.min.z);
  };
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
  function editHoleOffset(input, hole, plate, valueText) {
    if (busy()) { later(() => editHoleOffset(input, hole, plate, valueText)); return; }
    if (contextLost || failed || model.selected.length !== 1 || model.selected[0] !== hole) return;
    const node = model.object(hole);
    const reference = holeOffsetReference(node, view.localState(hole));
    if (!reference || reference.plate !== plate || node.locked) {
      status('Kanten har ændret sig. Vælg hullet igen for at se den aktuelle placering.'); return;
    }
    const axis = Number(input.dataset.holeOffset), start = reference.bounds.min.getComponent(axis);
    const maximum = reference.bounds.max.getComponent(axis) - start;
    const value = /^[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(valueText) ? Number(valueText.replace(',', '.')) : NaN;
    if (!Number.isFinite(value)) { invalid(input, 'Skriv et tal i mm.'); return; }
    if (value < 0 || value > round(maximum)) {
      invalid(input, `Skriv fra 0 til ${offsetNumber(maximum)} mm, så midten er på kassen.`); return;
    }
    const shift = start + Math.min(value, maximum) - reference.center.getComponent(axis);
    if (Math.abs(shift) < 1e-6) { renderUI(); return; }
    const delta = [0, 0, 0]; delta[axis] = shift;
    notice = `Hullets midte er nu ${offsetNumber(value)} mm ${axis === 0 ? 'fra venstre kant' : 'fra forkanten'}.`;
    mutate(() => model.moveEach([[hole, delta]]));
  }
  for (const input of offsetInputs) {
    input.addEventListener('change', () => {
      const reference = shownOffset;
      input.dataset.typing = 'false';
      if (reference) editHoleOffset(input, reference.hole, reference.plate, input.value.trim());
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        input.blur();
        if (!input.closest('[hidden]')) input.focus({preventScroll: true});
      }
      const key = event.key.toLowerCase();
      if ((event.ctrlKey || event.metaKey) && ['z', 'y'].includes(key) && !typingValue(input)) {
        event.preventDefault();
        historyStep(key === 'y' || event.shiftKey);
      }
    });
  }
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
  // The local error belongs to the field and the selected figure.
  function clearFieldError() {
    if (!fieldError) return;
    fieldError.input.removeAttribute('aria-invalid');
    if (fieldError.description) fieldError.input.setAttribute('aria-describedby', fieldError.description);
    else fieldError.input.removeAttribute('aria-describedby');
    $('field-error').hidden = true; fieldError = null;
  }
  function invalid(input, message) {
    clearFieldError();
    fieldError = {input, id: model.selected[0], description: input.getAttribute('aria-describedby')};
    const error = $('field-error');
    // Place the live explanation in the same control section as the invalid field.
    const offsets = input.closest('.hole-offsets');
    if (offsets) offsets.append(error);
    else (input.closest('fieldset, details') || input.closest('label') || input).after(error);
    error.textContent = message; error.hidden = false;
    status(message); input.setAttribute('aria-invalid', 'true');
    input.setAttribute('aria-describedby', [fieldError.description, error.id].filter(Boolean).join(' '));
    input.blur(); renderUI(); input.focus(); input.select();
  }
  $('selection-controls').addEventListener('input', event => { if (fieldError?.input === event.target) clearFieldError(); });
  $('facets').addEventListener('change', event => {
    if (model.selected.length !== 1) return;
    const [id] = model.selected, value = Number(event.target.value);
    mutate(() => model.setFacets(id, value));
  });
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
    clearTimeout(textPause); textPause = 0;
    const node = model.selectedObjects()[0]; if (model.selected.length !== 1 || node?.kind !== 'text') return;
    const text = $('text-value').value, height = Number($('text-height').value), depth = Number($('text-depth').value);
    commitTextFields(node.id, {text, height, depth});
  }
  function commitTextFields(id, values) {
    if (busy()) { later(() => commitTextFields(id, values)); return; }
    const node = model.object(id); if (node?.kind !== 'text' || node.locked) return;
    const {text, height, depth} = values;
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
  textFont.addEventListener('change', () => {
    const [node] = model.selectedObjects(), font = textFont.value;
    if (model.selected.length !== 1 || node?.kind !== 'text' || node.locked || !FONT_BY_KEY.has(font)) return;
    // Finish pending typing before changing the font so each edit can be undone on its own.
    if (textPause) updateText();
    mutate(() => model.setText(node.id, {font}));
  });
  function clearTextSpace(plate, cutter, matrix) {
    if (!isRoundHole(cutter, matrix) || !isVertical(matrix)) return null;
    const size = plate.getSize(new THREE.Vector3()), center = new THREE.Vector3().setFromMatrixPosition(matrix);
    const scale = new THREE.Vector3().setFromMatrixScale(matrix);
    const radius = cutter.size[0] * scale.x / 2, halfHeight = cutter.size[2] * scale.z / 2;
    if (center.z - halfHeight > plate.min.z + 1e-5 || center.z + halfHeight < plate.max.z - 1e-5) return null;
    if (['x', 'y'].some(axis => center[axis] - radius <= plate.min[axis] || center[axis] + radius >= plate.max[axis])) return null;

    // Only reserve a strip when the hole is visibly near an end of the long side.
    const axis = size.x >= size.y ? 'x' : 'y';
    if (Math.min(center[axis] - plate.min[axis], plate.max[axis] - center[axis]) > size[axis] / 3) return null;
    return roomBesideHole(plate, center, radius, axis);
  }
  function roomBesideHole(plate, center, radius, axis) {
    const size = plate.getSize(new THREE.Vector3());
    const toStart = center[axis] - plate.min[axis], toEnd = plate.max[axis] - center[axis];
    const margin = Math.max(.6, Math.min(size.x, size.y) * .05), gap = Math.max(1, margin);
    const room = plate.clone();
    for (const key of ['x', 'y']) { room.min[key] += margin; room.max[key] -= margin; }
    if (toStart <= toEnd) room.min[axis] = center[axis] + radius + gap;
    else room.max[axis] = center[axis] - radius - gap;
    if (room.max.x <= room.min.x || room.max.y <= room.min.y) return null;
    return {room, center, keepClear: radius + gap};
  }
  function textFit(box, room, proportion = 1) {
    const factor = Math.min(1, proportion * (room.max.x - room.min.x) / (box.max.x - box.min.x),
      proportion * (room.max.y - room.min.y) / (box.max.y - box.min.y));
    return {factor, center: box.getCenter(new THREE.Vector3()), middle: room.getCenter(new THREE.Vector3())};
  }
  function textFitMatrix({factor, center, middle}) {
    return new THREE.Matrix4().makeTranslation(middle.x, middle.y, 0)
      .multiply(new THREE.Matrix4().makeScale(factor, factor, 1))
      .multiply(new THREE.Matrix4().makeTranslation(-center.x, -center.y, 0));
  }
  function simpleTextPlate(part, bounds) {
    const extraCuts = (result?.uncombinedHoles || []).filter(id => holeTargets(id).includes(part.id));
    if (part.kind === 'box' && !part.params?.round && alongWorldAxes(objectMatrix(part))) {
      if (!extraCuts.length) return {hole: null};
      if (extraCuts.length !== 1) return null;
      const cutter = model.object(extraCuts[0]), hole = clearTextSpace(bounds, cutter, objectMatrix(cutter));
      return hole ? {hole} : null;
    }
    // A cut joins the original box and its cutter. Check the composed world transforms before using the old hole.
    if (part.kind !== 'group' || extraCuts.length || part.children.length !== 2) return null;
    const children = part.children.map(id => model.object(id));
    const base = children.find(node => node.kind === 'box' && node.mode !== 'hole' && !node.params?.round && !node.hidden);
    const cutter = children.find(node => node.mode === 'hole' && !node.hidden);
    if (!base || !cutter) return null;
    const parent = objectMatrix(part);
    if (!alongWorldAxes(parent.clone().multiply(objectMatrix(base)))) return null;
    const hole = clearTextSpace(bounds, cutter, parent.multiply(objectMatrix(cutter)));
    return hole ? {hole} : null;
  }
  function textOverhang(node, strict = false) {
    if (node?.kind !== 'text' || node.mode === 'hole' || node.hidden || node.locked || (strict && !alongWorldAxes(objectMatrix(node)))) return null;
    const box = view.bounds([node.id]); if (!box) return null;
    const center = box.getCenter(new THREE.Vector3());
    const acrossCenter = other => other.min.x <= center.x && other.max.x >= center.x && other.min.y <= center.y && other.max.y >= center.y;
    const supports = [];
    for (const id of model.data.roots) {
      const other = model.object(id);
      if (id === node.id || other.mode === 'hole' || other.hidden) continue;
      const bounds = view.bounds([id]);
      if (!bounds || Math.abs(bounds.max.z - (box.min.z + .5)) >= .6) continue;
      const touchesFootprint = bounds.min.x < box.max.x && bounds.max.x > box.min.x && bounds.min.y < box.max.y && bounds.max.y > box.min.y;
      if (strict ? touchesFootprint : acrossCenter(bounds)) supports.push({other, bounds});
    }
    if (!supports.length || (strict && supports.length !== 1)) return null;
    const [{other, bounds: support}] = supports;
    // The editor action needs one known flat surface beneath the text. Keep the existing print check for other supports.
    if (strict && (!acrossCenter(support) || support.min.z > box.min.z || support.max.z <= box.min.z + .01)) return null;
    const plate = strict ? simpleTextPlate(other, support) : {hole: null};
    if (!plate) return null;
    const contained = box.min.x >= support.min.x - .5 && box.max.x <= support.max.x + .5 &&
      box.min.y >= support.min.y - .5 && box.max.y <= support.max.y + .5;
    const hole = plate.hole;
    const distance = hole && Math.hypot(...['x', 'y'].map(axis => Math.max(box.min[axis] - hole.center[axis], hole.center[axis] - box.max[axis], 0)));
    if (contained && (!hole || distance >= hole.keepClear - 1e-5)) return null;
    return {...textFit(box, hole?.room || support, hole ? 1 : .9), supportId: other.id, clearsHole: Boolean(hole)};
  }
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
      const node = model.object(id), overhang = textOverhang(node); if (!overhang) continue;
      const {factor: fit, middle, center} = overhang, shift = [round(middle.x - center.x), round(middle.y - center.y), 0];
      issues.push({text: `Teksten "${node.params.text}" stikker ud over kanten af figuren under den.`, fix: fit < .999 ? 'Gør teksten mindre' : 'Flyt teksten ind', action: () => {
        const state = view.localState(id); if (!state) return; model.selected = [id]; renderUI(); const b = state.box;
        if (fit < .999) editSelection({type: 'scale', id, factor: [fit, fit, 1], pivot: [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, b.min[2]], lift: 0});
        // Scaled about its own centre, the text keeps that centre; then it moves to the middle of its part.
        if (shift[0] || shift[1]) mutate(() => model.moveEach([[id, shift]]));
      }});
    }
    return issues;
  }
  function fileName(extension = 'stl') {
    const text = model.data.objects.find(node => node.kind === 'text' && !node.hidden && node.mode !== 'hole')?.params.text || modelName;
    const clean = text.normalize('NFC').replace(/[^\p{L}\p{N} _-]/gu, '').trim().replace(/\s+/g, '-').slice(0, 40);
    return (clean || 'min-model') + '.' + extension;
  }
  function downloadModel(bytes, extension, size) {
    const url = URL.createObjectURL(new Blob([bytes], {type: `model/${extension}`}));
    const link = document.createElement('a'); link.href = url; link.download = fileName(extension); link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status(`Din fil er hentet: ${link.download} (${size.map(mm).join(' × ')} mm).`);
  }
  function saveStl() {
    try { const buffer = exportBinaryStl(result); downloadModel(buffer, 'stl', validateSTL(buffer).size); }
    catch (error) { status('STL kunne ikke hentes: ' + error.message); }
  }
  function save3mf() {
    try {
      const solids = result.meshes.filter(mesh => mesh.mode !== 'hole');
      const independent = solids.length === 1 || result.looseRoots?.length === solids.length;
      const meshes = independent ? solids.map(mesh => ({...mesh, name: listName(mesh.id), color: view.colorOf(mesh.id)}))
        : [{...result.merged, name: modelName}];
      const size = result.bounds.max.map((value, i) => value - result.bounds.min[i]);
      downloadModel(export3mf(meshes), '3mf', size);
    } catch (error) { status('3MF kunne ikke hentes. ' + error.message); }
  }
  // The name dialog and the print check lead on to either a download or a print.
  let afterCheck = saveStl;
  function startExport(next) {
    if (!result?.merged || result.exportBlocked || rendered !== sequence) return;
    afterCheck = next;
    if (!modelName && !model.data.objects.some(node => node.kind === 'text' && !node.hidden && node.mode !== 'hole')) {
      // A word written in the model, such as the pupil's name on a tag, is offered as the file name; Enter takes it.
      const words = model.data.objects.filter(node => node.kind === 'text' && node.params?.text?.trim()).map(node => node.params.text.trim());
      $('name-submit').textContent = next === saveStl ? 'Hent STL' : next === save3mf ? 'Hent 3MF' : 'Videre';
      $('model-name').value = words.length ? words[0].slice(0, 40) : ''; $('model-name-error').hidden = true; $('model-name').removeAttribute('aria-invalid'); $('name-dialog').showModal(); $('model-name').focus(); $('model-name').select(); return;
    }
    checkAndSave();
  }
  $('download').onclick = () => startExport(saveStl);
  $('download-3mf').onclick = () => startExport(save3mf);
  $('send-print').onclick = () => startExport(confirmPrint);
  $('name-form').addEventListener('submit', event => {
    event.preventDefault(); const typed = $('model-name').value.trim().slice(0, 40);
    $('model-name-error').hidden = !!typed; $('model-name').toggleAttribute('aria-invalid', !typed);
    if (!typed) { $('model-name').focus(); return; }
    modelName = typed; $('name-dialog').close(); checkAndSave();
  });
  $('name-cancel').onclick = () => $('name-dialog').close();
  function checkAndSave() {
    const issues = printIssues(); if (!issues.length) { afterCheck(); return; }
    const list = $('print-issues'), actions = $('print-actions'); list.replaceChildren(); actions.replaceChildren();
    for (const issue of issues) {
      const item = document.createElement('li'), text = document.createElement('p'); text.textContent = issue.text; item.append(text);
      if (issue.action) { const fix = document.createElement('button'); fix.type = 'button'; fix.textContent = issue.fix; fix.onclick = () => { $('print-check').close(); issue.action(); }; item.append(fix); }
      list.append(item);
    }
    const anyway = document.createElement('button'), back = document.createElement('button'); anyway.type = back.type = 'button';
    anyway.textContent = afterCheck === saveStl ? 'Hent STL alligevel' : afterCheck === save3mf ? 'Hent 3MF alligevel' : 'Send alligevel'; back.textContent = 'Tilbage til modellen'; back.className = 'primary';
    const next = afterCheck; anyway.onclick = () => { $('print-check').close(); next(); }; back.onclick = () => $('print-check').close(); actions.append(anyway, back);
    $('print-check').showModal(); back.focus();
  }
  // Send til print: the same request id is reused while the model is unchanged, so
  // pressing again after a lost answer never prints the model twice.
  let printModel, printRequest, colourLoader, printing = false;
  const printDialog = $('print-dialog'), colourField = $('print-colours');
  const selectedColour = () => colourField.querySelector('input:checked');
  const newRequestId = () => crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(20)), v => v.toString(16).padStart(2, '0')).join('');
  function modelSignature(buffer, name) {
    let hash = 0x811c9dc5; const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i++) { hash ^= bytes[i]; hash = Math.imul(hash, 0x01000193); }
    return `${name}|${bytes.length}|${(hash >>> 0).toString(16)}`;
  }
  function printView({title, message, details = '', error = '', yes = '', no = 'Tilbage til tegningen', busy = false, chooseColour = false}) {
    $('print-title').textContent = title; $('print-message').textContent = message;
    $('print-details').textContent = details; $('print-details').hidden = !details;
    $('print-error').textContent = error; $('print-error').hidden = !error;
    colourField.hidden = !chooseColour;
    $('print-yes').textContent = yes; $('print-yes').hidden = !yes; $('print-yes').disabled = busy;
    $('print-no').textContent = no; $('print-no').disabled = busy;
  }
  function syncPrintButton() {
    $('print-yes').disabled = printing || (!colourField.hidden && !selectedColour());
  }
  function renderPrintColours(colours, selected) {
    const options = [{hex: 'any', name: 'Vælg for mig'}, ...colours];
    $('print-colour-options').replaceChildren(...options.map(({hex, name}) => {
      const label = document.createElement('label'), input = document.createElement('input');
      input.type = 'radio'; input.name = 'print-colour'; input.value = hex;
      input.dataset.name = name; input.checked = hex === selected;
      label.append(input);
      if (hex !== 'any') {
        const swatch = document.createElement('span'); swatch.className = 'print-filament-swatch';
        swatch.style.backgroundColor = hex; swatch.setAttribute('aria-hidden', 'true'); label.append(swatch);
      }
      label.append(name);
      return label;
    }));
    syncPrintButton();
  }
  async function loadPrintColours(preferred) {
    colourLoader?.abort();
    const controller = new AbortController(); colourLoader = controller;
    const hint = $('print-colour-status');
    renderPrintColours([], preferred);
    hint.textContent = 'Henter farver fra skolens printere …';
    try {
      const response = await fetch('/api/print/colours', {cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(6000)])});
      if (!response.ok) throw Error();
      const {colours} = await response.json();
      if (!Array.isArray(colours)) throw Error();
      if (controller !== colourLoader || !printDialog.open) return;
      const available = colours.filter(colour => typeof colour?.hex === 'string' && /^#[0-9A-F]{6}$/.test(colour.hex) && typeof colour.name === 'string' && colour.name.trim());
      const focused = colourField.contains(document.activeElement);
      renderPrintColours(available, selectedColour()?.value ?? preferred);
      if (focused) (selectedColour() || colourField.querySelector('input')).focus();
      hint.textContent = available.length ? 'Farven gælder hele modellen.' : 'Printerne melder ingen farver lige nu. Du kan vælge Vælg for mig.';
    } catch {
      if (controller === colourLoader && printDialog.open) hint.textContent = 'Farverne kunne ikke hentes. Du kan vælge Vælg for mig.';
    }
  }
  function showPrintConfirmation(preferred, error = '') {
    printView({title: 'Send til print', message: `Er din model "${printModel.name.replace(/\.stl$/, '')}" færdig?`,
      details: 'Alle synlige, solide figurer sendes samlet. Printet venter i kø, hvis en passende printer er optaget.',
      yes: 'Ja, send til print', no: 'Nej, tegn videre', chooseColour: true, error});
    void loadPrintColours(preferred);
  }
  function showUnconfirmedPrint() {
    printView({title: 'Printet blev ikke bekræftet', message: 'Der kom ikke svar fra printværkstedet.',
      error: 'Prøv igen med samme farve. 3D-værkstedet genbruger afleveringen, så der kun bestilles ét print.', yes: 'Prøv igen'});
  }
  const printColourNote = request => request.colour === 'any' ? 'Farven vælges i printkøen.' : `Farve: ${request.colourName}.`;
  function showQueuedPrint(request, recheck = false) {
    const waitsForColour = request.waitingFor === 'colour';
    const when = waitsForColour ? 'den valgte farve er tilgængelig igen' :
      request.colour === 'any' ? 'forbindelsen er tilbage' : 'forbindelsen er tilbage, og farven er tilgængelig';
    printView({title: waitsForColour ? 'Printet venter på farven' : 'Printet venter på forbindelse',
      message: `Modellen er gemt på computeren og sendes, når ${when}.`,
      details: printColourNote(request), yes: recheck ? 'Tjek igen' : ''});
  }
  const printStatus = {preparing: 'Din model klargøres til print.', dispatching: 'Din model klargøres til print.', starting: 'Din model klargøres til print.', printing: 'Din model bliver printet.', done: 'Dit print er færdigt.'};
  function confirmPrint() {
    try {
      const buffer = exportBinaryStl(result), name = fileName();
      validateSTL(buffer); printModel = {buffer, name, signature: modelSignature(buffer, name)};
    } catch (error) {
      printView({title: 'Modellen kunne ikke sendes', message: 'Modellen kunne ikke gøres klar til print.', error: error.message});
      printDialog.showModal(); $('print-no').focus(); return;
    }
    const previous = printRequest?.signature === printModel.signature ? printRequest : null;
    printDialog.showModal();
    if (previous?.uncertain) showUnconfirmedPrint();
    else if (previous?.waitingFor) showQueuedPrint(previous, true);
    else showPrintConfirmation(previous?.colour ?? 'any');
    $('print-title').focus();
  }
  async function sendToPrint() {
    if (printing || !printModel) return;
    const {buffer, name, signature} = printModel;
    const choice = selectedColour();
    if (!colourField.hidden) {
      if (!choice) return;
      if (printRequest?.signature !== signature || printRequest.colour !== choice.value) {
        printRequest = {signature, colour: choice.value, colourName: choice.dataset.name, id: newRequestId(), receipt: ''};
      }
    }
    const request = printRequest;
    if (request?.signature !== signature) return;
    colourLoader?.abort(); colourLoader = null;
    printing = request.uncertain = true;
    printView({title: 'Sender din model', message: 'Modellen klargøres og sendes til printværkstedet …', no: 'Vent …', busy: true}); $('print-title').focus();
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch('/api/jobs', {method: 'POST', body: buffer, cache: 'no-store', signal: controller.signal, headers: {'Content-Type': 'application/octet-stream', Accept: 'application/json',
        'X-Filename': encodeURIComponent(name), 'X-Colour': request.colour, 'X-Quality': 'standard', 'X-Request-ID': request.id}});
      let data = null; try { data = await response.json(); } catch {}
      if (response.status === 202 && data?.queued) {
        request.uncertain = false;
        request.waitingFor = data.code === 'colour_unavailable' ? 'colour' : 'connection';
        showQueuedPrint(request);
      } else if (response.ok && typeof data?.id === 'string') {
        const receipt = data.id.slice(0, 8).toUpperCase(), again = request.receipt === receipt;
        request.receipt = receipt; request.uncertain = false; request.waitingFor = null;
        const details = `${printColourNote(request)} Afleveringsnummer: ${receipt}. Sig nummeret til læreren, hvis I skal finde printet.`;
        if (again) printView({title: 'Modellen er allerede sendt', message: 'Der er ikke sendt en ekstra kopi.', details});
        else printView({title: 'Din model er modtaget', message: printStatus[data.status] || (['failed', 'attention', 'cancelled', 'recovering'].includes(data.status) ? 'Indsendelsen er modtaget, men printet kræver eftersyn. Bed en voksen kontrollere printkøen.' : 'Din model er sat i kø. Den klargøres automatisk, når en passende printer er ledig.'),
          details});
        status(`Sendt til print: ${name}. Afleveringsnummer ${receipt}.`);
      } else if (response.status === 409 && data?.code === 'colour_unavailable') {
        request.uncertain = false; request.waitingFor = null;
        showPrintConfirmation(null, data.error || 'Farven er ikke længere tilgængelig. Vælg en anden farve.');
      } else if (response.status >= 500 || response.ok) {
        showUnconfirmedPrint();
      } else {
        request.uncertain = false;
        const message = typeof data?.error === 'string' && data.error ? data.error : 'Printværkstedet afviste modellen.';
        printView({title: 'Printet blev ikke sendt', message: 'Modellen er ikke sendt til print.', error: message, yes: 'Prøv igen'});
      }
    } catch {
      showUnconfirmedPrint();
    } finally {
      clearTimeout(timer); printing = false; $('print-no').disabled = false; syncPrintButton();
      requestAnimationFrame(() => (!colourField.hidden ? $('print-title') : !$('print-yes').hidden ? $('print-yes') : $('print-no')).focus());
    }
  }
  colourField.addEventListener('change', syncPrintButton);
  $('print-yes').onclick = () => sendToPrint();
  $('print-no').onclick = () => { if (!printing) printDialog.close(); };
  printDialog.addEventListener('cancel', event => { if (printing) event.preventDefault(); });
  printDialog.addEventListener('close', () => {
    if (printDialog.open) return;
    colourLoader?.abort(); colourLoader = null; printModel = null;
  });
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
    $('guide').hidden = !guide; $('empty').hidden = !!guide || model.data.roots.length > 0; if (!guide) return;
    const step = guide.steps[guideStep];
    if (step.target?.startsWith('[data-add')) showLibrary(false);
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
  $('ideas').onclick = () => $('ideas-dialog').showModal();
  const focusHelpMenu = () => $('help-menu').querySelector('summary').focus({preventScroll: true});
  $('guide-close').onclick = () => { guide = null; showGuide(); focusHelpMenu(); };
  $('guide-back').onclick = () => { guideStep = Math.max(0, guideStep - 1); showGuide(); };
  $('guide-next').onclick = () => {
    // Pressing Next before a step is done gives one friendly reminder; a second press moves on anyway.
    const step = guide.steps[guideStep];
    if (step.done && !step.done(guideContext()) && !guideNudged) { guideNudged = true; $('guide-nudge').hidden = false; return; }
    if (guideStep < guide.steps.length - 1) { guideStep++; showGuide(); } else { guide = null; showGuide(); focusHelpMenu(); status('Godt gået! Prøv en ny idé, eller byg videre på din egen.'); }
  };
  // A new idea starts on an empty plate if the pupil wants; building on keeps the parts.
  let pendingGuide = null;
  function startGuide(item) {
    if (model.data.roots.length) { pendingGuide = item; $('clear-dialog').showModal(); return; }
    openGuide(item);
  }
  function openGuide(item) {
    guide = item; guideStep = 0; showGuide(); $('guide-close').focus({preventScroll: true});
  }
  $('clear-yes').onclick = () => { $('clear-dialog').close(); setWorkplane(null); gamePieceEditor.reset(); notice = 'Pladen er tom. Fortryd henter dine figurer tilbage.'; mutate(() => { model.selected = [...model.data.roots]; model.remove(); }); openGuide(pendingGuide); };
  $('clear-no').onclick = () => { $('clear-dialog').close(); openGuide(pendingGuide); };
  $('select-all').onclick = () => { model.selected = model.data.roots.filter(id => !model.object(id)?.hidden); renderUI(); manyTip(); };
  $('help-open').onclick = () => { $('help').showModal(); $('help').scrollTop = 0; $('help').querySelector('h2').focus(); };
  document.addEventListener('keydown', event => {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.closest('dialog')) return;
    // Alt+Left belongs to browser navigation, including the kiosk's Til start.
    if (event.altKey) return;
    if (event.key === 'Escape') {
      const menu = event.target.closest(openMenu) || document.querySelector(openMenu);
      if (menu) { event.preventDefault(); closeMenu(menu); return; }
    }
    if (event.key === 'Escape' && $('pan-mode').getAttribute('aria-pressed') === 'true') { setPan(false); return; }
    if (event.key === 'Escape') { const dragging = Boolean(view.drag?.active); view.cancelDrag(true); if (!dragging && model.selected.length) { model.selected = []; renderUI(); } return; }
    if (view.drag || view.pendingPreview) return;
    const command = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
    if (command && ['z', 'y', 'd', 'c', 'v', 'x', 'g', 'a'].includes(key)) {
      event.preventDefault(); if (key === 'z' || key === 'y') historyStep(key === 'y' || event.shiftKey);
      if (key === 'd') duplicate(); if (key === 'c') copySelection(); if (key === 'v') pasteCopy(); if (key === 'x') { copySelection(); mutate(() => model.remove()); }
      if (key === 'g' && !$('group').disabled) $('group').click(); if (key === 'a' && !$('select-all').disabled) $('select-all').click(); return;
    }
    if (event.target.closest(openMenu)) return;
    if (key === 'w' && !command && !event.shiftKey && !event.isComposing && !event.target.isContentEditable) {
      if (event.repeat || (!workplane && (buildOnTop.hidden || buildOnTop.disabled))) return;
      event.preventDefault();
      if (workplane) clearWorkplane();
      else buildOnTop.click();
      return;
    }
    if (event.key === 'Delete') { event.preventDefault(); removeSelection(); }
    if (event.key.toLowerCase() === 'r' && !event.altKey && model.selected.length) { event.preventDefault(); setRotate($('rotate-mode').getAttribute('aria-pressed') !== 'true'); return; }
    const step = event.shiftKey ? 10 : 1, {right, away} = view.screenDirections();
    const arrows = {ArrowLeft: [-right[0] * step, -right[1] * step, 0], ArrowRight: [right[0] * step, right[1] * step, 0], ArrowUp: [away[0] * step, away[1] * step, 0], ArrowDown: [-away[0] * step, -away[1] * step, 0], PageUp: [0, 0, step], PageDown: [0, 0, -step]};
    if (arrows[event.key] && model.selected.length) { event.preventDefault(); move(...arrows[event.key]); }
  });
  window.addEventListener('beforeunload', event => { if (!managed && model.dirty) { event.preventDefault(); event.returnValue = ''; } });
  if (managed) {
    const projectActions = $('managed-project-actions');
    const inline = {
      actions:projectActions, save:$('project-save'), open:$('project-open'), status:$('project-save-status'),
      get active() { return !projectActions.hidden; },
      set active(value) { projectActions.hidden = !value; }
    };
    inline.status.textContent = 'Projekter gemmes på denne computer.';
    // The bridge already owns the copy dialog, also when its legacy bar is hidden.
    $('project-copy').onclick = () => $('elevhub-project-copy').click();
    const pendingImports = new WeakMap();
    const settle = async () => {
      const until = Date.now() + 20000;
      while (busy() || queued.length) {
        if (Date.now() > until) throw Error('Modellen beregnes stadig. Vent et øjeblik, og prøv igen.');
        await new Promise(resolve => setTimeout(resolve, 40));
      }
      if (failed) throw Error('Modellen kunne ikke beregnes. Ret fejlen, før du gemmer.');
    };
    const canAutoSave = () => projectReady && !printing && !busy() && !queued.length && !mechanicalEditors.pending
      && !view.drag && !view.pendingPreview && !contextLost && !failed;
    const prepare = async (options = {}) => {
      if (options.background) {
        if (!canAutoSave()) throw Error('Modellen redigeres eller beregnes stadig. Vent et øjeblik.');
        return;
      }
      if (printing) throw Error('Vent, til afleveringen til print er færdig.');
      clearTimeout(textPause);
      if (document.activeElement?.closest('#text-controls')) updateText();
      mechanicalEditors.flush();
      view.cancelDrag(true);
      await settle();
    };
    const readProject = async blob => {
      if (!blob || blob.size > 50 * 1024 * 1024) throw Error('Vælg et 3D-projekt på højst 50 MB.');
      if (pendingImports.has(blob)) return pendingImports.get(blob);
      const project = decodeProject(await blob.text());
      await findLegacyGamePieces(project, legacyPieces);
      return project;
    };
    const replaceScene = (scene, nextAssets, name = '') => {
      projectGeneration++;
      setWorkplane(null);
      gamePieceEditor.reset();
      resetShapeCategory();
      clearTimeout(textPause); queued = []; view.cancelDrag(true);
      worker.postMessage({type:'reset'});
      Object.assign(model, new SceneModel(), {data:scene});
      assets.clear(); for (const [id, asset] of nextAssets) assets.set(id, asset); sentAssets.clear();
      modelName = String(name).slice(0, 100); printRequest = null;
      guide = pendingGuide = null; showGuide(); lastColor = null; stickyError = notice = lastWarning = '';
      frameFirst = Boolean(scene.roots.length); focusText = false; panelFor = null;
      positionPreference = null; positionDetails.open = false; measuredPlates.clear(); openedHolePositions.clear();
      for (const modal of document.querySelectorAll('dialog[open]')) if (modal.id !== 'elevhub-project-dialog') modal.close();
      evaluate();
    };
    window.ElevhubProjectAdapter = {
      type:'3dprint', extension:'.mkv3d', name:() => modelName,
      setName:name => { modelName = String(name).slice(0, 100); },
      inline,
      signature:() => model.snapshot(), hasContent:() => Boolean(model.data.roots.length),
      canAutoSave,
      prepareSave:prepare, prepareFinish:prepare,
      export:async options => { await prepare(options); return new Blob([encodeProject(model.data, projectAssets())], {type:'application/json'}); },
      validateImport:async blob => { pendingImports.set(blob, await readProject(blob)); },
      import:async (blob, name) => {
        const next = await readProject(blob); await prepare();
        replaceScene(next.scene, next.assets, name); await settle(); model.dirty = false; projectReady = true;
      },
      reset:async () => {
        if (printing) throw Error('Vent, til afleveringen til print er færdig.');
        replaceScene(new SceneModel().data, new Map()); await settle(); projectReady = true;
      }
    };
    for (const note of document.querySelectorAll('[data-project-save-note]')) note.textContent = 'Dit navngivne projekt gemmes automatisk på denne computer. Vent på Gemt, før du slukker. Åbn projektet igen fra Find mit projekt. Hent STL og Hent 3MF laver filer til print.';
  }
  evaluate();
}
