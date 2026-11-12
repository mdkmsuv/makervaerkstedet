// Real compiled 3D editor + shared project lifecycle, isolated in-memory project API.
// Run after npm run build:model3d: node scripts/verify_model3d_projects.mjs
import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import os from 'node:os';
import {readFile, mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {chromium} from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const projectWeb = path.join(root, 'linux-elevhub/projekter/web');
const output = process.env.MODEL3D_PROJECT_REVIEW_OUTPUT || path.join(os.tmpdir(), 'mkv-model3d-project-review');
await mkdir(output, {recursive:true});
const records = new Map();
let failSave = false, request = null, saveCount = 0;
const types = {'.html':'text/html; charset=utf-8','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.wasm':'application/wasm','.ttf':'font/ttf','.svg':'image/svg+xml','.txt':'text/plain'};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://fixture');
  const send = (status, data, type='application/json') => {
    res.writeHead(status, {'Content-Type':type,'Cache-Control':'no-store'});
    res.end(type === 'application/json' ? JSON.stringify(data) : data);
  };
  try {
    if (url.pathname.startsWith('/_elevhub/session/')) {
      let data = ''; for await (const chunk of req) data += chunk;
      const input = data ? JSON.parse(data) : {};
      if (url.pathname.endsWith('/ack') && request?.id === input.id) request.state = input.state;
      return send(200, url.pathname.endsWith('/status') ? {state:request?.state} : {request});
    }
    const prefix = '/_elevhub/projekter/api/';
    if (url.pathname.startsWith(prefix)) {
      const action = url.pathname.slice(prefix.length), id = url.searchParams.get('id');
      if (action === 'list') return send(200, {projects:[...records.values()].map(({bytes,...meta}) => ({...meta,opening:{program:'3dprint',label:'3D-værksted'}})),local:true,scan_error:''});
      if (action === 'config') return send(200, {apps:[]});
      if (action === 'preview') return send(204, '', 'image/png');
      if (action === 'save') {
        if (failSave) return send(503, {error:'Projektet kunne ikke gemmes. Dit arbejde er stadig åbent.'});
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const old = records.get(id), meta = {...Object.fromEntries(url.searchParams),id,revision:(old?.revision || 0)+1,modified:Date.now()/1000,bytes:Buffer.concat(chunks)};
        records.set(id, meta); saveCount++;
        const {bytes,...plain} = meta; return send(200, plain);
      }
      const record = records.get(id);
      if (!record) return send(404, {error:'Projektet findes ikke.'});
      const {bytes,...meta} = record;
      if (action === 'open-info') return send(200, {project:meta,program:'3dprint',label:'3D-værksted'});
      if (action === 'file') return send(200, bytes, 'application/octet-stream');
      if (action === 'meta') return send(200, {...meta,versions:[{revision:meta.revision,created:meta.modified}]});
      return send(404, {error:'Unknown fixture API'});
    }
    const routes = {'/session.js':'project-session.js','/bridge.js':'projekt-bridge.js','/bridge.css':'projekt-bridge.css','/':'projekter.html'};
    let base = path.join(root, 'web'), relative = decodeURIComponent(url.pathname).replace(/^\//,'');
    if (url.pathname.startsWith('/_elevhub/projekter')) {
      const suffix = url.pathname.slice('/_elevhub/projekter'.length) || '/';
      base = projectWeb; relative = routes[suffix] || suffix.slice(1);
    }
    const filename = path.resolve(base, relative);
    if (!filename.startsWith(base + path.sep)) return send(403, {}, 'application/json');
    let body = await readFile(filename);
    if (relative === 'model3d.html') body = Buffer.from(body.toString().replace('</head>', '<link rel="stylesheet" href="/_elevhub/projekter/bridge.css"><script src="/_elevhub/projekter/bridge.js" defer></script></head>').replace('<body>', '<body><div id="elevhub" hidden></div>'));
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; connect-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; object-src 'none'");
    send(200, body, types[path.extname(filename)] || 'application/octet-stream');
  } catch (error) { send(500, {error:error.message}); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = 'http://127.0.0.1:' + server.address().port;
let browser;
try {
  browser = await chromium.launch({headless:true,channel:'chrome'});
  const context = await browser.newContext({viewport:{width:1024,height:768},reducedMotion:'reduce'});
  const page = await context.newPage(), errors = [], browserDialogs = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => {browserDialogs.push(dialog.type()); dialog.dismiss();});
  await page.goto(origin + '/model3d.html');
  await page.locator('#project-classroom').waitFor({timeout:8000});
  assert.equal(await page.evaluate(() => document.activeElement.id), 'project-classroom');
  await page.screenshot({path:path.join(output,'initial-metadata.png')});
  await page.locator('#project-classroom').fill('5A');
  await page.locator('#project-student').fill('Testelev');
  await page.locator('#project-title').fill('Min navnebrik');
  await page.locator('#project-start-confirm').click();
  assert.equal(saveCount, 0, 'starting must not save an empty project');
  const ready = () => page.waitForFunction(() => !document.querySelector('#download').disabled);
  const snapshot = () => page.evaluate(() => window.ElevhubProjectAdapter.signature());
  await page.getByRole('button',{name:'Kasse',exact:true}).click(); await ready();
  await page.locator('[data-size="0"]').fill('70'); await page.locator('[data-size="0"]').press('Enter'); await ready();
  await page.getByRole('button',{name:'Tekst',exact:true}).click(); await ready();
  await page.locator('#text-value').fill('ALMA'); await page.locator('#text-value').press('Enter'); await ready();
  await page.locator('#svg-file').setInputFiles({name:'synthetic.svg',mimeType:'image/svg+xml',buffer:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>')});
  await page.waitForFunction(() => document.querySelectorAll('#object-list button').length === 3); await ready();
  const beforeAlt = await snapshot();
  await page.locator('#workspace').focus(); await page.keyboard.press('Alt+ArrowLeft');
  assert.equal(await snapshot(), beforeAlt, 'Alt+Back must not move the selected figure');
  await page.locator('#elevhub-project-save').click();
  await page.waitForFunction(() => !document.querySelector('#elevhub-project-bar .error'));
  assert.equal(saveCount, 1);
  const saved = [...records.values()][0];
  assert.equal(saved.folder, '5A · Testelev'); assert.equal(saved.name, 'Min navnebrik'); assert.match(saved.filename,/\.mkv3d$/);
  assert(saved.bytes.length > 100, 'editable scene with imported assets must be stored');
  const layout = await page.evaluate(() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,canvas:document.querySelector('#canvas').getBoundingClientRect().bottom,status:document.querySelector('.status-bar').getBoundingClientRect().bottom,height:innerHeight}));
  assert(layout.scrollWidth <= layout.width, 'common bar must not cause horizontal overflow');
  assert(layout.canvas <= layout.height && layout.status <= layout.height, 'canvas/status must remain visible below common project bar');
  await page.screenshot({path:path.join(output,'editable-saved.png')});
  await page.setViewportSize({width:1024,height:688});
  assert(await page.locator('.status-bar').evaluate(element => element.getBoundingClientRect().bottom <= innerHeight), 'status must also fit below the native kiosk bar');
  await page.screenshot({path:path.join(output,'compact-saved.png')});
  await page.setViewportSize({width:1024,height:768});
  console.log('3D metadata, editable save/imported asset and 1024x768 layout: OK');

  await page.getByRole('button',{name:'Kugle',exact:true}).click(); await ready();
  const unsaved = await snapshot(); failSave = true;
  await page.locator('#elevhub-project-save').click();
  await page.locator('#elevhub-project-bar .error').waitFor();
  assert.equal(await snapshot(), unsaved); assert.equal(saveCount,1);
  await page.screenshot({path:path.join(output,'save-error-preserved.png')});
  const finish = async action => {
    request = {id:randomUUID().replaceAll('-',''),state:'pending'};
    await page.locator('#project-finish-continue').waitFor({timeout:5000});
    if (action) await page.locator(action).click(); else await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#elevhub-project-dialog')?.open);
    for (let i=0;i<100 && request.state==='pending';i++) await new Promise(resolve=>setTimeout(resolve,25));
    assert.notEqual(request.state,'pending','finish request must receive an acknowledgement');
    return request.state;
  };
  assert.equal(await finish('#project-finish-continue'), 'cancelled');
  assert.equal(await snapshot(), unsaved);
  assert.equal(await finish(null), 'cancelled', 'Escape must preserve the work');
  assert.equal(await snapshot(), unsaved);
  assert.equal(await finish('#project-finish-save'), 'cancelled', 'save failure must block finish');
  assert.equal(await snapshot(), unsaved);
  failSave = false;
  assert.equal(await finish('#project-finish-save'), 'completed');
  assert.equal(saveCount,2); assert.equal(await page.locator('#object-list button').count(),0);
  await page.reload(); await page.locator('#project-classroom').waitFor();
  assert.equal(await page.locator('#project-student').inputValue(),'');
  await page.locator('#project-start-open').click();
  const shelf = page.frameLocator('iframe[title="Projektarkiv"]');
  await shelf.locator('article.project .open-project').click();
  await page.locator('#elevhub-project-dialog').waitFor({state:'hidden'}); await ready();
  assert.equal(await snapshot(), unsaved, 'reopened model must retain editable scene and imported mesh');
  assert.equal(await page.locator('#object-list button').count(),4);
  await page.screenshot({path:path.join(output,'editable-reopened.png')});
  await page.setViewportSize({width:1024,height:688});
  await page.locator('#select-all').click();
  assert.equal(await page.locator('#object-list button[aria-pressed=true]').count(),4, 'visible select-all must select reopened objects');
  // The compact parts list is intentionally keyboard-only until focus enters it.
  await page.getByRole('button',{name:'Rør',exact:true}).focus();
  await page.keyboard.press('Tab'); await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => Boolean(document.activeElement.closest('#object-list'))),true);
  await page.keyboard.press('Enter'); await ready();
  assert.equal(await page.locator('[data-size="0"]').inputValue(),'70');
  await page.locator('[data-size="0"]').fill('72');
  await page.locator('[data-size="0"]').press('Enter'); await ready();
  assert.equal(await page.locator('[data-size="0"]').inputValue(),'72', 'reopened cube measurements remain editable at kiosk height');
  await page.screenshot({path:path.join(output,'reopened-edit-compact.png')});
  await page.getByRole('button',{name:'Cylinder',exact:true}).click(); await ready();
  assert.equal(await finish('#project-finish-discard'), 'completed');
  assert.equal(saveCount,2); assert.equal(await page.locator('#object-list button').count(),0);
  await page.reload(); await page.locator('#project-classroom').waitFor();
  for (const id of ['project-classroom','project-student','project-title']) assert.equal(await page.locator('#'+id).inputValue(),'');
  assert.deepEqual(browserDialogs, [], 'approved finish/reload must never trigger native beforeunload');
  assert.deepEqual(errors, []);
  console.log('3D save failure, finish cancel/save/discard, editable reopen and clean next pupil: OK');
  console.log('Screenshots: '+output);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
