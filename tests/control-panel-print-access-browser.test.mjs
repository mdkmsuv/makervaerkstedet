import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {AxeBuilder} from '@axe-core/playwright';
import {chromium} from 'playwright';

const base = 'https://control.example.test';
const web = fileURLToPath(new URL('../system/linux-elevhub/server/elevhub/web/', import.meta.url));
const endpoint = '/api/makerspace/print-access';

test('the control panel opens only the requested print access and handles a failed attempt', async () => {
  const browser = await chromium.launch({channel: 'chrome'});
  try {
    const context = await browser.newContext({viewport: {width: 320, height: 844}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const posts = [];
    const until = Date.now() / 1000 + 3600;
    const id = 'a'.repeat(32);
    const ipv6 = '2001:db8:1111:2222:3333:4444:5555:6666';
    let overviewAvailable = true;
    let blocked = [
      {id, computer: '192.0.2.2', blocked_until: until},
      {id: 'b'.repeat(32), computer: ipv6, blocked_until: until}
    ];
    await page.route('**/*', async route => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.origin !== base) return route.abort();
      const send = json => route.fulfill({json});
      if (url.pathname === '/api/laerer/session') return send({logget_ind: true});
      if (url.pathname === '/api/laerer/oversigt') return overviewAvailable
        ? send({computere: [{navn: 'Værksted 02', ip: '192.0.2.2'}], apps: []})
        : route.fulfill({status: 503, json: {}});
      if (url.pathname === '/api/makerspace/state') {
        const observed_at = Date.now() / 1000;
        return send({
          sources: {printers: 'ok'},
          print_review_url: 'https://print.example.test:8781/laerer',
          inventory: [
            {adapter: 'bambu', source_id: 'orange', name: 'Orange printer', status: 'attention', observed_at},
            {adapter: 'bambu', source_id: 'white', name: 'Hvid printer', status: 'bed', observed_at},
            {adapter: 'bambu', source_id: 'green', name: 'Grøn printer', status: 'preparing', observed_at}
          ]
        });
      }
      if (url.pathname === endpoint) {
        if (req.method() === 'POST') {
          posts.push(req.postDataJSON());
          if (posts.length === 1) return route.fulfill({status: 503, json: {error: 'Midlertidig fejl'}});
          blocked = blocked.filter(row => row.id !== req.postDataJSON().id);
        }
        return send({blocked});
      }
      if (url.pathname.startsWith('/api/')) return send({});
      try {
        await route.fulfill({path: path.join(web, url.pathname === '/kontrolpanel' ? 'kontrolpanel.html' : url.pathname.slice(1))});
      } catch {
        await route.fulfill({status: 404});
      }
    });

    await page.goto(base + '/kontrolpanel#printere');
    const printerStatus = page.locator('#printer-summary');
    await printerStatus.getByText('Orange printer', {exact: true}).waitFor();
    assert.equal(await printerStatus.getByRole('link', {name: 'Kontrollér printeren Orange printer'}).getAttribute('href', {timeout: 1500}),
      'https://print.example.test:8781/laerer#printer-orange');
    assert.equal(await printerStatus.getByRole('link', {name: 'Kontrollér printeren Hvid printer'}).getAttribute('href'),
      'https://print.example.test:8781/laerer#printer-white');
    assert.equal(await printerStatus.getByRole('link', {name: 'Kontrollér printeren Grøn printer'}).count(), 0);
    const section = page.getByRole('region', {name: 'Printblokeringer'});
    const button = section.getByRole('button', {name: 'Ophæv blokering for Værksted 02'});
    await button.waitFor();
    const other = section.getByRole('button', {name: 'Ophæv blokering for ' + ipv6});
    assert.equal(await other.isVisible(), true);
    assert.equal(await section.evaluate(node => node.scrollWidth <= node.clientWidth), true);
    assert.deepEqual((await new AxeBuilder({page}).include('#print-blocks-heading').include('#print-blocks-list').analyze()).violations.map(v => v.id), []);
    await button.press('Enter');
    await section.getByRole('alert').getByText(/Ophævelsen kunne ikke bekræftes/).waitFor();
    assert.equal(await button.evaluate(node => document.activeElement === node), true);
    await page.keyboard.press('Enter');
    await section.getByText('Printadgangen er åbnet for Værksted 02.').waitFor();
    assert.equal(await button.count(), 0);
    assert.equal(await other.isVisible(), true);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'print-blocks-heading');
    assert.deepEqual(posts, [{id}, {id}]);
    overviewAvailable = false;
    await page.reload();
    await other.waitFor();
    assert.equal(await other.isEnabled(), true);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
