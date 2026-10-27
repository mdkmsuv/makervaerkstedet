const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');
const {createRequire} = require('node:module');
const requireBundled = createRequire(__filename);
const {chromium} = requireBundled('playwright');

const ROOT = path.resolve(__dirname, '..', 'web');
let PORT = 0;
const viewports = [
  {name: 'lenovo', width: 1280, height: 656},
  {name: 'compact', width: 1024, height: 600},
  {name: 'wide', width: 1920, height: 1000},
  {name: 'narrow', width: 800, height: 600}
];
const pages = [
  {name: 'laser', url: '/laser.html'},
  {name: 'folie', url: '/folie.html'}
];
const results = [];

function contentType(file) {
  return {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'}[path.extname(file)] || 'application/octet-stream';
}
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://127.0.0.1');
  if (u.pathname.startsWith('/api/')) {
    res.writeHead(200, {'Content-Type':'application/json'}); res.end(JSON.stringify({ok:true, jobs:[]})); return;
  }
  let rel = u.pathname === '/folie.html' ? 'laser.html' : u.pathname.replace(/^\//, '') || 'laser.html';
  const file = path.resolve(ROOT, rel);
  if (!file.startsWith(ROOT) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end('Not found'); return; }
  let body = fs.readFileSync(file);
  if (rel === 'laser.html' && u.searchParams.get('embedded') === '1') {
    // The same HTML transformation as ElevhubHandler.tilpas(): one injected
    // style element, after the source head content and before </head>.
    const text = body.toString('utf8');
    body = Buffer.from(text.replace(/<\/head>/i, '<style id="elevhub">#workshop-home { display: none !important; }</style></head>'));
  }
  res.writeHead(200, {'Content-Type': contentType(file), 'Cache-Control':'no-store'}); res.end(body);
});

