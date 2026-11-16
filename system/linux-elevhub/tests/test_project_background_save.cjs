'use strict';
// Real session script and Chromium, synthetic adapter/store, loopback only.
// Catches stale asynchronous app work writing under a different pupil/session.
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {loadPlaywright} = require('./student_journey_fixture.js');
const script = fs.readFileSync(path.join(__dirname, '../projekter/web/project-session.js'));
const html = `<!doctype html><meta charset="utf-8"><title>Session save fixture</title><body>
<script>
window.fixture = {
  data:'empty', name:'', gates:{}, preparations:[], exports:[],
  hold(method) { let release; const promise = new Promise(r => release = r); this.gates[method] = {promise, release}; },
  release(method) { this.gates[method].release(); },
  async wait(method) { const gate = this.gates[method]; if (gate && !gate.taken) { gate.taken = true; await gate.promise; } }
};
const nativeSetInterval = window.setInterval;
window.setInterval = (callback, delay, ...args) => {
  if (delay === 15000) fixture.backup = callback;
  return nativeSetInterval(callback, delay, ...args);
};
window.addEventListener('elevhub-project-named', () => {
  fixture.namedState = window.ElevhubProjectSession.saveState();
});
window.ElevhubProjectAdapter = {
  type:'scratch', extension:'.sb3', name:() => fixture.name,
  setName:value => { fixture.name = value; },
  signature:async () => { const value = fixture.data; await fixture.wait('signature'); return value; },
  export:async options => { fixture.exports.push(options); return new Blob([fixture.data]); },
  prepareSave:options => { fixture.preparations.push(options); return fixture.wait('prepareSave'); },
  prepareFinish:() => fixture.wait('prepareFinish'),
  reset:async () => { fixture.data = 'empty'; fixture.name = ''; await fixture.wait('reset'); },
  import:async (blob, name) => { fixture.data = await blob.text(); fixture.name = name; await fixture.wait('import'); }
};
</script><script src="/project-session.js"></script>`;

