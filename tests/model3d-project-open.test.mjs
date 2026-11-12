// Integration acceptance against explicitly supplied, isolated local services.
// MODEL3D_PROJECT_SHELF_URL=http://127.0.0.1:8791/_elevhub/projekter/ node --test tests/model3d-project-open.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {chromium} from 'playwright';

const shelf = process.env.MODEL3D_PROJECT_SHELF_URL;
let browser;

function tetrahedron() {
  const vertices = [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]];
  const faces = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]];
  const bytes = Buffer.alloc(84 + faces.length * 50);
  bytes.writeUInt32LE(faces.length, 80);
  faces.forEach((face, i) => face.forEach((vertex, j) => vertices[vertex].forEach((n, k) => {
    bytes.writeFloatLE(n, 84 + i * 50 + 12 + j * 12 + k * 4);
  })));
  return bytes;
}

test.before(async () => {
  if (!shelf) return;
  assert(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(shelf).hostname),
    'Use an explicitly isolated loopback fixture');
  browser = await chromium.launch({headless: true, channel: 'chrome'});
});
test.after(async () => { await browser?.close(); });

async function openFromShelf(page, suffix) {
  await page.goto(shelf);
  const id = randomUUID().replaceAll('-', '');
  const name = 'SYNTHETIC 3D OPEN ' + suffix + ' ' + id.slice(0, 8);
  const params = new URLSearchParams({id, name, folder: 'synthetic-browser-review',
    kind: 'andet', filename: 'synthetic-tetrahedron.stl', expected: '0'});
  const response = await page.request.post(new URL('api/save?' + params, shelf).href, {
    headers: {Origin: new URL(shelf).origin, 'Content-Type': 'application/octet-stream', 'X-Elevhub-Project': '1'},
    data: tetrahedron()
  });
  assert.equal(response.status(), 200, await response.text());
  await page.reload();
  await page.locator('#search').fill(name);
  const card = page.locator('article.project').filter({has: page.getByRole('heading', {name, exact: true})});
  await card.locator('.open-project').focus();
  await page.keyboard.press('Enter');
  await page.waitForURL('**/_elevhub/projekter/open?**');
}

async function screenshot(page, name) {
  if (process.env.MODEL3D_PROJECT_SCREENSHOT_DIR) {
    await page.screenshot({path: process.env.MODEL3D_PROJECT_SCREENSHOT_DIR + '/' + name});
  }
}

test('saved STL opens from real shelf into local 3D scene', {skip: !shelf, timeout: 40000}, async () => {
  const context = await browser.newContext({viewport: {width: 1440, height: 950}});
  try {
    const page = await context.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await openFromShelf(page, 'VALID');
    await page.locator('#status').filter({hasText: /^Åbnet i /}).waitFor({timeout: 12000});
    const editor = page.frameLocator('iframe');
    await editor.locator('#object-list button').waitFor();
    await editor.locator('#download:enabled').waitFor();
    assert.equal(await editor.locator('#object-list button').count(), 1);
    await editor.locator('#project-classroom').fill('Testklasse');
    await editor.locator('#project-student').fill('Testelev');
    await editor.locator('#project-title').fill('STL-projekt');
    await editor.getByRole('button', {name: 'Start projekt', exact:true}).click();
    assert.equal(await editor.locator('#object-list button').count(), 1, 'Session startup must preserve the imported STL');
    assert.match(await page.locator('iframe').getAttribute('src'), /127\.0\.0\.1:8798\/model3d\.html\?elevhub_import=1/);
    assert.equal(await page.locator('#retry').isVisible(), false);
    assert.deepEqual(errors, []);
    await screenshot(page, 'mkv-project3d-valid.png');
  } finally { await context.close(); }
});

test('invalid transferred STL reports error instead of opened', {skip: !shelf, timeout: 40000}, async () => {
  const context = await browser.newContext({viewport: {width: 1366, height: 768}});
  try {
    const page = await context.newPage();
    // Real metadata and handshake; corrupt only the transferred bytes to exercise editor rejection.
    await page.route('**/_elevhub/projekter/api/file?*', route => route.fulfill({
      status: 200, contentType: 'application/octet-stream', body: 'synthetic invalid STL'
    }));
    await openFromShelf(page, 'INVALID');
    await page.locator('#retry').waitFor({state: 'visible', timeout: 12000});
    assert.equal(await page.locator('#status').innerText(), 'Projektet er stadig gemt.');
    assert.ok((await page.locator('#message').innerText()).trim(), 'Visible concrete error');
    assert.doesNotMatch(await page.locator('#status').innerText(), /^Åbnet i /);
    const editor = page.frameLocator('iframe');
    assert.equal(await editor.locator('#object-list button').count(), 0);
    assert.equal(await editor.locator('#download').isDisabled(), true);
    await page.locator('#retry').focus();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'retry');
    await screenshot(page, 'mkv-project3d-error.png');
  } finally { await context.close(); }
});
