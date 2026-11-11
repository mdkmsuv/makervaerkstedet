'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.dataset={};this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.open=false;this.checked=false;}
  appendChild(node){this.children.push(node);return node}
  removeChild(node){this.children.splice(this.children.indexOf(node),1)}
  get firstChild(){return this.children[0]}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn)}
  reset(){} focus(){} showModal(){this.open=true} close(){this.open=false}
}
const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id)};
const log=[];
const printer={id:'mini',name:'Mini',version:2,model:'A1 mini',host:'192.168.1.40',serial:'S1',enabled:false};
const pending={host:printer.host,serial:printer.serial,version:2,old_fingerprint:'a'.repeat(64),new_fingerprint:'b'.repeat(64)};
const operation={id:'mini',channel:'mqtt',expected_version:1,request_id:'e'.repeat(32),actor:'teacher:'+'f'.repeat(32),status:'error',error:'Printeropsætningen er ændret.'};
const window={teacherSessionReady:true,teacherPrinterApi:async(path,body)=>{
  log.push({path,body});
  if(path==='/api/settings')return {printers:[printer],models:['A1 mini'],demo:false};
  if(path==='/api/printers/certificates')return {printers:[{id:'mini',certificate_trust:{channels:{mqtt:{pending}}}}],queue:{paused:true},certificate_operations:[operation]};
  if(body&&window.failOnce){window.failOnce=false;throw Error('Mistet svar')}
  return {ok:true};
}};
const context={window,console,document:{getElementById:get,createElement:tag=>new Node(tag)},crypto:{randomUUID:()=> '01234567-89ab-cdef-0123-456789abcdef'},confirm:()=>true};
vm.runInNewContext(fs.readFileSync('web/printer-settings.js','utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const walk=node=>[node,...node.children.flatMap(walk)];
function click(button){for(const fn of get('printer-settings-list').listeners.click||[])fn({target:{closest:selector=>{
  if(selector==='[data-certificate-probe]'&&button.dataset.certificateProbe)return button;
  if(selector==='[data-certificate-confirm]'&&button.dataset.certificateConfirm)return button;
  if(selector==='[data-certificate-cancel]'&&button.dataset.certificateCancel)return button;
  return null;
}},preventDefault(){}})}
(async()=>{
  await tick();await tick();
  let card=get('printer-settings-list');
  assert(walk(card).some(node=>node.textContent.includes(pending.new_fingerprint)));
  assert(walk(card).some(node=>node.dataset.certificateProbe==='mini'));
  const confirmation=()=>walk(card).find(node=>node.dataset.certificateConfirm==='mini');
  context.confirm=()=>false;click(confirmation());await tick();
  assert(!log.some(item=>item.path.endsWith('/confirm')));
  context.confirm=()=>true;window.failOnce=true;click(confirmation());await tick();await tick();
  let posts=log.filter(item=>item.path.endsWith('/confirm'));
  assert.equal(posts.length,1);assert.equal(posts[0].body.repair,true);assert.equal(posts[0].body.expected_version,2);
  assert.equal(posts[0].body.fingerprint,pending.new_fingerprint);assert.match(posts[0].body.request_id,/^[a-f0-9]{32}$/);
  click(confirmation());await tick();await tick();
  posts=log.filter(item=>item.path.endsWith('/confirm'));
  assert.equal(posts[1].body.request_id,posts[0].body.request_id);
  assert(get('printer-settings-status').textContent.includes('plade'));
  click(walk(card).find(node=>node.dataset.certificateProbe==='mini'));await tick();await tick();
  assert(log.some(item=>item.path.endsWith('/probe')&&item.body.expected_version===2));
  const cancellation=()=>walk(card).find(node=>node.dataset.certificateCancel==='mini');
  context.confirm=()=>false;click(cancellation());await tick();
  assert(!log.some(item=>item.path.endsWith('/cancel')));
  context.confirm=()=>true;window.failOnce=true;click(cancellation());await tick();await tick();
  click(cancellation());await tick();await tick();
  const cancellations=log.filter(item=>item.path.endsWith('/cancel'));
  assert.equal(cancellations.length,2);
  assert.equal(cancellations[0].body.request_id,cancellations[1].body.request_id);
  assert.equal(cancellations[0].body.operation_actor,operation.actor);
  assert.equal(cancellations[0].body.operation_request_id,operation.request_id);
  assert.equal(cancellations[0].body.expected_version,2,'finish a stale journal using the current printer version');
  assert(get('printer-settings-status').textContent.includes('tillid bevares'));
  window.teacherSessionReady=false;window.teacherPrinterSettings.clear();
  assert(!walk(card).some(node=>node.textContent.includes(pending.new_fingerprint)));
  console.log('printer certificate teacher UI: OK');
})().catch(error=>{console.error(error);process.exitCode=1});
