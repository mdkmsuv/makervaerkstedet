// Isolated finish_fixture browser regression for E2-6/E2-7.
// The fixture uses temporary project storage and never contacts a school server.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
function loadPlaywright() {
  const candidates = [process.env.PLAYWRIGHT_MODULE, 'playwright', path.join(root, 'node_modules', 'playwright')].filter(Boolean);
  let last;
  for (const candidate of candidates) {
    try { return require(candidate); } catch (error) { last = error; }
  }
  throw new Error(`Playwright is required for the browser E2 test. Install it in node_modules or set PLAYWRIGHT_MODULE. ${last?.message || ''}`);
}
const {chromium} = loadPlaywright();
const python = process.env.PYTHON || [
  path.join(root, '.venv', 'Scripts', 'python.exe'),
  path.join(root, '.venv', 'bin', 'python'),
  'python3', 'python'
].find(candidate => candidate === 'python3' || candidate === 'python' || fs.existsSync(candidate));
const fixture = spawn(python, ['-u', path.join(root, 'linux-elevhub/tests/finish_fixture.py')],
  {stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true});
let browser;
let workshopPorts;

function stopFixture() {
  if (!fixture.stdin.destroyed) fixture.stdin.end();
  if (!fixture.killed) fixture.kill();
}

async function waitForWorkshop(page, viewport) {
  await page.setViewportSize(viewport);
  await page.goto(`http://127.0.0.1:${workshopPorts.laser}/laser.html`);
  await page.locator('#drawing').waitFor();
  await page.waitForFunction(() => document.querySelector('#drawing')?.getScreenCTM());
  await page.waitForTimeout(500);
  await page.evaluate(() => document.querySelector('#elevhub-project-dialog')?.close());
  await page.waitForFunction(() => !document.querySelector('#elevhub-project-dialog')?.open);
}

async function openToolLevels(page) {
  const levels = page.locator('.laser-tool-levels');
  if (await levels.count() && !(await levels.evaluate(node => node.open))) {
    await levels.locator('summary').click();
  }
}

async function screenPoint(page, id, fraction = 0.5) {
  return page.evaluate(({id, fraction}) => {
    const svg = document.querySelector('#drawing');
    const path = document.querySelector(`.design-item[data-id="${CSS.escape(id)}"] .visible-shape > *`);
    const b = path.getBBox();
    return new DOMPoint(b.x + b.width * fraction, b.y + b.height * fraction)
      .matrixTransform(svg.getScreenCTM());
  }, {id, fraction});
}

function fixturePorts() {
  return new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('finish fixture startup timed out')), 15000);
    fixture.stdout.on('data', chunk => {
      output += chunk;
      for (const line of output.split(/\r?\n/)) {
        try {
          const value = JSON.parse(line);
          if (value.laser) {
            clearTimeout(timer);
            resolve(value);
            return;
          }
        } catch (_) { /* wait for the JSON startup line */ }
      }
    });
    fixture.once('exit', code => {
      clearTimeout(timer);
      reject(new Error(`finish fixture exited before startup (${code})`));
    });
  });
}

