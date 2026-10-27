(function () {
'use strict';

/* Statusfeltet bruger kun den eksplicitte systemkontrakt. Især må policy-version
   fra den almindelige elevoversigt ikke vises som softwareversion. */
const systemEl = id => document.getElementById(id);
let systemTimer = null;
let systemRequest = null;
let systemGeneration = 0;
let systemAktiv = false;
let systemAuthFailure = null;
let systemController = null;
let systemAbortTimer = null;
let systemLastServer = null;

function node(tag, text, className) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined && text !== null) value.textContent = String(text);
  return value;
}
function unknown(value) {
  return value === null || value === undefined || value === '' ? 'Ukendt' : String(value);
}
function booleanLabel(value, yes, no) {
  return value === true ? yes : value === false ? no : 'Ukendt';
}
function siden(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return 'sidst kontakt ukendt';
  if (seconds < 60) return 'sidst kontakt for under 1 min. siden';
  if (seconds < 3600) return `sidst kontakt for ${Math.round(seconds / 60)} min. siden`;
  if (seconds < 86400) return `sidst kontakt for ${Math.round(seconds / 3600)} timer siden`;
  return `sidst kontakt for ${Math.round(seconds / 86400)} dage siden`;
}
function tidspunkt(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return 'Ukendt';
  const date = new Date(seconds * 1000);
  return Number.isFinite(date.getTime()) ? date.toLocaleString('da-DK', {dateStyle:'short', timeStyle:'short'}) : 'Ukendt';
}
function statusBadge(text, state) {
  return node('span', text, `system-badge ${state || 'unknown'}`);
}
function table(headers, rows, emptyText, captionText) {
  if (!rows.length) return node('p', emptyText, 'system-empty');
  const wrap = node('div', undefined, 'system-table-wrap');
  const tableNode = node('table', undefined, 'system-table');
  const caption = node('caption', captionText || emptyText, 'skjult');
  tableNode.append(caption);
  const head = node('thead');
  const headRow = node('tr');
  headers.forEach(header => headRow.append(node('th', header, undefined)));
  head.append(headRow);
  const body = node('tbody');
  rows.forEach(row => body.append(row));
  tableNode.append(head, body);
  wrap.append(tableNode);
  return wrap;
}
function cell(primary, secondary) {
  const td = node('td');
  td.append(node('strong', primary));
  if (secondary) td.append(node('span', secondary, 'system-secondary'));
  return td;
}
function certificateStatusLabel(status) {
  return ({valid:'Gyldigt', expiring_30_days:'Udløber inden 30 dage', expired:'Udløbet',
    not_yet_valid:'Ikke gyldigt endnu', unavailable:'Ikke tilgængeligt'})[status] || 'Ukendt';
}
function certificateDate(value) {
  if (typeof value !== 'string') return 'dato ukendt';
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleDateString('da-DK') : 'dato ukendt';
}
function renderCertificates(tls) {
  const block = node('div', undefined, 'system-tls');
  const heading = node('strong', 'HTTPS-certifikater');
  block.append(heading);
  const certificate = tls && typeof tls.certificate === 'object' ? tls.certificate : {};
  const ca = tls && typeof tls.ca === 'object' ? tls.ca : {};
  const rows = [
    ['Servercertifikat', certificate],
    ['Lokal CA', ca]
  ];
  rows.forEach(([label, value]) => {
    const row = node('div', undefined, 'system-tls-row');
    const status = typeof value.status === 'string' ? value.status : 'unavailable';
    row.append(node('span', label, 'system-server-label'));
    row.append(statusBadge(certificateStatusLabel(status), status));
    const days = typeof value.days_remaining === 'number' ? ` · ${value.days_remaining} dage tilbage` : '';
    row.append(node('span', `${certificateDate(value.not_after)}${days}`, 'system-secondary'));
    block.append(row);
  });
  if (ca.status === 'expired' || ca.status === 'not_yet_valid') {
    block.append(node('p', 'CA-certifikatet er uden for sin gyldighed. Skole-IT skal kontrollere serverens ur og planlægge skolens certifikattillid; menu 12 udskifter ikke CA.', 'system-tls-action'));
  } else if (certificate.status === 'expiring_30_days' || certificate.status === 'expired' ||
      ca.status === 'expiring_30_days') {
    block.append(node('p', 'Kontakt skoleadministrationen via menu 12 for at forny certifikatet.', 'system-tls-action'));
  }
  if (ca.status === 'unavailable') {
    block.append(node('p', 'CA-certifikatet kunne ikke læses. Kontrollér opsætningen; behold skolens eksisterende signaturnøgle ved fornyelse.', 'system-secondary'));
  }
  return block;
}
function computerRow(computer) {
  const navn = unknown(computer.navn);
  const ip = unknown(computer.ip);
  const online = computer.online === true;
  const onlineState = online ? 'online' : computer.online === false ? 'offline' : 'unknown';
  const softwareVersion = computer.software_version === null || computer.software_version === undefined || computer.software_version === '' ? 'Ukendt' : String(computer.software_version);
  const versionStatus = softwareVersion === 'Ukendt' ? 'unknown' : (['current', 'different', 'unknown'].includes(computer.version_status) ? computer.version_status : 'unknown');
  const row = node('tr');
  row.append(cell(navn, ip));
  const kontakt = node('td');
  kontakt.append(statusBadge(online ? 'Online' : computer.online === false ? 'Offline' : 'Online-status ukendt', onlineState));
  kontakt.append(node('span', siden(computer.sidst_set_sekunder), 'system-secondary'));
  row.append(kontakt);
  const version = node('td');
  const versionLabel = online
    ? (versionStatus === 'current' ? 'Aktuel' : versionStatus === 'different' ? 'Afviger' : 'Versionsstatus ukendt')
    : (versionStatus === 'current' ? 'Senest kendt: aktuel' : versionStatus === 'different' ? 'Senest kendt: afviger' : 'Version ukendt');
  const versionClass = online ? versionStatus : (versionStatus === 'unknown' ? 'unknown' : 'offline');
  version.append(statusBadge(versionLabel, versionClass));
  version.append(node('span', softwareVersion, 'system-secondary'));
  row.append(version);
  const detail = node('td');
  detail.append(node('span', booleanLabel(computer.registered, 'Tilmeldt', 'Ikke tilmeldt'), 'system-secondary'));
  detail.append(node('span', booleanLabel(computer.bekraeftet, 'Regler bekræftet', 'Regler ikke bekræftet'), 'system-secondary'));
  row.append(detail);
  return row;
}
function printerRow(printer) {
  const row = node('tr');
  row.append(cell(unknown(printer.name), unknown(printer.model)));
  const connection = node('td');
  connection.append(statusBadge(printer.online === true ? 'Online' : printer.online === false ? 'Offline' : 'Online-status ukendt', printer.online === true ? 'online' : printer.online === false ? 'offline' : 'unknown'));
  connection.append(node('span', booleanLabel(printer.enabled, 'Aktiveret', 'Deaktiveret'), 'system-secondary'));
  row.append(connection);
  const ready = node('td');
  ready.append(statusBadge(printer.ready === true ? 'Klar' : printer.ready === false ? 'Ikke klar' : 'Klarhedsstatus ukendt', printer.ready === true ? 'ready' : printer.ready === false ? 'different' : 'unknown'));
  ready.append(node('span', booleanLabel(printer.bed_clear, 'Plade meldt fri', 'Plade ikke meldt fri'), 'system-secondary'));
  row.append(ready);
  const errorLabel = printer.error === true ? 'Fejlmeldt' : printer.error === false ? 'Ingen fejlmelding' : 'Fejlstatus ukendt';
  const errorDetail = printer.error === true ? 'Seneste fejl fra serveren' : printer.error === false ? 'Ingen fejlmelding fra serveren' : 'Serveren gav ingen fejlstatus';
  row.append(cell(errorLabel, errorDetail));
  return row;
}
function renderServer(server, tls) {
  const target = systemEl('system-server');
  target.replaceChildren();
  const values = [
    ['Serverversion', server && server.release_id],
    ['Forventet elevversion', server && server.expected_client_release]
  ];
  values.forEach(([label, value]) => {
    const item = node('div', undefined, 'system-server-item');
    item.append(node('span', label, 'system-server-label'));
    const valueNode = node('strong', unknown(value), `system-server-value${server.stale ? ' system-ukendt' : ''}`);
    if (server.stale) item.append(node('span', 'Sidst kendt', 'system-secondary'));
    if (value === null || value === undefined || value === '') valueNode.classList.add('system-ukendt');
    item.append(valueNode);
    target.append(item);
  });
  target.append(renderCertificates(tls));
}
function renderTotals(computers, printers, printerStatus, computerStatus = 'ok') {
  const target = systemEl('system-tal');
  target.replaceChildren();
  const printerKnown = printerStatus === 'ok';
  const values = [
    ['Elevcomputere', computerStatus === 'ok' ? computers.length : 'Ukendt'],
    ['Online nu', computerStatus === 'ok' ? computers.filter(item => item.online === true).length : 'Ukendt'],
    ['Printere', printerKnown ? printers.length : 'Ukendt']
  ];
  values.forEach(([label, value]) => {
    const item = node('div', undefined, 'system-tal-item');
    item.append(node('dt', label));
    item.append(node('dd', value));
    target.append(item);
  });
}
function renderLists(data) {
  const computerStatus = data?.computers_status === 'ok' && Array.isArray(data?.computers) ? 'ok' : 'unknown';
  const computers = computerStatus === 'ok' && Array.isArray(data?.computers) ? data.computers : [];
  const printerData = data?.printers && typeof data.printers === 'object' ? data.printers : null;
  const printerStatus = printerData?.status === 'ok' ? 'ok' : 'unknown';
  const printers = printerStatus === 'ok' && Array.isArray(printerData.items) ? printerData.items : [];
  renderServer(data?.server && typeof data.server === 'object' ? data.server : {}, data?.tls);
  renderTotals(computers, printers, printerStatus, computerStatus);
  systemEl('system-computer-count').textContent = computerStatus === 'ok' ? `${computers.length} registreret` : 'status ukendt';
  systemEl('system-printer-count').textContent = printerStatus === 'ok' ? `${printers.length} modtaget` : 'status ukendt';
  const computerEmpty = computerStatus === 'ok' ? 'Ingen elevcomputerdata modtaget.' : 'Elevcomputerstatus er ukendt. Der vises ingen antaget tomt inventar.';
  const computerTable = table(['Navn og IP', 'Kontakt', 'Software', 'Tilmelding'], computers.map(computerRow), computerEmpty, 'Elevcomputerstatus');
  systemEl('system-computere').replaceChildren(computerTable);
  const printerEmpty = printerStatus === 'ok' ? 'Ingen printere er registreret i det aktuelle overblik.' : 'Printerstatus er ukendt. Der vises ingen gamle eller antagede printerdata.';
  const printerTable = table(['Printer og model', 'Kontakt', 'Klarhed', 'Fejl'], printers.map(printerRow), printerEmpty, 'Printerstatus');
  systemEl('system-printere').replaceChildren(printerTable);
  return {computerCount: computerStatus === 'ok' ? computers.length : null, printerCount: printerStatus === 'ok' ? printers.length : null};
}
function renderNoCurrentData() {
  renderServer(systemLastServer ? {...systemLastServer, stale: true} : {stale: true}, null);
  renderTotals([], [], 'unknown', 'unknown');
  systemEl('system-computer-count').textContent = 'ingen aktuelle data';
  systemEl('system-printer-count').textContent = 'ingen aktuelle data';
  systemEl('system-computere').replaceChildren(node('p', 'Ingen aktuelle elevcomputerdata efter opdateringsfejl.', 'system-empty'));
  systemEl('system-printere').replaceChildren(node('p', 'Ingen aktuelle printerdata efter opdateringsfejl.', 'system-empty'));
}
function showStatus(text, state) {
  const status = systemEl('system-status');
  status.textContent = text;
  status.className = `system-status${state ? ` ${state}` : ''}`;
}
async function fetchSystem() {
  if (!systemAktiv || systemRequest) return systemRequest;
  const generation = systemGeneration;
  systemController = new AbortController();
  systemAbortTimer = setTimeout(() => systemController?.abort(), 8000);
  systemRequest = (async () => {
    try {
      const response = await fetch('/api/laerer/system', {cache:'no-store', redirect:'error', signal:systemController.signal});
      let data = {};
      try { data = await response.json(); } catch { /* status below remains authoritative */ }
      if (!response.ok) {
        const error = new Error(data.error || 'Serverens systemoverblik kunne ikke hentes.');
        error.status = response.status;
        throw error;
      }
      if (!systemAktiv || generation !== systemGeneration) return;
      systemLastServer = data?.server && typeof data.server === 'object' ? {...data.server} : {};
      const result = renderLists(data);
      const printerData = data?.printers;
      const checked = printerData?.status === 'ok' ? `Printerstatus kontrolleret ${tidspunkt(printerData.checked_at)}.` : 'Printerstatus ukendt.';
      showStatus(`Sidst opdateret kl. ${new Date().toLocaleTimeString('da-DK', {hour:'2-digit', minute:'2-digit'})} · ${checked}`, 'ok');
      return result;
    } catch (error) {
      if (!systemAktiv || generation !== systemGeneration) return;
      if (error.name === 'AbortError') {
        renderNoCurrentData();
        showStatus('Kunne ikke opdatere overblikket: serveren svarede ikke i tide. Ingen aktuelle data vises.', 'fejl');
        return;
      }
      if (error.status === 401) { if (systemAuthFailure) systemAuthFailure(); return; }
      renderNoCurrentData();
      showStatus(`Kunne ikke opdatere overblikket: ${error.message} Ingen aktuelle data vises.`, 'fejl');
    } finally {
      clearTimeout(systemAbortTimer);
      systemAbortTimer = null;
      systemController = null;
      systemRequest = null;
      if (systemAktiv && generation === systemGeneration) {
        clearTimeout(systemTimer);
        systemTimer = setTimeout(fetchSystem, 17000);
      }
    }
  })();
  return systemRequest;
}
function startSystem(generation) {
  stopSystem();
  systemAktiv = true;
  if (systemRequest) return systemRequest.then(() => fetchSystem(), () => fetchSystem());
  return fetchSystem();
}
function stopSystem() {
  systemAktiv = false;
  systemGeneration += 1;
  clearTimeout(systemTimer);
  systemTimer = null;
  clearTimeout(systemAbortTimer);
  systemAbortTimer = null;
  if (systemController) systemController.abort();
  /* A late fetch may finish, but its generation check discards the result. */
}
function refreshSystem() {
  if (!systemAktiv || systemRequest) return systemRequest;
  clearTimeout(systemTimer);
  return fetchSystem();
}
systemEl('system-opdater').addEventListener('click', () => { void refreshSystem(); });
window.SystemOverview = {
  start: startSystem,
  stop: stopSystem,
  refresh: refreshSystem,
  onAuthFailure(callback) { systemAuthFailure = callback; }
};
})();