function assert(ok, message) { if (!ok) throw new Error(message); }
async function runCase(browser, pageDef, viewport, embedded) {
  const page = await browser.newPage({viewport});
  const errors = [];
  page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  try {
    const url = `http://127.0.0.1:${PORT}${pageDef.url}${embedded ? '?embedded=1' : ''}`;
    await page.goto(url, {waitUntil:'networkidle'});
    await page.waitForFunction(() => document.querySelector('#save-status')?.textContent.includes('browser'), null, {timeout:10000});
    const metrics = await page.evaluate(() => {
      const box = s => { const e=document.querySelector(s); if (!e) return null; const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,scrollWidth:e.scrollWidth,scrollHeight:e.scrollHeight,clientWidth:e.clientWidth,clientHeight:e.clientHeight}; };
      return {workspace:box('#workspace'), editor:box('.editor'), document:{scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,clientWidth:document.documentElement.clientWidth,clientHeight:document.documentElement.clientHeight}, headerHidden:getComputedStyle(document.querySelector('.header')).display==='none'};
    });
    assert(metrics.workspace && metrics.editor, 'workspace/editor missing');
    assert(!metrics.workspace || metrics.workspace.width > 0, 'workspace has no width');
    assert(!metrics.editor || metrics.editor.width > 0, 'editor has no width');
    assert(metrics.workspace.scrollWidth === metrics.workspace.clientWidth && metrics.workspace.scrollHeight === metrics.workspace.clientHeight, 'workspace overflows');
    assert(metrics.editor.scrollWidth === metrics.editor.clientWidth && metrics.editor.scrollHeight === metrics.editor.clientHeight, 'editor overflows');
    assert(metrics.document.scrollWidth === metrics.document.clientWidth && metrics.document.scrollHeight === metrics.document.clientHeight, 'document overflows');
    if (embedded) {
      assert(metrics.headerHidden, 'embedded header is not hidden');
      if (viewport.name === 'lenovo') assert(metrics.workspace.height >= 420, `Lenovo workspace too short: ${metrics.workspace.height}`);
      if (viewport.name === 'compact') assert(metrics.workspace.height >= 360, `compact workspace too short: ${metrics.workspace.height}`);
    }
    const visible = s => page.locator(s).isVisible();
    for (const s of ['#help-button','#undo','#save-project','[data-add="rect"]']) assert(await visible(s), `${s} is not visible`);
    assert(await visible('.studio-ideas'), 'idea button is not visible');
    await page.locator('.studio-ideas').click(); assert(await page.locator('.studio-idea-dialog').isVisible(), 'idea dialog did not open'); await page.locator('.studio-close').click();
    if (pageDef.name === 'folie') {
      assert(await page.locator('#vinyl-setup').count() === 1, 'vinyl settings missing');
      await page.locator('#vinyl-setup summary').click();
      assert(await visible('#vinyl-mat') && await visible('#vinyl-mirror'), 'vinyl settings are not accessible');
      await page.locator('#vinyl-mat').selectOption('long'); await page.waitForFunction(() => document.querySelector('#board-label')?.textContent.includes('603'));
      await page.locator('#vinyl-mat').selectOption('standard'); await page.waitForFunction(() => document.querySelector('#board-label')?.textContent.includes('298'));
      const vinylBefore = await page.evaluate(() => localStorage.getItem('kloster-folievaerksted-v1'));
      await page.locator('#vinyl-mirror').click(); await page.waitForTimeout(100);
      assert(await page.evaluate(() => localStorage.getItem('kloster-folievaerksted-v1')) !== vinylBefore, 'vinyl mirror did not change drawing');
      await page.locator('#undo').click(); await page.waitForFunction(s => localStorage.getItem('kloster-folievaerksted-v1') === s, vinylBefore);
    }
    for (let level=0; level<4; level++) { await page.locator(`[data-level="${level}"]`).click(); assert(await page.locator(`[data-level="${level}"]`).getAttribute('aria-pressed') === 'true', `level ${level} did not activate`); }
    await page.locator('[data-level="1"]').click(); await page.locator('[data-tool="polyline"]').click(); assert(await visible('[data-tool="polyline"]') && await page.locator('[data-tool="polyline"]').getAttribute('aria-pressed') === 'true', 'polyline did not activate at level 1');
    await page.locator('[data-level="3"]').click(); await page.locator('[data-tool="nodes"]').click(); assert(await visible('[data-tool="nodes"]') && await page.locator('[data-tool="nodes"]').getAttribute('aria-pressed') === 'true', 'node editor did not activate at level 3');
    const before = Number(await page.locator('#object-count').textContent());
    await page.locator('[data-add="rect"]').click();
    await page.waitForTimeout(120);
    await page.waitForFunction(n => { const t=document.querySelector('#object-count')?.textContent.trim(); return t !== '' && Number(t) > n; }, before, {timeout:5000});
    const afterAdd = Number(await page.locator('#object-count').textContent());
    assert(afterAdd > before, `rectangle was not added (${before} -> ${afterAdd})`);
    await page.locator('#undo').click();
    await page.waitForFunction(n => Number(document.querySelector('#object-count').textContent) === n, before);
    const download = page.waitForEvent('download'); await page.locator('#save-project').click(); const file = await download; assert((await file.suggestedFilename()).length > 0, 'save has no filename');
    await page.locator('#help-button').click(); assert(await page.locator('#help-dialog').isVisible(), 'help dialog did not open'); await page.locator('[data-close="help-dialog"]').first().click();
    const dims = await page.evaluate(() => ({widthVisible:!document.querySelector('#board-width').hidden, heightVisible:!document.querySelector('#board-height').hidden, overflowX:document.documentElement.scrollWidth>document.documentElement.clientWidth, overflowY:document.documentElement.scrollHeight>document.documentElement.clientHeight}));
    assert(!errors.length, errors.join('; '));
    if (pageDef.name === 'laser' && !embedded && viewport.name === 'lenovo') {
      await page.locator('[data-level="0"]').click(); await page.locator('[data-add="rect"]').click();
      await page.screenshot({path:path.resolve(__dirname,'..','.runtime','studio-laptop-lenovo-selected-rect.png'), fullPage:false});
    }
    return {page:pageDef.name, mode:embedded?'embedded':'standalone', viewport:viewport.name, metrics, dims, errors, pass:true};
  } catch (e) {
    return {page:pageDef.name, mode:embedded?'embedded':'standalone', viewport:viewport.name, errors, pass:false, failure:e.message};
  } finally { await page.close(); }
}

(async () => {
  fs.mkdirSync(path.resolve(__dirname,'..','.runtime'), {recursive:true});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  PORT = server.address().port;
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    for (const p of pages) for (const v of viewports) for (const embedded of [false,true]) results.push(await runCase(browser,p,v,embedded));
  } finally { await browser.close(); await new Promise(resolve=>server.close(resolve)); }
  const failed = results.filter(r=>!r.pass);
  console.log(JSON.stringify({total:results.length, passed:results.length-failed.length, failed:failed.length, results}, null, 2));
  process.exitCode = failed.length ? 1 : 0;
})();
