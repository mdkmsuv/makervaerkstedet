'use strict';
const $ = id => document.getElementById(id);
const VIS = ['indlaeser', 'login', 'opsaet', 'ikke-valgt', 'panel'];
const APP_NAVN = {};
let timer = null, travl = false, generation = 0, henter = null, appsNu = [], appNoegle = '';
const IKONER = {
  '3dprint': '<svg width="34" height="34" viewBox="0 0 34 34" fill="none" stroke="#c62b2f" stroke-width="2.5" stroke-linejoin="round"><rect x="4" y="4" width="26" height="26" rx="3"/><path d="M4 10h26M13 10v6h8v-6M13 25h8M17 16v6"/></svg>',
  laser: '<svg width="34" height="34" viewBox="0 0 34 34" fill="none" stroke="#c62b2f" stroke-width="2.5" stroke-linecap="round"><path d="M17 3v10"/><path d="m17 13-3 5h6z" fill="#c62b2f"/><path d="M5 28h24M9 24l4-4 4 4 4-4 4 4"/></svg>',
  folie: '<svg width="34" height="34" viewBox="0 0 34 34" fill="none" stroke="#c62b2f" stroke-width="2.5" stroke-linejoin="round"><rect x="5" y="6" width="24" height="22" rx="3"/><path d="M29 18 19 28M10 13l3 4 3-4 3 4 3-4"/></svg>',
  internet: '<svg width="34" height="34" viewBox="0 0 34 34" fill="none" stroke="#c62b2f" stroke-width="2.5"><circle cx="17" cy="17" r="12"/><path d="M5 17h24M17 5c-4 4-5 8-5 12s1 8 5 12M17 5c4 4 5 8 5 12s-1 8-5 12"/></svg>',
};

