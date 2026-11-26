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
import {createHash} from 'node:crypto';
import {strToU8, strFromU8, unzipSync, zipSync} from 'fflate';
import {sourcePath} from '../scripts/repository-layout.mjs';

const root = fileURLToPath(new URL('../', import.meta.url)), web = path.join(root, 'apps/3dprint/web');
const {chromium} = createRequire(path.join(root, 'package.json'))('playwright');
const types = {'.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.ttf': 'font/ttf', '.png': 'image/png', '.svg': 'image/svg+xml', '.json': 'application/json', '.txt': 'text/plain; charset=utf-8'};
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
    const route = new URL(request.url, 'http://x').pathname;
    // The standalone editor has no project shelf or saved school profile.
    if (route === '/project-import-config.js') return response.writeHead(200, {'Content-Type':'text/javascript'}).end('window.ElevhubProjectOrigins=[];');
    if (route === '/api/school-appearance') return response.writeHead(200, {'Content-Type':'application/json'}).end(JSON.stringify({theme:'red',school_name:'Syntetisk QA-skole'}));
    const file = path.resolve(web, '.' + decodeURIComponent(route));
    if (!file.startsWith(web + path.sep)) { response.writeHead(403).end(); return; }
    try {
      const body = await readFile(route === '/autosave.js' ? file : sourcePath('web/' + path.relative(web, file).split(path.sep).join('/'), root));
      response.writeHead(200, {'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'}).end(body);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}/model3d.html`;
  browser = await chromium.launch({headless: true, channel: 'chrome'});
});
test.after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });

async function open({managed = false} = {}) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, acceptDownloads: true, reducedMotion: 'reduce'}), problems = [];
  page.setDefaultTimeout(6000);
  if (managed) await page.addInitScript(() => { window.ElevhubApp = {}; });
  page.on('pageerror', error => problems.push(error.message));
  page.on('console', message => { if (['error', 'warning'].includes(message.type())) problems.push(`${message.text()} (${message.location().url})`); });
  page.on('request', request => { if (!request.url().startsWith(base.slice(0, base.lastIndexOf('/')))) problems.push('external request ' + request.url()); });
  await page.goto(base); await ready(page);
  return {page, problems};
}
const ready = page => page.waitForFunction(() => !document.querySelector('[data-add=box]').disabled && !/Beregner|Klargør/.test(document.querySelector('#status').textContent));
// A model without text asks for a name the first time it is downloaded.
async function nameIfAsked(page, name = 'testmodel') { await page.waitForTimeout(50); if (await page.locator('#name-dialog').isVisible()) { await page.locator('#model-name').fill(name); await page.locator('#name-form button[type=submit]').click(); } }
const field = (page, selector) => page.locator(selector).inputValue().then(value => Number(value.replace(',', '.')));

async function viewFrom(page, direction) {
  await page.locator('#camera-more > summary').click();
  await page.locator(`[data-camera=${direction}]`).click();
}

async function chooseExercise(page) {
  await page.locator('#help-menu > summary').click();
  await page.locator('#ideas').click();
}

async function openAlignment(page) {
  await page.locator('#more-menu > summary').click();
  await page.locator('#align-menu > summary').click();
}

async function importFile(page, type, file) {
  await page.locator('#project-menu > summary').click();
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.locator(`#import-${type}`).click()]);
  await chooser.setFiles(file);
}

const savedProject = page => page.evaluate(async () => await (await window.ElevhubProjectAdapter.export()).text());
async function reselectFirstPart(page, editor) {
  await page.locator('#panel-close').click();
  await editor.waitFor({state: 'hidden'});
  await page.locator('#panel-open').click();
  await editor.waitFor({state: 'visible'});
  await page.locator('#workspace').press('Escape');
  await editor.waitFor({state: 'hidden'});
  await page.locator('#show-parts').click();
  await page.locator('#object-list button').first().click();
  await editor.waitFor({state: 'visible'});
}
async function reopenFirstPart(page, saved, editor) {
  await page.evaluate(project => window.ElevhubProjectAdapter.import(new Blob([project]), 'Generatorprøve'), saved);
  await ready(page);
  await page.locator('#show-parts').click();
  await page.locator('#object-list button').first().click();
  await editor.waitFor({state: 'visible'});
}

