'use strict';
// Laserstation: afleveringer fra serverens kø lægges på laserpladen og sendes efter lærerens godkendelse.

const $ = (id) => document.getElementById(id);
const SVGNS = 'http://www.w3.org/2000/svg';
const OP_LABEL = { cut: 'Skær', 'engrave-line': 'Gravér streger', 'engrave-fill': 'Gravér flader' };
const STATUS_LABEL = { received: 'Modtaget', ready: 'Gennemset' };
const GAP = 4;
const MARGIN = 5;
const STORE_KEY = 'laserstation-plade';

let state = null;
let jobs = [];
let selected = null;
let drag = null;
let busy = false;
let scene = null;
let approvalToken = null;
let preparedTransfer = null;
let photoData = null;
let photoSize = null;
let calibrationCorners = [];
let regionMode = 'pick';
let drawing = [];
let editingRegions = [];
let selectedRegion = null;
let sceneSerial = 0;
let regionSaving = false;
let plateEditSerial = 0;
let photoReadSerial = 0;
let deliveryState = null;
let nextTarget = 'job-list';
let completionIds = null;
let completing = false;
const plate = new Map(); // id -> {id, name, short, x, y, rotation, geometry, nodes}

// --- hjælpere -----------------------------------------------------------------------
async function api(route, data) {
  const options = data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) };
  const response = await fetch(route, options);
  let body = {};
  try { body = await response.json(); } catch { /* tomt svar */ }
  if (response.status === 401) {
    showLogin();
    throw new Error(body.error || 'Log ind igen.');
  }
  if (!response.ok) throw new Error(body.error || `Fejl ${response.status}`);
  return body;
}

function el(name, attrs = {}, text) {
  const node = document.createElementNS(SVGNS, name);
  for (const [key, value] of Object.entries(attrs)) if (value !== undefined && value !== null) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function html(name, attrs = {}, text) {
  const node = document.createElement(name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'className') node.className = value; else node.setAttribute(key, value === true ? '' : value);
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ikke vigtigt */ }
}

function load(key) {
  try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch { return null; }
}

function duration(seconds) {
  const s = Math.max(1, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  if (h) return `${h} t ${m} min`;
  if (m) return `${m} min ${rest} s`;
  return `${rest} s`;
}

const mm = (v) => `${Math.round(v)}`;
const snap = (v) => Math.round(v * 2) / 2;

// --- geometri (samme regler som app/laser_plate.py) -----------------------------------
function rotatedSize(g, rotation) {
  return rotation % 180 ? [g.height, g.width] : [g.width, g.height];
}

function placeMatrix(bbox, x, y, rotation) {
  const [bx, by, bx2, by2] = bbox;
  const bw = bx2 - bx;
  const bh = by2 - by;
  if (rotation === 90) return [0, 1, -1, 0, x + bh + by, y - bx];
  if (rotation === 180) return [-1, 0, 0, -1, x + bw + bx, y + bh + by];
  if (rotation === 270) return [0, -1, 1, 0, x - by, y + bw + bx];
  return [1, 0, 0, 1, x - bx, y - by];
}

function boxOf(item) {
  const [w, h] = rotatedSize(item.geometry, item.rotation);
  return [item.x, item.y, item.x + w, item.y + h];
}

function overlaps(a, b, gap = 0) {
  return a[0] < b[2] + gap && b[0] < a[2] + gap && a[1] < b[3] + gap && b[1] < a[3] + gap;
}

function currentMaterial() {
  return state.materials.find((m) => m.id === $('material').value) || state.materials[0];
}

function plateProblems() {
  const bed = state.bed;
  const byItem = new Map();
  const list = [];
  const add = (item, text) => {
    list.push(text);
    byItem.set(item.id, true);
  };
  const items = [...plate.values()];
  items.forEach((item, index) => {
    const b = boxOf(item);
    if (b[0] < -0.01 || b[1] < -0.01 || b[2] > bed.width + 0.01 || b[3] > bed.height + 0.01) add(item, `${item.short} ligger uden for laserens område.`);
    for (const other of items.slice(0, index)) {
      if (overlaps(b, boxOf(other))) {
        add(item, `${item.short} overlapper ${other.short}.`);
        byItem.set(other.id, true);
      }
    }
  });
  const material = currentMaterial();
  if (material && !material.cut && items.some((i) => i.geometry.operations.includes('cut'))) {
    list.push(`${material.name} kan kun graveres, men der er skæringer på pladen.`);
  }
  return { list, byItem };
}

function freeSpot(geometry, ignoreId) {
  const others = [...plate.values()].filter((i) => i.id !== ignoreId).map(boxOf);
  for (const rotation of [0, 90]) {
    const [w, h] = rotatedSize(geometry, rotation);
    for (let y = MARGIN; y + h <= state.bed.height - MARGIN + 1e-9; y += 2) {
      for (let x = MARGIN; x + w <= state.bed.width - MARGIN + 1e-9; x += 2) {
        const b = [x, y, x + w, y + h];
        if (!others.some((o) => overlaps(b, o, GAP - 1e-6))) return { x, y, rotation };
      }
    }
  }
  return null;
}

// --- pladen ---------------------------------------------------------------------------
function svgPoint(event) {
  const svg = $('bed');
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(svg.getScreenCTM().inverse());
}

function renderBed() {
  const svg = $('bed');
  const { width, height } = state.bed;
  svg.setAttribute('viewBox', `-24 -14 ${width + 38} ${height + 28}`);
  svg.replaceChildren();
  const defs = el('defs');
  const hatch = el('pattern', { id: 'excluded-hatch', width: 8, height: 8, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' });
  hatch.append(el('rect', { width: 8, height: 8, fill: '#fff7f7', 'fill-opacity': '.65' }), el('line', { x1: 0, y1: 0, x2: 0, y2: 8, stroke: '#c62b2f', 'stroke-opacity': '.5', 'stroke-width': 3 }));
  defs.append(hatch); svg.append(defs);
  svg.append(el('rect', { class: 'sheet', x: 0, y: 0, width, height }));
  if (scene) {
    svg.append(el('image', { class: 'material-image', href: scene.imageUrl || `/api/station/material/image/${encodeURIComponent(scene.id)}`, x: 0, y: 0, width, height, preserveAspectRatio: 'none' }));
    renderRegions(svg);
  }
  const grid = el('g', { 'aria-hidden': 'true' });
  for (let x = 50; x < width; x += 50) grid.append(el('line', { class: 'grid', x1: x, y1: 0, x2: x, y2: height }));
  for (let y = 50; y < height; y += 50) grid.append(el('line', { class: 'grid', x1: 0, y1: y, x2: width, y2: y }));
  for (let x = 0; x <= width; x += 100) grid.append(el('text', { class: 'ruler', x, y: -4, 'text-anchor': 'middle' }, x ? `${x}` : '0 mm'));
  for (let y = 100; y <= height; y += 100) grid.append(el('text', { class: 'ruler', x: -4, y: y + 2, 'text-anchor': 'end' }, `${y}`));
  grid.append(el('path', { class: 'origin', d: 'M0 0 L9 0 L0 9 Z' }));
  svg.append(grid);
  const items = el('g', { id: 'items' });
  for (const item of plate.values()) items.append(renderItem(item));
  svg.append(items);
  plateChanged(false);
}

function renderItem(item) {
  const group = el('g', { class: 'item', 'data-id': item.id });
  const content = el('g');
  for (const part of item.geometry.parts) {
    content.append(el('path', { d: part.d, class: part.op, 'fill-rule': part.fillRule, transform: part.transform ? `matrix(${part.transform.join(' ')})` : null }));
  }
  for (const image of item.geometry.images) {
    content.append(el('image', { href: image.href, width: image.width, height: image.height, preserveAspectRatio: 'none', opacity: '0.85', transform: image.transform ? `matrix(${image.transform.join(' ')})` : null }));
  }
  const box = el('rect', { class: 'box', tabindex: '0', role: 'button' });
  const label = el('text', { class: 'label' }, item.short);
  group.append(content, box, label);
  item.nodes = { group, content, box, label };
  updateItem(item);

  box.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || regionMode !== 'pick' || busy) return;
    select(item.id);
    const p = svgPoint(event);
    drag = { item, dx: p.x - item.x, dy: p.y - item.y, moved: false };
    box.setPointerCapture(event.pointerId);
    group.classList.add('dragging');
    event.preventDefault();
  });
  box.addEventListener('pointermove', (event) => {
    if (!drag || drag.item !== item) return;
    const p = svgPoint(event);
    const x = snap(p.x - drag.dx);
    const y = snap(p.y - drag.dy);
    if (x !== item.x || y !== item.y) {
      item.x = x;
      item.y = y;
      drag.moved = true;
      plateEditSerial++;
      plateChanged();
      updateItem(item);
    }
  });
  const end = () => {
    if (!drag || drag.item !== item) return;
    const moved = drag.moved;
    drag = null;
    group.classList.remove('dragging');
    if (moved) plateChanged();
  };
  box.addEventListener('pointerup', end);
  box.addEventListener('pointercancel', end);
  box.addEventListener('focus', () => select(item.id));
  box.addEventListener('keydown', (event) => {
    const step = event.shiftKey ? 10 : 1;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (busy) return;
    if (moves[event.key]) {
      item.x = snap(item.x + moves[event.key][0]);
      item.y = snap(item.y + moves[event.key][1]);
      updateItem(item);
      plateChanged();
    } else if (event.key === 'r' || event.key === 'R') {
      rotate(item);
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      removeFromPlate(item.id);
    } else if (event.key === 'Escape') {
      select(null);
      box.blur();
    } else {
      return;
    }
    event.preventDefault();
  });
  return group;
}

