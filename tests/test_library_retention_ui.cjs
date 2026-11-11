'use strict';
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const end = source.indexOf('async function poll(){');
const nodes = new Map();
const node = id => { const value = {id, value:'', innerHTML:'', textContent:'', hidden:false, disabled:false,
  querySelector: () => nodes.get('submit')}; nodes.set(id, value); return value; };
['library-list','library-storage','library-storage-warning','library-retention-days','library-quota-mib',
 'library-search','library-prev','library-next','library-page','library-result','library-error',
 'library-pending','library-policy-form','submit'].forEach(node);
const requests = [];
const context = {console, assert, nodes, requests, URLSearchParams, setTimeout, clearTimeout,
  location:{pathname:'/laerer'}, document:{getElementById:id=>nodes.get(id)},
  localStorage:{getItem:()=> '[]'}, sessionStorage:{removeItem(){}}, CSS:{escape:x=>x},
  window:{}, crypto:{randomUUID:()=> '01234567-89ab-cdef-0123-456789abcdef'},
  confirm:()=>true,
  fetch:async(path, options)=>{requests.push({path,options});return {ok:true,status:200,json:async()=>
    path.startsWith('/api/library?')?{files:[],page:1,total:0,has_more:false,storage:{},policy:{version:1}}:{deleted:true}}}
};
vm.runInNewContext(source.slice(0,end), context);
const run = code => vm.runInNewContext(code, context);
(async()=>{
  run(`adminReady=true;renderLibrary({files:[{digest:'a'.repeat(64),generation:'b'.repeat(32),filename:'<script>.stl',in_use:true,expired:true,size:100}],total:26,has_more:true,policy:{retention_days:90,quota_bytes:1073741824,version:0},storage:{used_bytes:900000000,quota_bytes:1073741824,free_bytes:500000000,warning:true}})`);
  assert(nodes.get('library-list').innerHTML.includes('&lt;script&gt;'));
  assert(!nodes.get('library-list').innerHTML.includes('<script>'));
  assert(nodes.get('library-list').innerHTML.includes('data-library-delete='));
  assert(nodes.get('library-list').innerHTML.includes('disabled'));
  assert.equal(nodes.get('library-next').disabled,false);
  assert.equal(nodes.get('library-storage-warning').hidden,false);
  assert.equal(nodes.get('library-quota-mib').value,1024);
  run(`libraryPolicyDirty=true;nodes.get('library-retention-days').value='17';renderLibrary({files:[],policy:{version:8,retention_days:30,quota_bytes:26214400}})`);
  assert.equal(nodes.get('library-retention-days').value,'17');
  assert.equal(run('libraryPolicyVersion'),0,'poll must not rebase an edited policy');
  context.confirm=()=>false;
  await run(`deleteLibrary('a'.repeat(64),'navn.stl','b'.repeat(32))`);
  assert.equal(requests.length,0,'cancelled confirmation must not mutate');
  context.confirm=()=>true;
  let failures=1;
  context.fetch=async(path,options)=>{requests.push({path,options});return {ok:failures--<=0,status:503,json:async()=>({error:'Prøv igen'})}};
  await run(`deleteLibrary('a'.repeat(64),'navn.stl','b'.repeat(32))`);
  assert(nodes.get('library-pending').innerHTML.includes('Prøv samme sletning igen'));
  const first=JSON.parse(requests[0].options.body);
  context.fetch=async(path,options)=>{requests.push({path,options});return {ok:true,status:200,json:async()=>({files:[],total:0,has_more:false})}};
  await run(`deleteLibrary('a'.repeat(64),'','',true)`);
  assert.equal(JSON.parse(requests[1].options.body).request_id,first.request_id);
  assert.equal(JSON.parse(requests[1].options.body).generation,first.generation);
  assert.equal(nodes.get('library-pending').innerHTML,'');
  run(`libraryDeletes.clear();renderLibrary({files:[],total:0,storage:{pending_deletions:[{digest:'c'.repeat(64),generation:'d'.repeat(32),request_id:'e'.repeat(32)}]}})`);
  assert(nodes.get('library-pending').innerHTML.includes('Prøv samme sletning igen'),'server journal must be usable after a browser reload');
  await run(`deleteLibrary('c'.repeat(64),'','',true)`);
  const recoveredDelete=JSON.parse(requests.find(item=>item.path.includes('/'+'c'.repeat(64)+'/delete')).options.body);
  assert.equal(recoveredDelete.request_id,'e'.repeat(32));
  assert.equal(recoveredDelete.generation,'d'.repeat(32));
  requests.length=0;
  run(`libraryPolicyDirty=true;libraryPolicyVersion=9;nodes.get('library-retention-days').value='30';nodes.get('library-quota-mib').value='64'`);
  let policyFailure=true;
  context.fetch=async(path,options)=>{requests.push({path,options});return {ok:!(path==='/api/library/policy'&&policyFailure),status:503,json:async()=>({error:'Mistet svar',files:[],policy:{version:10}})}};
  await run('saveLibraryPolicy({preventDefault(){}})');
  assert.equal(run('libraryPolicyDirty'),true);
  assert.equal(run('libraryPolicyVersion'),9);
  policyFailure=false;
  await run('saveLibraryPolicy({preventDefault(){}})');
  const policyPosts=requests.filter(item=>item.path==='/api/library/policy').map(item=>JSON.parse(item.options.body));
  assert.equal(policyPosts.length,2);
  assert.equal(policyPosts[0].request_id,policyPosts[1].request_id);
  assert.equal(policyPosts[1].expected_version,9,'retry must preserve the decision despite newer polling');
  assert.equal(run('libraryPolicyDirty'),false);
  assert.equal(run('libraryPolicyVersion'),10);
  requests.length=0;
  nodes.get('library-search').value='ØVNING';
  run('libraryPage=3');
  await run('refreshLibrary()');
  const search=new URL(requests[0].path,'http://local');
  assert.equal(search.searchParams.get('query'),'ØVNING');
  assert.equal(search.searchParams.get('page'),'3');
  let finish;
  context.fetch=()=>new Promise(resolve=>{finish=resolve});
  const pending=run('refreshLibrary()');
  run(`adminReady=false;libraryEpoch++;nodes.get('library-list').innerHTML=''`);
  finish({ok:true,status:200,json:async()=>({files:[{filename:'privat-navn.stl'}]})});
  await pending;
  assert.equal(nodes.get('library-list').innerHTML,'','late response must not expose logged-out teacher data');
  const html=fs.readFileSync('web/index.html','utf8');
  for(const id of nodes.keys())if(id!=='submit')assert(html.includes('id="'+id+'"'),id+' must exist in delivered UI');
  console.log('teacher library retention UI: OK');
})().catch(error=>{console.error(error);process.exitCode=1});
