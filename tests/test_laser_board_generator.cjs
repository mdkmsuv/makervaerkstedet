'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '..');
let server;
let browser;
let cleanupUrl;

function startServer() {
  const native = process.platform === 'win32' ? process.env.MKV_QA_PYTHON : 'python3';
  const args = native ? ['-B', 'tests/fixtures/installed_app_server.py'] :
    ['-u', 'root', '--cd', root, '--', 'env', 'MKV_INSTALLED_APP_FILTER=laser', 'python3', '-B', 'tests/fixtures/installed_app_server.py'];
  server = spawn(native || 'wsl.exe', args, {
    cwd: root, env: {...process.env, MKV_INSTALLED_APP_FILTER: 'laser'}, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']
  });
  return new Promise((resolve, reject) => {
    let buffer = '';
    let stderr = '';
    const timer = setTimeout(() => reject(new Error(`Fixture timeout: ${stderr}`)), 45000);
    server.stderr.on('data', data => stderr += data);
    server.stdout.on('data', data => {
      buffer += data;
      const end = buffer.indexOf('\n');
      if (end < 0) return;
      clearTimeout(timer);
      try {
        const fixture = JSON.parse(buffer.slice(0, end));
        cleanupUrl = fixture.cleanup;
        resolve(fixture.laser);
      }
      catch (error) { reject(error); }
    });
    server.once('exit', code => { clearTimeout(timer); reject(new Error(`Fixture exit ${code}: ${stderr}`)); });
  });
}

const snapshot = page => page.evaluate(async () => JSON.parse(await (await window.ElevhubProjectAdapter.export()).text()));

