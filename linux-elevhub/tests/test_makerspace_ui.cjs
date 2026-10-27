/* Small DOM fixture for the makerspace editor; no browser or network needed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sourcePath = path.join(__dirname, '../server/elevhub/web/makerspace.js');

class Element {
  constructor(tag = 'div', id = '') { this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.parentNode = null; this.dataset = {}; this.listeners = {}; this.value = ''; this.checked = false; this.hidden = false; this.textContent = ''; this.options = []; }
  append(...items) { for (const item of items) { item.parentNode = this; this.children.push(item); if (item.tagName === 'OPTION') this.options.push(item); } }
  appendChild(item) { this.append(item); return item; }
  add(item) { this.append(item); }
  replaceChildren(...items) { this.children = []; this.options = []; this.append(...items); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatch(name, event = {}) { for (const fn of this.listeners[name] || []) fn({...event, target: this, preventDefault(){}}); }
  querySelector(selector) { const match = selector.match(/data-field="([^"]+)"/); if (match) return this.children.find(x => x.dataset?.field === match[1]); return null; }
  querySelectorAll() { return []; }
  closest(selector) { let node = this; while (node) { if (selector === 'form' && node.tagName === 'FORM') return node; node = node.parentNode; } return null; }
  focus() {}
}
class Document extends Element {
  constructor() { super('document'); this.ids = new Map(); this.globalListeners = {}; this.hidden = false; this.activeElement = null; }
  make(id, tag = 'div') { const el = new Element(tag, id); this.ids.set(id, el); return el; }
  getElementById(id) { return this.ids.get(id) || this.make(id); }
  createElement(tag) { return new Element(tag); }
  createTextNode(text) { const node = new Element('#text'); node.textContent = String(text); return node; }
  addEventListener(name, fn) { (this.globalListeners[name] ||= []).push(fn); }
  dispatch(name, event = {}) { for (const fn of this.globalListeners[name] || []) fn({...event, target: this, preventDefault(){}}); }
  querySelectorAll(selector) { if (selector === 'button') return [...this.ids.values()].filter(x => x.tagName === 'BUTTON'); if (selector === '#machine-form,#workflow-form') return ['machine-form','workflow-form'].map(id => this.ids.get(id)); return []; }
}

const timers = new Map(); let timerId = 0;
function fakeSetTimeout(fn, delay) { const timer = {id: ++timerId, fn, delay}; timers.set(timer.id, timer); timer.unref = () => timer; return timer; }
function fakeClearTimeout(timer) { if (timer) timers.delete(timer.id); }
async function advanceTimers(delay) { for (const timer of [...timers.values()]) if (timer.delay <= delay) { timers.delete(timer.id); await timer.fn(); } }

const document = new Document();
for (const id of ['error','status','machines','workflows','jobs','observed','machine-form','machine-reset','machine-cancel','machine-id','machine-name','machine-location','machine-enabled','machine-workflow','workflow-form','workflow-reset','workflow-cancel','workflow-id','workflow-name','workflow-kind','workflow-steps','add-step','job-form','job-title','job-machine']) document.make(id, id.endsWith('form') ? 'form' : 'div');
document.getElementById('machine-form').append(document.getElementById('machine-name'), document.getElementById('machine-location'), document.getElementById('machine-enabled'), document.getElementById('machine-workflow'));
document.getElementById('workflow-form').append(document.getElementById('workflow-name'), document.getElementById('workflow-kind'), document.getElementById('workflow-steps'));
document.getElementById('workflow-kind').value = 'manual';
const template = {name:'Manuel station', kind:'manual', steps:[
  {id:'submit',label:'Indsend opgave',role:'operator',action:'submit'},
  {id:'prepare',label:'Forbered arbejdsplads',role:'teacher',action:'prepare'},
  {id:'approve',label:'Godkend opgaven',role:'teacher',action:'approve'},
  {id:'produce',label:'Gennemfør fysisk arbejde',role:'teacher',action:'produce'},
  {id:'collect',label:'Aflever eller afhent',role:'operator',action:'collect'},
  {id:'clear',label:'Ryd arbejdsplads',role:'teacher',action:'clear'}
]};
const state = {machines:[{id:'m1',version:2,name:'Station',location:'Rum 1',enabled:true,workflow_id:'w1'}], workflows:[{id:'w1',version:4,name:'Gammel',kind:'manual',draft:template,active_revision:1,active_steps:template.steps,revisions:[1],has_unpublished_changes:true},{id:'w-print',version:2,name:'Printforløb',kind:'3d_print',draft:template,active_revision:2,active_steps:template.steps,revisions:[1,2],has_unpublished_changes:false}], jobs:[{id:'j1',version:1,title:'Låst opgave',machine_id:'m1',workflow_id:'w1',workflow_revision:1,definition:template,step_index:1,status:'active'}], printer_administration:{status:'ok',printers:[{id:'p1',workflow:{id:'w-print',revision:1}}],workflow_jobs:[{id:'pj1',workflow_id:'w-print',workflow_revision:1,status:'queued'}]}, laser_administration:{status:'unknown',stations:[],jobs:[]}, observed_machines:[{id:'m1',name:'Station',supported:true,status:'online',online:true,observed_at:Date.now()/1000-180}], templates:{manual:template, '3d_print':template, laser:template}};
let calls = [], stateLoads = 0, failState = false, holdState = false, pendingStateResolve = null, oldSnapshot = null, failNext = false, lostNext = false, invalidNext = false, uuidNumber=0;
const windowEvents = {};
const context = {
  document, window:{addEventListener:(name, fn)=>{windowEvents[name]=fn;}}, location:{href:''}, AbortController:class AbortController { constructor(){this.signal={};} abort(){} }, Option:class Option extends Element { constructor(label, value){super('option');this.textContent=label;this.value=value;} },
  crypto:{randomUUID:()=> (++uuidNumber).toString(16).padStart(32,'0')}, setTimeout:fakeSetTimeout, clearTimeout:fakeClearTimeout, fetch: async (url, options) => {
    if (url.endsWith('/state')) { stateLoads++; if (failState) throw new TypeError('state unavailable'); if (holdState) return new Promise(resolve => { pendingStateResolve = () => resolve({ok:true,json:async()=>oldSnapshot}); }); return {ok:true,json:async()=>state}; }
    calls.push(JSON.parse(options.body));
    if(lostNext){lostNext=false;throw new TypeError('response lost');}
    if(invalidNext){invalidNext=false;return {ok:true,json:async()=>{throw new SyntaxError('truncated JSON')}};}
    if (failNext) { failNext=false; return {ok:false,status:409,json:async()=>({error:'conflict'})}; }
    return {ok:true,json:async()=>({ok:true,action:JSON.parse(options.body).action,result:{id:'created'}})};
  }, console
};
vm.runInNewContext(fs.readFileSync(sourcePath,'utf8'), context, {filename:'makerspace.js'});
setImmediate(async () => {
  await new Promise(resolve => setImmediate(resolve));
  if (!document.getElementById('workflow-steps').children.length) throw new Error('fixture load failed: '+document.getElementById('status').textContent+' / '+document.getElementById('error').textContent);
  const steps = document.getElementById('workflow-steps').children;
  assert.equal(steps.length, 6, 'API template builds all workflow rows');
  assert.equal(steps[0].dataset.id, 'submit');
  assert.equal(steps[0].querySelector('[data-field="role"]').value, 'operator');
  assert.equal(steps[0].querySelector('[data-field="action"]').value, 'submit');
  assert(document.getElementById('workflows').children[0].children.some(child => /Ikkeaktiverede ændringer/.test(child.textContent)));
  assert(document.getElementById('workflows').children[1].children.some(child => /printerbinding/.test(child.textContent)));
  assert.match(document.getElementById('observed').children[0].children[2].textContent, /Gammel/);

  /* Polling must yield to an edited form and to hidden/departed pages. */
  const beforeDirtyPoll = stateLoads;
  document.getElementById('workflow-name').value = 'Redigeres';
  document.getElementById('workflow-form').dispatch('input');
  await advanceTimers(15000);
  assert.equal(stateLoads, beforeDirtyPoll, 'dirty editor suppresses polling');
  context.resetWorkflow();
  document.hidden = true;
  document.dispatch('visibilitychange');
  const beforeHiddenPoll = stateLoads;
  await advanceTimers(15000);
  assert.equal(stateLoads, beforeHiddenPoll, 'hidden page does not poll');
  document.hidden = false;
  windowEvents.pageshow();
  windowEvents.pagehide();
  const beforeDepartedPoll = stateLoads;
  await advanceTimers(30000);
  assert.equal(stateLoads, beforeDepartedPoll, 'pagehide cancels polling and age refresh');
  windowEvents.pageshow();
  /* A slower poll response cannot replace a newer explicit refresh. */
  oldSnapshot = JSON.parse(JSON.stringify(state));
  holdState = true;
  const inflight = advanceTimers(15000);
  await new Promise(resolve => setImmediate(resolve));
  state.machines[0].name = 'Nyeste station';
  holdState = false;
  await context.load();
  assert.equal(document.getElementById('machines').children[0].children[0].textContent, 'Nyeste station');
  pendingStateResolve();
  await inflight;
  assert.equal(document.getElementById('machines').children[0].children[0].textContent, 'Nyeste station', 'stale poll response ignored');
  pendingStateResolve = null;
  /* A local age refresh still marks a formerly fresh observation stale when
     the next server request fails. */
  document.hidden = false;
  state.observed_machines[0].observed_at = Date.now()/1000 - 1;
  context.renderObserved();
  const freshAge = document.getElementById('observed').children[0].children[2].textContent;
  assert.match(freshAge, /lige nu/);
  state.observed_machines[0].observed_at = Date.now()/1000 - 180;
  failState = true;
  await advanceTimers(30000);
  assert.match(document.getElementById('observed').children[0].children[2].textContent, /Gammel/);
  assert.match(document.getElementById('status').textContent,/sidst kendt/,'failed polling must not leave healthy update status');
  failState = false;
  await context.load();
  assert.match(document.getElementById('status').textContent,/opdateret/,'successful reconnect restores status');

  const form = document.getElementById('workflow-form');
  document.getElementById('workflow-name').value = 'Ny manuel';
  form.dispatch('submit');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.at(-1).params.definition.steps[3].action, 'produce');
  assert.equal(calls.at(-1).params.definition.steps[4].role, 'operator');

  /* A conflict leaves the user's form values intact. */
  document.getElementById('workflow-id').value = '';
  document.getElementById('workflow-name').value = 'Behold mig';
  failNext = true;
  form.dispatch('submit');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(document.getElementById('workflow-name').value, 'Behold mig');
  assert.match(document.getElementById('error').textContent, /conflict/);
  // A response lost after applying a mutation must be retried under the same ID.
  lostNext=true;
  const params={name:'Retry',location:'',enabled:false,workflow_id:null};
  assert.equal(await context.command('machine.create',params,0),false);
  const firstId=calls.at(-1).request_id, count=calls.length;
  assert.equal(await context.command('machine.create',{...params,name:'Other'},0),false);
  assert.equal(calls.length,count,'ambiguous result blocks a different mutation');
  assert.equal(await context.command('machine.create',params,0),true);
  assert.equal(calls.at(-1).request_id,firstId,'retry preserves the original request ID');
  // The retry button must preserve other drafts and edits made after submission.
  document.getElementById('machine-name').value='Retry';
  document.getElementById('job-title').value='Other draft';
  invalidNext=true;
  assert.equal(await context.command('machine.create',params,0),false,'unreadable 200 is an unknown outcome');
  const malformedId=calls.at(-1).request_id;
  document.getElementById('retry-command').dispatch('click');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.at(-1).request_id,malformedId);
  assert.equal(document.getElementById('machine-name').value,'','only matching submitted form resets');
  assert.equal(document.getElementById('workflow-name').value,'Behold mig','unrelated workflow draft remains');
  assert.equal(document.getElementById('job-title').value,'Other draft','unrelated job draft remains');
  lostNext=true;
  assert.equal(await context.command('machine.create',params,0),false);
  document.getElementById('machine-name').value='New unsaved changes';
  document.getElementById('retry-command').dispatch('click');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(document.getElementById('machine-name').value,'New unsaved changes','edits since uncertain submission remain');
  // A published edit may alter the draft while a job still uses its pinned definition.
  state.workflows[0].draft={...template,steps:template.steps.map(s=>({...s,id:'new-'+s.id}))};
  await context.load();
  const click=document.globalListeners.click[0];
  click({target:{closest:()=>({dataset:{action:'job-advance',value:'j1'}})}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.at(-1).params.step_id,'prepare','job advances its pinned step, not current draft');
  console.log('makerspace UI fixture: ok');
});
