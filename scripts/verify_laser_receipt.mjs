#!/usr/bin/env node
// Isolated browser checks for the pupil submission receipt. The test bundles
// the current source in memory and never writes web/vendor/laser.js.
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const esbuild = require('esbuild');
const {chromium} = require('playwright');

let submitMode = 'ok';
let nextId = 1;
const bundle = (await esbuild.build({
  entryPoints: [path.join(root, 'web/laser.js')], bundle: true, minify: false,
  write: false, format: 'iife', platform: 'browser', sourcemap: false,
})).outputFiles[0].text;

const mime = ext => ({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'}[ext] || 'application/octet-stream');
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname === '/vendor/laser.js') {res.writeHead(200, {'content-type': 'text/javascript; charset=utf-8'});res.end(bundle);return;}
  if (url.pathname === '/api/laser/health') {res.writeHead(200, {'content-type':'application/json'});res.end(JSON.stringify({surface:'student',inbox:true}));return;}
  if (url.pathname === '/api/laser/submit') {
    if (submitMode === 'fail') {res.writeHead(503, {'content-type':'application/json'});res.end(JSON.stringify({error:'mock failure'}));return;}
    let body=''; for await (const chunk of req) body += chunk;
    const payload=JSON.parse(body); const id=`receipt-${nextId++}`;
    res.writeHead(200, {'content-type':'application/json'});res.end(JSON.stringify({id, duplicate:false, project:payload.projekt}));return;
  }
  let file = url.pathname === '/' ? '/laser.html' : url.pathname;
  // The production server uses the same editor at both workshop URLs.
  if (file === '/folie.html') file = '/laser.html';
  if (file === '/assets/metrophobic-latin.ttf') {try {res.writeHead(200, {'content-type':'font/ttf'});res.end(await readFile(path.join(root, 'web/assets/metrophobic-latin.ttf')));return;} catch {}}
  if (file === '/laser.html' || file === '/folie.html' || file === '/school-theme.js' || file === '/project-import.js' || file === '/laser.css' || file === '/desktop-studio.css' || file === '/school-theme.css' || file === '/laser-workshop.css') {
    try {const data=await readFile(path.join(root, 'web', file.slice(1)));res.writeHead(200, {'content-type':mime(path.extname(file))});res.end(data);return;} catch {}
  }
  res.writeHead(404);res.end('missing');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({headless:true, executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe', args:['--no-sandbox']});
const context = await browser.newContext();
const page = await context.newPage();
const errors=[];
page.on('pageerror', error => errors.push(error.message));
const waitEditor = () => page.waitForFunction(() => !!window.ElevhubProjectAdapter);
const waitReady = async () => {try {await page.waitForFunction(() => document.querySelector('#send-button') && !document.querySelector('#send-button').disabled, null, {timeout:10000});} catch (error) {throw new Error(`send count=${await page.locator('#send-button').count()} disabled=${await page.locator('#send-button').isDisabled()} title=${await page.locator('#send-button').getAttribute('title')} notice=${await page.locator('#notice').textContent()}`, {cause:error});}};
const openSend = async (name='Ada', classroom='8.A', project='Testskilt', expectSuccess=true) => {
  await waitEditor();
  if (await page.locator('.design-item').count() === 0) await page.click('[data-add="rect"]');
  await waitReady(); await page.click('#send-button');
  await page.fill('#student-name', name); await page.fill('#student-class', classroom); await page.fill('#student-project', project);
  await page.click('#confirm-send');
  await page.waitForFunction(expectSuccess
    ? () => document.querySelector('#send-status')?.textContent.includes('Modtaget hos læreren')
    : () => document.querySelector('#send-status')?.classList.contains('error'));
};
const setMode = async mode => {submitMode=mode;};
try {
  await page.goto(`${base}/laser.html`); await openSend();
  assert.match(await page.locator('#latest-receipt-text').textContent(), /TESTSKILT|Testskilt/);
  await page.click('[data-close="send-dialog"]');
  assert.equal(await page.locator('#latest-receipt').isVisible(), true, 'receipt survives closing the dialog');
  await page.reload(); await waitEditor();
  assert.equal(await page.locator('#latest-receipt').isVisible(), true, 'receipt survives reload');
  assert.match(await page.locator('#latest-receipt-note').textContent(), /ændringer.*ny aflevering/i);
  await page.fill('#project-name', 'Ny version');
  assert.equal(await page.locator('#latest-receipt').isVisible(), true, 'later edits do not relabel the old receipt');

  await page.evaluate(() => localStorage.setItem('kloster-laservaerksted-v1-receipt', '{bad json'));
  await page.reload(); await waitEditor();
  assert.equal(await page.locator('#latest-receipt').isVisible(), false, 'corrupt receipt is ignored');
  await page.evaluate(() => localStorage.setItem('kloster-laservaerksted-v1-receipt', JSON.stringify({id:'only-id', project:'missing-fields'})));
  await page.reload(); await waitEditor();
  assert.equal(await page.locator('#latest-receipt').isVisible(), false, 'malformed receipt object is ignored');

  await setMode('fail'); await openSend('Bo', '8.B', 'Fejlet', false);
  assert.equal(await page.locator('#latest-receipt').isVisible(), false, 'network failure creates no receipt');
  await setMode('ok');

  await page.addInitScript(() => {const set=Storage.prototype.setItem; Storage.prototype.setItem=function(){if (arguments[0].endsWith('-receipt')) throw new Error('quota'); return set.apply(this, arguments);};});
  await page.reload(); await openSend('Cleo', '8.C', 'Uden lagerplads');
  assert.equal(await page.locator('#latest-receipt').isVisible(), true, 'receipt remains visible when receipt storage fails');

  const folie = await context.newPage(); await folie.goto(`${base}/folie.html`);
  folie.on('pageerror', error => errors.push(error.message));
  await folie.waitForFunction(() => window.ElevhubProjectAdapter?.type === 'folie');
  assert.equal(await folie.locator('#latest-receipt').isVisible(), false, 'folie editor hides laser receipt'); await folie.close();
  assert.deepEqual(errors, [], 'no JavaScript errors');
  console.log('verify_laser_receipt: all checks passed');
} finally {await browser.close();server.close();}
