'use strict';
let allWorkshops = [], selectedWorkshop = null, activeFilter = 'alle', connected = false, offline = false;
const workshopInfo = window.CreativeWorkshops;
const node = (tag, className, text) => { const el=document.createElement(tag); if(className)el.className=className; if(text!==undefined)el.textContent=text; return el; };
const isOpen = app => (connected || offline) && app.aaben && app.klar !== false && !app.offline_unavailable;
function statusLabel(app) {
  if(offline)return app.offline_unavailable ? 'Åbn fra elevbjælken' : app.requires_internet ? 'Kræver internet' : ((app.type==='browser'||app.type==='web') ? 'Åbent' : 'Tilgængelig offline');
  if(!connected)return 'Ingen forbindelse';
  if(app.klar===false)return 'Ikke klar endnu';
  if(app.aaben)return 'Åbent';
  if(app.type==='browser')return 'Spørg din lærer';
  if(typeof app.lukket_indtil==='number')return 'Åbner kl. '+new Date(app.lukket_indtil*1000).toLocaleTimeString('da-DK',{hour:'2-digit',minute:'2-digit'});
  return 'Lukket lige nu';
}
function kort(app) {
  const info=workshopInfo[app.id]||{name:app.navn,tag:app.kategori,intro:app.beskrivelse,image:'hub-bauhaus-v1.png'};
  const el=node('article','app'); el.dataset.app=app.id;
  const visual=node('a','app-image');visual.href='#vaerksted/'+encodeURIComponent(app.id);visual.setAttribute('aria-label','Se idéer til '+info.name);
  const img=node('img');img.src='assets/'+info.image;img.alt='';img.loading='lazy';img.width=600;img.height=400;
  visual.append(img,node('span','app-status'+(isOpen(app)?'':' closed'),statusLabel(app)));
  const copy=node('div','app-copy');
  const name=node('h3');const title=node('a',null,info.name);title.href=visual.href;name.append(title);
  copy.append(node('p','category',info.tag),name,node('p','app-description',info.short||info.intro));
  const actions=node('div','app-actions');const launch=node('button','app-open',isOpen(app)?'Åbn '+info.name+' →':statusLabel(app));
  launch.disabled=!isOpen(app);launch.setAttribute('aria-label',(isOpen(app)?'Åbn ':'')+info.name+(isOpen(app)?'':': '+statusLabel(app)));launch.addEventListener('click',e=>aabn(e,app));
  const more=node('a','app-about','Få idéer');more.href=visual.href;more.setAttribute('aria-label','Få idéer til '+info.name);actions.append(launch,more);copy.append(actions);el.append(visual,copy);return el;
}
function renderCards() {
  const focus=document.activeElement;const appId=focus?.closest('[data-app]')?.dataset.app;const control=focus?.className;
  const filtered=allWorkshops.filter(a=>activeFilter==='alle'||workshopInfo[a.id]?.group===activeFilter);
  const grid=document.getElementById('apps');grid.replaceChildren(...filtered.map(kort));
  document.getElementById('empty-filter').hidden=filtered.length>0||allWorkshops.length===0;
  document.getElementById('workshop-count').textContent=filtered.length+' værksteder';
  if(appId&&control){const card=Array.from(grid.children).find(c=>c.dataset.app===appId);Array.from(card?.querySelectorAll('a,button')||[]).find(c=>c.className===control)?.focus({preventScroll:true});}
}
function tegn(apps) {
  connected=true;
  if(!offline) allWorkshops=apps.filter(a=>a.aaben);
  else allWorkshops=apps;
  vis(allWorkshops.length?'apps':'ingen',!offline,offline);
  document.querySelectorAll('[data-discover]').forEach(button=>{
    button.hidden=!allWorkshops.some(a=>a.id===button.dataset.discover);
  });
  const key=JSON.stringify(allWorkshops); 
  if(key!==seneste){seneste=key;renderCards();}
  const detailClosed=selectedWorkshop&&!allWorkshops.some(a=>a.id===selectedWorkshop);
  if(detailClosed){
    routeWorkshop();
  }else if(selectedWorkshop)updateDetailAccess();
  if(!detailClosed&&location.hash.startsWith('#vaerksted/')&&!selectedWorkshop)routeWorkshop();
  drawIdea();
}
function updateDetailAccess(){
  const app=allWorkshops.find(a=>a.id===selectedWorkshop),button=document.getElementById('detail-start');
  button.disabled=!app||!isOpen(app);button.textContent=app&&isOpen(app)?(workshopInfo[app.id]?.action||'Åbn værkstedet')+' ↗':app?statusLabel(app):'Værkstedet er ikke tilgængeligt';
  document.getElementById('detail-access').textContent=app&&!isOpen(app)?'Læreren kan hjælpe med at åbne værkstedet.':'';
}
function routeWorkshop(){
  let id='';try{id=decodeURIComponent(location.hash.replace('#vaerksted/',''));}catch{}
  const app=allWorkshops.find(a=>a.id===id),info=workshopInfo[id];
  if(!location.hash.startsWith('#vaerksted/')||!app||!info){
    const wasDetail=selectedWorkshop;selectedWorkshop=null;document.getElementById('workshop-detail').hidden=true;document.getElementById('hub-home').hidden=false;
    document.title='Værkstedet · Makerværkstedet';
    if(wasDetail){const destination=document.getElementById(location.hash.slice(1));if(destination)destination.scrollIntoView();else window.scrollTo(0,0);if(location.hash==='#workshops')document.querySelector('[data-app="'+wasDetail+'"] .app-about')?.focus({preventScroll:true});}
    return;
  }
  if(id===selectedWorkshop)return;
  selectedWorkshop=id;document.getElementById('hub-home').hidden=true;document.getElementById('workshop-detail').hidden=false;
  document.getElementById('detail-name').textContent=info.name;document.getElementById('detail-tag').textContent=info.tag;document.getElementById('detail-title').textContent=info.title;document.getElementById('detail-intro').textContent=info.intro;document.getElementById('detail-tools').textContent=info.tools;
  document.getElementById('detail-image').src='assets/'+info.image;document.getElementById('detail-image').alt='Inspiration til '+info.name;
  document.getElementById('detail-steps').replaceChildren(...info.steps.map(([title,text])=>{const li=node('li');li.append(node('h3',null,title),node('p',null,text));return li;}));
  document.getElementById('detail-ideas').replaceChildren(...info.ideas.map(([title,text],i)=>{const item=node('details','detail-idea');item.open=i===0;item.append(node('summary',null,title),node('p',null,text));return item;}));
  document.title=info.name+' · Værkstedet';
  document.getElementById('detail-tip-text').textContent=info.tip;updateDetailAccess();window.scrollTo(0,0);document.getElementById('detail-title').focus({preventScroll:true});
}
window.CreativeHub={connection(ok,offlineMode=false){connected=ok;offline=offlineMode;if(!ok){seneste='';if(selectedWorkshop)updateDetailAccess();}},isOffline(){return offline;}};
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{activeFilter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));renderCards();}));
document.querySelectorAll('[data-discover]').forEach(button=>button.addEventListener('click',()=>{const id=button.dataset.discover;if(allWorkshops.some(a=>a.id===id))location.hash='vaerksted/'+id;else document.getElementById('fejl').textContent='Dette værksted er ikke tilgængeligt på computeren endnu.';}));
document.getElementById('detail-start').addEventListener('click',e=>{const app=allWorkshops.find(a=>a.id===selectedWorkshop);if(app&&isOpen(app))aabn(e,app);});
window.addEventListener('hashchange',routeWorkshop);
let ideaIndex=-1;
function drawIdea(next=false){
  const choices=allWorkshops.filter(app=>workshopInfo[app.id]&&app.type!=='browser').flatMap(app=>workshopInfo[app.id].ideas.map(idea=>({app,idea})));
  const button=document.getElementById('shuffle-idea'),link=document.getElementById('ticket-link');
  const key=choices.map(c=>c.app.id+c.idea[0]).join('|'),changed=drawIdea.key!==key;
  button.disabled=!choices.length;
  if(!choices.length){
    drawIdea.key=key;drawIdea.deck=[];ideaIndex=-1;
    link.dataset.ready='';link.dataset.app='';link.href='#workshops';link.textContent='Se værkstederne →';
    document.getElementById('ticket-workshop').textContent='ET FORSLAG';
    document.getElementById('ticket-title').textContent='Ingen idéer endnu';
    document.getElementById('ticket-text').textContent='Der er ingen åbne idéværksteder lige nu.';
    return;
  }
  // A complete shuffled deck avoids showing the same idea twice in a row.
  if(!drawIdea.deck||changed){
    drawIdea.deck=choices.slice();drawIdea.key=key;
    for(let i=drawIdea.deck.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[drawIdea.deck[i],drawIdea.deck[j]]=[drawIdea.deck[j],drawIdea.deck[i]];}
    ideaIndex=-1;
  }
  if(!next&&!changed&&link.dataset.ready==='true')return;
  const {app,idea}=drawIdea.deck[++ideaIndex%drawIdea.deck.length],info=workshopInfo[app.id];
  document.getElementById('ticket-workshop').textContent=info.name+' / IDÉ';
  document.getElementById('ticket-title').textContent=idea[0];document.getElementById('ticket-text').textContent=idea[1];
  link.dataset.ready='true';link.dataset.app=app.id;link.href='#vaerksted/'+encodeURIComponent(app.id);link.textContent='Se '+info.name+' →';
}
document.getElementById('shuffle-idea').addEventListener('click',()=>drawIdea(true));
document.querySelectorAll('[data-project-link]').forEach(link=>{
  const address=LOKALE_APP_URLER.projekter;
  if(typeof address==='string'){
    try{const url=new URL(address);if(url.hostname==='127.0.0.1'&&url.protocol==='http:')link.href=url.href;}catch{}
  }
});
opdater();