async function main() {
  const url = await startServer();
  browser = await chromium.launch({headless: true, channel: process.platform === 'win32' ? 'chrome' : undefined});
  const page = await browser.newPage({viewport: {width: 1366, height: 900}});
  page.setDefaultTimeout(5000);
  const errors = [];
  const outside = [];
  let submission;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/laser/submit') {
      submission = route.request().postDataJSON();
      return route.fulfill({status: 202, json: {queued: true}});
    }
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
      outside.push(url.href);
      return route.abort();
    }
    return route.continue();
  });
  await page.goto(url);
  await page.locator('#project-classroom').fill('QA-6');
  await page.locator('#project-student').fill('Syntetisk elev');
  await page.locator('#project-title').fill('Spillepladeprøve');
  await page.locator('#project-start-confirm').click();
  await page.waitForFunction(() => !document.querySelector('#elevhub-project-dialog')?.open);
  const starter = await snapshot(page);
  assert.deepEqual(starter.workspace, {widthMm: 250, heightMm: 167});

  const open = page.getByRole('button', {name: 'Lav en spilleplade', exact: true});
  const dialog = page.getByRole('dialog', {name: 'Lav en spilleplade'});
  const size = dialog.getByLabel('Størrelse', {exact: true});
  const removeExample = dialog.getByRole('checkbox', {name: 'Fjern nøgleringseksemplet'});
  const insert = dialog.getByRole('button', {name: 'Sæt ind i tegningen'});
  for (const [millimeters, widthMm, heightMm] of [[200, 250, 220], [300, 320, 320]]) {
    await open.click();
    assert.ok(await removeExample.isChecked());
    if (millimeters === 200) {
      await removeExample.uncheck();
      assert.ok(await dialog.locator('#boardOverlapNote').isVisible());
    }
    await size.selectOption(String(millimeters));
    await insert.click();
    const sample = await snapshot(page);
    assert.deepEqual(sample.workspace, {widthMm, heightMm});
    const board = sample.project.objects.at(-1);
    assert.deepEqual([board.width, board.height], [millimeters, millimeters]);
    assert.deepEqual(sample.project.objects.slice(0, -1), millimeters === 200 ? starter.project.objects : []);
    await page.locator('#undoButton').click();
    assert.deepEqual(await snapshot(page), starter);
  }

  await page.locator('[data-add-type="circle"]').click();
  const before = await snapshot(page);
  await open.click();
  assert.ok(!await removeExample.isVisible());
  assert.ok(await dialog.locator('#boardOverlapNote').isVisible());
  await size.click();
  await page.keyboard.press('Escape');
  assert.equal(await dialog.isVisible(), true, 'Escape closes the picker before it closes the board dialog');
  await size.click();
  await dialog.getByRole('option', {name: '37 × 37 cm', exact: true}).click();
  assert.equal(await size.inputValue(), '370');
  const track = dialog.getByLabel('Bane', {exact: true});
  await track.click();
  await dialog.getByRole('option', {name: 'Rund spiral', exact: true}).click();
  assert.equal(await track.inputValue(), 'spiral');
  const shape = dialog.getByLabel('Felternes form');
  await shape.click();
  await dialog.getByRole('option', {name: 'Cirkler', exact: true}).click();
  assert.equal(await shape.inputValue(), 'cirkel');
  assert.equal(await dialog.locator('svg').getAttribute('viewBox'), '0 0 370 370');
  await page.keyboard.press('Escape');
  assert.deepEqual(await snapshot(page), before, 'Closing preview leaves the drawing and workspace untouched');
  assert.equal(await open.evaluate(node => document.activeElement === node), true);

  await open.click();
  await insert.click();
  const inserted = await snapshot(page);
  assert.equal(inserted.project.objects.length, before.project.objects.length + 1);
  assert.deepEqual(inserted.project.objects.slice(0, -1), before.project.objects);
  assert.deepEqual(inserted.workspace, {widthMm: 390, heightMm: 390});
  assert.equal(await page.locator('#designCanvas').getAttribute('viewBox'), '0 0 390 390');
  const board = inserted.project.objects.at(-1);
  assert.equal(board.type, 'group');
  assert.deepEqual([board.width, board.height], [370, 370]);
  assert.deepEqual(inserted.project.selectedIds, [board.id]);
  assert.equal(await page.locator(`#objectLayer [data-object-id="${board.id}"]`).count(), 1);
  assert.equal(await page.locator('#selectedProcessBadge').textContent(), 'Blandet');

  await page.locator('#undoButton').click();
  assert.deepEqual(await snapshot(page), before);
  await page.locator('#redoButton').click();
  assert.deepEqual(await snapshot(page), inserted);
  const boardNode = page.locator(`#objectLayer [data-object-id="${board.id}"]`);
  await boardNode.focus();
  await page.keyboard.press('ArrowRight');
  const nudged = await snapshot(page);
  assert.ok(nudged.project.objects.at(-1).x > board.x);
  const box = await boardNode.boundingBox();
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.3 - 5, box.y + box.height * 0.3 + 5, {steps: 4});
  await page.mouse.up();
  const moved = await snapshot(page);
  const movedBoard = moved.project.objects.at(-1);
  assert.ok(movedBoard.x < nudged.project.objects.at(-1).x && movedBoard.y > board.y);
  assert.deepEqual(moved.project.objects.at(-1).children, board.children);

  const saving = page.waitForResponse(response => response.url().includes('/_elevhub/projekter/api/save?') && response.request().method() === 'POST');
  await page.locator('#saveProjectButton').click();
  const reply = await saving;
  assert.equal(reply.status(), 200);
  const saved = await reply.json();
  await page.goto(`${url}?elevhub_project=${saved.id}`);
  await page.locator(`#objectLayer [data-object-id="${board.id}"]`).waitFor();
  assert.deepEqual(await snapshot(page), moved, 'Project shelf restores the workspace, group, movement and engraving modes');

  await page.locator('#reviewButton').click();
  assert.equal(await page.locator('#reviewCanvas').getAttribute('viewBox'), '0 0 390 390');
  await page.locator('#reviewStudentName').fill('Syntetisk elev');
  await page.locator('#reviewClass').fill('QA-6');
  await page.locator('#submitTestButton').click();
  await page.waitForFunction(() => !document.querySelector('#reviewSuccess').hidden);
  assert.ok(submission, 'Intercept the real editor request without queue access');
  const operations = await page.evaluate(svg => {
    const root = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement;
    const drawing = document.createElementNS(root.namespaceURI, 'svg');
    const cut = document.createElementNS(root.namespaceURI, 'path');
    cut.setAttribute('d', root.querySelector('path[data-name="Pladens kant"]').getAttribute('d'));
    drawing.style.cssText = 'position:fixed; visibility:hidden';
    drawing.append(cut);
    document.body.append(drawing);
    const {x, y, width, height} = cut.getBBox();
    drawing.remove();
    return {width: root.getAttribute('width'), height: root.getAttribute('height'), bounds: {x, y, width, height}, paths: Object.fromEntries(
      [...root.querySelectorAll('g')].map(group => [group.getAttribute('data-operation'), [...group.children].map(path => path.getAttribute('d'))])
    )};
  }, submission.svg);
  assert.deepEqual([operations.width, operations.height], ['390mm', '390mm']);
  assert.deepEqual(Object.keys(operations.paths).sort(), ['cut', 'engrave-fill', 'engrave-line']);
  assert.ok(operations.paths['engrave-line'].some(path => /z/i.test(path)), 'Closed field outlines stay line engravings');
  for (const field of ['x', 'y', 'width', 'height']) {
    assert.ok(Math.abs(operations.bounds[field] - movedBoard[field]) < 0.01, `Cut geometry keeps its millimeter ${field}`);
  }

  await page.locator('#finishReviewButton').click();
  const legacy = {...starter, version: 1};
  await page.locator('#projectFileInput').setInputFiles({
    name: 'legacy-example.laser.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(legacy))
  });
  await page.waitForFunction(() => !document.querySelector('#projectFileInput').value);
  assert.deepEqual(await snapshot(page), starter);
  await open.click();
  assert.ok(!await removeExample.isVisible(), 'The same geometry in a project file is preserved');
  assert.ok(await dialog.locator('#boardOverlapNote').isVisible());
  await page.keyboard.press('Escape');
  assert.deepEqual(outside, []);
  assert.deepEqual(errors, []);
  console.log('PASS offline board preview, 200/300/370 mm, example safety, grouped insertion, undo/redo, drag and nudge, project reopening, legacy file and intercepted manufacturing layers');
}

main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  await browser?.close();
  if (!server || server.exitCode !== null) return;
  try {
    const response = await fetch(cleanupUrl, {method: 'POST', signal: AbortSignal.timeout(2000)});
    if (!response.ok) server.kill();
  } catch { server.kill(); }
});
