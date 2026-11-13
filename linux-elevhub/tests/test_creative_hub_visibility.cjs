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
  append(...children) { for (const child of children) child.parentElement = this; this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  click() { if (!this.disabled) this.listeners.click?.({ preventDefault() {} }); }
  focus() { this.ownerDocument.activeElement = this; }
  closest(selector) { return selector === '[data-app]' ? (this.dataset.app ? this : this.parentElement?.closest(selector)) || null : null; }
  querySelector(selector) {
    for (const child of this.children) {
      if (selector.startsWith('.') && child.className.split(' ').includes(selector.slice(1))) return child;
      const found = child.querySelector(selector); if (found) return found;
    }
    return null;
  }
}
class Document {
  constructor() {
    this.activeElement = new Element('body'); this.activeElement.ownerDocument = this;
    this.elements = new Map(); this.filters = []; this.projectLinks = [];
    for (const id of ['apps', 'empty-filter', 'workshop-count', 'workshop-scroll-hint', 'workshops-title', 'offline', 'ingen']) this.add(new Element('div', id));
  }
  add(element) { element.ownerDocument = this; this.elements.set(element.id, element); return element; }
  createElement(tag) { const element = new Element(tag); element.ownerDocument = this; return element; }
  getElementById(id) { return this.elements.get(id); }
  querySelectorAll(selector) {
    if (selector === '[data-filter]') return this.filters;
    if (selector === '[data-project-link]') return this.projectLinks;
    return [];
  }
}
const document = new Document();
const filter = group => { const button=document.createElement('button');button.dataset.filter=group;document.filters.push(button);return button; };
const allFilter=filter('alle'),buildFilter=filter('byg'),codeFilter=filter('kode');
const retained = ['folie', 'laser', '3dprint', 'scratch', 'microbit', 'internet'];
const workshopInfo = Object.fromEntries(retained.map(id=>[id,{name:id,group:['folie','laser','3dprint'].includes(id)?'byg':'kode',short:id,image:id+'.png'}]));
const context = {
  console, document,
  window: { CreativeWorkshops: workshopInfo, addEventListener() {} },
  LOKALE_APP_URLER: {},
  vis(id, connected) {
    document.getElementById('apps').hidden = id !== 'apps';
    document.getElementById('offline').hidden = id !== 'offline';
    document.getElementById('ingen').hidden = id !== 'ingen';
    context.connectionState = connected;
  },
  aabn(event, app, model) { context.lastLaunch = {id: app.id, model}; },
  // Layout and focus are exercised by the real browser acceptance tests.
  requestAnimationFrame() {}, setTimeout() {}
};
const script = fs.readFileSync(path.join(__dirname, '..', 'server', 'elevhub', 'web', 'assets', 'creative-hub.js'), 'utf8')
  .replace(/\nopdater\(\);\s*$/, '');
vm.runInNewContext("let seneste='',starter=false;" + script, context);
const app = (id, aaben, klar = true) => ({ id, aaben, klar, type: 'vaerksted', navn: id });
const cards = () => document.getElementById('apps').children;
const ids = () => cards().map(card=>card.dataset.app);
const card = id => cards().find(card=>card.dataset.app===id);
const launch = id => card(id).querySelector('.app-open');
const status = id => card(id).querySelector('.availability').textContent;

context.tegn([app('3dprint', false), app('scratch', true), app('laser', true)]);
assert.deepEqual(ids(), ['laser', 'scratch'], 'online catalog follows teacher visibility and pupil order');
assert.equal(document.getElementById('workshop-count').textContent, '2 værksteder');
launch('laser').click();assert.deepEqual(context.lastLaunch,{id:'laser',model:false});
context.tegn([app('laser', true, false)]);
assert.deepEqual(ids(), ['laser'], 'not-ready open app remains visible');
assert.equal(launch('laser').disabled,true,'not-ready app stays unavailable');
assert.equal(status('laser'),'Ikke klar endnu');
context.lastLaunch=null;launch('laser').click();assert.equal(context.lastLaunch,null,'disabled workshop does not launch');

context.tegn([app('3dprint', false), app('laser', false), app('scratch', false)]);
assert.equal(document.getElementById('ingen').hidden, false, 'no open workshops keeps empty state');
assert.equal(document.getElementById('offline').hidden, true, 'empty state is not connection error');
assert.equal(document.getElementById('empty-filter').hidden, true, 'filter empty hint stays hidden without open workshops');
context.tegn([app('3dprint', true)]);
assert.deepEqual(ids(), ['3dprint'], 'reopening restores card');
launch('3dprint').click();assert.deepEqual(context.lastLaunch,{id:'3dprint',model:true},'3D card opens drawing workshop directly');
context.tegn([app('3dprint', true, false)]);
assert.equal(launch('3dprint').disabled,true,'3D launch follows readiness');

