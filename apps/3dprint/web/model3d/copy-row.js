import {MAX_PROJECT_OBJECTS} from './project.js';
import {objectMatrix} from './scene.js';

const DIRECTIONS = new Map([['x+', [1, 0]], ['x-', [-1, 0]], ['y+', [0, 1]], ['y-', [0, -1]]]);
const SELECT_FIGURE = 'Vælg én synlig, ulåst figur først.';
const EPSILON = 1e-6;
const CORNER_SEGMENTS = 24;
const CORNER_HALF_STEP = Math.PI / CORNER_SEGMENTS;
const CORNER_EDGE = Math.cos(CORNER_HALF_STEP);
const CORNER_NORMALS = Array.from({length: CORNER_SEGMENTS / 4}, (_, edge) => {
  const angle = (2 * edge + 1) * CORNER_HALF_STEP;
  return [Math.cos(angle), Math.sin(angle)];
});

export function fitsInside(bounds, plate, offset, margin = 0) {
  const insideEdges = [0, 1].every(axis => bounds.min[axis] + offset[axis] >= plate.min[axis] + margin - EPSILON &&
    bounds.max[axis] + offset[axis] <= plate.max[axis] - margin + EPSILON);
  if (!insideEdges || !plate.radii?.every(radius => radius > 0)) return insideEdges;

  // The worker rounds each corner with a quarter of a 24-sided ellipse. Under the
  // outline's symmetry the furthest X and Y edges of the footprint give its worst corner.
  const [x, y] = plate.radii.map((radius, axis) => {
    const middle = (plate.min[axis] + plate.max[axis]) / 2;
    const extent = Math.max(Math.abs(bounds.min[axis] + offset[axis] - middle), Math.abs(bounds.max[axis] + offset[axis] - middle));
    return (extent + margin - (plate.max[axis] - middle - radius)) / radius;
  });
  if (x <= 0 || y <= 0) return true;
  return CORNER_NORMALS.every(([nx, ny]) => x * nx + y * ny <= CORNER_EDGE + EPSILON);
}

function overlapsAt(bounds, other, offset, gap = 0) {
  return [0, 1, 2].every(axis => {
    const margin = axis === 2 ? 0 : gap;
    return bounds.min[axis] + offset[axis] < other.max[axis] + margin - EPSILON &&
      bounds.max[axis] + offset[axis] > other.min[axis] - margin + EPSILON;
  });
}

// Stay on an axis through the original hole. Start beside it or just beyond a blocker on that axis.
export function findHoleCopyOffset({bounds, plate, occupied}) {
  const obstacles = occupied.map(part => part.bounds);
  for (const gap of [2, .5]) {
    const candidates = [];
    for (const direction of DIRECTIONS.values()) {
      const axis = direction[0] ? 0 : 1, sign = direction[axis];
      const nearest = bounds.max[axis] - bounds.min[axis] + gap;
      const distances = new Set([nearest]);
      for (const other of obstacles) {
        const distance = sign > 0 ? other.max[axis] - bounds.min[axis] : bounds.max[axis] - other.min[axis];
        if (distance + gap > nearest) distances.add(distance + gap);
      }
      for (const distance of distances) {
        const offset = [0, 0, 0];
        offset[axis] = sign * distance;
        candidates.push({distance, offset});
      }
    }
    candidates.sort((a, b) => a.distance - b.distance);
    const free = candidates.find(({offset}) => fitsInside(bounds, plate, offset, .5) &&
      !obstacles.some(other => overlapsAt(bounds, other, offset, gap)));
    if (free) return free.offset;
  }
  return null;
}

export function repeatCopyMatrix(model, chosen, lastCopy) {
  if (!lastCopy || chosen.length !== lastCopy.copies.length || !chosen.every(id => lastCopy.copies.includes(id))) return null;
  const current = lastCopy.copies.map(id => model.object(id));
  if (current.some(node => !node)) return null;
  const matrices = current.map(objectMatrix);
  const changed = matrices.findIndex((matrix, index) =>
    matrix.elements.some((value, element) => Math.abs(value - lastCopy.matrices[index][element]) > EPSILON));
  if (changed < 0 && !lastCopy.repeated) return null;
  const index = Math.max(0, changed), source = model.object(lastCopy.sources[index]);
  return source ? matrices[index].multiply(objectMatrix(source).invert()) : null;
}

function selectedSource(model) {
  if (model.selected.length !== 1) return null;
  const [id] = model.selected;
  const node = model.object(id);
  return node && model.data.roots.includes(id) && !node.hidden && !model.lockedTree(id) ? node : null;
}

