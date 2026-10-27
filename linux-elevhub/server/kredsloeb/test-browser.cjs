/* Reproducible local browser smoke test. Run with a local Playwright install. */
'use strict';

const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const runtimeRequire = createRequire(__filename);
const { chromium } = runtimeRequire('playwright');

const port = process.env.KREDSLOEB_PORT || '8812';
const base = `http://127.0.0.1:${port}`;
const screenshot = process.env.KREDSLOEB_SCREENSHOT || 'test-browser-output.png';
const expected = [
  { id: 'ohm', required: ['resistor'], text: ['5 mA', '1 kΩ'] },
  { id: 'serie', required: ['resistor', 'resistor'], text: ['3 mA', 'Vd = 3 V', 'Vd = 6 V'] },
  { id: 'parallel', required: ['resistor', 'resistor'], text: ['13.5 mA', '9 mA', '4.5 mA'] },
  { id: 'led', required: ['LED'] },
  { id: 'kondensator', required: ['capacitor'] },
  { id: 'kontakt', required: ['switch (SPST)', 'lamp'] }
];

function count(items, value) {
  return items.filter((item) => item.type === value).length;
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const external = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.origin !== base) external.push(request.url());
  });

  try {
    await page.goto(`${base}/?browser-test=1`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => Boolean(window.ElevhubProjectAdapter));
    await page.waitForTimeout(2500);
    const adapterMethods = await page.evaluate(() => ({
      signature: typeof window.ElevhubProjectAdapter.signature,
      hasContent: typeof window.ElevhubProjectAdapter.hasContent,
      reset: typeof window.ElevhubProjectAdapter.reset,
      export: typeof window.ElevhubProjectAdapter.export,
      import: typeof window.ElevhubProjectAdapter.import
    }));
    assert.deepEqual(adapterMethods, {
      signature: 'function', hasContent: 'function', reset: 'function',
      export: 'function', import: 'function'
    }, 'adapter contract is incomplete');

    if (process.env.KREDSLOEB_QUICK === '1') {
      const initial = await page.evaluate(() => window.ElevhubProjectAdapter.signature());
      await page.evaluate(() => { window.ElevhubProjectSession = {replace: async () => false}; });
      await page.locator('[data-example-id="serie"]').click();
      await page.waitForTimeout(500);
      const refused = await page.evaluate(() => ({title: document.querySelector('#active-title').textContent, active: document.querySelector('.example-button.active')?.dataset.exampleId}));
      assert.equal(refused.active, 'kontakt', 'Arbejd videre must keep the current circuit');
      await page.evaluate(() => { window.ElevhubProjectSession.replace = async callback => callback(); });
      await page.locator('[data-example-id="serie"]').click();
      await page.waitForTimeout(500);
      const changed = await page.evaluate(() => window.ElevhubProjectAdapter.signature());
      assert.notEqual(changed, initial, 'selecting a different circuit must change signature');
      await page.evaluate(async () => window.ElevhubProjectAdapter.reset());
      await page.keyboard.press('Control+z');
      const reset = await page.evaluate(() => ({signature: window.ElevhubProjectAdapter.signature(), hasContent: window.ElevhubProjectAdapter.hasContent()}));
      assert.equal(reset.hasContent, false, 'reset must restore the starter circuit');
      assert.match(reset.signature, /<cir /, 'reset must leave an exportable circuit');
      console.log('Kredsløb adapter reset/undo test passed');
      return;
    }

    const results = [];
    for (let index = 0; index < expected.length; index += 1) {
      const wanted = expected[index];
      await page.locator(`[data-example-id="${wanted.id}"]`).click();
      const result = await page.evaluate(async () => {
        const frame = document.querySelector('#simulator');
        const sim = frame.contentWindow.CircuitJS1;
        const blob = await window.ElevhubProjectAdapter.export();
        const text = await blob.text();
        const elements = sim.getElements().map((element) => ({
          type: element.getElmType(),
          dump: element.getDumpType(),
          info: element.getInfo()
        }));
        let switchBefore;
        let switchAfter;
        let lampAfter;
        if (window.ElevhubProjectAdapter.name().includes('Kontakt')) {
          const sw = sim.getElements().find((element) => element.getElmType() === 'switch (SPST)');
          switchBefore = sw.getInfo();
          const contact = window.KREDSLOEB_EKSEMPLER.find((example) => example.id === 'kontakt');
          const closedCircuit = contact.circuit.replace('s 208 128 272 128 0 1 false', 's 208 128 272 128 0 0 false');
          sim.importCircuit(closedCircuit, false);
          await new Promise((resolve) => setTimeout(resolve, 500));
          switchAfter = sw.getInfo();
          lampAfter = sim.getElements().find((element) => element.getElmType() === 'lamp').getInfo();
        }
        return { name: window.ElevhubProjectAdapter.name(), text, elements, switchBefore, switchAfter, lampAfter };
      });
      const infos = result.elements.flatMap((element) => element.info || []);
      const types = result.elements.map((element) => element.type);
      assert.match(result.text, /<cir /, `${wanted.id} export is not a CircuitJS circuit`);
      for (const type of wanted.required) assert.ok(count(result.elements, type) > 0, `${wanted.id} lacks ${type}`);
      for (const text of wanted.text || []) assert.ok(infos.some((info) => info.includes(text)), `${wanted.id} lacks ${text}`);
      if (wanted.id === 'led') assert.ok(result.elements.some((element) => element.dump === 162), 'LED dump type is not 162');
      if (wanted.id === 'kontakt') {
        assert.ok(result.elements.some((element) => element.dump === 181), 'lamp dump type is not 181');
        assert.ok(result.switchBefore.includes('open'), 'contact should start open');
        assert.ok(result.switchAfter.includes('closed'), 'contact did not close');
        assert.ok(result.lampAfter.some((info) => /mA/.test(info)), 'closed lamp has no current');
      }
      results.push({ id: wanted.id, name: result.name, elementTypes: types, info: infos });
    }

    const resetResult = await page.evaluate(async () => {
      await window.ElevhubProjectAdapter.reset();
      return {
        signature: window.ElevhubProjectAdapter.signature(),
        hasContent: window.ElevhubProjectAdapter.hasContent(),
        title: document.querySelector('#active-title').textContent
      };
    });
    assert.equal(resetResult.hasContent, false, 'reset must restore the starter circuit');
    assert.match(resetResult.signature, /<cir /, 'reset must leave an exportable circuit');

    assert.deepEqual(external, [], `external requests: ${external.join(', ')}`);
    await page.screenshot({ path: screenshot, fullPage: true });
    console.log(JSON.stringify({ base, examples: results, externalRequests: external, screenshot }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
