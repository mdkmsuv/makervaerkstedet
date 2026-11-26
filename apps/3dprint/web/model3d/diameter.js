import {PLATE_SIZE, uprightCylinderSection} from './scene.js';

export const DIAMETER_NAME = 'Diameter (Ø)';
const diameterFormat = new Intl.NumberFormat('da-DK', {useGrouping: false, maximumFractionDigits: 2});

export function parseDiameter(raw) {
  const value = raw.trim();
  return /^-?(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(value) ? Number(value.replace(',', '.')) : NaN;
}

export function mountDiameter({field, model, view, busy, later, mutate, invalid, historyStep, renderUI}) {
  const length = field.querySelector('[data-size="0"]'), width = field.querySelector('[data-size="1"]');
  const label = length.closest('label'), caption = label.querySelector('.measure-name'), widthLabel = width.closest('label');
  const fields = label.parentElement;
  const profile = field.ownerDocument.createElement('button');
  profile.type = 'button';
  profile.id = 'cylinder-profile';
  profile.hidden = true;
  fields.append(profile);
  let selectedId = null, mode = null, editingId = null, shown = [];

  const owns = input => Boolean(mode && (input === length || (mode === 'axes' && input === width)));
  const isSelected = id => model.selected.length === 1 && model.selected[0] === id;
  function commit(id, input, editingMode, raw) {
    if (busy()) { later(() => commit(id, input, editingMode, raw)); return; }
    if (!isSelected(id) || !mode || input.readOnly) return;
    const value = parseDiameter(raw);
    if (!Number.isFinite(value)) { invalid(input, 'Skriv et tal.'); return; }
    if (value < .1 || value > PLATE_SIZE) {
      invalid(input, value > PLATE_SIZE ? `Det tal er for stort. Skriv højst ${PLATE_SIZE} mm, så passer det på printeren.` : 'Det tal er for lille. Skriv mindst 0,1 mm.');
      return;
    }
    const axis = input === length ? 'length' : 'width';
    mutate(() => editingMode === 'diameter' ? model.setDiameter(id, value) : model.setCylinderSize(id, {[axis]: value}));
  }

  for (const [axis, input] of [length, width].entries()) {
    input.addEventListener('change', event => {
      if (!owns(input)) return;
      // A cylinder edit can switch this field's meaning before the generic listener runs.
      event.stopImmediatePropagation();
      input.dataset.typing = 'false';
      commit(selectedId, input, mode, input.value);
    });
    input.addEventListener('keydown', event => {
      if (!owns(input)) return;
      const key = event.key.toLowerCase(), typing = input.dataset.typing === 'true';
      if (key === 'enter') {
        event.preventDefault();
        input.blur();
        if (owns(input) && !input.closest('[hidden]')) input.focus({preventScroll: true});
      }
      if (key === 'escape' && typing) {
        event.preventDefault();
        input.value = shown[axis];
        input.dataset.typing = 'false';
        input.select();
      }
      if ((event.ctrlKey || event.metaKey) && ['z', 'y'].includes(key) && !typing) {
        event.preventDefault();
        historyStep(key === 'y' || event.shiftKey);
      }
    });
  }

  function makeRound(id) {
    if (busy()) { later(() => makeRound(id)); return; }
    if (!isSelected(id)) return;
    const section = uprightCylinderSection(model.object(id));
    if (section && section.diameter === null) mutate(() => model.setCylinderSize(id, {width: section.length}));
  }

  profile.onclick = () => {
    if (!isSelected(selectedId)) return;
    const section = uprightCylinderSection(model.object(selectedId));
    if (!section) return;
    if (section.diameter === null) {
      makeRound(selectedId);
      return;
    }
    editingId = mode === 'diameter' ? selectedId : null;
    renderUI();
    (editingId ? width : length).focus({preventScroll: true});
  };

  return {
    render(node, state, editable) {
      const nextId = node?.id ?? null, section = state ? uprightCylinderSection(node) : null;
      if (nextId !== selectedId || !section || section.diameter === null || node.locked) editingId = null;
      const nextMode = !section ? null : section.diameter === null || editingId ? 'axes' : 'diameter';
      if (nextMode !== mode) {
        const isDiameter = nextMode === 'diameter', name = isDiameter ? DIAMETER_NAME : 'Længde';
        caption.textContent = name;
        label.dataset.tip = `${name} i mm`;
        length.setAttribute('aria-label', label.dataset.tip);
        if (isDiameter) length.id = 'diameter'; else length.removeAttribute('id');
        fields.classList.toggle('diameter-fields', isDiameter);
        widthLabel.hidden = isDiameter;
        for (const [input, isText] of [[length, nextMode !== null], [width, nextMode === 'axes']]) {
          const value = input.value;
          input.type = isText ? 'text' : 'number';
          if (isText) input.inputMode = 'decimal';
          else { input.removeAttribute('inputmode'); input.value = value.replace(',', '.'); }
        }
      }
      if (nextId !== selectedId || nextMode !== mode) for (const input of [length, width]) input.dataset.typing = 'false';
      selectedId = nextId;
      mode = nextMode;
      view.freeCylinderId = editingId;
      profile.hidden = !section;
      if (!section) return;
      const disabled = !editable || Boolean(node.locked);
      profile.disabled = disabled || (section.diameter === null && (section.length < .1 || section.length > PLATE_SIZE));
      profile.textContent = section.diameter === null ? `Gør rund til Ø ${diameterFormat.format(section.length)} mm` : editingId ? 'Vis diameter' : 'Gør oval';
      shown = [mode === 'diameter' ? section.diameter : section.length, section.width].map(value => diameterFormat.format(value));
      for (const [axis, input] of [length, width].entries()) {
        input.readOnly = disabled;
        if (owns(input) && (input !== input.ownerDocument.activeElement || input.dataset.typing !== 'true')) input.value = shown[axis];
      }
    },
  };
}
