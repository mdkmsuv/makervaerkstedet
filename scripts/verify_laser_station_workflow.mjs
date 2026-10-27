// Headless regression checks for the teacher operated laser station.
// Uses a private static server and Playwright route mocks only.
import { createServer } from 'node:http';
import { readFile, mkdtemp, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = process.cwd();
const checks = [];
const errors = [];
const assert = (value, message) => { if (!value) throw Error(message); };
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg' };
const readWeb = async (pathname) => {
  const file = path.resolve(root, 'web', `.${pathname}`);
  const webRoot = path.resolve(root, 'web') + path.sep;
  if (!file.startsWith(webRoot)) throw Error('unsafe static path');
  return readFile(file);
};

const server = createServer(async (request, response) => {
  const pathname = new URL(request.url || '/', 'http://localhost').pathname;
  try {
    if (pathname === '/megavaerkstedet.html') {
      const [mega, laser] = await Promise.all([readFile(path.resolve(root, 'web/megavaerkstedet.html'), 'utf8'), readFile(path.resolve(root, 'web/laserstation.html'), 'utf8')]);
      const body = laser.match(/<body\b[^>]*>([\s\S]*?)<\/body\s*>/i)?.[1]?.replace(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi, '') || '';
      response.setHeader('Content-Type', 'text/html');
      response.end(mega.replace('<!--LASER_BODY-->', body).replace('<!--MEGA_SETTINGS-->', JSON.stringify({ printUrl: '' })));
      return;
    }
    response.setHeader('Content-Type', mime[path.extname(pathname)] || 'application/octet-stream');
    response.end(await readWeb(pathname === '/' ? '/megavaerkstedet.html' : pathname));
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = await mkdtemp(path.join(root, '.runtime', 'laser-station-check-'));
const browser = await chromium.launchPersistentContext(profile, { headless: true, channel: 'chrome' });
const page = browser.pages()[0] || await browser.newPage();
page.setDefaultTimeout(12000);
page.on('pageerror', error => errors.push(error.message));
page.on('requestfailed', request => errors.push(`request: ${request.url()} ${request.failure()?.errorText || ''}`));
const apiRequests = [];
page.on('request', request => { if (request.url().includes('/api/station/')) apiRequests.push(`${request.method()} ${new URL(request.url()).pathname}`); });

let loggedIn = false;
let doneAttempts = 0;
let lastSaved = false, finished = false;
const doneBodies = [];
let directAvailable = false;
const job = { id: 'job-1', student: 'Asta', classroom: '6A', project: 'Skilt', created: '2026-10-09T08:00:00Z', status: 'queued', needsChoice: false, images: [], operations: { cut: 1, engrave: 0 } };
const stationState = () => ({ loggedIn, server: 'Teststation', presetSource: 'test-presets, 2026', bed: { machine: 'Beambox II', width: 600, height: 375 }, materials: [{ id: 'birch', name: 'Krydsfiner', cut: true }], directTransfer: { available: directAvailable, message: directAvailable ? 'Klar til overførsel.' : 'Åbn filen i Beam Studio.' }, materialScene: null, lastPlate: lastSaved && !finished ? { ids: ['job-1'], names: ['Asta (6A), Skilt'] } : null });

try {
  await page.route('**/api/station/**', async route => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const body = method === 'POST' ? JSON.parse(route.request().postData() || '{}') : {};
    let payload = {};
    let status = 200;
    if (url.pathname === '/api/station/state') payload = stationState();
    else if (url.pathname === '/api/station/login') { loggedIn = true; payload = { ok: true }; }
    else if (url.pathname === '/api/station/jobs') payload = { jobs: loggedIn && !finished ? [job] : [] };
    else if (url.pathname === '/api/station/jobs/job-1/geometry') payload = { width: 50, height: 30, bbox: [0, 0, 50, 30], operations: ['cut'], parts: [{ d: 'M0 0 H50 V30 H0 Z', op: 'cut', fillRule: 'evenodd' }], images: [] };
    else if (url.pathname === '/api/station/plate/check') payload = { problems: [], flux: { seconds: 12 } };
    else if (url.pathname === '/api/station/plate/approve') payload = { approvalToken: 'approval-1' };
    else if (url.pathname === '/api/station/transfer/prepare') { directAvailable = true; payload = { transferId: 'transfer-1', directTransfer: { available: true, message: 'Klar til overførsel.' }, seconds: 12 }; }
    else if (url.pathname === '/api/station/transfer/send') { lastSaved = true; payload = { sent: true }; }
    else if (url.pathname === '/api/station/plate/open') { lastSaved = true; payload = { opened: true, file: 'C:/laser/job-1.svg' }; }
    else if (url.pathname === '/api/station/plate/done') {
      doneAttempts += 1; doneBodies.push(body);
      if (doneAttempts === 1) { status = 500; payload = { error: 'Midlertidig fejl' }; }
      else { finished = true; payload = { marked: body.ids.length }; }
    } else if (url.pathname === '/api/station/material/regions') payload = { scene: null };
    else payload = {};
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
  });

  const check = async (name, fn) => { await fn(); checks.push(name); console.log(`PASS ${name}`); };
  await page.goto(`${base}/megavaerkstedet.html#laser`);
  await page.waitForFunction(() => document.querySelector('#login-panel')?.hidden === false);

  await check('teacher login exposes next-step guidance and help dialog', async () => {
    await page.locator('#login-code').fill('test-code');
    await page.locator('#login-form').press('Enter');
    await page.waitForFunction(() => document.querySelector('#station')?.hidden === false);
    await page.locator('#refresh').click();
    assert(await page.locator('#next-title').textContent().then(x => x.trim()), 'next-step title is empty');
    await page.locator('#station-help').click();
    assert(await page.locator('#station-help-dialog').evaluate(node => node.open), 'help dialog did not open');
    await page.locator('[data-station-close="station-help-dialog"]').first().click();
  });

  await check('empty queue, optional photo controls, and responsive width are usable', async () => {
    await page.waitForFunction(() => document.querySelectorAll('#job-list .job').length === 1 || document.querySelector('#queue-error')?.textContent);
    assert(await page.locator('#job-list .job').count() === 1, `mock queue job missing: ${await page.locator('#queue-error').textContent()}`);
    assert(await page.locator('#photo-options').getAttribute('open') === null, 'optional photo options are expanded by default');
    assert(await page.locator('#analyze-material').count() === 0, 'removed analysis control is still present');
    assert(await page.locator('#material-photo').count() === 1, 'manual photo input is missing');
    assert(await page.locator('#material-note').textContent().then(text => text.includes('Tegn')), 'manual material guidance is missing');
    assert(await page.locator('.preset-details').first().getAttribute('open') === null, 'advanced preset details are expanded by default');
    for (const [width, height] of [[1366, 768], [1024, 768], [390, 844]]) {
      await page.setViewportSize({ width, height });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `horizontal overflow at ${width}px`);
    }
  });

  await check('selecting a queued job advances through check and approval', async () => {
    await page.locator('#job-list input[type="checkbox"]').check();
    await page.waitForTimeout(300);
    await page.waitForFunction(() => document.querySelector('#check')?.disabled === false);
    await page.locator('#check').click();
    await page.waitForFunction(() => document.querySelector('#approve-placement')?.disabled === false);
    await page.locator('#approve-placement').click();
    await page.waitForFunction(() => document.querySelector('#prepare-transfer')?.disabled === false);
    assert((await page.locator('#next-title').textContent()).includes('Fortsæt i Beam Studio'), 'approval did not guide to available fallback');
    await page.locator('#bed .box').focus();
    await page.locator('#bed .box').press('ArrowRight');
    assert(await page.locator('#prepare-transfer').isDisabled(), 'moving a product retained approval');
    assert((await page.locator('#next-title').textContent()).includes('Kontrollér materiale'), 'moving a product did not reset next step');
    await page.locator('#approve-placement').click();
    await page.waitForFunction(() => !document.querySelector('#prepare-transfer').disabled);
  });

  await check('unavailable direct transfer leaves Beam Studio fallback visible', async () => {
    assert(await page.locator('#send-transfer').isDisabled(), 'send should be disabled when direct transfer is unavailable');
    assert(await page.locator('#open').isEnabled(), 'Beam Studio fallback should be enabled');
    await page.locator('#open').click();
    await page.waitForFunction(() => document.querySelector('#open-result')?.textContent.includes('Beam Studio'));
    await page.locator('#prepare-transfer').click();
    await page.waitForFunction(() => document.querySelector('#send-transfer')?.disabled === false);
    assert((await page.locator('#next-title').textContent()).includes('Overfør den klargjorte fil'), 'prepared file did not become next step');
    await page.locator('#send-transfer').click();
    await page.waitForFunction(() => document.querySelector('#transfer-result')?.textContent.includes('sendt'));
    await page.waitForFunction(() => !document.querySelector('#next-action').disabled && document.querySelector('#next-title').textContent.includes('Kontrollér og start'));
  });

  await check('completion requires review, captures immutable ids, and retries after failure', async () => {
    await page.locator('#mark-done').click();
    assert(await page.locator('#complete-dialog').evaluate(node => node.open), 'completion review did not open');
    assert(await page.locator('#complete-list').textContent().then(x => x.includes('Asta')), 'completion review omitted job name');
    await page.locator('[data-station-close="complete-dialog"]').first().click();
    assert(doneAttempts === 0, 'cancel sent completion request');
    await page.locator('#mark-done').click();
    await page.locator('#confirm-done').click();
    await page.waitForFunction(() => document.querySelector('#done-result')?.textContent.includes('Midlertidig fejl'));
    assert(doneBodies.length === 1 && JSON.stringify(doneBodies[0].ids) === JSON.stringify(['job-1']), 'failed completion did not capture expected ids');
    await page.locator('[data-station-close="complete-dialog"]').first().click();
    await page.locator('#mark-done').click();
    await page.locator('#confirm-done').click();
    await page.waitForFunction(() => !document.querySelector('#complete-dialog').open && document.querySelector('#station-message').textContent.includes('1 aflevering er afsluttet'));
    assert(doneAttempts === 2, 'completion retry did not reach the mock endpoint');
    assert(doneBodies.every(body => JSON.stringify(body.ids) === JSON.stringify(['job-1'])), 'retry changed completion ids');
  });

  assert(!errors.length, `browser errors: ${errors.join('; ')}`);
  const result = { passed: checks.length, checks, errors, doneBodies };
  await writeFile(process.env.LASER_STATION_CHECK_OUTPUT || path.join(root, '.runtime', 'laser-station-checks.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
  const runtimeRoot = path.resolve(root, '.runtime') + path.sep;
  const profilePath = path.resolve(profile);
  if (!profilePath.startsWith(runtimeRoot) || !path.basename(profilePath).startsWith('laser-station-check-')) throw Error('Refusing to remove a profile outside the test folder');
  await rm(profilePath, { recursive: true, force: true });
}
