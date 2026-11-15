import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';

const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1024, height: 688 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://127.0.0.1:8892/');
  const click = key => page.locator(`button[data-key="${key}"]`).click();
  for (const key of ['1', ',', '5', '+', '2', '=']) await click(key);
  assert.equal(await page.locator('#display').innerText(), '3,5');
  for (const key of ['clear', '8', '/', '0', '=']) await click(key);
  assert.match(await page.locator('#feedback').innerText(), /dividere med 0/);
  const errorAxe = await new AxeBuilder({ page }).analyze();
  assert.deepEqual(errorAxe.violations.map(v => v.id), []);
  await click('2');
  await click('=');
  assert.equal(await page.locator('#display').innerText(), '4');
  await page.locator('body').click({position:{x:10,y:10}});
  await page.keyboard.press('Escape');
  await page.keyboard.type('0,1+0.2');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#display').innerText(), '0,3');
  const normalAxe = await new AxeBuilder({ page }).analyze();
  assert.deepEqual(normalAxe.violations.map(v => v.id), []);
  assert.deepEqual(errors, []);
  const report = {
    flows: ['click decimal sum', 'division by zero', 'correct denominator', 'keyboard decimal sum'],
    axe: {normal: normalAxe.violations.length, divisionByZero: errorAxe.violations.length,
      incomplete: [...new Set([...normalAxe.incomplete, ...errorAxe.incomplete].map(v=>v.id))]},
    browserErrors: errors,
  };
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