export function prepareCopyRow(model, {count, spacing, direction, bounds, holeContext}) {
  const source = selectedSource(model);
  if (!source) return {error: SELECT_FIGURE, field: null};
  if (!Number.isSafeInteger(count) || count < 2) return {error: 'Antal i alt skal være et helt tal på mindst 2.', field: 'count'};
  if (!Number.isFinite(spacing) || spacing < .01) return {error: 'Skriv en afstand på mindst 0,01 mm mellem midterne.', field: 'spacing'};
  const step = DIRECTIONS.get(direction);
  if (!step) return {error: 'Vælg en retning på pladen.', field: 'direction'};
  if (!bounds) return {error: 'Figuren kan ikke måles lige nu. Vælg den igen, og prøv en gang til.', field: null};

  const subtree = [];
  function collect(node) {
    subtree.push(node);
    node.children?.forEach(id => collect(model.object(id)));
  }
  collect(source);
  const maxCount = 1 + Math.floor((MAX_PROJECT_OBJECTS - model.data.objects.length) / subtree.length);
  if (maxCount < 2) return {error: 'Der er ikke plads til flere figurer i projektet.', field: null};
  if (count > maxCount) return {error: `Der er plads til højst ${maxCount} figurer i rækken. Sænk antallet.`, field: 'count'};

  const span = (count - 1) * spacing;
  const origin = [0, 0, 0], last = [step[0] * span, step[1] * span, 0];
  const halfPlate = [model.data.plate.w / 2, model.data.plate.d / 2];
  const printerPlate = {min: halfPlate.map(value => -value), max: halfPlate};
  if (!Number.isFinite(span) || ![origin, last].every(offset => fitsInside(bounds, printerPlate, offset))) {
    return {error: 'Rækken går ud over printerpladens kant. Prøv færre figurer, mindre afstand eller en anden retning.', field: null};
  }

  const warnings = [];
  if (source.mode === 'hole') {
    const axis = step[0] ? 0 : 1;
    const holes = holeContext?.occupied.filter(part => part.hole) || [];
    const offsets = Array.from({length: count - 1}, (_, i) => [step[0] * (i + 1) * spacing, step[1] * (i + 1) * spacing, 0]);
    if (spacing < bounds.max[axis] - bounds.min[axis] - EPSILON ||
        offsets.some(offset => holes.some(other => overlapsAt(bounds, other.bounds, offset)))) {
      warnings.push('Hullerne kan overlappe og blive til én åbning. Prøv en større afstand.');
    }
    if (holeContext && ![origin, last].every(offset => fitsInside(bounds, holeContext.plate, offset))) {
      warnings.push('Rækken går ud over kanten på modellen. Et hul kan blive et hak eller slet ikke skære.');
    }
  }

  return {span, warnings, apply() {
    const copies = [], roots = [], sourceMatrix = objectMatrix(source).toArray();
    for (let index = 1; index < count; index++) {
      const ids = new Map(subtree.map(node => [node.id, crypto.randomUUID()]));
      for (const node of subtree) {
        const copy = structuredClone(node);
        copy.id = ids.get(node.id);
        if (copy.children) copy.children = copy.children.map(id => ids.get(id));
        if (node === source) {
          copy.matrix = [...sourceMatrix];
          copy.matrix[12] += step[0] * index * spacing;
          copy.matrix[13] += step[1] * index * spacing;
          roots.push(copy.id);
        }
        copies.push(copy);
      }
    }
    model.change(() => {
      model.data.objects.push(...copies);
      model.data.roots.push(...roots);
    });
    model.selected = [source.id, ...roots];
  }};
}

export function setupCopyRow({model, getBounds, getHoleContext, onCopy}) {
  const opener = document.getElementById('copy-row-open');
  const menu = document.getElementById('more-menu');
  const dialog = document.getElementById('copy-row-dialog');
  const workspace = document.getElementById('workspace');
  const form = dialog.querySelector('form');
  const error = document.getElementById('copy-row-error');
  const measurement = document.getElementById('copy-row-measurement');
  const warnings = document.getElementById('copy-row-warnings');
  const submit = form.querySelector('[type=submit]');
  const fields = ['count', 'spacing', 'direction'].map(name => form.elements.namedItem(name));
  const [count, spacing, direction] = fields;
  const clearError = () => {
    error.hidden = true;
    error.textContent = '';
    fields.forEach(field => field.removeAttribute('aria-invalid'));
  };

  const mm = value => String(Number(value.toFixed(6))).replace('.', ',');
  function readForm() {
    const value = spacing.value.trim();
    const distance = /^(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(value) ? Number(value.replace(',', '.')) : NaN;
    const box = getBounds(), id = model.selected[0];
    return {count: count.valueAsNumber, spacing: distance, direction: direction.value,
      bounds: box && {min: box.min.toArray(), max: box.max.toArray()},
      holeContext: model.object(id)?.mode === 'hole' && getHoleContext?.(id),
    };
  }
  function preview() {
    const values = readForm(), result = prepareCopyRow(model, values);
    const span = (values.count - 1) * values.spacing;
    measurement.textContent = Number.isSafeInteger(values.count) && values.count >= 2 && values.spacing >= .01 && Number.isFinite(span)
      ? `Første til sidste midte: ${mm(span)} mm` : '';
    measurement.hidden = !measurement.textContent;
    warnings.textContent = result.warnings?.join(' ') || '';
    warnings.hidden = !warnings.textContent;
  }

  opener.addEventListener('click', () => {
    menu.open = false;
    clearError();
    form.reset();
    preview();
    dialog.returnValue = '';
    dialog.showModal();
    count.focus();
    count.select();
  });
  form.querySelector('[data-copy-row-cancel]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    (dialog.returnValue === 'copied' ? workspace : menu.querySelector('summary')).focus({preventScroll: true});
  });
  form.addEventListener('input', () => { clearError(); preview(); });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (submit.disabled) return;
    const values = readForm(), result = prepareCopyRow(model, values);
    if (result.error) {
      clearError();
      error.hidden = false;
      error.textContent = result.error;
      const field = result.field && form.elements.namedItem(result.field);
      if (field) {
        field.setAttribute('aria-invalid', 'true');
        field.focus();
      }
      return;
    }
    onCopy(result.apply, `${values.count} figurer på række med ${mm(values.spacing)} mm mellem midterne.`);
    dialog.close('copied');
  });

  return ready => {
    const enabled = ready && selectedSource(model);
    opener.disabled = submit.disabled = !enabled;
    opener.dataset.tipOff = ready ? SELECT_FIGURE : 'Vent, mens modellen beregnes.';
  };
}
