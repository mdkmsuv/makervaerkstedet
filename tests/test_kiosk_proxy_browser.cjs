/* Real Chromium regression for cold offline boot and reconnect policy. */
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

const root = path.join(__dirname, '..');
const python = process.env.PYTHON || process.env.PYTHON3 ||
  (process.platform === 'win32' ? path.join(root, '.venv', 'Scripts', 'python.exe') : path.join(root, '.venv', 'bin', 'python'));

function setMode(port, mode) {
  return new Promise((resolve, reject) => {
    const request = http.get(`http://127.0.0.1:${port}/__set?mode=${mode}`, response => {
      response.resume();
      response.once('end', () => response.statusCode === 204 ? resolve() : reject(new Error(`fixture mode ${response.statusCode}`)));
    });
    request.setTimeout(3000, () => request.destroy(new Error('fixture request timeout')));
    request.once('error', reject);
  });
}

function denyProxy() {
  const seen = [];
  const server = net.createServer(socket => {
    let data = '';
    socket.setTimeout(3000, () => socket.destroy());
    socket.on('data', chunk => {
      data += chunk.toString();
      if (!data.includes('\r\n\r\n')) return;
      const first = data.split(/\r?\n/, 1)[0];
      seen.push(first);
      socket.end('HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');
    });
  });
  return new Promise((resolve, reject) => server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port, seen })));
}

function fixtureProcess() {
  const child = spawn(python, [path.join(root, 'linux-elevhub', 'tests', 'kiosk_browser_fixture.py'), '--port', '0'], { cwd: root, stdio: ['ignore', 'pipe', 'inherit'], windowsHide: true });
  let buffer = '';
  const port = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('fixture did not announce a port')), 10000);
    child.stdout.on('data', chunk => {
      buffer += chunk.toString();
      const line = buffer.split(/\r?\n/)[0];
      if (/^\d+$/.test(line)) { clearTimeout(timer); resolve(Number(line)); }
    });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); if (code) reject(new Error(`fixture exited ${code}`)); });
  });
  return { child, port };
}

async function main() {
  const fixture = fixtureProcess();
  let proxy;
  let browser;
  try {
    proxy = await denyProxy();
    const port = await fixture.port;
    browser = await chromium.launch({
      channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || (process.platform === 'win32' ? 'chrome' : undefined),
      headless: true,
      args: [
        `--proxy-server=http=127.0.0.1:${proxy.port};https=127.0.0.1:${proxy.port}`,
        '--proxy-bypass-list=school.example.test,127.0.0.1,localhost',
        '--host-resolver-rules=MAP school.example.test 127.0.0.1',
      ],
    });
    const context = await browser.newContext();
    await context.route('**/*', route => {
      const requestUrl = new URL(route.request().url());
      if ((requestUrl.hostname === '127.0.0.1' || requestUrl.hostname === 'localhost') && requestUrl.port && Number(requestUrl.port) !== port) {
        return route.fulfill({ status: 404, body: 'fixture-only' });
      }
      return route.continue();
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error));
    const url = `http://school.example.test:${port}/start.html`;
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    const ids = () => page.locator('#apps [data-app]').evaluateAll(cards => cards.map(card => card.dataset.app).sort());
    await page.waitForFunction(() => document.querySelectorAll('#apps [data-app]').length === 9);
    assert.deepEqual(await ids(), ['3dprint', 'datatraener', 'folie', 'internet', 'kredsloeb', 'laser', 'microbit', 'scratch', 'skolegpt']);

    await setMode(port, 'closed');
    await page.waitForFunction(() => document.querySelectorAll('#apps [data-app]').length === 1, null, { timeout: 7000 });
    assert.deepEqual(await ids(), ['scratch'], 'reconnect applies the teacher-closed policy');

    await setMode(port, 'down');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelectorAll('#apps [data-app]').length === 9);
    assert.deepEqual(await ids(), ['3dprint', 'datatraener', 'folie', 'internet', 'kredsloeb', 'laser', 'microbit', 'scratch', 'skolegpt'], 'offline clears stale closures and pauses');

    await setMode(port, 'open');
    await page.waitForFunction(() => document.querySelectorAll('#apps [data-app]').length === 3, null, { timeout: 7000 });
    assert.deepEqual(await ids(), ['internet', 'laser', 'scratch'], 'reconnect adopts the new teacher policy');
    assert.equal(proxy.seen.filter(line => line.includes('school.example.test')).length, 0, 'Hub traffic bypasses the deny proxy');
    await page.goto('https://internet.example.test/', { waitUntil: 'domcontentloaded', timeout: 3000 }).catch(() => {});
    assert.ok(proxy.seen.some(line => line.includes('internet.example.test')), 'Internet probe reaches the deny proxy');
    assert.equal(pageErrors.length, 0, 'no unexpected page errors');

    await browser.close();
    browser = await chromium.launch({
      channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || (process.platform === 'win32' ? 'chrome' : undefined),
      headless: true,
      args: [`--proxy-server=http=127.0.0.1:${proxy.port};https=127.0.0.1:${proxy.port}`, '--proxy-bypass-list=127.0.0.1,localhost', '--host-resolver-rules=MAP school.example.test 127.0.0.1'],
    });
    const negativeContext = await browser.newContext();
    await negativeContext.route('**/*', route => {
      const requestUrl = new URL(route.request().url());
      if ((requestUrl.hostname === '127.0.0.1' || requestUrl.hostname === 'localhost') && requestUrl.port && Number(requestUrl.port) !== port) {
        return route.fulfill({ status: 404, body: 'fixture-only' });
      }
      return route.continue();
    });
    const negative = await negativeContext.newPage();
    await negative.goto(`http://127.0.0.1:${port}/start.html`, { waitUntil: 'domcontentloaded' });
    await negative.waitForFunction(() => document.querySelectorAll('#apps [data-app]').length === 9, null, { timeout: 7000 });
    assert.ok(proxy.seen.some(line => line.includes(`school.example.test:${port}`)), 'negative control detects Hub traffic sent through proxy');
    console.log('kiosk proxy browser regression: ok');
  } finally {
    await browser?.close();
    fixture.child.kill();
    proxy?.server.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
