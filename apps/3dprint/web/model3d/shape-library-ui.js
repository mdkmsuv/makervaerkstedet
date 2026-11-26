import {SHAPE_LIBRARY} from './shape-library.js';
import {CATALOG_RECIPES} from './catalog-recipes.js';
import {NATIVE_PROFILES, profileValues} from './native-profiles.js';
import {spurGearLimits} from './spur-gear.js';
import {ROD_LENGTH, THREAD_SIZES} from './threaded-nut.js';
import {mechanicalMeshes} from './mechanical-parts.js';
import {thumbnailColumns, thumbnailRows, thumbnailSlots} from './form-thumbnail-slots.js';

const byId = id => document.getElementById(id);
const mm = value => value.toLocaleString('da-DK', {maximumFractionDigits: 2});
function addThumbnail(button, key) {
  const slot = thumbnailSlots.get(key);
  if (slot === undefined) return;
  const art = document.createElement('span');
  art.className = 'shape-thumbnail';
  art.dataset.formThumbnail = key;
  art.setAttribute('aria-hidden', 'true');
  art.style.backgroundSize = `${thumbnailColumns * 100}% ${thumbnailRows * 100}%`;
  art.style.backgroundPosition = `${(slot % thumbnailColumns) * 100 / (thumbnailColumns - 1)}% ${Math.floor(slot / thumbnailColumns) * 100 / (thumbnailRows - 1)}%`;
  button.prepend(art);
}

const thumbnails = {
  washer: 'M8 24a22 15 0 1 0 44 0 22 15 0 1 0-44 0Zm13 0a9 6 0 1 1 18 0 9 6 0 1 1-18 0Z',
  nut: 'M7 24 18 7h24l11 17-11 17H18Zm15 0a8 8 0 1 1 16 0 8 8 0 1 1-16 0Z',
  rod: 'M8 31 42 6l9 1 4 6-1 8-34 25-9-1-4-6Zm5 4 3 4 4-1-4-5Zm7-5 4 5 4-3-4-5Zm8-6 4 5 4-3-4-5Zm8-6 4 5 4-3-4-5Zm8-6 4 5 2-3-2-3Z',
  spacer: 'M15 12a15 8 0 1 1 30 0v24a15 8 0 0 1-30 0Zm9 0a6 3 0 1 0 12 0 6 3 0 1 0-12 0Z',
  gear: 'M25 3h10l1 7 6 3 6-3 6 8-5 5v4l5 5-6 8-6-3-6 3-1 6H25l-1-6-6-3-6 3-6-8 5-5v-4l-5-5 6-8 6 3 6-3Zm-2 21a7 7 0 1 1 14 0 7 7 0 1 1-14 0Z',
  'game-piece': 'M30 3a9 9 0 0 1 4 17l6 13c6 1 10 4 10 8v3H10v-3c0-4 4-7 10-8l6-13a9 9 0 0 1 4-17Z',
  cup: 'M9 8h32v7h6a9 9 0 0 1 0 18h-7l-2 8H15Zm33 12-1 8h6a4 4 0 0 0 0-8Z',
  'toilet-roll': 'M8 14a21 9 0 1 1 42 0v20c0 5-9.4 9-21 9C17.9 43 8 39 8 34Zm8 0a13 5 0 1 0 26 0 13 5 0 1 0-26 0Zm26 12v8h3v-9Zm-21 4a20 7 0 0 0 14 0v2a20 7 0 0 1-14 0Z',
  phone: 'M21 2h18a5 5 0 0 1 5 5v34a5 5 0 0 1-5 5H21a5 5 0 0 1-5-5V7a5 5 0 0 1 5-5Zm-1 10v27h20V12Zm7-5v2h6V7Z',
  button: 'M9 24a21 20 0 1 0 42 0 21 20 0 1 0-42 0Zm12-6a3 3 0 1 0 6 0 3 3 0 1 0-6 0Zm12 0a3 3 0 1 0 6 0 3 3 0 1 0-6 0Zm-12 12a3 3 0 1 0 6 0 3 3 0 1 0-6 0Zm12 0a3 3 0 1 0 6 0 3 3 0 1 0-6 0Z',
};

