'use strict';
// Execute the served teacher modules with a controlled DOM/HTTP boundary.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.type=tag==='button'?'button':'';}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(name,value){this[name]=value;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  click(){if(!this.disabled)return this.listeners.click?.({preventDefault(){}});}
  reset(){}
  querySelectorAll(selector){return this.children.flatMap(c=>[...(selector.toUpperCase().split(',').includes(c.tagName)?[c]:[]),...c.querySelectorAll(selector)]);}
}
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
function fixture(kind,responses,confirmed=true){
  const ids=kind==='print'?['print-workflow-status','print-workflow-error','print-workflow-bindings','print-workflow-jobs','print-queue-control','print-workflow-retry','print-workflow-refresh']:
    ['laser-status','laser-error','laser-retry','laser-refresh','laser-form','laser-id','laser-name','laser-location','laser-model','laser-enabled','laser-save','laser-cancel','laser-stations','laser-jobs'];
  const nodes=new Map(ids.map(id=>[id,new Element()])),calls=[],events={},prompts=[];
  const context={document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag)},window:{location:{href:''},addEventListener:(n,fn)=>events[n]=fn,reload(){}},
    confirm:message=>{prompts.push(message);return confirmed;},crypto:{randomUUID:()=> '12345678-1234-4234-8234-123456789abc'},AbortController,setTimeout,clearTimeout,console,
    fetch:async(url,options={})=>{calls.push({url,options});const response=responses.shift();if(!response)throw Error('Unexpected HTTP call');if(response.error)throw response.error;
      return {ok:response.status===200,status:response.status,json:async()=>response.body};}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../server/elevhub/web',kind==='print'?'makerspace-print-workflows.js':'makerspace-lasers.js'),'utf8'),context);
  return {nodes,calls,prompts,events,remove(){const id=kind==='print'?'print-workflow-bindings':'laser-stations';return nodes.get(id).querySelectorAll('button').find(b=>b.textContent==='Fjern workflow');},retry(){return nodes.get(kind==='print'?'print-workflow-retry':'laser-retry');}};
}
function state(kind,workflow=true,version=7){
  const binding=workflow?{id:'wf',revision:1,name:'Forløb'}:null;
  const data={workflows:[{id:'wf',name:'Forløb',kind:kind==='print'?'3d_print':'laser',revisions:[1]}]};
  data[kind==='print'?'printer_administration':'laser_administration']={status:'ok',printers:[{id:'p1',name:'Printer',version,workflow:binding}],stations:[{id:'p1',name:'Laser',version,workflow:binding,enabled:true,model:'fbb2',location:'Rum'}],workflow_jobs:[],jobs:[]};
  return data;
}
(async()=>{
  for(const kind of ['print','laser']){
    const action=kind==='print'?'printer.workflow.assign':'laser.workflow.assign';
    const denied=fixture(kind,[{status:200,body:state(kind)}],false);await settle();
    await denied.remove().click();await settle();assert.equal(denied.calls.length,1);assert.equal(denied.prompts.length,1);
    const retried=fixture(kind,[{status:200,body:state(kind)},{status:503,body:{error:'Midlertidig'}},{status:200,body:{ok:true,action,result:{id:'p1',version:8,workflow:null}}},{status:200,body:state(kind,false,8)}]);await settle();
    await retried.remove().click();await settle();assert.equal(retried.retry().hidden,false);assert.equal(retried.remove().disabled,true);
    const first=retried.calls[1].options.body,command=JSON.parse(first);assert.equal(command.action,action);
    assert.deepEqual(command.params,{id:'p1',workflow_id:null,revision:null});assert.equal(command.expected_version,7);
    await retried.retry().click();await settle();assert.equal(retried.calls[2].options.body,first);assert.equal(retried.remove(),undefined);
    const conflict=fixture(kind,[{status:200,body:state(kind)},{status:409,body:{error:'Konflikt'}},{status:200,body:state(kind,true,8)}]);await settle();
    await conflict.remove().click();await settle();assert.equal(conflict.calls.length,3);assert.equal(conflict.retry().hidden,true);
    const inactive=fixture(kind,[{status:200,body:state(kind)}]);await settle();inactive.events.pagehide();await inactive.remove().click();await settle();assert.equal(inactive.calls.length,1);
  }
  console.log('PASS printer/laser workflow removal: confirmation, exact retry, conflicts, departed page');
})().catch(error=>{console.error(error);process.exitCode=1;});
