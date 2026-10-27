const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag, id = '') {
    this.tagName = tag.toUpperCase(); this.id = id; this.className = '';
    this.dataset = {}; this.children = []; this.hidden = false;
    this.textContent = ''; this.disabled = false; this.attributes = {};
    this.listeners = {};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  click() { this.listeners.click?.({ preventDefault() {} }); }
  focus() { this.ownerDocument.activeElement = this; }
  closest(selector) { return selector === '[data-app]' ? this.appCard || null : null; }
  querySelectorAll(selector) {
    if (selector === 'a,button') return this.children.filter(child => ['A', 'BUTTON'].includes(child.tagName));
    return [];
  }
  scrollIntoView() {}
}

class Document {
  constructor() {
    this.activeElement = new Element('body'); this.activeElement.ownerDocument = this;
    this.elements = new Map(); this.filters = []; this.discover = []; this.projectLinks = [];
    for (const id of ['apps', 'empty-filter', 'workshop-count', 'ticket-link', 'shuffle-idea',
      'ticket-workshop', 'ticket-title', 'ticket-text', 'workshop-detail', 'hub-home',
      'detail-start', 'detail-access', 'detail-name', 'detail-tag', 'detail-title',
      'detail-intro', 'detail-tools', 'detail-image', 'detail-steps', 'detail-ideas',
      'detail-tip-text', 'fejl']) this.add(new Element('div', id));
    this.elements.get('shuffle-idea').tagName = 'BUTTON';
    this.elements.get('detail-start').tagName = 'BUTTON';
  }
  add(element) { element.ownerDocument = this; this.elements.set(element.id, element); return element; }
  createElement(tag) { const element = new Element(tag); element.ownerDocument = this; return element; }
  getElementById(id) { return this.elements.get(id); }
  querySelectorAll(selector) {
    if (selector === '[data-filter]') return this.filters;
    if (selector === '[data-discover]') return this.discover;
    if (selector === '[data-project-link]') return this.projectLinks;
    return [];
  }
  querySelector() { return null; }
}

const document = new Document();
document.title = 'Værkstedet · Makerværkstedet';
const discoverPrint = new Element('button'); discoverPrint.dataset.discover = '3dprint';
const discoverLaser = new Element('button'); discoverLaser.dataset.discover = 'laser';
document.discover.push(discoverPrint, discoverLaser);
const location = { hash: '' };
const context = {
  console, document, location,
  window: { CreativeWorkshops: {
    laser: { name: 'Laser', group: 'byg', tag: 'LASER', image: 'laser.webp', title: 'Laser', intro: 'Laser', tools: 'Laser', tip: 'Tip',
      steps: [['Tegn', 'Tegn'], ['Vælg', 'Vælg'], ['Se', 'Se']], ideas: [['Laserraket', 'Lav en raket']] },
    '3dprint': { name: '3D-print', group: 'byg', tag: '3D', image: 'print.webp', title: 'Print', intro: 'Print', tools: 'Print', tip: 'Tip',
      steps: [['Tegn', 'Tegn'], ['Byg', 'Byg'], ['Print', 'Print']], ideas: [['Spilfigur', 'Lav en figur']] },
    scratch: { name: 'Scratch', group: 'kode', tag: 'KODE', image: 'scratch.webp', title: 'Scratch', intro: 'Scratch', tools: 'Kode', tip: 'Tip',
      steps: [['Vælg', 'Vælg'], ['Kod', 'Kod'], ['Test', 'Test']], ideas: [['Spil', 'Lav et spil']] }
  }, addEventListener() {}, scrollTo() {} },
  LOKALE_APP_URLER: {},
  vis(id, connected) {
    document.getElementById('apps').hidden = id !== 'apps';
    document.getElementById('offline').hidden = id !== 'offline';
    document.getElementById('ingen').hidden = id !== 'ingen';
    context.connectionState = connected;
  },
  setTimeout() {}
};
context.document.add(new Element('div', 'offline'));
context.document.add(new Element('div', 'ingen'));
context.connectionState = false;
const script = fs.readFileSync(path.join(__dirname, '..', 'server', 'elevhub', 'web', 'assets', 'creative-hub.js'), 'utf8')
  .replace(/\nopdater\(\);\s*$/, '');
