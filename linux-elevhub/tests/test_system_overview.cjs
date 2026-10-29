const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag, id = '') {
    this.tagName = tag.toUpperCase(); this.id = id; this.className = '';
    this.children = []; this.textContent = ''; this.listeners = {};
    this.classList = { add: (...names) => { this.className = `${this.className} ${names.join(' ')}`.trim(); } };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  click() { this.listeners.click?.({}); }
}
class Document {
  constructor() {
    this.elements = new Map();
    for (const id of ['system-opdater', 'system-status', 'system-server', 'system-tal',
      'system-computer-count', 'system-printer-count', 'system-computere', 'system-printere']) {
      this.elements.set(id, new Element('div', id));
    }
    for (const id of ['system-restart', 'system-restart-submit', 'system-restart-confirmation',
      'system-restart-confirm-text', 'system-restart-status', 'system-restart-cancel']) {
      this.elements.set(id, new Element('div', id));
    }
    this.elements.get('system-opdater').tagName = 'BUTTON';
  }
  getElementById(id) { return this.elements.get(id); }
  createElement(tag) { return new Element(tag); }
}
function textOf(element) {
  return [element.textContent, ...element.children.map(textOf)].join(' ');
}
function response(payload, ok = true, status = ok ? 200 : 401) {
  return { ok, status, json: async () => payload };
}

const document = new Document();
let calls = 0;
let nextFetch = () => Promise.resolve(response({
  server: {release_id: 'server-20261011', expected_client_release: 'client-20261011'},
  tls: {status: 'expiring_30_days', certificate: {status: 'expiring_30_days', not_after: '2026-10-20T12:00:00Z', days_remaining: 8},
    ca: {status: 'valid', not_after: '2027-10-20T12:00:00Z', days_remaining: 373}},
  computers_status: 'ok',
  computers: [
    {navn: '<elev>', ip: '203.0.113.11', online: true, sidst_set_sekunder: 12,
      visning: 'start', bekraeftet: true, software_version: 'client-20261011',
      version_status: 'current', registered: true, version: 999},
    {navn: 'vaerksted-02', ip: '203.0.113.12', online: false, sidst_set_sekunder: 500,
      visning: null, bekraeftet: false, software_version: 'client-20261010',
      version_status: 'current', registered: true}
  ],
  printers: {status: 'ok', checked_at: 1791711000, items: [
    {id: 'p1', name: 'Printer A', model: 'A1 mini', online: true, enabled: true,
      ready: true, bed_clear: true, error: false}
  ]}
}));
const context = {
  console, document, AbortController, Date, Intl, setTimeout, clearTimeout,
  fetch: (...args) => {
    if (args[0] === '/api/laerer/server-restart') return Promise.resolve(response({available:false}));
    calls += 1; return nextFetch(...args);
  },
  window: {}
};
const teacherSiden = () => 'teacher-panel-helper';
context.siden = teacherSiden;
const source = fs.readFileSync(path.join(__dirname, '..', 'server', 'elevhub', 'web', 'system-overview.js'), 'utf8');
vm.runInNewContext(source, context);
assert.equal(context.siden, teacherSiden, 'system overview helpers must not overwrite the teacher panel global');

(async () => {
  await context.window.SystemOverview.start(1);
  const computerText = textOf(document.getElementById('system-computere'));
  const printerText = textOf(document.getElementById('system-printere'));
  assert.match(computerText, /<elev>/, 'computer names use text nodes, not HTML interpolation');
  assert.match(computerText, /client-20261011/);
  assert.match(computerText, /Senest kendt: aktuel/);
  assert.match(computerText, /Offline/);
  assert.doesNotMatch(computerText, /999/, 'policy number must not be presented as software version');
  assert.doesNotMatch(computerText, /Versionsstatus ukendt/);
  assert.match(printerText, /Printer A/);
  assert.match(printerText, /Ingen fejlmelding/);
  assert.doesNotMatch(printerText, /false/);
  assert.match(textOf(document.getElementById('system-server')), /server-20261011/);
  assert.match(textOf(document.getElementById('system-server')), /Udløber inden 30 dage/);
  assert.match(textOf(document.getElementById('system-server')), /menu 12/);
  assert.doesNotMatch(textOf(document.getElementById('system-server')), /\.crt|BEGIN CERTIFICATE|\/etc\//);
  assert.equal(document.getElementById('system-computer-count').textContent, '2 registreret');
  assert.equal(document.getElementById('system-printer-count').textContent, '1 modtaget');

  let pendingResolve;
  nextFetch = () => new Promise(resolve => { pendingResolve = resolve; });
  const first = context.window.SystemOverview.refresh();
  const second = context.window.SystemOverview.refresh();
  assert.equal(calls, 2, 'manual refresh must not overlap an in-flight request');
  pendingResolve(response({server: {}, computers: [], computers_status: 'unknown', printers: {status: 'unknown', items: []}}));
  await first; await second;
  assert.equal(document.getElementById('system-printer-count').textContent, 'status ukendt');
  assert.equal(document.getElementById('system-computer-count').textContent, 'status ukendt');
  assert.match(textOf(document.getElementById('system-printere')), /Printerstatus er ukendt/);
  assert.match(textOf(document.getElementById('system-computere')), /Elevcomputerstatus er ukendt/);

  nextFetch = () => Promise.resolve(response({server: {}, computers: [], printers: {status:'unknown',items:[]},
    tls:{certificate:{status:'valid'},ca:{status:'expired'}}}));
  await context.window.SystemOverview.refresh();
  assert.match(textOf(document.getElementById('system-server')), /menu 12 udskifter ikke CA/);
  assert.doesNotMatch(textOf(document.getElementById('system-server')), /via menu 12 for at forny/);

  nextFetch = () => Promise.reject(new Error('forbindelsen blev afbrudt'));
  await context.window.SystemOverview.refresh();
  assert.match(document.getElementById('system-status').textContent, /Ingen aktuelle data/);
  assert.match(textOf(document.getElementById('system-server')), /Sidst kendt/);
  assert.match(textOf(document.getElementById('system-printere')), /Ingen aktuelle printerdata/);
  assert.doesNotMatch(textOf(document.getElementById('system-printere')), /Klar/);

  let authFailed = false;
  context.window.SystemOverview.onAuthFailure(() => { authFailed = true; });
  nextFetch = () => Promise.resolve(response({error: 'login'}, false, 401));
  await context.window.SystemOverview.refresh();
  assert.equal(authFailed, true, '401 must hand control back to the login flow');
  context.window.SystemOverview.stop();
  console.log('system overview fixture: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
