import {LASER_GUIDES, getLaserGuide} from './laser-project-guides.js';
import {previewSvg} from './design-inspiration.js';
import {addActionIcon} from './laser-action-icons.js';

const $ = id => document.getElementById(id);
const node = (tag, attrs = {}, text) => {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (text != null) element.textContent = text;
  return element;
};
const button = (id, text, style = 'secondary') => node('button', {id, type:'button', class:style}, text);
const LEVEL_NAMES = ['Former og tekst', 'Placér og gentag', 'Tegn og saml', 'Alle værktøjer'];

// This controller only changes guidance and reveals existing controls. Geometry
// changes always belong to the editor actions that the pupil chooses themselves.
export function installLaserWorkshop(_vinyl, callbacks) {
  document.body.classList.add('desktop-studio', 'laser-studio', 'laser-workshop');
  const editor = document.querySelector('.editor');
  const tools = document.querySelector('.tools-panel');
  const properties = document.querySelector('.properties-panel');
  const inspector = node('div', {class:'laser-inspector', 'aria-label':'Værktøjer og valgte dele'});
  inspector.append(tools, properties);
  editor.append(inspector);
  document.querySelector('.starter').remove();
  tools.querySelector('h2').textContent = 'Byg din idé';
  properties.querySelector('h2').textContent = 'Den valgte del';
  const actions = document.querySelector('.project-actions');
  const projectsButton = button('laser-projects-button', 'Projektguide');
  const resume = button('laser-guide-resume', 'Fortsæt guide');
  resume.hidden = true;
  actions.prepend(projectsButton, resume);
  actions.append($('export-button'), $('help-button'), $('send-button'));
  $('help-button').textContent = 'Hjælp';
  $('help-button').setAttribute('title', 'Hjælp til værktøjerne');
  $('project-name').setAttribute('placeholder', 'Giv din tegning et navn');
  $('empty-state').querySelector('span').textContent = 'Tilføj en form til højre, eller vælg en projektguide.';

  // Selection stays beside Undo; creation tools are grouped in one place.
  const select = tools.querySelector('[data-tool="select"]');
  document.querySelector('.canvas-toolbar > div').prepend(select);
  const tabs = node('div', {class:'laser-tool-tabs', 'aria-label':'Find et værktøj'});
  let category = 'build', currentLevel = 0;
  const toolButtons = [...tools.querySelectorAll('.tool-grid .tool')];
  const combine = tools.querySelector('.combine-tools');
  const box = tools.querySelector('.box-generator');
  const grid = tools.querySelector('.tool-grid');
  const groupOf = control => control.dataset.add || ['add-text', 'add-gear'].includes(control.id) ? 'build' : 'draw';
  function showCategory(next, elevate = true) {
    category = next;
    const requiredLevel = next === 'more' ? 3 : next === 'draw' ? 2 : 0;
    if (elevate && currentLevel < requiredLevel) callbacks.selectLevel(requiredLevel);
    for (const control of toolButtons) control.classList.toggle('laser-tool-group-hidden', groupOf(control) !== next);
    grid.classList.toggle('laser-tool-group-hidden', next === 'more');
    combine.classList.toggle('laser-tool-group-hidden', next !== 'more');
    box.classList.toggle('laser-tool-group-hidden', next !== 'more');
    $('library-button').classList.toggle('laser-tool-group-hidden', next !== 'build');
    for (const tab of tabs.children) {const active = tab.dataset.category === next; tab.classList.toggle('active', active); tab.setAttribute('aria-pressed', String(active));}
  }
  for (const [key, label] of [['build','Former'], ['draw','Tegn'], ['more','Saml']]) {
    const tab = button(`laser-tool-tab-${key}`, label, 'quiet');
    tab.dataset.category = key;
    tab.onclick = () => showCategory(key);
    tabs.append(tab);
  }
  tools.querySelector('h2').after(tabs);
  grid.after($('library-button'));
  const levelDetails = node('details', {class:'laser-tool-levels'});
  const levelSummary = node('summary');
  const levels = document.querySelector('.level-bar');
  levels.setAttribute('aria-label', 'Vælg hvor mange værktøjer du vil se');
  levels.querySelectorAll('[data-level]').forEach((control, i) => {control.querySelector('strong').textContent = LEVEL_NAMES[i];});
  levelDetails.append(levelSummary, levels);
  tools.append(levelDetails);
  const paper = node('details', {class:'laser-more-tools'});
  paper.append(node('summary', {}, 'Papir og gitter'), tools.querySelector('.board-settings'));
  tools.append(paper);
  properties.querySelectorAll('#advanced-tools details').forEach(details => details.open = false);
  combine.querySelector('p').textContent = 'Vælg flere dele med Shift eller ved at trække en ramme på papiret.';

  const dialog = node('dialog', {id:'laser-project-dialog', class:'laser-project-dialog', 'aria-labelledby':'laser-project-title'});
  const dialogHeader = node('div', {class:'laser-project-dialog-header'});
  const heading = node('div');
  heading.append(node('h2', {id:'laser-project-title'}, 'Hvad vil du bygge?'), node('p', {}, 'Vælg en idé. Du bygger selv med hjælp, ét trin ad gangen.'));
  const dialogClose = button('laser-project-close', '×', 'icon-button');
  dialogClose.setAttribute('aria-label', 'Luk projekter');
  dialogClose.onclick = () => dialog.close();
  dialogHeader.append(heading, dialogClose);
  const search = node('input', {id:'laser-project-search', type:'search', placeholder:'Find fx et skilt, en medalje eller en æske', 'aria-label':'Søg i projektguider'});
  const cards = node('div', {id:'laser-project-cards'});
  function drawCards() {
    const query = search.value.trim().toLocaleLowerCase('da');
    cards.replaceChildren();
    for (const guide of LASER_GUIDES.filter(g => `${g.title} ${g.intro} ${g.skill}`.toLocaleLowerCase('da').includes(query))) {
      const card = node('article', {class:'laser-project-card', 'data-guide-id':guide.id});
      const image = previewSvg(guide.id, false);
      image.setAttribute('aria-label', `Et eksempel på ${guide.title.toLocaleLowerCase('da')}`);
      const body = node('div', {class:'laser-project-card-body'});
      const choose = button(`choose-${guide.id}`, 'Vælg projekt', 'primary');
      choose.onclick = () => {if (callbacks.startGuide(guide.id) === true) {dialog.close(); stepTitle.focus();}};
      body.append(node('p', {class:'laser-project-skill'}, `${guide.steps.length} trin · ${LEVEL_NAMES[guide.level]}`), node('h3', {}, guide.title), node('p', {}, guide.intro), choose);
      card.append(image, body); cards.append(card);
    }
    if (!cards.children.length) cards.append(node('p', {role:'status'}, 'Ingen projekter matcher. Prøv et andet ord.'));
  }
  dialog.append(dialogHeader, search, cards); document.body.append(dialog);
  search.addEventListener('input', drawCards);
  const openLibrary = () => {search.value = ''; drawCards(); dialog.showModal(); search.focus();};
  projectsButton.onclick = openLibrary;

  const panel = node('aside', {id:'laser-guide-panel', class:'laser-guide', 'aria-labelledby':'laser-guide-title'});
  panel.hidden = true;
  panel.addEventListener('keydown', event => {
    // Reading/navigating the guide must not nudge or delete a selected shape.
    if (event.key.startsWith('Arrow') || ['Delete','Backspace','Escape'].includes(event.key)) event.stopPropagation();
  });
  const header = node('div', {class:'laser-guide-header'});
  const title = node('h2', {id:'laser-guide-title'});
  const close = button('laser-guide-close', '×', 'quiet');
  close.setAttribute('aria-label', 'Luk guiden og behold tegningen');
  close.onclick = () => {callbacks.toggleHelp(true); resume.focus();};
  header.append(title, close);
  const body = node('div', {class:'laser-guide-body'});
  const art = node('div', {class:'laser-guide-art'});
  const count = node('p', {class:'laser-guide-count', 'aria-live':'polite'});
  const progress = node('progress', {id:'laser-guide-progress', class:'laser-guide-progress', 'aria-label':'Dit trin i guiden'});
  const stepTitle = node('h3', {id:'laser-guide-step-title', tabindex:'-1'});
  const text = node('div', {class:'laser-guide-text'}), stepText = node('p'); text.append(stepText);
  const hint = node('details', {id:'laser-guide-hint'}), hintText = node('p');
  hint.append(node('summary', {}, 'Jeg vil gerne have et tip'), hintText);
  const locate = button('laser-guide-locate', 'Vis mig værktøjet');
  const locateStatus = node('p', {class:'muted', role:'status'});
  const check = node('p', {class:'laser-guide-check'});
  const reflectionLabel = node('label', {for:'laser-guide-reflection'}), reflectionQuestion = node('span');
  const reflection = node('textarea', {id:'laser-guide-reflection', rows:3, maxlength:1000, placeholder:'Skriv en tanke, eller fortæl den til en anden.'});
  reflectionLabel.append(reflectionQuestion, reflection);
  reflection.addEventListener('input', () => callbacks.reflect(reflection.value));
  body.append(art, node('p', {class:'muted'}, 'Et eksempel – du bestemmer udtrykket.'), count, progress, stepTitle, text, locate, locateStatus, hint, check, reflectionLabel);
  const navigation = node('div', {class:'laser-guide-navigation'});
  const previous = button('laser-guide-prev', '← Tilbage'), next = button('laser-guide-next', 'Næste trin →', 'primary'), finish = button('laser-guide-finish', 'Afslut guide', 'primary');
  navigation.append(previous, next, finish); panel.append(header, body, navigation); editor.prepend(panel);
  let activeGuide = null, currentLearning = null, activeIndex = 0, renderedStep = '', highlighted = null;
  function clearHighlight() {highlighted?.classList.remove('laser-tool-target'); highlighted = null; locateStatus.textContent = '';}
  const changeStep = offset => {callbacks.guideStep(activeIndex + offset); stepTitle.focus(); body.scrollTop = 0;};
  previous.onclick = () => changeStep(-1); next.onclick = () => changeStep(1);
  finish.onclick = () => {callbacks.finishGuide(); resume.focus();};
  resume.onclick = () => {callbacks.toggleHelp(false); stepTitle.focus();};
  for (const [id, icon] of Object.entries({
    'send-button':'mail', 'confirm-send':'mail', 'save-project':'save',
    'import-button':'folder', 'preview-button':'eye', 'laser-projects-button':'book',
    'new-button':'plus-file', 'export-button':'download', 'help-button':'help',
    'library-button':'library', 'laser-guide-locate':'target',
  })) addActionIcon($(id), icon);
  locate.onclick = () => {
    const step = activeGuide?.steps[activeIndex];
    if (!step?.target) return;
    clearHighlight();
    const target = document.querySelector(step.target);
    if (!target) return;
    if (target.closest('dialog:not([open])')) {
      locateStatus.textContent = 'Åbn først værktøjets vindue for at fortsætte.';
      return;
    }
    if (target.closest('.tool-grid')) showCategory(groupOf(target));
    if (target.closest('.combine-tools,.box-generator')) showCategory('more');
    const required = target.closest('[data-min-level]');
    if (required && currentLevel < Number(required.dataset.minLevel)) callbacks.selectLevel(Number(required.dataset.minLevel));
    if (target.closest('#object-properties')?.hidden) {
      locateStatus.textContent = 'Vælg først den del på tegningen, du vil ændre. Indstillingerne vises til højre.';
      return;
    }
    for (let parent = target.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
    target.scrollIntoView({block:'nearest', inline:'nearest'});
    highlighted = target; target.classList.add('laser-tool-target');
    if (target.disabled) locateStatus.textContent = 'Vælg først de dele på tegningen, som værktøjet skal bruges på.';
    else {target.focus({preventScroll:true}); locateStatus.textContent = 'Værktøjet er markeret. Du vælger selv, hvornår du bruger det.';}
  };

  function update(state, level) {
    currentLevel = level;
    if (category === 'draw' && level < 1 || category === 'more' && level < 2) category = 'build';
    levelSummary.textContent = `Værktøjer: ${LEVEL_NAMES[level]}`;
    // Paper dimensions remain discoverable without an unrelated skill level.
    tools.querySelector('.board-settings').hidden = false;
    showCategory(category, false);
    const learning = state.learning;
    const guide = learning?.guide === true ? getLaserGuide(learning.id) : null;
    currentLearning = guide ? learning : null; activeGuide = guide;
    const visible = !!guide && !learning.helpHidden;
    panel.hidden = !visible; editor.classList.toggle('laser-guiding', visible);
    resume.hidden = !guide || visible;
    resume.textContent = learning?.completed ? 'Se min guide' : 'Fortsæt guide';
    addActionIcon(resume, 'book');
    if (!guide) {renderedStep = ''; clearHighlight(); return;}
    const index = Math.min(guide.steps.length - 1, Math.max(0, learning.stepIndex || 0));
    activeIndex = index;
    const step = guide.steps[index], key = `${guide.id}:${index}`;
    title.textContent = guide.title;
    count.textContent = `Trin ${index + 1} af ${guide.steps.length}`;
    progress.max = guide.steps.length; progress.value = index + 1;
    if (renderedStep !== key) {
      if (!renderedStep.startsWith(`${guide.id}:`)) art.replaceChildren(previewSvg(guide.id, false));
      renderedStep = key; clearHighlight(); hint.open = false;
      stepTitle.textContent = step.title; stepText.textContent = step.text;
      hintText.textContent = step.hint || ''; hint.hidden = !step.hint;
      check.textContent = `Tænk efter: ${step.check}`;
      reflectionQuestion.textContent = guide.reflection;
    }
    if (!visible) clearHighlight();
    locate.hidden = !step.target;
    previous.disabled = index === 0;
    const last = index === guide.steps.length - 1;
    next.hidden = last; next.disabled = last; finish.hidden = !last;
    reflectionLabel.hidden = !last;
    if (document.activeElement !== reflection) reflection.value = learning.reflection || '';
  }
  return {update, openLibrary};
}
