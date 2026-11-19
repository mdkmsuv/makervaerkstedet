import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {AxeBuilder} from '@axe-core/playwright';
import {chromium} from 'playwright';

const control = 'https://control.example.test';
const teacher = 'https://print.example.test:8781';
const root = fileURLToPath(new URL('../system/', import.meta.url));

test('a blocked plate links to the right teacher controls after login', async () => {
  const browser = await chromium.launch({channel: 'chrome'});
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    let session = false;
    let stopped = false;
    let cleared = false;
    let reviewConfigured = true;
    const writes = [];
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));

    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (![control, teacher].includes(url.origin)) return route.abort();
      const send = json => route.fulfill({json});
      if (request.method() === 'POST') {
        writes.push([url.pathname, request.postDataJSON()]);
        assert.equal(url.origin, teacher);
        if (url.pathname === '/api/auth/login') {
          if (request.postDataJSON().code !== 'syntetisk-kode') {
            return route.fulfill({status: 403, json: {error: 'Forkert lærerkode.'}});
          }
          session = true;
        }
        else {
          assert.equal(session, true);
          if (url.pathname === '/api/jobs/resolve') stopped = true;
          else if (url.pathname === '/api/printers/release') {
            assert.equal(stopped, true);
            cleared = true;
          } else assert.fail(url.pathname);
        }
        return send({ok: true});
      }
      if (url.pathname === '/api/printplader') {
        return send({
          ...(reviewConfigured && {review_url: teacher + '/laerer'}),
          plates: [{id: 'white', name: 'Hvid printer', status: 'waiting', token: 'a'.repeat(32)},
            {id: 'orange', name: 'Orange printer', status: 'blocked', token: ''}]
        });
      }
      if (url.pathname === '/api/auth/status') return send({teacher: session});
      if (url.pathname === '/api/state') {
        return send({
          colours: [], paused: false, demo: true,
          printers: [{id: 'white', name: 'Hvid printer', model: 'A1 mini', status: 'bed', colours: [], can_release: true},
            {id: 'orange', name: 'Orange printer', model: 'A1 mini', status: stopped ? 'bed' : 'attention',
              colours: [], machine_state: 'FAILED', can_release: stopped && !cleared, bed_clear: cleared}],
          jobs: session && !stopped ? [{id: 'stopped-job', printer_id: 'orange', status: 'attention',
            filename: 'Testmodel.stl', resolution_options: ['cancelled', 'failed']}] : []
        });
      }
      if (url.pathname.startsWith('/api/')) return send({});
      const base = url.origin === control ? 'linux-elevhub/server/elevhub/web' : 'web';
      const file = url.pathname === '/ryd-plade' ? 'print-plates.html' : url.pathname === '/laerer'
        ? 'index.html' : url.pathname.slice(1);
      try {
        await route.fulfill({path: path.join(root, base, file)});
      } catch {
        await route.fulfill({status: 404});
      }
    });

    await page.goto(control + '/ryd-plade');
    const whitePlate = page.getByRole('article').filter({hasText: 'Hvid printer'});
    const orangePlate = page.getByRole('article').filter({hasText: 'Orange printer'});
    const review = orangePlate.getByRole('link', {name: 'Kontrollér printeren Orange printer'});
    await review.waitFor();
    assert.equal(await whitePlate.getByRole('link').count(), 0);
    assert.equal(await whitePlate.getByRole('button', {name: 'Jeg har ryddet pladen på Hvid printer'}).isVisible(), true);
    assert.equal(await orangePlate.getByRole('button').count(), 0);
    assert.deepEqual((await new AxeBuilder({page}).analyze()).violations.map(({id}) => id), []);
    await review.focus();
    await page.keyboard.press('Enter');
    await page.waitForURL(teacher + '/laerer#printer-orange');
    assert.equal(await page.locator('#teacher-content').isVisible(), false);
    await page.locator('#admin-token').fill('forkert-kode');
    await page.locator('#login-form button').press('Enter');
    await page.getByText('Forkert lærerkode.', {exact: true}).waitFor();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'admin-token');
    await page.locator('#admin-token').fill('syntetisk-kode');
    await page.locator('#login-form button').click();

    const card = page.locator('#printer-orange');
    await page.waitForFunction(() => document.activeElement === document.querySelector('#printer-orange h2'));
    assert.equal(await card.getByRole('button', {name: 'Pladen er ryddet'}).isDisabled(), true);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.textContent), 'Printet er stoppet');
    await page.keyboard.press('Enter');
    const release = card.getByRole('button', {name: 'Pladen er ryddet'});
    await page.waitForFunction(() => document.activeElement?.getAttribute('data-release') === 'orange');
    await release.click();
    await card.getByRole('button', {name: 'Pladen er frigivet'}).waitFor();
    assert.deepEqual(writes.map(([url, body]) => [url, body.id]), [
      ['/api/auth/login', undefined], ['/api/auth/login', undefined],
      ['/api/jobs/resolve', 'stopped-job'], ['/api/printers/release', 'orange']
    ]);
    reviewConfigured = false;
    await page.goto(control + '/ryd-plade');
    await orangePlate.getByText('Bed en lærer kontrollere printeren i Printværkstedets læreroversigt.').waitFor();
    assert.equal(await review.count(), 0);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
