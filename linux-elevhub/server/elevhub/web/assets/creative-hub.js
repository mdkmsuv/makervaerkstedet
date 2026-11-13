'use strict';
// Presentation only: permissions, installed-app checks and save/finish stay in the kiosk.
let allWorkshops=[],activeFilter='alle',connected=false,offline=false;
const workshopInfo=window.CreativeWorkshops;
const retiredWorkshops=new Set(['skolegpt','kredsloeb','datatraener']);
const displayOrder=['folie','laser','3dprint','scratch','microbit','internet'];
const node=(tag,className,text)=>{const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;};
const isOpen=app=>(connected||offline)&&app.aaben&&app.klar!==false&&!app.offline_unavailable;
function statusLabel(app){
 if(offline)return app.offline_unavailable?'Kræver serverforbindelse':app.requires_internet?'Kræver internet':((app.type==='browser'||app.type==='web')?'Åbent':'Tilgængelig offline');
 if(!connected)return 'Ingen forbindelse';
 if(app.klar===false)return 'Ikke klar endnu';
 if(app.aaben)return 'Åbent';
 if(app.type==='browser')return 'Spørg din lærer';
 if(typeof app.lukket_indtil==='number')return 'Åbner kl. '+new Date(app.lukket_indtil*1000).toLocaleTimeString('da-DK',{hour:'2-digit',minute:'2-digit'});
 return 'Lukket lige nu';
}
function kort(app){
 const info=workshopInfo[app.id]||{name:app.navn,short:app.beskrivelse,image:'workshop-internet-v2.png'};
 const article=node('article','app');article.dataset.app=app.id;
 const button=node('button','card-link app-open');button.type='button';button.disabled=!isOpen(app);button.setAttribute('aria-label',(isOpen(app)?'Åbn ':'')+info.name+(isOpen(app)?'':': '+statusLabel(app)));
 button.addEventListener('click',event=>aabn(event,app,app.id==='3dprint'));
 const copy=node('span','card-copy');copy.append(node('span','app-title',info.name),node('span','app-description',info.cardDescription||info.short||info.intro));
 const launch=node('span','launch',isOpen(app)?'Åbn':'Lukket lige nu');
 const arrow=node('span','launch-arrow','→');arrow.setAttribute('aria-hidden','true');if(isOpen(app))launch.append(arrow);copy.append(launch);
 const image=node('img','card-photo app-image');image.src='assets/'+info.image;image.alt='';image.width=1774;image.height=887;if(['microbit','internet'].includes(app.id))image.loading='lazy';button.append(copy,image);
 const status=node('span','availability app-status',statusLabel(app));status.dataset.state=offline?'offline':isOpen(app)?'open':'closed';article.append(button,status);return article;
}
function scrollHint(){const last=document.getElementById('apps').lastElementChild;document.getElementById('workshop-scroll-hint').hidden=!last||last.getBoundingClientRect().bottom+scrollY<=innerHeight;}
function renderCards(){
 const focus=document.activeElement,appId=focus?.closest('[data-app]')?.dataset.app;
 const filtered=allWorkshops.filter(app=>activeFilter==='alle'||workshopInfo[app.id]?.group===activeFilter);
 const grid=document.getElementById('apps');grid.replaceChildren(...filtered.map(kort));
 document.getElementById('empty-filter').hidden=filtered.length>0||allWorkshops.length===0;
 document.getElementById('workshop-count').textContent=filtered.length+(filtered.length===1?' værksted':' værksteder');
 if(appId){const card=Array.from(grid.children).find(c=>c.dataset.app===appId);(card?.querySelector('.app-open')||document.getElementById('workshops-title')).focus({preventScroll:true});}
 requestAnimationFrame(scrollHint);
}
function tegn(apps){
 connected=true;
 const available=apps.filter(app=>!retiredWorkshops.has(app.id)&&!retiredWorkshops.has(app.native_app));
 // Preserve the teacher's current visibility choice; offline uses the local catalogue.
 allWorkshops=(offline?available:available.filter(app=>app.aaben)).sort((a,b)=>{const rank=id=>displayOrder.includes(id)?displayOrder.indexOf(id):displayOrder.length;return rank(a.id)-rank(b.id);});
 vis(allWorkshops.length?'apps':'ingen',!offline,offline);
 const key=JSON.stringify(allWorkshops);if(key!==seneste){seneste=key;renderCards();}
}
window.CreativeHub={connection(ok,offlineMode=false){connected=ok;offline=offlineMode;if(!ok)seneste='';},isOffline(){return offline;}};
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{activeFilter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));renderCards();}));
document.querySelectorAll('[data-project-link]').forEach(link=>{const address=LOKALE_APP_URLER.projekter;if(typeof address==='string'){try{const url=new URL(address);if(url.hostname==='127.0.0.1'&&url.protocol==='http:')link.href=url.href;}catch{}}});
window.addEventListener('resize',()=>requestAnimationFrame(scrollHint));
document.fonts?.ready.then(()=>requestAnimationFrame(scrollHint));
opdater();