async function main() {
  workshopPorts = await fixturePorts();
  const launchOptions = {headless: true};
  const channel = process.env.PLAYWRIGHT_BROWSER_CHANNEL ||
    (process.platform === 'win32' ? 'chrome' : '');
  if (channel) launchOptions.channel = channel;
  browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({viewport: {width: 1366, height: 900}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await waitForWorkshop(page, {width: 1366, height: 900});
  // The editor's real project chooser is a same-origin iframe.  Exercise the
  // browser boundary and response CSP, rather than only inspecting source.
  const pickerResponse = page.waitForResponse(response =>
    response.url().includes('/_elevhub/projekter/?pick=laser'));
  await page.locator('#import-button').click();
  const picker = await pickerResponse;
  assert.match(picker.headers()['content-security-policy'] || '', /frame-ancestors 'self'/);
  const projectFrame = page.frameLocator('#elevhub-project-dialog iframe');
  await projectFrame.locator('h1').waitFor({state: 'attached'});
  assert.match(await projectFrame.locator('h1').textContent(), /Dine projekter/);
  await page.evaluate(() => document.querySelector('#elevhub-project-dialog')?.close());
  await page.waitForFunction(() => !document.querySelector('#elevhub-project-dialog')?.open);

  for (let i = 0; i < 5; i++) await page.locator('[data-add="rect"]').click();
  for (const kind of ['circle', 'heart', 'star']) await page.locator(`[data-add="${kind}"]`).click();
  const geometry = await page.evaluate(() => {
    const paths = [...document.querySelectorAll('.design-item .visible-shape > *')]
      .map(node => { const b = node.getBBox(); return {x: b.x, y: b.y, w: b.width, h: b.height}; });
    const overlap = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) *
      Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
    const first = paths[0], svg = document.querySelector('#drawing');
    const point = new DOMPoint(first.x + first.w / 2, first.y + first.h / 2)
      .matrixTransform(svg.getScreenCTM());
    return {
      paths,
      hit: document.elementFromPoint(point.x, point.y)?.closest('.design-item')?.dataset.id,
      overlaps: paths.flatMap((a, i) => paths.slice(i + 1).map(b => overlap(a, b)))
    };
  });
  assert.equal(geometry.paths.length, 8);
  assert(geometry.hit, 'clicking the closed shape interior must hit its design item');
  assert(geometry.paths.every(b => b.x >= 0 && b.y >= 0 && b.x + b.w <= 180 && b.y + b.h <= 120));
  assert(geometry.overlaps.every(value => value === 0), `new shapes overlap: ${JSON.stringify(geometry)}`);

  const firstId = await page.locator('.design-item').first().getAttribute('data-id');
  const box = node => { const b = node.getBBox(); return {x: b.x, y: b.y, width: b.width, height: b.height}; };
  const before = await page.locator(`.design-item[data-id="${firstId}"] .visible-shape > *`).evaluate(box);
  const closedPoint = await screenPoint(page, firstId);
  await page.mouse.click(closedPoint.x, closedPoint.y);
  assert.equal(await page.locator(`.object-row[data-id="${firstId}"]`).getAttribute('aria-pressed'), 'true');
  await page.mouse.move(closedPoint.x, closedPoint.y);
  await page.mouse.down();
  await page.mouse.move(closedPoint.x + 18, closedPoint.y + 12);
  await page.mouse.up();
  const after = await page.locator(`.design-item[data-id="${firstId}"] .visible-shape > *`).evaluate(box);
  assert(after.x !== before.x || after.y !== before.y, 'closed shape should be draggable through its hit area');

  // Open curves retain a broad stroke hit area while their visible stroke
  // remains unfilled, so a pupil can select and move a line without changing
  // the exported operation.
  await openToolLevels(page);
  await page.locator('[data-level="1"]').click();
  await page.locator('#laser-tool-tab-draw').click();
  await page.locator('[data-tool="line"]').click();
  const drawingBox = await page.locator('#drawing').boundingBox();
  await page.mouse.move(drawingBox.x + drawingBox.width * .25, drawingBox.y + drawingBox.height * .25);
  await page.mouse.down();
  await page.mouse.move(drawingBox.x + drawingBox.width * .55, drawingBox.y + drawingBox.height * .4);
  await page.mouse.up();
  const open = page.locator('.design-item').last();
  const openId = await open.getAttribute('data-id');
  assert.equal(await open.locator('.hit-shape path').getAttribute('data-open-path'), '1');
  assert.equal(await open.getAttribute('data-op'), 'engrave-line');
  assert.equal(await open.locator('.visible-shape path').getAttribute('fill'), 'none',
    'open curve must remain an unfilled engraving line for export');
  const openPoint = await screenPoint(page, openId);
  const openHit = await page.evaluate(({x, y}) => document.elementFromPoint(x, y)?.closest('.design-item')?.dataset.id, openPoint);
  assert.equal(openHit, openId, 'open curve hit area should be reachable');

  // The guide navigation must remain reachable after scrolling at every
  // supported desktop width, including the narrow 1150px breakpoint.
  for (const width of [1150, 1280, 1366, 1920]) {
    const guidePage = await browser.newPage({viewport: {width, height: 700}});
    await waitForWorkshop(guidePage, {width, height: 700});
    await guidePage.locator('#laser-projects-button').click();
    await guidePage.locator('#laser-project-cards button').first().click();
    const next = guidePage.locator('#laser-guide-next');
    await next.scrollIntoViewIfNeeded();
    assert(await next.isVisible(), `guide next button must be reachable at ${width}px`);
    const box = await next.boundingBox();
    assert(box && box.y >= 0 && box.y + box.height <= 700, `guide button is outside viewport at ${width}px`);
    await guidePage.close();
  }

  // A valid box preview is discarded on a subsequent invalid generation.
  await openToolLevels(page);
  await page.locator('[data-level="3"]').click();
  await page.locator('#laser-tool-tab-more').click();
  await page.route('**/api/laser/boxes', async route => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({svg: '<svg viewBox="0 0 40 40" width="40" height="40"><path d="M0,0 L30,0 L30,30 Z"/></svg>', details: {}})}));
  await page.locator('#box-button').click();
  await page.waitForFunction(() => document.querySelector('#box-insert')?.disabled === false);
  assert(await page.locator('#box-preview svg').count(), 'valid box response should render a preview');
  await page.locator('#box-width').fill('10');
  await page.locator('#box-generate').click();
  await page.waitForFunction(() => document.querySelector('#box-preview')?.childElementCount === 0);
  assert(await page.locator('#box-status.error').count(), 'invalid box generation should show an error');

  // A late failure from an obsolete request must not erase the newer preview.
  await page.unroute('**/api/laser/boxes');
  await page.locator('#box-width').fill('20');
  let boxCalls = 0;
  let firstEnteredResolve;
  let releaseFirst;
  const firstEntered = new Promise(resolve => { firstEnteredResolve = resolve; });
  const firstRelease = new Promise(resolve => { releaseFirst = resolve; });
  await page.route('**/api/laser/boxes', async route => {
    boxCalls += 1;
    if (boxCalls === 1) {
      firstEnteredResolve();
      await firstRelease;
      return route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({error: 'obsolete'})});
    }
    return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
      svg: '<svg viewBox="0 0 40 40" width="40" height="40"><path d="M0,0 L30,0 L30,30 Z"/></svg>', details: {}
    })});
  });
  await page.locator('#box-generate').click();
  await firstEntered;
  await page.locator('#box-generate').click();
  await page.waitForFunction(() => document.querySelector('#box-insert')?.disabled === false);
  releaseFirst();
  await page.waitForTimeout(100);
  assert(await page.locator('#box-preview svg').count(), 'late obsolete response must not remove current preview');
  await page.unroute('**/api/laser/boxes');

  await page.route('**/api/laser/boxes', route => route.abort());
  await page.locator('#box-generate').click();
  await page.waitForFunction(() => document.querySelector('#box-status.error') &&
    document.querySelector('#box-preview')?.childElementCount === 0);
  await page.unroute('**/api/laser/boxes');

  // Loading a full but valid draft exercises the production 500-part guard.
  const capPage = await browser.newPage({viewport: {width: 1366, height: 900}});
  await capPage.goto(`http://127.0.0.1:${workshopPorts.laser}/laser.html`);
  await capPage.waitForFunction(() => document.querySelector('#drawing')?.getScreenCTM());
  await capPage.waitForTimeout(500);
  await capPage.evaluate(() => document.querySelector('#elevhub-project-dialog')?.close());
  await capPage.locator('[data-add="rect"]').click();
  await capPage.evaluate(() => {
    const button = document.querySelector('#duplicate');
    for (let i = 0; i < 499; i += 1) button.click();
  });
  await capPage.waitForFunction(() => document.querySelector('#drawing')?.getScreenCTM());
  assert.equal(await capPage.locator('.design-item').count(), 500, 'duplicate should reach the production 500-part cap');
  await capPage.locator('[data-add="rect"]').click();
  await capPage.waitForTimeout(200);
  assert.match(await capPage.locator('#notice').innerText(), /500/);
  await capPage.close();

  assert.deepEqual(errors, []);
  console.log('PASS isolated browser geometry, guide reachability and box preview recovery');
}

main().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => {
    if (browser) await browser.close();
    stopFixture();
  });