test('the shape library switches categories and opens the game-piece generator by keyboard', async () => {
  const {page, problems} = await open();
  try {
    const category = page.locator('#shape-category');
    await category.waitFor({state: 'visible', timeout: 1500});
    assert.equal(await category.inputValue(), 'basic');
    assert.equal(await page.getByRole('button', {name: 'Kasse', exact: true}).isVisible(), true);

    await category.selectOption('hardware');
    const washer = page.getByRole('button', {name: 'Spændeskive', exact: true});
    assert.equal(await washer.isVisible(), true);
    assert.equal(await page.getByRole('button', {name: 'Kasse', exact: true}).count(), 0);
    await viewFrom(page, 'top');
    const workspace = page.locator('#workspace'), canvas = await workspace.boundingBox();
    await washer.dragTo(workspace, {targetPosition: {x: 8, y: Math.round(canvas.height / 2)}});
    await ready(page);
    const [x] = await place(page), [width] = await sizes(page);
    assert(x < -70, 'dropping near the left edge keeps the washer near that edge');
    assert(Math.abs(x) + width / 2 <= 90.01, 'the whole washer fits on the plate');
    await category.selectOption('everyday');
    assert.equal(await washer.count(), 0);
    const cup = page.getByRole('button', {name: 'Kop', exact: true});
    await cup.click(); await ready(page);
    assert.deepEqual(await sizes(page), [117, 78, 90]);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await page.getByRole('button', {name: 'Æske', exact: true}).count(), 0);
    await page.getByRole('button', {name: 'Toiletrulle', exact: true}).click(); await ready(page);
    assert.deepEqual(await sizes(page), [115, 115, 95]);
    await page.locator('#undo').click(); await ready(page);
    await page.getByRole('button', {name: 'Mobiltelefon', exact: true}).click(); await ready(page);
    assert.deepEqual(await sizes(page), [72, 151, 9]);
    await page.locator('#undo').click(); await ready(page);

    await category.selectOption({label: 'Byggedele'});
    assert.equal(await category.locator('option').getByText('Generatorer', {exact: true}).count(), 0);
    const gamePiece = page.locator('[data-preset=game-piece]');
    assert.equal(await gamePiece.locator('svg path').count(), 1);
    for (const kind of ['gear', 'nut', 'rod']) assert.equal(await page.locator(`[data-preset=${kind}]`).isVisible(), true);
    await gamePiece.focus();
    await page.keyboard.press('Enter');
    await page.locator('#game-piece-editor').waitFor({state: 'visible'});
    await ready(page);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik/);
    await page.locator('#workspace').press('Escape');
    await page.locator('#game-piece-editor').waitFor({state: 'hidden'});
    assert.equal(await category.inputValue(), 'hardware');
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('the expanded library exposes 30 basic cards, 15 configurable building parts and the final Inspiration category', async () => {
  const {page, problems} = await open();
  try {
    await page.setViewportSize({width: 1164, height: 655});
    const category = page.locator('#shape-category');
    assert.equal(await page.locator('#basic-shapes button:visible').count(), 30);
    for (const id of ['trapezoid', 'parallelogram']) {
      const label = page.locator(`[data-preset=${id}] .shape-name`);
      assert(await label.evaluate(element => element.scrollWidth <= element.clientWidth + 1), `${id} fits inside its card`);
    }
    await category.selectOption('hardware');
    assert.equal(await page.locator('#preset-shapes button:visible').count(), 15);
    await category.selectOption('inspiration');
    assert.equal(await category.locator('option').last().textContent(), 'Inspiration');
    assert.equal(await page.locator('#preset-shapes button:visible').count(), 11);
    await page.getByRole('button', {name: 'Robotkrop', exact: true}).click(); await ready(page);
    await page.locator('#catalog-editor').getByRole('heading', {name: 'Robotkrop'}).waitFor({state: 'visible'});
    assert.equal(await page.locator('#download').isEnabled(), true);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('form cards show their own compact 3D thumbnails across the library', async () => {
  const {page, problems} = await open();
  try {
    await page.setViewportSize({width: 1164, height: 655});
    const library = page.locator('#forms-panel'), category = page.locator('#shape-category');
    const checked = new Set();
    for (const [kind, count] of [['basic', 30], ['hardware', 15], ['everyday', 4], ['connectors', 6], ['inspiration', 11]]) {
      await category.selectOption(kind);
      const thumbnails = library.locator('#basic-shapes .shape-thumbnail:visible, #preset-shapes .shape-thumbnail:visible');
      await thumbnails.first().waitFor({state: 'visible', timeout: 2000});
      assert.equal(await thumbnails.count(), count);
      for (const {key, style, width, height, hidden} of await thumbnails.evaluateAll(nodes => nodes.map(node => ({
        key: node.dataset.formThumbnail, style: getComputedStyle(node).backgroundImage,
        width: node.clientWidth, height: node.clientHeight, hidden: node.getAttribute('aria-hidden'),
      })))) {
        assert(key && !checked.has(key)); checked.add(key);
        assert.match(style, /form-thumbnails\.png/);
        assert(width <= 56 && height <= 45);
        assert.equal(hidden, 'true');
      }
    }
    assert.deepEqual(await library.locator('.hole-shapes .shape-thumbnail:visible').evaluateAll(nodes =>
      nodes.map(node => node.dataset.formThumbnail)), ['cylinder-hole', 'box-hole']);
    await page.getByRole('button', {name: 'Robotarm', exact: true}).press('Enter'); await ready(page);
    await page.locator('#catalog-editor').getByRole('heading', {name: 'Robotarm'}).waitFor({state: 'visible'});
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a student makes and edits separate robot modules with compatible fixed snap fittings', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await page.locator('#shape-category').selectOption('inspiration');
    await page.getByRole('button', {name: 'Robotkrop', exact: true}).press('Enter'); await ready(page);
    await page.locator('#catalog-fit').waitFor({state: 'visible'});
    assert.match(await page.locator('#catalog-fit').textContent(), /arme i siderne/);
    await page.getByRole('button', {name: 'Robotarm', exact: true}).click(); await ready(page);
    const editor = page.locator('#catalog-editor'), length = editor.getByLabel('Længde uden tap (mm)', {exact: true});
    await length.fill('31'); await length.press('Enter'); await ready(page);
    const {scene} = JSON.parse(await savedProject(page));
    assert.equal(scene.roots.length, 2);
    const roots = scene.roots.map(id => scene.objects.find(node => node.id === id));
    const [torso, arm] = roots;
    assert(arm.position[1] < torso.position[1] - 15, 'the arm is visible in front of the taller body in the opening view');
    assert.equal(arm.generator.settings.values.length, 31);
    const connectors = part => scene.objects.filter(obj => part.children.includes(obj.id) && obj.generator?.kind === 'connector');
    assert.equal(connectors(torso).length, 6);
    assert(connectors(torso).every(node => node.mode === 'hole' && /^snap-9(?:$|-roof-)/.test(node.generator.settings.profile)));
    assert.equal(connectors(arm)[0].generator.settings.role, 'plug');
    assert.deepEqual(connectors(arm)[0].size, [8.8, 5.6, 12]);
    assert.equal(await page.locator('#download').isEnabled(), true);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('dragon parts are individually inserted and editable like other inspiration models', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await page.locator('#shape-category').selectOption('inspiration');
    assert.equal(await page.getByRole('button', {name: 'Dragesæt', exact: true}).count(), 0);
    await page.getByRole('button', {name: 'Dragekrop', exact: true}).press('Enter'); await ready(page);
    await page.locator('#catalog-editor').getByRole('heading', {name: 'Dragekrop'}).waitFor({state: 'visible'});
    assert.equal(JSON.parse(await savedProject(page)).scene.roots.length, 1);
    await page.getByRole('button', {name: 'Dragehoved', exact: true}).click(); await ready(page);
    await page.locator('#catalog-editor').getByRole('heading', {name: 'Dragehoved'}).waitFor({state: 'visible'});
    const {scene} = JSON.parse(await savedProject(page));
    const parts = scene.roots.map(id => scene.objects.find(node => node.id === id));
    assert.deepEqual(parts.map(part => part.generator.settings.recipe), ['dragon-torso', 'dragon-head']);
    assert.equal(await page.locator('#download').isEnabled(), true);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(JSON.parse(await savedProject(page)).scene.roots.length, 1);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('ball joint cards are fixed solid parts and the cup selects its host for joining', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await page.locator('[data-add=box]').first().click(); await ready(page);
    await page.locator('#shape-category').selectOption('connectors');
    await viewFrom(page, 'top');
    const workspace = page.locator('#workspace'), area = await workspace.boundingBox();
    await page.getByRole('button', {name: 'Kugleskål Ø9', exact: true}).dragTo(workspace, {
      targetPosition: {x: Math.round(area.width / 2), y: Math.round(area.height / 2)},
    });
    await ready(page);
    assert.equal(await page.locator('#group').isEnabled(), true);
    await page.locator('#group').click(); await ready(page);
    await page.getByRole('button', {name: 'Kugletap Ø9', exact: true}).press('Enter'); await ready(page);
    assert.equal(await page.locator('#connector-size-note').isVisible(), true);
    assert.equal(await page.locator('[data-size="0"]').getAttribute('readonly'), '');
    const {scene} = JSON.parse(await savedProject(page));
    assert.equal(scene.roots.length, 2);
    const joints = scene.objects.filter(node => node.generator?.settings.profile === 'ball-9');
    assert.deepEqual(joints.map(({mode, generator}) => [generator.settings.role, mode]), [['cup', 'solid'], ['plug', 'solid']]);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('catalog figure parameters change actual dimensions automatically and survive a project round trip', async () => {
  const {page, problems} = await open({managed: true});
  try {
    const category = page.locator('#shape-category'), menu = page.locator('#catalog-editor');
    await page.locator('[data-preset=torus]').press('Enter'); await ready(page);
    await menu.getByRole('heading', {name: 'Torus'}).waitFor({state: 'visible'});
    const height = menu.getByLabel('Højde (mm)', {exact: true});
    assert.equal((await sizes(page))[2], 8);
    await height.fill('15'); await height.press('Enter'); await ready(page);
    assert.equal((await sizes(page))[2], 15);
    const saved = await savedProject(page);
    await page.locator('#undo').click(); await ready(page);
    assert.equal((await sizes(page))[2], 8);
    await reopenFirstPart(page, saved, menu);
    assert.equal(await height.inputValue(), '15');
    assert.equal((await sizes(page))[2], 15);

    await page.locator('#show-forms').click(); await category.selectOption('hardware');
    await page.locator('[data-preset=mounting-plate]').click(); await ready(page);
    await menu.getByRole('heading', {name: 'Monteringsplade'}).waitFor({state: 'visible'});
    const thickness = menu.getByLabel('Tykkelse (mm)', {exact: true});
    assert.equal((await sizes(page))[2], 5);
    await thickness.fill('8'); await thickness.press('Enter'); await ready(page);
    assert.equal((await sizes(page))[2], 8);
    const savedScene = JSON.parse(await savedProject(page)).scene;
    const plate = savedScene.objects.find(node => node.name === 'Monteringsplade');
    assert.equal(plate.op, 'difference');
    assert.equal(plate.children.filter(id => savedScene.objects.find(node => node.id === id).mode === 'hole').length, 4);
    assert.equal(plate.generator.settings.values.thickness, 8);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a native star saves its edited outline and reopens with the same point count', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await page.getByRole('button', {name: 'Stjerne', exact: true}).click(); await ready(page);
    const editor = page.locator('#profile-editor'), points = editor.getByLabel('Antal spidser', {exact: true});
    assert.equal(await points.inputValue(), '5');
    await points.fill('9'); await points.press('Enter'); await ready(page);
    const saved = await savedProject(page);
    assert.deepEqual(JSON.parse(saved).scene.objects[0].params, {points: 9});
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await points.inputValue(), '5');
    await reopenFirstPart(page, saved, editor);
    assert.equal(await points.inputValue(), '9');
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a socket dropped on a box keeps its mouth and its fixed size when the assembly gets taller', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await addBox(page, [36, 30, 24], [0, 0, 0]);
    await page.locator('#shape-category').selectOption('connectors');
    assert.equal(await page.locator('#preset-shapes button:visible').count(), 6);
    await viewFrom(page, 'top');
    const workspace = page.locator('#workspace'), area = await workspace.boundingBox();
    await page.locator('[data-preset=snap-9-socket]').dragTo(workspace, {targetPosition: {x: Math.round(area.width / 2), y: Math.round(area.height / 2)}});
    await page.locator('#connector-size-note').waitFor({state: 'visible'}); await ready(page);
    assert.deepEqual(await sizes(page), [9.4, 6.4, 12.8]);
    assert.equal(await page.locator('[data-size="0"]').getAttribute('readonly'), '');
    assert.equal(await page.locator('.handle.resize').count(), 0);
    assert(Math.abs((await place(page))[2] - 11.25) < .031);
    await page.locator('#cut-holes').click(); await ready(page);
    await writeMeasure(page, '[data-size="2"]', 36);
    const {scene} = JSON.parse(await savedProject(page));
    const group = scene.objects.find(node => node.id === scene.roots[0]);
    const socket = scene.objects.find(node => node.generator?.kind === 'connector');
    assert.deepEqual(group.children.filter(id => id === socket.id), [socket.id]);
    assert.equal(socket.mode, 'hole');
    assert.deepEqual(socket.size, [9.4, 6.4, 12.8]);
    assert.equal(socket.generator.origin[2], -6.4);
    assert(Math.abs(group.matrix[10] * socket.matrix[10] - 1) < .0001);
    assert(Math.abs(group.matrix[10] * socket.matrix[14] + group.matrix[14] + 6.4 - 36.075) < .001);
    const corner = page.locator('.handle.corner:not(.off) .knob').first(), spot = await corner.boundingBox();
    await page.mouse.move(spot.x + spot.width / 2, spot.y + spot.height / 2);
    await page.mouse.down(); await page.mouse.move(spot.x - 26, spot.y - 18, {steps: 4}); await page.mouse.up(); await ready(page);
    const after = JSON.parse(await savedProject(page)).scene.objects;
    const grown = after.find(node => node.id === group.id), fixed = after.find(node => node.id === socket.id);
    assert.notDeepEqual(grown.matrix, group.matrix);
    assert(Math.abs(grown.matrix[0] * fixed.matrix[0] - 1) < .0001);
    assert(Math.abs(grown.matrix[5] * fixed.matrix[5] - 1) < .0001);
    assert.equal(await page.locator('#download').isEnabled(), true);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a snap socket follows the vertical face it is dropped on', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await addBox(page, [36, 30, 24], [0, 0, 0]);
    await page.locator('#shape-category').selectOption('connectors');
    await viewFrom(page, 'front');
    const workspace = page.locator('#workspace'), area = await workspace.boundingBox();
    await page.locator('[data-preset=snap-9-socket]').dragTo(workspace, {targetPosition: {x: Math.round(area.width / 2), y: Math.round(area.height / 2)}});
    await page.locator('#connector-size-note').waitFor({state: 'visible'}); await ready(page);
    const {scene} = JSON.parse(await savedProject(page));
    const socket = scene.objects.find(node => node.generator?.kind === 'connector');
    assert(Math.abs(socket.matrix[9] + 1) < .0001);
    assert(Math.abs(socket.matrix[10]) < .0001);
    assert(Math.abs(socket.matrix[13] + 8.65) < .001);
    await page.locator('#cut-holes').click(); await ready(page);
    assert.equal(await page.locator('#download').isEnabled(), true);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a dropped snap plug selects its host so Saml is ready', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await addBox(page, [36, 30, 24], [0, 0, 0]);
    await page.locator('#shape-category').selectOption('connectors');
    await viewFrom(page, 'front');
    const workspace = page.locator('#workspace'), area = await workspace.boundingBox();
    await page.locator('[data-preset=snap-9-plug]').dragTo(workspace, {targetPosition: {x: Math.round(area.width / 2), y: Math.round(area.height / 2)}});
    await ready(page);
    assert.equal(await page.locator('#group').isEnabled(), true);
    await page.locator('#group').click(); await ready(page);
    const {scene} = JSON.parse(await savedProject(page));
    const group = scene.objects.find(node => node.id === scene.roots[0]);
    assert.equal(scene.roots.length, 1);
    assert.equal(group.children.length, 2);
    assert(scene.objects.some(node => node.generator?.settings.profile === 'snap-9' && node.mode === 'solid'));
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('gears update automatically, preserve an invalid draft and restore settings after undo and project reopen', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await page.locator('#shape-category').selectOption('hardware', {timeout: 1500});
    await page.locator('[data-preset=gear]').click();
    const dialog = page.locator('#gear-editor');
    await dialog.waitFor({state: 'visible'}); await ready(page);
    assert.equal(await dialog.getByRole('button', {name: /Opdatér/}).count(), 0);
    const teeth = dialog.getByLabel('Antal tænder', {exact: true});
    assert.equal(await teeth.inputValue(), '24');
    assert.equal(await page.locator('#object-list button').count(), 1);
    await teeth.fill('3');
    await dialog.getByRole('alert').waitFor({state: 'visible'});
    assert.equal(await page.locator('#object-list button').count(), 1);
    assert.equal(await teeth.evaluate(el => el === document.activeElement), true);
    assert.match(await page.locator('#selection-label').textContent(), /24 tænder/);
    await teeth.press('Control+a');
    await teeth.pressSequentially('32', {delay: 100});
    await page.locator('#selection-label').filter({hasText: /32 tænder/}).waitFor({state: 'visible'});
    await ready(page);
    assert.equal(await teeth.evaluate(el => el === document.activeElement), true);
    assert.equal(await dialog.getByRole('alert').isHidden(), true);
    assert.equal(await page.locator('#download').isEnabled(), true);

    assert.equal(await page.locator('#object-list button').count(), 1, 'updating replaces the selected gear');
    const saved = await savedProject(page);
    await reselectFirstPart(page, dialog);
    assert.equal(await teeth.inputValue(), '32');
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await teeth.inputValue(), '24', 'one undo restores the previous generator settings');
    assert.equal((await sizes(page))[2], 5);
    await page.locator('#redo').click(); await ready(page);
    assert.equal(await teeth.inputValue(), '32');
    await teeth.fill('35');
    const undo = await page.locator('#undo').boundingBox();
    await page.mouse.click(undo.x + undo.width / 2, undo.y + undo.height / 2);
    await ready(page);
    assert.equal(await teeth.inputValue(), '32', 'one click after typing undoes the edit committed on blur');
    assert.match(await page.locator('#selection-label').textContent(), /32 tænder/);
    await reopenFirstPart(page, saved, dialog);
    assert.equal(await teeth.inputValue(), '32');
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('clicking another part immediately after typing applies the generator edit and changes selection', async () => {
  const {page, problems} = await open();
  try {
    await page.locator('#shape-category').selectOption('hardware');
    await page.locator('[data-preset=rod]').click(); await ready(page);
    await page.locator('[data-preset=gear]').click(); await ready(page);
    await page.locator('#show-parts').click();
    const rod = page.locator('#object-list button').filter({hasText: /Gevindstang/});
    const teeth = page.locator('#gear-teeth');
    await teeth.fill('32');
    const target = await rod.boundingBox();
    await page.mouse.click(target.x + target.width / 2, target.y + target.height / 2);
    await ready(page);
    await page.locator('#rod-editor').waitFor({state: 'visible', timeout: 1500});
    assert.equal(await rod.evaluate(button => button === document.activeElement), true);
    assert.match(await page.locator('#object-list').textContent(), /32 tænder/);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a 3MF with two transformed parts can be imported, downloaded and dragged back to the plate', async () => {
  const {page, problems} = await open({managed: true});
  const content = zipSync({
    '[Content_Types].xml': strToU8('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'),
    '_rels/.rels': strToU8('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r0" Target="/3D/sample.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'),
    '3D/sample.model': strToU8('<model unit="centimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources><object id="1" name="Prøvefigur" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="2" z="0"/><vertex x="0" y="0" z="0.5"/></vertices><triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/><triangle v1="0" v2="3" v3="2"/><triangle v1="1" v2="2" v3="3"/></triangles></mesh></object></resources><build><item objectid="1"/><item objectid="1" transform="1 0 0 0 1 0 0 0 1 3 0 0"/></build></model>'),
  });
  try {
    async function verifyParts() {
      const scene = JSON.parse(await savedProject(page)).scene;
      assert.equal(scene.roots.length, 2);
      const parts = scene.objects.filter(node => node.kind === 'mesh').sort((a, b) => a.position[0] - b.position[0]);
      assert.deepEqual(parts.map(node => node.size), [[10, 20, 5], [10, 20, 5]]);
      assert.equal(parts[1].position[0] - parts[0].position[0], 30);
      assert.deepEqual(parts.map(node => node.position[2]), [2.5, 2.5]);
    }
    const projectMenu = page.locator('#project-menu > summary');
    await projectMenu.click();
    assert.equal(await page.getByRole('button', {name: 'Hent 3MF', exact: true}).isEnabled(), false);
    await projectMenu.click();
    await importFile(page, '3mf', {name: 'blokke.3mf', mimeType: 'model/3mf', buffer: Buffer.from(content)});
    await page.waitForFunction(() => document.getElementById('selection-label').textContent.includes('2 figurer'));
    await ready(page);
    await verifyParts();
    await importFile(page, '3mf', {name: 'beskadiget.3mf', mimeType: 'model/3mf', buffer: Buffer.from('ikke et arkiv')});
    await page.locator('#status').getByText(/3MF-filen|3MF-arkivet/).waitFor({state: 'visible'});
    await verifyParts();
    await page.locator('#undo').click(); await ready(page);
    assert.equal(JSON.parse(await savedProject(page)).scene.roots.length, 0);
    await page.locator('#redo').click(); await ready(page);
    await projectMenu.click();
    await page.getByRole('button', {name: 'Hent 3MF', exact: true}).click();
    await page.locator('#model-name').fill('Geometri');
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Hent 3MF', exact: true}).click();
    if (await page.locator('#print-check').isVisible()) await page.getByRole('button', {name: 'Hent 3MF alligevel'}).click();
    const download = await downloading;
    assert.equal(download.suggestedFilename(), 'Geometri.3mf');
    const bytes = await readFile(await download.path());
    const modelXml = Object.entries(unzipSync(bytes)).find(([name]) => name.endsWith('.model'))?.[1];
    assert.match(strFromU8(modelXml), /unit="millimeter"/);
    await page.evaluate(async () => window.ElevhubProjectAdapter.reset());
    await ready(page);
    const transfer = await page.evaluateHandle(values => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([Uint8Array.from(values)], 'Geometri.3mf', {type: 'model/3mf'}));
      return transfer;
    }, [...bytes]);
    await page.locator('#workspace').dispatchEvent('drop', {dataTransfer: transfer});
    await page.waitForFunction(() => document.getElementById('selection-label').textContent.includes('2 figurer'));
    await ready(page);
    await verifyParts();
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a threaded rod updates its thread and length automatically and retains them after reopening', async () => {
  const {page, problems} = await open({managed: true});
  try {
    const category = page.locator('#shape-category');
    await category.selectOption('hardware');
    await page.locator('[data-preset=rod]').press('Enter');
    const editor = page.locator('#rod-editor');
    await editor.waitFor({state: 'visible'});
    await ready(page);
    const length = editor.getByLabel('Længde (mm)', {exact: true});
    assert.equal(await length.inputValue(), '30');
    await editor.getByLabel('Gevindstørrelse', {exact: true}).selectOption('6');
    await editor.getByLabel('Stigning (mm)', {exact: true}).selectOption('0.75');
    await editor.getByLabel('Gevindretning', {exact: true}).selectOption('left');
    await page.locator('#selection-label').filter({hasText: /M6 × 0,75 · venstregevind/}).waitFor({state: 'visible'});
    await ready(page);
    await length.fill('101');
    await editor.getByRole('alert').waitFor({state: 'visible'});
    assert.match(await editor.getByRole('alert').textContent(), /5 og 100 mm/);
    assert.equal(await length.getAttribute('aria-invalid'), 'true');
    assert.equal((await sizes(page))[2], 30);
    await length.fill('22.5');
    await length.press('Enter');
    await ready(page);
    assert.match(await page.locator('#selection-label').textContent(), /M6 × 0,75 · venstregevind/);
    assert.equal((await sizes(page))[2], 22.5);
    const saved = await savedProject(page);
    await reselectFirstPart(page, editor);
    assert.equal(await length.inputValue(), '22.5');
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await length.inputValue(), '30');
    await reopenFirstPart(page, saved, editor);
    assert.equal(await length.inputValue(), '22.5');
    assert.equal(await editor.getByLabel('Stigning (mm)', {exact: true}).inputValue(), '0.75');
    assert.equal(await editor.getByLabel('Gevindretning', {exact: true}).inputValue(), 'left');
    assert.equal(await page.locator('#download').isEnabled(), true);
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('the threaded rod print toggle updates geometry on the workplane and keeps its saved setting', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await addBox(page, [80, 60, 12], [0, 0, 0]);
    await page.locator('#build-on-top').click();
    await page.locator('#shape-category').selectOption('hardware');
    await page.locator('[data-preset=rod]').click(); await ready(page);
    const editor = page.locator('#rod-editor');
    const toggle = editor.getByRole('checkbox', {name: 'Liggende print', exact: true});
    assert.equal(await toggle.isChecked(), false);
    assert.deepEqual(await sizes(page), [8, 8, 30]);
    const position = await place(page);
    assert.equal(position[2], 12);

    await toggle.press('Space'); await ready(page);
    const horizontal = await sizes(page);
    assert.equal(horizontal[0], 30);
    assert.equal(horizontal[1], 8);
    assert(horizontal[2] > 6.5 && horizontal[2] < 8);
    assert.deepEqual(await place(page), position);
    assert.equal(await toggle.evaluate(element => element === document.activeElement), true);
    const saved = await savedProject(page);
    const stored = JSON.parse(saved).scene.objects.find(node => node.generator?.kind === 'rod');
    assert.equal(stored.generator.settings.lying, true);

    await page.locator('#undo').click(); await ready(page);
    assert.equal(await toggle.isChecked(), false);
    assert.deepEqual(await sizes(page), [8, 8, 30]);
    await page.locator('#redo').click(); await ready(page);
    assert.equal(await toggle.isChecked(), true);
    await page.locator('#workspace').press('Escape');
    await editor.waitFor({state: 'hidden'});
    await page.locator('#show-parts').click();
    const rod = page.locator('#object-list button').filter({hasText: /Gevindstang/});
    await rod.click();
    await editor.waitFor({state: 'visible'});
    assert.equal(await toggle.isChecked(), true);
    await toggle.uncheck(); await ready(page);
    assert.deepEqual(await sizes(page), [8, 8, 30]);
    assert.deepEqual(await place(page), position);

    await page.evaluate(project => window.ElevhubProjectAdapter.import(new Blob([project]), 'Liggende gevindstang'), saved);
    await ready(page);
    await page.locator('#show-parts').click(); await rod.click();
    assert.equal(await toggle.isChecked(), true);
    assert.deepEqual(await sizes(page), horizontal);
    assert.deepEqual(await place(page), position);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a nut updates fine left-hand threads and print clearance without an apply button', async () => {
  const {page, problems} = await open({managed: true});
  try {
    await page.locator('#shape-category').selectOption('hardware');
    const button = page.locator('[data-preset=nut]');
    await button.focus();
    await page.keyboard.press('Enter');
    const dialog = page.locator('#nut-editor');
    await dialog.waitFor({state: 'visible', timeout: 1500});
    await dialog.getByLabel('Gevindstørrelse', {exact: true}).selectOption('8');
    await dialog.getByLabel('Stigning (mm)', {exact: true}).selectOption('1');
    await dialog.getByLabel('Gevindretning', {exact: true}).selectOption('left');
    await page.locator('#selection-label').filter({hasText: /M8 × 1 · venstregevind/}).waitFor({state: 'visible'});
    await ready(page);
    const clearance = dialog.getByLabel('Spillerum pr. side (mm)', {exact: true});
    await clearance.fill('0.3');
    await clearance.press('Enter');
    await ready(page);
    const height = dialog.getByLabel('Højde (mm)', {exact: true});
    await height.fill('7.5');
    await height.press('Tab');
    await ready(page);
    assert.match(await page.locator('#object-list button').textContent(), /M8 × 1 · venstregevind/);
    assert.equal((await sizes(page))[2], 7.5);
    const saved = await savedProject(page);
    await reselectFirstPart(page, dialog);
    assert.equal(await dialog.locator('#nut-pitch').inputValue(), '1');
    assert.equal(await dialog.locator('#nut-hand').inputValue(), 'left');
    assert.equal(await field(page, '#nut-clearance'), .3);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await height.inputValue(), '6.8');
    assert.equal(await clearance.inputValue(), '0.3');
    await page.locator('#redo').click(); await ready(page);
    assert.match(await page.locator('#object-list button').textContent(), /M8 × 1 · venstregevind/);
    await reopenFirstPart(page, saved, dialog);
    assert.match(await page.locator('#object-list button').textContent(), /M8 × 1 · venstregevind/);
    assert.equal(await page.locator('#download').isEnabled(), true);
    assert.equal(await dialog.locator('#nut-pitch').inputValue(), '1');
    assert.equal(await dialog.locator('#nut-hand').inputValue(), 'left');
    assert.equal(await field(page, '#nut-clearance'), .3);
    assert.equal(await field(page, '#nut-thickness'), 7.5);
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

async function addGamePieceFromHelp(page) {
  await page.locator('#help-menu > summary').click();
  await page.getByRole('button', {name: 'Lav en spillebrik'}).click({timeout: 2500});
  const editor = page.locator('#game-piece-editor');
  await editor.waitFor({state: 'visible'}); await ready(page);
  return editor;
}

test('a Skoleværkstedet game piece can be inserted, split and edited one part at a time', async () => {
  const {page, problems} = await open();
  try {
    const editor = await addGamePieceFromHelp(page);
    for (const name of ['Sokkel', 'Krop', 'Hoved', 'Hat']) {
      const select = editor.getByRole('combobox', {name, exact: true});
      await select.waitFor({timeout: 1500});
      assert.equal(await select.evaluate(field => field.tagName), 'SELECT');
      assert.equal(await select.locator('option').count(), 7);
    }
    const body = editor.getByRole('combobox', {name: 'Krop', exact: true});
    const preview = () => editor.locator('canvas').evaluate(canvas => canvas.toDataURL());
    const before = await preview();
    await body.press('r'); await ready(page);
    assert.equal(await body.evaluate(el => el === document.activeElement), true);
    assert.equal(await body.locator('option:checked').textContent(), 'Robot');
    assert.notEqual(await preview(), before);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Robo-Sol/);
    assert.equal(await editor.getByRole('button', {name: 'Opdatér spillebrik'}).count(), 0);
    await body.selectOption({label: 'Robot'});
    assert.match(await editor.getByRole('status').textContent(), /Robo-Sol/);
    await ready(page);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Robo-Sol/);
    assert.equal(await page.locator('#download').isEnabled(), true);
    const split = page.locator('#part-split');
    assert.equal(await split.isEnabled(), true);
    await split.press('Enter');
    await ready(page);
    const parts = page.locator('#object-list button');
    assert.deepEqual(await parts.allTextContents(), ['Sokkel · Rund', 'Krop · Robot', 'Hoved · Smiley', 'Hat · Krone']);
    assert.equal(await page.locator('#selection-label').textContent(), '4 figurer valgt');
    assert.equal(await page.locator('#workspace').evaluate(el => el === document.activeElement), true);
    await viewFrom(page, 'top');
    await page.locator('#show-parts').press('Enter');
    await parts.first().press('Enter');
    const knobs = page.locator('.handle.corner .knob');
    await knobs.first().waitFor({state: 'visible'});
    const basePoint = await knobs.evaluateAll(elements => {
      const boxes = elements.map(el => el.getBoundingClientRect());
      const xs = boxes.map(r => r.left + r.width / 2), ys = boxes.map(r => r.top + r.height / 2);
      // Halfway from the rim to the centre, clear of the skewer hole.
      return [Math.min(...xs) * .75 + Math.max(...xs) * .25, (Math.min(...ys) + Math.max(...ys)) / 2];
    });
    await page.locator('#workspace').press('Escape');
    await page.mouse.click(...basePoint);
    assert.equal(await page.locator('#selection-label').textContent(), 'Sokkel · Rund');
    await page.locator('#delete').click();
    await ready(page);
    assert.equal(await parts.count(), 3);
    await page.locator('#undo').click();
    await ready(page);
    assert.equal(await parts.count(), 4);
    await page.locator('#undo').click();
    await ready(page);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Robo-Sol/);
    await page.locator('#undo').click();
    await ready(page);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Kegle-Sol/);
    await page.locator('#undo').click();
    await ready(page);
    assert.equal(await page.locator('#empty').isVisible(), true);
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('a selected game piece restores its applied choices after reselect, undo and project reopen', async () => {
  const {page, problems} = await open({managed: true});
  try {
    const empty = await savedProject(page);
    await page.evaluate(project => window.ElevhubProjectAdapter.import(new Blob([project]), 'Tomt generatorprojekt'), empty);
    await ready(page);
    const editor = await addGamePieceFromHelp(page);
    const body = editor.getByRole('combobox', {name: 'Krop', exact: true});
    const chosenBody = () => body.locator('option:checked').textContent();
    await body.selectOption({label: 'Robot'});
    await ready(page);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Robo-Sol/);
    assert((await sizes(page)).every(value => Number.isFinite(value) && value > 0));
    const saved = await savedProject(page);
    await reselectFirstPart(page, editor);
    assert.equal(await chosenBody(), 'Robot');
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await chosenBody(), 'Kegle');
    await page.locator('#redo').click(); await ready(page);
    assert.equal(await chosenBody(), 'Robot');
    await reopenFirstPart(page, saved, editor);
    assert.equal(await chosenBody(), 'Robot');
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Robo-Sol/);
    await page.evaluate(() => { Math.random = () => .5; });
    const shuffle = editor.getByRole('button', {name: 'Tilfældig brik', exact: true});
    await shuffle.press('Enter'); await ready(page);
    const randomized = JSON.parse(await savedProject(page));
    assert.deepEqual(randomized.scene.objects.find(node => node.generator?.kind === 'game-piece').generator.settings, [3, 3, 3, 3]);
    assert.equal(await shuffle.evaluate(el => el === document.activeElement), true);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await chosenBody(), 'Robot', 'one undo restores the previous choices');
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('a damaged game-piece asset leaves the selected model and undo history intact', async () => {
  const {page, problems} = await open();
  try {
    await page.route('**/assets/model3d/game-piece-data.json', async route => {
      const catalog = JSON.parse(await readFile(path.join(web, 'assets/model3d/game-piece-data.json')));
      catalog.categories.body.find(part => part.label === 'Robot').mesh.p = '@';
      await route.fulfill({json: catalog});
    });
    const editor = await addGamePieceFromHelp(page);
    const body = editor.getByRole('combobox', {name: 'Krop', exact: true});
    await body.focus();
    await body.selectOption({label: 'Robot'});
    assert.match(await editor.getByRole('status').textContent(), /delen kunne ikke læses/i);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Kegle-Sol/);
    assert.equal(await body.evaluate(el => el === document.activeElement), true);
    assert.equal(await body.locator('option:checked').textContent(), 'Kegle');
    await body.selectOption({label: 'Kjole'}); await ready(page);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Prinsesse-Sol/);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await body.locator('option:checked').textContent(), 'Kegle');
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await page.locator('#empty').isVisible(), true);
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('a saved game piece from the original STL generator splits in place and reopens editable', async () => {
  // Recreate the four triangle sequences from the first released generator.
  // Pin them to the actual flattened STL so the fixture cannot drift with the new code.
  const catalog = JSON.parse(await readFile(path.join(web, 'assets/model3d/game-piece-data.json')));
  let x = 0;
  const triangles = [];
  for (const category of ['base', 'body', 'head', 'hat']) {
    const {mesh} = catalog.categories[category][0];
    const vertices = new Float32Array(Uint8Array.from(Buffer.from(mesh.p, 'base64')).buffer);
    const indices = new Uint16Array(Uint8Array.from(Buffer.from(mesh.i, 'base64')).buffer);
    const coords = [0, 1, 2].map(axis => vertices.filter((_, i) => i % 3 === axis));
    const min = coords.map(values => Math.min(...values));
    const offset = [x - min[0], -min[1], -min[2]];
    for (const i of indices) offset.forEach((n, axis) => triangles.push(Math.fround(vertices[i * 3 + axis] + n)));
    x += Math.max(...coords[0]) - min[0] + 6;
  }
  assert.equal(createHash('sha256').update(Buffer.from(Float32Array.from(triangles).buffer)).digest('hex'),
    '85cb833afc6ac741e536c299fa015f3982d00bd09f1a4c9f2a11b1b3bb5b13c9');
  const mesh = {id:'old', kind:'mesh', mode:'solid', asset:'stl-5sxs-y5wkse-escdxa', name:'Spillebrik Kegle-Sol 1',
    size:[55.4,13,11], color:'#2f6fb5', matrix:[0,1,0,0,-1,0,0,0,0,0,1,0,10,5,5.5,1]};
  const project = {format:'makervaerkstedet-3d-project', version:1, assets:[[mesh.asset,triangles]],
    scene:{format:'makervaerkstedet-3d',version:2,units:'mm',plate:{w:180,d:180},objects:[mesh],roots:[mesh.id]}};
  const page = await browser.newPage();
  try {
    await page.addInitScript(() => { window.ElevhubApp = {}; });
    await page.goto(base);
    await page.waitForFunction(() => Boolean(window.ElevhubProjectAdapter));
    await page.evaluate(doc => window.ElevhubProjectAdapter.import(new Blob([JSON.stringify(doc)]), 'Test'), project);
    const list = page.locator('#object-list button');
    await page.locator('#show-parts').press('Enter');
    await list.first().press('Enter');
    await page.locator('#part-split').press('Enter');
    await ready(page);
    assert.equal(await list.count(), 4);
    const saved = await page.evaluate(async () => JSON.parse(await (await window.ElevhubProjectAdapter.export()).text()));
    assert(saved.scene.objects.every(node => node.color === mesh.color));
    await page.evaluate(doc => window.ElevhubProjectAdapter.import(new Blob([JSON.stringify(doc)]), 'Test'), saved);
    await list.first().press('Enter');
    assert.match(await list.first().textContent(), /Sokkel/);
    const [px, py, bottom] = await place(page);
    assert(Math.abs(px - 10) < .01 && Math.abs(py + 16.2) < .01 && bottom === 0, 'rotated base stays in place');
    assert.equal(await page.locator('#download').isEnabled(), true);
  } finally { await page.close(); }
});

test('print colour uses the available filament and survives a lost reply', async () => {
  const {page} = await open();
  try {
    const submissions = [];
    let colours = [{hex: '#E84142', name: 'Rød', ready: 0}, {hex: '#3678DC', name: 'Blå', ready: 1}];
    await page.route('**/api/print/colours', route => route.fulfill({json: {colours}}));
    await page.route('**/api/jobs', async route => {
      const headers = route.request().headers();
      submissions.push({colour: headers['x-colour'], id: headers['x-request-id']});
      if (submissions.length === 1) return route.abort('failed');
      if (submissions.length === 3) {
        colours = colours.filter(colour => colour.name !== 'Rød');
        return route.fulfill({status: 409, json: {code: 'colour_unavailable', error: 'Farven er ikke længere tilgængelig.'}});
      }
      if (submissions.length >= 4) return route.fulfill({status: 202, json: {queued: true, code: 'colour_unavailable'}});
      return route.fulfill({status: 201, json: {id: 'afe01234567890123456789012345678', status: 'queued'}});
    });
    await page.locator('[data-add=box]:not([data-mode])').click();
    await ready(page);
    const openPrint = page.locator('#send-print');
    await openPrint.click();
    await page.locator('#model-name').fill('Min røde figur');
    await page.getByRole('button', {name: 'Videre', exact: true}).click();
    const dialog = page.locator('#print-dialog');
    const choose = name => dialog.getByRole('radio', {name, exact: true});
    const confirm = dialog.getByRole('button', {name: 'Ja, send til print', exact: true});
    await choose('Rød').waitFor({timeout: 2500});
    assert.equal(await choose('Vælg for mig').isChecked(), true);
    assert.deepEqual(submissions, []);
    await choose('Rød').check();
    await confirm.click();
    await dialog.getByRole('button', {name: 'Prøv igen', exact: true}).click();
    await dialog.getByText(/Farve: Rød/).waitFor();
    assert.deepEqual(submissions[0], submissions[1]);
    assert.equal(submissions[1].colour, '#E84142');

    await dialog.getByRole('button', {name: 'Tilbage til tegningen'}).click();
    await openPrint.click();
    await choose('Rød').waitFor();
    assert.equal(await choose('Rød').isChecked(), true);
    await confirm.click();
    await dialog.getByRole('alert').getByText('Farven er ikke længere tilgængelig.').waitFor();
    await choose('Blå').waitFor();
    assert.equal(submissions.length, 3);
    await choose('Blå').check();
    await confirm.click();
    await dialog.getByText(/Farve: Blå/).waitFor();
    assert.equal(submissions[3].colour, '#3678DC');
    assert.notEqual(submissions[3].id, submissions[2].id);
    await dialog.getByRole('button', {name: 'Tilbage til tegningen'}).click();
    await openPrint.click();
    assert.equal(await dialog.locator('#print-colours').isHidden(), true);
    await dialog.getByRole('button', {name: 'Tjek igen', exact: true}).click({timeout: 2500});
    await dialog.getByText(/Farve: Blå/).waitFor();
    assert.deepEqual(submissions[4], submissions[3]);
  } finally {
    await page.close();
  }
});

async function revealMeasures(page) {
  if (await page.locator('.measure[data-axis]:visible').count()) return;
  const knob = page.locator('.handle.resize:visible .knob');
  const visibleGrip = await knob.evaluateAll(elements => elements.findIndex(element => {
    const rect = element.getBoundingClientRect();
    return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2) === element;
  }));
  assert(visibleGrip >= 0, 'a size handle can be reached');
  await knob.nth(visibleGrip).click();
}

async function typeMeasure(page, letter, value) {
  await revealMeasures(page);
  await page.locator('.measure', {hasText: new RegExp(`^${letter}`)}).first().click();
  await page.locator('.measure-input').fill(String(value)); await page.keyboard.press('Enter'); await ready(page);
}

test('the whole plate is shown and nothing is selected at the start', async () => {
  const {page, problems} = await open();
  assert.equal(await page.locator('#status').textContent(), 'Pladen er tom.');
  assert.equal(await page.locator('.plate-front').isVisible(), true);
  assert.equal(await page.locator('#selection-controls').isHidden(), true);
  assert.equal(await page.locator('.measure').count(), 0, 'no measures without a selection');
  assert.deepEqual(problems, []);
  await page.close();
});

test('the hole shortcut names its target and leaves a separate locked part untouched', async () => {
  const {page} = await open();
  try {
    await page.locator('[data-add=sphere]').click(); await ready(page);
    await openPosition(page);
    await page.locator('#lock').click(); await ready(page);
    await page.getByRole('button', {name: 'Kasse', exact: true}).click(); await ready(page);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    assert.match(await page.locator('#combine-note').innerText(), /Kasse/);
    await page.locator('#cut-holes').click(); await ready(page);
    const names = await page.locator('#object-list button').allTextContents();
    assert.deepEqual(names, ['Kugle · låst', 'Samlet figur']);
    const split = page.locator('#part-split');
    assert.equal(await split.isVisible(), true);
    assert.equal(await split.isEnabled(), true);
    await split.click(); await ready(page);
    assert.deepEqual(await page.locator('#object-list button').allTextContents(), ['Kugle · låst', 'Kasse', 'Rundt hul']);
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await page.locator('#object-list button').allTextContents(), names);
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await page.locator('#object-list button').allTextContents(), ['Kugle · låst', 'Kasse', 'Rundt hul']);
  } finally { await page.close(); }
});

test('the drawing panels expose selection and clear field errors when the part changes', async () => {
  const {page} = await open();
  try {
    await page.getByRole('button', {name: 'Kasse', exact: true}).click(); await ready(page);
    const width = page.locator('[data-size="1"]');
    await width.fill('-5'); await width.press('Enter');
    assert.equal(await width.getAttribute('aria-invalid'), 'true');
    await width.fill('20'); await width.press('Enter');
    assert.equal(await width.getAttribute('aria-invalid'), null);
    await page.getByRole('button', {name: 'Cylinder', exact: true}).click(); await ready(page);
    await page.getByRole('button', {name: /^Dele/}).click();
    const row = page.locator('#object-list button').first();
    await row.press('Enter');
    assert.equal(await row.getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('#selection-label').innerText(), 'Kasse');
    await page.getByRole('button', {name: 'Fold ind'}).press('Enter');
    assert.equal(await width.isHidden(), true);
    await page.getByRole('button', {name: 'Åbn egenskaber'}).press('Enter');
    assert.equal(await width.isVisible(), true);
  } finally { await page.close(); }
});

test('name plate: typed measures, text on top, hole, combine and a valid STL', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await revealMeasures(page);
  assert.deepEqual(await page.locator('.measure').allTextContents(), ['L 20 mm', 'B 20 mm', 'H 20 mm']);
  for (const [tag, name] of [['L', 'Længde'], ['B', 'Bredde'], ['H', 'Højde']]) {
    const measure = page.getByRole('button', {name: `${tag} 20 mm. ${name}`, exact: true});
    assert.equal(await measure.isVisible(), true);
    assert.equal(await measure.getAttribute('title'), `${name}: 20 mm. Klik for at skrive et nyt mål.`);
  }
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
  for (const [letter, value] of [['Ø', 6], ['H', 6]]) await typeMeasure(page, letter, value);
  await page.locator('#hole').click(); await ready(page);
  await openPosition(page);
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
  await openPosition(page);
  await page.locator('[data-position="2"]').fill('12'); await page.locator('[data-position="2"]').press('Enter'); await ready(page);
  assert.equal(await page.locator('.measure.lift').textContent(), '↕ 12 mm');
  assert.equal(await page.getByRole('button', {name: '↕ 12 mm. Afstand til pladen', exact: true}).isVisible(), true);
  await page.locator('.measure.lift').click(); await page.locator('.measure-input').fill('5'); await page.keyboard.press('Enter'); await ready(page);
  assert.equal(await field(page, '[data-position="2"]'), 5);
  await page.locator('#drop').click(); await ready(page);
  assert.equal(await field(page, '[data-position="2"]'), 0); assert.equal(await page.locator('.measure.lift').count(), 0);
  // Escape cancels a typed measure without changing the part.
  await revealMeasures(page);
  await page.locator('.measure', {hasText: /^L/}).click(); await page.locator('.measure-input').fill('55'); await page.keyboard.press('Escape');
  assert.equal(await field(page, '[data-size="0"]'), 20);
  assert.deepEqual(problems, []);
  await page.close();
});

test('dragging a part moves it in one undo step', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  // "Vis model" puts the part's centre on the orbit target, drawn in the middle of the canvas beside the panel.
  await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  const box = await page.locator('#canvas canvas').boundingBox(), start = [box.x + box.width / 2, box.y + box.height / 2];
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

async function writeMeasure(page, selector, value) {
  const input = page.locator(selector);
  await input.fill(String(value)); await input.press('Enter'); await ready(page);
}

async function openPosition(page) {
  if (!(await page.locator('#position-details').evaluate(details => details.open))) await page.locator('#position-details summary').click();
}

async function addBox(page, dimensions, position) {
  await page.getByRole('button', {name: 'Kasse', exact: true}).click(); await ready(page);
  for (const [axis, value] of dimensions.entries()) await writeMeasure(page, `[data-size="${axis}"]`, value);
  await openPosition(page);
  for (const [axis, value] of position.entries()) await writeMeasure(page, `[data-position="${axis}"]`, value);
}

test('Byg ovenpå sets a fixed plane for stacking, dragging, and placing parts', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [42, 30, 20], [34, -26, 0]);
    await page.locator('#build-on-top').click();
    const note = page.locator('#workplane-note');
    await note.waitFor({state: 'visible'});
    assert.match(await note.textContent(), /20\s*mm/);
    assert.equal(await page.getByRole('button', {name: 'Læg på arbejdsplan', exact: true}).count(), 1);

    await page.getByRole('button', {name: 'Cylinder', exact: true}).click(); await ready(page);
    assert.deepEqual(await place(page), [34, -26, 20], 'the cylinder starts flush and centered on the support');

    await page.locator('[data-camera=home]').click(); await viewFrom(page, 'top');
    const workspace = page.locator('#workspace'), canvas = await workspace.boundingBox();
    await page.locator('[data-add=heart]').dragTo(workspace, {targetPosition: {x: Math.round(canvas.width * .27), y: Math.round(canvas.height * .58)}});
    await ready(page);
    const droppedAt = await place(page);
    assert(droppedAt[0] < 0, 'the heart is on an empty area away from the stack');
    assert.equal(droppedAt[2], 20, 'dragging to empty space uses the same fixed height');

    const parts = page.locator('#object-list button');
    assert.equal(await parts.count(), 3);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await parts.count(), 2, 'undo removes only the dragged part');
    assert.match(await note.textContent(), /20\s*mm/);
    await page.locator('#redo').click(); await ready(page);
    assert.equal(await parts.count(), 3);
    await page.locator('#show-parts').click();
    await parts.last().click();
    await openPosition(page);
    for (const height of [37, 9]) {
      await writeMeasure(page, '[data-position="2"]', height);
      await page.getByRole('button', {name: 'Læg på arbejdsplan', exact: true}).click(); await ready(page);
      assert.deepEqual(await place(page), droppedAt, 'placing changes only the height, from either direction');
    }

    await page.locator('#show-forms').click();
    const category = page.locator('#shape-category');
    await category.selectOption('hardware');
    await page.locator('[data-preset=game-piece]').dragTo(workspace, {targetPosition: {x: Math.round(canvas.width * .69), y: Math.round(canvas.height * .55)}});
    await page.locator('#game-piece-editor').waitFor({state: 'visible'});
    await ready(page);
    assert.equal((await place(page))[2], 20, 'the generated game piece also uses the workplane');
    assert((await place(page))[0] > 0, 'the game piece lands on the chosen side of the workplane');
    await page.locator('#game-piece-editor').getByRole('combobox', {name: 'Krop', exact: true}).selectOption({label: 'Robot'});
    await ready(page);
    assert.match(await page.locator('#selection-label').textContent(), /Spillebrik Robo-Sol/);
    assert.equal((await place(page))[2], 20, 'updating the selected game piece preserves its workplane height');

    await page.locator('#reset-workplane').click();
    await note.waitFor({state: 'hidden'});
    await category.selectOption('basic');
    await page.getByRole('button', {name: 'Kugle', exact: true}).click(); await ready(page);
    assert.equal((await place(page))[2], 0, 'reset returns newly placed parts to plate height');
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('W toggles the workplane and its button remains usable after deselection', async () => {
  const {page, problems} = await open();
  try {
    const workspace = page.locator('#workspace'), note = page.locator('#workplane-note');
    await workspace.press('w');
    assert.equal(await note.isHidden(), true);
    await addBox(page, [28, 24, 17], [0, 0, 0]);
    await page.locator('[data-size="0"]').press('w');
    assert.equal(await note.isHidden(), true, 'typing into a field does not run the shortcut');
    await workspace.press('Shift+w');
    await workspace.dispatchEvent('keydown', {key: 'w', ctrlKey: true});
    assert.equal(await note.isHidden(), true, 'modified shortcuts do not change the workplane');
    await workspace.press('w');
    await note.waitFor({state: 'visible', timeout: 1500});
    assert.match(await note.textContent(), /17\s*mm/);
    await workspace.dispatchEvent('keydown', {key: 'w', repeat: true});
    assert.equal(await note.isVisible(), true, 'holding W does not toggle repeatedly');
    await workspace.press('w');
    assert.equal(await note.isHidden(), true);

    await workspace.press('w');
    await workspace.press('Escape');
    const reset = note.getByRole('button', {name: 'Slå arbejdsplan fra', exact: true});
    await reset.click();
    assert.equal(await note.isHidden(), true);
    assert.equal(await workspace.evaluate(el => el === document.activeElement), true);
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('opening a saved 3D project resets the workplane without shifting its parts', async () => {
  const {page} = await open({managed: true});
  try {
    await addBox(page, [40, 30, 20], [34, -26, 0]);
    await page.locator('#build-on-top').click();
    await page.getByRole('button', {name: 'Cylinder', exact: true}).click(); await ready(page);
    assert.equal((await place(page))[2], 20);
    const saved = await page.evaluate(async () => JSON.parse(await (await window.ElevhubProjectAdapter.export()).text()));
    await page.evaluate(project => window.ElevhubProjectAdapter.import(new Blob([JSON.stringify(project)]), 'Gemt stabel'), saved);
    await ready(page);
    await page.locator('#workplane-note').waitFor({state: 'hidden'});
    await page.locator('#show-parts').click();
    await page.getByRole('button', {name: 'Cylinder', exact: true}).click();
    await openPosition(page);
    assert.deepEqual(await place(page), [34, -26, 20], 'the original stack survives reopening');
    assert.equal(await page.getByRole('button', {name: 'Læg på pladen', exact: true}).count(), 1);
    await page.locator('#show-forms').click();
    await page.getByRole('button', {name: 'Kugle', exact: true}).click(); await ready(page);
    assert.equal((await place(page))[2], 0, 'the old workplane does not carry into the opened project');
  } finally {
    await page.close();
  }
});

test('all eleven text fonts update the real 3D model and export different lettering', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [150, 38, 4], [0, 0, 0]);
    await page.getByRole('button', {name: 'Tekst', exact: true}).click(); await ready(page);
    const font = page.getByRole('combobox', {name: 'Skrifttype', exact: true}), text = page.locator('#text-value');
    assert.equal(await font.isVisible(), true);
    assert.equal(await font.inputValue(), 'mkv-sans');
    const styles = ['mkv-sans', 'archivo-black', 'titan-one', 'anton', 'alfa-slab-one', 'bungee', 'righteous', 'bree-serif', 'kalam-bold', 'space-mono-bold', 'rubik-mono-one'];
    assert.deepEqual(await font.locator('option').evaluateAll(options => options.map(option => option.value)), styles);
    const name = 'Værksted 3D';
    await text.fill(name); await text.press('Enter'); await ready(page);
    const [, height, thickness] = await sizes(page), originalPosition = await place(page);
    await font.press('ArrowDown'); await ready(page);
    assert.equal(await font.inputValue(), 'archivo-black', 'the native selector also works from the keyboard');
    const widths = [], files = new Map();
    for (const style of styles) {
      await font.selectOption(style); await ready(page);
      assert.equal(await font.inputValue(), style);
      assert.equal(await text.inputValue(), name);
      const [width, newHeight, newThickness] = await sizes(page);
      widths.push(width);
      assert.equal(newHeight, height);
      assert.equal(newThickness, thickness);
      assert.deepEqual(await place(page), originalPosition);
      if (style === 'anton' || style === 'rubik-mono-one') {
        const download = page.waitForEvent('download');
        await page.locator('#download').click(); await nameIfAsked(page);
        if (await page.locator('#print-check').isVisible()) await page.getByRole('button', {name: 'Hent STL alligevel'}).click();
        const stl = await readFile(await (await download).path());
        assert.equal(stl.length, 84 + 50 * stl.readUInt32LE(80));
        files.set(style, createHash('sha256').update(stl).digest('hex'));
      }
    }
    assert.equal(new Set(widths).size, styles.length, 'each font creates a different width from the same letters and height');
    assert.notEqual(files.get('anton'), files.get('rubik-mono-one'));
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a font change preserves pending typing, has its own undo and reopens per text part', async () => {
  const {page, problems} = await open({managed: true});
  try {
    const oldProject = {format: 'makervaerkstedet-3d-project', version: 1, assets: [], scene: {
      format: 'makervaerkstedet-3d', version: 2, units: 'mm', plate: {w: 180, d: 180}, roots: ['old-text'], objects: [
        {id: 'old-text', kind: 'text', mode: 'solid', position: [0, 0, 1], rotation: [0, 0, 0],
          params: {text: 'Æble', font: 'mkv-sans', height: 12, depth: 2}}
      ]
    }};
    const reopen = project => page.evaluate(saved => window.ElevhubProjectAdapter.import(new Blob([JSON.stringify(saved)]), 'Prøve'), project);
    await reopen(oldProject); await ready(page);
    const font = page.getByRole('combobox', {name: 'Skrifttype', exact: true}), text = page.locator('#text-value');
    const part = name => page.getByRole('button', {name, exact: true});
    await page.locator('#show-parts').click();
    await part('Æble').click();
    assert.equal(await font.inputValue(), 'mkv-sans');

    await text.fill('Æble Øst');
    await font.selectOption('anton'); await ready(page);
    await part('Æble Øst').waitFor();
    assert.equal(await font.inputValue(), 'anton');
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await font.inputValue(), 'mkv-sans');
    assert.equal(await text.inputValue(), 'Æble Øst');
    await page.locator('#redo').click(); await ready(page);
    assert.equal(await font.inputValue(), 'anton');

    await page.locator('#show-forms').click();
    await part('Tekst').click(); await ready(page);
    assert.equal(await font.inputValue(), 'mkv-sans', 'a new text starts in the default font');
    await text.fill('Åse'); await text.press('Enter'); await ready(page);
    await font.selectOption('titan-one'); await ready(page);
    await text.fill('Åse Ørn'); await text.press('Enter'); await ready(page);
    assert.equal(await font.inputValue(), 'titan-one', 'typing new letters keeps the selected font');
    await page.locator('#show-parts').click();
    await part('Æble Øst').click(); assert.equal(await font.inputValue(), 'anton');
    await part('Åse Ørn').click(); assert.equal(await font.inputValue(), 'titan-one');
    const saved = await page.evaluate(async () => JSON.parse(await (await window.ElevhubProjectAdapter.export()).text()));
    assert.deepEqual(saved.scene.objects.map(node => [node.params.text, node.params.font]), [['Æble Øst', 'anton'], ['Åse Ørn', 'titan-one']]);
    await reopen(saved);
    await part('Æble Øst').click(); assert.equal(await font.inputValue(), 'anton');
    await part('Åse Ørn').click(); assert.equal(await font.inputValue(), 'titan-one');
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a round hole can be positioned from the foot edges and cuts an 8 mm through-hole in STL', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [60, 30, 6], [0, 0, 0]);
    await addBox(page, [60, 8, 20], [0, 11, 5.5]);
    await page.locator('#show-parts').click();
    const parts = page.locator('#object-list button');
    await parts.first().click();
    const supportId = await parts.last().getAttribute('data-object');
    await page.locator('#show-forms').click();
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    await writeMeasure(page, '[data-size="0"]', 8);
    await writeMeasure(page, '[data-size="2"]', 8);

    assert.equal(await page.locator('#hole-offsets').isVisible(), true);
    await writeMeasure(page, '[data-hole-offset="0"]', 10);
    await writeMeasure(page, '[data-hole-offset="1"]', 15);
    assert.deepEqual(await place(page), [-20, 0, 0], 'centre measured from the left and front edges');
    await writeMeasure(page, '[data-hole-offset="0"]', 14);
    assert.deepEqual(await place(page), [-16, 0, 0]);
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await place(page), [-20, 0, 0], 'the position edit is one undo step');
    await page.locator('#redo').click(); await ready(page);
    assert.deepEqual(await place(page), [-16, 0, 0]);

    const guides = page.locator('.hole-offset-guide text:visible');
    assert.deepEqual(await guides.allTextContents(), ['14 mm', '15 mm']);
    assert.equal(await page.locator('button.measure[data-axis="L"], button.measure[data-axis="B"]').count(), 0);
    assert.equal(await page.locator('button.measure[data-axis="Ø"]').count(), 1);
    await page.locator('[data-hole-offset="0"]').focus();
    assert.deepEqual(await guides.allTextContents(), ['14 mm']);
    const leftLine = page.locator('.hole-offset-guide [data-axis="0"] path').first();
    const before = await leftLine.getAttribute('d');
    await viewFrom(page, 'top');
    await page.waitForFunction(previous => document.querySelector('.hole-offset-guide [data-axis="0"] path').getAttribute('d') !== previous, before);
    assert.deepEqual(await guides.allTextContents(), ['14 mm', '15 mm']);
    await page.locator('#position-details summary').click();
    await page.locator('.hole-offset-guide').waitFor({state: 'hidden'});
    assert.equal(await guides.count(), 0);
    assert.equal(await page.locator('button.measure[data-axis="L"], button.measure[data-axis="B"]').count(), 0);
    assert.equal(await page.locator('button.measure[data-axis="Ø"]').count(), 1);

    await page.locator('#cut-holes').click(); await ready(page);
    assert.deepEqual(await parts.allTextContents(), ['Kasse', 'Samlet figur']);
    assert.equal(await parts.first().getAttribute('data-object'), supportId);
    const download = page.waitForEvent('download');
    await page.locator('#download').click(); await nameIfAsked(page, 'fod-med-bagstoette');
    const stl = await readFile(await (await download).path());
    const faces = stl.readUInt32LE(80), points = [];
    let wallFaces = 0;
    for (let i = 0; i < faces; i++) {
      const corners = [0, 1, 2].map(c => [0, 1, 2].map(a => stl.readFloatLE(96 + 50 * i + 12 * c + 4 * a)));
      points.push(...corners);
      const rings = corners.every(([x, y]) => Math.abs(Math.hypot(x + 16, y) - 4) < .01);
      if (rings && corners.some(([, , z]) => z === 0) && corners.some(([, , z]) => z === 6)) wallFaces++;
    }
    assert(wallFaces >= 24, 'the 8 mm circular wall at x=-16, y=0 spans the full 6 mm foot');
    assert.equal(Math.max(...points.map(([, , z]) => z)), 25.5, 'STL includes the back support');

    await page.locator('#part-split').click(); await ready(page);
    await page.locator('#show-parts').click();
    await parts.last().click();
    await openPosition(page);
    assert.deepEqual(await sizes(page), [8, 8, 8]);
    assert.deepEqual(await place(page), [-16, 0, 0]);
    assert.equal(await field(page, '[data-hole-offset="0"]'), 14);
    assert.equal(await field(page, '[data-hole-offset="1"]'), 15);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a loose round hole can move to either end of one straight badge plate', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [70, 25, 4], [0, 0, 0]);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    assert.deepEqual(await place(page), [0, 0, 0], 'insertion from the box still starts in the middle');
    const left = page.getByRole('button', {name: 'Læg til venstre', exact: true});
    const right = page.getByRole('button', {name: 'Læg til højre', exact: true});
    await left.press('Enter'); await ready(page);
    assert.deepEqual(await place(page), [-30, 0, 0]);
    assert.equal(await field(page, '[data-hole-offset="0"]'), 5);
    assert.equal(await left.getAttribute('aria-pressed'), 'true');
    await left.press('Enter'); await ready(page);
    assert.equal(await left.evaluate(button => button === document.activeElement), true);
    await page.keyboard.press('Tab');
    assert.equal(await right.evaluate(button => button === document.activeElement), true);
    await page.keyboard.press('Enter'); await ready(page);
    assert.deepEqual(await place(page), [30, 0, 0]);
    assert.equal(await right.getAttribute('aria-pressed'), 'true');
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await place(page), [-30, 0, 0]);
    await writeMeasure(page, '[data-hole-offset="0"]', 9);
    assert.deepEqual(await place(page), [-26, 0, 0], 'a later precise placement stays where the pupil puts it');
    assert.equal(await left.getAttribute('aria-pressed'), 'false');
    assert.equal(await right.getAttribute('aria-pressed'), 'false');
    assert.equal(await page.locator('#download').isDisabled(), true);
    assert.equal(await page.locator('#cut-holes').isEnabled(), true);
    await writeMeasure(page, '[data-size="2"]', 3);
    assert.equal(await left.isHidden(), true, 'a short cutter cannot be placed as a through-hole');
    await page.locator('#undo').click(); await ready(page);
    await page.locator('#lock').click(); await ready(page);
    assert.equal(await right.isHidden(), true, 'a locked cutter cannot move');
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('the Kopi button puts a distinct second round hole in a rounded plate', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [70, 26, 4], [0, 0, 0]);
    await writeMeasure(page, '#box-round', 3);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    await writeMeasure(page, '#diameter', 8);
    await page.locator('#duplicate').click(); await ready(page);
    const [x, y, bottom] = await place(page);
    assert(Math.hypot(x, y) >= 8.5, `two diameter 8 holes must have room between their edges: ${x}, ${y}`);
    assert.equal(bottom, 0);
    assert.match(await page.locator('#cut-explanation').textContent(), /Skær i Kasse/);
    assert.equal(await page.getByRole('button', {name: 'Læg til venstre', exact: true}).isVisible(), true);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('pasting a copied hole remembers its plate when a different part is selected', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [70, 26, 4], [0, 0, 0]);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    await writeMeasure(page, '#diameter', 8);
    await page.locator('#workspace').press('Control+c');
    await page.locator('#show-parts').click();
    await page.locator('#object-list').getByRole('button', {name: 'Kasse', exact: true}).click();
    await page.locator('#workspace').press('Control+v'); await ready(page);
    const [x, y, bottom] = await place(page);
    assert(Math.hypot(x, y) >= 8.5, `pasting must clear the original diameter 8 hole: ${x}, ${y}`);
    assert.equal(bottom, 0);
    assert.match(await page.locator('#cut-explanation').textContent(), /Skær i Kasse/);
    assert.match(await page.locator('#status').textContent(), /samme plade/);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await page.locator('#object-list button').count(), 2);
    await page.locator('#object-list').getByRole('button', {name: 'Rundt hul', exact: true}).click();
    await writeMeasure(page, '[data-position="0"]', -20);
    await page.locator('#object-list').getByRole('button', {name: 'Kasse', exact: true}).click();
    await page.locator('#workspace').press('Control+v'); await ready(page);
    assert.deepEqual(await place(page), [10, 0, 0], 'the clipboard keeps the geometry and position copied before the original was moved');
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a quarter-turned capsule badge puts a hole inside the rounded end when the name is selected', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [26, 70, 4], [0, 0, 0]);
    await writeMeasure(page, '#box-round', 13);
    await page.locator('#rotation-controls summary').click();
    await writeMeasure(page, '[data-rotation="2"]', 90);
    await page.getByRole('button', {name: 'Tekst', exact: true}).click(); await ready(page);
    const word = page.locator('#text-value');
    await word.fill('Ida'); await word.press('Enter');
    await page.locator('#selection-label', {hasText: 'Ida'}).waitFor(); await ready(page);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    assert.deepEqual(await sizes(page), [7, 7, 5]);
    const [x, y, bottom] = await place(page);
    assert(x > -29.5 && x < -23, `the hole stays in the curved left end with an edge margin: ${x}`);
    assert.deepEqual([y, bottom], [0, 0]);
    assert.match(await page.locator('#cut-explanation').textContent(), /Skær i Kasse/);
    assert.equal(await page.getByRole('button', {name: 'Læg til venstre', exact: true}).getAttribute('aria-pressed'), 'true');
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

async function addHoleRow(page, width) {
  await addBox(page, [80, width, 6], [0, 0, 0]);
  await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
  await writeMeasure(page, '#diameter', 8);
  await writeMeasure(page, '[data-position="0"]', -27);
  await page.locator('#more-menu > summary').click();
  await page.locator('#copy-row-open').click();
  await page.locator('#copy-row-dialog [name=spacing]').fill('18');
  await page.locator('#copy-row-dialog button[type=submit]').click(); await ready(page);
}

test('Kopi and paste move a whole selected hole row together on its original plate', async () => {
  const {page, problems} = await open();
  try {
    await addHoleRow(page, 30);
    assert.equal(await page.locator('#selection-label').textContent(), '4 figurer valgt');
    await page.locator('#duplicate').click(); await ready(page);
    assert.deepEqual(await place(page), [0, 10, 0], 'the row moves parallel to its original footprint');
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await page.locator('#object-list button').count(), 5);
    assert.equal(await page.locator('#selection-label').textContent(), '4 figurer valgt');
    await page.locator('#workspace').press('Control+c');
    await page.locator('#show-parts').click();
    await page.locator('#object-list').getByRole('button', {name: 'Kasse', exact: true}).click();
    await page.locator('#workspace').press('Control+v'); await ready(page);
    const selected = page.locator('#object-list button[aria-pressed=true]');
    assert.equal(await selected.count(), 4);
    const ids = await selected.evaluateAll(parts => parts.map(part => part.dataset.object));
    const positions = [];
    for (const id of ids) {
      await page.locator(`#object-list [data-object="${id}"]`).click();
      positions.push(await place(page));
    }
    assert.deepEqual(positions, [[-27, 10, 0], [-9, 10, 0], [9, 10, 0], [27, 10, 0]]);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await page.locator('#object-list button').count(), 5);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a crowded plate leaves a selected hole row intact when Kopi or paste cannot fit', async () => {
  const {page, problems} = await open();
  try {
    await addHoleRow(page, 17);
    await page.locator('#duplicate').click(); await ready(page);
    assert.equal(await page.locator('#object-list button').count(), 5);
    assert.match(await page.locator('#status').textContent(), /ikke plads til en kopi af hullerne på samme plade/);
    await page.locator('#workspace').press('Control+c');
    await page.locator('#workspace').press('Control+v'); await ready(page);
    assert.equal(await page.locator('#object-list button').count(), 5);
    assert.match(await page.locator('#status').textContent(), /ikke plads til en kopi af hullerne på samme plade/);
    await page.locator('#undo').click(); await ready(page);
    assert.equal(await page.locator('#object-list button').count(), 2, 'neither rejected copy added a history step');
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('inserting a round hole from a selected name keeps the name clear and the cut separate', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [70, 25, 4], [0, 0, 0]);
    await page.getByRole('button', {name: 'Tekst', exact: true}).click(); await ready(page);
    const word = page.locator('#text-value');
    await word.fill('Ida'); await word.press('Enter');
    await page.locator('#selection-label', {hasText: 'Ida'}).waitFor(); await ready(page);
    const nameSizes = await sizes(page), namePlace = await place(page);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    assert.deepEqual(await sizes(page), [6, 6, 5]);
    assert.deepEqual(await place(page), [-30, 0, 0]);
    assert.equal(await page.locator('#download').isDisabled(), true);
    assert.match(await page.locator('#cut-explanation').textContent(), /Skær i Kasse/);
    assert.equal(await page.getByRole('combobox', {name: 'Skær i', exact: true}).isHidden(), true);
    await page.locator('#show-parts').click();
    await page.locator('#object-list').getByRole('button', {name: 'Ida', exact: true}).click();
    assert.deepEqual(await sizes(page), nameSizes);
    assert.deepEqual(await place(page), namePlace);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a badge hole uses the clear end and falls back to a detached hole when both ends are occupied', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [70, 25, 4], [0, 0, 0]);
    await page.getByRole('button', {name: 'Tekst', exact: true}).click(); await ready(page);
    await addBox(page, [10, 10, 3], [-30, 0, 4]);
    await page.locator('#lock').click(); await ready(page);
    const name = page.locator('#object-list').getByRole('button', {name: 'Navn', exact: true});
    const round = page.getByRole('button', {name: 'Rundt hul', exact: true});
    await page.locator('#show-parts').click(); await name.click(); await page.locator('#show-forms').click();
    await round.click(); await ready(page);
    assert.deepEqual(await place(page), [30, 0, 0]);
    assert.equal(await page.getByRole('button', {name: 'Læg til venstre', exact: true}).isHidden(), true);
    assert.match(await page.locator('#badge-hole-hint').textContent(), /venstre ende er optaget/);
    await page.locator('#undo').click(); await ready(page);

    await addBox(page, [10, 10, 3], [30, 0, 4]);
    await page.locator('#show-parts').click(); await name.click(); await page.locator('#show-forms').click();
    await round.click(); await ready(page);
    assert.match(await page.locator('#status').textContent(), /begge ender er optaget.*ved siden af modellen/);
    assert.equal(await page.locator('#cut-holes').isDisabled(), true);
    assert.equal(await page.getByRole('button', {name: 'Læg til højre', exact: true}).isHidden(), true);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('fitting a long name and moving its badge hole is an explicit, single undo step', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [70, 26, 4], [0, 0, 0]);
    await page.getByRole('button', {name: 'Tekst', exact: true}).click(); await ready(page);
    const word = page.locator('#text-value');
    await word.fill('Frederikke'); await word.press('Enter');
    await page.locator('#selection-label', {hasText: 'Frederikke'}).waitFor(); await ready(page);
    const originalSize = await sizes(page), originalPlace = await place(page), font = await page.locator('#text-font').inputValue();
    assert(originalSize[0] > 70);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    const detached = await place(page);
    assert.equal(await page.locator('#cut-holes').isDisabled(), true);
    const left = page.getByRole('button', {name: 'Tilpas navn og læg hul til venstre', exact: true});
    await left.press('Enter'); await ready(page);
    assert.deepEqual(await place(page), [-29.5, 0, 0]);
    assert.match(await page.locator('#status').textContent(), /Navnet er tilpasset.*venstre ende/);
    assert.equal(await page.locator('#cut-holes').isEnabled(), true);
    assert.equal(await page.locator('#download').isDisabled(), true);
    assert.equal(await page.getByRole('button', {name: 'Tilpas navn og læg hul til højre', exact: true}).isVisible(), true);
    await page.locator('#show-parts').click();
    const name = page.locator('#object-list').getByRole('button', {name: 'Frederikke', exact: true});
    await name.click();
    const fitted = await sizes(page);
    assert(fitted[0] < 65);
    assert.equal(fitted[2], originalSize[2], 'the text depth is preserved');
    assert.equal((await place(page))[2], originalPlace[2], 'the text still sits at the same depth in its base');
    assert.equal(await page.locator('#text-font').inputValue(), font);
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await place(page), detached);
    await name.click();
    assert.deepEqual(await sizes(page), originalSize);
    assert.deepEqual(await place(page), originalPlace);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a hole through two boxes can be cut directly in one named box or both', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [45, 35, 6], [0, 0, 0]);
    await addBox(page, [30, 25, 4], [0, 0, 5]);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    const parts = page.locator('#object-list button'), choice = page.getByRole('combobox', {name: 'Skær i', exact: true}), cut = page.locator('#cut-holes');
    const unchangedId = await parts.nth(1).getAttribute('data-object');
    assert.equal(await choice.isVisible(), true, await page.locator('#cut-explanation').textContent());
    assert.equal(await cut.isDisabled(), true);
    assert.equal(await page.locator('#badge-hole-panel').isHidden(), true, 'no end shortcut picks among overlapping plates');
    assert.deepEqual(await choice.locator('option').allTextContents(), ['Vælg figur', 'Kasse 1', 'Kasse 2', 'Begge: Kasse 1 og Kasse 2']);
    await choice.selectOption({label: 'Begge: Kasse 1 og Kasse 2'});
    assert.equal(await cut.textContent(), 'Skær i begge');
    const reference = page.getByRole('combobox', {name: 'Mål fra', exact: true});
    assert.equal(await reference.isVisible(), true, 'the edge reference has its own control');
    assert.equal(await reference.inputValue(), '', 'no reference is assumed when both boxes are chosen');
    await reference.selectOption({label: 'Kasse 2'});
    assert.equal(await field(page, '[data-hole-offset="0"]'), 15);
    assert.equal(await page.locator('[data-hole-offset="1"]').inputValue(), '12,5');
    assert.equal(await choice.locator('option:checked').textContent(), 'Begge: Kasse 1 og Kasse 2');
    assert.equal(await cut.textContent(), 'Skær i begge');
    await cut.click(); await ready(page);
    assert.deepEqual(await parts.allTextContents(), ['Samlet figur']);
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await parts.allTextContents(), ['Kasse 1', 'Kasse 2', 'Rundt hul']);
    assert.equal(await cut.isDisabled(), true, 'after undo, cutting needs a new choice');

    await choice.selectOption({label: 'Kasse 1'});
    assert.equal(await cut.textContent(), 'Skær kun i Kasse 1');
    assert.deepEqual(await page.locator('#object-list button[aria-pressed=true]').allTextContents(), ['Rundt hul']);
    await cut.click(); await ready(page);
    assert.deepEqual(await parts.allTextContents(), ['Kasse', 'Samlet figur']);
    assert.equal(await parts.first().getAttribute('data-object'), unchangedId);
    await page.locator('#show-parts').click();
    await parts.first().click();
    assert.deepEqual(await sizes(page), [30, 25, 4]);
    assert.deepEqual(await place(page), [0, 0, 5]);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a white handle reveals editable dimensions and shows them changing during a drag', async () => {
  const {page, problems} = await open();
  try {
    await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
    await page.locator('[data-camera=fit]').click();
    const dimensions = page.locator('.measure[data-axis]:visible');
    const knob = page.locator('.handle.corner:visible .knob').first();
    assert.equal(await dimensions.count(), 0);
    await knob.hover();
    assert.equal(await dimensions.count(), 0, 'hover keeps the workspace clear');
    await knob.click();
    assert.deepEqual(await dimensions.allTextContents(), ['L 20 mm', 'B 20 mm', 'H 20 mm']);
    const point = await handleAt(page, '.handle.corner', 0);
    await page.mouse.move(...point); await page.mouse.down();
    await page.mouse.move(point[0] - 30, point[1] + 20, {steps: 4});
    await page.waitForFunction(() => document.querySelector('.measure[data-axis="L"]')?.textContent !== 'L 20 mm');
    assert.equal(await dimensions.count(), 3);
    await page.mouse.up(); await ready(page);
    await dimensions.filter({hasText: /^L/}).click();
    const input = page.getByRole('spinbutton', {name: 'Længde i mm', exact: true}).and(page.locator('.measure-input'));
    assert.equal(await input.evaluate(field => field.closest('button') === null && document.activeElement === field), true);
    await input.fill('32'); await input.press('Enter'); await ready(page);
    assert.equal((await sizes(page))[0], 32);
    const corners = await Promise.all([0, 1, 2, 3].map(index => handleAt(page, '.handle.corner', index)));
    const top = await handleAt(page, '.handle.resize:not(.corner)', 4);
    const center = [0, 1].map(axis => (corners.reduce((sum, corner) => sum + corner[axis], 0) / 4 + top[axis]) / 2);
    await page.mouse.click(...center);
    assert.equal(await dimensions.count(), 0);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('dragging the surface beside a size handle moves the part, while the visible handle resizes it', async () => {
  const {page, problems} = await open();
  try {
    await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
    await page.locator('[data-camera=fit]').click(); await viewFrom(page, 'top');
    const point = await page.locator('.handle.corner').evaluateAll(handles => {
      const corners = handles.map(handle => {
        const line = handle.querySelector('.resize-leader');
        if (line) {
          const point = new DOMPoint(line.x2.baseVal.value, line.y2.baseVal.value).matrixTransform(line.getScreenCTM());
          return [point.x, point.y];
        }
        const knob = handle.querySelector('.knob').getBoundingClientRect();
        return [knob.x + knob.width / 2, knob.y + knob.height / 2];
      });
      const center = [0, 1].map(axis => corners.reduce((sum, corner) => sum + corner[axis], 0) / corners.length);
      const corner = corners.sort((a, b) => a[0] + a[1] - b[0] - b[1])[0];
      return corner.map((value, axis) => value + Math.sign(center[axis] - value) * 9);
    });
    assert.equal(await page.evaluate(([x, y]) => document.elementFromPoint(x, y).localName, point), 'canvas');
    const before = await sizes(page), position = await place(page);
    await drag(page, point, [40, 24]);
    assert.deepEqual(await sizes(page), before);
    assert.notDeepEqual(await place(page), position);
    const corners = await Promise.all([0, 1, 2, 3].map(index => handleAt(page, '.handle.corner', index)));
    const rightCorner = corners.sort((a, b) => b[0] - a[0])[0];
    await drag(page, rightCorner, [48, 0]);
    assert((await sizes(page))[0] > before[0]);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

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
  await openPosition(page); await page.locator('[data-position="2"]').fill('7'); await page.locator('[data-position="2"]').press('Enter'); await ready(page);
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

test('after Saml and Del op, a canvas click selects one part and a drag moves the whole selection', async () => {
  const {page, problems} = await open();
  const parts = page.locator('#object-list button');
  const selected = () => page.locator('#object-list button[aria-pressed=true]').allTextContents();
  const boxMiddle = async () => {
    const corners = await Promise.all([0, 1, 2, 3].map(i => handleAt(page, '.handle.corner', i)));
    return [0, 1].map(axis => corners.reduce((sum, point) => sum + point[axis], 0) / corners.length);
  };
  const pressOnPart = async point => {
    assert.equal(await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.matches('#canvas canvas'), point), true);
    await page.mouse.move(...point);
    await page.waitForFunction(() => document.querySelector('#canvas canvas').style.cursor === 'grab');
  };

  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#duplicate').click(); await ready(page);
  await viewFrom(page, 'top'); await page.locator('[data-camera=fit]').click();
  await page.locator('#show-parts').click();
  const points = [];
  for (const index of [0, 1]) {
    await parts.nth(index).click();
    await page.waitForFunction(() => document.querySelector('.handle.corner')?.hasAttribute('transform'));
    points.push(await boxMiddle());
  }
  // Prove that each point hits the intended canvas part when starting with an empty selection.
  for (const [index, point] of points.entries()) {
    await page.locator('#workspace').press('Escape');
    assert.deepEqual(await selected(), []);
    await pressOnPart(point); await page.mouse.click(...point);
    assert.deepEqual(await selected(), [`Kasse ${index + 1}`]);
  }

  await page.locator('#select-all').click(); await page.locator('#group').click(); await ready(page);
  await page.locator('#part-split').click(); await ready(page);
  assert.deepEqual(await selected(), ['Kasse 1', 'Kasse 2']);
  assert.equal(await page.locator('#part-split').isDisabled(), true);
  await pressOnPart(points[0]); await page.mouse.click(...points[0]);
  assert.deepEqual(await selected(), ['Kasse 1'], 'clicking one split part narrows the selection');

  await page.waitForFunction(() => document.querySelector('.handle.corner')?.hasAttribute('transform'));
  const knob = await handleAt(page, '.handle.corner');
  assert.equal(await page.evaluate(([x, y]) => Boolean(document.elementFromPoint(x, y)?.closest('.handle.corner')), knob), true);
  await page.mouse.click(...knob);
  assert.deepEqual(await selected(), ['Kasse 1'], 'a handle click keeps the current selection');
  await page.keyboard.down('Shift');
  await pressOnPart(points[1]); await page.mouse.click(...points[1]);
  await page.keyboard.up('Shift');
  assert.deepEqual(await selected(), ['Kasse 1', 'Kasse 2']);

  await pressOnPart(points[0]); await drag(page, points[0], [0, -65]); await ready(page);
  assert.deepEqual(await selected(), ['Kasse 1', 'Kasse 2'], 'dragging one selected part keeps both selected');
  const centres = [];
  for (const index of [0, 1]) { await parts.nth(index).click(); centres.push((await place(page)).slice(0, 2)); }
  assert(centres[0][1] > 0, 'the grabbed part moved');
  assert.deepEqual(centres[1], [centres[0][0] + 25, centres[0][1]], 'both parts moved by the same amount');
  assert.deepEqual(problems, []); await page.close();
});

test('right drag turns the view, a left frame chooses parts, the hand moves the view; a plain click clears the selection', async () => {
  const {page, problems} = await open();
  const canvasLabel = await page.locator('#canvas canvas').getAttribute('aria-label');
  assert.match(canvasLabel, /Træk en ramme på den tomme flade for at vælge flere/);
  assert.match(canvasLabel, /Træk med højre museknap for at dreje visningen/);
  // The copy stands 25 mm from the orbit centre, so turning the view moves it on screen.
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page); await page.locator('#duplicate').click(); await ready(page);
  const canvas = await page.locator('#canvas canvas').boundingBox(), empty = [canvas.x + 50, canvas.y + 170], before = await handleAt(page, '.handle.lift');
  await page.mouse.move(...empty); await page.mouse.down({button: 'right'}); await page.mouse.move(empty[0] + 120, empty[1] + 30, {steps: 10}); await page.mouse.up({button: 'right'}); await page.waitForTimeout(600);
  const after = await handleAt(page, '.handle.lift');
  assert(Math.hypot(after[0] - before[0], after[1] - before[1]) > 10, 'the right button turns the camera');
  assert.equal(await page.locator('#selection-controls').isHidden(), false, 'turning the view keeps the selection');
  // A left drag on the empty plate no longer turns the view: it draws a frame round both parts.
  await page.locator('[data-camera=home]').click(); await page.waitForTimeout(200);
  await page.locator('#show-parts').click();
  const lifts = [];
  for (const i of [0, 1]) {
    await page.locator('#object-list button').nth(i).click();
    await page.waitForFunction(() => document.querySelector('.handle.lift')?.hasAttribute('transform'));
    lifts.push(await handleAt(page, '.handle.lift'));
  }
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

test('a part near the right edge stays editable when selected after panning and zooming', async () => {
  const {page, problems} = await open();
  try {
    await page.locator('[data-add=box]').first().click(); await ready(page);
    await viewFrom(page, 'top');
    await page.getByRole('button', {name: 'Zoom ind'}).click();
    const workspace = page.locator('#workspace'), canvas = page.locator('#canvas canvas');
    const narrowed = await canvas.boundingBox(), initial = await place(page);
    const corners = await Promise.all([0, 1, 2, 3].map(i => handleAt(page, '.handle.corner', i)));
    const center = [0, 1].map(axis => corners.reduce((sum, point) => sum + point[axis], 0) / corners.length);
    await workspace.press('Escape');
    const expanded = await canvas.boundingBox();
    const edge = expanded.x + expanded.width - 90;
    assert(edge > narrowed.x + narrowed.width, 'the new inspector will cover this screen position');

    await page.locator('#pan-mode').click();
    await drag(page, [expanded.x + 70, expanded.y + expanded.height * .52], [edge - center[0], 0]);
    await page.waitForTimeout(500);
    await page.keyboard.press('Escape');
    await page.mouse.move(edge, center[1]);
    await page.waitForFunction(() => document.querySelector('#canvas canvas').style.cursor === 'grab');
    await page.mouse.click(edge, center[1]);
    assert.equal(await page.locator('#selection-label').innerText(), 'Kasse');
    assert.deepEqual(await place(page), initial, 'opening properties moves the view without moving the part');
    await typeMeasure(page, 'L', 25);
    assert.equal((await sizes(page))[0], 25);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
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
  await page.getByRole('button', {name: 'Oven', exact: true}).click(); await page.waitForTimeout(200);
  assert.equal(await page.locator('.handle.lift.off').count(), 1, 'seen from above, the lift arrow is hidden');
  await viewFrom(page, 'right'); await page.waitForTimeout(200);
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
  // A missed hole stays editable, with a reason shown before the operation.
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  assert.equal(await page.locator('#combine-note').isVisible(), true);
  assert.equal(await page.locator('#cut-holes').isDisabled(), true);
  assert.equal(await page.locator('#object-list button').count(), 3);
  assert.match(await page.locator('#cut-explanation').textContent(), /rører ikke/);
  // Move the hole over the plate, then cut only what it intersects.
  await page.locator('#object-list button', {hasText: 'hul'}).dispatchEvent('click');
  await typeMeasure(page, 'Ø', 6);
  await openPosition(page);
  for (const [axis, value] of [[0, -27], [1, 0]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  await page.locator('#cut-holes').click(); await ready(page);
  assert.equal(await page.locator('#object-list button').count(), 2, 'the text remains a separate part');
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
  assert.equal(await page.locator('#center').isHidden(), true, 'one selected part cannot be stacked');
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
  await openPosition(page); await page.locator('#lock').click(); await ready(page);
  assert.equal(await page.locator('.handle').count(), 0); assert.equal(await page.locator('.measure.static').count(), 3);
  await page.locator('#workspace').focus(); await page.keyboard.press('ArrowRight'); await page.keyboard.press('Delete'); await ready(page);
  assert.deepEqual((await place(page)).slice(0, 2), [0, 0]); assert.equal(await page.locator('#object-list button').count(), 1);
  assert.match(await page.locator('#object-list button').textContent(), /låst/);
  await page.locator('#lock').click(); await ready(page);
  await writeMeasure(page, '[data-position="2"]', 5);
  await page.locator('#lock').click(); await ready(page);
  assert.equal(await page.locator('.measure.lift.static').textContent(), '↕ 5 mm');
  await page.locator('#lock').click(); await ready(page);
  await page.locator('#drop').click(); await ready(page);
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await openPosition(page); await page.locator('#hide').click(); await ready(page);
  assert.match(await page.locator('#object-list button').last().textContent(), /skjult/);
  await page.locator('#object-list button').first().dispatchEvent('click'); await page.locator('#download').click(); await nameIfAsked(page);
  assert.match(await page.locator('#print-issues').textContent(), /skjult figur kommer ikke med/);
  await page.getByRole('button', {name: 'Tilbage til modellen'}).click();
  await page.locator('#object-list button').last().dispatchEvent('click');
  await openPosition(page);
  await page.locator('#hide').click(); await ready(page);
  assert.doesNotMatch(await page.locator('#object-list button').last().textContent(), /skjult/);
  assert.deepEqual(problems, []); await page.close();
});

test('everyone gets the same workshop with all named shapes, text and holes ready to choose', async () => {
  const {page, problems} = await open();
  assert.equal(await page.locator('#ideas-dialog').isVisible(), false, 'no start question');
  assert.equal(await page.locator('.levels').count(), 0);
  assert.deepEqual(await page.locator('[data-add]:visible').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label'))),
    ['Kasse', 'Cylinder', 'Kugle', 'Kegle', 'Pyramide', 'Tag', 'Kile', 'Halvkugle', 'Sekskant', 'Stjerne', 'Hjerte', 'Rør', 'Tekst', 'Rundt hul', 'Kassehul']);
  const split = page.locator('#part-split');
  assert.equal(await split.isHidden(), true);
  assert.equal(await split.isDisabled(), true, 'an empty plate cannot be split');
  assert.equal(await page.locator('#import-stl').isVisible(), false, 'fetching a file waits behind its button');
  await page.locator('#project-menu > summary').click(); assert.equal(await page.locator('#import-stl').isVisible(), true);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const tucked of ['#mirror', '#lock', '[data-rotation="0"]', '[data-position="0"]']) assert.equal(await page.locator(tucked).isVisible(), false, tucked);
  assert.equal(await split.isVisible(), true);
  assert.equal(await split.isDisabled(), true, 'a primitive cannot be split');
  assert.equal(await page.locator('.handle.rotate:visible').count(), 0, 'turning arcs wait until Drej is pressed');
  await page.locator('#rotate-mode').click(); await page.waitForTimeout(100);
  assert(await page.locator('.handle.tilt:visible').count() > 0, 'Drej shows the turning and tilting arcs');
  await page.locator('#workspace').focus(); await page.keyboard.press('r'); await page.waitForTimeout(100);
  assert.equal(await page.locator('.handle.rotate:visible').count(), 0, 'R turns the arcs off again');
  await page.locator('#more-menu > summary').click(); assert.equal(await page.locator('#mirror').isVisible(), true);
  await page.locator('#mirror').click(); await ready(page); assert.equal(await page.locator('#mirror').isVisible(), false, 'the menu closes after a choice');
  await page.reload(); await ready(page); await page.waitForTimeout(100);
  assert.equal(await page.locator('#ideas-dialog').isVisible(), false);
  assert.deepEqual(problems, []); await page.close();
});

test('a hole over the edge is called a notch; a lone text is named; a new text is ready for typing', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  await openPosition(page); await page.locator('[data-position="0"]').fill('-12'); await page.locator('[data-position="0"]').press('Enter'); await ready(page);
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
  assert.match(await page.locator('#status').textContent(), /sat på siden af Kugle/);
  assert.deepEqual(problems, []); await page.close();
});

test('hovering a round hole previews without editing and dropping it bores through the front of a box', async () => {
  const {page, problems} = await open();
  try {
    await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
    for (const [axis, value] of [['L', 32], ['B', 24], ['H', 28]]) await typeMeasure(page, axis, value);
    await page.locator('.cube-face', {hasText: 'Foran'}).dispatchEvent('click');
    await page.locator('[data-camera=fit]').click();
    await page.waitForTimeout(250);
    const canvas = await page.locator('#canvas canvas').boundingBox();
    const target = {clientX: canvas.x + canvas.width / 2, clientY: canvas.y + canvas.height / 2};
    const workspace = page.locator('#workspace');
    const hole = page.getByRole('button', {name: 'Rundt hul', exact: true});
    const parts = page.locator('#object-list button');
    const preview = page.locator('#shape-drop-preview');
    const transfer = await page.evaluateHandle(() => new DataTransfer());
    await hole.dispatchEvent('dragstart', {dataTransfer: transfer});
    await workspace.dispatchEvent('dragover', {...target, dataTransfer: transfer});
    await preview.waitFor({state: 'visible', timeout: 1500});
    assert.match(await preview.textContent(), /Hul gennem Kasse/);
    assert.deepEqual(await parts.allTextContents(), ['Kasse']);
    await workspace.dispatchEvent('dragleave', {dataTransfer: transfer});
    await preview.waitFor({state: 'hidden'});
    assert.deepEqual(await parts.allTextContents(), ['Kasse']);

    const board = await workspace.boundingBox();
    await hole.dragTo(workspace, {targetPosition: {x: target.clientX - board.x, y: target.clientY - board.y}});
    await ready(page);
    assert.equal(await preview.isHidden(), true);
    assert.equal(await parts.count(), 2);
    assert.match(await page.locator('#status').textContent(), /Hullet går gennem Kasse/);
    await page.locator('#cut-holes').click(); await ready(page);
    assert.equal(await parts.count(), 1);

    const [download] = await Promise.all([page.waitForEvent('download'), (async () => {
      await page.locator('#download').click(); await nameIfAsked(page, 'Sideboret kasse');
      if (await page.locator('#print-check').isVisible()) {
        assert.doesNotMatch(await page.locator('#print-issues').textContent(), /hak/i, 'a bore through the side is not a notch');
        await page.getByRole('button', {name: 'Hent STL alligevel'}).click();
      }
    })()]);
    const stl = await readFile(await download.path());
    const faces = stl.readUInt32LE(80);
    assert.equal(stl.length, 84 + 50 * faces);
    // Check where a ray through the centre meets the four external faces.
    // The opening passes through both Y walls while the top and bottom remain solid.
    const triangles = Array.from({length: faces}, (_, face) => [0, 1, 2].map(corner =>
      [0, 1, 2].map(axis => stl.readFloatLE(96 + 50 * face + 12 * corner + 4 * axis))));
    function coversPoint(normalAxis, point, wall) {
      const [u, v] = [0, 1, 2].filter(axis => axis !== normalAxis);
      return triangles.some(corners => {
        if (wall !== undefined && !corners.every(vertex => Math.abs(vertex[normalAxis] - wall) < .01)) return false;
        const sides = corners.map((start, i) => {
          const end = corners[(i + 1) % 3];
          return (end[u] - start[u]) * (point[1] - start[v]) - (end[v] - start[v]) * (point[0] - start[u]);
        });
        const area = sides.reduce((sum, side) => sum + side, 0);
        return Math.abs(area) > .001 && (sides.every(side => side >= -.001) || sides.every(side => side <= .001));
      });
    }
    assert.equal(coversPoint(1, [0, 14]), false, 'the full bore from front to back is open');
    assert.equal(coversPoint(1, [13, 14], -12), true, 'the front wall remains beside the bore');
    assert.equal(coversPoint(1, [13, 14], 12), true, 'the back wall remains beside the bore');
    assert.equal(coversPoint(2, [0, 0], 0), true, 'the bottom remains solid');
    assert.equal(coversPoint(2, [0, 0], 28), true, 'the top remains solid');

    await page.locator('#undo').click(); await ready(page);
    assert.equal(await parts.count(), 2, 'undoing the cut restores the separate cutter');
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await parts.allTextContents(), ['Kasse'], 'undoing the drop restores the uncut box');

    const busyDrop = await page.evaluate(position => {
      // Start a geometry calculation and try the drop before the worker can reply.
      document.getElementById('duplicate').click();
      const dataTransfer = new DataTransfer();
      const options = {...position, dataTransfer, bubbles: true, cancelable: true};
      document.querySelector('[data-add="cylinder"][data-mode="hole"]').dispatchEvent(new DragEvent('dragstart', options));
      const board = document.getElementById('workspace');
      board.dispatchEvent(new DragEvent('dragover', options));
      board.dispatchEvent(new DragEvent('drop', options));
      return {previewHidden: document.getElementById('shape-drop-preview').hidden, status: document.getElementById('status').textContent};
    }, target);
    assert.equal(busyDrop.previewHidden, true);
    assert.match(busyDrop.status, /Træk hullet igen/);
    await ready(page);
    assert.equal(await parts.count(), 2, 'only the copy was added during the calculation');
    assert.deepEqual(problems, []);
  } finally {
    await page.close();
  }
});

test('switching to named views including Nedefra keeps an off-centre model framed and Hele pladen restores the overview', async () => {
  const {page} = await open();
  try {
    await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
    for (const [axis, value] of [['L', 28], ['B', 28], ['H', 22]]) await typeMeasure(page, axis, value);
    if (!await page.locator('[data-position="0"]').isVisible()) await page.locator('#position-details summary').click();
    for (const [axis, value] of [[0, 43], [1, -37]]) {
      const position = page.locator(`[data-position="${axis}"]`);
      await position.fill(String(value)); await position.press('Enter'); await ready(page);
    }
    await page.locator('[data-camera=fit]').click();
    const canvas = await page.locator('#canvas canvas').boundingBox();
    const readCorners = () => page.locator('.handle.corner .knob').evaluateAll(knobs => {
      const xs = knobs.map(knob => {
        const rect = knob.getBoundingClientRect();
        return rect.x + rect.width / 2;
      });
      const left = Math.min(...xs), right = Math.max(...xs);
      return {width: right - left, center: (left + right) / 2};
    });
    await page.waitForTimeout(250);
    const before = await readCorners();
    assert(before.width > 100, 'Vis model must enlarge the model enough to work on it');
    for (const direction of ['Oven', 'Foran', 'Højre']) {
      await page.locator('.cube-face', {hasText: direction}).dispatchEvent('click');
      await page.waitForTimeout(250);
      const after = await readCorners();
      assert(after.width >= .7 * before.width, `${direction} reduced the fitted width from ${before.width} to ${after.width}`);
      assert(Math.abs(after.center - canvas.x - canvas.width / 2) < 45, `${direction} lost the fitted horizontal centre`);
    }
    await viewFrom(page, 'top');
    const fromAbove = await page.locator('#canvas canvas').screenshot();
    await viewFrom(page, 'bottom');
    await page.waitForTimeout(250);
    const below = await readCorners();
    assert(below.width >= .7 * before.width, 'Nedefra retains a useful working scale');
    assert(Math.abs(below.center - canvas.x - canvas.width / 2) < 45, 'Nedefra retains the model focus');
    const fromBelow = await page.locator('#canvas canvas').screenshot();
    assert(!fromAbove.equals(fromBelow), 'Nedefra must render a different view of the built shape');
    await page.locator('[data-camera=home]').click();
    await page.waitForTimeout(250);
    const overview = await readCorners();
    assert(overview.width < .7 * before.width, 'Hele pladen must still show the overview');
  } finally {
    await page.close();
  }
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
  await openPosition(page);
  for (const [axis, value] of [[0, 0], [1, -25]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  await page.locator('[data-camera=fit]').click(); await page.waitForTimeout(200);
  const arrow = await handleAt(page, '.handle.lift'), grab = [arrow[0], arrow[1] + 80];
  await page.mouse.move(...grab); await page.mouse.down(); await page.mouse.move(grab[0] + 8, grab[1], {steps: 4}); await page.mouse.up(); await ready(page);
  const [x, y, z] = await place(page); assert.equal(z, 0, 'the part must stay on the plate'); assert(y < -15, `the part must not jump onto the box behind, y ${y}`); void x;
  assert.deepEqual(problems, []); await page.close();
});

test('an idea card opens a step panel that points at the button to use', async () => {
  const {page, problems} = await open();
  await chooseExercise(page); assert.equal(await page.locator('.idea-card').count(), 4);
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

test('Hjælp opens the instructions and exercises by keyboard and takes focus back when they close', async () => {
  const {page, problems} = await open();
  const help = page.locator('#help-menu > summary');
  await help.press('Enter');
  await page.keyboard.press('Tab');
  assert.equal(await page.locator('#help-open').evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#help').isVisible(), true);
  assert.equal(await page.locator('#help h2').evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#help').isHidden(), true);
  assert.equal(await help.evaluate(el => el === document.activeElement), true);

  await page.keyboard.press('Space');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  assert.equal(await page.locator('#ideas').evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#ideas-dialog').isVisible(), true);
  assert.equal(await page.locator('.idea-card').count(), 4);
  await page.locator('#ideas-dialog form button').click();
  assert.equal(await page.locator('#guide').isHidden(), true);
  assert.equal(await help.evaluate(el => el === document.activeElement), true);
  assert.deepEqual(problems, []); await page.close();
});

test('a round hole stays round when its diameter is typed, and a new idea on a full plate asks to clear it', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  await typeMeasure(page, 'Ø', 6); assert.deepEqual(await sizes(page), [6, 6, 20]);
  await chooseExercise(page); await page.locator('.idea-card', {hasText: 'Spillebrik'}).click();
  assert.equal(await page.locator('#clear-dialog').isVisible(), true); await page.locator('#clear-yes').click(); await ready(page);
  assert.equal(await page.locator('#object-list button').count(), 0); assert.equal(await page.locator('#guide').isVisible(), true);
  await page.locator('#undo').click(); await ready(page); assert.equal(await page.locator('#object-list button').count(), 1);
  assert.deepEqual(problems, []); await page.close();
});

test('the parts list calls a hole oval after its two dimensions become different', async () => {
  const {page, problems} = await open();
  try {
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    await page.getByRole('button', {name: 'Gør oval', exact: true}).click();
    await writeMeasure(page, '[data-size="1"]', 12);
    assert.deepEqual(await sizes(page), [20, 12, 20]);
    await page.locator('#show-parts').click();
    assert.deepEqual(await page.locator('#object-list button').allTextContents(), ['Ovalt hul']);
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await page.locator('#object-list button').allTextContents(), ['Rundt hul']);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('a copy that was moved and turned makes the next copy repeat it; Spejlvend mirrors in place', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#duplicate').click(); await ready(page);
  // Move the copy to x = 30 and turn it 15°: the next copy goes another 30 mm and 15° further.
  await openPosition(page); await page.locator('[data-position="0"]').fill('30'); await page.locator('[data-position="0"]').press('Enter'); await ready(page);
  await page.locator('#rotation-controls summary').click(); await page.locator('[data-rotation="2"]').fill('15'); await page.locator('[data-rotation="2"]').press('Enter'); await ready(page);
  await page.locator('#duplicate').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /gentager/);
  assert.equal(await field(page, '[data-rotation="2"]'), 30); const [x] = await place(page); assert(Math.abs(x - 60) < 2, `the third box must stand near x = 60, got ${x}`);
  // Spejlvend keeps size and place; a mirrored text still makes a closed file.
  await page.locator('#more-menu > summary').click(); await page.locator('#mirror').click(); await ready(page);
  assert.deepEqual(await sizes(page), [20, 20, 20]); assert(Math.abs((await place(page))[0] - x) < .01);
  await page.locator('[data-add=text]').click(); await ready(page); await page.locator('#more-menu > summary').click(); await page.locator('#mirror').click(); await ready(page);
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
  await importFile(page, 'stl', {name: 'terning.stl', mimeType: 'model/stl', buffer});
  await page.locator('#object-list button', {hasText: 'terning'}).waitFor({state: 'attached'});
  await ready(page);
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
  assert.equal(await page.locator('.measure.gap').textContent(), '↔ 5 mm');
  assert.equal(await page.getByRole('button', {name: '↔ 5 mm. Afstand mellem figurerne', exact: true}).isVisible(), true);
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
  await page.getByRole('button', {name: 'Højre', exact: true}).click(); await page.waitForTimeout(150);
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

test('the main tools have visible names; a hole chosen with a plate selected lands in the plate; the menu closes outside', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]').first().click(); await ready(page);
  await page.locator('[data-add=box]').first().click(); await ready(page);
  await page.locator('#select-all').click();
  for (const [id, name] of [['select-all', 'Vælg alle'], ['duplicate', 'Kopi'], ['delete', 'Slet'], ['center', 'Sæt midt på'], ['group', 'Saml'], ['part-split', 'Del op']]) {
    const caption = page.locator(`#${id} .tool-name`);
    assert.equal(await caption.isVisible(), true);
    assert.equal((await caption.innerText()).trim(), name);
  }
  await page.locator('#undo').click(); await ready(page);
  await page.locator('#show-parts').click();
  await page.locator('#object-list button').first().click();
  await page.locator('#group').hover(); await page.waitForTimeout(450);
  assert.match(await page.locator('#tip').textContent(), /Saml til én figur/); assert.equal(await page.locator('#tip').isVisible(), true);
  for (const [letter, value] of [['L', 70], ['B', 26], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('#show-forms').click();
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  assert.deepEqual(await sizes(page), [7, 7, 5], 'a quarter of the plate width, through the plate');
  assert.match(await page.locator('#status').textContent(), /midt i figuren/);
  await page.locator('#more-menu > summary').click(); assert.equal(await page.locator('#mirror').isVisible(), true);
  await page.locator('#status').click(); assert.equal(await page.locator('#mirror').isVisible(), false);
  assert.deepEqual(problems, []); await page.close();
});

test('Ret ind lines the chosen parts up with the largest one, and waits until two parts are chosen', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 60], ['B', 40], ['H', 10]]) await typeMeasure(page, letter, value);
  await openAlignment(page); assert.equal(await page.locator('.align-items').isVisible(), false, 'one part: nothing to line up');
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  await page.locator('[data-add=cylinder]:not([data-mode]):not([data-sides])').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await openAlignment(page); await page.locator('[data-align="-1"][data-axis="0"]').click(); await ready(page);
  await page.locator('[data-align="1"][data-axis="1"]').click(); await ready(page);
  await page.locator('#show-parts').click();
  const parts = page.locator('#object-list button');
  await parts.last().click();
  const [x, y] = (await place(page)).slice(0, 2);
  await parts.first().click(); const [px, py] = (await place(page)).slice(0, 2);
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

test('a grey button says why in its tip; a hole deliberately moved over the name can cut both parts', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  await page.locator('#more-menu > summary').click();
  await page.locator('#align-menu > summary').hover(); await page.waitForTimeout(450);
  assert.match(await page.locator('#tip').textContent(), /Vælg to eller flere figurer først/);
  await page.locator('#status').click();
  for (const [letter, value] of [['L', 70], ['B', 26], ['H', 4]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=text]').click(); await ready(page);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page);
  await writeMeasure(page, '[data-size="2"]', 6);
  await openPosition(page);
  await writeMeasure(page, '[data-position="0"]', 0);
  const [, , bottom] = await place(page), [, , height] = await sizes(page);
  assert.equal(bottom, 0, 'the hole starts at the bottom of the plate'); assert(height > 5, `the hole reaches above the name, got ${height}`);
  assert.equal(await page.locator('#cut-holes').isDisabled(), true, 'both targets must be chosen explicitly');
  await page.locator('#workspace').press('Control+a');
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
  await page.locator('[data-add=star]').click(); await ready(page);
  assert.deepEqual(await sizes(page), [20, 20, 5]); assert.match(await page.locator('#object-list').textContent(), /Stjerne/);
  assert.deepEqual(problems, []); await page.close();
});

test('the parts list can be opened and a hidden part comes back with Vis skjulte', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.equal(await page.locator('.parts').isHidden(), true);
  await page.locator('#show-parts').click();
  assert.equal(await page.locator('#object-list button').first().isVisible(), true);
  await openPosition(page); await page.locator('#hide').click(); await ready(page);
  await page.locator('#more-menu > summary').click(); await page.locator('#show-hidden').click(); await ready(page);
  assert.equal(await page.locator('#selection-controls').isVisible(), true, 'the part is back and chosen');
  assert.doesNotMatch(await page.locator('#object-list').textContent(), /skjult/);
  assert.deepEqual(problems, []); await page.close();
});

test('clicking the chosen part again picks the part behind it, such as a hole inside a plate', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  for (const [letter, value] of [['L', 40], ['B', 40], ['H', 10]]) await typeMeasure(page, letter, value);
  await page.locator('[data-add=cylinder][data-mode=hole]').click(); await ready(page); await typeMeasure(page, 'H', 6);
  await viewFrom(page, 'top'); await page.waitForTimeout(200);
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
  await page.locator('[data-add=tube]').click(); await ready(page);
  assert.equal(await page.locator('#wall').inputValue(), '3.6', 'the standard wall is shown');
  await page.locator('#wall').fill('1.5'); await page.locator('#wall').press('Enter'); await ready(page);
  assert.equal(await page.locator('#wall').inputValue(), '1.5'); assert.deepEqual(await sizes(page), [20, 20, 20]);
  assert.deepEqual(problems, []); await page.close();
});

test('a heart dragged in lands flat; choosing several parts names the tools for them', async () => {
  const {page, problems} = await open();
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

test('the toolbar offers stacking, lining up and joining when several parts are chosen', async () => {
  const {page, problems} = await open();
  const center = page.locator('#center'), group = page.locator('#group');
  const more = page.locator('#more-menu > summary'), align = page.locator('#align-menu > summary'), commands = page.locator('.align-items');
  await page.locator('[data-add=sphere]').click(); await ready(page);
  assert.equal(await center.isHidden(), true);
  assert.equal(await group.isDisabled(), true);
  await more.press('Enter');
  await align.press('Enter');
  assert.equal(await commands.isHidden(), true);
  await page.locator('[data-add=sphere]').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  assert.equal(await group.isEnabled(), true);
  await center.click(); await ready(page);
  assert.equal(await page.locator('#object-list .loose-part').count(), 0, 'stacked, they touch');
  await more.press('Enter');
  await page.keyboard.press('Tab');
  assert.equal(await align.evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Enter');
  assert.equal(await commands.isVisible(), true);
  await page.keyboard.press('Tab');
  assert.equal(await commands.getByRole('button', {name: 'Ret ind til venstre'}).evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Escape');
  assert.equal(await commands.isHidden(), true);
  assert.equal(await align.evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Escape');
  assert.equal(await align.isHidden(), true);
  assert.equal(await more.evaluate(el => el === document.activeElement), true);
  assert.equal(await page.locator('#selection-label').textContent(), '2 figurer valgt', 'closing menus keeps the selection');
  assert.deepEqual(problems, []); await page.close();
});

test('an SVG drawing becomes a flat part 40 mm across; lines become bands; text alone is explained', async () => {
  const {page, problems} = await open();
  const ring = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><path fill="#000" d="M0 0H100V50H0Z M20 10V40H80V10Z"/></svg>';
  await importFile(page, 'svg', {name: 'ramme.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(ring)}); await page.locator('#status', {hasText: 'ramme er hentet ind'}).waitFor(); await ready(page);
  assert.deepEqual(await sizes(page), [40, 20, 3]); assert.match(await page.locator('#status').textContent(), /ramme er hentet ind som en flad figur/);
  // A drawing of lines only: each line becomes a band as wide as the line.
  const lines = '<svg xmlns="http://www.w3.org/2000/svg"><path fill="none" stroke="#000" stroke-width="4" d="M0 0L60 0L60 30"/></svg>';
  await importFile(page, 'svg', {name: 'streg.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(lines)}); await page.locator('#status', {hasText: 'streg er hentet ind'}).waitFor(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /streg er hentet ind/);
  const empty = '<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="10">Hej</text></svg>';
  await importFile(page, 'svg', {name: 'tekst.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(empty)}); await page.waitForTimeout(200);
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

test('on a 1280 × 720 laptop the work area stays clear when empty, selected and deselected', async () => {
  const {page, problems} = await open();
  await page.setViewportSize({width: 1280, height: 720}); await page.waitForTimeout(100);
  const editor = page.getByRole('group', {name: 'Rediger modellen'});
  const selectAll = page.locator('#select-all'), undo = page.locator('#undo'), redo = page.locator('#redo');
  const workspace = page.locator('#workspace'), properties = page.locator('#selection-controls'), empty = page.locator('#empty');
  assert.equal(await empty.isVisible(), true);
  assert.equal(await editor.isHidden(), true);
  assert.equal(await properties.isHidden(), true);
  assert.equal(await page.getByRole('button', {name: 'Hele pladen'}).isVisible(), true);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.equal(await empty.isHidden(), true);
  assert.equal(await properties.isVisible(), true);
  assert.equal(await editor.isVisible(), true);
  assert.deepEqual(await page.evaluate(() => [document.documentElement.scrollHeight <= innerHeight, document.documentElement.scrollWidth <= innerWidth]), [true, true]);
  assert.equal(await page.locator('.library').evaluate(library => library.scrollHeight <= library.clientHeight), true, 'every shape fits without scrolling');
  const tops = await page.locator('#color-row .swatch').evaluateAll(all => new Set(all.map(swatch => { const r = swatch.getBoundingClientRect(); return Math.round(r.top + r.height / 2); })).size);
  assert.equal(tops, 1);
  const selected = await workspace.boundingBox();
  await workspace.press('Escape');
  assert.equal(await empty.isHidden(), true, 'the model is still on the plate');
  assert.equal(await properties.isHidden(), true);
  assert.equal(await page.locator('#panel-open').isHidden(), true);
  assert.equal(await page.locator('#duplicate').isHidden(), true);
  assert.equal(await page.locator('#more-menu > summary').isHidden(), true);
  assert.equal(await selectAll.isVisible(), true);
  assert((await workspace.boundingBox()).width >= selected.width, 'the empty inspector releases space for the model');

  await selectAll.press('Enter');
  assert.equal(await properties.isVisible(), true);
  await undo.click(); await ready(page);
  assert.equal(await empty.isVisible(), true);
  assert.equal(await redo.isEnabled(), true);
  await redo.click(); await ready(page);
  assert.equal(await properties.isVisible(), true);
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
  const square = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0H10V10H0Z"/></svg>';
  await importFile(page, 'svg', {name: 'logo.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(square)});
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
    await openPosition(page);
    for (const [axis, value] of [[0, 0], [1, 0], [2, z]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  }
  await page.locator('[data-camera=home]').click();
  await viewFrom(page, 'top'); await page.waitForTimeout(200);
  const box = await page.locator('#canvas canvas').boundingBox(), handles = await page.evaluate(() => [...document.querySelectorAll('.handle.corner .knob')].map(k => { const r = k.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }));
  const middle = [0, 1].map(axis => handles.reduce((sum, point) => sum + point[axis], 0) / handles.length);
  await page.locator('#workspace').focus(); await page.keyboard.press('Escape');
  const bottom = async () => (await place(page))[2], seen = [];
  for (let i = 0; i < 4; i++) {
    const target = await page.evaluate(([x, y]) => {
      const element = document.elementFromPoint(x, y);
      return element?.closest('.measure, .handle')?.getAttribute('class') || element?.localName;
    }, middle);
    assert.equal(target, 'canvas', `stack click ${i + 1} must reach the model`);
    await page.mouse.click(...middle); await page.waitForTimeout(120); seen.push(await bottom());
  }
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
  await openPosition(page); await page.locator('#lock').click(); await ready(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await openAlignment(page); await page.locator('[data-align="-1"][data-axis="0"]').click(); await page.waitForTimeout(150);
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
  await openPosition(page);
  for (const [axis, value] of [[0, 0], [1, 0], [2, 0]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  await page.locator('#object-list button', {hasText: 'Ida'}).dispatchEvent('click');
  await page.locator('#hole').click(); await ready(page);
  assert.equal((await place(page))[2], 3, 'the name now cuts 1 mm down from the top of the plate');
  await page.locator('[data-add=cylinder][data-sides="6"]').click(); await ready(page);
  assert.equal(await page.locator('#sides').inputValue(), '6');
  assert.deepEqual(problems, []); await page.close();
});

test('Fordel gives three parts equal gaps; the outermost stay', async () => {
  const {page, problems} = await open();
  for (const x of [-40, -25, 40]) {
    await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
    await openPosition(page);
    for (const [axis, value] of [[0, x], [1, 0]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  }
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await openAlignment(page); await page.locator('[data-spread="0"]').click(); await ready(page);
  assert.match(await page.locator('#status').textContent(), /20 mm mellemrum/);
  await page.locator('#show-parts').click();
  await page.locator('#object-list button').nth(1).click(); assert.deepEqual((await place(page)).slice(0, 2), [0, 0]);
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
  try {
    await moving.clock.install();
    for (const page of [still, moving]) { await page.goto(base); await ready(page); }
    await still.locator('[data-camera=top]').dispatchEvent('click');
    const initially = await where(moving);
    await still.waitForFunction(start => [...document.querySelectorAll('.cube-face')].map(face => face.style.transform).join('|') !== start, initially);
    const destination = await where(still);
    await moving.clock.pauseAt(await moving.evaluate(() => Date.now() + 100));
    await moving.locator('[data-camera=top]').dispatchEvent('click');
    assert.notEqual(await where(moving), destination, 'the view is still on its way right after the click');
    await moving.clock.runFor(100);
    assert.notEqual(await where(moving), initially, 'the view takes a step toward the destination');
    assert.notEqual(await where(moving), destination, 'the step is still between the two views');
    await moving.clock.runFor(300);
    assert.equal(await where(moving), destination, 'the last frame shows the exact destination');
  } finally { await still.close(); await moving.close(); }
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

test('Tilpas tekst fits the name around an existing through-hole and can be undone in one step', async () => {
  const {page, problems} = await open();
  try {
    await addBox(page, [60, 30, 6], [0, 0, 0]);
    await page.getByRole('button', {name: 'Rundt hul', exact: true}).click(); await ready(page);
    await writeMeasure(page, '[data-size="0"]', 8);
    await writeMeasure(page, '[data-size="2"]', 8);
    await writeMeasure(page, '[data-hole-offset="0"]', 10);
    await writeMeasure(page, '[data-hole-offset="1"]', 15);
    await page.locator('#cut-holes').click(); await ready(page);
    await page.getByRole('button', {name: 'Tekst', exact: true}).click(); await ready(page);
    const name = 'Charlotte og Alexander', textField = page.locator('#text-value');
    await textField.fill(name); await textField.press('Enter');
    await page.locator('#selection-label', {hasText: name}).waitFor(); await ready(page);
    const original = await sizes(page), originalPosition = await place(page);
    assert(original[0] > 60, 'the text starts wider than the plate');
    const fit = page.getByRole('button', {name: 'Tilpas tekst', exact: true});
    assert.equal(await fit.isVisible(), true, 'a name hanging over a plate with an existing hole can be fitted');
    const samples = [];
    for (let x = -3.5; x <= 3.5; x += .5) for (let y = -3.5; y <= 3.5; y += .5) {
      if (Math.hypot(x, y) < 3.75) samples.push([x - 20, y]);
    }
    const cross = ([ax, ay], [bx, by], [x, y]) => (bx - ax) * (y - ay) - (by - ay) * (x - ax);
    const coveringTriangles = async () => {
      const download = page.waitForEvent('download');
      await page.locator('#download').click(); await nameIfAsked(page);
      if (await page.locator('#print-check').isVisible()) await page.getByRole('button', {name: 'Hent STL alligevel'}).click();
      const stl = await readFile(await (await download).path());
      const faces = stl.readUInt32LE(80);
      let covered = 0;
      for (let i = 0; i < faces; i++) {
        const corners = [0, 1, 2].map(c => [0, 1].map(a => stl.readFloatLE(96 + 50 * i + 12 * c + 4 * a)));
        const [a, b, c] = corners, area = cross(a, b, c);
        if (Math.abs(area) < 1e-6) continue; // A vertical wall won't block a vertical path.
        const side = Math.sign(area);
        if (samples.some(point => [cross(a, b, point), cross(b, c, point), cross(c, a, point)].every(edge => edge * side >= -1e-6))) covered++;
      }
      return covered;
    };
    assert(await coveringTriangles() > 0, 'the unfitted text blocks part of the opening in the STL');
    await fit.click(); await ready(page);
    const fitted = await sizes(page), fittedPosition = await place(page);
    assert(fitted[0] <= 60, 'the text fits on its plate');
    assert.equal(fitted[2], original[2], 'the text keeps its thickness');
    assert.equal(fittedPosition[2], originalPosition[2], 'the text stays on its plate');
    assert(Math.hypot(fittedPosition[0] - originalPosition[0], fittedPosition[1] - originalPosition[1]) > 1, 'the text moved to clear the opening');
    assert.equal(await textField.inputValue(), name);
    assert.equal(await fit.isVisible(), false);
    assert.equal(await coveringTriangles(), 0, 'the opening is clear in the fitted STL');
    await page.locator('#undo').click(); await ready(page);
    assert.deepEqual(await sizes(page), original);
    assert.deepEqual(await place(page), originalPosition);
    assert.equal(await textField.inputValue(), name);
    assert.equal(await fit.isVisible(), true);
    assert.deepEqual(problems, []);
  } finally { await page.close(); }
});

test('pointing at a Ret ind button shows where the parts would line up, without moving them', async () => {
  const {page, problems} = await open();
  for (const kind of ['box', 'sphere']) { await page.locator(`[data-add=${kind}]:not([data-mode])`).first().click(); await ready(page); }
  await page.locator('#show-parts').click();
  const lastPart = page.locator('#object-list button').last();
  await lastPart.click(); const before = await place(page);
  await page.locator('#workspace').focus(); await page.keyboard.press('Control+a');
  await openAlignment(page); await page.locator('[data-align="-1"][data-axis="1"]').hover(); await page.waitForTimeout(200);
  assert.match(await page.locator('#status').textContent(), /Sæt midt på|klar|valgt|Ret ind/, 'nothing was changed by pointing');
  await lastPart.click(); assert.deepEqual(await place(page), before, 'the part has not moved');
  assert.deepEqual(problems, []); await page.close();
});

test('review 2: the hexagon is regular; Ctrl+A leaves hidden parts out; a star dropped on a box stands on it', async () => {
  const {page, problems} = await open();
  await page.locator('[data-add=cylinder][data-sides="6"]').click(); await ready(page);
  assert.deepEqual(await sizes(page), [20, 17.32, 20]);
  await openPosition(page); await page.locator('#hide').click(); await ready(page);
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
    await openPosition(page);
    for (const [axis, value] of [[0, 0], [1, 0], [2, z]]) { await page.locator(`[data-position="${axis}"]`).fill(String(value)); await page.locator(`[data-position="${axis}"]`).press('Enter'); await ready(page); }
  }
  await viewFrom(page, 'top'); await page.waitForTimeout(200);
  const corners = await page.evaluate(() => [...document.querySelectorAll('.handle.corner .knob')].map(k => { const r = k.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }));
  const middle = [0, 1].map(axis => corners.reduce((sum, point) => sum + point[axis], 0) / corners.length);
  // The bottom plate is chosen; the top plate lies over it on screen.
  await page.locator('#object-list button').first().dispatchEvent('click');
  await page.mouse.move(...middle); await page.mouse.down(); await page.mouse.move(middle[0] + 60, middle[1], {steps: 8}); await page.mouse.up(); await ready(page);
  await page.locator('#object-list button').first().dispatchEvent('click'); assert.deepEqual((await place(page)).slice(0, 2), [0, 0], 'the plate behind stays');
  await page.locator('#object-list button').last().dispatchEvent('click'); assert.notDeepEqual((await place(page)).slice(0, 2), [0, 0], 'the plate taken hold of moved');
  assert.deepEqual(problems, []); await page.close();
});

test('the inspector opens compact and remembers whether the pupil opened Placering', async () => {
  const {page, problems} = await open();
  const details = () => page.evaluate(() => [document.querySelector('#rotation-controls').open, document.querySelector('#position-details').open]);
  await page.locator('[data-add=box]:not([data-mode])').click(); await ready(page);
  assert.deepEqual(await details(), [false, false]);
  await page.locator('#rotation-controls summary').click(); await page.locator('#position-details summary').click();
  await page.locator('[data-add=sphere]').click(); await ready(page);
  assert.deepEqual(await details(), [false, true]);
  await page.locator('#position-details summary').click();
  await page.locator('[data-add=cone]').click(); await ready(page);
  assert.deepEqual(await details(), [false, false]);
  assert.deepEqual(problems, []); await page.close();
});
