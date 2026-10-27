'use strict';
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '..', 'server', 'elevhub', 'web', 'makerspace-lasers.js'), 'utf8');
class Element {
  constructor(id = '') { this.id = id; this.children = []; this.listeners = {}; this.hidden = false; this.disabled = false; this.value = ''; this.checked = false; this.textContent = ''; this.type = ''; this.serverDisabled = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  click() { return this.listeners.click?.({preventDefault(){}}); }
  submit() { return this.listeners.submit?.({preventDefault(){}}); }
  reset() { this.value = ''; this.checked = false; for (const child of this.children) child.reset?.(); }
  setAttribute() {}
  querySelectorAll(selector) { const result = []; const walk = node => { for (const child of node.children || []) { if (selector.includes('button') && child.type === 'button') result.push(child); if (selector.includes('select') && child.tagName === 'SELECT') result.push(child); if (selector.includes('input') && child.tagName === 'INPUT') result.push(child); walk(child); } }; walk(this); return result; }
}
function setup(fetchImpl) {
  const elements = {};
  ['laser-status','laser-error','laser-retry','laser-refresh','laser-form','laser-id','laser-name','laser-location','laser-model','laser-enabled','laser-save','laser-cancel','laser-stations','laser-jobs'].forEach(id => { elements[id] = new Element(id); });
  elements['laser-id'].type = 'text'; elements['laser-id'].tagName = 'INPUT'; elements['laser-name'].tagName = 'INPUT'; elements['laser-location'].tagName = 'INPUT'; elements['laser-model'].tagName = 'SELECT'; elements['laser-enabled'].type = 'checkbox'; elements['laser-enabled'].tagName = 'INPUT'; elements['laser-save'].type = 'button'; elements['laser-save'].tagName = 'BUTTON';
  elements['laser-form'].append(elements['laser-id'], elements['laser-name'], elements['laser-location'], elements['laser-model'], elements['laser-enabled'], elements['laser-save']);
  const listeners = {};
  const context = {document:{getElementById:id => elements[id], createElement:tag => { const e = new Element(); e.tagName = tag.toUpperCase(); e.type = tag === 'button' ? 'button' : ''; return e; }}, fetch: fetchImpl, crypto:{randomUUID:() => '12345678-1234-4234-8234-123456789abc'}, AbortController, setTimeout, clearTimeout, console, confirm:() => true, window:{location:{href:''}, addEventListener:(n,fn) => {listeners[n] = fn;}, reload(){}}};
  context.globalThis = context;
  vm.runInNewContext(source, context, {filename:'makerspace-lasers.js'});
  return {elements, listeners};
}
const state = job => ({laser_administration:{status:'ok', stations:[{id:'flux-main',name:'Laser',location:'Rum',model:'fbb2',enabled:true,version:2,workflow:{id:'wf',revision:1,name:'Laserflow'}}], jobs:job ? [job] : []}, workflows:[{id:'wf',name:'Laserflow',kind:'laser',revisions:[1]}]});
async function settle() { await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve)); }
async function testPreproduce() {
  const calls = [];
  const responses = [ {ok:true,json:async()=>state({id:'a'.repeat(32),project:'P',status:'ready',version:1,workflow_name:'Laserflow',workflow_revision:1,production_authorized:false,physical_done:false,handoff_claimed:false,step:{id:'s',label:'Tillad',role:'teacher',action:'produce'}})} ];
  const {elements} = setup(async (url, options) => { calls.push([url, options]); return responses.shift(); }); await settle();
  const buttons = elements['laser-jobs'].children[0].children.filter(n => n.type === 'button'); assert(buttons.some(b => b.textContent === 'Tillad klargøring til laser'));
}
async function testFinishAndClear() {
  for (const job of [
    {id:'b'.repeat(32),project:'P',status:'received',version:1,workflow_name:'W',workflow_revision:1,production_authorized:true,physical_done:false,handoff_claimed:true,step:{id:'f',label:'Afhent',role:'teacher',action:'collect'}},
    {id:'c'.repeat(32),project:'P',status:'done',version:2,workflow_name:'W',workflow_revision:1,production_authorized:true,physical_done:true,handoff_claimed:true,step:{id:'c',label:'Ryd',role:'teacher',action:'clear'}}
  ]) { const {elements} = setup(async () => ({ok:true,json:async()=>state(job)})); await settle(); const buttons = elements['laser-jobs'].children[0].children.filter(n => n.type === 'button'); assert(buttons.length && (buttons[0].textContent.includes('fysisk') || buttons[0].textContent.includes('ryddet'))); }
}
async function testCreateAndCapturedUpdateVersion() {
  const calls = []; let n = 0;
  const {elements} = setup(async (url, options) => {
    calls.push([url, options]); n++;
    if (n === 1 || n === 3) return {ok:true,json:async()=>state(null)};
    const command = JSON.parse(options.body);
    return {ok:true,json:async()=>({ok:true,action:command.action,result:{id:command.params.id,version:command.expected_version + 1}})};
  }); await settle();
  elements['laser-id'].value = 'laser-fhexa1'; elements['laser-name'].value = 'HEXA'; elements['laser-location'].value = 'Rum'; elements['laser-model'].value = 'fhexa1'; elements['laser-enabled'].checked = true;
  await elements['laser-form'].submit(); await settle(); const create = JSON.parse(calls[1][1].body);
  assert.strictEqual(create.action, 'laser.station.create'); assert.strictEqual(create.params.id, 'laser-fhexa1'); assert.strictEqual(create.expected_version, 0); assert.strictEqual(create.params.enabled, false);
  const editState = state(null); editState.laser_administration.stations.push({id:'laser-fhexa1',name:'HEXA',location:'Rum',model:'fhexa1',enabled:false,version:7,workflow:null});
  calls.length = 0; n = 0; const second = setup(async (url, options) => { calls.push([url, options]); n++; if (n === 1) return {ok:true,json:async()=>editState}; if (n === 2) { const c=JSON.parse(options.body); return {ok:true,json:async()=>({ok:true,action:c.action,result:{id:c.params.id,version:c.expected_version+1}})}; } return {ok:true,json:async()=>editState}; }); await settle();
  const edit = second.elements['laser-stations'].children[1].children.find(node => node.type === 'button'); await edit.click(); assert.strictEqual(second.elements['laser-id'].readOnly, true); assert.strictEqual(second.elements['laser-id'].value, 'laser-fhexa1'); second.elements['laser-name'].value = 'HEXA ny'; await second.elements['laser-form'].submit(); await settle(); const update = JSON.parse(calls[1][1].body); assert.strictEqual(update.action, 'laser.station.update'); assert.strictEqual(update.expected_version, 7); assert.strictEqual(update.params.id, 'laser-fhexa1');
}
async function testExactRetryAndWrongResult() {
  const job = {id:'d'.repeat(32),project:'P',status:'workflow_wait',version:0,workflow_name:'W',workflow_revision:1,production_authorized:false,physical_done:false,handoff_claimed:false,step:{id:'p',label:'Forbered',role:'teacher',action:'prepare'}};
  let calls = 0; const bodies = []; const {elements} = setup(async (url, options) => { calls++; if (calls === 1) return {ok:true,json:async()=>state(job)}; if (options?.body) bodies.push(options.body); if (calls === 2) return {ok:false,status:503,json:async()=>({error:'midlertidig'})}; if (calls === 3) return {ok:true,json:async()=>({ok:true,action:'laser.job.advance',result:{id:job.id,version:1}})}; return {ok:true,json:async()=>state(job)}; }); await settle(); const button = elements['laser-jobs'].children[0].children.find(n => n.type === 'button'); await button.click(); await settle(); assert.strictEqual(elements['laser-retry'].hidden, false); await elements['laser-retry'].click(); await settle(); assert.strictEqual(bodies[1], bodies[0]); }
