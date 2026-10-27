/* Teacher update queue. Browser sends reviewed release IDs and target IDs only. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const labels = {pending:'Venter i kø',offline:'Slukket eller uden forbindelse',checking:'Kontrollerer identitet og version',
    transferring:'Overfører opdatering',applying:'Anvender opdatering',staged_awaiting_restart:'Klargjort · afventer genstart',
    completed:'Afsluttet og bekræftet',waiting_for_idle:'Afventer ledig computer eller afsluttede maskinopgaver',blocked:'Blokeret · kræver kontrol'};
  const roles = {linux_student:'Linux-elevcomputere',windows_laser_teacher:'Windows-lasercomputer · lærerværktøj',windows_server:'Windows-hovedserver'};
  const blockedHelp = {
    recovery_required:'Gendannelsen er ikke bekræftet. Lad computeren være tændt, og bed IT kontrollere den bevarede sikkerhedskopi og fejlkode. Slet ikke elevfiler eller opdateringens journal.',
    failed_rolled_back:'Opdateringen blev afbrudt. Den tidligere version er gendannet, og elevens gemte arbejde er bevaret. Den ønskede nye version er endnu ikke installeret.',
    profile_missing:'Den eksisterende skoleopsætning kunne ikke bekræftes. Bed IT kontrollere skoleprofil og opdateringsforudsætninger; elevcomputeren skal ikke geninstalleres.',
    version_unknown:'Den installerede version kunne ikke bestemmes. IT skal kontrollere den eksisterende installation, før opdateringen kan fortsætte.',
    version_not_older:'Computeren har allerede denne eller en nyere version. En almindelig opdatering må ikke nedgradere den.',
    local_admin_required:'Vælg Opdatér i denne elevcomputers lokale admin-tilstand for at godkende pakken.',
    windows_long_paths_disabled:'Windows understøtter ikke lange filstier på denne computer. Bed IT aktivere LongPathsEnabled gennem skolens Windows-politik, og prøv igen. Opgaver og installerede filer blev ikke ændret.'
  };
  const versionKey = value => /^\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?$/.test(value||'') ? value.split('.').map(Number).concat([0]).slice(0,4) : null;
  const newer = (candidate,current) => {
    const a=versionKey(candidate),b=versionKey(current);
    if(!a||!b)return true;
    for(let i=0;i<4;i++){if(a[i]!==b[i])return a[i]>b[i];}
    return false;
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let active = false, timer, state, pending, busy = false, generation = 0, refreshRequest;
  let feedState, feedPending, feedBusy=false;
  const shownNotices=new Set(),notice=$('update-notice');
  function validNoticeVersion(value) {
    if(typeof value!=='string')return false;
    const key=versionKey(value);
    if(!key)return false;
    const date=new Date(0);
    date.setUTCFullYear(key[0],key[1]-1,key[2]);
    return key[0]>0&&date.getUTCFullYear()===key[0]&&date.getUTCMonth()===key[1]-1&&date.getUTCDate()===key[2];
  }
  function closeNotice(){if(notice?.open)notice.close('later');}
  function showNotice(feed,installed) {
    const latest=feed.latest,age=Date.now()/1000-feed.last_checked;
    if(!active||!notice||feed.schema!==1||feed.enabled!==true||feed.state!=='available'||feed.error||
        feed.worker_online!==true||!Number.isFinite(feed.last_checked)||feed.last_checked<=0||age< -300||age>86400||
        !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(feed.repository||'')||
        !latest||latest.repository!==feed.repository||!Number.isSafeInteger(latest.release_id)||latest.release_id<=0||
        !validNoticeVersion(installed)||!validNoticeVersion(latest.version)||!newer(latest.version,installed)||
        !Array.isArray(latest.packages)||!latest.packages.some(p=>p?.role==='windows_server')){
      closeNotice();return;
    }
    const key='makervaerkstedet.update-notice.'+JSON.stringify([feed.repository,latest.release_id,latest.version]);
    if(shownNotices.has(key))return;
    try{if(sessionStorage.getItem(key)){shownNotices.add(key);return;}}catch{/* Storage may be unavailable. */}
    if(document.querySelector('dialog[open]'))return;
    $('update-notice-version').textContent=`Version ${latest.version} er tilgængelig til hovedserveren. Installeret version: ${installed}.`;
    $('update-notice-notes').textContent=typeof latest.notes==='string'?latest.notes:'';
    notice.returnValue='';
    try{notice.showModal();}catch{return;}
    shownNotices.add(key);
    try{sessionStorage.setItem(key,'shown');}catch{/* Memory still prevents repeated prompts. */}
  }
  notice?.addEventListener('close',()=>{
    if(!active||notice.returnValue!=='view')return;
    $('update-admin').scrollIntoView({block:'start'});
    $('feed-title').focus({preventScroll:true});
  });
  async function request(path, body) {
    const controller = new AbortController(), timeout = setTimeout(()=>controller.abort(),10000);
    try {
      const response = await fetch(path,{cache:'no-store',redirect:'error',signal:controller.signal,
        ...(body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {})});
      const data = await response.json();
      if (!response.ok) { const error = new Error(data.error || 'Opdateringsserveren afviste anmodningen.'); error.status=response.status; throw error; }
      return data;
    } finally {clearTimeout(timeout);}
  }
  function render() {
    const groups = Object.keys(roles).map(role => {
      const targets = state.targets.filter(t=>t.role===role);
      const releases = state.releases.filter(r=>r.role===role);
      return `<section class="system-liste"><h3>${roles[role]}</h3>
        ${role==='windows_server'?'<p>Opdaterer hovedsystemet på denne Windows-computer. Kontrolpanelet er kortvarigt utilgængeligt under opdateringen. Aktive maskinopgaver skal først afsluttes.</p>':''}
        ${!targets.length ? '<p>Ingen computere er klargjort til denne opdateringskø.</p>' : `
        <label>Kontrolleret udgivelse <select data-release="${role}" ${busy||pending?'disabled':''}>
          <option value="">Vælg udgivelse</option>${releases.map(r=>`<option value="${esc(r.id)}" ${!r.ready?'disabled':''}>${esc(r.version)}${!r.ready?' · mangler kontrol':''}</option>`).join('')}</select></label>
        <p>${releases.some(r=>r.worker_online)?'Opdateringsopgaven har meldt sig fra hovedserveren.':'Opdateringsopgaven har endnu ikke meldt sig, eller svarer ikke nu. Køen bevares.'}</p>
        <div>${targets.map(target=>{
          const job=target.job, ready=target.credentials_ready&&target.enrolled;
          return `<article class="computer"><label><input type="checkbox" data-target="${esc(target.id)}" data-role="${role}" ${!ready||busy||pending?'disabled':''}> <strong>${esc(target.name)}</strong></label>
            <p>${target.credentials_ready?(target.credentials_mode==='local_server'?'Lokal opdateringstjeneste er klargjort':target.credentials_mode==='enrollment_proof'?'SSH-nøgle klar · værtsnøgle bindes efter computerens tilmeldingsbevis':'SSH-nøgle og fastholdt værtsnøgle er klargjort'):(role==='windows_server'?'Serverens lokale opdateringstjeneste mangler':'Serverens SSH-opsætning mangler')}${!target.enrolled&&role==='linux_student'?' · tilmeldingsidentitet mangler eller er ændret':''}${role==='windows_laser_teacher'&&!target.role_verified?' · lasercomputerens rolle er endnu ikke verificeret':''}</p>
            <p>${target.last_verified_version?`Senest bekræftet installeret: ${esc(target.last_verified_version)} · ${esc(new Date(target.last_verified_at*1000).toLocaleString('da-DK'))}`:'Den installerede version er endnu ikke bekræftet af opdateringstjenesten.'}</p>
            <p>${job?`${esc(labels[job.status]||job.status)}${job.paused?' · kø sat på pause':''} · ønsket version ${esc(target.desired_version)} · ${job.attempt} forsøg`:'Ingen ønsket opdatering'}</p>
            ${job&&['transferring','applying'].includes(job.status)?'<p role="status"><strong>Opdatering i gang. Lad computeren være tændt. Genstart ikke.</strong></p>':''}
            ${job&&job.status==='staged_awaiting_restart'?'<p>Lad eleverne gemme arbejdet, og genstart computeren normalt. Serveren kontrollerer resultatet efter opstart.</p>':''}
            ${job&&job.status==='blocked'?`<p>${esc(blockedHelp[job.code]||'Kontrollér computeridentitet, udgivelse og seneste forsøg med IT.')} Statuskode: ${esc(job.code)}</p>`:''}
            ${job&&job.status!=='completed'?`<button type="button" class="sekundaer" data-command="${job.paused?'resume':'pause'}" data-id="${esc(target.id)}" ${busy||pending?'disabled':''}>${job.paused?'Genoptag kø':'Sæt kø på pause'}</button>
            ${['pending','offline','waiting_for_idle','blocked'].includes(job.status)?`<button type="button" class="sekundaer" data-command="retry" data-id="${esc(target.id)}" ${busy||pending?'disabled':''}>Prøv computeren igen</button>`:''}`:''}</article>`;
        }).join('')}</div><button type="button" class="primaer" data-enqueue="${role}" ${busy||pending?'disabled':''}>Sæt valgte computere i kø</button>`}</section>`;
    }).join('');
    $('updates-content').innerHTML = groups + `<details><summary>Seneste opdateringshændelser</summary><ul>${state.audit.map(a=>`<li>${esc(new Date(a.at*1000).toLocaleString('da-DK'))} · ${esc(a.target)} · ${esc(labels[a.action]||({enqueue:'Sat i kø',pause:'Kø sat på pause',resume:'Kø genoptaget',retry:'Nyt forsøg bestilt'}[a.action])||a.action)}</li>`).join('')||'<li>Ingen hændelser endnu.</li>'}</ul></details>`;
    $('updates-retry-request').hidden=!pending;
  }
  async function refresh() {
    if (!active) return;
    if (refreshRequest) return refreshRequest;
    const epoch=generation;
    refreshRequest=(async()=>{
      try {const next=await request('/api/updates/state');if(!active||epoch!==generation)return;
        // Keep selection/focus while a teacher is preparing a queue request.
        const selected=Array.from(document.querySelectorAll('#updates-content [data-target]:checked')).map(e=>e.dataset.target);
        const chosen=Array.from(document.querySelectorAll('#updates-content [data-release]')).map(e=>[e.dataset.release,e.value]);
        state=next;render();
        if(feedState)paintFeed();
        document.querySelectorAll('#updates-content [data-target]').forEach(e=>{e.checked=selected.includes(e.dataset.target);});
        chosen.forEach(([role,value])=>{const el=document.querySelector(`#updates-content [data-release="${role}"]`);if(el)el.value=value;});
        if(!pending)$('updates-status').textContent=state.configured?'Status hentet. Opdateringer starter fra hovedserverens kø.':'Opdateringskøen er endnu ikke klargjort på hovedserveren. Se hjælpen nedenfor.';
      }catch(error){if(active&&epoch===generation)$('updates-status').textContent=error.status===401?'Log ind igen for at se opdateringer.':'Status kunne ikke hentes. Køen bevares; prøv igen om lidt.';}
      finally {refreshRequest=null;}
    })();return refreshRequest;
  }
  async function submit(command) {
    if(busy)return;busy=true;pending=command;render();let accepted=false;
    try {await request('/api/updates/commands',pending);pending=null;accepted=true;$('updates-status').textContent='Anmodningen er gemt i hovedserverens kø.';}
    catch(error){
      if(error.status){pending=null;$('updates-status').textContent=error.message;}
      else $('updates-status').textContent='Svaret mangler. Køen kan være ændret. Brug samme anmodning igen for at undgå dobbeltbestilling.';
    }finally{busy=false;if(state)render();}
    if(accepted){await refresh();$('updates-status').textContent='Anmodningen er gemt i hovedserverens kø.';}
  }
  $('updates-content').addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button||busy||pending)return;
    let action,params;
    if(button.dataset.enqueue){
      const role=button.dataset.enqueue,release_id=document.querySelector(`[data-release="${role}"]`).value;
      const target_ids=Array.from(document.querySelectorAll(`[data-role="${role}"]:checked`)).map(e=>e.dataset.target);
      if(!release_id||!target_ids.length){$('updates-status').textContent='Vælg en udgivelse og mindst én computer.';return;}
      const release=state.releases.find(r=>r.id===release_id);
      if(target_ids.some(id=>!state.targets.find(t=>t.id===id).release_ids.includes(release_id))){$('updates-status').textContent='Den valgte udgivelse gælder ikke alle de valgte computere.';return;}
      if(target_ids.some(id=>!newer(release.version,state.targets.find(t=>t.id===id).last_verified_version))){$('updates-status').textContent='En valgt computer har allerede denne eller en nyere bekræftet version. Vælg kun computere, der mangler opdateringen.';return;}
      if(!confirm(role==='windows_server'?`Sæt hovedserveren i kø til ${release.version}? Opdateringen afventer afsluttede maskinopgaver. Kontrolpanelet bliver kortvarigt utilgængeligt; skolens data bevares.`:`Sæt ${target_ids.length} computer(e) i kø til ${release.version}? Arbejdet bevares. Slukkede computere venter i køen.`))return;
      action='enqueue';params={release_id,target_ids};
    }else if(button.dataset.command){
      action=button.dataset.command;const target=state.targets.find(t=>t.id===button.dataset.id);params={target_id:target.id,version:target.job.version};
    }else return;
    void submit({request_id:crypto.randomUUID().replaceAll('-',''),action,params});
  });
  $('updates-refresh').addEventListener('click',()=>void refresh());
  $('updates-retry-request').addEventListener('click',()=>pending&&void submit(pending));
  function paintFeed() {
    const configured=Boolean(feedState.repository),latest=feedState.latest;
    $('feed-configure').hidden=configured;
    $('feed-check').disabled=!feedState.enabled||feedBusy||Boolean(feedPending);
    $('feed-retry').hidden=!feedPending;
    $('feed-configure').querySelector('button').disabled=feedBusy||Boolean(feedPending);
    const errors={network_unavailable:'Internet eller GitHub kunne ikke nås.',github_rate_limited:'GitHub har midlertidigt begrænset antallet af opslag.',github_unavailable:'GitHub svarede ikke som forventet.',no_release:'Der er endnu ingen offentlig udgivelse.',manifest_missing:'Udgivelsen mangler opdateringsmanifestet.',no_stable_release:'Der er endnu ingen almindelig, stabil udgivelse.',invalid_release:'Udgivelsens oplysninger kunne ikke godkendes.'};
    const checked=feedState.last_checked?` Senest kontrolleret ${new Date(feedState.last_checked*1000).toLocaleString('da-DK')}.`:'';
    $('feed-status').textContent=!configured?'Vælg projektets GitHub-lager for at aktivere udgivelseskontrollen.':
      `${feedState.repository} · ${latest?(feedState.error?'Senest kendte udgivelse: ':'Udgivelse tilgængelig: ')+latest.version+'.':'Ingen godkendt udgivelse fundet endnu.'}${checked}${!feedState.enabled?' Kontrollen er deaktiveret.':''}${feedState.error?' '+(errors[feedState.error]||'Udgivelseskontrollen kunne ikke bekræftes.'):''}${feedState.check_requested?' Ny kontrol er bestilt.':''}${!feedState.worker_online?' Baggrundsopgaven har endnu ikke meldt sig eller svarer ikke nu.':''}`;
    if(!latest){$('feed-latest').innerHTML='';return;}
    const packageRoles={...roles,windows_server:'Windows-hovedserver'};
    const statuses={queued:'Venter på download',downloading:'Henter',ready:'Hentet · integriteten er kontrolleret',imported:'Klargjort til det kontrollerede opdateringsforløb',blocked:'Kræver kontrol af IT',failed:'Download kunne ikke godkendes'};
    const importErrors={local_profile_missing:'IT skal først klargøre en lokal computerprofil til denne rolle.',release_no_longer_current:'Udgivelsen er afløst. Søg efter opdateringer og vælg den aktuelle udgivelse.',package_import_rejected:'Pakken eller den lokale opsætning kunne ikke godkendes. Bed IT kontrollere opdateringstjenesten.'};
    $('feed-latest').innerHTML=`<p>${esc(latest.notes).replace(/\n/g,'<br>')}</p><ul>${latest.packages.map(p=>{
      const download=feedState.downloads.find(d=>d.release_id===latest.release_id&&d.asset===p.asset);
      const catalogRelease=state?.releases.find(r=>r.id===`github-${p.role}-${latest.version}`&&r.role===p.role&&r.version===latest.version&&r.ready);
      return `<li><strong>${esc(packageRoles[p.role]||p.role)}</strong> · ${esc(p.asset)} · ${Math.ceil(p.size/1024/1024)} MB
        ${download?`<span>${esc(statuses[download.status]||download.status)}</span>`:''}
        ${download?.error&&importErrors[download.error]?`<span>${esc(importErrors[download.error])}</span>`:''}
        ${!download||['failed','blocked'].includes(download.status)?`<button type="button" class="sekundaer" data-feed-asset="${esc(p.asset)}" ${feedBusy||feedPending||!feedState.enabled?'disabled':''}>${download?'Hent og kontrollér igen':'Hent og klargør opdatering'}</button>`:''}
        ${download?.status==='imported'&&catalogRelease?`<button type="button" class="sekundaer" data-feed-release="${esc(catalogRelease.id)}" ${busy||pending?'disabled':''}>Vælg udgivelsen i opdateringskøen</button>`:''}</li>`;
    }).join('')}</ul>${feedState.importer_online?'':'<p>Den lokale klargøringstjeneste har endnu ikke meldt sig eller svarer ikke nu. Hentede pakker venter; bed IT kontrollere tjenesten på hovedserveren.</p>'}<p>Hovedserveren kontrollerer de hentede pakker og klargør dem til køen. Vælg derefter udgivelse og computere nedenfor. Eksisterende købestillinger bevares, og elevcomputere genstartes ikke automatisk.</p>`;
  }
  async function refreshFeed() {
    if(!active)return;const epoch=generation;
    try {
      const [next,system]=await Promise.all([request('/api/updates/feed/state'),request('/api/laerer/system').catch(()=>null)]);
      if(active&&epoch===generation){feedState=next;paintFeed();showNotice(next,system?.server?.release_id);}
    }
    catch(error){if(active&&epoch===generation){closeNotice();$('feed-status').textContent=error.status===401?'Log ind igen for at se programopdateringer.':'Udgivelsesstatus kunne ikke hentes. Der er ikke bekræftet nogen ny udgivelse.';}}
  }
  async function sendFeed(command) {
    if(feedBusy)return;feedBusy=true;feedPending=command;if(feedState)paintFeed();
    let message;
    try {await request('/api/updates/feed/commands',command);feedPending=null;message='Anmodningen er gemt. Hovedserverens baggrundsopgave udfører den.';}
    catch(error){if(error.status){feedPending=null;message=error.message;}else message='Svaret mangler. Brug samme anmodning igen for at undgå dobbeltbestilling.';}
    finally{feedBusy=false;}
    await refreshFeed();$('feed-status').textContent=message;
  }
  function feedCommand(action,params){return {request_id:crypto.randomUUID().replaceAll('-',''),action,params};}
  $('feed-configure').addEventListener('submit',event=>{
    event.preventDefault();if(feedBusy||feedPending)return;
    const repository=$('feed-repository').value.trim();
    if(!confirm(`Brug GitHub-lageret ${repository} som udgivelseskilde? Vælg kun projektets officielle lager.`))return;
    void sendFeed(feedCommand('configure',{repository}));
  });
  $('feed-check').addEventListener('click',()=>void sendFeed(feedCommand('check',{})));
  $('feed-latest').addEventListener('click',event=>{
    const choice=event.target.closest('[data-feed-release]');
    if(choice){
      if(busy||pending||!state)return;
      const release=state.releases.find(r=>r.id===choice.dataset.feedRelease);
      if(!release)return;
      const select=document.querySelector(`[data-release="${release.role}"]`);
      if(!select){$('updates-status').textContent='Der er endnu ingen computere klargjort til denne rolle. Bed skolens IT om at tilføje dem.';return;}
      select.value=release.id;select.focus();
      $('updates-status').textContent=`Udgivelse ${release.version} er valgt. Vælg de computere, der skal opdateres, og sæt dem i kø.`;
      return;
    }
    const button=event.target.closest('[data-feed-asset]');if(!button||feedBusy||feedPending)return;
    void sendFeed(feedCommand('download',{release_id:feedState.latest.release_id,asset:button.dataset.feedAsset}));
  });
  $('feed-retry').addEventListener('click',()=>feedPending&&void sendFeed(feedPending));
  async function poll(epoch){await Promise.all([refresh(),refreshFeed()]);if(active&&generation===epoch)timer=setTimeout(()=>void poll(epoch),10000);}
  window.UpdateAdmin={start(){if(active)return;active=true;generation++;void poll(generation);},stop(){active=false;generation++;clearTimeout(timer);closeNotice();}};
})();
