const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../server/elevhub/web/assets/native-3d.js'), 'utf8');
function fixture(offline, teacherOpen = true, finish = true) {
  const calls = [], message = {textContent: ''};
  const context = {console, TypeError, Error, AbortController, starter: false, offlineMode: offline,
    UI_TOKEN: 'fixture-token', COMPUTER: 'vaerksted-05', OFFLINE_APPS: [{id:'3dprint', offline_unavailable:true}],
    window: {CreativeWorkshops:{'3dprint':{name:'3D-print',steps:[]}}}, document:{getElementById(){return message}},
    setTimeout(){return 1},clearTimeout(){},aabn:async()=>calls.push('other-app'),tegn:()=>calls.push('policy-refresh'),
    meld:async()=>{calls.push('teacher');return {apps:[{id:'3dprint',aaben:teacherOpen,klar:true}]};},
    kioskHandling:async(path)=>{calls.push([path]);return finish;},
    fetch:async(url,options)=>{calls.push([url,options]);return {ok:true,json:async()=>({})};}};
  vm.runInNewContext(code, context);
  return {context,calls,message};
}
(async()=>{
  let f=fixture(true);await f.context.aabn({preventDefault(){}},{id:'3dprint'});
  assert.equal(f.context.OFFLINE_APPS[0].offline_unavailable, undefined);
  assert(!f.calls.includes('teacher'));
  assert(f.calls.some(c=>Array.isArray(c)&&c[0].endsWith('/open')));
  assert.match(f.message.textContent,/Åbner/);
  f=fixture(false,false);await f.context.aabn({preventDefault(){}},{id:'3dprint'});
  assert(!f.calls.some(c=>Array.isArray(c)&&c[0].endsWith('/open')));
  assert.match(f.message.textContent,/lukket/);
  f=fixture(false,true);await f.context.aabn({preventDefault(){}},{id:'3dprint'});
  assert(f.calls.includes('teacher'));assert(f.calls.some(c=>Array.isArray(c)&&c[0].endsWith('/open')));
  await f.context.aabn({preventDefault(){}},{id:'scratch'});assert(f.calls.includes('other-app'));
  assert.equal(f.context.starter,false);
  f=fixture(true,true,false);await f.context.aabn({preventDefault(){}},{id:'3dprint'});
  assert.equal(f.message.textContent,'','Continue must not claim a workshop is opening');
  assert.equal(f.context.starter,false);
  console.log('Native 3D: online, offline, teacher closure and other workshops passed');
})().catch(error=>{console.error(error);process.exitCode=1});
