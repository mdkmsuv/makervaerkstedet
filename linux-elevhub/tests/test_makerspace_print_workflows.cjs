const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Node {
  constructor(tag = 'div', id = '') { this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.listeners = {}; this.textContent = ''; this.value = ''; this.disabled = false; this.hidden = false; this.className = ''; }
  append(...nodes) { nodes.forEach(node => { node.parentNode = this; this.children.push(node); }); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  click() { if (!this.disabled) (this.listeners.click || []).forEach(fn => fn({preventDefault() {}})); }
  setAttribute(name, value) { this[name] = String(value); }
  querySelectorAll(tag) { const result = []; const walk = node => { for (const child of node.children) { if (child.tagName === tag.toUpperCase()) result.push(child); walk(child); } }; walk(this); return result; }
}

function state(authorized = true, jobStatus = 'workflow_wait', action = 'produce', queue = {id: 'print', paused: false, version: 1}) {
  return {workflows: [{id: 'wf-a', name: 'Print', kind: '3d_print', active_revision: 2, revisions: [1, 2]}], printer_administration: {
    queue,
    status: 'ok',
    printers: [{id: 'p1', name: 'A1', version: 7, workflow: {id: 'wf-a', revision: 2, name: 'Print'}}],
    workflow_jobs: [{id: 'job-1', printer_id: 'p1', filename: 'test.3mf', status: jobStatus, version: 4,
      workflow_id: 'wf-a', workflow_revision: 2, workflow_name: 'Print',
      step: {id: action, label: action === 'collect' ? 'Afhent print' : 'Ryd pladen', role: 'teacher', action}, production_authorized: authorized}]
  }};
}

function setup(responses) {
  const ids = ['print-workflow-status', 'print-workflow-error', 'print-workflow-bindings', 'print-workflow-jobs', 'print-queue-control', 'print-workflow-retry', 'print-workflow-refresh'];
  const nodes = new Map(ids.map(id => [id, new Node('div', id)]));
  const document = {getElementById: id => nodes.get(id) || null, createElement: tag => new Node(tag)};
  const events = {};
  const window = {location: {href: ''}, addEventListener(type, fn) { events[type] = fn; }, reload() { events.reloaded = true; }};
  const requests = [];
  const fetch = async (url, options = {}) => {
    requests.push({url, options});
    const response = responses.shift();
    if (!response) throw new TypeError('missing fixture response');
    if (response.wait) await response.wait;
    if (response.error) throw response.error;
    return {ok: response.status >= 200 && response.status < 300, status: response.status,
      async json() { if (response.invalid) throw new Error('invalid'); return response.body; }};
  };
  const context = {document, window, fetch, setTimeout, clearTimeout, AbortController, confirm:() => true,
    crypto: {randomUUID: (() => { let n = 0; return () => '00000000-0000-4000-8000-' + String(++n).padStart(12, '0'); })()}, console};
  const source = fs.readFileSync(path.join(__dirname, '..', 'server', 'elevhub', 'web', 'makerspace-print-workflows.js'), 'utf8');
  vm.runInNewContext(source, context);
  return {nodes, events, requests, async settle() { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); }};
}

