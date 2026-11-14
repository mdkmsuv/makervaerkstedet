const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() {
    this.children = []; this.listeners = {}; this.textContent = ''; this.className = '';
    this.hidden = false; this.disabled = false; this.value = '';
    this.classList = {add() {}};
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  focus() { this.focused = true; }
  click() { if (!this.disabled) return this.listeners.click?.({preventDefault() {}}); }
  submit() { return this.listeners.submit?.({preventDefault() {}}); }
}
const source = fs.readFileSync(path.join(__dirname, '..', 'server', 'elevhub', 'web', 'system-overview.js'), 'utf8');
function reply(data, status = 200) { return {ok: status >= 200 && status < 300, status, json: async () => data}; }
function fixture(available = true) {
  const elements = new Map();
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const calls = [];
  let post = () => Promise.resolve(reply({accepted: true}, 202));
  const window = {};
  const context = {document: {getElementById: element, createElement: () => new Element()},
    window, Date, Intl, AbortController, setTimeout, clearTimeout,
    fetch(url, options = {}) {
      calls.push({url, options});
      if (options.method === 'POST') return post();
      if (url === '/api/laerer/server-restart') return Promise.resolve(reply({available}));
      return Promise.resolve(reply({server: {}, computers: [], printers: {status: 'unknown'}}));
    }};
  vm.runInNewContext(source, context);
  return {element, window, calls, setPost(callback) { post = callback; }};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const f = fixture();
  try {
    await f.window.SystemOverview.start();
    assert.equal(f.element('system-restart').disabled, false);
    f.element('system-restart').click();
    assert.equal(f.element('system-restart-confirmation').hidden, false);
    f.element('system-restart-confirm-text').value = 'wrong';
    await f.element('system-restart-confirmation').submit();
    assert.equal(f.calls.filter(call => call.options.method === 'POST').length, 0);
    f.element('system-restart-cancel').click();
    assert.equal(f.element('system-restart-confirmation').hidden, true);
    f.element('system-restart').click();
    let resolvePost;
    f.setPost(() => new Promise(resolve => { resolvePost = resolve; }));
    f.element('system-restart-confirm-text').value = 'GENSTART HELE SERVEREN';
    const pending = f.element('system-restart-confirmation').submit();
    await f.element('system-restart-confirmation').submit();
    assert.equal(f.calls.filter(call => call.options.method === 'POST').length, 1, 'in-flight clicks must not repeat a reboot');
    const sent = f.calls.find(call => call.options.method === 'POST');
    assert.equal(sent.url, '/api/laerer/server-restart');
    assert.deepEqual(JSON.parse(sent.options.body), {confirmation: 'GENSTART HELE SERVEREN'});
    assert.equal(sent.options.headers['Content-Type'], 'application/json');
    resolvePost(reply({accepted: true}, 202));
    await pending;
    assert.match(f.element('system-restart-status').textContent, /modtaget/i);
    assert.equal(f.element('system-restart').disabled, true, 'accepted request stays locked');
    await f.window.SystemOverview.refresh();
    assert.equal(f.element('system-restart').disabled, true, 'polling must not unlock an accepted reboot');
    f.window.SystemOverview.stop();
    await f.window.SystemOverview.start();
    assert.equal(f.element('system-restart').disabled, false, 'new login resets the old request state');

    f.setPost(() => Promise.reject(new Error('connection lost')));
    f.element('system-restart').click();
    f.element('system-restart-confirm-text').value = 'GENSTART HELE SERVEREN';
    await f.element('system-restart-confirmation').submit();
    assert.match(f.element('system-restart-status').textContent, /ukendt/i);
    assert.equal(f.element('system-restart').disabled, true);
    const count = f.calls.filter(call => call.options.method === 'POST').length;
    await settle();
    assert.equal(f.calls.filter(call => call.options.method === 'POST').length, count, 'connection loss never retries the reboot');

    f.window.SystemOverview.stop();
    await f.window.SystemOverview.start();
    let authFailed = false;
    f.window.SystemOverview.onAuthFailure(() => { authFailed = true; f.window.SystemOverview.stop(); });
    f.setPost(() => Promise.resolve(reply({error: 'login'}, 401)));
    f.element('system-restart').click();
    f.element('system-restart-confirm-text').value = 'GENSTART HELE SERVEREN';
    await f.element('system-restart-confirmation').submit();
    assert.equal(authFailed, true);
    assert.equal(f.element('system-restart').disabled, true);
    assert.equal(f.element('system-restart-confirmation').hidden, true);
    assert.equal(f.element('system-restart-confirm-text').value, '');
  } finally { f.window.SystemOverview.stop(); }

  const unsupported = fixture(false);
  try {
    await unsupported.window.SystemOverview.start();
    assert.equal(unsupported.element('system-restart').disabled, true);
    unsupported.element('system-restart').click();
    assert.equal(unsupported.calls.filter(call => call.options.method === 'POST').length, 0);
    assert.match(unsupported.element('system-restart-status').textContent, /ikke tilgængelig/i);
  } finally { unsupported.window.SystemOverview.stop(); }
  console.log('server restart UI fixture: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