function updateItem(item) {
  const { content, box, label } = item.nodes;
  content.setAttribute('transform', `matrix(${placeMatrix(item.geometry.bbox, item.x, item.y, item.rotation).join(' ')})`);
  const [x0, y0, x1, y1] = boxOf(item);
  box.setAttribute('x', x0);
  box.setAttribute('y', y0);
  box.setAttribute('width', x1 - x0);
  box.setAttribute('height', y1 - y0);
  box.setAttribute('aria-label', `${item.name}. ${mm(x1 - x0)} × ${mm(y1 - y0)} mm, ${mm(x0)} mm fra venstre og ${mm(y0)} mm fra toppen.`);
  label.setAttribute('x', x0 + 1);
  label.setAttribute('y', y0 > 9 ? y0 - 2 : y1 + 7);
}

function select(id) {
  selected = id && plate.has(id) ? id : null;
  for (const item of plate.values()) item.nodes?.group.classList.toggle('selected', item.id === selected);
  $('rotate').disabled = !selected;
  $('remove').disabled = !selected;
}

function rotate(item) {
  const [x0, y0, x1, y1] = boxOf(item);
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  item.rotation = (item.rotation + 90) % 360;
  const [w, h] = rotatedSize(item.geometry, item.rotation);
  item.x = snap(cx - w / 2);
  item.y = snap(cy - h / 2);
  updateItem(item);
  plateChanged();
}

function removeFromPlate(id) {
  const item = plate.get(id);
  if (!item) return;
  item.nodes?.group.remove();
  plate.delete(id);
  if (selected === id) select(null);
  plateChanged();
  renderJobs();
}

function plateChanged(resetResults = true) {
  if (resetResults) { approvalToken = null; preparedTransfer = null; deliveryState = null; plateEditSerial++; }
  const { list, byItem } = plateProblems();
  for (const item of plate.values()) item.nodes?.group.classList.toggle('problem', byItem.has(item.id));
  const problems = $('problems');
  problems.replaceChildren(...list.map((text) => html('li', {}, text)));
  problems.hidden = !list.length;
  const count = plate.size;
  if (!count) {
    $('bed-status').textContent = 'Vælg afleveringer i listen for at lægge dem på pladen.';
  } else {
    const boxes = [...plate.values()].map(boxOf);
    const right = Math.max(...boxes.map((b) => b[2]));
    const bottom = Math.max(...boxes.map((b) => b[3]));
    $('bed-status').textContent = `${count} ${count === 1 ? 'elevprodukt' : 'elevprodukter'} på pladen. Brugt område: ${mm(right)} × ${mm(bottom)} mm fra nulpunktet.`;
  }
  $('check').disabled = !count || list.length > 0 || busy || !!photoData;
  $('open').disabled = !count || list.length > 0 || busy || !!photoData;
  $('approve-placement').disabled = !count || list.length > 0 || busy || !!photoData || (scene && !scene.approved);
  if (scene && !approvalToken) $('open').disabled = true;
  $('prepare-transfer').disabled = !count || list.length > 0 || busy || !!photoData || !approvalToken || (scene && !scene.approved);
  const transfer = state.directTransfer || {};
  $('send-transfer').disabled = !transfer.available || !preparedTransfer || busy || !approvalToken || list.length > 0 || !!photoData;
  $('transfer-gate').textContent = transfer.available ? 'Direkte overførsel er tilgængelig. Overførslen starter ikke laseren.' : (transfer.message || 'Direkte overførsel afventer opsætning af laseren.');
  $('auto-place').disabled = !count || busy;
  $('clear').disabled = !count || busy;
  if (resetResults) {
    $('approval-result').textContent = '';
    $('check-result').textContent = '';
    $('check-result').className = 'result';
    $('open-result').textContent = '';
    $('open-result').className = 'result';
    $('transfer-result').textContent = '';
    $('transfer-result').className = 'result';
  }
  store(STORE_KEY, { items: [...plate.values()].map(({ id, x, y, rotation }) => ({ id, x, y, rotation })) });
  updateQueueCount();
  renderNextStep(list);
}

