'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const listeners = {};
const sent = [];
const frame = {contentWindow: {postMessage: (message, targetOrigin) => sent.push({message, targetOrigin})}};
const window = {addEventListener: (type, handler) => {listeners[type] = handler;}, dispatchEvent: () => {}};
const context = {window, document: {getElementById: () => frame}, Event: class Event {constructor(type) {this.type = type;}}, Blob, JSON, Date, structuredClone, setTimeout: () => {}};
vm.runInNewContext(fs.readFileSync('linux-elevhub/server/microbit-controller/app.js', 'utf8'), context);
const adapter = window.ElevhubProjectAdapter;
assert.equal(adapter.type, 'microbit');
assert.equal(adapter.extension, '.json');
assert.equal(adapter.hasContent(), false);

const project = {header: {name: 'Test'}, text: {'pxt.json': '{}', 'main.ts': 'basic.showNumber(1)', 'main.blocks': '<xml/>'}};
listeners.message({source: frame.contentWindow, origin: 'https://makecode.microbit.org', data: {type: 'pxthost', action: 'workspacesync', projects: []}});
listeners.message({source: frame.contentWindow, origin: 'https://makecode.microbit.org', data: {type: 'pxthost', action: 'workspacesave', project}});
function tick() { return new Promise(resolve => setImmediate(resolve)); }
async function answer(action, projectToSave) {
  await tick();
  const message = sent.map(entry => entry.message).reverse().find(item => item.type === 'pxteditor' && item.action === action && !item.__answered);
  assert.ok(message, `missing ${action} request`);
  message.__answered = true;
  if (projectToSave) listeners.message({source: frame.contentWindow, origin: 'https://makecode.microbit.org', data: {type: 'pxthost', action: 'workspacesave', project: projectToSave}});
  listeners.message({source: frame.contentWindow, origin: 'https://makecode.microbit.org', data: {type: 'pxteditor', id: message.id, success: true, resp: {}}});
}
(async () => {
  assert.equal(adapter.hasContent(), true);
  const renamed = {header: {name: 'Renamed'}, text: {'pxt.json': '{"name":"Renamed"}', 'main.ts': 'basic.showNumber(1)', 'main.blocks': '<xml/>'}};
  const setName = adapter.setName('Renamed');
  await answer('saveproject');
  await answer('importproject', renamed);
  await answer('saveproject', renamed);
  await setName;
  assert.equal(adapter.name(), 'Renamed');
  const blob = await adapter.export();
  const value = JSON.parse(await blob.text());
  assert.equal(value.format, 'elevhub-microbit-project');
  assert.deepEqual(value.project, renamed);
  listeners.message({source: frame.contentWindow, origin: 'https://evil.example', data: {type: 'pxthost', action: 'workspacesave', project: {header: {name: 'bad'}, text: {}}}});
  assert.equal(adapter.name(), 'Renamed');
  const flush = adapter.prepareSave();
  await answer('saveproject', renamed);
  await flush;
  const imported = {header: {name: 'Imported'}, text: {'pxt.json': '{"name":"Imported"}', 'main.ts': 'basic.showIcon(IconNames.Heart)'}};
  const importing = adapter.import(new Blob([JSON.stringify({format: 'elevhub-microbit-project', version: 1, type: 'microbit', project: imported})]));
  await answer('importproject', imported);
  await answer('saveproject', imported);
  await importing;
  const resetting = adapter.reset();
  await answer('newproject', {header: {name: 'micro:bit-projekt'}, text: {}});
  await answer('saveproject', {header: {name: 'micro:bit-projekt'}, text: {}});
  await resetting;
  console.log('MakeCode controller protocol contract passed');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