(async () => {
  const writes = [], attempts = [];
  const latestProject = {revision:4, body:'latest autosave B'};
  let failNext = false;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/project-session.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(script); return; }
    if (url.pathname === '/editor') { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(html); return; }
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname.endsWith('/save')) {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      attempts.push(Object.fromEntries(url.searchParams));
      if (failNext) { failNext = false; res.writeHead(503); res.end(JSON.stringify({error:'Synthetic local save failure'})); return; }
      writes.push({query:Object.fromEntries(url.searchParams), body:Buffer.concat(chunks).toString()});
      if (url.searchParams.get('id') === 'a'.repeat(32)) {
        latestProject.body = writes.at(-1).body;
        latestProject.revision++;
        res.end(JSON.stringify({revision:latestProject.revision})); return;
      }
      res.end(JSON.stringify({revision:writes.length})); return;
    }
    if (url.pathname.endsWith('/list')) { res.end(JSON.stringify({projects:[]})); return; }
    if (url.pathname.endsWith('/poll')) { res.end('{}'); return; }
    if (url.pathname.endsWith('/open-info')) {
      res.end(JSON.stringify({program:'scratch', project:{id:'a'.repeat(32), name:'Older project', folder:'5A · Alma', filename:'older.sb3', revision:latestProject.revision}})); return;
    }
    if (url.pathname.endsWith('/file')) { res.end(url.searchParams.get('revision') === '1' ? 'older version' : latestProject.body); return; }
    if (url.pathname === '/_elevhub/projekter/') { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><title>Picker</title>'); return; }
    res.writeHead(404); res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await loadPlaywright().chromium.launch({headless:true, channel:'chrome'});
    const page = await browser.newPage();
    page.on('dialog', dialog => dialog.accept());
    const errors = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (!request.url().startsWith(base)) external.push(request.url()); });
    const state = () => page.evaluate(() => ElevhubProjectSession.saveState());
    const background = () => page.evaluate(() => ElevhubProjectSession.save({background:true, expectedNaming:ElevhubProjectSession.naming()}));
    async function name(student = 'Alma', title = 'Named empty project') {
      await page.locator('#project-classroom').fill('5A');
      await page.locator('#project-student').fill(student);
      await page.locator('#project-title').fill(title);
      await page.locator('#project-start-confirm').click();
      await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
    }
    async function recoveryRecord(legacy = false) {
      return page.evaluate(legacy => new Promise((resolve, reject) => {
        const open = indexedDB.open('elevhub-workshop-recovery', 1);
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const db = open.result, tx = db.transaction('copies', legacy ? 'readwrite' : 'readonly');
          const store = tx.objectStore('copies'), get = store.get('/editor');
          let value;
          get.onsuccess = () => {
            value = get.result;
            if (legacy) { delete value.historical; store.put(value, '/editor'); }
          };
          tx.oncomplete = () => { db.close(); resolve(value); };
          tx.onerror = () => { db.close(); reject(tx.error); };
        };
      }), legacy);
    }
    async function recoverAfterReload() {
      await page.reload();
      await page.locator('#project-start-confirm').waitFor();
      await name('Recovery', 'Recovery view');
      await page.locator('#elevhub-project-recover').click();
      await page.waitForFunction(() => document.querySelector('[role=status]')?.textContent.includes('Arbejdet er gendannet'));
    }
    await page.goto(base + '/editor');
    await page.locator('#project-start-confirm').waitFor();
    assert.equal(await page.evaluate(() => typeof ElevhubProjectSession.saveState), 'function', 'session exposes safe background-save readiness');
    assert.deepEqual(await state(), {ready:false, stored:false, historical:false});
    assert.equal(await background(), null, 'startup is silently ineligible');
    assert.equal(writes.length, 0);
    await name();
    assert.deepEqual(await state(), {ready:true, stored:false, historical:false});
    assert.equal(await page.evaluate(() => ElevhubProjectSession.dirty()), false);
    failNext = true;
    assert.equal(await background(), false, 'first empty save reports a real failure');
    assert.deepEqual(await state(), {ready:true, stored:false, historical:false}, 'an unconfirmed binding must remain eligible for the first save');
    assert.equal(await page.evaluate(() => ElevhubProjectSession.dirty()), false, 'empty project stays clean after failed first save');
    assert.equal(writes.length, 0);
    assert.equal(await background(), true, 'a named empty project can be stored the first time');
    assert.deepEqual(await state(), {ready:true, stored:true, historical:false});
    assert.equal(writes.length, 1);
    assert.equal(writes[0].body, 'empty');
    assert.equal(writes[0].query.expected, '0');
    assert.equal(attempts[0].id, writes[0].query.id, 'failed first save retries the existing project id');
    assert.deepEqual(await page.evaluate(() => fixture.preparations.at(-1)), {background:true});
    assert.deepEqual(await page.evaluate(() => fixture.exports.at(-1)), {background:true});

    const staleNaming = await page.evaluate(() => {
      window.oldNaming = ElevhubProjectSession.naming();
      return ElevhubProjectSession.save({background:true, expectedNaming:{...oldNaming}});
    });
    assert.equal(staleNaming, null, 'naming must be the same session object');
    assert.equal(writes.length, 1);
    await page.evaluate(() => {
      fixture.hold('prepareSave');
      window.pendingSave = ElevhubProjectSession.save({background:true, expectedNaming:ElevhubProjectSession.naming()});
    });
    assert.deepEqual(await state(), {ready:false, stored:true, historical:false});
    assert.equal(await background(), null, 'busy is locked before yielding to adapter');
    await page.evaluate(() => fixture.release('prepareSave'));
    assert.equal(await page.evaluate(() => pendingSave), true);
    assert.equal(writes.length, 2);
    failNext = true;
    assert.equal(await background(), false, 'a real local save error is distinguishable from ineligibility');
    assert.equal(await page.locator('#elevhub-project-dialog').isVisible(), false);
    assert.match(await page.locator('[role=status]').textContent(), /Synthetic local save failure/);

    await page.evaluate(() => ElevhubProjectSession.open());
    assert.deepEqual(await state(), {ready:false, stored:true, historical:false});
    assert.equal(await background(), null, 'project picker blocks background saves without replacing its dialog');
    assert.equal(await page.locator('#project-library-close').isVisible(), true);
    await page.locator('#project-library-close').click();

    await page.evaluate(() => {
      fixture.hold('prepareFinish');
      window.pendingFinish = ElevhubProjectSession.finish();
    });
    assert.equal((await state()).ready, false);
    assert.equal(await background(), null, 'finish blocks new background writes');
    await page.evaluate(() => fixture.release('prepareFinish'));
    assert.equal(await page.evaluate(() => pendingFinish), true);
    assert.deepEqual(await state(), {ready:false, stored:false, historical:false});
    assert.equal(await background(), null, 'completed pupil session remains ineligible');

    await page.evaluate(() => { ElevhubProjectSession.begin(); });
    await name();
    await page.evaluate(() => {
      window.beforeSwitch = ElevhubProjectSession.naming();
      fixture.data = 'old pupil changes'; fixture.hold('signature');
      window.appDirty = ElevhubProjectSession.dirty();
      fixture.hold('reset'); window.pendingReset = ElevhubProjectSession.reset();
    });
    assert.equal((await state()).ready, false);
    assert.equal(await background(), null, 'reset blocks background saves before clearing naming');
    await page.evaluate(() => fixture.release('reset'));
    await page.evaluate(() => pendingReset);
    await page.evaluate(() => { ElevhubProjectSession.begin(); });
    await name('Bo', 'Next pupil project');
    await page.evaluate(() => fixture.release('signature'));
    assert.equal(await page.evaluate(() => appDirty), true);
    assert.deepEqual(await state(), {ready:true, stored:false, historical:false});
    assert.equal(await page.evaluate(() => ElevhubProjectSession.save({background:true, expectedNaming:beforeSwitch})), null,
      'an app resuming after async dirty cannot save the next pupil');
    assert.equal(writes.length, 2);

    await page.evaluate(() => {
      fixture.hold('build'); window.pendingReplace = ElevhubProjectSession.replace(async () => {
        await fixture.wait('build'); fixture.data = 'imported new content';
      });
    });
    await page.waitForFunction(() => fixture.gates.build.taken);
    assert.equal((await state()).ready, false);
    assert.equal(await background(), null, 'replacement import blocks writes');
    await page.evaluate(() => fixture.release('build'));
    assert.equal(await page.evaluate(() => pendingReplace), true);
    assert.deepEqual(await state(), {ready:true, stored:false, historical:false});

    // A reset that starts while export preparation waits must not persist its data.
    await page.evaluate(() => {
      fixture.hold('prepareSave');
      window.pendingSave = ElevhubProjectSession.save({background:true, expectedNaming:ElevhubProjectSession.naming()});
      fixture.hold('reset'); window.pendingReset = ElevhubProjectSession.reset();
      fixture.release('prepareSave');
    });
    assert.equal(await page.evaluate(() => pendingSave), null);
    assert.equal(writes.length, 2);
    await page.evaluate(() => fixture.release('reset'));
    await page.evaluate(() => pendingReset);

    // Historical A is dirty but must never replace the latest autosave B
    // without an explicit restore, including after browser recovery.
    await page.goto(base + '/editor?elevhub_project=' + 'a'.repeat(32) + '&elevhub_revision=1');
    await page.waitForFunction(() => document.querySelector('[role=status]')?.textContent.includes('Åbnet'));
    assert.equal(await page.evaluate(() => ElevhubProjectSession.dirty()), true, 'historical restore stays dirty for explicit save/finish');
    assert.equal(await background(), null, 'opening historical A must not automatically overwrite newer autosave B');
    assert.equal(latestProject.body, 'latest autosave B');
    assert.deepEqual(await state(), {ready:false, stored:true, historical:true});
    assert.equal(await page.evaluate(() => fixture.namedState.historical), true, 'controllers observe historical state before the named event');
    assert.equal(writes.length, 2, 'loading an old version never saves by itself');
    await page.evaluate(() => fixture.backup());
    const backedUp = await recoveryRecord();
    assert.equal(backedUp.historical, true);
    assert.equal(backedUp.saved, null);
    await recoverAfterReload();
    assert.deepEqual(await state(), {ready:false, stored:true, historical:true});
    assert.equal(await background(), null, 'browser recovery retains the historical barrier');
    await recoveryRecord(true);
    await recoverAfterReload();
    assert.deepEqual(await state(), {ready:false, stored:true, historical:true});
    assert.equal(await background(), null, 'ambiguous old recovery records conservatively retain the barrier');
    assert.equal(writes.length, 2);
    assert.equal(latestProject.body, 'latest autosave B');
    failNext = true;
    assert.equal(await page.evaluate(() => ElevhubProjectSession.save()), false);
    assert.deepEqual(await state(), {ready:false, stored:true, historical:true}, 'a failed explicit save cannot unlock historical work');
    assert.equal(await page.evaluate(() => ElevhubProjectSession.save()), true, 'explicit manual restore stays available');
    assert.deepEqual(await state(), {ready:true, stored:true, historical:false});
    assert.equal(writes[2].body, 'older version');
    assert.equal(writes[2].query.expected, '4');
    assert.equal(writes[2].query.autosave, undefined, 'explicit restore creates a protected manual checkpoint');
    assert.deepEqual(await page.evaluate(() => fixture.preparations.at(-1)), {background:false});
    assert.deepEqual(await page.evaluate(() => fixture.exports.at(-1)), {background:false});
    await page.evaluate(() => { ElevhubProjectAdapter.signature = null; fixture.exports = []; });
    assert.equal(await background(), true);
    assert.deepEqual(await page.evaluate(() => fixture.exports), [{background:true}, {background:true}],
      'export-based signature must also preserve the background context');
    assert.equal(writes.at(-1).query.autosave, '1', 'autosave resumes only after acknowledged manual restore');
    await page.goto(base + '/editor?elevhub_project=' + 'a'.repeat(32) + '&elevhub_revision=1');
    await page.waitForFunction(() => document.querySelector('[role=status]')?.textContent.includes('Åbnet'));
    assert.equal((await state()).historical, true);
    await page.locator('#elevhub-project-copy').click();
    await page.locator('#project-title').fill('Historical copy');
    await page.locator('#project-save-confirm').click();
    await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'});
    assert.deepEqual(await state(), {ready:true, stored:true, historical:false}, 'acknowledged manual copy unlocks its new project');
    assert.notEqual(writes.at(-1).query.id, 'a'.repeat(32));
    assert.equal(writes.at(-1).query.autosave, undefined);
    await page.goto(base + '/editor?elevhub_project=' + 'a'.repeat(32));
    await page.waitForFunction(() => document.querySelector('[role=status]')?.textContent.includes('Åbnet'));
    assert.deepEqual(await state(), {ready:true, stored:true, historical:false}, 'current version opens normally');
    await page.goto(base + '/editor?elevhub_project=' + 'a'.repeat(32) + '&elevhub_revision=1');
    await page.waitForFunction(() => document.querySelector('[role=status]')?.textContent.includes('Åbnet'));
    await page.evaluate(() => { window.pendingFinish = ElevhubProjectSession.finish(); });
    await page.locator('#project-finish-continue').click();
    assert.equal(await page.evaluate(() => pendingFinish), false);
    assert.deepEqual(await state(), {ready:false, stored:true, historical:true}, 'continue preserves the historical barrier');
    await page.evaluate(() => { window.pendingFinish = ElevhubProjectSession.finish(); });
    await page.locator('#project-finish-save').click();
    assert.equal(await page.evaluate(() => pendingFinish), true, 'explicit save-and-finish can still restore historical work');
    assert.deepEqual(await state(), {ready:false, stored:false, historical:false}, 'finished reset clears the historical state');
    await page.evaluate(() => { ElevhubProjectSession.begin(); });
    await name('New pupil', 'Fresh project');
    assert.deepEqual(await state(), {ready:true, stored:false, historical:false});
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('PASS safe background saves, stale pupil work, historical autosave barrier, historical/legacy browser recovery and acknowledged manual restore/copy');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
