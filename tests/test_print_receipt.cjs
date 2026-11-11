'use strict';
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('web/app.js', 'utf8');
const end = source.indexOf('async function poll(){');
assert(end > 0, 'app.js render functions not found');
const harness = String.raw`
const nodes = contextNodes; globalThis.__nodes = nodes;
const node = id => { const value = {id, textContent:'', innerHTML:'', hidden:false}; nodes.set(id, value); return value; };
['queue-count','queue-list','mini-queue-summary','mini-queue-list','print-confirmation-filename','print-confirmation-status','print-confirmation-demo','receipt'].forEach(node);
state = {paused:false, demo:false, ready:1, printers:[{id:'p1',name:'P1S',status:'printing'}], colours:[], jobs:[]};
recentJob = 'job1';
recentReceipt = {id:'job1', status:'queued', filename:'mit-eget-print.stl'};
renderQueue();
renderReceipt();
assert.equal(nodes.get('print-confirmation-filename').textContent, 'mit-eget-print.stl');
const transitions = [
  ['queued', 'Din model er i kø.'],
  ['printing', 'Din model bliver printet'],
  ['done', 'Dit print er færdigt']
];
for (const [status, expected] of transitions) {
  state.jobs = [{id:'job1', status, printer_id:'p1'}, {id:'other', status:'queued'}];
  renderQueue(); renderReceipt();
  assert(nodes.get('print-confirmation-status').textContent.includes(expected), status + ' must use live status');
  assert(nodes.get('mini-queue-list').innerHTML.includes(expected === 'Din model er i kø.' ? 'I kø' : status === 'printing' ? 'Printer' : 'Færdig!'));
  assert(nodes.get('mini-queue-list').innerHTML.includes('mit-eget-print.stl'));
  assert(nodes.get('mini-queue-list').innerHTML.includes('Printopgave'));
  assert(!nodes.get('mini-queue-list').innerHTML.includes('other-secret.stl'));
}
state.jobs = [{id:'job1', status:'cancelled', isolated:1, printer_id:'p1'}];
renderQueue(); renderReceipt();
assert(nodes.get('print-confirmation-status').textContent.includes('fysiske status er uafklaret'));
assert(!nodes.get('print-confirmation-status').textContent.includes('Printet er afbrudt'));
assert(nodes.get('receipt').innerHTML.includes('Afventer læreren'));
assert(nodes.get('mini-queue-list').innerHTML.includes('Afventer fysisk kontrol'));
assert(nodes.get('mini-queue-list').innerHTML.includes('mit-eget-print.stl'));
state.jobs[0].physical_resolved=1;
renderQueue(); renderReceipt();
assert(nodes.get('print-confirmation-status').textContent.includes('Printet er afbrudt'));
assert(!nodes.get('receipt').innerHTML.includes('Afventer læreren'));
assert(!nodes.get('mini-queue-list').innerHTML.includes('Afventer fysisk kontrol'));
state.jobs = [];
renderReceipt();
assert.equal(nodes.get('print-confirmation-filename').textContent, 'mit-eget-print.stl', 'expired live job may use own receipt');
assert(nodes.get('print-confirmation-status').textContent.includes('Din model er i kø.'));
console.log('print receipt live merge fixture: OK');
`;
const contextNodes = new Map();
const context = {
  console,
  assert,
  location: {pathname: '/'},
  localStorage: {getItem: () => '[]', setItem() {}, removeItem() {}},
  sessionStorage: {removeItem() {}},
  document: {getElementById: id => contextNodes.get(id), querySelectorAll: () => []},
  crypto: {randomUUID: () => 'a'.repeat(32), getRandomValues: values => values},
  CSS: {escape: value => value},
  window: {}, contextNodes,
};
vm.runInNewContext(source.slice(0, end) + harness, context, {filename: 'web/app.js'});
