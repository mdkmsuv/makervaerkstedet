const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag, id = '') { this.tagName = tag.toUpperCase(); this.id = id; this.className = ''; this.dataset = {}; this.children = []; this.hidden = false; this.textContent = ''; this.disabled = false; this.attributes = {}; this.listeners = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  focus() { this.ownerDocument.activeElement = this; }
  closest(selector) { return selector === '[data-app]' ? this.appCard || null : null; }
  querySelectorAll(selector) { return selector === 'a,button' ? this.children.filter(child => ['A', 'BUTTON'].includes(child.tagName)) : []; }
  scrollIntoView() {}
}
class Document {
  constructor() {
    this.activeElement = new Element('body'); this.activeElement.ownerDocument = this; this.elements = new Map(); this.filters = []; this.discover = []; this.projectLinks = [];
    for (const id of ['apps','empty-filter','workshop-count','ticket-link','shuffle-idea','ticket-workshop','ticket-title','ticket-text','workshop-detail','hub-home','detail-start','detail-access','detail-name','detail-tag','detail-title','detail-intro','detail-tools','detail-image','detail-steps','detail-ideas','detail-tip-text','fejl','venter','offline','ingen']) this.add(new Element('div', id));
    this.elements.get('shuffle-idea').tagName = 'BUTTON'; this.elements.get('detail-start').tagName = 'BUTTON';
  }
  add(element) { element.ownerDocument = this; this.elements.set(element.id, element); return element; }
  createElement(tag) { const element = new Element(tag); element.ownerDocument = this; return element; }
  getElementById(id) { return this.elements.get(id); }
  querySelectorAll(selector) { if (selector === '[data-filter]') return this.filters; if (selector === '[data-discover]') return this.discover; if (selector === '[data-project-link]') return this.projectLinks; return []; }
  querySelector() { return null; }
}
const document = new Document();
document.title = 'Værkstedet starter';
const frames = [];
const readyTimers = [];
const location = { hash: '', assign(url) { this.href = url; } };
const workshops = {};
for (const id of ['3dprint','laser','folie','microbit','datatraener','skolegpt','scratch','kredsloeb','internet']) workshops[id] = { name:id, group:'byg', tag:'TEST', image:'x.webp', title:id, intro:id, tools:id, tip:id, steps:[['1','1']], ideas:[['idé','idé']] };
let fetchImpl = () => Promise.reject({ name: 'TypeError', message: 'offline' });
const context = {
  console, document, location, URL, AbortController, setTimeout(callback, delay) { if (delay === 500) readyTimers.push(callback); }, clearTimeout() {},
  requestAnimationFrame(callback) { frames.push(callback); },
  fetch(...args) { return fetchImpl(...args); },
  window: { CreativeWorkshops: workshops, addEventListener() {}, scrollTo() {}, open() { return null; } },
  SERVER:'192.0.2.1', PROTOKOL:'https', HUB:'https://192.0.2.1:8773', COMPUTER:'test', UI_TOKEN:'ui-token',
  LOKALE_APP_URLER:{ scratch:'http://127.0.0.1:8792/', datatraener:'http://127.0.0.1:8793/', kredsloeb:'http://127.0.0.1:8794/', laser:'http://127.0.0.1:8795/', folie:'http://127.0.0.1:8796/', projekter:'http://127.0.0.1:8791/' }
};
for (const id of ['3dprint','laser','folie']) { const button = new Element('button'); button.dataset.discover = id; document.discover.push(button); }
for (const filter of ['alle','byg','kode','ide']) { const button = new Element('button'); button.dataset.filter = filter; document.filters.push(button); }

const root = path.join(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'ansible','roles','kiosk','templates','start.html.j2'), 'utf8');
const inline = template.match(/<script>\r?\n([\s\S]*?)\r?\n<\/script>/)[1]
  .replaceAll('{{ kiosk_server | to_json }}', JSON.stringify('192.0.2.1'))
  .replaceAll('{{ kiosk_protokol | to_json }}', JSON.stringify('https'))
  .replaceAll('{{ kiosk_hub_port }}', '8773')
  .replaceAll('{{ inventory_hostname | to_json }}', JSON.stringify('test'))
  .replaceAll('{{ kiosk_enhed.ui_token | to_json }}', JSON.stringify('ui-token'))
  .replaceAll('{{ kiosk_lokale_app_urler | tojson }}', JSON.stringify(context.LOKALE_APP_URLER))
  .replaceAll('{{ kiosk_internet_start | to_json }}', JSON.stringify('https://www.google.com/imghp?hl=da&safe=active')) + '\nwindow.__start = {opdater};';
const creative = fs.readFileSync(path.join(root, 'server','elevhub','web','assets','creative-hub.js'), 'utf8').replace(/opdater\(\);\s*$/, '');
vm.runInNewContext(inline + '\n' + creative, context);
function cards() { return document.getElementById('apps').children; }
function response(apps, status = 200) { return Promise.resolve({ ok: status >= 200 && status < 300, status, async json() { return { apps }; } }); }
const app = (id, aaben = true) => ({ id, aaben, klar:true, type:'vaerksted', navn:id });

(async () => {
  await context.window.__start.opdater();
  assert.equal(cards().length, 9, 'timeout exposes the complete offline catalog');
  assert.equal(context.window.CreativeHub.isOffline(), true);
  // Offline boot also releases the start screen, only after the page has painted.
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(document.title, 'Værkstedet starter');
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.equal(document.title, 'Værkstedet starter');
  frames.shift()();
  assert.equal(document.title, 'Værkstedet · Makerværkstedet');
  // An occluded browser can suspend requestAnimationFrame behind the cover.
  // The prepared DOM must still release the cover rather than deadlock.
  vm.runInNewContext("startupReady = false; document.title = 'Værkstedet starter'; markStartupReady();", context);
  for (let i = 0; i < 8; i++) await Promise.resolve();
  readyTimers.at(-1)();
  assert.equal(document.title, 'Værkstedet · Makerværkstedet', 'occluded page releases without RAF');
  fetchImpl = () => response([app('laser', false), app('scratch', true)]);
  await context.window.__start.opdater();
  assert.equal(context.window.CreativeHub.isOffline(), false, 'successful reconnect clears offline mode');
  assert.equal(cards().length, 1, 'teacher closed policy remains enforced after reconnect');
  assert.equal(cards()[0].dataset.app, 'scratch');
  fetchImpl = () => response([], 403);
  await context.window.__start.opdater();
  assert.equal(context.window.CreativeHub.isOffline(), false, '403 is not treated as offline');
  assert.equal(cards().length, 0, '403 clears stale offline cards');
  fetchImpl = () => Promise.reject({ name: 'TypeError', message: 'offline' });
  await context.window.__start.opdater();
  fetchImpl = () => new Promise(() => {});
  const pending = context.window.__start.opdater();
  await Promise.resolve();
  assert.equal(context.window.CreativeHub.isOffline(), true, 'pending reconnect keeps offline launch state');
  assert.equal(cards().length, 9);
  void pending;
  console.log('kiosk offline reconnect dynamic regression: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