// Guidance describes the current state; it never triggers a production action.
function renderNextStep(problems = plateProblems().list) {
  let title, detail, target;
  if (busy) {
    title = 'Arbejder …'; detail = 'Vent på resultatet af den handling, du har valgt.';
  } else if (photoData) {
    title = 'Gør materialefotoet færdigt'; detail = 'Markér de fire hjørner og kontrollér højden. Vælg Brug foto, eller Annullér foto for at fortsætte uden.'; target = 'calibration';
  } else if (!plate.size) {
    title = '1. Vælg afleveringer';
    detail = jobs.length ? 'Find klasse eller projekt i køen, vælg eventuel billedmetode, og sæt flueben ved de produkter, der skal på pladen.' : 'Eleverne sender fra laserværkstedet på deres computer. Opdatér køen, når en elev har fået en afleveringskvittering.';
    target = jobs.length ? 'job-list' : 'refresh';
  } else if (problems.length) {
    title = '2. Ret pladen før du fortsætter'; detail = problems[0]; target = 'problems';
  } else if (scene && !scene.approved) {
    title = '2. Kontrollér materialeområderne'; detail = 'Kontrollér eller tegn de brugbare områder og de områder, der skal undgås. Godkend områderne, når de passer til materialet.'; target = 'region-workflow';
  } else if (deliveryState) {
    title = '4. Kontrollér og start ved laseren';
    detail = deliveryState === 'sent' ? 'Filen er overført; laseren er ikke startet. Kontrollér materiale, fokus og placering ved maskinen. Afslut afleveringerne, når produkterne er færdige.' : 'Pladen er åbnet i Beam Studio. Kontrollér materiale, fokus og placering dér, før du starter. Afslut afleveringerne efter produktionen.';
    target = 'done-step';
  } else if (!approvalToken) {
    title = '2. Kontrollér materiale og placering'; detail = 'Vælg den rigtige materialetype og tykkelse. Beregn eventuelt tiden, og godkend placeringen. Du kan også åbne pladen i Beam Studio.'; target = 'material';
  } else if (!state.directTransfer?.available) {
    title = '3. Fortsæt i Beam Studio'; detail = 'Placeringen er godkendt. Direkte overførsel er ikke tilgængelig; åbn pladen i Beam Studio og kontrollér den dér.'; target = 'open';
  } else if (preparedTransfer) {
    title = '3. Overfør den klargjorte fil'; detail = 'Filen er klar. Send til laseren overfører filen, men starter ikke skæring eller gravering.'; target = 'send-transfer';
  } else {
    title = '3. Klargør laserfilen'; detail = 'Placeringen er godkendt. Klargør til laseren laver filen. Derefter vælger du selv Send til laseren.'; target = 'prepare-transfer';
  }
  $('next-title').textContent = title;
  $('next-detail').textContent = detail;
  nextTarget = target;
  $('next-action').disabled = !target || $(target).hidden;
}