function presetButton(preset) {
  const button = document.createElement('button');
  button.type = 'button';
  button.draggable = true;
  button.dataset.preset = preset.id;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 60 48');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', preset.icon || thumbnails[preset.id]);
  path.setAttribute('fill-rule', 'evenodd');
  svg.append(path);
  const label = document.createElement('span');
  label.className = 'shape-name';
  if (preset.id === 'spacer') label.append('Afstands', document.createElement('wbr'), 'stykke');
  else label.textContent = preset.label;
  button.append(svg, label);
  addThumbnail(button, preset.id);
  const caption = SHAPE_LIBRARY.everyday.includes(preset) ? preset.size.map(mm).join(' × ') + ' mm' : '';
  if (caption) {
    const detail = document.createElement('span');
    detail.id = 'preset-detail-' + preset.id;
    detail.className = 'preset-detail';
    detail.textContent = caption;
    button.setAttribute('aria-label', preset.label);
    button.setAttribute('aria-describedby', detail.id);
    button.append(detail);
  }
  return button;
}

function setupPartEditor(id, load, refresh, build, apply, generate = mechanicalMeshes[id]) {
  const field = key => byId(id + '-' + key);
  const panel = field('editor'), form = field('form'), error = field('error');
  let currentId = null, loaded = '', committing = '', ready = false, editable = false;
  let pending = false, timer = 0;
  const clearError = () => {
    error.hidden = true;
    for (const input of form.querySelectorAll('[aria-invalid]')) {
      input.removeAttribute('aria-invalid');
      input.removeAttribute('aria-errormessage');
    }
  };
  const showError = (message, input) => {
    error.textContent = message; error.hidden = false;
    if (input) {
      input.setAttribute('aria-invalid', 'true');
      input.setAttribute('aria-errormessage', error.id);
    }
  };
  function stopTimer() { clearTimeout(timer); timer = 0; }
  function flush() {
    stopTimer();
    if (!currentId || !ready || !editable || !pending) return;
    pending = false;
    clearError();
    const result = build();
    if (result.message) { showError(result.message, result.input); return; }
    const key = JSON.stringify(result.settings);
    if (key === loaded) return;
    committing = key;
    try {
      const shape = generate(result.settings);
      if (!shape) { showError('Figuren kunne ikke beregnes. Kontrollér målene.'); return; }
      const message = apply(currentId, result.settings, shape);
      if (message) showError(message);
    } finally { committing = ''; }
  }
  form.oninput = event => {
    if (event.target.type !== 'number' || !editable) return;
    clearError(); refresh();
    pending = true;
    stopTimer(); timer = setTimeout(flush, 400);
  };
  form.onchange = event => {
    if (!editable || (event.target.type === 'number' && !pending)) return;
    refresh(); pending = true; flush();
  };
  form.onkeydown = event => {
    if (event.key === 'Enter' && event.target.type === 'number') { event.preventDefault(); flush(); }
  };
  form.onsubmit = event => { event.preventDefault(); flush(); };
  function render(node, canEdit, canApply) {
    panel.hidden = !node;
    const nodeId = node?.id ?? null, changed = nodeId !== currentId;
    const becameDisabled = editable && !canEdit;
    currentId = nodeId; ready = canApply; editable = canEdit;
    const settings = node?.generator.settings;
    const key = settings ? JSON.stringify(settings) : '';
    if (changed || becameDisabled || (loaded !== key && key !== committing)) {
      stopTimer(); pending = false; clearError();
      if (settings) { load(settings); refresh(); }
    }
    for (const input of form.querySelectorAll('input, select')) input.disabled = !editable;
    loaded = key;
    if (pending && !timer && ready && editable) queueMicrotask(flush);
  }
  return {render, flush, get pending() { return pending; }};
}