Object.assign(IKONER, {
  microbit: '<svg viewBox="0 0 34 34" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="8" width="28" height="20" rx="3"/><path d="M7 14h6m-3-3v6M17 14h2m3 0h2m-7 5h2m3 0h2M8 28v4m9-4v4m9-4v4"/></svg>',
  datatraener: '<svg viewBox="0 0 34 34" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4v26h26M10 25V17h4v8m6 0V10h4v15m5 0V5"/></svg>',
  skolegpt: '<svg viewBox="0 0 34 34" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 4h28v21H18L9 31v-6H3zM9 11h16M9 17h12"/></svg>',
  scratch: '<svg viewBox="0 0 34 34" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h10v4h8V3h10v10h-4v8h4v10H21v-4h-8v4H3V21h4v-8H3z"/></svg>'
});
if (window.SystemOverview) {
  window.SystemOverview.onAuthFailure(() => {
    clearTimeout(timer);
    window.SystemOverview.stop();
    generation++;
    vis('login');
    $('login-fejl').textContent = 'Log ind igen for at fortsætte.';
  });
}
function vis(id) {
  if (window.UpdateAdmin) {
    if (id === 'panel') window.UpdateAdmin.start(); else window.UpdateAdmin.stop();
  }
  for (const v of VIS) $(v).hidden = v !== id;
  $('log-ud').hidden = id !== 'panel';
}
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, t => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[t])); }
function siden(s) {
  if (s === null || s === undefined) return 'aldrig set';
  if (s < 60) return 'for lidt siden';
  if (s < 3600) return `for ${Math.round(s / 60)} min. siden`;
  if (s < 86400) return `for ${Math.round(s / 3600)} timer siden`;
  return `for ${Math.round(s / 86400)} dage siden`;
}
async function api(sti, data) {
  const c = new AbortController(), t = setTimeout(() => c.abort(), 8000);
  try {
    const svar = await fetch(sti, {cache:'no-store', redirect:'error', signal:c.signal,
      ...(data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})});
    let krop = {};
    try { krop = await svar.json(); } catch { /* status remains authoritative */ }
    if (!svar.ok) { const e = new Error(krop.error || 'Noget gik galt. Prøv igen.'); e.status = svar.status; throw e; }
    return krop;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('Serveren svarede ikke i tide. Kontrollér status, før du prøver igen.');
    throw e;
  } finally { clearTimeout(t); }
}
function laererLink(url) {
  if (!url) return '';
  try {
    const u = new URL(url);
    const loopback = h => h === 'localhost' || h.endsWith('.localhost') || h === '[::1]' || /^127\./.test(h);
    if (loopback(u.hostname) && !loopback(location.hostname)) return '';
    return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password ? u.href : '';
  }
  catch { return ''; }
}
function appKort(a) {
  const brug = a.i_brug ? `${a.i_brug} ${a.i_brug === 1 ? 'computer' : 'computere'} i værkstedet` : 'Ingen computere i værkstedet';
  const link = laererLink(a.laererside);
  const frist = typeof a.lukket_indtil === 'number' ? new Date(a.lukket_indtil * 1000) : null;
  const note = a.type === 'browser' ? (a.aaben ? 'Luk igen efter brug. Lukkes også ved servergenstart.' : 'Forbliver lukket, indtil en lærer åbner.') :
    !a.aaben && frist && Number.isFinite(frist.getTime()) ? `Åbner igen kl. ${frist.toLocaleTimeString('da-DK', {hour:'2-digit',minute:'2-digit'})}` : 'Lukning varer højst 2 timer';
  const klar = a.klar !== false;
  return `<article class="app ${a.aaben ? 'aaben' : 'lukket'}">
    <div class="app-top"><div class="ikon" aria-hidden="true">${IKONER[a.id] || ''}</div>
      <div><h3>${esc(a.navn)}</h3><p class="beskrivelse">${esc(a.beskrivelse)}</p></div></div>
    <button class="kontakt" type="button" role="switch" aria-checked="${a.aaben}" data-app="${esc(a.id)}"
      aria-label="${esc(a.navn)}: ${a.aaben ? 'åben for eleverne' : 'lukket for eleverne'}">
      <span class="kontakt-knap" aria-hidden="true"></span><span>${a.aaben ? 'Åben for eleverne' : 'Lukket'}</span></button>
    <p class="note">${esc(note)}</p>
    ${!klar ? '<p class="ikke-klar">Afventer lokal installation. Eleverne kan ikke starte endnu.</p>' : ''}
    <p class="brug">${esc(brug)}</p>
    ${link ? `<a class="laererside" href="${esc(link)}" target="_blank" rel="noopener noreferrer">Værkstedets lærerside ↗</a>` : ''}
    ${a.laererside_note ? `<p class="note">${esc(a.laererside_note)}</p>` : ''}
  </article>`;
}
function tegnApps(apps) {
  appsNu = apps;
  apps.forEach(a => { APP_NAVN[a.id] = a.navn; });
  const key = JSON.stringify(apps);
  if (key === appNoegle) return;
  const fokus = document.activeElement?.dataset.app;
  $('apps').innerHTML = apps.filter(a => a.type !== 'browser').map(appKort).join('');
  $('internet').innerHTML = apps.filter(a => a.type === 'browser').map(appKort).join('');
  appNoegle = key;
  if (fokus) Array.from(document.querySelectorAll('[data-app]')).find(e => e.dataset.app === fokus)?.focus({preventScroll:true});
}
function tegnComputere(computere) {
  if (!computere.length) { $('computere').innerHTML = '<p class="tom">Ingen elevcomputere har meldt sig endnu. De vises her, når de er tændt og forbundet.</p>'; return; }
  $('computere').innerHTML = computere.map(c => {
    const navn = c.navn.replace(/^vaerksted/i, 'Værksted');
    const hvor = c.visning === 'start' ? 'på startsiden' : c.visning ? `i ${esc(APP_NAVN[c.visning] || c.visning)}` : 'forbundet';
    const status = c.online ? `Tændt · ${hvor}` : `Slukket eller uden net · ${siden(c.sidst_set_sekunder)}`;
    const bekraeft = !c.online ? '' : c.bekraeftet ? '<div class="c-bekraeft">✓ Regler bekræftet</div>' : '<div class="c-bekraeft venter">Afventer nye regler</div>';
    return `<div class="computer ${c.online ? 'online' : 'offline'}"><div class="c-navn">${esc(navn)}</div>
      <div class="c-status"><span class="prik" aria-hidden="true"></span>${status}</div>${bekraeft}
      ${c.usb ? `<div class="c-usb">USB-nøgle: ${esc(c.usb)}</div>` : ''}</div>`;
  }).join('');
}
function knapper(disabled) { document.querySelectorAll('.kontakt, #aabn-alle, #luk-alle, #pause-start, #pause-resume').forEach(k => { k.disabled = disabled; }); }
function pauseData(raw) {
  if (raw && raw.pause && typeof raw.pause === 'object') return raw.pause;
  return raw && typeof raw === 'object' ? raw : null;
}
function tegnPause(raw, computere) {
  const pause = pauseData(raw);
  const status = $('pause-status');
  if (!pause || typeof pause.paused !== 'boolean') {
    $('pause-start').disabled = true; $('pause-resume').disabled = true;
    status.textContent = 'Skærmpause bliver tilgængelig, når serveren er opdateret.';
    status.className = 'pause-status pause-ukendt';
    return;
  }
  const start = $('pause-start'), resume = $('pause-resume');
  start.disabled = false; resume.disabled = false;
  if (pause.paused) {
    const until = typeof pause.until === 'number' ? new Date(pause.until * 1000) : null;
    const tidspunkt = until && Number.isFinite(until.getTime()) ? ` indtil kl. ${until.toLocaleTimeString('da-DK', {hour:'2-digit', minute:'2-digit'})}` : '';
    const ack = Array.isArray(computere) ? computere.filter(c => c.online && c.pause_ack && c.pause_ack.version === pause.version && c.pause_ack.shown).length : 0;
    const online = Array.isArray(computere) ? computere.filter(c => c.online).length : 0;
    status.textContent = `Aktiv${tidspunkt} · ${ack} af ${online} online ${online === 1 ? 'computer viser pausen' : 'computere viser pausen'}.`;
    status.className = 'pause-status pause-aktiv';
  } else {
    status.textContent = 'Ingen skærmpause. Eleverne kan arbejde videre.';
    status.className = 'pause-status pause-ikke-aktiv';
  }
}
async function skiftPause(paused) {
  if (travl) return;
  travl = true; knapper(true); $('handling-fejl').textContent = '';
  try {
    const data = {paused};
    if (paused) {
      const minutter = Number($('pause-minutter').value);
      if (!Number.isInteger(minutter) || minutter < 1 || minutter > 120) throw new Error('Vælg mellem 1 og 120 minutter.');
      data.expires_in = minutter * 60;
    }
    await api('/api/laerer/pause', data);
  } catch (e) {
    $('handling-fejl').textContent = e.message;
  } finally {
    travl = false;
    if (!$('panel').hidden) await hent();
  }
}
async function hent() {
  if (henter) return henter;
  const g = generation;
  henter = (async () => {
    try {
      const d = await api('/api/laerer/oversigt');
      if (g !== generation || $('panel').hidden) return;
      if (!travl) { tegnApps(d.apps); knapper(false); }
      tegnComputere(d.computere);
      tegnPause(d.pause, d.computere);
      const online = d.computere.filter(c => c.online).length;
      const klar = d.computere.filter(c => c.online && c.bekraeftet).length;
      $('forbindelse').className = 'forbindelse';
      $('forbindelse').textContent = `${online} ${online === 1 ? 'computer' : 'computere'} forbundet · ${klar} har bekræftet reglerne · Opdateret kl. ${new Date().toLocaleTimeString('da-DK')}`;
    } catch (e) {
      if (g !== generation) return;
      if (e.status === 401) {
        clearTimeout(timer);
        if (window.SystemOverview) window.SystemOverview.stop();
        generation++;
        vis('login');
        $('login-fejl').textContent = 'Log ind igen for at fortsætte.';
        return;
      }
      knapper(true);
      $('forbindelse').className = 'forbindelse fejl';
      $('forbindelse').textContent = 'Ingen forbindelse. Knapperne aktiveres igen, når serveren svarer.';
    }
  })();
  try { await henter; } finally { henter = null; }
}
async function poll(g) {
  if (g !== generation || $('panel').hidden) return;
  if (!travl) await hent();
  if (g === generation) timer = setTimeout(() => poll(g), 3000);
}
function bekraeftLukning(id) {
  const antal = appsNu.filter(a => (!id ? a.type !== 'browser' : a.id === id) && a.aaben).reduce((n, a) => n + a.i_brug, 0);
  if (!antal) return Promise.resolve(true);
  const dialog = $('luk-dialog');
  $('luk-besked').textContent = `${antal} ${antal === 1 ? 'computer bruger' : 'computere bruger'} værkstedet lige nu. Eleverne sendes til startsiden. Bed dem gemme først; arbejde i en åben fane kan gå tabt.`;
  dialog.returnValue = '';
  return new Promise(resolve => { dialog.addEventListener('close', () => resolve(dialog.returnValue === 'luk'), {once:true}); dialog.showModal(); });
}
async function skift(id, aaben) {
  if (travl) return;
  travl = true; knapper(true);
  try {
    if (!aaben && !(await bekraeftLukning(id))) return;
    $('handling-fejl').textContent = '';
    if (henter) await henter;
    await api(id ? '/api/laerer/app' : '/api/laerer/alle', id ? {id, aaben} : {aaben});
  } catch (e) {
    $('handling-fejl').textContent = e.message;
    if (e.status === 401) {
      if (window.SystemOverview) window.SystemOverview.stop();
      generation++;
      vis('login');
    }
  } finally { travl = false; if (!$('panel').hidden) await hent(); }
}
async function start() {
  clearTimeout(timer);
  if (window.SystemOverview) window.SystemOverview.stop();
  const g = ++generation;
  try {
    const s = await api('/api/laerer/session');
    if (g !== generation) return;
    if (s.logget_ind) {
      vis('panel');
      appNoegle = '';
      if (window.SystemOverview) void window.SystemOverview.start(g);
      await poll(g);
    }
    else if (!s.kode_valgt) vis(s.lokal ? 'opsaet' : 'ikke-valgt');
    else { vis('login'); $('login-kode').focus(); }
  } catch { vis('indlaeser'); $('indlaeser').textContent = 'Serveren svarer ikke lige nu. Genindlæs siden om lidt.'; }
}
for (const prefix of ['login', 'opsaet']) $(prefix + '-form').addEventListener('submit', async e => {
  e.preventDefault();
  const button = e.target.querySelector('button'); button.disabled = true;
  $(prefix + '-fejl').textContent = '';
  try {
    const data = {kode:$(prefix + '-kode').value};
    if (prefix === 'opsaet') data.kode_igen = $('opsaet-kode-igen').value;
    await api('/api/laerer/' + prefix, data);
    $(prefix + '-kode').value = '';
    if (prefix === 'opsaet') $('opsaet-kode-igen').value = '';
    await start();
  } catch (e) { $(prefix + '-fejl').textContent = e.message; }
  finally { button.disabled = false; }
});
for (const id of ['apps', 'internet']) $(id).addEventListener('click', e => {
  const k = e.target.closest('.kontakt');
  if (k) skift(k.dataset.app, k.getAttribute('aria-checked') !== 'true');
});
$('aabn-alle').addEventListener('click', () => skift(null, true));
$('luk-alle').addEventListener('click', () => skift(null, false));
$('pause-start').addEventListener('click', () => skiftPause(true));
$('pause-resume').addEventListener('click', () => skiftPause(false));
$('log-ud').addEventListener('click', async () => {
  try { await api('/api/laerer/logout', {}); await start(); }
  catch { $('handling-fejl').textContent = 'Kunne ikke logge ud. Prøv igen, når forbindelsen er tilbage.'; }
});
start();