function locateNextStep() {
  const target = $(nextTarget);
  if (!target || target.hidden) return;
  for (let parent = target.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
  if (!target.matches('button,input,select,[tabindex]')) target.tabIndex = -1;
  target.scrollIntoView({ block: 'center', behavior: 'auto' });
  target.focus({ preventScroll: true });
}

function payload(extra = {}) {
  return { material: $('material').value, items: [...plate.values()].map(({ id, x, y, rotation }) => ({ id, x, y, rotation })), ...(scene ? { sceneId: scene.id, sceneRevision: scene.revision } : {}), ...extra };
}

// --- køen -----------------------------------------------------------------------------
function jobName(job) {
  return `${job.student} (${job.classroom}), ${job.project}`;
}

async function addToPlate(job, spot) {
  const geometry = await api(`/api/station/jobs/${job.id}/geometry`);
  const place = spot || freeSpot(geometry);
  const item = {
    id: job.id, name: jobName(job), short: `${job.student}: ${job.project}`, geometry,
    x: place ? place.x : MARGIN, y: place ? place.y : MARGIN, rotation: place ? place.rotation : 0,
  };
  plate.set(job.id, item);
  $('items').append(renderItem(item));
  plateChanged();
  if (!place && !spot) setStatus(`Der er ikke plads til ${item.short}. Flyt, drej eller tag noget af pladen.`);
}

function setStatus(text) {
  $('bed-status').textContent = text;
}

function renderJobs() {
  const classes = [...new Set(jobs.map((j) => j.classroom))].sort((a, b) => a.localeCompare(b, 'da'));
  const filter = $('class-filter');
  const chosen = filter.value;
  filter.replaceChildren(html('option', { value: 'all' }, 'Alle klasser'), ...classes.map((c) => html('option', { value: c }, c)));
  filter.value = classes.includes(chosen) ? chosen : 'all';
  const query = $('search').value.trim().toLocaleLowerCase('da');
  const visible = jobs.filter((j) => (filter.value === 'all' || j.classroom === filter.value) &&
    `${j.student} ${j.project}`.toLocaleLowerCase('da').includes(query));
  const list = $('job-list');
  list.replaceChildren(...visible.map(renderJob));
  $('queue-empty').hidden = visible.length > 0;
  $('queue-empty').textContent = jobs.length ? 'Ingen afleveringer passer til filteret.' : 'Der er ingen afleveringer, der venter.';
  updateQueueCount();
  renderNextStep();
}

function updateQueueCount() {
  const query = $('search').value.trim().toLocaleLowerCase('da');
  const classroom = $('class-filter').value;
  const count = jobs.filter(job => (classroom === 'all' || job.classroom === classroom) && `${job.student} ${job.project}`.toLocaleLowerCase('da').includes(query)).length;
  $('queue-count').textContent = `${count} af ${jobs.length} afleveringer · ${plate.size} på pladen`;
}

function renderJob(job) {
  const onPlate = plate.has(job.id);
  const li = html('li', { className: `job${onPlate ? ' on-plate' : ''}` });
  const box = html('input', { type: 'checkbox', id: `job-${job.id}`, checked: onPlate, disabled: job.needsChoice && !onPlate });
  const label = html('label', { for: `job-${job.id}` });
  label.append(html('span', { className: 'project' }, job.project));
  const when = new Date(job.created);
  const date = Number.isNaN(when.getTime()) ? '' : ` · ${when.toLocaleDateString('da-DK', { day: 'numeric', month: 'short' })}`;
  const who = html('span', { className: 'who' }, `${job.student} · ${job.classroom}${date} `);
  if (STATUS_LABEL[job.status]) who.append(html('span', { className: 'status' }, STATUS_LABEL[job.status]));
  label.append(who);
  li.append(box, label);
  const ops = html('div', { className: 'ops' });
  for (const [op, count] of Object.entries(job.operations)) if (count > 0 && OP_LABEL[op]) ops.append(html('span', { className: `op ${op}` }, OP_LABEL[op]));
  li.append(ops);
  const error = html('p', { className: 'job-error', role: 'status' });
  for (const image of job.images.filter((i) => !i.mode)) {
    const choice = html('div', { className: 'choice' });
    choice.append(html('p', {}, `Billedet «${image.name}» skal graveres. Vælg hvordan:`));
    const buttons = html('div', { className: 'buttons' });
    const raster = html('button', { type: 'button', title: 'Graveres som et gråtonebillede' }, 'Som foto');
    const vector = html('button', { type: 'button', title: 'Kun billedets konturer graveres som streger', disabled: !image.hasTrace }, 'Som streger');
    raster.addEventListener('click', () => chooseImage(job, image, 'raster', error));
    vector.addEventListener('click', () => chooseImage(job, image, 'vector', error));
    buttons.append(raster, vector);
    choice.append(buttons);
    li.append(choice);
  }
  li.append(error);
  box.addEventListener('change', async () => {
    error.textContent = '';
    if (box.checked) {
      box.disabled = true;
      try {
        await addToPlate(job);
        li.classList.add('on-plate');
      } catch (e) {
        box.checked = false;
        error.textContent = e.message;
      } finally {
        box.disabled = false;
      }
    } else {
      removeFromPlate(job.id);
    }
  });
  return li;
}

async function chooseImage(job, image, mode, error) {
  try {
    plateChanged();
    await api(`/api/station/jobs/${job.id}/image-mode`, { imageId: image.id, mode });
    await loadJobs();
  } catch (e) {
    error.textContent = e.message;
  }
}

async function loadJobs() {
  try {
    const data = await api('/api/station/jobs');
    jobs = data.jobs;
    $('queue-error').hidden = true;
    // Afleveringer, der er markeret færdige andre steder, forsvinder også fra pladen.
    const ids = new Set(jobs.map((j) => j.id));
    for (const id of [...plate.keys()]) if (!ids.has(id)) removeFromPlate(id);
    renderJobs();
  } catch (e) {
    $('queue-error').textContent = e.message;
    $('queue-error').hidden = false;
  }
}

async function restorePlate(saved) {
  if (!saved || !Array.isArray(saved.items)) return;
  for (const item of saved.items) {
    const job = jobs.find((j) => j.id === item.id);
    if (!job || job.needsChoice) continue;
    try {
      await addToPlate(job, { x: item.x, y: item.y, rotation: item.rotation });
    } catch { /* afleveringen kan ikke længere bruges */ }
  }
  renderJobs();
}

// --- materiale og handlinger --------------------------------------------------------
function renderMaterials() {
  const select = $('material');
  const cutting = html('optgroup', { label: 'Skæring og gravering' });
  const engraving = html('optgroup', { label: 'Kun gravering' });
  for (const m of state.materials) (m.cut ? cutting : engraving).append(html('option', { value: m.id }, m.name));
  select.replaceChildren(cutting, engraving);
  const saved = load('laserstation-materiale');
  select.value = state.materials.some((m) => m.id === saved) ? saved : (state.materials.find((m) => m.id === 'wood_3mm') || state.materials[0]).id;
  $('preset-source').textContent = `FLUX' egne indstillinger til ${state.bed.machine} fra ${state.presetSource.split(',')[0]}.`;
  renderPreset();
}

function renderPreset() {
  const material = currentMaterial();
  const info = $('preset-info');
  info.replaceChildren();
  const row = (title, preset, note) => {
    info.append(html('dt', {}, title));
    const dd = html('dd', {}, preset ? `${preset.power} % · ${preset.speed} mm/s${preset.repeat > 1 ? ` · ${preset.repeat} gange` : ''}` : 'Kan ikke bruges');
    if (preset) dd.append(html('small', {}, preset.name));
    if (note) dd.append(html('small', {}, note));
    info.append(dd);
  };
  row('Skæring', material.cutPreset);
  row('Gravering', material.engravePreset, 'Streger graveres højst med 50 mm/s.');
}

async function checkPlate() {
  const result = $('check-result');
  const request = payload();
  const fingerprint = JSON.stringify(request);
  const editSerial = plateEditSerial;
  busy = true;
  plateChanged(false);
  result.className = 'result';
  result.textContent = 'FLUX beregner laserjobbet …';
  try {
    const data = await api('/api/station/plate/check', request);
    if (editSerial !== plateEditSerial || fingerprint !== JSON.stringify(payload())) return;
    if (data.problems.length) {
      result.className = 'result warn';
      result.textContent = data.problems.join(' ');
    } else if (data.flux) {
      result.className = 'result ok';
      result.textContent = `Beregnet tid: ca. ${duration(data.flux.seconds)}. FLUX har lavet jobbet uden fejl.`;
    } else {
      result.className = 'result warn';
      result.textContent = data.fluxError || 'Tiden kunne ikke beregnes.';
    }
  } catch (e) {
    if (editSerial !== plateEditSerial || fingerprint !== JSON.stringify(payload())) return;
    result.className = 'result warn';
    result.textContent = e.message;
  } finally {
    busy = false;
    plateChanged(false);
  }
}

async function openPlate() {
  if (scene && !approvalToken) { $('open-result').textContent = 'Godkend placeringen først.'; return; }
  const result = $('open-result');
  const editSerial = plateEditSerial;
  busy = true;
  plateChanged(false);
  result.className = 'result';
  result.textContent = 'Gemmer pladen og åbner Beam Studio …';
  try {
    const data = await api('/api/station/plate/open', payload(approvalToken ? { approvalToken } : {}));
    const time = data.flux ? ` Beregnet tid: ca. ${duration(data.flux.seconds)}.` : '';
    result.className = data.opened ? 'result ok' : 'result warn';
    result.replaceChildren(
      document.createTextNode(data.opened ? `Pladen er åbnet i Beam Studio.${time} ` : `${data.openError || 'Beam Studio kunne ikke åbnes.'}${time} `),
      html('br'), html('code', {}, data.file),
    );
    if (!data.flux && data.fluxError) result.append(html('br'), document.createTextNode(data.fluxError));
    await loadState();
    if (data.opened && editSerial === plateEditSerial) deliveryState = 'beam';
  } catch (e) {
    result.className = 'result warn';
    result.textContent = e.message;
  } finally {
    busy = false;
    plateChanged(false);
  }
}

async function prepareTransfer() {
  if (!approvalToken || busy) return;
  const request = payload({ approvalToken });
  const fingerprint = JSON.stringify(request);
  const editSerial = plateEditSerial;
  const result = $('transfer-result');
  preparedTransfer = null;
  deliveryState = null;
  busy = true;
  plateChanged(false);
  result.className = 'result';
  result.textContent = 'Klargør laserfilen …';
  try {
    const data = await api('/api/station/transfer/prepare', request);
    if (editSerial !== plateEditSerial || fingerprint !== JSON.stringify(payload({ approvalToken }))) return;
    preparedTransfer = data.transferId;
    state.directTransfer = data.directTransfer || state.directTransfer;
    result.className = 'result ok';
    result.textContent = `Laserfilen er klar${data.seconds == null ? '' : ` · ca. ${duration(data.seconds)}`}. Den er ikke sendt, og laseren er ikke startet.`;
  } catch (e) {
    if (editSerial !== plateEditSerial || fingerprint !== JSON.stringify(payload({ approvalToken }))) return;
    result.className = 'result warn';
    result.textContent = e.message;
  } finally {
    busy = false;
    plateChanged(false);
  }
}

async function sendTransfer() {
  if (!preparedTransfer || !state.directTransfer?.available || !approvalToken || busy) return;
  const transferId = preparedTransfer;
  const request = payload({ approvalToken, transferId, confirmed: true });
  const fingerprint = JSON.stringify(request);
  const editSerial = plateEditSerial;
  const result = $('transfer-result');
  preparedTransfer = null; // A timeout can still mean that the file arrived; never retry automatically.
  busy = true;
  plateChanged(false);
  result.className = 'result';
  result.textContent = 'Sender laserfilen …';
  try {
    const data = await api('/api/station/transfer/send', request);
    if (editSerial !== plateEditSerial || fingerprint !== JSON.stringify(payload({ approvalToken, transferId, confirmed: true }))) return;
    result.className = 'result ok';
    result.textContent = data.sent ? 'Laserfilen er sendt. Skæringen er ikke startet. Kontrollér placeringen og start ved laseren.' : 'Laserfilen blev ikke sendt.';
    if (data.sent) deliveryState = 'sent';
    loadState().catch(() => {});
  } catch (e) {
    if (editSerial !== plateEditSerial || fingerprint !== JSON.stringify(payload({ approvalToken, transferId, confirmed: true }))) return;
    result.className = 'result warn';
    result.textContent = `${e.message} Kontrollér laseren, før du klargør og sender igen.`;
  } finally {
    busy = false;
    plateChanged(false);
  }
}

function completionNames(last) {
  return last.ids.map((id, index) => {
    const job = jobs.find(job => job.id === id);
    return job ? jobName(job) : last.names?.length === last.ids.length ? last.names[index] : `Aflevering ${id.slice(0, 8).toUpperCase()}`;
  });
}

function renderLastPlate() {
  const last = state.lastPlate;
  $('done-step').hidden = !last;
  if (!last) return;
  $('done-list').replaceChildren(...completionNames(last).map((n) => html('li', {}, n)));
}

function reviewCompletion() {
  if (busy || completing || !state.lastPlate?.ids?.length) return;
  completionIds = [...state.lastPlate.ids];
  $('complete-list').replaceChildren(...completionNames(state.lastPlate).map(name => html('li', {}, name)));
  $('complete-result').textContent = '';
  $('confirm-done').disabled = false;
  $('complete-dialog').showModal();
}

async function markDone() {
  const last = state.lastPlate;
  if (busy || completing || !$('complete-dialog').open || !completionIds) return;
  if (!last || JSON.stringify(last.ids) !== JSON.stringify(completionIds)) {
    $('complete-result').textContent = 'Listen er ændret. Gå tilbage, og åbn afslutningen igen, så du kan kontrollere de rigtige afleveringer.';
    return;
  }
  const ids = [...completionIds];
  const result = $('done-result');
  completing = busy = true;
  $('complete-dialog').querySelectorAll('button').forEach(button => button.disabled = true);
  $('complete-result').textContent = 'Afslutter afleveringerne …';
  plateChanged(false);
  try {
    const data = await api('/api/station/plate/done', { ids });
    for (const id of ids) removeFromPlate(id);
    result.className = 'result ok';
    result.textContent = `${data.marked} ${data.marked === 1 ? 'aflevering er' : 'afleveringer er'} afsluttet. Historikken er bevaret.`;
    $('station-message').textContent = result.textContent;
    $('station-message').hidden = false;
    $('complete-dialog').close();
    completionIds = null;
    try {
      await loadState();
      await loadJobs();
    } catch {
      $('station-message').textContent += ' Oversigten kunne ikke opdateres. Opdatér køen før næste plade.';
    }
  } catch (e) {
    // Status may have been applied to only part of the plate. Reload the
    // persisted station state so a retry contains only unfinished jobs.
    try {
      await loadState();
      await loadJobs();
    } catch { /* Keep the current plate if the queue is still offline. */ }
    result.className = 'result warn';
    result.textContent = e.message;
    $('complete-result').textContent = `${e.message} Gå tilbage og kontrollér listen, før du prøver igen.`;
    completionIds = null;
  } finally {
    completing = busy = false;
    $('complete-dialog').querySelectorAll('button').forEach(button => button.disabled = false);
    $('confirm-done').disabled = !completionIds;
    plateChanged(false);
  }
}

async function autoPlace() {
  if (!plate.size) return;
  const sceneAtRequest = scene ? `${scene.id}:${scene.revision}` : null;
  const ids = [...plate.keys()];
  const fingerprint = JSON.stringify(payload());
  const editSerial = plateEditSerial;
  try {
    const data = await api('/api/station/place', { ids, ...(scene ? { sceneId: scene.id, sceneRevision: scene.revision } : {}) });
    if (editSerial !== plateEditSerial || fingerprint !== JSON.stringify(payload()) || sceneAtRequest !== (scene ? `${scene.id}:${scene.revision}` : null) || ids.join(',') !== [...plate.keys()].join(',')) return;
    for (const place of data.placements) {
      const item = plate.get(place.id);
      Object.assign(item, { x: place.x, y: place.y, rotation: place.rotation });
      updateItem(item);
    }
    plateChanged();
    if (data.unplaced.length) {
      const names = data.unplaced.map((id) => plate.get(id)?.short).filter(Boolean);
      setStatus(`Der var ikke plads til: ${names.join(', ')}. Tag dem af pladen og skær dem på en ny plade.`);
    }
  } catch (e) {
    if (editSerial !== plateEditSerial) return;
    setStatus(e.message);
  }
}

// --- materialefoto og lærerens områder -----------------------------------------------
function cloneRegions(regions) {
  return regions.map((r) => ({ id: r.id, source: r.source || 'manual', points: r.points.map((p) => [...p]), holes: (r.holes || []).map((h) => h.map((p) => [...p])) }));
}

function materialMessage(text, warn = false, id = 'region-result') {
  const node = $(id);
  node.textContent = text;
  node.className = `result${warn ? ' warn' : ''}`;
}

function renderMaterial() {
  const active = !!scene;
  if (active || photoData) $('photo-options').open = true;
  $('material-mode').textContent = active ? (scene.approved ? 'Materiale godkendt' : 'Materiale afventer kontrol') : 'Manuel plade';
  $('material-mode').classList.toggle('approved', !!scene?.approved);
  $('region-workflow').hidden = !active;
  $('calibration').hidden = !photoData;
  $('material-note').textContent = `Tegn materialets brugbare områder og udeluk huller. Kontrollér kanterne selv. ${(scene?.warnings || []).join(' ')}`;
  $('approve-material').disabled = !active || !editingRegions.length || busy || regionSaving || drawing.length > 0 || !!scene?.approved;
  $('material-clear').disabled = !active || busy;
  $('region-list').replaceChildren(...editingRegions.map((r, index) => {
    const li = html('li', { className: selectedRegion === r.id ? 'selected' : '' });
    const choose = html('button', { type: 'button', className: 'region-select' }, `Område ${index + 1} · tegnet · ${r.holes.length} udelukket`);
    choose.addEventListener('click', () => { selectedRegion = r.id; renderMaterial(); renderBed(); });
    const remove = html('button', { type: 'button', className: 'quiet small' }, 'Slet');
    remove.disabled = regionSaving;
    remove.addEventListener('click', () => { if (regionSaving) return; sceneSerial++; editingRegions.splice(index, 1); selectedRegion = null; saveRegions(); });
    li.append(choose, remove);
    return li;
  }));
  for (const [mode, id] of [['pick', 'region-pick'], ['usable', 'region-usable'], ['hole', 'region-hole']]) $(id).classList.toggle('active', regionMode === mode);
  $('region-finish').disabled = drawing.length < 3;
  $('region-undo').disabled = !drawing.length;
  $('region-instruction').textContent = regionMode === 'pick' ? 'Vælg et område for at flytte dets hjørner. Kontrollér kanter og huller.' : regionMode === 'hole' ? 'Vælg først et brugbart område, klik så mindst tre punkter inden i det, og afslut polygonen.' : 'Klik mindst tre punkter langs materialets brugbare kant, og afslut polygonen.';
  plateChanged(false);
}

function regionPath(points) { return points.map((p, i) => `${i ? 'L' : 'M'}${p[0]} ${p[1]}`).join(' ') + ' Z'; }
function renderRegions(svg) {
  const group = el('g', { id: 'material-regions' });
  for (const region of editingRegions) {
    const path = el('path', { class: `usable-region${region.id === selectedRegion ? ' selected' : ''}`, d: [regionPath(region.points), ...region.holes.map(regionPath)].join(' '), 'fill-rule': 'evenodd' });
    path.addEventListener('pointerdown', (event) => { if (regionMode === 'pick') { selectedRegion = region.id; renderMaterial(); renderBed(); event.stopPropagation(); } });
    group.append(path);
    region.holes.forEach((hole) => group.append(el('path', { class: 'excluded-region', d: regionPath(hole) })));
    if (region.id === selectedRegion && regionMode === 'pick') {
      const rings = [region.points, ...region.holes];
      rings.forEach((ring, ringIndex) => ring.forEach((point, vertexIndex) => {
        const handle = el('circle', { class: 'region-handle', cx: point[0], cy: point[1], r: 5, tabindex: 0, role: 'button', 'aria-label': `Flyt hjørne ${vertexIndex + 1}` });
        handle.addEventListener('pointerdown', (event) => { if (event.button !== 0 || regionSaving) return; handle.setPointerCapture(event.pointerId); event.stopPropagation(); });
        handle.addEventListener('pointermove', (event) => {
          if (!handle.hasPointerCapture(event.pointerId)) return;
          const p = svgPoint(event);
          ring[vertexIndex] = [Math.max(0, Math.min(state.bed.width, snap(p.x))), Math.max(0, Math.min(state.bed.height, snap(p.y)))];
          sceneSerial++;
          point[0] = ring[vertexIndex][0]; point[1] = ring[vertexIndex][1];
          handle.setAttribute('cx', point[0]); handle.setAttribute('cy', point[1]);
          path.setAttribute('d', [regionPath(region.points), ...region.holes.map(regionPath)].join(' '));
          scene.approved = false; plateChanged();
        });
        handle.addEventListener('pointerup', (event) => { if (handle.hasPointerCapture(event.pointerId)) { handle.releasePointerCapture(event.pointerId); saveRegions(); } });
        group.append(handle);
      }));
    }
  }
  if (drawing.length) group.append(el('polyline', { class: 'drawing-region', points: drawing.map((p) => p.join(',')).join(' ') }));
  svg.append(group);
}

function drawBedPoint(event) {
  if (regionMode === 'pick' || event.button !== 0 || !scene || regionSaving) return;
  const p = svgPoint(event);
  if (p.x < 0 || p.y < 0 || p.x > state.bed.width || p.y > state.bed.height) return;
  drawing.push([snap(p.x), snap(p.y)]);
  sceneSerial++;
  scene.approved = false;
  plateChanged();
  renderBed(); renderMaterial();
  event.preventDefault();
}

function finishRegion() {
  if (drawing.length < 3 || regionSaving) return;
  if (regionMode === 'hole') {
    const region = editingRegions.find((r) => r.id === selectedRegion);
    if (!region) { materialMessage('Vælg først et brugbart område.', true); return; }
    region.holes.push(drawing);
  } else {
    const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    editingRegions.push({ id, source: 'manual', points: drawing, holes: [] });
    selectedRegion = id;
  }
  drawing = [];
  sceneSerial++;
  regionMode = 'pick';
  saveRegions();
}

async function saveRegions() {
  if (!scene || regionSaving) return;
  regionSaving = true;
  const serial = ++sceneSerial;
  const request = { sceneId: scene.id, revision: scene.revision, regions: cloneRegions(editingRegions) };
  approvalToken = null;
  scene.approved = false;
  plateChanged();
  renderBed(); renderMaterial();
  try {
    const data = await api('/api/station/material/regions', request);
    if (serial !== sceneSerial || scene?.id !== request.sceneId) return;
    scene = data.scene;
    editingRegions = cloneRegions(scene.regions || []);
    materialMessage('Områderne er gemt. Kontrollér dem på fotoet og godkend.');
    renderBed(); renderMaterial();
  } catch (e) {
    if (serial === sceneSerial) {
      editingRegions = cloneRegions(scene.regions || []);
      materialMessage(`${e.message} Områderne blev ikke gemt.`, true);
      renderBed();
    }
  } finally {
    regionSaving = false;
    renderMaterial();
  }
}

function renderPhotoPreview() {
  const preview = $('photo-preview');
  if (!photoData || !photoSize) return;
  preview.setAttribute('viewBox', `0 0 ${photoSize.width} ${photoSize.height}`);
  preview.replaceChildren(el('image', { href: photoData, width: photoSize.width, height: photoSize.height }), ...calibrationCorners.map((p, i) => el('circle', { cx: p[0], cy: p[1], r: Math.max(5, photoSize.width / 100), class: 'corner-mark' })));
  const labels = ['øverst venstre', 'øverst højre', 'nederst højre', 'nederst venstre'];
  $('calibration-step').textContent = calibrationCorners.length < 4 ? `Klik hjørne ${calibrationCorners.length + 1}: ${labels[calibrationCorners.length]}.` : 'Fire hjørner markeret. Kontrollér materialets højde og brug fotoet.';
  $('photo-submit').disabled = calibrationCorners.length !== 4 || !$('height-confirm').checked || !Number.isFinite(Number($('material-height').value)) || Number($('material-height').value) < 0;
}

async function loadMaterialPhoto(file) {
  if (!file) return;
  const readSerial = ++photoReadSerial;
  sceneSerial++;
  plateChanged();
  if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 8 * 1024 * 1024) { materialMessage('Vælg et PNG- eller JPEG-foto på højst 8 MB.', true, 'photo-result'); return; }
  const dataUrl = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('Fotoet kunne ikke læses.')); reader.readAsDataURL(file); });
  const image = await new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = () => reject(new Error('Fotoet kunne ikke åbnes.')); img.src = dataUrl; });
  if (readSerial !== photoReadSerial) return;
  photoData = dataUrl; photoSize = { width: image.naturalWidth, height: image.naturalHeight }; calibrationCorners = [];
  approvalToken = null; plateChanged();
  $('height-confirm').checked = false;
  $('calibration').hidden = false;
  materialMessage('Markér alle fire pladehjørner på fotoet.', false, 'photo-result');
  renderPhotoPreview();
}

