// App-owned scheduling contract; the local API has separate real-browser tests.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(app) {
  const intervals = [], classes = new Set();
  const status = {textContent:'', classList:{contains:n=>classes.has(n),
    add:n=>classes.add(n), remove:n=>classes.delete(n), toggle:(n,on)=>on?classes.add(n):classes.delete(n)}};
  const state = {name:null, ready:true, stored:false, dirty:false, idle:true, historical:false,
    content:'blank', archive:[], fail:false, saves:0, gate:null, dirtyGate:null};
  const session = {
    naming:()=>state.name,
    saveState:()=>({ready:state.ready && Boolean(state.name), stored:state.stored, historical:state.historical}),
    dirty:async()=>{if(state.dirtyGate)await state.dirtyGate;return state.dirty;},
    save:async options=>{
      assert.equal(options.background,true);
      if(!state.ready || options.expectedNaming!==state.name)return null;
      state.saves++;
      const content=state.content;
      if(state.gate)await state.gate;
      if(state.fail){status.textContent='Fejl: Disken er fuld.';classes.add('error');return false;}
      state.archive.push({name:state.name.title,content});state.stored=true;
      state.dirty=state.content!==content;
      status.textContent='Gemt på denne computer';classes.delete('error');return true;
    }
  };
  const listeners = {};
  const context = {window:{ElevhubProjectSession:session,
      ElevhubProjectAdapter:{type:app,canAutoSave:()=>state.idle},
      addEventListener:(name,fn)=>{listeners[name]=fn;}},
    document:{readyState:'complete',hidden:false,activeElement:null,
      querySelector:selector=>selector.includes('role=')||selector.includes('role="')?status:null,
      getElementById:id=>id==='elevhub-project-bar'?{querySelector:()=>status}:null,
      addEventListener:(name,fn)=>{listeners[name]=fn;}},
    setInterval:fn=>{intervals.push(fn);return intervals.length;},clearInterval:()=>{},
    setTimeout,clearTimeout,console};
  const source=path.join(__dirname,'../apps',app,'web/autosave.js');
  vm.runInNewContext(fs.existsSync(source)?fs.readFileSync(source,'utf8'):'',context);
  return {state,status,async tick(){
    assert(intervals.length>0,app+' must schedule its own autosave');
    for(const fn of intervals)await fn();
  }};
}
const named = f => {f.state.name={classroom:'5A',student:'Testelev',title:'Mit lange projekt'};};

for(const app of ['scratch','3dprint']) {
  test(app+': a newly named project is stored without a manual click',async()=>{
    const f=fixture(app);await f.tick();assert.equal(f.state.archive.length,0);
    named(f);await f.tick();assert.deepEqual(f.state.archive,[{name:'Mit lange projekt',content:'blank'}]);
    assert.match(f.status.textContent,/Gemt/);
  });
  test(app+': changes are saved and unchanged content makes no extra version',async()=>{
    const f=fixture(app);named(f);await f.tick();
    f.state.content='editable work';f.state.dirty=true;await f.tick();await f.tick();
    assert.equal(f.state.archive.length,2);assert.equal(f.state.archive[1].content,'editable work');
  });
  test(app+': an active edit is not interrupted by autosave',async()=>{
    const f=fixture(app);named(f);f.state.idle=false;await f.tick();assert.equal(f.state.saves,0);
    f.state.idle=true;await f.tick();assert.equal(f.state.saves,1);
  });
  test(app+': browsing an earlier version cannot replace the latest working copy',async()=>{
    const f=fixture(app);named(f);f.state.stored=true;f.state.dirty=true;f.state.historical=true;
    await f.tick();await f.tick();assert.equal(f.state.saves,0);
    assert.match(f.status.textContent,/Tidligere version/);assert.match(f.status.textContent,/pause/);
  });
  test(app+': real save errors remain actionable until a successful retry',async()=>{
    const f=fixture(app);named(f);f.state.fail=true;await f.tick();
    assert.match(f.status.textContent,/fuld/);assert.match(f.status.textContent,/Gem projekt/);
    assert(f.status.classList.contains('error'));assert.equal(f.state.archive.length,0);
    await f.tick();assert(f.status.classList.contains('error'));
    f.state.fail=false;await f.tick();assert.equal(f.state.archive.length,1);
    assert(!f.status.classList.contains('error'));
  });
  test(app+': an async dirty check cannot save into another pupil session',async()=>{
    const f=fixture(app);named(f);await f.tick();f.state.dirty=true;
    let release;f.state.dirtyGate=new Promise(r=>{release=r;});
    const pending=f.tick();await Promise.resolve();
    f.state.name=null;f.state.ready=false;release();await pending;
    assert.equal(f.state.archive.length,1);
  });
  test(app+': in-flight saves serialize and later edits stay visibly unsaved',async()=>{
    const f=fixture(app);named(f);let release;f.state.gate=new Promise(r=>{release=r;});
    const pending=f.tick();await new Promise(r=>setImmediate(r));
    await f.tick();assert.equal(f.state.saves,1);
    f.state.content='changed during save';f.state.dirty=true;release();await pending;
    assert(!/^Gemt/.test(f.status.textContent),'an old receipt must not claim newer edits are saved');
    f.state.gate=null;await f.tick();assert.equal(f.state.archive.at(-1).content,'changed during save');
  });
}
