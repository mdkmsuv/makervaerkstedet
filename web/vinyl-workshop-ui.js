import {installDesignInspiration} from './design-inspiration.js';
import {addActionIcon} from './laser-action-icons.js';

const $ = id => document.getElementById(id);
const node = (tag, attrs = {}, text) => {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (text != null) element.textContent = text;
  return element;
};
const button = (id, text) => node('button', {id, type:'button', class:'quiet'}, text);

const LEVEL_NAMES = ['Former og dit navn', 'Placér og gentag', 'Tegn og saml', 'Alle værktøjer'];

// Vinyl keeps the existing inspiration and project guidance. This layer only
// makes the existing editor easier to scan; it does not perform geometry work.
export function installVinylWorkshop(vinyl, callbacks = {}) {
  const inspiration = installDesignInspiration(true, callbacks);
  document.body.classList.add('vinyl-workshop');

  const tools = document.querySelector('.tools-panel');
  const grid = tools?.querySelector('.tool-grid');
  const toolbar = document.querySelector('.canvas-toolbar > div');
  if (!tools || !grid) return inspiration;

  const select = tools.querySelector('[data-tool="select"]');
  if (select && toolbar && !toolbar.contains(select)) toolbar.prepend(select);

  const gear = $('add-gear');
  const box = tools.querySelector('.box-generator');
  if (gear) gear.hidden = true;
  if (box) box.hidden = true;

  const tabs = node('div', {'class':'vinyl-tool-tabs', 'aria-label':'Find et værktøj'});
  let category = 'build';
  let currentLevel = 0;
  const toolButtons = [...grid.querySelectorAll('.tool')].filter(control => control !== select && control !== gear);
  const combine = tools.querySelector('.combine-tools');
  const library = $('library-button');
  const groupOf = control => {
    if (control.dataset.add || control.id === 'add-text') return 'build';
    return 'draw';
  };
  const showCategory = (next, elevate = true) => {
    category = next;
    const requiredLevel = next === 'more' ? 3 : next === 'draw' ? 2 : 0;
    if (elevate && currentLevel < requiredLevel) callbacks.selectLevel?.(requiredLevel);
    for (const control of toolButtons) control.classList.toggle('vinyl-tool-group-hidden', groupOf(control) !== next);
    grid.classList.toggle('vinyl-tool-group-hidden', next === 'more');
    if (combine) combine.classList.toggle('vinyl-tool-group-hidden', next !== 'more');
    if (library) library.classList.toggle('vinyl-tool-group-hidden', next !== 'build');
    for (const tab of tabs.children) {
      const active = tab.dataset.category === next;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-pressed', String(active));
    }
  };

  for (const [key, label] of [['build', 'Former'], ['draw', 'Tegn'], ['more', 'Saml']]) {
    const tab = button(`vinyl-tool-tab-${key}`, label);
    tab.dataset.category = key;
    tab.setAttribute('aria-pressed', String(key === 'build'));
    tab.addEventListener('click', () => showCategory(key));
    tabs.append(tab);
  }
  tools.querySelector('h2')?.after(tabs);
  if (library && !grid.nextElementSibling?.isSameNode(library)) grid.after(library);

  const levelDetails = node('details', {'class':'vinyl-tool-levels'});
  const levelSummary = node('summary');
  const levels = document.querySelector('.level-bar');
  if (levels) {
    levels.setAttribute('aria-label', 'Vælg hvor mange værktøjer du vil se');
    levels.querySelectorAll('[data-level]').forEach((control, i) => {
      if (LEVEL_NAMES[i]) control.querySelector('strong').textContent = LEVEL_NAMES[i];
    });
    levelDetails.append(levelSummary, levels);
    tools.append(levelDetails);
  }

  // The shared icon set keeps the project actions legible without changing
  // their existing labels, including configureVinylUI's “SVG til Brother”.
  for (const [id, icon] of Object.entries({
    'new-button':'plus-file', 'import-button':'folder', 'save-project':'save',
    'preview-button':'eye', 'export-button':'download', 'help-button':'help',
    'library-button':'library'
  })) {
    const action = $(id);
    // configureVinylUI may have replaced the original export markup with a
    // text-only Brother label. Keep the icon as the sole download affordance.
    if (id === 'export-button' && action) action.textContent = action.textContent.replace(/\s*↓\s*$/, '');
    addActionIcon(action, icon);
  }

  showCategory('build', false);
  const update = (state, level) => {
    currentLevel = level;
    if ((category === 'draw' && level < 1) || (category === 'more' && level < 2)) category = 'build';
    if (levelSummary) levelSummary.textContent = `Værktøjer: ${LEVEL_NAMES[level] || LEVEL_NAMES[0]}`;
    if (gear) gear.hidden = true;
    if (box) box.hidden = true;
    showCategory(category, false);
    inspiration.update(state, level);
  };
  return {...inspiration, update};
}
