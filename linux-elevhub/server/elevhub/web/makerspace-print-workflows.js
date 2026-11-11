(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let generation = 0, busy = false, active = true;
  let pending = null, current = null;
  const controllers = new Set();
  const requestId = () => crypto.randomUUID().replaceAll('-', '');
  const setText = (id, text) => { const node = $(id); if (node) node.textContent = text || ''; };
  const message = text => { if (active) setText('print-workflow-error', text); };
  const unknown = error => error && (error.retryable || error.status >= 500 || error.status === 0);

  function controls() {
    const retry = $('print-workflow-retry'), refresh = $('print-workflow-refresh');
    if (retry) { retry.hidden = !pending; retry.disabled = !active || busy; }
    if (refresh) refresh.disabled = !active || busy || !!pending;
    for (const rootId of ['print-queue-control', 'print-workflow-bindings', 'print-workflow-jobs']) {
      const root = $(rootId);
      if (!root) continue;
      const buttons = root.querySelectorAll ? root.querySelectorAll('button') : [];
      buttons.forEach(button => { button.disabled = !active || busy || !!pending || button.serverDisabled === true; });
    }
  }
  function suspend() {
    active = false; generation++; pending = null;
    controllers.forEach(controller => controller.abort()); controllers.clear(); controls();
  }
  async function request(path, options = {}) {
    const started = generation, controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, {...options, cache: 'no-store', redirect: 'error', signal: controller.signal});
      let body;
      try { body = await response.json(); }
      catch (cause) { const error = new Error('Makerværkstedets svar kunne ikke læses.'); error.status = response.status; error.retryable = response.ok || response.status >= 500; throw error; }
      if (!active || started !== generation) { const error = new Error('Svar efter sideskift.'); error.stale = true; throw error; }
      if (!response.ok) {
        const error = new Error(typeof body?.error === 'string' ? body.error : 'Makerværkstedets ændring blev afvist.');
        error.status = response.status; error.retryable = response.status >= 500; throw error;
      }
      return body;
    } catch (error) {
      if (!active || started !== generation) { error.stale = true; throw error; }
      if (error.name === 'AbortError') { const timeout = new Error('Makerværkstedets svar kom ikke i tide.'); timeout.retryable = true; throw timeout; }
      if (error instanceof TypeError) error.retryable = true;
      throw error;
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }
  function auth(error) {
    if (error.status !== 401) return false;
    suspend(); window.location.href = '/kontrolpanel'; return true;
  }
  function option(select, value, label) {
    const item = document.createElement('option'); item.value = String(value); item.textContent = String(label); select.append(item);
  }
  function workflows(data) {
    const result = [];
    (Array.isArray(data?.workflows) ? data.workflows : []).forEach(workflow => {
      if (workflow?.kind !== '3d_print' || typeof workflow.id !== 'string' || !Array.isArray(workflow.revisions)) return;
      workflow.revisions.forEach(revision => {
        if (Number.isInteger(revision) && revision > 0) result.push({workflow, revision});
      });
    });
    return result;
  }
  function workflowValue(id, revision) { return id + ':' + revision; }
  function renderBindings(config, data) {
    const root = $('print-workflow-bindings'); if (!root) return; root.replaceChildren();
    const choices = workflows(data);
    if (!Array.isArray(config?.printers) || !config.printers.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'Ingen printere er registreret.'; root.append(empty); return; }
    config.printers.forEach(printer => {
      const card = document.createElement('article'); card.className = 'item';
      const heading = document.createElement('h3'); heading.textContent = printer.name; card.append(heading);
      const detail = document.createElement('p'); detail.textContent = printer.workflow
        ? printer.workflow.name + ' · revision ' + printer.workflow.revision
        : 'Intet printworkflow valgt. Printeren følger den hidtidige kø.'; card.append(detail);
      const select = document.createElement('select'); select.setAttribute('aria-label', 'Workflow for ' + printer.name);
      choices.forEach(choice => option(select, workflowValue(choice.workflow.id, choice.revision), choice.workflow.name + ' · revision ' + choice.revision));
      const currentWorkflow = printer.workflow;
      if (currentWorkflow && choices.some(choice => choice.workflow.id === currentWorkflow.id && choice.revision === currentWorkflow.revision)) select.value = workflowValue(currentWorkflow.id, currentWorkflow.revision);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'primaer'; button.textContent = 'Anvend workflow';
      button.serverDisabled = !choices.length; button.disabled = !active || busy || !!pending || button.serverDisabled;
      button.addEventListener('click', () => {
        if (button.disabled || pending) return;
        const selected = choices.find(choice => workflowValue(choice.workflow.id, choice.revision) === select.value);
        if (!selected) { message('Vælg en offentliggjort 3D-print-workflowrevision.'); return; }
        send({action: 'printer.workflow.assign', body: JSON.stringify({action: 'printer.workflow.assign', params: {id: printer.id, workflow_id: selected.workflow.id, revision: selected.revision}, expected_version: printer.version, request_id: requestId()})});
      });
      card.append(select, button);
      if(currentWorkflow){
        const remove=document.createElement('button');remove.type='button';remove.className='sekundaer';remove.textContent='Fjern workflow';
        remove.serverDisabled=false;
        remove.addEventListener('click',()=>{
          if(remove.disabled||pending)return;
          if(!confirm(`Fjern workflowet fra ${printer.name}? Nye job følger den almindelige printkø. Igangværende job beholder deres valgte workflow, og pladen skal stadig kontrolleres.`))return;
          send({action:'printer.workflow.assign',body:JSON.stringify({action:'printer.workflow.assign',params:{id:printer.id,workflow_id:null,revision:null},expected_version:printer.version,request_id:requestId()})});
        });
        card.append(remove);
      }
      root.append(card);
    });
  }
  const statusLabel = {workflow_wait: 'Afventer bekræftelser eller klar printer', queued: 'I kø',
    preparing: 'Klargør print', dispatching: 'Sender til printer', starting: 'Afventer printstart',
    printing: 'Printer', recovering: 'Kontrollerer printerens job', attention: 'Kræver lærerens kontrol',
    done: 'Print afsluttet', failed: 'Print mislykkedes', cancelled: 'Print afbrudt'};
  const stepLabel = step => step ? step.label + (step.role === 'teacher' ? ' · lærer' : '') : 'Forløbet er afsluttet';
  function jobButton(job, step) {
    if (job.status === 'workflow_wait' && step && ['prepare', 'approve', 'produce'].includes(step.action)) return step.action === 'produce' ? 'Tillad printstart' : 'Bekræft næste trin';
    if (job.status === 'done' && step?.action === 'prepare') return 'Bekræft næste trin';
    if (job.status === 'done' && step?.action === 'collect') return 'Bekræft afhentning';
    if ((job.status === 'done' || job.status === 'failed' || job.status === 'cancelled') && step?.action === 'clear') return 'Jeg har ryddet pladen';
    return '';
  }
  function renderJobs(config) {
    const root = $('print-workflow-jobs'); if (!root) return; root.replaceChildren();
    const jobs = Array.isArray(config?.workflow_jobs) ? config.workflow_jobs : [];
    if (!jobs.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'Ingen workflow-opgaver.'; root.append(empty); return; }
    jobs.forEach(job => {
      const card = document.createElement('article'); card.className = 'item';
      const heading = document.createElement('h3'); heading.textContent = job.filename || job.id; card.append(heading);
      const printer = config.printers.find(item => item.id === job.printer_id);
      const detail = document.createElement('p'); detail.textContent = [printer?.name || job.printer_id,
        statusLabel[job.status] || 'Ukendt status', job.workflow_name,
        job.workflow_revision ? 'Revision ' + job.workflow_revision : '', stepLabel(job.step)].filter(Boolean).join(' · '); card.append(detail);
      if (job.production_authorized !== true && job.status === 'workflow_wait') { const note = document.createElement('p'); note.textContent = 'Afventer lærerens bekræftelser før printstart.'; card.append(note); }
      const label = jobButton(job, job.step);
      if (label) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'primaer'; button.textContent = label;
        button.serverDisabled = false; button.disabled = !active || busy || !!pending;
        button.addEventListener('click', () => {
          if (button.disabled || pending || !job.step) return;
          if(!confirm(`${label} for “${job.filename||job.id}”? Bekræft kun, når du har kontrolleret det aktuelle trin ved printeren.`))return;
          send({action: 'printer.job.advance', body: JSON.stringify({action: 'printer.job.advance', params: {id: job.id, step_id: job.step.id, confirmed: true}, expected_version: job.version, request_id: requestId()})});
        });
        card.append(button);
      }
      root.append(card);
    });
  }
  function renderQueue(config) {
    const root = $('print-queue-control'); if (!root) return; root.replaceChildren();
    const queue = config?.status === 'ok' ? config.queue : null;
    const valid = queue?.id === 'print' && typeof queue.paused === 'boolean'
      && Number.isSafeInteger(queue.version) && queue.version >= 1;
    const state = document.createElement('p'); state.setAttribute('role', 'status');
    state.textContent = !valid ? 'Printkøens pause kan ikke styres fra dette overblik endnu.'
      : queue.paused ? 'Printkøen er sat på pause. Nye print bliver i køen.'
      : 'Printkøen kan klargøre nye print, når printeren og jobbets godkendelser er klar.';
    root.append(state);
    if (!valid) return;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'sekundaer';
    button.textContent = queue.paused ? 'Genoptag printkøen' : 'Sæt printkøen på pause';
    button.addEventListener('click', () => {
      if (!active || button.disabled || busy || pending) return;
      send({action: 'printer.queue.pause', body: JSON.stringify({action: 'printer.queue.pause',
        params: {id: 'print', paused: !queue.paused}, expected_version: queue.version, request_id: requestId()})});
    });
    root.append(button);
  }
  function render(data) {
    current = data; const config = data?.printer_administration;
    renderQueue(config);
    if (!config || config.status !== 'ok') { setText('print-workflow-status', config?.status === 'not_configured' ? 'Printeradministration er ikke konfigureret endnu.' : 'Printeradministration er midlertidigt utilgængelig.'); $('print-workflow-bindings')?.replaceChildren(); $('print-workflow-jobs')?.replaceChildren(); controls(); return; }
    setText('print-workflow-status', 'Printer-workflows er opdateret.'); renderBindings(config, data); renderJobs(config); controls();
  }
  async function load() {
    try { render(await request('/api/makerspace/state')); return true; }
    catch (error) { if (!error.stale && !auth(error)) { renderQueue(null); setText('print-workflow-status', 'Workflow-overblikket kunne ikke hentes.'); message(error.message); controls(); } return false; }
  }
  async function send(command) {
    if (!active || busy || (pending && pending !== command)) return;
    busy = true; pending = command; controls(); message('');
    try {
      const result = await request('/api/makerspace/commands', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: command.body});
      const envelope = JSON.parse(command.body);
      if (result?.ok !== true || result.action !== command.action || typeof result.result?.id !== 'string' || !Number.isInteger(result.result.version) || result.result.version !== envelope.expected_version + 1) { const error = new Error('Printer-workflowændringen kunne ikke bekræftes.'); error.retryable = true; throw error; }
      if (command.action === 'printer.queue.pause' && (result.result.id !== 'print' || result.result.paused !== envelope.params.paused)) { const error = new Error('Printkøens ændring kunne ikke bekræftes.'); error.retryable = true; throw error; }
      pending = null;
      if (!await load()) message('Ændringen blev gemt, men overblikket kunne ikke opdateres.');
    } catch (error) {
      if (error.stale || auth(error)) return;
      if (unknown(error)) { message(error.message + ' Brug “Prøv igen” for samme request.'); }
      else if (error.status === 409) { pending = null; await load(); message(error.message + ' Hent den aktuelle printer- og workflowversion, før du prøver igen.'); }
      else { pending = null; message(error.message); }
    } finally { busy = false; controls(); }
  }
  $('print-workflow-retry')?.addEventListener('click', () => { if (pending) send(pending); });
  $('print-workflow-refresh')?.addEventListener('click', async () => { if (!active || busy || pending) return; busy = true; controls(); try { await load(); } finally { busy = false; controls(); } });
  window.addEventListener('pagehide', suspend); window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
  busy = true; controls(); load().finally(() => { busy = false; controls(); });
})();