async function submitPhoto() {
  if (!photoData || calibrationCorners.length !== 4 || !$('height-confirm').checked) return;
  const serial = ++sceneSerial;
  $('photo-submit').disabled = true;
  materialMessage('Kalibrerer fotoet …', false, 'photo-result');
  try {
    const data = await api('/api/station/material/image', { image: photoData, corners: calibrationCorners, heightMm: Number($('material-height').value), confirmed: true });
    if (serial !== sceneSerial) return;
    scene = data.scene; editingRegions = cloneRegions(scene.regions || []); approvalToken = null; selectedRegion = null; plateChanged();
    photoData = null; calibrationCorners = []; $('material-photo').value = '';
    materialMessage('Fotoet er lagt på pladen. Tegn eller kontrollér områderne.', false, 'photo-result');
    renderMaterial(); renderBed();
  } catch (e) { if (serial === sceneSerial) materialMessage(e.message, true, 'photo-result'); renderPhotoPreview(); }
}

async function approveMaterial() {
  if (!scene || !editingRegions.length) return;
  const serial = ++sceneSerial, id = scene.id, revision = scene.revision;
  approvalToken = null; plateChanged();
  try {
    const data = await api('/api/station/material/approve', { sceneId: id, revision, confirmed: true });
    if (serial !== sceneSerial || scene?.id !== id || scene?.revision !== revision) return;
    scene = data.scene; approvalToken = null; plateChanged(); materialMessage('Materialeområderne er godkendt. Placér emnerne og godkend placeringen.'); renderMaterial(); renderBed();
  } catch (e) { if (serial === sceneSerial) materialMessage(e.message, true); }
}