function setupGear(apply) {
  const fields = Object.fromEntries(['teeth', 'module', 'pressure', 'backlash', 'thickness', 'bore'].map(key => [key, byId('gear-' + key)]));
  const load = values => {
    for (const [key, input] of Object.entries(fields)) input.value = values[key === 'pressure' ? 'pressureAngle' : key];
  };
  const read = () => ({
    teeth: fields.teeth.valueAsNumber, module: fields.module.valueAsNumber, pressureAngle: Number(fields.pressure.value),
    backlash: fields.backlash.valueAsNumber, thickness: fields.thickness.valueAsNumber, bore: fields.bore.valueAsNumber,
  });
  const limitsText = byId('gear-dimensions');
  const refresh = () => {
    const limits = spurGearLimits(read());
    if (limits) {
      fields.teeth.min = limits.minTeeth; fields.teeth.max = limits.maxTeeth;
      fields.backlash.max = limits.maxBacklash; fields.bore.max = limits.maxBore;
    }
    limitsText.textContent = limits?.pitchDiameter
      ? `Delingsdiameter ${mm(limits.pitchDiameter)} mm · Yderdiameter ${mm(limits.outsideDiameter)} mm`
      : '';
  };
  const build = () => {
    const values = read(), limits = spurGearLimits(values);
    if (!limits) return {input: fields.module, message: 'Vælg et modul mellem 0,5 og 4 mm.'};
    if (!Number.isInteger(values.teeth) || values.teeth < limits.minTeeth || values.teeth > limits.maxTeeth) {
      return {input: fields.teeth, message: `Vælg ${limits.minTeeth}–${limits.maxTeeth} hele tænder med dette modul og denne trykvinkel.`};
    }
    if (!(values.backlash >= 0 && values.backlash <= limits.maxBacklash)) {
      return {input: fields.backlash, message: `Vælg et tandspillerum mellem 0 og ${mm(limits.maxBacklash)} mm.`};
    }
    if (!(values.thickness >= limits.minThickness && values.thickness <= limits.maxThickness)) {
      return {input: fields.thickness, message: 'Vælg en tykkelse mellem 2 og 20 mm.'};
    }
    if (!(values.bore === 0 || (values.bore >= limits.minBore && values.bore <= limits.maxBore))) {
      const safeMax = Math.floor(limits.maxBore * 100) / 100;
      return {input: fields.bore, message: `Skriv 0 for intet akselhul, eller vælg et mål fra ${mm(limits.minBore)} til ${mm(safeMax)} mm.`};
    }
    return {settings: values};
  };
  return setupPartEditor('gear', load, refresh, build, apply);
}

function setupThreadedPart(kind, apply) {
  const nut = kind === 'nut';
  const field = key => byId(kind + '-' + key);
  const diameter = field('diameter'), pitch = field('pitch'), hand = field('hand');
  const sizeField = nut ? 'thickness' : 'length';
  const dimension = field(sizeField), clearance = field('clearance'), lying = field('lying');
  const sizes = new Map(THREAD_SIZES.map(size => [String(size.diameter), size]));
  diameter.replaceChildren(...THREAD_SIZES.map(({diameter: d}) => new Option('M' + d, d)));
  diameter.value = '8';
  const setSize = () => {
    const size = sizes.get(diameter.value);
    pitch.replaceChildren(...size.pitches.map((value, index) => new Option(`${mm(value)} (${index === 0 ? 'grov' : 'fin'})`, value)));
    if (nut) dimension.value = size.thickness;
  };
  const load = values => {
    diameter.value = values.diameter;
    setSize();
    pitch.value = values.pitch;
    hand.value = values.hand;
    dimension.value = values[sizeField];
    if (nut) clearance.value = values.clearance;
    else lying.checked = values.lying === true;
  };
  const refresh = () => {
    const p = Number(pitch.value);
    if (nut) dimension.min = Math.max(2, 2 * p);
    field('dimensions').textContent = `${nut ? 'Til gevind M' : 'M'}${diameter.value} × ${mm(p)} · ${hand.value === 'left' ? 'venstregevind' : 'højregevind'}`;
  };
  diameter.addEventListener('change', () => { setSize(); refresh(); });
  setSize();
  const build = () => {
    if (nut && !(clearance.valueAsNumber >= 0 && clearance.valueAsNumber <= .5)) {
      return {input: clearance, message: 'Vælg et spillerum mellem 0 og 0,5 mm pr. side.'};
    }
    const min = nut ? Number(dimension.min) : ROD_LENGTH.min;
    const max = nut ? 24 : ROD_LENGTH.max;
    if (!(dimension.valueAsNumber >= min && dimension.valueAsNumber <= max)) {
      return {input: dimension, message: `Vælg en ${nut ? 'højde' : 'længde'} mellem ${mm(min)} og ${max} mm.`};
    }
    const settings = {diameter: Number(diameter.value), pitch: Number(pitch.value), hand: hand.value,
      [sizeField]: dimension.valueAsNumber};
    if (nut) settings.clearance = clearance.valueAsNumber;
    else if (lying.checked) settings.lying = true;
    return {settings};
  };
  return setupPartEditor(kind, load, refresh, build, apply);
}

function mountFields(id, fields) {
  const inputs = new Map();
  byId(`${id}-fields`).replaceChildren(...fields.map(field => {
    const label = document.createElement('label'), select = field.type === 'select';
    const input = document.createElement(select ? 'select' : 'input');
    if (select) input.append(...field.options.map(option => new Option(option.label, option.value)));
    else { input.type = 'number'; input.min = field.min; input.max = field.max; input.step = field.step; }
    input.id = `${id}-${field.key}`;
    label.htmlFor = input.id; label.append(field.label, input);
    inputs.set(field.key, input);
    return label;
  }));
  return inputs;
}

