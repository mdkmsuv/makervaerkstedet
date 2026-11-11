// Browser test of the 3D workshop in a real Chrome. It serves web/ itself on a loopback port with the
// same headers as the local previews: strict CSP with wasm-unsafe-eval, application/wasm and no caching.
// Run after `npm run build:model3d`: node --test tests/model3d-browser.test.mjs
// Package acceptance: set MODEL3D_TEST_URL to its real /model3d.html URL. TLS remains verified.
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const root = fileURLToPath(new URL('../', import.meta.url)), web = path.join(root, 'web');
const {chromium} = createRequire(path.join(root, 'package.json'))('playwright');
const types = {'.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.ttf': 'font/ttf', '.svg': 'image/svg+xml', '.json': 'application/json', '.txt': 'text/plain; charset=utf-8'};
const csp = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; connect-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'";

let server, browser, base;
test.before(async () => {
  if (process.env.MODEL3D_TEST_URL) {
    const target = new URL(process.env.MODEL3D_TEST_URL);
    if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password || target.pathname !== '/model3d.html') throw new Error('Expected a product /model3d.html URL');
    base = target.href;
    browser = await chromium.launch({headless: true, channel: 'chrome'});
    return;
  }
  server = http.createServer(async (request, response) => {
    const file = path.resolve(web, '.' + decodeURIComponent(new URL(request.url, 'http://x').pathname));
    if (!file.startsWith(web + path.sep)) { response.writeHead(403).end(); return; }
    try {
      const body = await readFile(file);
      response.writeHead(200, {'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'}).end(body);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}/model3d.html`;
  browser = await chromium.launch({headless: true, channel: 'chrome'});
});
test.after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });

async function open() {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, acceptDownloads: true, reducedMotion: 'reduce'}), problems = [];
  page.on('pageerror', error => problems.push(error.message));
  page.on('console', message => { if (['error', 'warning'].includes(message.type())) problems.push(message.text()); });
  page.on('request', request => { if (!request.url().startsWith(base.slice(0, base.lastIndexOf('/')))) problems.push('external request ' + request.url()); });
  await page.goto(base); await ready(page);
  return {page, problems};
}
const ready = page => page.waitForFunction(() => !document.querySelector('[data-add=box]').disabled && !/Beregner|Klargør/.test(document.querySelector('#status').textContent));
// A model without text asks for a name the first time it is downloaded.
async function nameIfAsked(page, name = 'testmodel') { await page.waitForTimeout(50); if (await page.locator('#name-dialog').isVisible()) { await page.locator('#model-name').fill(name); await page.locator('#name-form button[type=submit]').click(); } }
const field = (page, selector) => page.locator(selector).inputValue().then(Number);
async function typeMeasure(page, letter, value) {
  await page.locator('.measure', {hasText: new RegExp(`^${letter}`)}).first().click();
  await page.locator('.measure-input').fill(String(value)); await page.keyboard.press('Enter'); await ready(page);
}

test('the whole plate is shown and nothing is selected at the start', async () => {
  const {page, problems} = await open();
  assert.equal(await page.locator('#status').textContent(), 'Begynd med en form eller din egen tekst. Højre museknap drejer synet, og rullehjulet zoomer.');
  assert.equal(await page.locator('#selection-controls').isHidden(), true);
  assert.equal(await page.locator('.measure').count(), 0, 'no measures without a selection');
  assert.deepEqual(problems, []);
  await page.close();
});

test('name plate: typed measures, text on top, hole, combine and a valid STL', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.deepEqual(await page.locator('.measure').allTextContents(), ['L20 mm', 'B20 mm', 'H20 mm']);
  for (const [letter, value] of [['L', 70], ['B', 26], ['H', 4]]) await typeMeasure(page, letter, value);
  assert.deepEqual(await Promise.all([0, 1, 2].map(i => field(page, `[data-size="${i}"]`))), [70, 26, 4]);
  assert.equal(await field(page, '[data-position="2"]'), 0, 'the bottom must stay on the plate when the height is typed');

  // With the plate selected, a new text stands on it, sunk 0,5 mm so the two fuse into one part.
  await page.locator('[data-add=text]').click(); await ready(page);
  assert.equal(await field(page, '[data-position="2"]'), 3.5);
  assert.deepEqual([await field(page, '[data-position="0"]'), await field(page, '[data-position="1"]')], [0, 0]);
  await page.locator('#text-value').fill('Æble øå'); await page.locator('#text-value').press('Enter'); await ready(page);
  assert.match(await page.locator('#status').textContent(), /Modellen er klar/);

  // A new cylinder takes a free spot behind the plate, never inside it.
  await page.locator('[data-add=cylinder]:not([data-mode]):not([data-sides])').click(); await ready(page);
  assert.deepEqual([await field(page, '[data-position="0"]'), await field(page, '[data-position="1"]')], [0, 30]);
  for (const [letter, value] of [['L', 6], ['B', 6], ['H', 6]]) await typeMeasure(page, letter, value);
  await page.locator('#hole').click(); await ready(page);
  await page.locator('#position-details summary').click();
  for (const [axis, value] of [[0, -29], [1, 0]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  assert.equal(await page.locator('#download').isDisabled(), true, 'a loose hole must block the STL');

  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.locator('#group').click(); await ready(page);
  assert.equal(await page.locator('#object-list button').count(), 1);
  const download = page.waitForEvent('download'); await page.locator('#download').click();
  const bytes = await readFile(await (await download).path()), triangles = bytes.readUInt32LE(80);
  assert.equal(bytes.length, 84 + 50 * triangles);
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity], edges = new Map();
  for (let face = 0; face < triangles; face++) {
    const points = [0, 1, 2].map(corner => [0, 1, 2].map(axis => bytes.readFloatLE(84 + 50 * face + 12 + corner * 12 + axis * 4)));
    points.forEach(point => point.forEach((value, axis) => { min[axis] = Math.min(min[axis], value); max[axis] = Math.max(max[axis], value); }));
    for (let edge = 0; edge < 3; edge++) { const key = [points[edge].join(), points[(edge + 1) % 3].join()].sort().join('|'); edges.set(key, (edges.get(key) || 0) + 1); }
  }
  assert.deepEqual(max.map((value, axis) => Math.round((value - min[axis]) * 10) / 10), [70, 26, 5]);
  assert.equal(Math.round(min[2] * 1000) / 1000, 0);
  assert([...edges.values()].every(count => count === 2), 'the STL must be closed: every edge belongs to two triangles');
  assert.match(await page.locator('#status').textContent(), /Din fil er hentet: Æble-øå.stl/);

  // Undo brings the three parts back; redo combines them again.
  await page.locator('#undo').click(); await ready(page); assert.equal(await page.locator('#object-list button').count(), 3);
  await page.locator('#redo').click(); await ready(page); assert.equal(await page.locator('#object-list button').count(), 1);
  assert.deepEqual(problems, []);
  await page.close();
});

test('a floating part shows its height above the plate, and the label moves it', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.equal(await page.locator('.measure.lift').count(), 0);
  await page.locator('#position-details summary').click();
  await page.locator('[data-position="2"]').fill('12'); await page.locator('[data-position="2"]').press('Enter'); await ready(page);
  assert.equal(await page.locator('.measure.lift').textContent(), '↕12 mm');
  await page.locator('.measure.lift').click(); await page.locator('.measure-input').fill('5'); await page.keyboard.press('Enter'); await ready(page);
  assert.equal(await field(page, '[data-position="2"]'), 5);
  await page.locator('#drop').click(); await ready(page);
  assert.equal(await field(page, '[data-position="2"]'), 0); assert.equal(await page.locator('.measure.lift').count(), 0);
  // Escape cancels a typed measure without changing the part.
  await page.locator('.measure', {hasText: /^L/}).click(); await page.locator('.measure-input').fill('55'); await page.keyboard.press('Escape');
  assert.equal(await field(page, '[data-size="0"]'), 20);
  assert.deepEqual(problems, []);
  await page.close();
});

test('dragging a part moves it in one undo step', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  // "Vis model" puts the part's centre on the orbit target, drawn in the middle of the free area left of the panel.
  await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  const box = await page.locator('#canvas canvas').boundingBox(), inset = 294 + 34, start = [box.x + (box.width - inset) / 2, box.y + box.height / 2];
  await page.mouse.move(...start); await page.mouse.down(); await page.mouse.move(start[0] + 60, start[1] + 20, {steps: 10}); await page.mouse.up(); await ready(page);
  const moved = [await field(page, '[data-position="0"]'), await field(page, '[data-position="1"]')];
  assert.notDeepEqual(moved, [0, 0]); assert(moved.every(Number.isInteger), 'snap keeps whole millimetres');
  await page.locator('#undo').click(); await ready(page);
  assert.deepEqual([await field(page, '[data-position="0"]'), await field(page, '[data-position="1"]')], [0, 0]);
  assert.deepEqual(problems, []);
  await page.close();
});

// Screen centre of a handle, or a point on a rotation arc's stroke.
const handleAt = (page, selector, index = 0) => page.evaluate(([selector, index]) => {
  const element = document.querySelectorAll(selector)[index], arc = element.querySelector('.arc');
  if (arc) { const svg = arc.ownerSVGElement.getBoundingClientRect(), p = arc.getPointAtLength(arc.getTotalLength() / 2); return [svg.left + p.x, svg.top + p.y]; }
  const r = element.querySelector('.knob').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2];
}, [selector, index]);
async function drag(page, from, by, modifiers = []) {
  for (const key of modifiers) await page.keyboard.down(key);
  await page.mouse.move(...from); await page.mouse.down(); await page.mouse.move(from[0] + by[0], from[1] + by[1], {steps: 12}); await page.mouse.up();
  for (const key of modifiers) await page.keyboard.up(key);
  await ready(page);
}
const sizes = page => Promise.all([0, 1, 2].map(i => field(page, `[data-size="${i}"]`)));
const place = page => Promise.all([0, 1, 2].map(i => field(page, `[data-position="${i}"]`)));

test('size handles keep the opposite side, the top handle keeps the bottom, Shift keeps the shape', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page); await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  assert.equal(await page.locator('.handle.resize').count(), 9); assert.equal(await page.locator('.handle.lift').count(), 1); assert.equal(await page.locator('.handle.rotate').count(), 3);
  // The two length handles: the right-hand one on screen is the +X side.
  const lengths = await Promise.all([0, 1].map(i => handleAt(page, '.handle.resize:not(.corner)', i * 2)));
  const right = lengths[0][0] > lengths[1][0] ? 0 : 1;
  await page.hover('#status'); await drag(page, lengths[right], [70, 0]);
  const [length] = await sizes(page), [x] = await place(page);
  assert(length > 24, `length should grow, got ${length}`); assert(Number.isInteger(length)); assert.equal(Math.round((x - length / 2) * 100) / 100, -10, 'the left side must stay at -10');
  const top = await handleAt(page, '.handle.resize:not(.corner)', 4); await drag(page, top, [0, -60]);
  const [, , height] = await sizes(page); assert(height > 24, `height should grow, got ${height}`); assert.equal((await place(page))[2], 0, 'the bottom must stay on the plate');
  const before = await sizes(page), corner = await handleAt(page, '.handle.corner', 3); await drag(page, corner, [60, -20], ['Shift']);
  const after = await sizes(page), ratio = after.map((value, i) => value / before[i]);
  assert(ratio[0] > 1.05 && ratio.every(value => Math.abs(value - ratio[0]) < .02), `Shift must scale all axes alike, got ${ratio}`);
  assert.deepEqual(problems, []); await page.close();
});

test('the arrow lifts, the turntable arc turns in 15° steps and the part stays on the plate', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page); await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  await drag(page, await handleAt(page, '.handle.lift'), [0, -50]);
  const lifted = (await place(page))[2]; assert(lifted > 3 && Number.isInteger(lifted), `lift should be whole mm, got ${lifted}`);
  assert.equal(await page.locator('.measure.lift').count(), 1);
  await page.locator('#drop').click(); await ready(page);
  assert.equal(await page.locator('.handle.rotate:visible').count(), 0, 'no arcs until Drej is pressed');
  await page.locator('#rotate-mode').click(); await page.waitForTimeout(100);
  await drag(page, await handleAt(page, '.handle.rotate', 2), [90, -25]);
  const angle = await field(page, '[data-rotation="2"]'); assert(angle !== 0 && angle % 15 === 0, `Z angle must be a 15° step, got ${angle}`);
  assert.equal((await place(page))[2], 0);
  // A typed tilt about X keeps the bottom on the plate too.
  await page.locator('#rotation-controls summary').click(); await page.locator('[data-rotation="0"]').fill('45'); await page.locator('[data-rotation="0"]').press('Enter'); await ready(page);
  assert.equal(await field(page, '[data-rotation="0"]'), 45); assert.equal((await place(page))[2], 0);
  await page.locator('#undo').click(); await ready(page); assert.equal(await field(page, '[data-rotation="0"]'), 0);
  assert.deepEqual(problems, []); await page.close();
});

test('text fields show the visible size after stretching; H and Backspace change nothing by accident', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=text]').click(); await ready(page);
  assert.equal(await field(page, '#text-height'), 12);
  await page.locator('[data-size="1"]').fill('24'); await page.locator('[data-size="1"]').press('Enter'); await ready(page);
  assert.equal(await field(page, '#text-height'), 24, 'the text height field must follow the stretched size');
  await page.locator('#text-height').fill('6'); await page.locator('#text-height').press('Enter'); await ready(page);
  assert.equal(await field(page, '[data-size="1"]'), 6);
  await page.locator('#workspace').focus(); await page.keyboard.press('h'); await page.keyboard.press('Backspace'); await page.waitForTimeout(150);
  assert.equal(await page.locator('#solid').getAttribute('aria-pressed'), 'true'); assert.equal(await page.locator('#object-list button').count(), 1);
  await page.keyboard.press('Delete'); await ready(page);
  assert.equal(await page.locator('#object-list button').count(), 0); assert.match(await page.locator('#status').textContent(), /er slettet\. Tryk på Fortryd/);
  assert.deepEqual(problems, []); await page.close();
});

test('ready-made holes, new shapes, colours and the print check', async () => {
  const {page, problems} = await open();
  await page.locator('.more-shapes summary').click();
  for (const kind of ['tube', 'roof', 'halfsphere', 'pyramid']) { await page.locator(`[data-add=${kind}]`).click(); await ready(page); }
  assert.equal(await page.locator('#object-list button').count(), 4);
  // A colour applies to the selected part and shows in the parts list.
  await page.locator('#color-row [aria-label="Blå"]').click(); await ready(page);
  assert.equal(await page.locator('#color-row [aria-label="Blå"]').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#object-list button[aria-pressed=true] .part-icon').evaluate(icon => getComputedStyle(icon).backgroundColor), 'rgb(47, 111, 181)');
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.keyboard.press('Delete'); await ready(page);
  // The ready-made round hole arrives as a hole; no colour choice for holes.
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  assert.equal(await page.locator('#hole').getAttribute('aria-pressed'), 'true'); assert.equal(await page.locator('#color-row').isVisible(), false);
  await page.locator('#delete').click(); await ready(page);
  // A floating model opens the print check; its button puts the model on the plate.
  await page.locator('#object-list button').first().dispatchEvent('click');
  await page.locator('#position-details summary').click(); await page.locator('[data-position="2"]').fill('7'); await page.locator('[data-position="2"]').press('Enter'); await ready(page);
  await page.locator('#download').click(); await nameIfAsked(page);
  assert.equal(await page.locator('#print-check').isVisible(), true);
  assert.match(await page.locator('#print-issues').textContent(), /svæver 7 mm/);
  await page.getByRole('button', {name: 'Læg modellen på pladen'}).click(); await ready(page);
  assert.equal(await field(page, '[data-position="2"]'), 0);
  const download = page.waitForEvent('download'); await page.locator('#download').click(); await download;
  assert.equal(await page.locator('#print-check').isVisible(), false, 'a model without problems downloads directly');
  assert.deepEqual(problems, []); await page.close();
});

test('a copy lands beside the original, and again beside the copy', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#duplicate').click(); await ready(page);
  assert.deepEqual((await place(page)).slice(0, 2), [25, 0]);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+d'); await ready(page);
  assert.deepEqual((await place(page)).slice(0, 2), [50, 0]);
  assert.equal(await page.locator('#object-list button').count(), 3);
  assert.deepEqual(problems, []); await page.close();
});

test('right drag turns the view, a left frame chooses parts, the hand moves the view; a plain click clears the selection', async () => {
  const {page, problems} = await open();
  // The copy stands 25 mm from the orbit centre, so turning the view moves it on screen.
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page); await page.locator('#duplicate').click(); await ready(page);
  const canvas = await page.locator('#canvas canvas').boundingBox(), empty = [canvas.x + 50, canvas.y + 170], before = await handleAt(page, '.handle.lift');
  await page.mouse.move(...empty); await page.mouse.down({button: 'right'}); await page.mouse.move(empty[0] + 120, empty[1] + 30, {steps: 10}); await page.mouse.up({button: 'right'}); await page.waitForTimeout(600);
  const after = await handleAt(page, '.handle.lift');
  assert(Math.hypot(after[0] - before[0], after[1] - before[1]) > 10, 'the right button turns the camera');
  assert.equal(await page.locator('#selection-controls').isHidden(), false, 'turning the view keeps the selection');
  // A left drag on the empty plate no longer turns the view: it draws a frame round both parts.
  await page.locator('[data-camera=home]').click(); await page.waitForTimeout(200);
  const lifts = []; for (const i of [0, 1]) { await page.locator('#object-list button').nth(i).dispatchEvent('click'); lifts.push(await handleAt(page, '.handle.lift')); }
  const left = Math.min(lifts[0][0], lifts[1][0]) - 70, right = Math.max(lifts[0][0], lifts[1][0]) + 70, top = Math.min(lifts[0][1], lifts[1][1]) - 20, bottom = Math.max(lifts[0][1], lifts[1][1]) + 120;
  await page.mouse.click(canvas.x + canvas.width * .45, canvas.y + 30); assert.equal(await page.locator('#selection-controls').isHidden(), true, 'a plain click clears the selection');
  const still = await page.locator('.cube-face').first().evaluate(face => face.style.transform);
  await page.mouse.move(left, top); await page.mouse.down(); await page.mouse.move(right, bottom, {steps: 8}); await page.mouse.up(); await page.waitForTimeout(100);
  assert.equal(await page.locator('#selection-label').textContent(), '2 figurer valgt');
  assert.equal(await page.locator('.cube-face').first().evaluate(face => face.style.transform), still, 'the frame does not turn the view');
  // The hand: a left drag moves the view and keeps the selection.
  await page.locator('#pan-mode').click(); const handBefore = await handleAt(page, '.handle.lift');
  await page.mouse.move(canvas.x + 300, canvas.y + 300); await page.mouse.down(); await page.mouse.move(canvas.x + 380, canvas.y + 340, {steps: 8}); await page.mouse.up(); await page.waitForTimeout(400);
  const handAfter = await handleAt(page, '.handle.lift');
  assert(Math.hypot(handAfter[0] - handBefore[0], handAfter[1] - handBefore[1]) > 20, 'the hand moves the view');
  assert.equal(await page.locator('#selection-label').textContent(), '2 figurer valgt');
  await page.keyboard.press('Escape'); assert.equal(await page.locator('#pan-mode').getAttribute('aria-pressed'), 'false', 'Escape puts the hand away');
  assert.deepEqual(problems, []); await page.close();
});

test('a tipped-over part keeps its bottom on the plate when it grows, and quick typing is not lost', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#rotation-controls summary').click(); await page.locator('[data-rotation="0"]').fill('90'); await page.locator('[data-rotation="0"]').press('Enter'); await ready(page);
  await page.locator('[data-size="1"]').fill('40'); await page.locator('[data-size="1"]').press('Enter'); await ready(page);
  assert.equal(await field(page, '[data-size="1"]'), 40); assert.equal((await place(page))[2], 0, 'the bottom must stay on the plate');
  // Two lengths typed straight after each other: the last one wins once the model is ready.
  const length = page.locator('[data-size="0"]');
  await length.fill('30'); await length.press('Enter'); await length.fill('45'); await length.press('Enter'); await ready(page); await page.waitForTimeout(300); await ready(page);
  await page.locator('#status').click();
  assert.equal(await field(page, '[data-size="0"]'), 45);
  assert.deepEqual(problems, []); await page.close();
});

test('the direction cube turns the view to a side', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.equal(await page.locator('.handle.lift.off').count(), 0);
  await page.locator('.cube-face', {hasText: 'Oven'}).dispatchEvent('click'); await page.waitForTimeout(200);
  assert.equal(await page.locator('.handle.lift.off').count(), 1, 'seen from above, the lift arrow is hidden');
  await page.locator('.cube-face', {hasText: 'Højre'}).dispatchEvent('click'); await page.waitForTimeout(200);
  assert.equal(await page.locator('.handle.lift.off').count(), 0);
  assert.deepEqual(problems, []); await page.close();
});

test('pupil-test fixes: fields follow the clicked part, a missed hole warns, one button cuts holes, parts stack when moved', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 70], ['B', 26], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=text]').click(); await ready(page);
  // Clicking the plate in the list must show the plate's sizes, not the text's.
  await page.locator('#object-list button').first().dispatchEvent('click');
  assert.deepEqual(await sizes(page), [70, 26, 4]);
  // A hole beside the plate: "Skær hullet ud" melts all together and warns that the hole missed.
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  assert.equal(await page.locator('#combine-note').isVisible(), true);
  await page.locator('#cut-holes').click(); await ready(page);
  assert.equal(await page.locator('#object-list button').count(), 1); assert.match(await page.locator('#status').textContent(), /rører ikke din figur/);
  await page.locator('#undo').click(); await ready(page);
  // Moved onto the plate, the hole cuts: no warning. Undo restored all three as selected, so pick the hole alone.
  await page.locator('#object-list button', {hasText: 'hul'}).dispatchEvent('click');
  await page.locator('#position-details summary').click();
  for (const [axis, value] of [[0, -22], [1, 0]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  await page.locator('#cut-holes').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /Hullet er skåret ud/);
  const download = page.waitForEvent('download'); await page.locator('#download').click(); assert.equal((await download).suggestedFilename(), 'Navn.stl');
  assert.deepEqual(problems, []); await page.close();
});

test('a ball dragged onto another ball stands on it, and a ball stays round at a corner', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  // Find the top of the first ball on screen (the lift arrow sits 40 px above it), then drag the second ball there.
  await page.locator('#object-list button').first().dispatchEvent('click'); const first = await handleAt(page, '.handle.lift');
  await page.locator('#object-list button').last().dispatchEvent('click'); const second = await handleAt(page, '.handle.lift');
  const grab = [second[0], second[1] + 95], goal = [first[0], first[1] + 52];
  await page.mouse.move(...grab); await page.mouse.down();
  for (let i = 1; i <= 12; i++) await page.mouse.move(grab[0] + (goal[0] - grab[0]) * i / 12, grab[1] + (goal[1] - grab[1]) * i / 12);
  const on = /ovenpå/.test(await page.locator('#drag-readout').textContent());
  await page.mouse.up(); await ready(page);
  assert(on, 'the readout must say the ball lands on the other one'); assert.deepEqual(await place(page), [0, 0, 19.5], 'the ball must sit centred on top, sunk 0,5 mm');
  const corner = await handleAt(page, '.handle.corner', 3); await drag(page, corner, [40, -10]);
  const [a, b, c] = await sizes(page); assert(Math.abs(a - b) < .01 && Math.abs(a - c) < .01, `a ball must stay round, got ${a}, ${b}, ${c}`);
  assert.deepEqual(problems, []); await page.close();
});

test('a wrong number is refused at the field and the field shows the real size again', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('[data-size="0"]').click(); await page.keyboard.type('2070'); await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('[data-size="0"]').getAttribute('aria-invalid') === 'true', null, {timeout: 3000});
  assert.equal(await page.locator('[data-size="0"]').getAttribute('aria-invalid'), 'true');
  assert.equal(await field(page, '[data-size="0"]'), 20, 'clicking selects the number, and a refused value is replaced by the real size');
  assert.match(await page.locator('#status').textContent(), /for stort/);
  assert.deepEqual(problems, []); await page.close();
});

test('Centrér puts a hole in the middle of a cube in one undo step', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape'); assert.equal(await page.locator('#selection-controls').isVisible(), false, 'Escape lets go of the part');
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  assert.equal(await page.locator('#center').isDisabled(), true);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.locator('#center').click(); await ready(page);
  await page.locator('#object-list button', {hasText: 'hul'}).dispatchEvent('click');
  assert.deepEqual((await place(page)).slice(0, 2), [0, 0]);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.locator('#cut-holes').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /Hullet er skåret ud/);
  await page.locator('#undo').click(); await ready(page); await page.locator('#undo').click(); await ready(page);
  await page.locator('#object-list button', {hasText: 'hul'}).dispatchEvent('click'); assert.notDeepEqual((await place(page)).slice(0, 2), [0, 0]);
  assert.deepEqual(problems, []); await page.close();
});

test('a locked part cannot be moved, resized or deleted; a hidden part stays out of the file', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#position-details summary').click(); await page.locator('#lock').click(); await ready(page);
  assert.equal(await page.locator('.handle').count(), 0); assert.equal(await page.locator('.measure.static').count(), 3);
  await page.locator('#workspace').focus(); await page.keyboard.press('ArrowRight'); await page.keyboard.press('Delete'); await ready(page);
  assert.deepEqual((await place(page)).slice(0, 2), [0, 0]); assert.equal(await page.locator('#object-list button').count(), 1);
  assert.match(await page.locator('#object-list button').textContent(), /låst/);
  await page.locator('#lock').click(); await ready(page);
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await page.locator('#position-details summary').click(); await page.locator('#hide').click(); await ready(page);
  assert.match(await page.locator('#object-list button').last().textContent(), /skjult/);
  await page.locator('#object-list button').first().dispatchEvent('click'); await page.locator('#download').click(); await nameIfAsked(page);
  assert.match(await page.locator('#print-issues').textContent(), /skjult figur kommer ikke med/);
  await page.getByRole('button', {name: 'Tilbage til modellen'}).click();
  await page.locator('#object-list button').last().dispatchEvent('click');
  if (!(await page.locator('#position-details').evaluate(d => d.open))) await page.locator('#position-details summary').click();
  await page.locator('#hide').click(); await ready(page);
  assert.doesNotMatch(await page.locator('#object-list button').last().textContent(), /skjult/);
  assert.deepEqual(problems, []); await page.close();
});

test('everyone gets the same workshop: every shape in front, harder tools one click away', async () => {
  const {page, problems} = await open();
  assert.equal(await page.locator('#ideas-dialog').isVisible(), false, 'no start question');
  assert.equal(await page.locator('.levels').count(), 0);
  assert.equal(await page.locator('[data-add]:visible').count(), 15, 'every shape in front');
  assert.equal(await page.locator('#import-stl').isVisible(), false, 'fetching a file waits behind its button');
  await page.locator('.more-shapes summary').click(); assert.equal(await page.locator('#import-stl').isVisible(), true);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const tucked of ['#mirror', '#ungroup', '#lock', '[data-rotation="0"]', '[data-position="0"]']) assert.equal(await page.locator(tucked).isVisible(), false, tucked);
  assert.equal(await page.locator('.handle.rotate:visible').count(), 0, 'turning arcs wait until Drej is pressed');
  await page.locator('#rotate-mode').click(); await page.waitForTimeout(100);
  assert(await page.locator('.handle.tilt:visible').count() > 0, 'Drej shows the turning and tilting arcs');
  await page.locator('#workspace').focus(); await page.keyboard.press('r'); await page.waitForTimeout(100);
  assert.equal(await page.locator('.handle.rotate:visible').count(), 0, 'R turns the arcs off again');
  await page.locator('#more-menu summary').click(); assert.equal(await page.locator('#mirror').isVisible(), true);
  await page.locator('#mirror').click(); await ready(page); assert.equal(await page.locator('#mirror').isVisible(), false, 'the menu closes after a choice');
  await page.reload(); await ready(page); await page.waitForTimeout(100);
  assert.equal(await page.locator('#ideas-dialog').isVisible(), false);
  assert.deepEqual(problems, []); await page.close();
});

test('a hole over the edge is called a notch; a lone text is named; a new text is ready for typing', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  await page.locator('#position-details summary').click(); await page.locator('[data-position="0"]').fill('-12'); await page.locator('[data-position="0"]').press('Enter'); await ready(page);
  await page.locator('#cut-holes').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /hak og ikke et hul/);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.keyboard.press('Delete'); await ready(page);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#workspace').click({position: {x: 40, y: 200}}); await ready(page);
  await page.locator('[data-add=text]').click(); await ready(page); await page.waitForTimeout(50);
  await page.keyboard.type('Ida'); await page.keyboard.press('Enter'); await ready(page);
  assert.equal(await page.locator('#text-value').inputValue(), 'Ida', 'the default word must be selected so the name replaces it');
  assert.match(await page.locator('#status').textContent(), /Teksten "Ida" rører ikke de andre figurer/);
  assert.deepEqual(problems, []); await page.close();
});

test('snowman: Centrér stacks the balls, a cone aimed at the head lands on it, a typed size keeps a ball round', async () => {
  const {page, problems} = await open();
  for (let i = 0; i < 3; i++) { await page.locator('[data-add=sphere]').click(); await ready(page); }
  await page.locator('#object-list button').nth(1).dispatchEvent('click'); await typeMeasure(page, 'L', 14);
  assert.deepEqual(await sizes(page), [14, 14, 14], 'a typed size must keep a ball round');
  await page.locator('#object-list button').nth(2).dispatchEvent('click'); await typeMeasure(page, 'L', 10);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.locator('#center').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /oven på hinanden/);
  const bottoms = [];
  for (let i = 0; i < 3; i++) { await page.locator('#object-list button').nth(i).dispatchEvent('click'); bottoms.push((await place(page))[2]); }
  assert.deepEqual(bottoms, [0, 19.5, 33], 'body on the plate, head on the body, top ball on the head');
  // A cone dragged from the panel onto the top ball stands on it.
  const head = await handleAt(page, '.handle.lift'), aim = [head[0], head[1] + 48];
  const data = await page.evaluateHandle(() => new DataTransfer());
  await page.locator('[data-add=cone]').dispatchEvent('dragstart', {dataTransfer: data});
  await page.locator('#workspace').dispatchEvent('dragover', {dataTransfer: data, clientX: aim[0], clientY: aim[1]});
  await page.locator('#workspace').dispatchEvent('drop', {dataTransfer: data, clientX: aim[0], clientY: aim[1]}); await ready(page);
  assert((await place(page))[2] > 35, `the cone must stand on the top ball, bottom ${(await place(page))[2]}`);
  assert.deepEqual(problems, []); await page.close();
});

test('a cone dropped on the front of a ball points forward, like a nose', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await page.locator('.cube-face', {hasText: 'Foran'}).dispatchEvent('click'); await page.waitForTimeout(200);
  await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  // The ball's centre on screen: the lift arrow sits 40 px above its top, so aim a little below the arrow's mid-height.
  const arrow = await handleAt(page, '.handle.lift'), canvas = await page.locator('#canvas canvas').boundingBox();
  const ball = await page.evaluate(() => { const box = [...document.querySelectorAll('.handle.corner .knob')].map(knob => knob.getBoundingClientRect()); return box.length ? (box[0].y + box[2].y) / 2 : null; });
  const aim = [arrow[0], (arrow[1] + 40 + (ball ?? arrow[1] + 120)) / 2 + 10];
  const data = await page.evaluateHandle(() => new DataTransfer());
  await page.locator('[data-add=cone]').dispatchEvent('dragstart', {dataTransfer: data});
  await page.locator('#workspace').dispatchEvent('drop', {dataTransfer: data, clientX: aim[0], clientY: aim[1]}); await ready(page);
  await page.locator('#rotation-controls summary').click();
  const tilt = Math.abs(await field(page, '[data-rotation="0"]'));
  assert(tilt > 45, `the cone must point outwards from the front of the ball, tilt ${tilt}`); void canvas;
  assert.deepEqual(await sizes(page), [10, 10, 10], 'a nose gets half the size of the head');
  assert.match(await page.locator('#status').textContent(), /klar/);
  assert.deepEqual(problems, []); await page.close();
});

test('a nose points straight out in a contrast colour, a hat is no wider than the head, a model gets a name', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=sphere]').click(); await ready(page); await typeMeasure(page, 'L', 12);
  await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  const arrow = await handleAt(page, '.handle.lift'), drop = async (kind, point) => {
    const data = await page.evaluateHandle(() => new DataTransfer());
    await page.locator(`[data-add=${kind}]:not([data-mode])`).dispatchEvent('dragstart', {dataTransfer: data});
    await page.locator('#workspace').dispatchEvent('drop', {dataTransfer: data, clientX: point[0], clientY: point[1]}); await ready(page);
  };
  // The ball's middle on screen: halfway between the top handle and the middle of the four bottom corners.
  const middle = await page.evaluate(() => { const knob = element => element.querySelector('.knob').getBoundingClientRect(), mid = r => [r.x + r.width / 2, r.y + r.height / 2];
    const top = mid(knob([...document.querySelectorAll('.handle.resize:not(.corner)')].at(-1))), corners = [...document.querySelectorAll('.handle.corner')].map(element => mid(knob(element)));
    const bottom = [0, 1].map(i => corners.reduce((sum, p) => sum + p[i], 0) / corners.length); return [(top[0] + bottom[0]) / 2, (top[1] + bottom[1]) / 2]; });
  await drop('cone', [middle[0] - 30, middle[1]]);
  await page.locator('#rotation-controls summary').click();
  const angles = await Promise.all([0, 1, 2].map(i => field(page, `[data-rotation="${i}"]`)));
  assert(angles.every(Number.isInteger), `whole degrees, got ${angles}`); assert.equal(Math.abs(angles[0]) % 45, 0, `the nose lies level, got ${angles}`);
  assert.equal(await page.locator('#color-row [aria-label="Orange"]').getAttribute('aria-pressed'), 'true');
  await page.locator('#object-list button').first().dispatchEvent('click');
  const top = await handleAt(page, '.handle.lift'); await drop('cone', [top[0], top[1] + 46]);
  const [width] = await sizes(page);
  assert(width <= 12, `the hat must not be wider than the head, got ${width}`);
  await page.locator('#download').click(); assert.equal(await page.locator('#name-dialog').isVisible(), true);
  await page.locator('#model-name').fill('Snemand'); const download = page.waitForEvent('download'); await page.locator('#name-form button[type=submit]').click();
  if (await page.locator('#print-check').isVisible()) await page.getByRole('button', {name: 'Hent STL alligevel'}).click();
  assert.equal((await download).suggestedFilename(), 'Snemand.stl');
  assert.deepEqual(problems, []); await page.close();
});

test('a small push does not throw a part onto the one behind it; a text on any side reads upright', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  // Put the second box straight in front of the first, then nudge it 1 mm sideways by dragging.
  await page.locator('#position-details summary').click();
  for (const [axis, value] of [[0, 0], [1, -25]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  const arrow = await handleAt(page, '.handle.lift'), grab = [arrow[0], arrow[1] + 80];
  await page.mouse.move(...grab); await page.mouse.down(); await page.mouse.move(grab[0] + 8, grab[1], {steps: 4}); await page.mouse.up(); await ready(page);
  const [x, y, z] = await place(page); assert.equal(z, 0, 'the part must stay on the plate'); assert(y < -15, `the part must not jump onto the box behind, y ${y}`); void x;
  assert.deepEqual(problems, []); await page.close();
});

test('an idea card opens a step panel that points at the button to use', async () => {
  const {page, problems} = await open();
  await page.locator('#ideas').click(); assert.equal(await page.locator('.idea-card').count(), 4);
  await page.locator('.idea-card', {hasText: 'Terning med hul'}).click();
  assert.equal(await page.locator('#guide').isVisible(), true); assert.match(await page.locator('#guide-count').textContent(), /Trin 1 af 6/);
  assert.equal(await page.locator('[data-add="box"]:not([data-mode])').evaluate(element => element.classList.contains('guide-target')), true);
  // Next before the step is done gives one reminder; doing the step marks it done.
  await page.locator('#guide-next').click(); assert.equal(await page.locator('#guide-nudge').isVisible(), true);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page); await page.waitForTimeout(500);
  assert.equal(await page.locator('#guide .guide-done').isVisible(), true);
  await page.locator('#guide-next').click(); assert.equal(await page.locator('[data-add="cylinder"][data-mode="hole"]').evaluate(element => element.classList.contains('guide-target')), true);
  await page.locator('#guide-back').click(); await page.locator('#guide-close').click();
  assert.equal(await page.locator('#guide').isVisible(), false); assert.equal(await page.locator('.guide-target').count(), 0);
  assert.deepEqual(problems, []); await page.close();
});

test('Øv dig is the one other way in: it opens the exercises, and closing it leaves the same workshop', async () => {
  const {page, problems} = await open();
  await page.locator('#ideas').click(); assert.equal(await page.locator('#ideas-dialog').isVisible(), true);
  assert.equal(await page.locator('.idea-card').count(), 4);
  await page.locator('#ideas-dialog form button').click(); assert.equal(await page.locator('#guide').isVisible(), false);
  assert.deepEqual(problems, []); await page.close();
});

test('a round hole stays round when one size is typed, and a new idea on a full plate asks to clear it', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  await typeMeasure(page, 'L', 6); assert.deepEqual(await sizes(page), [6, 6, 20]);
  await page.locator('#ideas').click(); await page.locator('.idea-card', {hasText: 'Spillebrik'}).click();
  assert.equal(await page.locator('#clear-dialog').isVisible(), true); await page.locator('#clear-yes').click(); await ready(page);
  assert.equal(await page.locator('#object-list button').count(), 0); assert.equal(await page.locator('#guide').isVisible(), true);
  await page.locator('#undo').click(); await ready(page); assert.equal(await page.locator('#object-list button').count(), 1);
  assert.deepEqual(problems, []); await page.close();
});

test('a copy that was moved and turned makes the next copy repeat it; Spejlvend mirrors in place', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#duplicate').click(); await ready(page);
  // Move the copy to x = 30 and turn it 15°: the next copy goes another 30 mm and 15° further.
  await page.locator('#position-details summary').click(); await page.locator('[data-position="0"]').fill('30'); await page.locator('[data-position="0"]').press('Enter'); await ready(page);
  await page.locator('#rotation-controls summary').click(); await page.locator('[data-rotation="2"]').fill('15'); await page.locator('[data-rotation="2"]').press('Enter'); await ready(page);
  await page.locator('#duplicate').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /gentager/);
  assert.equal(await field(page, '[data-rotation="2"]'), 30); const [x] = await place(page); assert(Math.abs(x - 60) < 2, `the third box must stand near x = 60, got ${x}`);
  // Spejlvend keeps size and place; a mirrored text still makes a closed file.
  await page.locator('#more-menu summary').click(); await page.locator('#mirror').click(); await ready(page);
  assert.deepEqual(await sizes(page), [20, 20, 20]); assert(Math.abs((await place(page))[0] - x) < .01);
  await page.locator('[data-add=text]').click(); await ready(page); await page.locator('#more-menu summary').click(); await page.locator('#mirror').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /spejlvendt/);
  assert.deepEqual(problems, []); await page.close();
});

test('what stands on a part follows when the part gets taller or lower', async () => {
  const {page, problems} = await open();
  for (let i = 0; i < 3; i++) { await page.locator('[data-add=sphere]').click(); await ready(page); }
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.locator('#center').click(); await ready(page);
  const bottoms = async () => { const result = []; for (let i = 0; i < 3; i++) { await page.locator('#object-list button').nth(i).dispatchEvent('click'); result.push((await place(page))[2]); } return result; };
  assert.deepEqual(await bottoms(), [0, 19.5, 39]);
  await page.locator('#object-list button').first().dispatchEvent('click'); await typeMeasure(page, 'L', 30);
  assert.deepEqual(await bottoms(), [0, 29.5, 49], 'the head and the top ball rise with the body');
  await page.locator('#undo').click(); await ready(page);
  assert.deepEqual(await bottoms(), [0, 19.5, 39], 'one undo step');
  assert.deepEqual(problems, []); await page.close();
});

test('an STL file is brought in as a solid part, can get a hole and comes out as one closed file', async () => {
  const {page, problems} = await open();
  // A 30 × 30 × 30 mm cube as a binary STL with 12 triangles.
  const v = [[0,0,0],[30,0,0],[30,30,0],[0,30,0],[0,0,30],[30,0,30],[30,30,30],[0,30,30]], faces = [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]];
  const buffer = Buffer.alloc(84 + 50 * faces.length); buffer.writeUInt32LE(faces.length, 80);
  faces.forEach((face, i) => face.forEach((corner, c) => v[corner].forEach((value, a) => buffer.writeFloatLE(value, 84 + 50 * i + 12 + c * 12 + a * 4))));
  await page.locator('#stl-file').setInputFiles({name: 'terning.stl', mimeType: 'model/stl', buffer}); await ready(page);
  assert.match(await page.locator('#object-list').textContent(), /terning/); assert.deepEqual(await sizes(page), [30, 30, 30]);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  await page.locator('#select-all').click(); await page.locator('#center').click(); await ready(page);
  await page.locator('#cut-holes').click(); await ready(page);
  await page.locator('#download').click(); await nameIfAsked(page, 'terning');
  if (await page.locator('#print-check').isVisible()) await page.getByRole('button', {name: 'Hent STL alligevel'}).click();
  assert.match(await page.locator('#status').textContent(), /Din fil er hentet: terning\.stl \(30 × 30 × 30 mm\)/);
  assert.deepEqual(problems, []); await page.close();
});

test('two chosen parts show the gap between them; typing a new gap moves the part chosen last', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#object-list button').first().dispatchEvent('click'); await page.locator('#object-list button').last().dispatchEvent('click', {shiftKey: true});
  assert.equal(await page.locator('.measure.gap').textContent(), '↔5 mm');
  await page.locator('.measure.gap').click(); await page.locator('.measure-input').fill('12'); await page.keyboard.press('Enter'); await ready(page);
  await page.locator('#object-list button').last().dispatchEvent('click'); assert.deepEqual((await place(page)).slice(0, 2), [32, 0]);
  await page.locator('#object-list button').first().dispatchEvent('click'); assert.deepEqual((await place(page)).slice(0, 2), [0, 0], 'the reference stays');
  assert.deepEqual(problems, []); await page.close();
});

test('arrow keys follow the camera, and the pointer shows a part can be grabbed', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('ArrowRight'); await ready(page);
  assert.deepEqual((await place(page)).slice(0, 2), [1, 0], 'from the front, right is +X');
  // Seen from the right side, "right" on screen is +Y and "up" (away) is -X.
  await page.locator('.cube-face', {hasText: 'Højre'}).dispatchEvent('click'); await page.waitForTimeout(150);
  await page.locator('#workspace').focus(); await page.keyboard.press('ArrowRight'); await ready(page); await page.keyboard.press('ArrowUp'); await ready(page);
  assert.deepEqual((await place(page)).slice(0, 2), [0, 1]);
  await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(150);
  const arrow = await handleAt(page, '.handle.lift'); await page.mouse.move(arrow[0], arrow[1] + 90); await page.waitForTimeout(100);
  assert.equal(await page.locator('#canvas canvas').evaluate(canvas => canvas.style.cursor), 'grab');
  assert.deepEqual(problems, []); await page.close();
});

test('a box can get round corners in millimetres, and undo takes them away', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 70], ['B', 26], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('#box-round').fill('5'); await page.locator('#box-round').press('Enter'); await ready(page);
  assert.equal(await page.locator('#box-round').inputValue(), '5'); assert.deepEqual(await sizes(page), [70, 26, 4]);
  await page.locator('#box-round').fill('40'); await page.locator('#box-round').press('Enter'); await ready(page);
  assert.equal(await page.locator('#box-round').inputValue(), '13', 'the radius stops at half the width');
  await page.locator('#undo').click(); await ready(page); await page.locator('#undo').click(); await ready(page);
  assert.equal(await page.locator('#box-round').inputValue(), '0');
  assert.deepEqual(problems, []); await page.close();
});

test('pictograms carry the words as tips; a hole chosen with a plate selected lands in the plate; the menu closes outside', async () => {
  const {page, problems} = await open();
  for (const id of ['#select-all', '#duplicate', '#delete', '#center', '#group']) assert.equal((await page.locator(id).innerText()).trim(), '', `${id} shows no words`);
  await page.locator('#group').hover(); await page.waitForTimeout(450);
  assert.match(await page.locator('#tip').textContent(), /Smelt sammen/); assert.equal(await page.locator('#tip').isVisible(), true);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 70], ['B', 26], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  assert.deepEqual(await sizes(page), [7, 7, 5], 'a quarter of the plate width, through the plate');
  assert.match(await page.locator('#status').textContent(), /midt i figuren/);
  await page.locator('#more-menu summary').click(); assert.equal(await page.locator('#mirror').isVisible(), true);
  await page.locator('#status').click(); assert.equal(await page.locator('#mirror').isVisible(), false);
  assert.deepEqual(problems, []); await page.close();
});

test('Ret ind lines the chosen parts up with the largest one, and waits until two parts are chosen', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 60], ['B', 40], ['H', 10]]) await typeMeasure(page, letter, value);
  await page.locator('.align-menu summary').click(); assert.equal(await page.locator('.align-items').isVisible(), false, 'one part: nothing to line up');
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  await page.locator('[data-add=cylinder]:not([data-mode]):not([data-sides])').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await page.locator('.align-menu summary').click(); await page.locator('[data-align="-1"][data-axis="0"]').click(); await ready(page);
  await page.locator('[data-align="1"][data-axis="1"]').click(); await ready(page);
  await page.locator('#object-list button').last().dispatchEvent('click');
  const plate = await page.locator('#object-list button').first();
  const [x, y] = (await place(page)).slice(0, 2);
  await plate.dispatchEvent('click'); const [px, py] = (await place(page)).slice(0, 2);
  assert.deepEqual([x - 10, y + 10], [px - 30, py + 20], 'left edges and back edges meet');
  assert.match(await page.locator('#status').textContent(), /rettet ind/);
  assert.deepEqual(problems, []); await page.close();
});

test('the camera follows a model that grows up out of the picture', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page); await page.waitForTimeout(300);
  await typeMeasure(page, 'H', 400); await page.waitForTimeout(300);
  // The arrow stands 40 px above the top of the part.
  const box = await page.locator('#workspace').boundingBox(), arrow = await page.locator('.handle.lift').boundingBox(), top = arrow.y + arrow.height / 2 + 40;
  assert(top > box.y + 10, `the top of the tall part is in view (top at ${top}, view from ${box.y})`);
  // Working close up on a model that is already partly out of view does not move the camera.
  await page.locator('[data-camera=in]').click(); await page.locator('[data-camera=in]').click(); await page.locator('[data-camera=in]').click(); await page.waitForTimeout(200);
  const close = await page.locator('.handle.lift').boundingBox(); await typeMeasure(page, 'L', 30); await page.waitForTimeout(300);
  assert.deepEqual(await page.locator('.handle.lift').boundingBox().then(b => b && Math.round(b.y)), close && Math.round(close.y));
  assert.deepEqual(problems, []); await page.close();
});

test('a grey button says why in its tip; a hole chosen with the name selected cuts through the plate too', async () => {
  const {page, problems} = await open();
  await page.locator('#center').hover(); await page.waitForTimeout(450);
  assert.match(await page.locator('#tip').textContent(), /Vælg to eller flere figurer først/);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 70], ['B', 26], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=text]').click(); await ready(page);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  const [, , bottom] = await place(page), [, , height] = await sizes(page);
  assert.equal(bottom, 0, 'the hole starts at the bottom of the plate'); assert(height > 5, `the hole reaches above the name, got ${height}`);
  await page.locator('#cut-holes').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /Del op/);
  assert.deepEqual(problems, []); await page.close();
});

test('parts that do not touch the rest are marked in the list', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert(await page.locator('#object-list .loose-part').count() > 0, 'apart, they are marked');
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.locator('#center').click(); await ready(page);
  assert.equal(await page.locator('#object-list .loose-part').count(), 0, 'stacked, they touch');
  assert.deepEqual(problems, []); await page.close();
});

test('a star lands flat, like a charm', async () => {
  const {page, problems} = await open();
  await page.locator('.more-shapes summary').click(); await page.locator('[data-add=star]').click(); await ready(page);
  assert.deepEqual(await sizes(page), [20, 20, 5]); assert.match(await page.locator('#object-list').textContent(), /Stjerne/);
  assert.deepEqual(problems, []); await page.close();
});

test('the list of parts is out of sight; a hidden part comes back with Vis skjulte', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert((await page.locator('.parts').boundingBox()).width <= 1, 'no list on screen');
  await page.locator('#position-details summary').click(); await page.locator('#hide').click(); await ready(page);
  await page.locator('#more-menu summary').click(); await page.locator('#show-hidden').click(); await ready(page);
  assert.equal(await page.locator('#selection-controls').isVisible(), true, 'the part is back and chosen');
  assert.doesNotMatch(await page.locator('#object-list').textContent(), /skjult/);
  assert.deepEqual(problems, []); await page.close();
});

test('clicking the chosen part again picks the part behind it, such as a hole inside a plate', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 40], ['B', 40], ['H', 10]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page); await typeMeasure(page, 'H', 6);
  await page.locator('[data-camera=top]').click(); await page.waitForTimeout(200);
  await page.locator('#object-list button').first().dispatchEvent('click'); await page.waitForTimeout(100);
  const corners = await page.evaluate(() => [...document.querySelectorAll('.handle.corner .knob')].map(knob => { const r = knob.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }));
  const middle = [0, 1].map(axis => corners.reduce((sum, point) => sum + point[axis], 0) / corners.length);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  const chosen = () => page.locator('#object-list button[aria-pressed=true]').textContent();
  await page.mouse.click(...middle); await page.waitForTimeout(100); assert.doesNotMatch(await chosen(), /hul/i, 'first click: the plate on top');
  await page.mouse.click(...middle); await page.waitForTimeout(100); assert.match(await chosen(), /hul/i, 'second click: the hole inside');
  await page.mouse.click(...middle); await page.waitForTimeout(100); assert.doesNotMatch(await chosen(), /hul/i, 'third click: back to the plate');
  assert.deepEqual(problems, []); await page.close();
});

test('a cylinder can get flat sides, and 0 makes it round again', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=cylinder]:not([data-mode]):not([data-sides])').click(); await ready(page);
  await page.locator('#sides').fill('6'); await page.locator('#sides').press('Enter'); await ready(page);
  assert.equal(await page.locator('#sides').inputValue(), '6'); assert.deepEqual(await sizes(page), [20, 20, 20]);
  await page.locator('#sides').fill('2'); await page.locator('#sides').press('Enter'); await page.waitForTimeout(100);
  assert.match(await page.locator('#status').textContent(), /fra 3 til 64/);
  await page.locator('#undo').click(); await ready(page); assert.equal(await page.locator('#sides').inputValue(), '0');
  assert.deepEqual(problems, []); await page.close();
});

test('a new part takes the colour chosen last', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await page.locator('#color-row [aria-label="Blå"]').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  await page.locator('[data-add=sphere]').click(); await ready(page);
  assert.equal(await page.locator('#color-row [aria-label="Blå"]').getAttribute('aria-pressed'), 'true');
  assert.deepEqual(problems, []); await page.close();
});

test('a tube gets a wall in millimetres', async () => {
  const {page, problems} = await open();
  await page.locator('.more-shapes summary').click(); await page.locator('[data-add=tube]').click(); await ready(page);
  assert.equal(await page.locator('#wall').inputValue(), '3.6', 'the standard wall is shown');
  await page.locator('#wall').fill('1.5'); await page.locator('#wall').press('Enter'); await ready(page);
  assert.equal(await page.locator('#wall').inputValue(), '1.5'); assert.deepEqual(await sizes(page), [20, 20, 20]);
  assert.deepEqual(problems, []); await page.close();
});

test('a heart dragged in lands flat; choosing several parts names the tools for them', async () => {
  const {page, problems} = await open();
  await page.locator('.more-shapes summary').click();
  await page.locator('[data-add=heart]').dragTo(page.locator('#workspace'), {targetPosition: {x: 600, y: 450}}); await ready(page);
  assert.deepEqual(await sizes(page), [20, 20, 5]);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  assert.match(await page.locator('#status').textContent(), /Sæt midt på/);
  assert.deepEqual(problems, []); await page.close();
});

test('fast typing with Tab keeps every measure, also while the model is calculated', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('[data-size="0"]').click(); await page.keyboard.type('40'); await page.keyboard.press('Tab');
  await page.keyboard.type('30'); await page.keyboard.press('Tab'); await page.keyboard.type('4'); await page.keyboard.press('Tab');
  await ready(page); await page.waitForTimeout(300); await ready(page);
  assert.deepEqual(await sizes(page), [40, 30, 4]);
  assert.deepEqual(problems, []); await page.close();
});

test('with several parts chosen, the panel offers stacking, lining up and melting', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=sphere]').click(); await ready(page);
  assert.equal(await page.locator('#many-actions').isVisible(), false, 'one part: no tools for several');
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  assert.equal(await page.locator('#many-actions').isVisible(), true);
  await page.locator('[data-proxy=center]').click(); await ready(page);
  assert.equal(await page.locator('#object-list .loose-part').count(), 0, 'stacked, they touch');
  await page.locator('[data-proxy=align]').click(); assert.equal(await page.locator('.align-items').isVisible(), true);
  assert.deepEqual(problems, []); await page.close();
});

test('an SVG drawing becomes a flat part 40 mm across; lines become bands; text alone is explained', async () => {
  const {page, problems} = await open();
  await page.locator('.more-shapes summary').click();
  const ring = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><path fill="#000" d="M0 0H100V50H0Z M20 10V40H80V10Z"/></svg>';
  await page.locator('#svg-file').setInputFiles({name: 'ramme.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(ring)}); await page.locator('#status', {hasText: 'ramme er hentet ind'}).waitFor(); await ready(page);
  assert.deepEqual(await sizes(page), [40, 20, 3]); assert.match(await page.locator('#status').textContent(), /ramme er hentet ind som en flad figur/);
  // A drawing of lines only: each line becomes a band as wide as the line.
  const lines = '<svg xmlns="http://www.w3.org/2000/svg"><path fill="none" stroke="#000" stroke-width="4" d="M0 0L60 0L60 30"/></svg>';
  await page.locator('#svg-file').setInputFiles({name: 'streg.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(lines)}); await page.locator('#status', {hasText: 'streg er hentet ind'}).waitFor(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /streg er hentet ind/);
  const empty = '<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="10">Hej</text></svg>';
  await page.locator('#svg-file').setInputFiles({name: 'tekst.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(empty)}); await page.waitForTimeout(200);
  assert.match(await page.locator('#status').textContent(), /stier/);
  assert.deepEqual(problems, []); await page.close();
});

test('the print check finds a part too thin to print on a thicker model, and fixes it', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 60], ['B', 30], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=text]').click(); await ready(page);
  await page.locator('#text-depth').fill('0.4'); await page.locator('#text-depth').press('Enter'); await ready(page);
  await page.locator('#download').click(); await nameIfAsked(page, 'brik');
  await page.locator('#print-check').waitFor({state: 'visible'});
  assert.match(await page.locator('#print-issues').textContent(), /0,4 mm tyk/);
  await page.locator('#print-issues button', {hasText: 'Gør den 1 mm'}).click(); await ready(page);
  assert.equal(await page.locator('#text-depth').inputValue(), '1');
  assert.deepEqual(problems, []); await page.close();
});

test('the measures on a part that is small on screen shrink out of the way', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 3], ['B', 3], ['H', 3]]) await typeMeasure(page, letter, value);
  await page.locator('[data-camera=home]').click(); await page.waitForTimeout(200);
  assert.equal(await page.locator('.measure-layer').evaluate(layer => layer.classList.contains('tiny')), true);
  for (const [letter, value] of [['L', 40], ['B', 40], ['H', 40]]) await typeMeasure(page, letter, value); await page.waitForTimeout(200);
  assert.equal(await page.locator('.measure-layer').evaluate(layer => layer.classList.contains('tiny')), false, 'a larger part has normal measures');
  assert.deepEqual(problems, []); await page.close();
});

test('a name on a plate made a hole sinks 1 mm into the plate, in one undo step', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 60], ['B', 25], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=text]').click(); await ready(page);
  const before = (await place(page))[2]; assert.equal(before, 3.5);
  await page.locator('#hole').click(); await ready(page);
  assert.equal((await place(page))[2], 3, 'the text now reaches 1 mm into the plate');
  assert.match(await page.locator('#status').textContent(), /sænket 1 mm/);
  await page.locator('#undo').click(); await ready(page);
  assert.equal((await place(page))[2], 3.5); assert.equal(await page.locator('#solid').getAttribute('aria-pressed'), 'true');
  assert.deepEqual(problems, []); await page.close();
});

test('on a 1280 × 720 screen the workshop fits without the page scrolling, and the colours stay on one line', async () => {
  const {page, problems} = await open();
  await page.setViewportSize({width: 1280, height: 720}); await page.waitForTimeout(100);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.deepEqual(await page.evaluate(() => [document.documentElement.scrollHeight <= innerHeight, document.documentElement.scrollWidth <= innerWidth]), [true, true]);
  assert.equal(await page.locator('.library').evaluate(library => library.scrollHeight <= library.clientHeight), true, 'every shape fits without scrolling');
  const tops = await page.locator('#color-row .swatch').evaluateAll(all => new Set(all.map(swatch => { const r = swatch.getBoundingClientRect(); return Math.round(r.top + r.height / 2); })).size);
  assert.equal(tops, 1);
  assert.deepEqual(problems, []); await page.close();
});

test('a tablet held upright (768 × 1024) fits without the page scrolling', async () => {
  const {page, problems} = await open();
  await page.setViewportSize({width: 768, height: 1024}); await page.waitForTimeout(100);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.deepEqual(await page.evaluate(() => [document.documentElement.scrollHeight <= innerHeight, document.documentElement.scrollWidth <= innerWidth]), [true, true]);
  assert.deepEqual(problems, []); await page.close();
});

test('a drawing fetched with a plate chosen lies on the plate, 80 % of its size', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 50], ['B', 50], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('.more-shapes summary').click();
  const square = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0H10V10H0Z"/></svg>';
  await page.locator('#svg-file').setInputFiles({name: 'logo.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(square)});
  await page.locator('#status', {hasText: 'oven på figuren'}).waitFor(); await ready(page);
  assert.deepEqual(await sizes(page), [40, 40, 2]); assert.deepEqual(await place(page), [0, 0, 3.5]);
  assert.deepEqual(problems, []); await page.close();
});

test('review fixes: clicking again reaches every part under the pointer; a sunk text stands up again; a wrong number leaves no stale tip', async () => {
  const {page, problems} = await open();
  // Three plates on top of each other, seen from above: each click goes one further down, then round again.
  for (const z of [0, 4, 8]) {
    await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
    for (const [letter, value] of [['L', 30], ['B', 30], ['H', 4]]) await typeMeasure(page, letter, value);
    if (!(await page.locator('#position-details').evaluate(d => d.open))) await page.locator('#position-details summary').click();
    for (const [axis, value] of [[0, 0], [1, 0], [2, z]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  }
  await page.locator('[data-camera=top]').click(); await page.waitForTimeout(200);
  const box = await page.locator('#canvas canvas').boundingBox(), handles = await page.evaluate(() => [...document.querySelectorAll('.handle.corner .knob')].map(k => { const r = k.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }));
  const middle = [0, 1].map(axis => handles.reduce((sum, point) => sum + point[axis], 0) / handles.length);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  const bottom = async () => (await place(page))[2], seen = [];
  for (let i = 0; i < 4; i++) { await page.mouse.click(...middle); await page.waitForTimeout(120); seen.push(await bottom()); }
  assert.deepEqual(seen, [8, 4, 0, 8], `clicks went ${seen}`); assert(box);
  // A text 2 mm thick sinks as a hole and stands on the plate again when made solid.
  await page.locator('[data-add=text]').click(); await ready(page);
  await page.locator('#text-depth').fill('2'); await page.locator('#text-depth').press('Enter'); await ready(page);
  const standing = (await place(page))[2];
  await page.locator('#hole').click(); await ready(page); assert.equal((await place(page))[2], standing - .5, 'sunk');
  await page.locator('#solid').click(); await ready(page); assert.equal((await place(page))[2], standing, 'up again');
  // A wrong number: the field's tip is its own name afterwards.
  await page.locator('[data-size="0"]').fill('999'); await page.locator('[data-size="0"]').press('Enter'); await page.waitForTimeout(2700);
  assert.equal(await page.locator('[data-size="0"]').evaluate(input => input.dataset.tip || input.title || ''), '');
  assert.deepEqual(problems, []); await page.close();
});

test('Ret ind says when a locked part stays where it is', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 60], ['B', 40], ['H', 10]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await page.locator('#position-details summary').click(); await page.locator('#lock').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await page.locator('#align-menu summary').click(); await page.locator('[data-align="-1"][data-axis="0"]').click(); await page.waitForTimeout(150);
  assert.match(await page.locator('#status').textContent(), /Den låste figur kan ikke flyttes\. Lås den op først/);
  assert.deepEqual(problems, []); await page.close();
});

test('the file name comes from the word in the model, also when the word is engraved', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 60], ['B', 25], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=text]').click(); await ready(page);
  await page.locator('#text-value').fill('Emil'); await page.locator('#text-value').press('Enter'); await ready(page);
  let download = page.waitForEvent('download'); await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.locator('#group').click(); await ready(page);
  await page.locator('#download').click(); assert.equal((await download).suggestedFilename(), 'Emil.stl', 'a raised name names the file');
  await page.locator('#undo').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  await page.mouse.click(10, 10); await page.locator('#object-list button', {hasText: 'Emil'}).dispatchEvent('click');
  await page.locator('#hole').click(); await ready(page); await page.locator('#cut-holes').click(); await ready(page);
  await page.locator('#download').click(); await page.locator('#name-dialog').waitFor({state: 'visible'});
  assert.equal(await page.locator('#model-name').inputValue(), 'Emil', 'an engraved name is offered');
  download = page.waitForEvent('download'); await page.keyboard.press('Enter'); assert.equal((await download).suggestedFilename(), 'Emil.stl');
  assert.deepEqual(problems, []); await page.close();
});

test('round 10: a name made before the plate is cut from the top; the hexagon has its own button; Hul works right after typing', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=text]').click(); await ready(page);
  await page.locator('#text-value').fill('Ida');
  // Hul is pressed straight after typing: the text change and the hole both happen.
  await page.locator('#hole').click(); await ready(page); await page.waitForTimeout(200); await ready(page);
  assert.equal(await page.locator('#hole').getAttribute('aria-pressed'), 'true'); assert.equal(await page.locator('#text-value').inputValue(), 'Ida');
  await page.locator('#undo').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  // The plate drawn over the name afterwards.
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 70], ['B', 26], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('#position-details summary').click();
  for (const [axis, value] of [[0, 0], [1, 0], [2, 0]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  await page.locator('#object-list button', {hasText: 'Ida'}).dispatchEvent('click');
  await page.locator('#hole').click(); await ready(page);
  assert.equal((await place(page))[2], 3, 'the name now cuts 1 mm down from the top of the plate');
  await page.locator('.more-shapes summary').click(); await page.locator('[data-add=cylinder][data-sides="6"]').click(); await ready(page);
  assert.equal(await page.locator('#sides').inputValue(), '6');
  assert.deepEqual(problems, []); await page.close();
});

test('Fordel gives three parts equal gaps; the outermost stay', async () => {
  const {page, problems} = await open();
  await page.locator('#position-details summary').click().catch(() => {});
  for (const x of [-40, -25, 40]) {
    await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
    if (!(await page.locator('#position-details').evaluate(d => d.open))) await page.locator('#position-details summary').click();
    for (const [axis, value] of [[0, x], [1, 0]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  }
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await page.locator('#align-menu summary').click(); await page.locator('[data-spread="0"]').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /20 mm mellemrum/);
  await page.locator('#object-list button').nth(1).dispatchEvent('click'); assert.deepEqual((await place(page)).slice(0, 2), [0, 0]);
  assert.deepEqual(problems, []); await page.close();
});

test('Drej turns itself off when nothing is chosen any more', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#rotate-mode').click(); assert.equal(await page.locator('#rotate-mode').getAttribute('aria-pressed'), 'true');
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  assert.equal(await page.locator('#rotate-mode').getAttribute('aria-pressed'), 'false');
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.equal(await page.locator('.handle.rotate:visible').count(), 0, 'the next part has no arcs until Drej is pressed again');
  assert.deepEqual(problems, []); await page.close();
});

test('the camera glides to a new view and arrives where it jumps to with reduced motion', async () => {
  const where = async page => page.evaluate(() => [...document.querySelectorAll('.cube-face')].map(face => face.style.transform).join('|'));
  const still = await browser.newPage({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'}), moving = await browser.newPage({viewport: {width: 1440, height: 900}, reducedMotion: 'no-preference'});
  for (const page of [still, moving]) { await page.goto(base); await page.waitForFunction(() => !/Klargør/.test(document.querySelector('#status').textContent)); }
  await still.locator('[data-camera=top]').click(); await moving.locator('[data-camera=top]').click();
  const during = await where(moving); await moving.waitForTimeout(500);
  assert.notEqual(during, await where(moving), 'the view is still on its way right after the click');
  assert.equal(await where(moving), await where(still), 'and ends where the jump goes');
  await still.close(); await moving.close();
});

test('round 11: quick clicks do not stack shapes; the text follows the typing without Enter', async () => {
  const {page, problems} = await open();
  for (const kind of ['box', 'sphere', 'cone', 'box']) await page.locator(`[data-add=${kind}]:not([data-mode])`).first().click();
  await ready(page); await page.waitForTimeout(300); await ready(page);
  const centres = [];
  for (let i = 0; i < 4; i++) { await page.locator('#object-list button').nth(i).dispatchEvent('click'); centres.push((await place(page)).slice(0, 2).join(',')); }
  assert.equal(new Set(centres).size, 4, `each shape has its own spot: ${centres}`);
  await page.locator('[data-add=text]').click(); await ready(page);
  await page.locator('#text-value').fill('Ida'); await page.waitForTimeout(900); await ready(page);
  assert.match(await page.locator('#object-list').textContent(), /Ida/, 'the 3D text changed without Enter');
  assert.deepEqual(problems, []); await page.close();
});

test('the print check finds a text hanging over the edge of its part, and makes it fit', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 30], ['B', 20], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  await page.locator('[data-add=text]').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a'); await page.locator('#center').click(); await ready(page);
  await page.locator('#download').click(); await nameIfAsked(page, 'skilt'); await page.locator('#print-check').waitFor({state: 'visible'});
  assert.match(await page.locator('#print-issues').textContent(), /stikker ud over kanten/);
  await page.locator('#print-issues button', {hasText: 'Gør teksten mindre'}).click(); await ready(page);
  const [width] = await sizes(page); assert(width <= 27.5, `the text now fits: ${width} mm`);
  assert.deepEqual(problems, []); await page.close();
});

test('pointing at a Ret ind button shows where the parts would line up, without moving them', async () => {
  const {page, problems} = await open();
  for (const kind of ['box', 'sphere']) { await page.locator(`[data-add=${kind}]:not([data-mode])`).first().click(); await ready(page); }
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await page.locator('#object-list button').last().dispatchEvent('click', {shiftKey: false}); const before = await place(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await page.locator('#align-menu summary').click(); await page.locator('[data-align="-1"][data-axis="1"]').hover(); await page.waitForTimeout(200);
  assert.match(await page.locator('#status').textContent(), /Sæt midt på|klar|valgt|Ret ind/, 'nothing was changed by pointing');
  await page.locator('#object-list button').last().dispatchEvent('click'); assert.deepEqual(await place(page), before, 'the part has not moved');
  assert.deepEqual(problems, []); await page.close();
});

test('review 2: the hexagon is regular; Ctrl+A leaves hidden parts out; a star dropped on a box stands on it', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=cylinder][data-sides="6"]').click(); await ready(page);
  assert.deepEqual(await sizes(page), [20, 17.32, 20]);
  await page.locator('#position-details summary').click(); await page.locator('#hide').click(); await ready(page);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  assert.equal(await page.locator('#selection-label').textContent(), 'Kasse', 'only the visible box is chosen');
  const lift = await handleAt(page, '.handle.lift');
  await page.locator('[data-add=star]').dragTo(page.locator('#workspace'), {targetPosition: {x: lift[0] - (await page.locator('#workspace').boundingBox()).x, y: lift[1] + 60 - (await page.locator('#workspace').boundingBox()).y}}); await ready(page);
  const [, , bottom] = await place(page); assert(bottom >= 19 && bottom <= 20, `the star stands on the box, bottom ${bottom}`);
  assert.deepEqual(problems, []); await page.close();
});

test('with the part behind chosen, a drag moves the part the pupil takes hold of', async () => {
  const {page, problems} = await open();
  for (const z of [0, 4]) {
    await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
    for (const [letter, value] of [['L', 30], ['B', 30], ['H', 4]]) await typeMeasure(page, letter, value);
    if (!(await page.locator('#position-details').evaluate(d => d.open))) await page.locator('#position-details summary').click();
    for (const [axis, value] of [[0, 0], [1, 0], [2, z]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  }
  await page.locator('[data-camera=top]').click(); await page.waitForTimeout(200);
  const corners = await page.evaluate(() => [...document.querySelectorAll('.handle.corner .knob')].map(k => { const r = k.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }));
  const middle = [0, 1].map(axis => corners.reduce((sum, point) => sum + point[axis], 0) / corners.length);
  // The bottom plate is chosen; the top plate lies over it on screen.
  await page.locator('#object-list button').first().dispatchEvent('click');
  await page.mouse.move(...middle); await page.mouse.down(); await page.mouse.move(middle[0] + 60, middle[1], {steps: 8}); await page.mouse.up(); await ready(page);
  await page.locator('#object-list button').first().dispatchEvent('click'); assert.deepEqual((await place(page)).slice(0, 2), [0, 0], 'the plate behind stays');
  await page.locator('#object-list button').last().dispatchEvent('click'); assert.notDeepEqual((await place(page)).slice(0, 2), [0, 0], 'the plate taken hold of moved');
  assert.deepEqual(problems, []); await page.close();
});

test('a new part opens with Drejning and Placering closed, so its fields stand where they always do', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#rotation-controls summary').click(); await page.locator('#position-details summary').click();
  await page.locator('[data-add=sphere]').click(); await ready(page);
  assert.deepEqual(await page.evaluate(() => [document.querySelector('#rotation-controls').open, document.querySelector('#position-details').open]), [false, false]);
  assert.deepEqual(problems, []); await page.close();
});