async function approvePlacement() {
  if (!plate.size || (scene && !scene.approved) || busy) return;
  const request = payload({ confirmed: true });
  const fingerprint = JSON.stringify(request);
  const editSerial = plateEditSerial;
  $('approve-placement').disabled = true;
  try {
    const data = await api('/api/station/plate/approve', request);
    if (editSerial !== plateEditSerial || JSON.stringify(payload({ confirmed: true })) !== fingerprint) return;
    approvalToken = data.approvalToken;
    preparedTransfer = null;
    $('approval-result').className = 'result ok';
    $('approval-result').textContent = 'Placeringen er godkendt. Kontrollér den ved laseren før start.';
  } catch (e) { if (editSerial === plateEditSerial && JSON.stringify(payload({ confirmed: true })) === fingerprint) { $('approval-result').className = 'result warn'; $('approval-result').textContent = e.message; } }
  plateChanged(false);
}

// --- login og opstart ----------------------------------------------------------------
function showLogin() {
  if ($('complete-dialog').open) $('complete-dialog').close();
  completionIds = null;
  $('loading').hidden = true;
  $('station').hidden = true;
  $('logout').hidden = true;
  $('login-panel').hidden = false;
  if (state) $('server-name').textContent = state.server;
  $('login-code').focus();
}

