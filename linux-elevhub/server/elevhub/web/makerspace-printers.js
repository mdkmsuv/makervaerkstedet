(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let generation = 0, busy = false, active = true, available = false;
  let pending = null, editVersion = null;
  const controllers = new Set();
  const requestId = () => crypto.randomUUID().replaceAll('-', '');
  const message = text => { if (active) $('printer-error').textContent = text || ''; };
  const clearCredential = () => { $('printer-access-code').value = ''; };

  function controls() {
    $('printer-save').disabled = !active || !available || busy || !!pending;
    $('printer-retry').hidden = !pending;
    $('printer-retry').disabled = !active || busy;
    $('printer-refresh').disabled = !active || busy;
    $('printer-cancel').disabled = !active || busy || !!pending;
  }
  function suspend() {
    active = false; generation++; clearCredential(); pending = null;
    controllers.forEach(controller => controller.abort()); controllers.clear(); controls();
  }
  async function request(path, options = {}) {
    const started = generation, controller = new AbortController(); controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, {...options, cache: 'no-store', redirect: 'error', signal: controller.signal});
      let body;
      try { body = await response.json(); }
      catch {
        const error = new Error('Printerserverens svar kunne ikke læses.');
        error.status = response.status; error.retryable = response.ok || response.status >= 500; throw error;
      }
      if (!active || started !== generation) throw new Error('Svar efter sideskift.');
      if (!response.ok) {
        const error = new Error(typeof body?.error === 'string' ? body.error : 'Printerændringen blev afvist.');
        error.status = response.status; error.retryable = response.status >= 500; throw error;
      }
      return body;
    } catch (error) {
      if (!active || started !== generation) { error.stale = true; throw error; }
      if (error.name === 'AbortError') {
        error = new Error('Printerserveren svarede ikke i tide.'); error.retryable = true;
      }
      if (error instanceof TypeError) error.retryable = true;
      throw error;
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }
  function auth(error) {
    if (error.status !== 401) return false;
    suspend(); window.location.href = '/kontrolpanel'; return true;
  }
  function options(select, values, placeholder) {
    const selected = select.value;
    select.replaceChildren(new Option(placeholder, ''));
    values.forEach(value => select.append(new Option(String(value), String(value))));
    select.value = selected;
  }
  function render(data) {
    const config = data?.printer_administration;
    available = config?.status === 'ok' && Array.isArray(config.printers) &&
      Array.isArray(config.models) && Array.isArray(config.bed_types);
    if (!available) {
      $('printer-status').textContent = config?.status === 'not_configured'
        ? 'Printeradministration er ikke konfigureret endnu.' : 'Printeradministration er midlertidigt utilgængelig.';
      $('printers').replaceChildren(); controls(); return;
    }
    options($('printer-model'), config.models, 'Vælg model');
    options($('printer-bed-type'), config.bed_types, 'Vælg pladetype');
    const root = $('printers'); root.replaceChildren();
    $('printer-status').textContent = 'Printerlisten er opdateret.';
    if (!config.printers.length) {
      const empty = document.createElement('p'); empty.className = 'empty';
      empty.textContent = 'Ingen printere er registreret.'; root.append(empty);
    }
    config.printers.forEach(printer => {
      const card = document.createElement('article'); card.className = 'item ' + (printer.enabled ? 'active' : '');
      const heading = document.createElement('h3'); heading.textContent = printer.name; card.append(heading);
      const details = document.createElement('p');
      details.textContent = [printer.location, printer.model, printer.bed_type,
        printer.requires_restart ? 'Forbindelsesfejl · afventer genstart af printertjenesten' :
          (printer.enabled ? 'Aktiv i printkøen' : 'Deaktiveret i printkøen'), 'Version ' + printer.version].filter(Boolean).join(' · ');
      card.append(details);
      renderCertificateTrust(card, printer);
      const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'sekundaer';
      edit.textContent = 'Redigér'; edit.addEventListener('click', () => editPrinter(printer)); card.append(edit); root.append(card);
    });
    renderCertificateOperations(root, config.printers, config.certificate_operations);
    controls();
  }

  function renderCertificateOperations(root, printers, operations) {
    if (!Array.isArray(operations) || !operations.length) return;
    const block = document.createElement('section'); block.className = 'printer-certificate-operations';
    const heading = document.createElement('h3'); heading.textContent = 'Afventende certifikatforsøg'; block.append(heading);
    operations.forEach(operation => {
      const row = document.createElement('p'); row.className = 'small-note';
      const printer = printers.find(item => item.id === operation.id);
      const label = printer ? `${printer.name} · ` : '';
      row.append(document.createTextNode(`${label}${String(operation.channel || '').toUpperCase()} · ` +
        (operation.status === 'error' ? `Fejl: ${operation.error || 'kan ikke genoptages automatisk.'}` :
          'Et tidligere lærerbekræftet forsøg mangler afslutning.')));
      if (printer && typeof operation.id === 'string' && typeof operation.request_id === 'string' &&
          Number.isInteger(operation.expected_version)) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'sekundaer';
        button.textContent = 'Afslut gammelt forsøg';
        button.addEventListener('click', () => cancelCertificateOperation(operation, printer));
        row.append(document.createTextNode(' ')); row.append(button);
      }
      block.append(row);
    });
    root.append(block);
  }

  function cancelCertificateOperation(operation, printer) {
    if (!active || busy || pendingCommand() || !printer) return;
    const question = `Afslut det gamle certifikatforsøg for ${printer.name}?\n\n` +
      'Det afslutter kun den afbrudte journal. En allerede anvendt certifikattillid tilbageføres ikke, ' +
      'og printerjob, printkø og pladestatus ændres ikke.';
    if (!window.confirm(question)) return;
    const params = {id: operation.id, operation_request_id: operation.request_id};
    if (typeof operation.actor === 'string') params.operation_actor = operation.actor;
    send({action: 'printer.certificate.cancel', draft: JSON.stringify(params),
      body: JSON.stringify({action: 'printer.certificate.cancel', params,
        expected_version: operation.expected_version, request_id: requestId()})});
  }

  function renderCertificateTrust(card, printer) {
    const trust = printer.certificate_trust;
    if (!trust?.channels) return;
    const block = document.createElement('div'); block.className = 'printer-certificate-trust';
    const heading = document.createElement('strong'); heading.textContent = 'Printercertifikater'; block.append(heading);
    const probe = document.createElement('button'); probe.type = 'button'; probe.className = 'sekundaer';
    probe.textContent = 'Kontrollér certifikater';
    probe.addEventListener('click', () => probeCertificates(printer));
    block.append(probe);
    for (const channel of ['mqtt', 'ftp']) {
      const state = trust.channels[channel];
      if (!state) continue;
      const row = document.createElement('p'); row.className = 'small-note';
      const label = channel.toUpperCase();
      if (state.status === 'confirmation_required' && state.pending) {
        const pending = state.pending;
        row.append(document.createTextNode(`${label}: Nyt certifikat observeret. Gammelt: ${pending.old_fingerprint || 'intet'} · Nyt: ${pending.new_fingerprint}. `));
        const button = document.createElement('button'); button.type = 'button'; button.className = 'sekundaer';
        button.textContent = `Bekræft ${label}-certifikat`;
        button.addEventListener('click', () => confirmCertificate(printer, channel, pending));
        row.append(button);
      } else if (state.status === 'trusted') {
        row.textContent = `${label}: Godkendt · ${state.pinned_fingerprint}`;
      } else if (state.status === 'error') {
        row.textContent = `${label}: Certifikatstatus kunne ikke læses. ${state.error || 'Kontrollér printertjenesten.'}`;
      } else {
        row.textContent = `${label}: Intet certifikat er godkendt endnu.`;
      }
      block.append(row);
    }
    card.append(block);
  }

  function probeCertificates(printer) {
    if (!active || busy || pendingCommand()) return;
    const params = {id: printer.id};
    send({action: 'printer.certificate.probe', draft: JSON.stringify(params),
      body: JSON.stringify({action: 'printer.certificate.probe', params,
        expected_version: printer.version, request_id: requestId()})});
  }

  function confirmCertificate(printer, channel, pending) {
    if (!active || busy || pendingCommand()) return;
    const oldFingerprint = pending.old_fingerprint || 'intet';
    const question = `Printer: ${printer.name} (${printer.host}, serienummer ${printer.serial})\n` +
      `Forbindelse: ${channel.toUpperCase()}\n` +
      `Gammelt certifikat: ${oldFingerprint}\nNyt certifikat: ${pending.new_fingerprint}\n\n` +
      'Bekræft kun, hvis du har kontrolleret printerens adresse og identitet.';
    if (!window.confirm(question)) return;
    const params = {id: printer.id, channel, fingerprint: pending.new_fingerprint};
    send({action: 'printer.certificate.confirm', draft: JSON.stringify(params),
      body: JSON.stringify({action: 'printer.certificate.confirm', params,
        expected_version: printer.version, request_id: requestId()})});
  }

  function pendingCommand() { return pending !== null; }
  async function load() {
    try { render(await request('/api/makerspace/state')); return true; }
    catch (error) {
      if (!error.stale && !auth(error)) {
        available = false; $('printer-status').textContent = 'Printerlisten kunne ikke hentes.'; message(error.message); controls();
      }
      return false;
    }
  }
  function reset() {
    editVersion = null;
    ['printer-id', 'printer-name', 'printer-location', 'printer-host', 'printer-serial', 'printer-model', 'printer-bed-type']
      .forEach(id => { $(id).value = ''; });
    clearCredential(); $('printer-enabled').checked = false; $('printer-enabled').disabled = true;
  }
  function editPrinter(printer) {
    if (!active || busy) return;
    if (pending) { message('Afklar først den sidste printerændring.'); return; }
    editVersion = printer.version;
    for (const name of ['id', 'name', 'location', 'model', 'host', 'serial']) $('printer-' + name).value = printer[name];
    $('printer-bed-type').value = printer.bed_type;
    $('printer-enabled').checked = printer.enabled === true; $('printer-enabled').disabled = false;
    clearCredential(); message(''); $('printer-name').focus();
  }
  function params() {
    const result = {name: $('printer-name').value, location: $('printer-location').value,
      model: $('printer-model').value, host: $('printer-host').value, serial: $('printer-serial').value,
      access_code: $('printer-access-code').value, enabled: !!$('printer-id').value && $('printer-enabled').checked,
      bed_type: $('printer-bed-type').value};
    if ($('printer-id').value) result.id = $('printer-id').value;
    return result;
  }
  function draft() { const value = params(); delete value.access_code; return JSON.stringify(value); }
  async function send(command) {
    if (!active || busy) return;
    busy = true; pending = command; clearCredential(); controls(); message('');
    try {
      const result = await request('/api/makerspace/commands', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: command.body});
      const certificate = command.action === 'printer.certificate.confirm';
      const probe = command.action === 'printer.certificate.probe';
      const certificateCancel = command.action === 'printer.certificate.cancel';
      if (result?.ok !== true || result.action !== command.action || typeof result.result?.id !== 'string' ||
          !Number.isInteger(result.result.version) || result.result.version < 1 ||
          (certificate && (result.result.channel !== JSON.parse(command.body).params.channel ||
            result.result.fingerprint !== JSON.parse(command.body).params.fingerprint ||
            typeof result.result.reconnected !== 'boolean')) ||
          (probe && (!result.result.identity || !result.result.channels ||
            !result.result.channels.mqtt || !result.result.channels.ftp)) ||
          (certificateCancel && (result.result.operation_request_id !== JSON.parse(command.body).params.operation_request_id ||
            result.result.cancelled !== true || typeof result.result.trust_preserved !== 'boolean'))) {
        const error = new Error('Printerændringen kunne ikke bekræftes.'); error.retryable = true; throw error;
      }
      // A confirmed write is never retried because its subsequent status read failed.
      pending = null;
      if (!certificate && !probe && !certificateCancel && draft() === command.draft && !$('printer-access-code').value) reset();
      if (!await load()) message('Ændringen blev gemt, men printerlisten kunne ikke opdateres.');
    } catch (error) {
      if (error.stale || auth(error)) return;
      if (error.retryable) message(error.message + ' Brug “Afklar sidste printerændring” til at gentage den samme ændring sikkert.');
      else {
        pending = null;
        if (error.status === 409) {
          await load(); message(error.message + ' Dine felter er bevaret. Vælg Redigér på den aktuelle printer for at hente dens nye version.');
        } else message(error.message);
      }
    } finally { busy = false; controls(); }
  }
  $('printer-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!active || busy) return;
    if (pending) { message('Afklar først den sidste printerændring.'); return; }
    if (!available) { message('Hent printerlisten, før du gemmer en ændring.'); return; }
    const values = params(), action = values.id ? 'printer.update' : 'printer.create';
    if (!values.id && !values.access_code) { message('Indtast printerens LAN-adgangskode.'); return; }
    send({action, draft: draft(), body: JSON.stringify({action, params: values,
      expected_version: values.id ? editVersion : 0, request_id: requestId()})});
  });
  $('printer-retry').addEventListener('click', () => { if (pending) send(pending); });
  $('printer-refresh').addEventListener('click', async () => {
    if (!active || busy) return; busy = true; controls();
    try { await load(); } finally { busy = false; controls(); }
  });
  $('printer-cancel').addEventListener('click', () => { if (active && !busy && !pending) reset(); });
  window.addEventListener('pagehide', suspend);
  window.addEventListener('pageshow', event => {
    // A suspended mutation may have committed; reload without replaying it or retaining its secret.
    if (event.persisted) window.location.reload();
  });
  reset(); busy = true; controls(); load().finally(() => { busy = false; controls(); });
})();