vm.runInNewContext("let seneste='',starter=false;" + script, context);

const app = (id, aaben, klar = true) => ({ id, aaben, klar, type: 'vaerksted', navn: id });
context.tegn([app('3dprint', false), app('laser', true), app('scratch', true)]);
assert.deepEqual(document.getElementById('apps').children.map(card => card.dataset.app), ['laser', 'scratch']);
assert.equal(document.getElementById('workshop-count').textContent, '2 værksteder');
assert.equal(discoverPrint.hidden, true, 'closed discovery entry is hidden');
assert.equal(discoverLaser.hidden, false, 'open discovery entry remains visible');
context.routeWorkshop();
assert.equal(document.title, 'Værkstedet · Makerværkstedet', 'home route uses the startup-ready title');
const firstIdea = document.getElementById('ticket-title').textContent;
document.getElementById('shuffle-idea').click();
assert.notEqual(document.getElementById('ticket-title').textContent, firstIdea, 'idea button still rotates open ideas');
context.tegn([app('laser', true, false)]);
assert.equal(document.getElementById('apps').children[0].dataset.app, 'laser', 'not-ready open app remains visible');
assert.equal(document.getElementById('apps').children[0].children[1].children[3].children[0].disabled, true, 'not-ready app stays unavailable');

location.hash = '#vaerksted/3dprint'; context.routeWorkshop();
assert.equal(document.getElementById('workshop-detail').hidden, true, 'closed deep link must stay on home');
location.hash = '#vaerksted/laser'; context.routeWorkshop();
assert.equal(document.getElementById('workshop-detail').hidden, false, 'open deep link must work');
assert.equal(document.title, 'Laser · Værkstedet', 'workshop detail gets its own title');
location.hash = ''; context.routeWorkshop();
assert.equal(document.title, 'Værkstedet · Makerværkstedet', 'returning home restores the startup-ready title');

context.tegn([app('3dprint', false), app('laser', false), app('scratch', true)]);
assert.equal(document.getElementById('workshop-detail').hidden, true, 'closing current workshop must leave detail');
assert.equal(document.getElementById('ticket-link').dataset.app, 'scratch', 'idea link follows open workshops');

context.tegn([app('3dprint', false), app('laser', false), app('scratch', false)]);
assert.equal(document.getElementById('ingen').hidden, false, 'no open workshops keeps empty state');
assert.equal(document.getElementById('offline').hidden, true, 'empty state is not connection error');
assert.equal(document.getElementById('empty-filter').hidden, true, 'filter empty hint stays hidden without open workshops');
assert.equal(document.getElementById('shuffle-idea').disabled, true, 'idea chooser disables with no open idea workshops');
assert.equal(document.getElementById('ticket-link').href, '#workshops');

context.tegn([app('3dprint', true)]);
assert.deepEqual(document.getElementById('apps').children.map(card => card.dataset.app), ['3dprint'], 'reopening restores card');
assert.equal(discoverPrint.hidden, false, 'reopening restores discovery entry');
assert.equal(document.getElementById('ticket-link').dataset.app, '3dprint', 'reopening restores idea entry');

context.window.CreativeHub.connection(false, true);
context.tegn([app('laser', true), app('scratch', true), {...app('microbit', true), requires_internet: true}]);
assert.deepEqual(document.getElementById('apps').children.map(card => card.dataset.app), ['laser', 'scratch', 'microbit'], 'offline catalog keeps local workshops visible');
assert.equal(document.getElementById('apps').children[0].children[0].children[1].textContent, 'Tilgængelig offline', 'offline status is explicit');
assert.equal(document.getElementById('apps').children[0].children[1].children[3].children[0].disabled, false, 'local workshop remains launchable offline');
const microbitCard = document.getElementById('apps').children[2];
assert.equal(microbitCard.children[0].children[1].textContent, 'Kræver internet', 'micro:bit declares its external internet dependency');
assert.equal(microbitCard.children[1].children[3].children[0].disabled, false, 'internet-dependent workshop remains launchable while offline');
console.log('creative hub visibility regression: ok');