async function loadState() {
  state = await api('/api/station/state');
  if (!$('station').hidden) plateChanged(false);
  if (state.materialScene?.id !== scene?.id || state.materialScene?.revision !== scene?.revision) {
    scene = state.materialScene || null;
    sceneSerial++;
    approvalToken = null;
    if (!$('station').hidden) plateChanged();
    editingRegions = cloneRegions(scene?.regions || []);
    selectedRegion = null;
    drawing = [];
    regionMode = 'pick';
    if (!$('station').hidden) { renderMaterial(); renderBed(); }
  }
  renderLastPlate();
  if (!$('station').hidden) renderNextStep();
  return state;
}

async function showStation() {
  $('loading').hidden = true;
  $('login-panel').hidden = true;
  $('station').hidden = false;
  $('logout').hidden = false;
  const saved = plate.size ? null : load(STORE_KEY);
  renderMaterials();
  renderMaterial();
  renderBed();
  await loadJobs();
  await restorePlate(saved);
}

async function start() {
  try {
    await loadState();
  } catch (e) {
    $('loading').textContent = `Laserstationen svarer ikke: ${e.message}`;
    return;
  }
  $('machine-note').textContent = `${state.bed.machine} · ${state.bed.width} × ${state.bed.height} mm`;
  $('bed-dimensions').textContent = `PLADEN · ${state.bed.width} × ${state.bed.height} MM`;
  if (state.loggedIn) await showStation(); else showLogin();
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('login-error').textContent = '';
  const field = $('login-code');
  try {
    await api('/api/station/login', { code: field.value });
    field.value = '';
    await loadState();
    await showStation();
  } catch (e) {
    $('login-error').textContent = e.message;
  }
});

