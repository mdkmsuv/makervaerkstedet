/* School-selected software; successful requests are never installed status. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), endpoint='/api/admin/apps';
  const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const key='mkv.app-order.v1';
  let active=false,epoch=0,timer,busy=false,stale=true,state=null,pending=null,confirmation=null,last='';
  const selected=new Set();let defaultDraft=null;
  try {const saved=JSON.parse(sessionStorage.getItem(key)||'null');if(saved&&[endpoint+'/order',endpoint+'/defaults'].includes(saved.path)&&/^[a-f0-9-]{36}$/.test(saved.body?.command_id||''))pending=saved;}catch{}
  function persist(){try{if(pending)sessionStorage.setItem(key,JSON.stringify(pending));else sessionStorage.removeItem(key);}catch{}}
  function message(text){$('apps-status').textContent=text;}
  async function request(path,body){
    const response=await fetch(path,{credentials:'same-origin',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(12000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    const value=await response.json();if(!response.ok){const error=Error(value.error||'request_failed');error.status=response.status;throw error;}return value;
  }
  function devices(){return (state?.devices||[]).filter(d=>selected.has(d.device));}
  function inventory(device,id){return device.installed.find(item=>item.id===id);}
  function present(device,id){const item=inventory(device,id);return item&&item.state!=='absent';}
  function targets(app,action){return devices().filter(d=>action==='remove'?present(d,app.id):action==='install'?!present(d,app.id):inventory(d,app.id)?.state==='installed'&&inventory(d,app.id)?.version!==app.version).map(d=>d.device);}
  function sync(){
    const locked=!active||busy||!!pending||stale;
    $('apps-retry').hidden=!pending;$('apps-retry').disabled=!active||busy;
    $('apps-refresh').disabled=busy;
    $('apps-content').querySelectorAll('input,button').forEach(el=>el.disabled=locked||el.dataset.unavailable==='true');
    $('apps-confirm-submit').disabled=locked;
  }
  function card(entry,scope){
    const app=entry.descriptor,installed=scope.filter(d=>present(d,app.id));
    const partial=installed.filter(d=>inventory(d,app.id)?.state==='incomplete').length;
    const unknown=scope.filter(d=>!d.inventory_known).length;
    const detail=scope.length?`${unknown?'Mindst ':''}${installed.length} af ${scope.length} ${selected.size?'valgte':'tilknyttede'} computere har appen${partial?` · ${partial} med ufuldstændig installation`:''}.${unknown?` Appstatus er ukendt på ${unknown} computere.`:''}`:'Ingen computere har rapporteret endnu.';
    const commands=[['install','Installér på manglende'],['update','Opdatér installerede'],['remove','Afinstallér']].map(([action,label])=>{
      const names=targets(app,action);if(!names.length||entry.retired&&action!=='remove')return '';
      const blocked=devices().some(d=>!d.capable||!d.inventory_known);
      return `<button id="app-${esc(app.id)}-${action}" type="button" class="sekundaer" data-app="${esc(app.id)}" data-action="${action}" data-unavailable="${blocked}">${label} (${names.length})</button>`;
    }).join('');
    const versions=installed.map(d=>`${d.device}: ${inventory(d,app.id).version||'version ukendt'}`).join(' · ');
    // offline = usable without the school server; kraever_internet = needs the internet itself.
    const reach=app.kraever_internet?(app.offline?'Kræver internetforbindelse':'Kræver internetforbindelse og forbindelse til skoleserveren')
      :app.offline?'Kan bruges uden internet':'Kræver forbindelse til skoleserveren';
    return `<article class="catalog-card" data-catalog-app="${esc(app.id)}"><h4>${esc(app.navn)}</h4><p>${esc(app.description)}</p><p class="catalog-version">${entry.retired?'Udgået af kataloget':'Tilgængelig version '+esc(app.version)} · ${reach}</p><p>${esc(detail)}</p>${versions?`<details><summary id="app-versions-${esc(app.id)}">Versioner på computere</summary><p>${esc(versions)}</p></details>`:''}<div class="admin-actions">${commands}</div></article>`;
  }
  function render(){
    if(!state)return;
    const container=$('apps-content'),focus=container.contains(document.activeElement)?document.activeElement.id:null;
    const detailKey=node=>(node.closest('[data-catalog-app]')?.dataset.catalogApp||'')+'|'+node.querySelector('summary')?.textContent;
    const opened=new Set([...container.querySelectorAll('details[open]')].map(detailKey));
    const scope=selected.size?devices():state.devices;
    const installed=state.catalog.filter(e=>scope.some(d=>present(d,e.descriptor.id)));
    const available=state.catalog.filter(e=>!e.retired&&!installed.includes(e));
    const checks=state.devices.map(d=>`<label class="device-choice"><input id="app-device-${esc(d.device)}" type="checkbox" name="app-device" value="${esc(d.device)}" ${selected.has(d.device)?'checked':''}> <span><strong>${esc(d.device)}</strong><small>${d.capable?(d.inventory_known?'Status rapporteret':'Afventer appstatus'):'Skal have opdateringstjenesten klargjort først'}${d.last_seen?' · '+esc(new Date(d.last_seen).toLocaleString('da-DK')):''}</small></span></label>`).join('');
    const errors=state.catalog_errors.length?'<p class="fejl">Appkataloget kunne ikke godkendes fuldt ud. Hent udgivelsesstatus under Opdatér computere.</p>':'';
    const jobs=state.jobs.slice(0,24).map(j=>`<li><strong>${esc(state.catalog.find(e=>e.descriptor.id===j.app_id)?.descriptor.navn||j.app_id)}</strong> · ${esc(j.device)} · ${{install:'Installation',update:'Opdatering',remove:'Afinstallation'}[j.action]}: ${{queued:'bestilt, afventer computer',succeeded:'gennemført ifølge computerens rapport',failed:'mislykkedes — prøv at hente status',superseded:'afløst af en nyere bestilling'}[j.state]||'status ukendt'}</li>`).join('');
    const defaults=defaultDraft||new Set(state.defaults);
    const choices=state.catalog.filter(e=>!e.retired).map(e=>`<label class="device-choice"><input id="default-app-${esc(e.descriptor.id)}" type="checkbox" name="default-app" value="${esc(e.descriptor.id)}" ${defaults.has(e.descriptor.id)?'checked':''}> ${esc(e.descriptor.navn)}</label>`).join('');
    $('apps-content').innerHTML=`${errors}<fieldset><legend>Vælg elevcomputere</legend>${checks||'<p>Ingen elevcomputere er tilknyttet. Tilknyt dem under Opdatér computere.</p>'}</fieldset><p>${selected.size?`${selected.size} computere valgt. Handlinger gælder de relevante valgte computere.`:'Vælg computere for at installere eller afinstallere. Oversigten viser alle tilknyttede computere.'}</p><section aria-labelledby="installed-apps-title"><h3 id="installed-apps-title">Installerede apps</h3><div class="catalog-grid">${installed.map(e=>card(e,scope)).join('')||'<p>Ingen apps er registreret installeret i dette udvalg.</p>'}</div></section><section aria-labelledby="available-apps-title"><h3 id="available-apps-title">Kan installeres</h3><div class="catalog-grid">${available.map(e=>card(e,scope)).join('')||'<p>Der er ingen yderligere apps i det godkendte katalog.</p>'}</div></section><details><summary id="app-defaults-summary">Apps til nye elevcomputere</summary><p>Disse valg installeres på nye tilknyttede computere, når opdateringstjenesten er klar. Eksisterende computere ændres gennem knapperne ovenfor.</p><form id="apps-defaults"><fieldset><legend>Standardvalg</legend>${choices||'<p>Afventer et godkendt katalog.</p>'}</fieldset><button id="app-defaults-save" class="sekundaer" type="submit" data-unavailable="${!state.catalog_sha256}">Gem standardvalg</button></form></details><details><summary id="app-jobs-summary">Seneste appbestillinger</summary><ul class="app-job-list">${jobs||'<li>Ingen appbestillinger endnu.</li>'}</ul></details>`;
    sync();
    container.querySelectorAll('details').forEach(node=>{if(opened.has(detailKey(node)))node.open=true;});
    if(focus){const target=document.getElementById(focus)||$('apps-status');if(!target.id.startsWith('app-')&&!target.hasAttribute('tabindex'))target.tabIndex=-1;target.focus({preventScroll:true});}
  }
  async function refresh(keepMessage=false){
    if(!active||busy)return;const run=epoch;
    try{const next=await request(endpoint);if(!active||run!==epoch)return;
      if(next.schema!==1||!Number.isSafeInteger(next.revision)||!Array.isArray(next.catalog)||!Array.isArray(next.devices)||!Array.isArray(next.jobs)||!Array.isArray(next.defaults)||!Array.isArray(next.catalog_errors))throw Error('invalid_response');
      state=next;stale=false;const encoded=JSON.stringify(next);if(encoded!==last){last=encoded;render();}else sync();
      if(!pending&&!keepMessage)message('Appstatus hentet. En bestilling er først gennemført, når computeren har rapporteret resultatet.');
    }catch(error){if(!active||run!==epoch)return;stale=true;message(error.status===401?'Log ind igen for at se appkataloget.':'Appstatus kunne ikke hentes. Hent status igen, før du bestiller ændringer.');sync();}
  }
  async function send(command){
    if(!active||busy)return;const run=epoch;busy=true;pending=command;persist();sync();
    try{const receipt=await request(command.path,command.body);if(receipt.command_id!==command.body.command_id||!Number.isSafeInteger(receipt.revision))throw Error('invalid_response');
      pending=null;persist();stale=true;if(command.path.endsWith('/defaults'))defaultDraft=null;
      if(active&&run===epoch)message(command.path.endsWith('/defaults')?'Standardvalget er gemt.':'Bestillingen er gemt. Afventer computerens rapport; gem elevernes arbejde inden installation.');
    }catch(error){if(error.status>=400&&error.status<500&&error.status!==401){pending=null;persist();stale=true;if(active&&run===epoch)message(error.status===409?'Status er ændret. Hent status og vælg handlingen igen.':error.status===401?'Log ind igen.':'Bestillingen blev afvist. Kontroller de valgte computere og hent status igen.');}
      else if(active&&run===epoch)message(error.status===401?'Log ind igen, og afklar derefter samme bestilling.':'Svaret mangler. Afklar samme bestilling, før du laver en ny.');
    }finally{busy=false;sync();}if(!pending&&active&&run===epoch)await refresh(true);
  }
  $('apps-content').addEventListener('change',event=>{if(event.target.name==='app-device'){event.target.checked?selected.add(event.target.value):selected.delete(event.target.value);render();}if(event.target.name==='default-app')defaultDraft=new Set([...$('apps-content').querySelectorAll('[name=default-app]:checked')].map(x=>x.value));});
  $('apps-content').addEventListener('click',event=>{
    const button=event.target.closest('[data-action]');if(!button||busy||pending||stale)return;
    const entry=state.catalog.find(e=>e.descriptor.id===button.dataset.app);if(!entry)return;
    const action=button.dataset.action,names=targets(entry.descriptor,action);if(!names.length)return;
    confirmation={path:endpoint+'/order',body:{command_id:crypto.randomUUID(),expected_revision:state.revision,devices:names,app_id:entry.descriptor.id,action,catalog_sha256:entry.catalog_sha256}};
    $('apps-confirm-title').textContent=({install:'Installér ',update:'Opdatér ',remove:'Afinstallér '})[action]+entry.descriptor.navn;
    $('apps-confirm-detail').textContent=`${action==='remove'?'Appen fjernes. Gemte projekter bevares.':'Version '+entry.descriptor.version+'. Gem elevernes arbejde først.'} Gælder: ${names.join(', ')}.`;
    $('apps-confirm').showModal();$('apps-confirm-cancel').focus();
  });
  $('apps-content').addEventListener('submit',event=>{if(event.target.id!=='apps-defaults')return;event.preventDefault();if(busy||pending||stale)return;void send({path:endpoint+'/defaults',body:{command_id:crypto.randomUUID(),expected_revision:state.revision,catalog_sha256:state.catalog_sha256,apps:[...event.target.querySelectorAll('[name=default-app]:checked')].map(x=>x.value)}});});
  $('apps-confirm-form').addEventListener('submit',event=>{event.preventDefault();if(confirmation){const command=confirmation;confirmation=null;$('apps-confirm').close();void send(command);}});
  $('apps-confirm-cancel').onclick=()=>$('apps-confirm').close();
  $('apps-refresh').onclick=()=>void refresh();$('apps-retry').onclick=()=>pending&&void send(pending);
  async function poll(run){await refresh();if(active&&run===epoch)timer=setTimeout(()=>void poll(run),15000);}
  window.AppAdmin={start(){if(active)return;active=true;epoch++;void poll(epoch);sync();},stop(){active=false;epoch++;clearTimeout(timer);if($('apps-confirm').open)$('apps-confirm').close();sync();}};
})();