function fieldValues(fields, inputs) {
  return Object.fromEntries(fields.map(field => {
    const input = inputs.get(field.key);
    const value = field.type === 'select' ? field.options.find(option => String(option.value) === input.value)?.value : input.valueAsNumber;
    return [field.key, value];
  }));
}

function setupCatalog(apply) {
  let recipe, inputs;
  function load(settings) {
    const next = CATALOG_RECIPES.get(settings.recipe);
    if (recipe !== next) {
      recipe = next;
      byId('catalog-title').textContent = recipe.label;
      const fit = byId('catalog-fit');
      fit.textContent = recipe.fit || '';
      fit.hidden = !recipe.fit;
      inputs = mountFields('catalog', recipe.fields);
    }
    for (const [key, input] of inputs) input.value = settings.values[key];
  }
  function read() {
    const values = fieldValues(recipe.fields, inputs);
    const error = recipe.validate(values);
    return error ? {message: error.message, input: inputs.get(error.key)} : {settings: {recipe: recipe.id, values}};
  }
  return setupPartEditor('catalog', load, () => {}, read, apply, ({values}) => recipe.build(values));
}

function setupNativeProfile(apply) {
  let profile, inputs;
  function load(settings) {
    if (profile !== settings.profile) {
      profile = settings.profile;
      inputs = mountFields('profile', NATIVE_PROFILES[profile]);
    }
    for (const [key, input] of inputs) input.value = settings.values[key];
  }
  function read() {
    const fields = NATIVE_PROFILES[profile], values = fieldValues(fields, inputs);
    const bad = fields.find(({key, min, max}) => !Number.isInteger(values[key]) || values[key] < min || values[key] > max);
    return bad ? {input: inputs.get(bad.key), message: `Skriv et helt tal fra ${bad.min} til ${bad.max}.`}
      : {settings: {profile, values}};
  }
  return setupPartEditor('profile', load, () => {}, read, apply, settings => settings);
}

export function setupMechanicalEditors(apply, applyCatalog, applyProfile) {
  const editors = {gear: setupGear(apply), nut: setupThreadedPart('nut', apply), rod: setupThreadedPart('rod', apply),
    'catalog-shape': setupCatalog(applyCatalog), 'native-profile': setupNativeProfile(applyProfile)};
  return {
    get pending() { return Object.values(editors).some(editor => editor.pending); },
    render(node, editable, ready) {
      if (node && Object.hasOwn(NATIVE_PROFILES, node.kind)) {
        node = {id: node.id, generator: {kind: 'native-profile', settings: {profile: node.kind, values: profileValues(node.kind, node.params)}}};
      }
      for (const [kind, editor] of Object.entries(editors)) {
        const part = node?.generator?.kind === kind ? node : null;
        editor.render(part, Boolean(part && editable), ready);
      }
    },
    flush() { for (const editor of Object.values(editors)) editor.flush(); },
  };
}

export function setupShapeLibrary({insert, startDrag, unavailable}) {
  const category = byId('shape-category'), presets = byId('preset-shapes'), basics = byId('basic-shapes');
  const panel = byId('forms-panel');
  for (const button of panel.querySelectorAll('[data-add]')) {
    const {add, mode, sides} = button.dataset;
    addThumbnail(button, mode === 'hole' ? `${add}-hole` : sides ? `${add}-${sides}` : add);
  }
  const thumbnailImage = new Image();
  thumbnailImage.addEventListener('load', () => panel.classList.add('form-thumbnails-ready'), {once: true});
  thumbnailImage.src = '/assets/model3d/form-thumbnails.png';
  function card(preset) {
    const button = presetButton(preset);
    button.disabled = unavailable();
    button.addEventListener('click', () => insert(preset));
    button.addEventListener('dragstart', event => startDrag(event, preset));
    return button;
  }
  const extended = document.createElement('div');
  extended.className = 'basic-catalog-grid';
  extended.append(...SHAPE_LIBRARY.basic.map(card));
  basics.append(extended);
  function showCategory() {
    const selected = category.value;
    const shapes = selected === 'basic' ? null : SHAPE_LIBRARY[selected];
    basics.hidden = selected !== 'basic';
    byId('connector-library-hint').hidden = selected !== 'connectors';
    byId('inspiration-library-hint').hidden = selected !== 'inspiration';
    presets.hidden = !shapes;
    presets.replaceChildren(...(shapes || []).map(card));
    category.closest('#forms-panel').querySelector('.library-shapes').scrollTop = 0;
  }
  function resetCategory() { category.value = 'basic'; showCategory(); }
  category.addEventListener('change', showCategory);
  resetCategory();
  return resetCategory;
}
