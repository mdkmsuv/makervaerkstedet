/* The authenticated panel requests one persistent server operation, never an EXE URL. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('server-update-card')) return;
  const endpoint = '/api/admin/server-update';
  const storage = 'makervaerkstedet.server-update.v1';
  const states = new Set(['idle','checking','available','current','downloading','verifying','installing','succeeded','failed','interrupted']);
  const running = new Set(['checking','downloading','verifying','installing']);
  const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
  const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const version = value => typeof value === 'string' && value.length > 0 && value.length <= 200;
  let active=false, busy=false, reading=false, stale=true, epoch=0, timer, readController;
  let data=null, pending=null, tracking=null, confirmation=null, authFailure=null;
  let authLost=false, disconnected=false, error='', notice='';
  function validPending(value) {
    if (!value || !uuid(value.body?.command_id)) return false;
    if (value.path === endpoint+'/check') return Object.keys(value.body).length === 1;
    return value.path === endpoint+'/install' && Object.keys(value.body).length === 3
      && value.body.confirmation === 'OPDATER SERVER' && hash(value.body.manifest_sha256) && version(value.target_version);
  }
  try {
    const saved=JSON.parse(sessionStorage.getItem(storage)||'null');
    if (validPending(saved?.pending)) pending=saved.pending;
    if (uuid(saved?.tracking?.command_id) && version(saved.tracking.target_version)) tracking=saved.tracking;
  } catch { /* Invalid local state cannot authorize a request. */ }
  function persist() {
    try {
      if (pending || tracking) sessionStorage.setItem(storage,JSON.stringify({pending,tracking}));
      else sessionStorage.removeItem(storage);
    } catch { /* The in-memory identity remains available on this page. */ }
  }
  function validate(value) {
    if (!value || value.schema !== 1 || typeof value.available !== 'boolean' || !states.has(value.state)
      || !(value.installed_version === null || version(value.installed_version))
      || !(value.command_id === null || uuid(value.command_id))
      || !(value.code === null || typeof value.code === 'string' && value.code.length <= 200)
      || !(value.updated_at === null || typeof value.updated_at === 'string' && value.updated_at.length < 80 && Number.isFinite(Date.parse(value.updated_at)))
      || !(value.queues_paused === null || typeof value.queues_paused === 'boolean')
      || !Number.isSafeInteger(value.downloaded_bytes) || value.downloaded_bytes < 0
      || !Number.isSafeInteger(value.total_bytes) || value.total_bytes < 0
      || !(value.candidate === null || version(value.candidate?.version) && hash(value.candidate.manifest_sha256)
        && Number.isSafeInteger(value.candidate.size) && value.candidate.size > 0)
      || value.state === 'available' && value.candidate === null) throw Error('invalid_response');
    return value;
  }
  async function request(path,body,controller=new AbortController()) {
    const timeout=setTimeout(()=>controller.abort(),10000);
    try {
      const response=await fetch(path,{cache:'no-store',credentials:'same-origin',redirect:'error',signal:controller.signal,
        ...(body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {})});
      const text=await response.text();
      let value;try { if (text.length > 65536) throw Error();value=JSON.parse(text); } catch { throw Error('invalid_response'); }
      if (!response.ok) { const e=Error(typeof value.error === 'string' ? value.error : 'request_refused');e.status=response.status;throw e; }
      return {value,status:response.status};
    } finally { clearTimeout(timeout); }
  }
  function targetVersion() {
    return data?.candidate?.version || (tracking?.command_id === data?.command_id ? tracking.target_version : null);
  }
  function success() { return data?.state === 'succeeded' && !!targetVersion() && data.installed_version === targetVersion(); }
  function canInstall() {
    return active && !busy && !pending && !stale && data?.available && data.state === 'available'
      && data.candidate && version(data.installed_version) && data.candidate.version !== data.installed_version;
  }
  function sync() {
    $('server-update-check').textContent=data?.state === 'interrupted' ? 'Afklar opdateringen' : 'Søg efter serveropdatering';
    $('server-update-check').disabled=!active || busy || !!pending || stale || !data?.available || running.has(data.state) || data.state === 'succeeded' && !success();
    $('server-update-install').hidden=data?.state !== 'available' || !data?.candidate || !data.available;
    $('server-update-install').disabled=!canInstall();
    // A read can be coalesced without disabling the focused status button.
    $('server-update-refresh').disabled=!active;
    $('server-update-refresh').setAttribute('aria-busy',String(reading));
    $('server-update-retry').hidden=!pending;
    $('server-update-retry').disabled=!active || busy || reading;
    $('server-update-confirm-submit').disabled=!canInstall() || confirmation?.manifest_sha256 !== data?.candidate?.manifest_sha256;
    if ($('server-update-confirm').open && $('server-update-confirm-submit').disabled)
      $('server-update-confirm-version').textContent='Opdateringsstatus er ændret. Annullér og kontrollér serverstatus, før du starter.';
  }
  function render() {
    $('server-update-installed').textContent=data?.installed_version || 'Ukendt';
    $('server-update-candidate').hidden=!data?.candidate;
    $('server-update-candidate').textContent=data?.candidate ? 'Kontrolleret udgivelse: '+data.candidate.version : '';
    let status='Søg efter en serveropdatering, når du vil kontrollere nye udgivelser.';
    if (data) status=({idle:status,checking:'Søger efter en serveropdatering …',available:'En nyere kontrolleret serverversion er tilgængelig.',
      current:'Serveren har den seneste kontrollerede version.',downloading:'Henter opdateringen …',verifying:'Kontrollerer opdateringen …',
      installing:'Installerer opdateringen. Serverprogrammet genstarter; forbindelsen kan forsvinde midlertidigt.',
      succeeded:success()?'Hovedserveren er opdateret til '+targetVersion()+'.':'Resultatet er ikke afklaret: den installerede version matcher ikke opdateringen.',
      failed:'Serveropdateringen blev ikke gennemført.',interrupted:'Resultatet er ikke afklaret. Afklar opdateringen undersøger kun det tidligere forløb; installeren startes ikke igen. Kontakt IT, hvis resultatet fortsat er uafklaret.'})[data.state];
    if (data && !data.available) status='Serveropdatering fra panelet er ikke tilgængelig på denne installation.';
    if (data?.state === 'available' && !version(data.installed_version)) status='Den installerede serverversion er ukendt. Hent serverstatus, eller kontakt IT, før du opdaterer.';
    if (notice) status=notice;
    if (pending) status='Svaret mangler. Opdateringen kan være bestilt. Afklar samme anmodning før en ny bestilling.';
    if (disconnected) status=tracking || data && ['downloading','verifying','installing'].includes(data.state)
      ? 'Forbindelsen til serveren er afbrudt. Opdateringen kan stadig køre. Henter status igen automatisk …'
      : 'Forbindelsen til serveren mangler. Hent serverstatus igen.';
    if (authLost) status='Log ind igen for at se opdateringens resultat. En igangværende opdatering fortsætter.';
    $('server-update-status').textContent=status;
    const failure=data?.state === 'failed' ? 'Opdateringen kunne ikke gennemføres. Hent serverstatus, før du søger efter en ny opdatering.' : '';
    $('server-update-error').textContent=error || failure;
    $('server-update-error').hidden=!(error || failure);
    $('server-update-status').classList.toggle('confirmed',success() && !stale && !pending);
    const progress=data?.state === 'downloading' && data.total_bytes > 0 && data.downloaded_bytes <= data.total_bytes;
    $('server-update-download').hidden=!progress;
    if (progress) {
      $('server-update-progress').max=data.total_bytes;$('server-update-progress').value=data.downloaded_bytes;
      const mb=value=>new Intl.NumberFormat('da-DK',{maximumFractionDigits:1}).format(value/1048576);
      $('server-update-bytes').textContent=mb(data.downloaded_bytes)+' af '+mb(data.total_bytes)+' MB hentet';
    }
    const paused=data?.queues_paused;
    $('server-update-queues').textContent=paused === true ? 'Køerne er på pause. Kontrollér maskiner og køer, før du genoptager dem.'
      : paused === false ? 'Observeret køstatus: køerne er ikke pauset.' : 'Køstatus er ikke oplyst.';
    if (stale && paused !== undefined && paused !== null) $('server-update-queues').textContent='Senest kendte køstatus: '+(paused ? 'på pause.' : 'ikke pauset.');
    $('server-update-resume').hidden=paused !== true || stale || !['succeeded','failed'].includes(data?.state) || data.state === 'succeeded' && !success();
    const detail=data?.code === 'download_retained' && success() ? 'Opdateringen lykkedes. Installationsfilen er bevaret til senere oprydning.' : data?.code ? 'Fejlkode: '+data.code : '';
    $('server-update-detail').textContent=[data?.command_id ? 'Anmodning: '+data.command_id : '',detail,
      data?.updated_at ? 'Seneste serverstatus: '+new Date(data.updated_at).toLocaleString('da-DK') : ''].filter(Boolean).join(' · ') || 'Ingen tekniske oplysninger endnu.';
    sync();
  }
  function schedule() {
    clearTimeout(timer);
    if (active) timer=setTimeout(()=>void refresh(),pending || running.has(data?.state) || disconnected ? 2000 : 15000);
  }
  function unauthorized() {
    authLost=true;stale=true;
    if ($('server-update-confirm').open) $('server-update-confirm').close();
    render();
    if (authFailure) authFailure();
  }
  async function refresh(preserveError=false) {
    if (!active || reading) return;
    const run=epoch;reading=true;readController=new AbortController();sync();
    try {
      const next=validate((await request(endpoint,undefined,readController)).value);
      if (!active || run !== epoch) return;
      if (pending && next.command_id === pending.body.command_id) {
        if (pending.path.endsWith('/install')) tracking={command_id:pending.body.command_id,target_version:pending.target_version};
        pending=null;
      }
      data=next;stale=false;authLost=false;disconnected=false;if (!preserveError) error='';notice='';
      if (uuid(next.command_id) && next.candidate && ['downloading','verifying','installing','succeeded','failed','interrupted'].includes(next.state))
        tracking={command_id:next.command_id,target_version:next.candidate.version};
      persist();render();
    } catch (e) {
      if (!active || run !== epoch) return;
      stale=true;
      if (e.status === 401) unauthorized();
      else { disconnected=e.status === undefined && e.message !== 'invalid_response';error=e.message === 'invalid_response' ? 'Serverstatus kunne ikke læses. Hent status igen; der startes ingen ny opdatering.' : e.status ? 'Serverstatus kunne ikke hentes. Prøv at hente status igen.' : '';render(); }
    } finally { reading=false;readController=null;sync();schedule(); }
  }
  async function send(command) {
    if (!active || busy) return;
    busy=true;pending=command;stale=true;error='';notice='';persist();render();
    try {
      const {value,status}=await request(command.path,command.body);
      if (status !== 202 || value.schema !== 1 || value.accepted !== true || value.command_id !== command.body.command_id) throw Error('invalid_response');
      if (command.path.endsWith('/install')) tracking={command_id:command.body.command_id,target_version:command.target_version};
      else tracking=null;
      pending=null;persist();notice='Anmodningen er modtaget. Henter serverstatus …';
    } catch (e) {
      if (e.status === 401) unauthorized();
      else if (e.status >= 400 && e.status < 500) { pending=null;persist();error='Anmodningen blev afvist. Hent serverstatus, før du prøver igen.'; }
      // Unknown response retains the exact request identity, including across reload/login.
    } finally { busy=false;render();schedule(); }
    if (active && pending && !authLost) $('server-update-retry').focus();
    if (active && !pending) await refresh(true);
  }
  function closeConfirmation() {
    confirmation=null;$('server-update-confirm').close();
    if (active && $('server-update-install').getClientRects().length) $('server-update-install').focus();
  }
  $('server-update-check').onclick=()=>{
    if (!$('server-update-check').disabled) void send({path:endpoint+'/check',body:{command_id:crypto.randomUUID()}});
  };
  $('server-update-refresh').onclick=()=>void refresh();
  $('server-update-retry').onclick=()=>{if (pending && !busy) void send(pending);};
  $('server-update-install').onclick=()=>{
    if (!canInstall()) return;
    confirmation={manifest_sha256:data.candidate.manifest_sha256,version:data.candidate.version};
    $('server-update-confirm-version').textContent='Fra '+data.installed_version+' til '+confirmation.version+'.';
    $('server-update-confirm').showModal();sync();$('server-update-confirm-cancel').focus();
  };
  $('server-update-confirm-cancel').onclick=closeConfirmation;
  $('server-update-confirm').addEventListener('cancel',event=>{event.preventDefault();closeConfirmation();});
  window.addEventListener('hashchange',()=>queueMicrotask(()=>{
    if ($('server-update-confirm').open && !$('server-update-card').getClientRects().length) closeConfirmation();
  }));
  $('server-update-confirm-form').onsubmit=event=>{
    event.preventDefault();
    if (!canInstall() || confirmation?.manifest_sha256 !== data.candidate.manifest_sha256) return;
    const command={path:endpoint+'/install',body:{command_id:crypto.randomUUID(),manifest_sha256:confirmation.manifest_sha256,confirmation:'OPDATER SERVER'},target_version:confirmation.version};
    closeConfirmation();$('server-update-status').tabIndex=-1;$('server-update-status').focus();void send(command);
  };
  window.ServerUpdate={
    start(){if (active) return;active=true;epoch++;authLost=false;render();void refresh();},
    stop(){active=false;epoch++;clearTimeout(timer);readController?.abort();if ($('server-update-confirm').open) closeConfirmation();sync();},
    onAuthFailure(callback){authFailure=callback;}
  };
  render();
})();
