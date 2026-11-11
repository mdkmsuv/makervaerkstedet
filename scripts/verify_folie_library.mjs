import {createServer} from 'node:http';
import {readFile, mkdtemp, rm, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {chromium} = require('playwright');
const root = process.cwd();
const checks = [];
const pass = (name, value) => { if (!value) throw Error(name); checks.push(name); console.log(`PASS ${name}`); };
const ids = Array.from({length: 25}, (_, i) => (i + 1).toString(16).padStart(64, '0'));
const vinylSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50"><path d="M5 5H45V45H5Z M15 15H35V35H15Z" fill="none" fill-rule="evenodd"/><path d="M60 8L90 42" fill="none"/></svg>';
const laserSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50"><path d="M5 5H45V45H5Z" data-operation="engrave-fill"/><path d="M60 8L90 42" data-operation="engrave-line"/></svg>';
const items = ids.map((id, i) => ({id, title: `Motiv ${String(i + 1).padStart(2, '0')}`, category: i % 2 ? 'Former' : 'Dyr'}));
let version = 1;
let offline = false;
let blockVinylFile = false;
let blockedRequest = false;
let releaseBlockedFile;

const json = (response, status, body) => { response.writeHead(status, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}); response.end(JSON.stringify(body)); };
const server = createServer(async (request, response) => {
  const url = new URL(request.url || '/', 'http://localhost');
  const isLibrary = url.pathname.startsWith('/api/folie/library') || url.pathname.startsWith('/api/laser/library');
  const workshop = url.pathname.startsWith('/api/folie/') ? 'folie' : 'laser';
  if (isLibrary) {
    if (url.pathname.endsWith('/status')) { if (offline) return json(response, 503, {error: 'offline'}); return json(response, 200, {enabled: true, version}); }
    if (url.pathname === `/api/${workshop}/library`) { if (offline) return json(response, 503, {error: 'offline'}); return json(response, 200, {version, items}); }
    const match = url.pathname.match(/\/file\/([0-9a-f]{64})$/);
    if (match) {
      if (offline) return json(response, 503, {error: 'offline'});
      if (!ids.includes(match[1])) return json(response, 404, {error: 'not found'});
      if (workshop === 'folie' && blockVinylFile && !blockedRequest) {
        blockedRequest = true;
        await new Promise(resolve => { releaseBlockedFile = resolve; });
      }
      return json(response, 200, {version, svg: workshop === 'folie' ? vinylSvg : laserSvg});
    }
  }
  const route = url.pathname === '/folie.html' ? '/laser.html' : url.pathname;
  const webRoot = path.join(root, 'web') + path.sep;
  const file = path.resolve(root, 'web', `.${route}`);
  if (!file.startsWith(webRoot)) return response.writeHead(403).end();
  try {
    const ext = path.extname(file);
    const type = ext === '.html' ? 'text/html; charset=utf-8' : ext === '.js' ? 'text/javascript; charset=utf-8' : ext === '.css' ? 'text/css; charset=utf-8' : ext === '.svg' ? 'image/svg+xml' : 'application/octet-stream';
    response.setHeader('Content-Type', type);
    response.end(await readFile(file));
  } catch { response.writeHead(404).end(); }
});

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const profile = await mkdtemp(path.join(root, '.runtime', 'folie-library-review-'));
const browser = await chromium.launchPersistentContext(profile, {headless: true, channel: 'chrome'});
const page = browser.pages()[0] || await browser.newPage();
page.setDefaultTimeout(12000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const base = `http://127.0.0.1:${server.address().port}`;
const ready = () => page.waitForFunction(() => window.ElevhubProjectAdapter && document.querySelector('#save-status')?.textContent === 'Kladde på denne computer');
const state = () => page.evaluate(() => JSON.parse(localStorage.getItem('kloster-folievaerksted-v1')));
const load = async value => { await page.evaluate(value => localStorage.setItem('kloster-folievaerksted-v1', JSON.stringify(value)), value); await page.reload(); await ready(); };
const download = async selector => { const event = page.waitForEvent('download'); await page.locator(selector).click(); const file = await event; return readFile(await file.path(), 'utf8'); };

try {
  await page.goto(`${base}/folie.html`);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await ready();
  const openLevels = async () => { const details = page.locator('.vinyl-tool-levels'); if (!(await details.getAttribute('open'))) await details.locator('summary').click(); };
  pass('vinyl UI has build/draw/more tabs and hides laser-only tools', await page.locator('#vinyl-tool-tab-build, #vinyl-tool-tab-draw, #vinyl-tool-tab-more').count() === 3 && await page.locator('[data-op="engrave"]').isHidden() && await page.locator('#add-gear').isHidden() && await page.locator('.box-generator').isHidden());
  await openLevels();
  for (const level of ['0', '1', '2', '3']) {
    await page.locator(`[data-level="${level}"]`).click();
    for (const tab of ['build', 'draw', 'more']) {
      await page.locator(`#vinyl-tool-tab-${tab}`).click();
      pass(`vinyl ${tab} tab level ${level} keeps box gear engrave unavailable`, await page.locator('[data-op="engrave"]').isHidden() && await page.locator('#add-gear').isHidden() && await page.locator('.box-generator').isHidden());
    }
  }
  await page.locator('#vinyl-tool-tab-build').click();
  await page.locator('#library-button').click();
  await page.waitForFunction(() => document.querySelector('#library-dialog')?.open === true);
  await page.waitForFunction(() => document.querySelectorAll('#library-grid article').length === 12);
  pass('folie library loads categories and first page', await page.locator('#library-category option').count() === 3 && (await page.locator('#library-page').innerText()).includes('Side 1 af 3'));
  await page.locator('#library-category').selectOption('Dyr');
  pass('category filter narrows library', await page.locator('#library-grid article').count() === 12 && (await page.locator('#library-page').innerText()).includes('13 figurer'));
  await page.locator('#library-search').fill('Motiv 25');
  pass('search finds one item', await page.locator('#library-grid article').count() === 1 && (await page.locator('#library-page').innerText()).includes('1 figurer'));
  await page.locator('#library-search').fill('findes-ikke');
  pass('not found search gives Danish empty text', (await page.locator('#library-grid').innerText()).includes('Ingen figurer matcher'));
  await page.locator('#library-search').fill(''); await page.locator('#library-category').selectOption(''); await page.locator('#library-next').click();
  pass('library paging reaches second page', (await page.locator('#library-page').innerText()).includes('Side 2 af 3') && await page.locator('#library-grid article').count() === 12);
  await page.locator('#library-prev').click();

  const seed = {version: 1, name: 'Bevar denne tegning', mat: 'long', width: 296, height: 603, items: [{id: 'old', name: 'Eksisterende', d: 'M20 20H40V40H20Z', op: 'cut'}]};
  await page.locator('[data-close="library-dialog"]').first().click();
  await load(seed);
  await page.locator('#vinyl-tool-tab-build').click(); await page.locator('#library-button').click(); await page.waitForFunction(() => document.querySelector('#library-dialog')?.open);
  await page.locator('#library-grid article').first().locator('button').click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('kloster-folievaerksted-v1')).items.length === 3);
  const afterInsert = await state();
  pass('synthetic hole and open path append as cut and preserve drawing/mat', afterInsert.mat === 'long' && afterInsert.width === 296 && afterInsert.height === 603 && afterInsert.items[0].id === 'old' && afterInsert.items.slice(1).every(item => item.op === 'cut'));
  pass('library insertion scales and centers on the mat', (() => { const ds = afterInsert.items.slice(1).map(item => item.d); return ds[0].startsWith('M103,281.5') && ds[1].startsWith('M158,284.5'); })());
  pass('inserted library items have unique ids and a shared group id', afterInsert.items[1].id !== afterInsert.items[2].id && afterInsert.items[1].group && afterInsert.items[1].group === afterInsert.items[2].group);
  await page.locator('[data-close="library-dialog"]').first().click(); await page.locator('#undo').click();
  pass('undo removes the complete library insertion', (await state()).items.length === 1);
  await page.locator('#redo').click();
  pass('redo restores the complete library insertion', (await state()).items.length === 3);
  await page.locator('#preview-button').click();
  pass('vinyl preview reports open cut strokes', (await page.locator('#preview-issues').innerText()).includes('Åbne streger'));
  await page.locator('[data-close="preview-dialog"]').first().click();
  const brother = await download('#export-button');
  const exportInfo = await page.evaluate(source => { const doc = new DOMParser().parseFromString(source, 'image/svg+xml'); const root = doc.documentElement; return {width: root.getAttribute('width'), height: root.getAttribute('height'), paths: root.querySelectorAll('path').length, forbidden: root.querySelectorAll('image,text,g,metadata,style,script').length, engrave: source.includes('engrave')}; }, brother);
  pass('Brother export is physical mm plain paths without engrave or metadata', exportInfo.width === '296mm' && exportInfo.height === '603mm' && exportInfo.paths === 3 && exportInfo.forbidden === 0 && !exportInfo.engrave);

  blockVinylFile = false; blockedRequest = false; version = 1;
  await page.locator('#library-button').click(); await page.waitForFunction(() => document.querySelector('#library-dialog')?.open);
  await page.waitForFunction(() => [...document.querySelectorAll('#library-grid article img')].filter(img => img.src).length >= 12);
  blockVinylFile = true;
  const beforeBlocked = await state();
  const addPromise = page.locator('#library-grid article').first().locator('button').click();
  while (!blockedRequest) await new Promise(resolve => setTimeout(resolve, 20));
  version = 2; releaseBlockedFile?.(); await addPromise; await new Promise(resolve => setTimeout(resolve, 100));
  pass('access revocation during file fetch does not insert', (await state()).items.length === beforeBlocked.items.length);
  blockVinylFile = false; await page.locator('[data-close="library-dialog"]').first().click();

  offline = true; await page.reload(); await ready();
  await page.locator('[data-level="1"]').click(); await page.locator('[data-add="circle"]').click();
  pass('offline mode still permits local figures', (await state()).items.length >= 2);
  const savedOffline = await download('#save-project');
  pass('offline mode still permits local project save', savedOffline.includes('kloster-folievaerksted-v1') || savedOffline.includes('Bevar denne tegning'));

  await page.goto(`${base}/laser.html`); await page.evaluate(() => localStorage.clear()); await page.reload(); await ready();
  await page.locator('#library-button').click(); await page.waitForFunction(() => document.querySelector('#library-dialog')?.open);
  await page.locator('#library-grid article').first().locator('button').click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')).items.length === 2);
  const laserItems = await page.evaluate(() => JSON.parse(localStorage.getItem('kloster-laservaerksted-v1')).items);
  pass('laser library keeps engrave fill and line behavior', laserItems.some(item => item.op === 'engrave-fill') && laserItems.some(item => item.op === 'engrave-line'));
  if (errors.length) throw Error(`browser errors: ${errors.join('; ')}`);
  await writeFile(path.join(root, '.runtime', 'folie-library-results.json'), JSON.stringify({checks, errors}, null, 2));
  console.log(JSON.stringify({checks: checks.length, errors}));
} finally {
  await browser.close(); await new Promise(resolve => server.close(resolve));
  const profilePath = path.resolve(profile), runtimeRoot = path.resolve(root, '.runtime') + path.sep;
  if (!profilePath.startsWith(runtimeRoot) || !path.basename(profilePath).startsWith('folie-library-review-')) throw Error('Unexpected test profile path');
  await rm(profilePath, {recursive: true, force: true});
}
