'use strict';
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const runtimeRequire = createRequire(__filename);
const {chromium} = runtimeRequire('playwright');
const root = process.env.DATATRAENER_BUILD || 'D:/Elevhub-VM/datatraener/cctd-ml-machine-release-dr-build-adapter-test-20261009e';
const port = Number(process.env.DATATRAENER_ADAPTER_PORT || 8813);
const server = http.createServer((req, res) => {
  const file = path.resolve(root, `.${decodeURIComponent((req.url || '/').split('?')[0] === '/' ? '/index.html' : (req.url || '/').split('?')[0])}`);
  if (!file.startsWith(path.resolve(root))) return res.writeHead(403).end();
  fs.readFile(file, (error, data) => { if (error) return res.writeHead(404).end(); res.writeHead(200, {'content-type': path.extname(file) === '.js' ? 'text/javascript' : 'text/html'}); res.end(data); });
});
(async () => {
  await new Promise(resolve => server.listen(port, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH});
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${port}/`, {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => window.ElevhubProjectAdapter?.type === 'datatraener', null, {timeout: 15000});
    const result = await page.evaluate(async () => {
      const adapter = window.ElevhubProjectAdapter;
      const before = adapter.signature();
      await adapter.reset();
      const empty = {signature: adapter.signature(), hasContent: adapter.hasContent(), name: adapter.name()};
      const blob = await adapter.export();
      const project = JSON.parse(await blob.text());
      return {before, empty, project};
    });
    assert.equal(result.empty.hasContent, false);
    assert.equal(result.empty.name, 'Datatræner-projekt');
    assert.equal(result.project.requiresRetraining, true);
    assert.equal(result.project.format, 'elevhub-datatraener-project');
    console.log('Datatræner adapter reset/export test passed');
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
