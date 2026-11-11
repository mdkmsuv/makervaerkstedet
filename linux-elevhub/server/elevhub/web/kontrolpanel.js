/* Teacher panel using the currently deployed teacher and update APIs. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let active = false, generation = 0, timer, loading = false, acting = false;
  const el = (tag, text, cls) => { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; };
  async function request(path, body) {
    const c = new AbortController(), timeout = setTimeout(() => c.abort(), 10000);
    try {
      const r = await fetch(path, {cache:'no-store', credentials:'same-origin', redirect:'error', signal:c.signal,
        ...(body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})});
      let d = {}; try { d = await r.json(); } catch {}
      if (!r.ok) { const e = new Error(typeof d.error === 'string' ? d.error : 'Serveren kunne ikke udføre handlingen.'); e.status = r.status; throw e; }
      return d;
    } finally { clearTimeout(timeout); }
  }
  function view() {
    const hash = location.hash.slice(1), selected = ['forside','apps','computere','printere','teknik'].includes(hash) ? hash : 'forside';
    document.querySelectorAll('[data-view]').forEach(n => n.hidden = n.dataset.view !== selected);
    document.querySelectorAll('#nav [data-v]').forEach(n => { if (n.dataset.v === selected) n.setAttribute('aria-current','page'); else n.removeAttribute('aria-current'); });
  }
  function signedIn(on) {
    active = on; generation++; clearTimeout(timer); if (!on) window.UpdateAdmin.stop();
    $('panel').hidden = !on; $('login').hidden = on; $('setup').hidden = true; $('logout').hidden = !on; $('refresh').hidden = !on; $('nav').hidden = !on;
    $('app-shell').classList.toggle('signed-in',on); $('status').textContent = on ? '' : 'Log ind for at administrere værkstedet.';
    if (on) { view(); window.UpdateAdmin.start(); void poll(generation); }
  }
  function render(d) {
    if (!Array.isArray(d.computere)) throw Error('Ugyldigt svar fra serveren.');
    const appList=$('apps-list'); appList.replaceChildren();
    if (Array.isArray(d.apps)) for (const app of d.apps) {
      const card=el('article','', 'panel'); card.append(el('h3',app.navn || app.id),el('p',app.aaben ? 'Åben for eleverne' : 'Lukket','small muted'));
      const b=el('button',app.aaben ? 'Luk' : 'Åbn','btn'); b.type='button'; b.disabled=acting;
      b.addEventListener('click',()=>changeApp(app)); card.append(b); appList.append(card);
    } else appList.append(el('p','Status for værksteder er ikke tilgængelig.'));
    const paused=typeof d.pause?.paused === 'boolean' ? d.pause.paused : null;
    $('pause-state').textContent=paused===null?'Skærmpausens status er ukendt.':paused?'Skærmpause er aktiv.':'Ingen skærmpause er aktiv.';
    $('pause-start').disabled=acting || paused!==false; $('pause-end').disabled=acting || paused!==true;
    const computers = d.computere, online = computers.filter(c => c.online).length;
    $('overview-date').textContent = new Date().toLocaleDateString('da-DK',{weekday:'long',day:'numeric',month:'long'});
    $('overview-lead').textContent = `${computers.length} registrerede elevcomputere · ${online} online.`;
    $('quick-computers').textContent = `${online} online · ${computers.length-online} uden forbindelse`;
    $('quick-updates').textContent = 'Se status under Teknik'; $('it-summary').textContent = 'Oplysninger til IT-ansvarlig';
    $('attention').replaceChildren(el('li','Se computerne og maskinadministrationen for aktuelle oplysninger.'));
    $('it-attention').replaceChildren(el('li','Opdateringsstatus findes under Teknik.'));
    const room=$('room'), list=$('computer-list'); room.replaceChildren(); list.replaceChildren();
    for (const c of computers) {
      const name = typeof c.navn === 'string' ? c.navn : 'Computer', status = c.online ? 'Online' : 'Slukket eller uden forbindelse';
      const seat = el('a','',`seat ${c.online ? 'on' : ''}`); seat.href='#computere'; seat.append(el('span',name,'n'),el('span',status,'s')); room.append(seat);
      const card=el('article','',`pc ${c.online ? '' : 'offline'}`); card.append(el('h3',name),el('p',status,`pill ${c.online?'ok':'off'}`));
      if (c.online) card.append(el('p',c.bekraeftet ? 'Regler bekræftet' : 'Afventer bekræftelse af regler','small muted'));
      list.append(card);
    }
    if (!computers.length) { room.append(el('p','Ingen elevcomputere har meldt sig endnu.','small muted')); list.append(el('p','Ingen elevcomputere har meldt sig endnu.','small muted')); }
    $('updated-time').textContent = `Opdateret ${new Date().toLocaleTimeString('da-DK')}`;
  }
  async function changeApp(app) {
    if (acting) return;
    if (app.aaben && app.i_brug && !confirm(`${app.i_brug} computer(e) bruger værkstedet. Eleverne sendes til startsiden og kan miste arbejde, som ikke er gemt. Luk alligevel?`)) return;
    acting=true; document.querySelectorAll('#apps-list button, #pause-start, #pause-end').forEach(b=>b.disabled=true);
    try { await request('/api/laerer/app',{id:app.id,aaben:!app.aaben}); $('error').textContent=''; } catch(e) { $('error').textContent='Handlingen kunne ikke udføres: '+e.message; } finally { acting=false; await refresh(); }
  }
  async function changePause(paused) {
    if (acting) return; acting=true; $('pause-start').disabled=true; $('pause-end').disabled=true;
    try { await request('/api/laerer/pause',paused?{paused:true,message:$('pause-message').value,expires_in:3600}:{paused:false}); $('error').textContent=''; } catch(e) { $('error').textContent='Skærmpausen kunne ikke ændres: '+e.message; } finally { acting=false; await refresh(); }
  }
  function renderPrinters(data) {
    const summary=$('printer-summary'); summary.replaceChildren();
    if (data?.sources?.printers !== 'ok' || !Array.isArray(data.inventory)) {
      $('printer-count').textContent='Ukendt'; summary.append(el('p','Printerstatus kunne ikke hentes. Åbn maskinadministrationen for at kontrollere forbindelsen.','small muted')); return;
    }
    const rows=data.inventory.filter(p=>p.adapter==='bambu' || p.kind==='3d_print');
    $('printer-count').textContent=`${rows.length} registreret`;
    if (!rows.length) { summary.append(el('p','Ingen printere er registreret.','small muted')); return; }
    const names={ready:'Klar',bed:'Pladen skal kontrolleres',printing:'Printer',preparing:'Klargør',attention:'Kræver kontrol',disabled:'Deaktiveret',offline:'Offline',unknown:'Ukendt'};
    for (const p of rows) {
      const when=typeof p.observed_at==='number' ? p.observed_at*1000 : Date.parse(p.observed_at);
      const age=Date.now()-when, fresh=Number.isFinite(when) && age>=-60000 && age<=120000;
      const card=el('article','', 'pr'); card.append(el('h3',p.name || 'Printer'));
      card.append(el('p',fresh ? (names[p.status] || 'Ukendt') : 'Status ukendt eller forældet','small muted'));
      card.append(el('p',Number.isFinite(when) ? `Senest observeret: ${new Date(when).toLocaleString('da-DK')}` : 'Intet observationstidspunkt','small muted'));
      summary.append(card);
    }
  }
  async function refreshPrinters(g) {
    try { const data=await request('/api/makerspace/state'); if(active && g===generation) renderPrinters(data); }
    catch { if(active && g===generation) renderPrinters(null); }
  }
  async function refresh() {
    if (!active || loading) return; loading=true; const g=generation; $('refresh').disabled=true;
    try { const [d] = await Promise.all([request('/api/laerer/oversigt'), refreshPrinters(g)]); if(active && g===generation) { render(d); $('status').textContent=''; } }
    catch(e) { if(g===generation) { if(e.status===401) { signedIn(false); $('status').textContent='Log ind igen for at fortsætte.'; } else $('status').textContent='Status kunne ikke hentes. De viste oplysninger kan være forældede.'; } }
    finally { loading=false; $('refresh').disabled=false; }
  }
  async function poll(g) { await refresh(); if(active && g===generation) timer=setTimeout(()=>poll(g),17000); }
  async function start() {
    try { const s=await request('/api/laerer/session'); if(s.logget_ind) signedIn(true); else if(!s.kode_valgt) { signedIn(false); $('login').hidden=true; if(s.lokal) { $('setup').hidden=false; $('status').textContent='Vælg lærerkode på serveren.'; } else $('status').textContent='Lærerkoden skal først vælges på serveren.'; } else signedIn(false); }
    catch { $('status').textContent='Serveren svarer ikke. Genindlæs siden, og prøv igen.'; }
  }
  $('login-form').addEventListener('submit',async e=> { e.preventDefault(); const b=$('login-submit'); b.disabled=true; try { await request('/api/laerer/login',{kode:$('password').value}); $('password').value=''; $('error').textContent=''; await start(); } catch(err) { $('error').textContent=err.message; } finally { b.disabled=false; } });
  $('setup-form').addEventListener('submit',async e=> { e.preventDefault(); const b=e.target.querySelector('button'); b.disabled=true; try { await request('/api/laerer/opsaet',{kode:$('setup-code').value,kode_igen:$('setup-confirm').value}); $('setup-code').value=''; $('setup-confirm').value=''; $('error').textContent=''; await start(); } catch(err) { $('error').textContent=err.message; } finally { b.disabled=false; } });
  $('logout').addEventListener('click',async()=>{try { await request('/api/laerer/logout',{}); signedIn(false); } catch(e) { $('error').textContent='Kunne ikke logge ud. Prøv igen.'; }});
  $('pause-start').addEventListener('click',()=>void changePause(true));
  $('pause-end').addEventListener('click',()=>void changePause(false));
  $('refresh').addEventListener('click',()=>void refresh());
  $('help-toggle').addEventListener('click',()=>{ $('help').hidden=!$('help').hidden; $('help-toggle').setAttribute('aria-expanded',String(!$('help').hidden)); });
  window.addEventListener('hashchange',view);
  $('printer-count').textContent='Ukendt'; $('printer-summary').replaceChildren(el('p','Åbn maskinadministrationen for aktuel printerstatus.','small muted'));
  void start();
})();

// Opened from the laser computer's start page (#forside=<port>): show a way back,
// so the laser station, 3D print and administration feel like one entrance.
(() => {
  'use strict';
  try {
    const opened = /^#forside=(\d{2,5})$/.exec(location.hash);
    if (opened) {
      sessionStorage.setItem('makervaerkstedet-forside', opened[1]);
      history.replaceState(null, '', location.pathname + location.search);
    }
    const port = sessionStorage.getItem('makervaerkstedet-forside') || '';
    if (!/^\d{2,5}$/.test(port) || document.getElementById('forside-retur')) return;
    const bar = document.createElement('div'), back = document.createElement('a');
    bar.id = 'forside-retur';
    // Only this computer's own start page can be the target.
    back.href = `http://127.0.0.1:${port}/makervaerkstedet.html`;
    back.textContent = '\u2190 Tilbage til forsiden';
    Object.assign(bar.style, {display: 'flex', justifyContent: 'flex-end', padding: '10px 5%', background: '#fff', borderBottom: '1px solid #e1e1e1'});
    Object.assign(back.style, {border: '1px solid #c62b2f', borderRadius: '2px', color: '#8c1d24', background: '#fff', padding: '10px 18px', textDecoration: 'none', font: '15px Metrophobic,Helvetica,Arial,sans-serif'});
    bar.append(back);
    document.body.prepend(bar);
  } catch { /* without session storage the button is simply not shown */ }
})();