context.tegn([app('laser',true),app('scratch',true)]);
codeFilter.click();assert.deepEqual(ids(),['scratch'],'code filter selects open coding workshops');
buildFilter.click();assert.deepEqual(ids(),['laser'],'build filter selects open making workshops');
context.tegn([app('scratch',true)]);assert.equal(document.getElementById('empty-filter').hidden,false,'empty selected group offers filter recovery');
allFilter.click();assert.deepEqual(ids(),['scratch']);assert.equal(document.getElementById('empty-filter').hidden,true);

context.window.CreativeHub.connection(false, true);
context.tegn([app('scratch', true), {...app('microbit', true), requires_internet: true}, app('laser', true)]);
assert.deepEqual(ids(), ['laser', 'scratch', 'microbit'], 'offline catalog keeps local workshops visible in pupil order');
assert.equal(context.connectionState,false,'offline catalog does not claim a server connection');
assert.equal(status('laser'),'Tilgængelig offline');
assert.equal(launch('laser').disabled,false,'local workshop remains launchable offline');
assert.equal(status('microbit'),'Kræver internet','micro:bit declares its external internet dependency');
assert.equal(launch('microbit').disabled,false,'internet-dependent workshop remains launchable while offline');
context.tegn([{...app('3dprint', true), offline_unavailable: true}]);
assert.equal(status('3dprint'),'Kræver serverforbindelse');
assert.equal(launch('3dprint').disabled,true,'server-dependent workshop remains unavailable offline');
context.tegn([app('laser',false)]);
assert.deepEqual(ids(),['laser'],'offline local catalog retains its visibility');
assert.equal(launch('laser').disabled,true,'offline presentation cannot grant launch permission');

// An older catalog must not bring retired workshops back, including native aliases.
const retired = ['skolegpt', 'kredsloeb', 'datatraener'];
const stale = [...[...retained].reverse().map(id => app(id, true)), ...retired.map(id => app(id, true)),
  {...app('old-alias', true), native_app: 'kredsloeb'}];
context.window.CreativeHub.connection(true, false);
context.tegn(stale);
assert.deepEqual(ids(), retained, 'online stale catalog hides retired entries and aliases');
context.window.CreativeHub.connection(false, true);
context.tegn(stale);
assert.deepEqual(ids(), retained, 'offline stale catalog hides retired entries and aliases');

const metadata = fs.readFileSync(path.join(__dirname, '..', 'server', 'elevhub', 'web', 'assets', 'workshops.js'), 'utf8');
const metadataContext = {window: {}};
vm.runInNewContext(metadata, metadataContext);
assert.deepEqual(Object.keys(metadataContext.window.CreativeWorkshops).sort(), [...retained].sort());
const template = fs.readFileSync(path.join(__dirname, '..', 'ansible', 'roles', 'kiosk', 'templates', 'start.html.j2'), 'utf8');
const addressFunction = template.match(/function model3dAdresse\(app\) \{[\s\S]*?\n\}/)[0];
const addressContext = {URL, adresse: app => app.address};
vm.runInNewContext(addressFunction, addressContext);
assert.equal(addressContext.model3dAdresse({address: 'http://example.test/proxy/'}), 'http://example.test/proxy/model3d.html');
assert.equal(addressContext.model3dAdresse({address: 'http://example.test/'}), 'http://example.test/model3d.html');
// The offline list now comes from the installed app manifests, which the core
// package writes from APP_MANIFESTS. Every retained local workshop needs one.
assert.equal(/const OFFLINE_APPS\b/.test(template), false, 'no hard-coded offline list next to the manifests');
const packaging = fs.readFileSync(path.join(__dirname, '..', 'packaging', 'prepare_inputs.py'), 'utf8');
const manifestBlock = packaging.match(/APP_MANIFESTS = \{([\s\S]*?)\n\}/)[1];
const manifestIds = [...manifestBlock.matchAll(/\("([a-z0-9]+)",/g)].map(match => match[1]);
assert.deepEqual(manifestIds.sort(), retained.filter(id => id !== 'internet').sort(), 'every retained local workshop has an app manifest');

console.log('creative hub visibility regression: ok');
