/* Teacher-authorized update requests; no device credentials in the browser. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names = {studentcore:'Elevsystem',scratch:'Scratch',datatraener:'Datatræner',matchbox:'Vinduesstyring',updater:'Opdateringstjeneste',server:'Hovedserver',laser:'Laserstation'};
  const roles = {student:'Elevcomputer',server:'Hovedserver',laser_station:'Laserstation'};
  const storage = 'makervaerkstedet.simple-update.pending.v1';
  let active=false, busy=false, stale=true, timer, generation=0, state=null, source=null, pending=null;
  try {
    const saved=JSON.parse(sessionStorage.getItem(storage)||'null');
    if (saved && ['/api/admin/updates','/api/admin/updates/bind','/api/admin/updates/refresh'].includes(saved.path) && /^[a-f0-9-]{36}$/.test(saved.body?.command_id || '')) pending=saved;
  } catch { /* An invalid saved request is never sent. */ }
  function persist(){try{if(pending)sessionStorage.setItem(storage,JSON.stringify(pending));else sessionStorage.removeItem(storage);}catch{/* Same-page retry remains available. */}}
  function sync(){
    $('updates-retry-request').hidden=!pending;
    $('updates-retry-request').disabled=busy || !active;
    $('updates-refresh').disabled=busy;
    $('source-refresh').disabled=busy || !!pending || !active || !source?.enabled || source.state==='refreshing';
    $('updates-content').querySelectorAll('button,select,input').forEach(el=>{el.disabled=busy || !!pending || stale;});
  }
  async function request(path,body){
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await fetch(path,{cache:'no-store',redirect:'error',credentials:'same-origin',signal:controller.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
      let data; try{data=await response.json();}catch{throw new Error('unreadable_response');}
      if(!response.ok){const error=new Error(typeof data.error==='string'?data.error:'unknown_error');error.status=response.status;throw error;}
      return data;
    }finally{clearTimeout(timeout);}
  }
  function render(){
    const selection=$('updates-content').querySelector('[name=release]')?.value;
    const checked=new Set([...$('updates-content').querySelectorAll('[name=device]:checked')].map(x=>x.value));
    const releases=state.catalog.filter(x=>x.orderable===true&&!x.rollback_required);
    const windows=state.catalog.filter(x=>x.orderable===false&&x.platform==='windows-amd64');
    const cards=state.devices.map(d=>{
      const installed=d.installed.map(i=>`${escape(names[i.component]||i.component)}: ${escape(i.version)}`).join(' · ')||'Ikke rapporteret';
      const desired=d.desired.map(x=>{
        const release=state.catalog.find(c=>c.manifest_sha256===x.hash);
        return `${escape(names[x.component]||x.component)}: ${escape(release?.version||'Udgivelse ikke tilgængelig')} · revision ${escape(x.revision)}${x.available?'':' · kan ikke tilbydes lige nu'}`;
      }).join('<br>')||'Ingen bestilling';
      const result=d.result ? `${d.result.state==='failed'?'Fejl':d.result.state==='succeeded'?'Forsøget rapporteres gennemført':'Ukendt resultat'} · revision ${escape(d.result.revision)}${d.result.code?' · '+escape(d.result.code):''}` : 'Intet resultat rapporteret';
      const seen=d.last_seen ? escape(d.last_seen) : 'Ingen rapport';
      return `<article class="system-liste"><h4>${escape(d.device)}</h4><p>${escape(roles[d.role]||d.role)} · ${escape(d.platform)}</p><p><b>Installeret, rapporteret af computeren:</b> ${installed}</p><p><b>Ønsket:</b> ${desired}</p><p><b>Seneste resultat:</b> ${result}</p><p>Seneste rapport: ${seen}</p></article>`;
    }).join('');
    const unbound=state.unbound_devices.map(d=>`<form data-bind="${escape(d)}"><label>${escape(d)} <select name="role" aria-label="Rolle for ${escape(d)}"><option value="student">Elevcomputer · Debian</option><option value="server">Hovedserver · Windows</option><option value="laser_station">Laserstation · Windows</option></select></label> <button class="sekundaer" type="submit">Tilknyt computer</button></form>`).join('');
    const options=releases.map(r=>`<option value="${escape(r.manifest_sha256)}">${escape(names[r.component]||r.component)} · ${escape(r.version)} · ${escape(roles[r.role]||r.role)} (${escape(r.platform)})</option>`).join('');
    const windowsText=windows.length ? `<p>Windows-opdateringer installeres med den signerede rolle-EXE: ${windows.map(r=>escape((roles[r.role]||r.role)+' '+r.version)).join(' · ')}.</p>` : '';
    const checks=state.devices.map(d=>`<label><input type="checkbox" name="device" value="${escape(d.device)}"> ${escape(d.device)} (${escape(roles[d.role]||d.role)})</label>`).join(' ');
    const errors=state.catalog_errors.map(e=>`${escape(e.component||'Udgivelse')}: ${escape(e.code||'ukendt fejl')}`).join('<br>');
    $('updates-content').innerHTML=`${errors?`<p class="fejl">Udgivelser kunne ikke godkendes:<br>${errors}</p>`:''}<section class="system-liste"><h3>Tilknyt registrerede computere</h3>${unbound||'<p>Ingen computere afventer tilknytning.</p>'}</section><section class="system-liste"><h3>Bestil opdatering</h3>${windowsText}${releases.length&&state.devices.length?`<form id="simple-order"><label>Kontrolleret udgivelse <select name="release" required><option value="">Vælg udgivelse</option>${options}</select></label><fieldset><legend>Computere</legend>${checks}</fieldset><button type="submit">Opdatér valgte computere</button></form>`:'<p>Der skal være en kontrolleret udgivelse og en tilknyttet computer.</p>'}</section><section><h3>Computernes status</h3>${cards||'<p>Ingen computere er tilknyttet endnu.</p>'}</section>`;
    const select=$('updates-content').querySelector('[name=release]');
    if(select && [...select.options].some(x=>x.value===selection))select.value=selection;
    $('updates-content').querySelectorAll('[name=device]').forEach(x=>x.checked=checked.has(x.value));
    sync();
  }
  async function refreshSource(){
    if(!active)return;const epoch=generation;
    try{
      const next=await request('/api/admin/updates/source');
      if(!active||epoch!==generation)return;
      if(next.schema!==1||typeof next.enabled!=='boolean'||!['idle','refreshing','ready','failed'].includes(next.state))throw Error('unreadable_response');
      source=next;
      const label={idle:'Afventer søgning',refreshing:'Søger efter opdateringer',ready:'Seneste søgning er gennemført',failed:'Seneste søgning mislykkedes'}[next.state];
      $('source-status').textContent=next.enabled ? label+(next.code?' · fejlkode: '+next.code:'')+(next.last_attempt?' · seneste forsøg: '+next.last_attempt:'')+(next.last_success?' · seneste gennemførte søgning: '+next.last_success:' · ingen gennemført søgning endnu')+(next.active_sequence!=null?' · aktiv udgivelsesrevision: '+next.active_sequence:'')+(next.seen_sequence!=null?' · senest set revision: '+next.seen_sequence:'') : 'Automatisk søgning er ikke aktiveret.';
    }catch(error){if(!active||epoch!==generation)return;source=null;$('source-status').textContent='Kildestatus kunne ikke hentes'+(error.status?' · fejl: '+error.message:'')+'.';}
    sync();
  }
  async function refresh(preserveStatus=false){
    if(!active||busy)return;const epoch=generation;
    try{
      const next=await request('/api/admin/updates');
      if(!active||epoch!==generation)return;
      if(next.schema!==1||!Number.isSafeInteger(next.revision)||!Array.isArray(next.devices)||!Array.isArray(next.catalog)||!Array.isArray(next.catalog_errors)||!Array.isArray(next.unbound_devices))throw Error('unreadable_response');
      state=next;stale=false;render();
      if(!pending && !preserveStatus)$('updates-status').textContent='Status hentet. Installation vises ud fra computerens egen rapport.';
    }catch(error){if(!active||epoch!==generation)return;stale=true;$('updates-status').textContent=error.status===401?'Log ind igen for at se opdateringer.':'Status kunne ikke hentes. Prøv igen. '+(error.status?'Fejl: '+error.message:'');sync();}
  }
  async function send(command){
    if(!active||busy)return;busy=true;pending=command;persist();sync();
    try{
      const receipt=await request(command.path,command.body);
      const isSource=command.path==='/api/admin/updates/refresh';
      if(receipt.command_id!==command.body.command_id||receipt.schema!==1||(isSource?receipt.accepted!==true:!Number.isSafeInteger(receipt.revision)))throw Error('unreadable_response');
      pending=null;persist();stale=true;$('updates-status').textContent=isSource?'Søgningen er bestilt. Se kildestatus for resultatet.':'Bestillingen er gemt på serveren. Computeren skal stadig modtage og rapportere den.';
    }catch(error){
      if(error.status>=400&&error.status<500&&error.status!==401){pending=null;persist();stale=true;$('updates-status').textContent='Anmodningen blev afvist: '+error.message+'. Hent status og prøv igen.';}
      else $('updates-status').textContent='Svaret mangler. Serveren kan have gemt anmodningen. Afklar samme anmodning før en ny bestilling.';
    }finally{busy=false;sync();}
    if(!pending){await refresh(true);await refreshSource();}
  }
  $('updates-content').addEventListener('submit',event=>{
    event.preventDefault();if(busy||pending||stale||!state)return;
    const form=event.target;
    if(form.dataset.bind!==undefined){
      const role=form.elements.role.value, platform=role==='student'?'debian-amd64':'windows-amd64';
      void send({path:'/api/admin/updates/bind',body:{command_id:crypto.randomUUID(),expected_revision:state.revision,device:form.dataset.bind,role,platform}});return;
    }
    if(form.id!=='simple-order')return;
    const release=state.catalog.find(r=>r.manifest_sha256===form.elements.release.value&&r.orderable===true&&!r.rollback_required);
    const devices=[...form.querySelectorAll('[name=device]:checked')].map(x=>x.value);
    if(!release||!devices.length){$('updates-status').textContent='Vælg en udgivelse og mindst én computer.';return;}
    if(devices.some(name=>{const d=state.devices.find(x=>x.device===name);return !d||d.role!==release.role||d.platform!==release.platform;})){$('updates-status').textContent='Udgivelsen passer ikke til alle valgte computere. Vælg computere med samme rolle og platform.';return;}
    void send({path:'/api/admin/updates',body:{command_id:crypto.randomUUID(),expected_revision:state.revision,devices,component:release.component,manifest_sha256:release.manifest_sha256,rollback_manifest_sha256:null}});
  });
  $('updates-refresh').addEventListener('click',()=>{void refresh();void refreshSource();});
  $('source-refresh').addEventListener('click',()=>{if(!busy&&!pending&&source?.enabled)void send({path:'/api/admin/updates/refresh',body:{command_id:crypto.randomUUID()}});});
  $('updates-retry-request').addEventListener('click',()=>{if(pending)void send(pending);});
  async function poll(epoch){await Promise.all([refresh(),refreshSource()]);if(active&&epoch===generation)timer=setTimeout(()=>void poll(epoch),15000);}
  window.UpdateAdmin={start(){if(active)return;active=true;generation++;void poll(generation);sync();},stop(){active=false;generation++;clearTimeout(timer);sync();}};
})();
