(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let generation = 0, busy = false, active = true, current = null, pending = null;
  let editId = null, editVersion = null;
  const controllers = new Set();
  const requestId = () => crypto.randomUUID().replaceAll('-', '');
  const text = (id, value) => { const node = $(id); if (node) node.textContent = value || ''; };
  const unknown = error => error && (error.retryable || error.status === 0 || error.status >= 500);
  const message = value => { if (active) text('laser-error', value); };
  function controls() {
    const retry = $('laser-retry'), refresh = $('laser-refresh');
    if (retry) { retry.hidden = !pending; retry.disabled = !active || busy; }
    if (refresh) refresh.disabled = !active || busy || !!pending;
    const config = current?.laser_administration;
    const unavailable = !config || config.status !== 'ok';
    ['laser-form', 'laser-stations', 'laser-jobs'].forEach(id => {
      const root = $(id); if (!root) return;
      (root.querySelectorAll ? root.querySelectorAll('button,select,input') : []).forEach(node => {
        if (node.type === 'hidden') return;
        node.disabled = !active || busy || !!pending || unavailable || node.serverDisabled === true;
      });
    });
  }
  function suspend() {
    active = false; generation++; pending = null;
    controllers.forEach(controller => controller.abort()); controllers.clear(); controls();
    const token = $('laser-token'); if (token) token.value = '';
  }
  async function request(path, options = {}) {
    const started = generation, controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, {...options, cache: 'no-store', redirect: 'error', signal: controller.signal});
      let body;
      try { body = await response.json(); } catch (_) { const e = new Error('Laseroverblikkets svar kunne ikke læses.'); e.status = response.status; e.retryable = response.ok || response.status >= 500; throw e; }
      if (!active || started !== generation) { const e = new Error('Svar efter sideskift.'); e.stale = true; throw e; }
      if (!response.ok) { const e = new Error(typeof body?.error === 'string' ? body.error : 'Laserændringen blev afvist.'); e.status = response.status; e.retryable = response.status >= 500; throw e; }
      return body;
    } catch (error) {
      if (!active || started !== generation) { error.stale = true; throw error; }
      if (error.name === 'AbortError') { const e = new Error('Laseroverblikkets svar kom ikke i tide.'); e.retryable = true; throw e; }
      if (error instanceof TypeError) error.retryable = true;
      throw error;
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }
  function auth(error) { if (error.status !== 401) return false; suspend(); window.location.href = '/kontrolpanel'; return true; }
  function option(select, value, label) { const item = document.createElement('option'); item.value = String(value); item.textContent = String(label); select.append(item); }
  function laserWorkflows(data) {
    const result = [];
    (Array.isArray(data?.workflows) ? data.workflows : []).forEach(workflow => {
      if (workflow?.kind !== 'laser' || typeof workflow.id !== 'string' || !Array.isArray(workflow.revisions)) return;
      workflow.revisions.forEach(revision => { if (Number.isInteger(revision) && revision > 0) result.push({workflow, revision}); });
    });
    return result;
  }
  function fillForm(station) {
    editId = station?.id || null; editVersion = Number.isInteger(station?.version) ? station.version : null;
    $('laser-id').value = editId || ''; $('laser-id').disabled = true; $('laser-id').readOnly = true;
    $('laser-name').value = station?.name || ''; $('laser-location').value = station?.location || '';
    $('laser-model').value = station?.model || 'fbb2'; $('laser-enabled').checked = station?.enabled === true; $('laser-save').disabled = false;
    controls();
  }
  function clearForm() { editId = null; editVersion = null; $('laser-form')?.reset(); $('laser-id').value = ''; $('laser-id').disabled = false; $('laser-id').readOnly = false; $('laser-model').value = 'fbb2'; $('laser-save').disabled = false; controls(); }
  function stationCard(station, choices) {
    const card = document.createElement('article'); card.className = 'item';
    const heading = document.createElement('h3'); heading.textContent = station.name; card.append(heading);
    const detail = document.createElement('p'); detail.textContent = [station.location, station.model === 'fhexa1' ? 'FLUX HEXA' : 'FLUX Beambox II', station.enabled ? 'Aktiv' : 'Deaktiveret', 'Fysisk status: ukendt'].filter(Boolean).join(' · '); card.append(detail);
    const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'sekundaer'; edit.textContent = 'Rediger station'; edit.addEventListener('click', () => fillForm(station)); card.append(edit);
    const select = document.createElement('select'); select.setAttribute('aria-label', 'Workflow for ' + station.name);
    choices.forEach(choice => option(select, choice.workflow.id + ':' + choice.revision, choice.workflow.name + ' · revision ' + choice.revision));
    if (station.workflow) select.value = station.workflow.id + ':' + station.workflow.revision;
    const assign = document.createElement('button'); assign.type = 'button'; assign.className = 'primaer'; assign.textContent = 'Anvend laserworkflow'; assign.serverDisabled = !choices.length;
    assign.addEventListener('click', () => { const chosen = choices.find(item => item.workflow.id + ':' + item.revision === select.value); if (!chosen) { message('Vælg en offentliggjort laserworkflowrevision.'); return; } sendCommand('laser.workflow.assign', {id: station.id, workflow_id: chosen.workflow.id, revision: chosen.revision}, station.version); });
    card.append(select, assign);
    if(station.workflow){
      const remove=document.createElement('button');remove.type='button';remove.className='sekundaer';remove.textContent='Fjern workflow';remove.serverDisabled=false;
      remove.addEventListener('click',()=>{
        if(remove.disabled||busy||pending||!active)return;
        if(!confirm(`Fjern workflowet fra ${station.name}? Igangværende job beholder deres valgte forløb. Nye job kan først knyttes til stationen, når du vælger et workflow igen.`))return;
        sendCommand('laser.workflow.assign',{id:station.id,workflow_id:null,revision:null},station.version);
      });
      card.append(remove);
    }
    return card;
  }
  const labels = {workflow_wait: 'Afventer lærerens bekræftelser', queued: 'I kø', received: 'Modtaget', ready: 'Klar', preparing: 'Klargøres', running: 'I gang', done: 'Afsluttet', failed: 'Mislykkedes', cancelled: 'Annulleret'};
  function advanceButton(job) {
    const step = job.step;
    if (!step) return null;
    if (!job.production_authorized && !job.physical_done && ['prepare', 'approve', 'produce'].includes(step.action)) return {action: 'laser.job.advance', label: step.action === 'produce' ? 'Tillad klargøring til laser' : 'Bekræft næste trin'};
    if (job.production_authorized && job.handoff_claimed && !job.physical_done) return {action: 'laser.job.finish', label: 'Lærer: arbejdet er fysisk færdigt'};
    if (job.physical_done && ['collect', 'prepare', 'clear'].includes(step.action)) return {action: 'laser.job.advance', label: step.action === 'clear' ? 'Jeg har ryddet laserområdet' : step.action === 'collect' ? 'Bekræft afhentning' : 'Bekræft næste trin'};
    return null;
  }
  function jobCard(job, config) {
    const card = document.createElement('article'); card.className = 'item';
    const heading = document.createElement('h3'); heading.textContent = job.project || job.id; card.append(heading);
    const detail = document.createElement('p'); detail.textContent = [labels[job.status] || 'Ukendt status', job.workflow_name, Number.isInteger(job.workflow_revision) ? 'Revision ' + job.workflow_revision : '', job.step ? job.step.label + (job.step.role === 'teacher' ? ' · lærer' : '') : ''].filter(Boolean).join(' · '); card.append(detail);
    const station = document.createElement('p'); station.textContent = job.physical_done ? (job.production_authorized ? 'Læreren har markeret arbejdet fysisk færdigt.' : 'Køopgaven er annulleret. Laserområdet skal stadig ryddes og bekræftes.') : 'Fysisk status: ukendt; ingen fjernstart udføres.'; card.append(station);
    const button = advanceButton(job);
    if (button) { const action = document.createElement('button'); action.type = 'button'; action.className = 'primaer'; action.textContent = button.label; action.addEventListener('click', () => {if(!confirm(`${button.label} for “${job.project||job.id}”? Kontrollér det aktuelle trin og laserområdet først.`))return;sendCommand(button.action, button.action === 'laser.job.finish' ? {id: job.id, confirmed: true} : {id: job.id, step_id: job.step.id, confirmed: true}, job.version)}); card.append(action); }
    if (!job.station_id) { const eligible = (Array.isArray(config?.stations) ? config.stations : []).filter(station => station.enabled === true && station.workflow); if (eligible.length) { const select = document.createElement('select'); select.setAttribute('aria-label', 'Laserstation for ' + (job.project || job.id)); eligible.forEach(station => option(select, station.id, station.name)); const bind = document.createElement('button'); bind.type = 'button'; bind.className = 'primaer'; bind.textContent = 'Knyt til laserstation'; bind.addEventListener('click', () => sendCommand('laser.job.bind', {id: job.id, station_id: select.value}, 0)); card.append(select, bind); } else { const note = document.createElement('p'); note.textContent = 'Opgaven kan først knyttes, når en aktiv laserstation har et workflow.'; card.append(note); } }
    if (!['done', 'failed', 'cancelled'].includes(job.status) && job.station_id && Number.isInteger(job.version) && job.version >= 1 && !job.physical_done) { const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'sekundaer'; cancel.textContent = 'Lærer: annullér køopgaven'; cancel.addEventListener('click', () => {if(!confirm(`Annullér køopgaven “${job.project||job.id}”? Det stopper ikke en fysisk laser. Kontrollér maskinen og området først.`))return;sendCommand('laser.job.cancel', {id: job.id, confirmed: true}, job.version)}); card.append(cancel); }
    return card;
  }
  function render(data) {
    current = data; const config = data?.laser_administration;
    if (!config || config.status !== 'ok') { text('laser-status', config?.status === 'not_configured' ? 'Laseradministration er ikke konfigureret endnu.' : 'Laseradministrationen er midlertidigt utilgængelig.'); $('laser-stations')?.replaceChildren(); $('laser-jobs')?.replaceChildren(); controls(); return; }
    text('laser-status', 'Laseradministrationen er opdateret.'); const choices = laserWorkflows(data); const stations = Array.isArray(config.stations) ? config.stations : []; const root = $('laser-stations');
    if (root) { root.replaceChildren(); if (!stations.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'Ingen laserstationer er registreret.'; root.append(empty); } else stations.forEach(station => root.append(stationCard(station, choices))); }
    const jobsRoot = $('laser-jobs'); const jobs = Array.isArray(config.jobs) ? config.jobs : [];
    if (jobsRoot) { jobsRoot.replaceChildren(); if (!jobs.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'Ingen laseropgaver.'; jobsRoot.append(empty); } else jobs.forEach(job => jobsRoot.append(jobCard(job, config))); }
    controls();
  }
  async function load() { try { render(await request('/api/makerspace/state')); return true; } catch (error) { if (!error.stale && !auth(error)) { text('laser-status', 'Laseroverblikket kunne ikke hentes.'); message(error.message); controls(); } return false; } }
  function validResult(result, command) {
    return result?.ok === true && result.action === command.action && result.result?.id === command.params.id && Number.isInteger(result.result?.version) && result.result.version === command.expected_version + 1;
  }
  async function sendEntry(entry) {
    if (!active || busy && pending !== entry || pending && pending !== entry) return false;
    pending = entry; busy = true; controls(); message('');
    try {
      const command = JSON.parse(entry.body);
      const result = await request('/api/makerspace/commands', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: entry.body});
      if (!validResult(result, command)) { const e = new Error('Laserændringen kunne ikke bekræftes.'); e.retryable = true; throw e; }
      pending = null;
      if (['laser.station.create', 'laser.station.update'].includes(command.action)) clearForm();
      const refreshed = await load(); if (!refreshed) message('Ændringen blev gemt, men overblikket kunne ikke opdateres.'); return true;
    } catch (error) {
      if (error.stale || auth(error)) return false;
      if (unknown(error)) message(error.message + ' Brug “Prøv igen” for samme ændring.');
      else if (error.status === 409) { pending = null; await load(); message(error.message + ' Hent den aktuelle laserstatus, før du prøver igen.'); }
      else { pending = null; message(error.message); }
      return false;
    } finally { busy = false; controls(); }
  }
  function sendCommand(action, params, expectedVersion) {
    if (!active || busy || pending || !Number.isInteger(expectedVersion)) return false;
    return sendEntry({action, body: JSON.stringify({action, params, expected_version: expectedVersion, request_id: requestId()})});
  }

  $('laser-form')?.addEventListener('submit', event => { event.preventDefault(); if (busy || pending || !active || !current?.laser_administration || current.laser_administration.status !== 'ok') return; const id = $('laser-id').value.trim(); const name = $('laser-name').value.trim(); const location = $('laser-location').value.trim(); const model = $('laser-model').value; const enabled = $('laser-enabled').checked; if (!name || !id || !['fbb2', 'fhexa1'].includes(model)) { message('Udfyld lokal station-id, navn og vælg en gyldig lasermodel.'); return; } const params = {id, name, location, model, enabled: editId ? enabled : false}; sendCommand(editId ? 'laser.station.update' : 'laser.station.create', params, editId ? editVersion : 0); });
  $('laser-cancel')?.addEventListener('click', () => { if (!busy && !pending) clearForm(); }); $('laser-refresh')?.addEventListener('click', async () => { if (!active || busy || pending) return; busy = true; controls(); try { await load(); } finally { busy = false; controls(); } }); $('laser-retry')?.addEventListener('click', () => { if (!active || busy || !pending) return; sendEntry(pending); });
  window.addEventListener('pagehide', suspend); window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
  busy = true; controls(); load().finally(() => { busy = false; controls(); });
})();



