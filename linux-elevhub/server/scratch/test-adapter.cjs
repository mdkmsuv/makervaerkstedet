'use strict';

const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const runtimeRequire = createRequire(__filename);
const { chromium } = runtimeRequire('playwright');

const root = process.env.SCRATCH_BUILD || 'D:\\Elevhub-VM\\scratch\\scratch-gui-v15.1.1-local-projects-build';
const port = Number(process.env.SCRATCH_ADAPTER_PORT || 8800);
const mime = {'.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.svg':'image/svg+xml', '.json':'application/json', '.png':'image/png', '.ico':'image/x-icon'};
const server = http.createServer((req, res) => {
  const requested = decodeURIComponent((req.url || '/').split('?')[0]);
  const file = path.resolve(root, `.${requested === '/' ? '/index.html' : requested}`);
  if (!file.startsWith(path.resolve(root))) { res.writeHead(403); return res.end(); }
  fs.readFile(file, (error, data) => {
    if (error) { res.writeHead(404); return res.end(); }
    res.writeHead(200, {'content-type': mime[path.extname(file)] || 'application/octet-stream'}); res.end(data);
  });
});

(async () => {
  await new Promise(resolve => server.listen(port, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH});
  const page = await browser.newPage();
  try {
    await page.goto(`http://127.0.0.1:${port}/`, {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => window.ElevhubProjectAdapter && window.ElevhubProjectAdapter.type === 'scratch');
    await page.evaluate(() => window.ElevhubProjectAdapter.reset());
    await page.keyboard.press('Control+z');
    await page.waitForTimeout(200);
    const after = await page.evaluate(() => ({signature: window.ElevhubProjectAdapter.signature(), hasContent: window.ElevhubProjectAdapter.hasContent()}));
    assert.equal(after.hasContent, false, 'default Scratch project is not dirty');
    const targetCount = await page.evaluate(() => JSON.parse(window.ElevhubProjectAdapter.signature()).targets.length);
    assert.equal(targetCount, 2, 'reset/undo must retain Scratch stage and sprite');
    console.log('Scratch adapter reset/undo test passed');
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
