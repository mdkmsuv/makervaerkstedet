'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('D:/Elevhub-VM/datatraener/cctd-ml-machine-release-dr/node_modules/typescript');

function store(value) {
  return {set(next) { value = next; }, update(fn) { value = fn(value); }, subscribe(fn) { fn(value); return () => {}; }, get value() { return value; }};
}
const gestures = store([{name: 'A', ID: 1, recordings: [{ID: 1, data: {x: [1], y: [2], z: [3]}}], output: {}}]);
const chosenGesture = store({name: 'A'});
const model = store({classify() {}});
const trainingStatus = store(1);
const state = store({isRequestingDevice: 0, isFlashingDevice: false, isTesting: false, isRecording: false, isTraining: false, isInputConnected: false, isOutputConnected: false, isPredicting: true, offerReconnect: false, requestDeviceWasCancelled: false, reconnectState: 0, isInputReady: false, isInputAssigned: false, isOutputAssigned: false, isOutputReady: false, isLoading: false});
const outputting = store({text: 'output'});
const message = store({warning: true, text: 'message'});
const events = [];
const window = {dispatchEvent(event) { events.push(event.type); }};
let source = fs.readFileSync('linux-elevhub/server/datatraener/project-adapter.ts', 'utf8');
source = source.replace(/import[\s\S]*?from "\.\/script\/stores\/mlStore";\s*/, '');
source = source.replace(/import[\s\S]*?from "\.\/script\/stores\/uiStore";\s*/, '');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const context = {window, get: value => value.value, gestures, chosenGesture, model, trainingStatus, state, outputting, message, TrainingStatus: {Untrained: 0}, GestureData: undefined, Blob, JSON, Event: class Event {constructor(type) {this.type = type;}}};
vm.runInNewContext(compiled, context);
const adapter = context.window.ElevhubProjectAdapter;
const validBlob = () => new Blob([JSON.stringify({format: 'elevhub-datatraener-project', version: 1, type: 'datatraener', gestureData: []})]);

(async () => {
  for (const field of ['isRecording', 'isTraining', 'isFlashingDevice']) {
    state.set({...state.value, [field]: true});
    const before = JSON.stringify(gestures.value);
    await assert.rejects(() => adapter.prepareFinish(), /før projektet/);
    await assert.rejects(() => adapter.reset(), /før projektet/);
    await assert.rejects(() => adapter.import(validBlob()), /før projektet/);
    assert.equal(JSON.stringify(gestures.value), before, `${field} guard mutated gestures`);
    state.set({...state.value, [field]: false});
  }
  state.set({...state.value, isRecording: true});
  await assert.rejects(() => adapter.prepareSave(), /Stop optagelsen/);
  state.set({...state.value, isRecording: false});

  await adapter.reset();
  assert.equal(JSON.stringify(gestures.value), '[]');
  assert.equal(chosenGesture.value, null);
  assert.equal(model.value, undefined);
  assert.equal(state.value.isPredicting, false);
  assert.equal(state.value.isTesting, false);
  assert.equal(JSON.stringify(outputting.value), '{"text":""}');
  assert.equal(JSON.stringify(message.value), '{"warning":false,"text":""}');
  assert.ok(events.includes('elevhub-project-changed'));
  console.log('Datatræner adapter transition guards passed');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
