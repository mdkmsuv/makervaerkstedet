'use strict';
const $=id=>document.getElementById(id), BASE='/_elevhub/projekter/api/';
const labels={laser:'Laser',folie:'Folie',scratch:'Scratch',cadoodle:'3D / CaDoodle',microbit:'micro:bit',datatraener:'Datatræneren',kredsloeb:'Kredsløb',andet:'Andre filer'};
const parameters=new URL(location.href).searchParams;
const requestedPicker=parameters.get('pick');
const picker=Object.hasOwn(labels,requestedPicker)?requestedPicker:null;
const requestedScope=parameters.has('pick')?requestedPicker:parameters.get('workshop');
const scope=Object.hasOwn(labels,requestedScope)?requestedScope:null;
const invalidScope=(parameters.has('pick')||parameters.has('workshop'))&&!scope;
const categoryOrder=['cadoodle','laser','folie','scratch','microbit','kredsloeb','datatraener','andet'];
const expanded=new Set();
let projects=[],apps=[],uploadId=null,working=false,refreshNumber=0;
const date=n=>new Date(n*1000).toLocaleString('da-DK',{dateStyle:'short',timeStyle:'short'});
const shortDate=n=>new Date(n*1000).toLocaleDateString('da-DK',{day:'numeric',month:'short'});
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,options={}){const r=await fetch(BASE+path,{cache:'no-store',...options});if(!r.ok){let e;try{e=await r.json();}catch{}throw Error(e?.error||'Projektbiblioteket svarer ikke. Prøv igen.');}return r.json();}
function fileUrl(p,revision){return BASE+'file?'+new URLSearchParams({id:p.id,revision:revision||p.revision});}
function shelfKind(p){const app=p.opening?.program;return app==='3dprint'?'cadoodle':labels[app]?app:p.kind==='datatraener'?'datatraener':'andet';}
function canOpen(p){return !invalidScope&&(!scope||shelfKind(p)===scope);}
function open(p,revision){
 if(!canOpen(p))return;
 if(picker){window.parent.postMessage({type:'elevhub-open-project',id:p.id,revision:revision||p.revision},location.origin);return;}
 location.assign('/_elevhub/projekter/open?'+new URLSearchParams({id:p.id,revision:revision||p.revision}));
}
function actions(p,revision){const n=node('div',undefined,'actions');if(canOpen(p)){const b=node('button',picker?'Åbn her':'Åbn i '+(p.opening?.label||labels[p.kind]||'Filviseren'));b.onclick=()=>open(p,revision);n.append(b);}const a=node('a','Hent fil','button secondary');a.href=fileUrl(p,revision);a.download=p.filename;n.append(a);return n;}
async function history(p){$('history-title').textContent=p.name;const box=$('versions');box.replaceChildren(node('p','Henter versioner …'));$('history').showModal();try{const d=await api('meta?id='+p.id);box.replaceChildren(...d.versions.map(v=>{const n=node('div',undefined,'version');n.append(node('span','Version '+v.revision+' · '+date(v.created)),actions(p,v.revision));return n;}));}catch(e){box.textContent=e.message;}}
function icon(kind){
 const paths={cadoodle:'M12 2 3 7v10l9 5 9-5V7L12 2Zm0 10L3 7m9 5 9-5m-9 5v10',laser:'m5 3 16 16M6 15l-3 6 6-3m-5-6H1m11 8v3m3-11h5m-8-5V2',folie:'M4 3h16v12l-6 6H4V3Zm10 18v-6h6M7 7h9M7 11h6',scratch:'m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18',microbit:'M5 5h14v14H5V5ZM8 9h1m3 0h1m3 0h1M8 13h1m3 0h1m3 0h1M9 2v3m6-3v3M9 19v3m6-3v3',kredsloeb:'M6 6h12v12H6V6ZM9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4',datatraener:'M4 17V7h16v10H4Zm4-6h1m6 0h1m-7 3h6M12 3v4M9 21h6m-3-4v4',andet:'M3 5h7l3 3h8v12H3V5Z'};
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
 for(const [key,value]of Object.entries({viewBox:'0 0 24 24',width:'24',height:'24',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(key,value);
 const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[kind]||paths.andet);svg.append(path);return svg;
}
function savedAgo(timestamp){
 const days=Math.max(0,Math.floor((Date.now()/1000-timestamp)/86400));
 return days===0?'Gemt i dag':days===1?'Gemt i går':days<7?'Gemt for '+days+' dage siden':'Gemt '+shortDate(timestamp);
}
function projectCard(p){
 const kind=shelfKind(p),c=node('article',undefined,'project');c.dataset.kind=kind;c.dataset.id=p.id;
 const preview=node('button',undefined,'project-preview');preview.type='button';preview.setAttribute('aria-label','Åbn '+p.name);preview.onclick=()=>open(p);
 const fallback=node('span',undefined,'preview-fallback');fallback.append(icon(kind),node('span','Intet projektbillede'));preview.append(fallback);
 const img=node('img');img.alt='';img.loading='lazy';img.decoding='async';img.style.opacity='0';
 img.onload=()=>{img.style.opacity='1';fallback.hidden=true;};img.onerror=()=>img.remove();
 img.src=BASE+'preview?'+new URLSearchParams({id:p.id,revision:p.revision});preview.append(img);c.append(preview);
 if(kind==='scratch')preview.title='Billede eller figur fra det gemte Scratch-projekt';
 const body=node('div',undefined,'project-body');body.append(node('h3',p.name),node('p',p.folder,'project-folder'));
 const saved=node('p',savedAgo(p.modified),'project-modified');saved.title='Gemt '+date(p.modified)+' · version '+p.revision;body.append(saved);c.append(body);
 const footer=node('div',undefined,'project-footer');
 const b=node('button',picker?'Åbn her':'Åbn projekt →','open-project');b.setAttribute('aria-label',picker?'Åbn her':'Åbn i '+(p.opening?.label||labels[p.kind]||'Filviseren'));b.onclick=()=>open(p);footer.append(b);
 const menu=node('details',undefined,'project-menu'),summary=node('summary','⋯');summary.setAttribute('aria-label','Flere muligheder for '+p.name);menu.append(summary);
 const options=node('div',undefined,'menu-items'),download=node('a','Hent fil','button secondary');download.href=fileUrl(p);download.download=p.filename;options.append(download);
 const versions=node('button','Tidligere versioner','secondary');versions.onclick=()=>{menu.open=false;history(p);};options.append(versions);menu.append(options);footer.append(menu);c.append(footer);
 const expiry=Number(p.expires_at);
 if(Number.isFinite(expiry)&&expiry>0){const days=Math.max(0,Math.ceil((expiry-Date.now()/1000)/86400));const notice=node('p','Slettes '+shortDate(expiry)+(days<=7?' · '+(days===0?'frist udløbet':days===1?'1 dag tilbage':days+' dage tilbage'):''),'project-expiry'+(days<=7?' soon':''));notice.title='Slettes efter 30 dage uden gemte ændringer. Gem en kopi på USB for at beholde projektet.';c.append(notice);}
 return c;
}
function rowLimit(){return picker?2:innerWidth>1100?4:innerWidth>760?3:2;}
function render(){
 if(invalidScope)return;
 const q=$('search').value.trim().toLocaleLowerCase('da'),kind=scope||$('kind').value;
 const available=projects.filter(p=>!scope||shelfKind(p)===scope);
 const matches=available.filter(p=>(!kind||shelfKind(p)===kind)&&(p.name+' '+p.folder).toLocaleLowerCase('da').includes(q));
 $('projects').replaceChildren(...categoryOrder.flatMap(category=>{
  const list=matches.filter(p=>shelfKind(p)===category);if(!list.length)return [];
  const section=node('section',undefined,'category');section.dataset.kind=category;section.setAttribute('aria-labelledby','category-'+category);
  const header=node('div',undefined,'category-header'),title=node('h2');title.id='category-'+category;title.append(icon(category),node('span',labels[category]),node('span',String(list.length),'category-count'));header.append(title);
  const all=Boolean(q||kind||expanded.has(category)),limit=rowLimit();
  if(!q&&!kind&&list.length>limit){const toggle=node('button',all?'Vis færre':'Vis alle '+list.length+' →','category-toggle');toggle.dataset.category=category;toggle.setAttribute('aria-expanded',String(all));toggle.setAttribute('aria-label',(all?'Vis færre':'Vis alle '+list.length)+' i '+labels[category]);toggle.onclick=()=>{all?expanded.delete(category):expanded.add(category);render();document.querySelector('.category-toggle[data-category="'+category+'"]').focus();};header.append(toggle);}
  const grid=node('div',undefined,'category-grid');grid.append(...(all?list:list.slice(0,limit)).map(projectCard));section.append(header,grid);return [section];
 }));
 $('status').textContent=matches.length+(matches.length===1?' projekt':' projekter');
 $('empty-projects').hidden=matches.length>0;
 $('empty-title').textContent=available.length?'Ingen projekter passer til din søgning.':scope?'Ingen gemte projekter i '+labels[scope]+' endnu':'Ingen gemte projekter endnu';
 $('empty-text').textContent=available.length?(scope?'Prøv projektets navn, klasse eller fornavn.':'Prøv projektets navn, jeres gruppe eller et andet værksted.'):(scope?'Gem dit første projekt i værkstedet. Så dukker det op her.':'Gem et projekt i værkstedet, eller brug knappen “Gem en fil”.');
 $('clear-search').textContent=scope?'Ryd søgning':'Vis alle projekter';
 $('clear-search').hidden=!available.length;
}
$('clear-search').onclick=()=>{$('search').value='';if(!scope)$('kind').value='';render();$('search').focus();};
async function refresh(){if(invalidScope){$('status').textContent='Værkstedet kunne ikke genkendes. Åbn projektoversigten fra værkstedet igen.';return;}const request=++refreshNumber;try{const [list,config]=await Promise.all([api('list'+(scope?'?workshop='+scope:'')),api('config')]);if(request!==refreshNumber)return;projects=list.projects;apps=config.apps;render();if(list.scan_error)$('status').textContent+=' · '+list.scan_error;}catch(e){if(request===refreshNumber)$('status').textContent=e.message;}}
$('show-upload').onclick=()=>{uploadId=null;$('upload-error').textContent='';$('upload').showModal();};$('cancel-upload').onclick=()=>{if(!working)$('upload').close();};$('close-history').onclick=()=>$('history').close();$('refresh').onclick=refresh;$('search').oninput=$('kind').onchange=render;
if(parameters.has('pick'))document.body.classList.add('picker');
if(scope||invalidScope){$('kind').value=scope||'';$('kind').disabled=true;$('kind').closest('label').hidden=true;$('show-upload').hidden=true;}
if(scope){document.title=labels[scope]+' · Dine projekter';document.querySelector('.results-heading h2').textContent='Dine projekter · '+labels[scope];document.querySelector('header h1').textContent='Dine projekter · '+labels[scope];document.querySelector('header>div>p:last-child').textContent='Find et projekt fra dette værksted, og byg videre.';}
$('file').onchange=()=>{const f=$('file').files[0];uploadId=null;if(f&&!$('name').value)$('name').value=f.name.replace(/\.(projekt\.svg|\w+)$/,'');$('svg-workshop').hidden=!f||!/\.svg$/i.test(f.name);};
$('upload-form').onsubmit=async e=>{e.preventDefault();if(working)return;const file=$('file').files[0];if(!file||file.size>50*1024*1024){$('upload-error').textContent='Vælg en fil på højst 50 MB.';return;}working=true;$('upload-submit').disabled=true;uploadId ||= crypto.randomUUID().replaceAll('-','');try{const params=new URLSearchParams({id:uploadId,name:$('name').value,folder:$('folder').value,kind:/\.svg$/i.test(file.name)?$('upload-kind').value:'andet',filename:file.name,expected:0});await api('save?'+params,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Elevhub-Project':'1'},body:file});$('upload').close();$('upload-form').reset();$('svg-workshop').hidden=true;uploadId=null;await refresh();}catch(e){$('upload-error').textContent=e.message;}finally{working=false;$('upload-submit').disabled=false;}};
refresh();
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(render,150);});
document.addEventListener('click',event=>{document.querySelectorAll('.project-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});});
document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelectorAll('.project-menu[open]').forEach(menu=>{menu.open=false;menu.querySelector('summary').focus();});});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