$('logout').addEventListener('click', async () => {
  await api('/api/station/logout', {}).catch(() => {});
  plate.clear();
  select(null);
  showLogin();
});

$('material').addEventListener('change', () => {
  store('laserstation-materiale', $('material').value);
  renderPreset();
  plateChanged();
});
$('refresh').addEventListener('click', loadJobs);
$('class-filter').addEventListener('change', renderJobs);
$('search').addEventListener('input', renderJobs);
$('auto-place').addEventListener('click', autoPlace);
$('rotate').addEventListener('click', () => { if (selected) rotate(plate.get(selected)); });
$('remove').addEventListener('click', () => { if (selected) removeFromPlate(selected); });
$('clear').addEventListener('click', () => {
  for (const id of [...plate.keys()]) removeFromPlate(id);
});
$('check').addEventListener('click', checkPlate);
$('approve-placement').addEventListener('click', approvePlacement);
$('prepare-transfer').addEventListener('click', prepareTransfer);
$('send-transfer').addEventListener('click', sendTransfer);
$('open').addEventListener('click', openPlate);
$('mark-done').addEventListener('click', reviewCompletion);
$('confirm-done').addEventListener('click', markDone);
$('complete-dialog').addEventListener('cancel', event => { if (completing) event.preventDefault(); });
$('station-help').addEventListener('click', () => $('station-help-dialog').showModal());
$('next-action').addEventListener('click', locateNextStep);
document.querySelectorAll('[data-station-close]').forEach(button => button.addEventListener('click', () => {
  if (button.dataset.stationClose === 'complete-dialog' && completing) return;
  $(button.dataset.stationClose).close();
}));
$('bed').addEventListener('pointerdown', (event) => { if (regionMode !== 'pick') drawBedPoint(event); else if (event.target === $('bed') || event.target.classList.contains('sheet')) select(null); });
$('material-photo').addEventListener('change', (event) => loadMaterialPhoto(event.target.files[0]).catch((e) => materialMessage(e.message, true, 'photo-result')));
$('photo-preview').addEventListener('pointerdown', (event) => {
  if (!photoData || calibrationCorners.length >= 4 || event.button !== 0) return;
  const point = $('photo-preview').createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
  const p = point.matrixTransform($('photo-preview').getScreenCTM().inverse());
  if (p.x < 0 || p.y < 0 || p.x > photoSize.width || p.y > photoSize.height) return;
  calibrationCorners.push([Math.round(p.x), Math.round(p.y)]); renderPhotoPreview();
});
$('calibration-reset').addEventListener('click', () => { calibrationCorners = []; renderPhotoPreview(); });
$('calibration-cancel').addEventListener('click', () => { photoReadSerial++; sceneSerial++; photoData = null; photoSize = null; calibrationCorners = []; $('material-photo').value = ''; renderMaterial(); materialMessage('', false, 'photo-result'); });
$('height-confirm').addEventListener('change', renderPhotoPreview);
$('material-height').addEventListener('input', renderPhotoPreview);
$('photo-submit').addEventListener('click', submitPhoto);
$('approve-material').addEventListener('click', approveMaterial);
$('material-clear').addEventListener('click', async () => {
  try {
    await api('/api/station/material/clear', {});
    sceneSerial++; scene = null; editingRegions = []; selectedRegion = null; drawing = []; regionMode = 'pick'; approvalToken = null;
    plateChanged();
    materialMessage('Materialefotoet er fjernet. Du arbejder nu på en manuel plade.'); renderMaterial(); renderBed();
  } catch (e) { materialMessage(e.message, true); }
});
for (const [id, mode] of [['region-pick', 'pick'], ['region-usable', 'usable'], ['region-hole', 'hole']]) {
  $(id).addEventListener('click', () => { drawing = []; regionMode = mode; renderMaterial(); renderBed(); });
}
$('region-finish').addEventListener('click', finishRegion);
$('region-undo').addEventListener('click', () => { drawing.pop(); renderBed(); renderMaterial(); });

setInterval(() => {
  if (!drag && !busy && document.visibilityState === 'visible' && !$('station').hidden) loadJobs();
}, 30000);

start();