(async () => {
  const responses = [
    {status: 200, body: state(false)},
    {status: 503, body: {error: 'midlertidig'}},
    {status: 200, body: {ok: true, action: 'printer.workflow.assign', result: {id: 'p1', version: 8}}},
    {status: 200, body: state(true)}
  ];
  const fixture = setup(responses);
  await fixture.settle();
  const card = fixture.nodes.get('print-workflow-bindings').children[0];
  const select = card.children.find(node => node.tagName === 'SELECT');
  const apply = card.children.find(node => node.tagName === 'BUTTON');
  const initialJobButton = fixture.nodes.get('print-workflow-jobs').children[0].children.find(node => node.tagName === 'BUTTON');
  assert.equal(initialJobButton.disabled, false, 'teacher UI may present the step; server remains authoritative');
  assert.equal(apply.disabled, false);
  select.value = 'wf-a:1';
  apply.click();
  await fixture.settle();
  const failed = fixture.requests[1].options.body;
  assert.match(failed, /"expected_version":7/);
  assert.match(failed, /"workflow_id":"wf-a"/);
  assert.match(failed, /"revision":1/);
  assert.equal(fixture.nodes.get('print-workflow-retry').hidden, false);
  fixture.nodes.get('print-workflow-retry').click();
  await fixture.settle();
  assert.equal(fixture.requests[2].options.body, failed, 'retry must replay exact JSON');
  fixture.events.pagehide();
  assert.equal(fixture.nodes.get('print-workflow-retry').hidden, true);

  const collect = setup([{status: 200, body: state(true, 'done', 'collect')}]);
  await collect.settle();
  const collectButton = collect.nodes.get('print-workflow-jobs').children[0].children.find(node => node.tagName === 'BUTTON');
  assert.equal(collectButton.textContent, 'Bekræft afhentning');
  assert.equal(collectButton.disabled, false);
  const clear = setup([{status: 200, body: state(true, 'failed', 'clear')}]);
  await clear.settle();
  const clearButton = clear.nodes.get('print-workflow-jobs').children[0].children.find(node => node.tagName === 'BUTTON');
  assert.equal(clearButton.textContent, 'Jeg har ryddet pladen');
  assert.equal(clearButton.disabled, false);

  for (const action of ['prepare', 'clear']) {
    const completed = setup([{status: 200, body: state(true, 'done', action)}]);
    await completed.settle();
    const button = completed.nodes.get('print-workflow-jobs').children[0].children.find(node => node.tagName === 'BUTTON');
    assert.equal(button.disabled, false, `completed print permits ${action}`);
  }
  const produce = setup([{status: 200, body: state(false)},
    {status: 200, body: {ok: true, action: 'printer.job.advance', result: {id: 'job-1', version: 5}}},
    {status: 200, body: state(true, 'printing', 'collect')}]);
  await produce.settle();
  produce.nodes.get('print-workflow-jobs').children[0].children.find(node => node.tagName === 'BUTTON').click();
  await produce.settle();
  const sent = JSON.parse(produce.requests[1].options.body);
  assert.equal(sent.action, 'printer.job.advance');
  assert.deepEqual(sent.params, {id: 'job-1', step_id: 'produce', confirmed: true});
  assert.equal(sent.expected_version, 4);
  assert.equal(produce.nodes.get('print-workflow-jobs').querySelectorAll('button').length, 0,
    'collection must wait for physical completion');

  let completeLoad;
  const late = setup([{status: 200, body: state(false), wait: new Promise(resolve => { completeLoad = resolve; })}]);
  late.events.pagehide();
  completeLoad();
  await late.settle();
  assert.equal(late.nodes.get('print-workflow-jobs').children.length, 0, 'late state cannot revive a departed page');
  assert.equal(late.nodes.get('print-workflow-refresh').disabled, true);

  const unauthorized = setup([{status: 401, body: {error: 'login required'}}]);
  await unauthorized.settle();
  assert.equal(unauthorized.requests.length, 1);
  assert.equal(unauthorized.nodes.get('print-workflow-refresh').disabled, true);

  const strict = setup([{status: 200, body: state(true)}, {status: 200, body: {ok: true, action: 'printer.workflow.assign', result: {id: 'p1', version: 7}}}]);
  await strict.settle();
  const strictCard = strict.nodes.get('print-workflow-bindings').children[0];
  const strictSelect = strictCard.children.find(node => node.tagName === 'SELECT');
  strictSelect.value = 'wf-a:1';
  strictCard.children.find(node => node.tagName === 'BUTTON').click();
  await strict.settle();
  assert.equal(strict.nodes.get('print-workflow-retry').hidden, false, 'wrong result version must remain retryable');

  const queue = setup([
    {status: 200, body: state(false, 'workflow_wait', 'produce', {id: 'print', paused: false, version: 1})},
    {status: 200, body: {ok: true, action: 'printer.queue.pause', result: {id: 'print', paused: true, version: 2}}},
    {status: 200, body: state(false, 'workflow_wait', 'produce', {id: 'print', paused: true, version: 2})}
  ]);
  await queue.settle();
  const queueControl = queue.nodes.get('print-queue-control');
  let queueButton = queueControl.children.find(node => node.tagName === 'BUTTON');
  assert.equal(queueButton.textContent, 'Sæt printkøen på pause');
  queueButton.click();
  await queue.settle();
  const queueEnvelope = JSON.parse(queue.requests[1].options.body);
  assert.equal(queueEnvelope.action, 'printer.queue.pause');
  assert.deepEqual(queueEnvelope.params, {id: 'print', paused: true});
  assert.equal(queueEnvelope.expected_version, 1);
  queueButton = queueControl.children.find(node => node.tagName === 'BUTTON');
  assert.equal(queueButton.textContent, 'Genoptag printkøen');

  const queueRetry = setup([
    {status: 200, body: state(false, 'workflow_wait', 'produce', {id: 'print', paused: false, version: 1})},
    {status: 503, body: {error: 'midlertidig'}},
    {status: 503, body: {error: 'midlertidig'}}
  ]);
  await queueRetry.settle();
  queueRetry.nodes.get('print-queue-control').children.find(node => node.tagName === 'BUTTON').click();
  await queueRetry.settle();
  const retryBody = queueRetry.requests[1].options.body;
  assert.equal(queueRetry.nodes.get('print-workflow-retry').hidden, false);
  assert.equal(queueRetry.nodes.get('print-queue-control').children.find(node => node.tagName === 'BUTTON').disabled, true);
  queueRetry.nodes.get('print-workflow-retry').click();
  await queueRetry.settle();
  assert.equal(queueRetry.requests[2].options.body, retryBody, 'queue retry must replay exact JSON');

  const queueConflict = setup([
    {status: 200, body: state(false, 'workflow_wait', 'produce', {id: 'print', paused: false, version: 1})},
    {status: 409, body: {error: 'version conflict'}},
    {status: 200, body: state(false, 'workflow_wait', 'produce', {id: 'print', paused: true, version: 2})}
  ]);
  await queueConflict.settle();
  queueConflict.nodes.get('print-queue-control').children.find(node => node.tagName === 'BUTTON').click();
  await queueConflict.settle();
  assert.equal(queueConflict.requests.length, 3);
  assert.equal(queueConflict.nodes.get('print-queue-control').children.find(node => node.tagName === 'BUTTON').textContent, 'Genoptag printkøen');
  assert.equal(queueConflict.nodes.get('print-workflow-retry').hidden, true);

  for (const malformed of [null, {id: 'other', paused: false, version: 1}, {id: 'print', paused: 'false', version: 1}, {id: 'print', paused: false, version: 0}]) {
    const missing = setup([{status: 200, body: state(false, 'workflow_wait', 'produce', malformed)}]);
    await missing.settle();
    assert.equal(missing.nodes.get('print-queue-control').children.some(node => node.tagName === 'BUTTON'), false, 'invalid queue metadata must render no button');
  }

  const wrong = setup([
    {status: 200, body: state(false, 'workflow_wait', 'produce', {id: 'print', paused: false, version: 1})},
    {status: 200, body: {ok: true, action: 'printer.queue.pause', result: {id: 'other', paused: true, version: 2}}}
  ]);
  await wrong.settle();
  wrong.nodes.get('print-queue-control').children.find(node => node.tagName === 'BUTTON').click();
  await wrong.settle();
  assert.equal(wrong.nodes.get('print-workflow-retry').hidden, false, 'wrong queue result remains pending');
  assert.equal(wrong.nodes.get('print-queue-control').children.find(node => node.tagName === 'BUTTON').disabled, true);
  wrong.events.pagehide();
  assert.equal(wrong.nodes.get('print-queue-control').children.find(node => node.tagName === 'BUTTON').disabled, true);
  console.log('makerspace print workflow fixture: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
