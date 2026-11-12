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
    for (const id of ['apps','empty-filter','workshop-count','ticket-link','shuffle-idea','ticket-workshop','ticket-title','ticket-text','workshop-detail','hub-home','detail-start','detail-access','detail-name','detail-tag','detail-title','detail-intro','detail-tools','detail-image','detail-steps','detail-ideas','detail-tip-text','fejl','venter','offline','ingen','catalog-status','catalog-error','workshops-title']) this.add(new Element('div', id));
    this.elements.get('shuffle-idea').tagName = 'BUTTON'; this.elements.get('detail-start').tagName = 'BUTTON';
  }
  add(element) { element.ownerDocument = this; this.elements.set(element.id, element); return element; }
  createElement(tag) { const element = new Element(tag); element.ownerDocument = this; return element; }
  getElementById(id) { return this.elements.get(id); }
  querySelectorAll(selector) { if (selector === '[data-filter]') return this.filters; if (selector === '[data-discover]') return this.discover; if (selector === '[data-project-link]') return this.projectLinks; return []; }
  querySelector() { return null; }
}
const document = new Document();
document.add(new Element('button', 'detail-model3d'));
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
  fetch(...args) { if (args[0] === 'http://127.0.0.1:8790/apps') return response(manifests); return fetchImpl(...args); },
  window: { CreativeWorkshops: workshops, addEventListener() {}, scrollTo() {}, open() { return null; } },
  SERVER:'192.0.2.1', PROTOKOL:'https', HUB:'https://192.0.2.1:8773', COMPUTER:'test', UI_TOKEN:'ui-token',
  LOKALE_APP_URLER:{ microbit:'http://127.0.0.1:8797/', scratch:'http://127.0.0.1:8792/', datatraener:'http://127.0.0.1:8793/', kredsloeb:'http://127.0.0.1:8794/', laser:'http://127.0.0.1:8795/', folie:'http://127.0.0.1:8796/', projekter:'http://127.0.0.1:8791/' }
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
  .replaceAll('{{ kiosk_internet_start | to_json }}', JSON.stringify('https://www.google.com/imghp?hl=da&safe=active')) + '\nwindow.__start = {opdater, aabn};';
const creative = fs.readFileSync(path.join(root, 'server','elevhub','web','assets','creative-hub.js'), 'utf8').replace(/opdater\(\);\s*$/, '');
vm.runInNewContext(inline + '\n' + creative, context);
function cards() { return document.getElementById('apps').children; }
function response(apps, status = 200) { return Promise.resolve({ ok: status >= 200 && status < 300, status, async json() { return { apps }; } }); }
const manifests = ['3dprint','laser','folie','microbit','scratch'].map((id, i) => ({schema:1,id,navn:id,version:'test',start:'http://127.0.0.1:'+({'3dprint':8798,laser:8795,folie:8796,microbit:8797,scratch:8792}[id])+'/'+(id==='3dprint'?'model3d.html':''),offline:true,kraever_internet:id==='microbit',aflevering:null,licens:'test'}));
const app = (id, aaben = true) => ({ id, aaben, klar:true, type:'vaerksted', navn:id });

(async () => {
  await context.window.__start.opdater();
  assert.equal(cards().length, 6, 'timeout exposes the installed offline catalog');
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
  fetchImpl = (url) => {
    assert.ok(url.startsWith('http://127.0.0.1:8790/finish'), 'offline launch only finishes the local session');
    return Promise.resolve({ok:true, async json() { return {id:'a'.repeat(32),state:'completed'}; }});
  };
  await context.window.__start.aabn({preventDefault() {}}, app('3dprint'), true);
  assert.equal(location.href, 'http://127.0.0.1:8798/model3d.html', '3D opens locally without school contact');
  fetchImpl = () => response([app('laser', false), app('scratch', true)]);
  await context.window.__start.opdater();
  assert.equal(context.window.CreativeHub.isOffline(), false, 'successful reconnect clears offline mode');
  assert.equal(cards().length, 1, 'teacher closed policy remains enforced after reconnect');
  assert.equal(cards()[0].dataset.app, 'scratch');
  const microbitWeb = {...app('microbit'), type:'web', start_url:'https://makecode.microbit.org/'};
  fetchImpl = url => url.startsWith('http://127.0.0.1:8790/finish')
    ? Promise.resolve({ok:true,json:async()=>({id:'a'.repeat(32),state:'completed'})})
    : response([microbitWeb, {...app('word'),type:'web',start_url:'https://word.example.test/'}]);
  await context.window.__start.opdater();
  assert.deepEqual(cards().map(card=>card.dataset.app), ['microbit','word']);
  await context.window.__start.aabn({preventDefault(){}}, microbitWeb);
  assert.equal(location.href, 'http://127.0.0.1:8797/', 'web-typed local wrapper uses its manifest start');
  const microbitIndex = manifests.findIndex(value=>value.id==='microbit');
  const [microbitManifest] = manifests.splice(microbitIndex, 1);
  await context.window.__start.opdater();
  assert.deepEqual(cards().map(card=>card.dataset.app), ['word'], 'local web wrappers require a manifest; external web apps remain');
  await context.window.__start.aabn({preventDefault(){}}, microbitWeb);
  assert.equal(location.href, 'http://127.0.0.1:8797/', 'stale micro:bit action must not navigate to external MakeCode');
  assert.match(document.getElementById('fejl').textContent, /ikke installeret/);
  manifests.push(microbitManifest);
  const scratchManifest = manifests.find(value=>value.id==='scratch');
  scratchManifest.offline = false;
  fetchImpl = () => Promise.reject({name:'TypeError',message:'offline'});
  await context.window.__start.opdater();
  let scratchCard = cards().find(card=>card.dataset.app==='scratch');
  assert.equal(scratchCard.children[0].children[1].textContent, 'Kræver serverforbindelse');
  assert.equal(scratchCard.children[1].children.at(-1).children[0].disabled, true);
  const beforeBlockedLaunch = location.href;
  await context.window.__start.aabn({preventDefault(){}}, app('scratch'));
  assert.equal(location.href, beforeBlockedLaunch, 'offline:false cannot launch during outage');
  assert.match(document.getElementById('fejl').textContent, /kræver forbindelse til skoleserveren/);
  fetchImpl = url => url.startsWith('http://127.0.0.1:8790/finish')
    ? Promise.resolve({ok:true,json:async()=>({id:'a'.repeat(32),state:'completed'})}) : response([app('scratch')]);
  await context.window.__start.opdater();
  scratchCard = cards().find(card=>card.dataset.app==='scratch');
  assert.equal(scratchCard.children[1].children.at(-1).children[0].disabled, false, 'online teacher-open app remains enabled');
  await context.window.__start.aabn({preventDefault(){}}, app('scratch'));
  assert.equal(location.href, 'http://127.0.0.1:8792/', 'offline:false may launch online');
  scratchManifest.offline = true;
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
  assert.equal(cards().length, 6);
  void pending;
  console.log('kiosk offline reconnect dynamic regression: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
