'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const listeners = {};
const timers = [];
const sent = [];
const frame = {contentWindow: {postMessage(message) { sent.push(message); }}};
const window = {
  addEventListener(type, handler) { listeners[type] = handler; },
  dispatchEvent() {}
};
const context = {
  window,
  document: {getElementById: () => frame},
  Event: class Event { constructor(type) { this.type = type; } },
  Blob, JSON, Date, structuredClone,
  setTimeout(callback) { timers.push(callback); return timers.length - 1; },
  clearTimeout() {}
};
vm.runInNewContext(fs.readFileSync('linux-elevhub/server/microbit-controller/app.js', 'utf8'), context);
const adapter = window.ElevhubProjectAdapter;

(async () => {
  for(const action of ['workspaceloaded','workspacereset']){
    const before=sent.length;
    listeners.message({source:frame.contentWindow,origin:'https://makecode.microbit.org',
      data:{type:'pxthost',action,id:'workspace-'+action,response:true}});
    assert.equal(sent.length,before+1,'known workspace requests are acknowledged');
    assert.deepEqual(JSON.parse(JSON.stringify(sent.at(-1))),
      {type:'pxthost',id:'workspace-'+action,success:true});
  }
  const beforeUntrusted=sent.length;
  for(const [source,origin] of [[{},'https://makecode.microbit.org'],[frame.contentWindow,'https://untrusted.invalid']]){
    listeners.message({source,origin,data:{type:'pxthost',action:'workspaceloaded',id:'untrusted'}});
  }
  listeners.message({source:frame.contentWindow,origin:'https://makecode.microbit.org',
    data:{type:'pxthost',action:'shareproject',id:'unrelated'}});
  listeners.message({source:frame.contentWindow,origin:'https://makecode.microbit.org',
    data:{type:'pxthost',action:'workspaceloaded'}});
  assert.equal(sent.length,beforeUntrusted,'no acknowledgement for wrong source/origin or unknown action');
  const timedOut = adapter.prepareSave();
  assert.equal(timers.length, 1, 'preparation installs a bounded readiness timeout');
  timers.shift()();
  await assert.rejects(timedOut, error => {
    assert.equal(error.message, 'micro:bit kunne ikke åbnes. Det kræver internet eller lidt længere tid. Prøv igen, eller spørg din lærer.');
    assert.match(error.message, /eller lidt længere tid/);
    return true;
  });

  const retried = adapter.prepareSave();
  listeners.message({source: frame.contentWindow, origin: 'https://makecode.microbit.org',
    data: {type: 'pxthost', action: 'workspacesync', projects: []}});
  await new Promise(resolve => setImmediate(resolve));
  const save = sent.find(message => message.type === 'pxteditor' && message.action === 'saveproject');
  assert.ok(save, 'retry sends the save request after MakeCode becomes ready');
  listeners.message({source: frame.contentWindow, origin: 'https://makecode.microbit.org',
    data: {type: 'pxteditor', id: save.id, success: true, resp: {}}});
  await retried;
  console.log('micro:bit exact workspace acknowledgements, rejected unrelated senders/actions, timeout and late-readiness retry regression: ok');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