async function testUnavailableDisablesControls() { const {elements} = setup(async () => ({ok:true,json:async()=>({laser_administration:{status:'unavailable',stations:[],jobs:[]}})})); await settle(); assert.strictEqual(elements['laser-name'].disabled, true); assert.strictEqual(elements['laser-save'].disabled, true); }
async function testWrongResultKeepsRetryPending() { const job = {id:'e'.repeat(32),project:'P',status:'ready',version:1,workflow_name:'W',workflow_revision:1,production_authorized:false,physical_done:false,handoff_claimed:false,step:{id:'p',label:'Forbered',role:'teacher',action:'prepare'}}; let n=0; const {elements}=setup(async(url,options)=>{ n++; if(n===1) return {ok:true,json:async()=>state(job)}; if(n===2) return {ok:true,json:async()=>({ok:true,action:'laser.job.advance',result:{id:'f'.repeat(32),version:2}})}; return {ok:true,json:async()=>state(job)}; }); await settle(); await elements['laser-jobs'].children[0].children.find(x=>x.type==='button').click(); await settle(); assert.strictEqual(elements['laser-retry'].hidden,false); }
(async () => { await testPreproduce(); await testFinishAndClear(); await testCreateAndCapturedUpdateVersion(); await testExactRetryAndWrongResult(); await testUnavailableDisablesControls(); await testWrongResultKeepsRetryPending(); console.log('makerspace lasers fixture tests passed'); })().catch(error => { console.error(error); process.exitCode = 1; });


