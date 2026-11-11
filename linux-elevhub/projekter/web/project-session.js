/* Local, explicit project saves and safe handoff between pupils. */
(() => {
  'use strict';
  const base = '/_elevhub/projekter/api/', sessions = '/_elevhub/session/';
  const key = location.pathname;
  // A reload keeps only an anonymous handoff token, never a project or pupil.
  // This lets the server recognise the editor again after an interrupted poll.
  const client = (() => {
    const storageKey = 'elevhub-session-client-' + key;
    try {
      let id = sessionStorage.getItem(storageKey);
      if (!/^[a-f0-9]{32}$/.test(id || '')) id = crypto.randomUUID().replaceAll('-', '');
      sessionStorage.setItem(storageKey, id); return id;
    } catch (_) { return crypto.randomUUID().replaceAll('-', ''); }
  })();
  let adapter, bound = null, naming = null, saved = null, initial = null;
  let busy = false, importing = false, approved = false, finishing = null;
  let dialog, status, cancelDialog, currentRequest = null, polling = false, backupBusy = false;
  let started = false;
  const node = (tag, text, attrs = {}) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  const button = (text, id) => node('button', text, {type:'button', id});
  const say = (text, error = false) => { status.textContent = text; status.classList.toggle('error', error); };
  async function request(url, options = {}) {
    const r = await fetch(url, {cache:'no-store', ...options});
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw Error(d.error || 'Projektet kunne ikke gemmes. Dit arbejde er stadig åbent.');
    }
    return r;
  }
  const post = (action, data) => request(sessions + action, {method:'POST',
    headers:{'Content-Type':'application/json', 'X-Elevhub-Session':'1'}, body:JSON.stringify(data)}).then(r => r.json());
  async function signature() {
    if (adapter.signature) return JSON.stringify({name:adapter.name(), data:await adapter.signature()});
    const data = await (await adapter.export()).arrayBuffer();
    return [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].join(',');
  }
  const dirty = async () => await signature() !== saved;
  function recovery(mode, value) {
    return new Promise((resolve, reject) => {
      const open = indexedDB.open('elevhub-workshop-recovery', 1);
      open.onupgradeneeded = () => open.result.createObjectStore('copies');
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result, tx = db.transaction('copies', mode === 'get' ? 'readonly' : 'readwrite');
        const store = tx.objectStore('copies');
        const op = mode === 'get' ? store.get(key) : mode === 'put' ? store.put(value, key) : store.delete(key);
        tx.oncomplete = () => { db.close(); resolve(op.result); };
        tx.onerror = () => { db.close(); reject(tx.error); };
      };
    });
  }
  async function backup() {
    if (busy || importing || finishing || approved || backupBusy) return;
    backupBusy = true;
    try {
      if (await dirty()) await recovery('put', {blob:await adapter.export(), bound, naming, saved, time:Date.now(), kind:adapter.type});
    } catch (_) { say('Sikkerhedskopien kunne ikke gemmes. Brug Gem projekt.', true); }
    finally { backupBusy = false; }
  }
  const clean = (s, max) => String(s || '').normalize('NFC').replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, max);
  const fileName = n => [n.classroom, n.student, n.title].join(' - ').replace(/[\\/:*?"<>|]/g, '-').slice(0, 160) + adapter.extension;
  function show(content) {
    if (dialog.open) dialog.close();
    dialog.replaceChildren(content); dialog.showModal();
  }
  async function save() {
    if (!bound || busy) return false;
    busy = true;
    const target = bound;
    try {
      if (adapter.prepareSave) await adapter.prepareSave();
      if (naming && adapter.setName && adapter.name()) {
        naming.title = clean(adapter.name(), 100);
        target.name = naming.title; target.filename = fileName(naming);
      }
      const sig = await signature(), blob = await adapter.export();
      if (target !== bound) throw Error('Projektet blev skiftet. Gem det nye projekt igen.');
      const query = new URLSearchParams({...target, kind:adapter.type});
      const meta = await (await request(base + 'save?' + query, {method:'POST',
        headers:{'Content-Type':'application/octet-stream', 'X-Elevhub-Project':'1'}, body:blob})).json();
      if (target !== bound) return false;
      target.expected = meta.revision; saved = sig;
      say('Gemt på denne computer · ' + target.folder + ' · ' + target.name);
      window.dispatchEvent(new CustomEvent('elevhub-project-saved', {detail:naming}));
      return true;
    } catch (error) { say(error.message, true); return false; }
    finally { busy = false; }
  }
  function saveDialog(copy = false, starting = false) {
    return new Promise(resolve => {
      const form = node('form');
      form.append(node('h2', starting ? 'Hvad vil du arbejde på?' : copy ? 'Gem en kopi' : 'Gem dit projekt'), node('p', starting ? 'Start et nyt projekt med klasse og navn, eller åbn et projekt, du har gemt.' : 'Vælg klasse, skriv dit fornavn og giv projektet et navn. Projektet gemmes på denne computer.'));
      const classroom = node('input', undefined, {id:'project-classroom', required:'', maxlength:'40', list:'project-class-options', autocomplete:'off'});
      const student = node('input', undefined, {id:'project-student', required:'', maxlength:'60', autocomplete:'off'});
      const title = node('input', undefined, {id:'project-title', required:'', maxlength:'100', autocomplete:'off'});
      classroom.value = naming?.classroom || ''; student.value = naming?.student || ''; title.value = naming?.title || adapter.name() || '';
      classroom.placeholder = 'Vælg eller skriv din klasse'; student.placeholder = 'Fx Alma K.';
      for (const [text, input] of [['Klasse', classroom], ['Dit fornavn', student], ['Projektets navn', title]]) {
        const label = node('label', text); label.append(input); form.append(label);
      }
      const options = node('datalist', undefined, {id:'project-class-options'}); form.append(options);
      request(base + 'list').then(r => r.json()).then(data => {
        for (const value of [...new Set(data.projects.map(p => p.folder.split(' · ')[0]).filter(Boolean))].sort()) options.append(node('option', undefined, {value}));
      }).catch(() => {});
      const submit = node('button', starting ? 'Start projekt' : 'Gem projekt', {id:starting ? 'project-start-confirm' : 'project-save-confirm', type:'submit'});
      const cancel = button(starting ? 'Åbn projekt' : 'Arbejd videre', starting ? 'project-start-open' : 'project-save-cancel'), error = node('p', '', {role:'alert'});
      form.append(error, submit, cancel);
      let settled = false;
      const done = value => { if (settled) return; settled = true; cancelDialog = null; dialog.close(); resolve(value); };
      cancel.onclick = () => { if (!busy) { done(false); if (starting) shelf(); } };
      cancelDialog = () => { if (!busy && !starting) done(false); };
      form.onsubmit = async event => {
        event.preventDefault(); if (busy) return;
        const value = {classroom:clean(classroom.value, 40), student:clean(student.value, 60), title:clean(title.value, 100)};
        if (Object.values(value).some(v => !v)) { error.textContent = 'Udfyld klasse, navn og projektnavn.'; return; }
        const old = bound, oldNaming = naming;
        naming = value;
        submit.disabled = cancel.disabled = true;
        try {
          if (adapter.setName) await adapter.setName(value.title);
          if (starting) {
          initial = saved = await signature(); started = true; approved = false;
          say(value.classroom + ' · ' + value.student + ' · ' + value.title);
          window.dispatchEvent(new CustomEvent('elevhub-project-named', {detail:value}));
            done(true); return;
          }
          bound = {id:copy || !old ? crypto.randomUUID().replaceAll('-', '') : old.id,
          name:value.title, folder:value.classroom + ' · ' + value.student, filename:fileName(value), expected:copy || !old ? 0 : old.expected};
          if (await save()) done(true);
          else { bound = old; naming = oldNaming; error.textContent = status.textContent; }
        } catch (e) { naming = oldNaming; bound = old; error.textContent = e.message; }
        finally { if (!settled) submit.disabled = cancel.disabled = false; }
      };
      show(form); classroom.focus();
    });
  }
  const saveProject = () => {
    if (bound) return save();
    if (!naming) return saveDialog();
    bound = {id:crypto.randomUUID().replaceAll('-', ''), name:naming.title,
      folder:naming.classroom + ' · ' + naming.student, filename:fileName(naming), expected:0};
    return save();
  };
  async function clearWork() {
    if (typeof adapter.reset !== 'function') throw Error('Værkstedet er ikke klar til elevskift. Dit arbejde er stadig åbent.');
    importing = true;
    try {
      // Remove the recoverable copy before allowing a new pupil to enter.
      while (backupBusy) await new Promise(r => setTimeout(r, 25));
      await recovery('delete'); await adapter.reset();
      bound = naming = null;
      started = false;
      sessionStorage.removeItem('elevhub-project-binding-' + key);
      localStorage.removeItem('elevhub-project-folder');
      initial = saved = await signature(); approved = true;
      document.getElementById('elevhub-project-recover').hidden = true;
      say('Klar til en ny elev.');
    } finally { importing = false; }
  }
  async function finishImpl() {
    if (busy || importing) { say('Vent, til projektet er færdigt med at gemme eller åbne.', true); return false; }
    if (adapter.prepareFinish) await adapter.prepareFinish();
    if (!await dirty()) { await clearWork(); return true; }
    return new Promise(resolve => {
      const content = node('div');
      content.append(node('h2', 'Vil du gemme, inden du går videre?'), node('p', 'Der er ændringer i dit projekt, som du ikke har gemt.'));
      const yes = button('Gem og afslut', 'project-finish-save'), no = button('Afslut uden at gemme', 'project-finish-discard');
      const back = button('Arbejd videre', 'project-finish-continue'), error = node('p', '', {role:'alert'});
      content.append(error, yes, no, back);
      let settled = false;
      const done = result => { if (settled) return; settled = true; cancelDialog = null; dialog.close(); resolve(result); };
      back.onclick = cancelDialog = () => done(false);
      async function complete(saving) {
        yes.disabled = no.disabled = back.disabled = true;
        try {
          if (saving && !await saveProject()) { done(false); return; }
          if (currentRequest) {
            const check = await (await request(sessions + 'status?id=' + currentRequest)).json();
            if (check.state !== 'pending') { done(false); return; }
          }
          if (saving && await dirty()) throw Error('Projektet blev ændret under gemning. Gem igen, før du afslutter.');
          await clearWork(); done(true);
        } catch (e) {
          error.textContent = e.message; say(e.message, true);
          yes.disabled = no.disabled = back.disabled = false;
          if (!dialog.open || !dialog.contains(error)) show(content);
          cancelDialog = () => done(false);
        }
      }
      yes.onclick = () => complete(true); no.onclick = () => complete(false);
      show(content); back.focus();
    });
  }
  function finish() {
    if (!finishing) finishing = finishImpl().catch(e => { say(e.message, true); return false; }).finally(() => { finishing = null; });
    return finishing;
  }
  async function replaceProject(build) {
    const previousName = naming ? {...naming} : null;
    if (!await finish()) return false;
    importing = true;
    try {
      await build();
      naming = previousName; bound = null; saved = null; approved = false;
      started = !!naming;
      if (naming) {
        if (adapter.setName) await adapter.setName(naming.title);
        say(naming.classroom + ' · ' + naming.student + ' · ' + naming.title);
        window.dispatchEvent(new CustomEvent('elevhub-project-named', {detail:naming}));
      } else saveDialog(false, true);
      return true;
    } catch (e) { say(e.message, true); saveDialog(false, true); return false; }
    finally { importing = false; }
  }
  async function load(id, revision = '') {
    if (busy || !/^[a-f0-9]{32}$/.test(id)) return;
    // Read before asking to replace anything, so a failed lookup keeps the work.
    let info, blob;
    try {
      info = await (await request(base + 'open-info?' + new URLSearchParams({id, revision}))).json();
      if (info.program !== adapter.type) throw Error('Projektet hører til et andet værksted.');
      blob = info.editable ? new Blob([JSON.stringify(info.editable)], {type:'application/json'}) :
        await (await request(base + 'file?' + new URLSearchParams({id, revision}))).blob();
      if (await dirty() && !await finish()) return;
      busy = importing = true;
      await adapter.import(blob, info.project.name);
      const m = info.project, parts = m.folder.split(' · ');
      naming = parts.length === 2 ? {classroom:parts[0], student:parts[1], title:m.name} : null;
      bound = m.filename.toLowerCase().endsWith(adapter.extension.toLowerCase()) ?
        {id:m.id, name:m.name, folder:m.folder, filename:m.filename, expected:m.revision} : null;
      saved = revision && Number(revision) !== m.revision ? null : await signature(); approved = false;
      started = true;
      if (naming) window.dispatchEvent(new CustomEvent('elevhub-project-named', {detail:naming}));
      if (dialog.open) dialog.close(); history.replaceState(null, '', location.pathname);
      say('Åbnet ' + m.name + ' · ' + m.folder);
    } catch (e) { say(e.message, true); if (!started) saveDialog(false, true); }
    finally { busy = importing = false; }
  }
  function shelf() {
    const content = node('div'); content.append(node('h2', 'Åbn et projekt'));
    const frame = node('iframe', undefined, {title:'Projektarkiv', src:'/_elevhub/projekter/?pick=' + adapter.type});
    const close = button(started ? 'Arbejd videre' : 'Tilbage', 'project-library-close');
    close.onclick = () => { dialog.close(); if (!started) saveDialog(false, true); };
    cancelDialog = close.onclick;
    content.append(frame, close); show(content);
  }
  async function poll() {
    if (polling || importing) return;
    polling = true;
    try {
      const changed = await dirty(); if (changed) approved = false;
      const data = await post('poll', {client, kind:adapter.type, dirty:changed, active:!approved});
      if (data.request?.state === 'pending' && !currentRequest) {
        currentRequest = data.request.id;
        finish().then(async ok => {
          try { await post('ack', {client, id:currentRequest, state:ok ? 'completed' : 'cancelled'}); }
          catch (_) { say('Afslutningen kunne ikke bekræftes. Prøv Til start igen.', true); }
          finally { currentRequest = null; }
        });
      } else if (currentRequest && data.request?.id === currentRequest && data.request.state !== 'pending') cancelDialog?.();
    } catch (_) { /* Kiosk navigation requires a confirmed response. */ }
    finally { polling = false; }
  }
  async function start() {
    if (!dialog) {
      dialog = node('dialog', undefined, {id:'elevhub-project-dialog', 'aria-label':'Dit projekt'});
      document.body.append(dialog);
      dialog.addEventListener('cancel', e => { e.preventDefault(); if (!busy && cancelDialog) cancelDialog(); });
      const loading = node('div'); loading.append(node('h2', 'Gør værkstedet klar …'));
      show(loading);
    }
    adapter = window.ElevhubProjectAdapter;
    if (!adapter) { setTimeout(() => start().catch(startFailed), 250); return; }
    if (document.getElementById('elevhub-project-bar')) return;
    const bar = node('section', undefined, {id:'elevhub-project-bar', 'aria-label':'Gem og åbn projekter'});
    const saveButton = button('Gem projekt', 'elevhub-project-save'), open = button('Åbn projekt', 'elevhub-project-open');
    const copy = button('Gem kopi', 'elevhub-project-copy'), recover = button('Gendan efter afbrydelse', 'elevhub-project-recover');
    recover.hidden = true; status = node('span', 'Gem med klasse, navn og projektnavn.', {role:'status'});
    bar.append(saveButton, open, copy, recover, status); document.body.prepend(bar); document.body.classList.add('elevhub-project-ready');
    const existingSave = document.getElementById('save-project');
    if (existingSave && ['laser', 'folie'].includes(adapter.type)) {
      // Reuse the editor's visible controls rather than displaying two save bars.
      bar.hidden = true; document.body.classList.add('elevhub-project-inline');
      status = document.getElementById('save-status');
      document.querySelector('.project-actions').append(recover);
      existingSave.onclick = () => saveProject();
      document.getElementById('import-button').onclick = shelf;
    }
    initial = await signature(); saved = adapter.hasContent && await adapter.hasContent() ? null : initial;
    saveButton.onclick = () => saveProject(); open.onclick = shelf; copy.onclick = () => saveDialog(true);
    recover.onclick = async () => {
      try {
        const old = await recovery('get'); if (!old || old.kind !== adapter.type) return;
        if (await dirty() && !await finish()) return;
        importing = true; await adapter.import(old.blob, old.bound?.name || 'Gendannet projekt');
        bound = old.bound; naming = old.naming; saved = old.saved; approved = false;
        started = true; if (dialog.open) dialog.close();
        recover.hidden = true; say('Arbejdet er gendannet. Gem projektet, når du er klar.');
      } catch (e) { say(e.message, true); }
      finally { importing = false; }
    };
    recovery('get').then(v => { recover.hidden = !v; }).catch(() => {});
    window.ElevhubProjectSession = {finish, save:saveProject, open:shelf, dirty, reset:clearWork,
      replace:replaceProject, begin:() => { approved = false; return saveDialog(false, true); }, naming:() => naming};
    window.addEventListener('elevhub-project-changed', () => {
      if (importing) return;
      bound = null; saved = null; approved = false;
      sessionStorage.removeItem('elevhub-project-binding-' + key);
      say('Nyt projekt · gem med klasse, navn og projektnavn.');
    });
    window.addEventListener('message', e => {
      if (e.origin === location.origin && e.source === dialog.querySelector('iframe')?.contentWindow && e.data?.type === 'elevhub-open-project') load(e.data.id, e.data.revision || '');
    });
    window.addEventListener('keydown', e => {
      if (!(e.ctrlKey || e.metaKey) || !['s', 'o'].includes(e.key.toLowerCase())) return;
      e.preventDefault(); e.stopImmediatePropagation();
      if (dialog.open) return;
      if (e.key.toLowerCase() === 's') saveProject(); else shelf();
    }, true);
    document.addEventListener('click', async e => {
      const link = e.target.closest('a[href]');
      if (!link || link.hasAttribute('download') || link.target === '_blank' || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const url = new URL(link.href, location.href);
      if (!['http:', 'https:', 'file:'].includes(url.protocol) || url.href.split('#')[0] === location.href.split('#')[0]) return;
      e.preventDefault(); e.stopImmediatePropagation(); if (await finish()) location.assign(url.href);
    }, true);
    let changed = saved !== initial;
    setInterval(async () => { try { changed = await dirty(); } catch (_) { changed = true; } }, 500);
    window.addEventListener('beforeunload', e => { if (!approved && changed) { e.preventDefault(); e.returnValue = ''; } });
    window.addEventListener('pagehide', () => {
      fetch(sessions + 'poll', {method:'POST', keepalive:true, headers:{'Content-Type':'application/json', 'X-Elevhub-Session':'1'},
        body:JSON.stringify({client, kind:adapter.type, dirty:changed, active:false})}).catch(() => {});
    });
    const q = new URL(location.href).searchParams;
    if (q.get('elevhub_project')) await load(q.get('elevhub_project'), q.get('elevhub_revision') || '');
    else {
      if (adapter.hasContent && await adapter.hasContent()) await backup();
      importing = true;
      try { if (adapter.reset) await adapter.reset(); initial = saved = await signature(); }
      finally { importing = false; }
      saveDialog(false, true);
    }
    window.addEventListener('pageshow', event => { if (event.persisted && approved) { approved = false; saveDialog(false, true); } });
    setInterval(poll, 750); setInterval(backup, 15000); poll();
  }
  function startFailed(error) {
    if (status) say(error.message, true);
    const content = node('div'), retry = button('Prøv igen', 'project-start-retry');
    content.append(node('h2', 'Værkstedet kunne ikke gøres klar'), node('p', error.message), retry);
    retry.onclick = () => location.reload();
    show(content);
  }
  start().catch(startFailed);
})();
