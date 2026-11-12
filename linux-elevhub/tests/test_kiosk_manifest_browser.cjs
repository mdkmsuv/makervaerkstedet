// Render the real kiosk template against synthetic hub/catalog services, never a school host.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const {chromium} = require('playwright');
const fixtureScript = path.join(__dirname, 'kiosk_browser_fixture.py');
async function startFixture() {
  const windows = process.platform === 'win32';
  const linuxPath = windows ? '/mnt/' + fixtureScript[0].toLowerCase() + fixtureScript.slice(2).replaceAll('\\', '/') : fixtureScript;
  const command = process.env.ELEVHUB_TEST_PYTHON || (windows ? 'wsl' : 'python3');
  const args = windows && !process.env.ELEVHUB_TEST_PYTHON ? ['python3', '-B', linuxPath] : ['-B', fixtureScript];
  const child = spawn(command, args, {windowsHide:true});
  let stderr = ''; child.stderr.on('data', chunk => stderr += chunk);
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {child.kill(); reject(Error('Fixture startup timeout: ' + stderr));}, 15000);
    let text = ''; child.stdout.on('data', chunk => {
      text += chunk; const line = text.split(/\r?\n/).find(value => /^\d+$/.test(value));
      if (line) {clearTimeout(timer); resolve(Number(line));}
    });
    child.once('exit', code => {clearTimeout(timer); reject(Error('Fixture exited ' + code + ': ' + stderr));});
  });
  return {child, origin:'http://127.0.0.1:' + port};
}
(async () => {
  const fixture = await startFixture();
  let browser;
  try {
    browser = await chromium.launch({headless:true, channel:'chrome'});
    const context = await browser.newContext({viewport:{width:1366,height:900}});
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let hubRequests = 0;
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname === 'school.example.test' && url.pathname === '/api/status') {
        hubRequests++; return route.fulfill({response:await route.fetch({url:fixture.origin + '/api/status'})});
      }
      if (url.hostname === 'school.example.test' && url.port === '8771') {
        return route.fulfill({contentType:'text/html',body:'<h1>Synthetic server laser destination</h1>'});
      }
      if (url.origin === 'http://127.0.0.1:8790') {
        assert.equal(route.request().headers().authorization, 'Bearer fixture-token');
        assert.equal(route.request().headers()['x-elevhub-computer'], 'browser-fixture');
        if (url.pathname === '/apps') return route.fulfill({response:await route.fetch({url:fixture.origin + '/apps'})});
        if (url.pathname === '/finish') return route.fulfill({json:{id:'a'.repeat(32),state:'completed'}});
      }
      if (url.origin === 'http://127.0.0.1:8798') return route.fulfill({contentType:'text/html',body:'<h1>Synthetic local 3D destination</h1>'});
      if (url.origin === 'https://internet.example.test') return route.fulfill({contentType:'text/html',body:'<h1>Synthetic Internet destination</h1>'});
      if (url.origin === fixture.origin) return route.continue();
      throw Error('Unexpected external contact: ' + url.href);
    });
    const set = query => page.request.get(fixture.origin + '/__set?' + query);
    const ids = () => page.locator('#apps [data-app]').evaluateAll(nodes => nodes.map(node => node.dataset.app).sort());
    const waitCount = n => page.waitForFunction(n => document.querySelectorAll('#apps [data-app]').length === n, n);
    await page.goto(fixture.origin);
    await waitCount(6);
    console.log('manifest browser: offline catalogue ready');
    assert.deepEqual(await ids(), ['3dprint','folie','internet','laser','microbit','scratch']);
    for (const viewport of [{width:1004,height:768}, {width:1366,height:900}]) {
      await page.setViewportSize(viewport);
      const layout = await page.evaluate(() => ({
        viewport: innerWidth, document: document.documentElement.scrollWidth,
        actions: [...document.querySelectorAll('.app-actions')].map(actions => {
          const launch = actions.querySelector('.app-open').getBoundingClientRect();
          const about = actions.querySelector('.app-about').getBoundingClientRect();
          return {launchRight:launch.right, aboutLeft:about.left, right:about.right};
        })
      }));
      assert(layout.document <= layout.viewport, 'menu must fit the real window viewport at ' + viewport.width);
      for (const actions of layout.actions) {
        assert(actions.launchRight <= actions.aboutLeft, 'primary action must not overlap the ideas link');
        assert(actions.right <= layout.viewport, 'rightmost action must remain inside the viewport');
      }
      if (process.env.KIOSK_MANIFEST_SCREENSHOT) {
        await page.locator('#workshops-title').evaluate(title => {
          document.documentElement.style.scrollBehavior = 'auto';
          window.scrollTo(0, title.getBoundingClientRect().top + scrollY - 20);
        });
        await page.screenshot({path:process.env.KIOSK_MANIFEST_SCREENSHOT.replace(/\.png$/, '-'+viewport.width+'.png')});
      }
    }
    if (process.env.KIOSK_MANIFEST_LAYOUT_ONLY === '1') {
      assert.deepEqual(errors, []);
      console.log('kiosk manifest layout: 1004x768 and 1366x900 fit without action overlap: OK');
      return;
    }
    assert.equal(await page.locator('[data-app=microbit] .app-status').innerText(), 'Kræver internet');
    await page.waitForFunction(() => document.title !== 'Værkstedet starter');
    await page.locator('[data-app="3dprint"] .app-open').focus();
    const beforeLaunch = hubRequests;
    await page.keyboard.press('Enter');
    await page.waitForURL('http://127.0.0.1:8798/model3d.html');
    assert.equal(hubRequests, beforeLaunch, 'offline launch does not contact the school');
    await page.goto(fixture.origin); await waitCount(6);
    await page.locator('[data-app=scratch] .app-open').focus();
    await set('catalog=removed');
    await waitCount(5);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'workshops-title');
    await set('catalog=installed'); await waitCount(6);
    await page.locator('[data-app=scratch] .app-about').click();
    await page.locator('#detail-start').focus();
    await set('catalog=removed'); await waitCount(5);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'workshops-title', 'removal also exits focused workshop details');
    await set('catalog=installed'); await waitCount(6);
    console.log('manifest browser: offline removal/reinstall and focus passed');
    await set('catalog=empty'); await waitCount(1);
    assert.deepEqual(await ids(), ['internet']);
    assert.match(await page.locator('#catalog-status').innerText(), /ingen lokale værksteder installeret/);
    await set('catalog=broken');
    await page.locator('#catalog-error').filter({hasText:/kunne ikke læses/}).waitFor();
    assert.deepEqual(await ids(), ['internet']);
    assert.equal(await page.locator('[data-app=internet] .app-open').isEnabled(), true);
    await page.locator('[data-app=internet] .app-open').focus();
    const popupReady = page.waitForEvent('popup');
    await page.keyboard.press('Enter');
    const popup = await popupReady;
    await popup.waitForURL('https://internet.example.test/');
    await popup.close();
    console.log('manifest browser: catalogue error keeps working Internet popup');
    await set('mode=open&catalog=installed'); await waitCount(4);
    assert.deepEqual(await ids(), ['internet','laser','microbit','scratch']);
    await set('catalog=microbit-removed'); await waitCount(3);
    assert.deepEqual(await ids(), ['internet','laser','scratch'], 'local micro:bit web wrapper requires an installed manifest');
    await set('catalog=installed'); await waitCount(4);
    await set('catalog=removed'); await waitCount(3);
    assert.deepEqual(await ids(), ['internet','laser','microbit']);
    await set('catalog=installed'); await waitCount(4);
    // A server-hosted workshop without a local package must stay visible (it opens on the server).
    // First drop Scratch so the next count change proves the catalogue was re-read.
    await set('catalog=removed'); await waitCount(3);
    await set('catalog=laser-removed'); await waitCount(4);
    assert.deepEqual(await ids(), ['internet','laser','microbit','scratch'], 'server-hosted laser stays visible without a local package');
    await page.getByRole('link', {name:'Værksteder', exact:true}).click();
    await page.locator('[data-app=laser] .app-open').waitFor({state:'visible'});
    await page.locator('[data-app=laser] .app-open').focus();
    await page.keyboard.press('Enter');
    await page.waitForURL('http://school.example.test:8771/');
    assert.equal(await page.locator('h1').innerText(), 'Synthetic server laser destination');
    await page.goto(fixture.origin); await waitCount(4);
    await set('catalog=installed'); await waitCount(4);
    await set('mode=closed'); await waitCount(1);
    assert.deepEqual(await ids(), ['scratch'], 'online teacher policy closes laser and Internet');
    await set('catalog=broken'); await waitCount(0);
    assert.equal(await page.evaluate(() => window.CreativeHub.isOffline()), false);
    assert.equal(await page.locator('#fejl').innerText(), '', 'catalog error is not a school-offline error');
    assert.match(await page.locator('#catalog-error').innerText(), /kunne ikke læses/);
    await set('mode=forbidden&catalog=installed');
    await page.locator('#fejl').filter({hasText:/bekræftes/}).waitFor();
    assert.equal(await page.evaluate(() => window.CreativeHub.isOffline()), false);
    assert.deepEqual(await ids(), []);
    await set('mode=down&catalog=broken'); await waitCount(1);
    // Optional specialist review; default regression needs only the locked Playwright dependency.
    if (process.env.KIOSK_MANIFEST_AXE === '1') {
      const AxeBuilder = require('@axe-core/playwright').default;
      const axe = await new AxeBuilder({page}).analyze();
      console.log('AXE catalog failure:', JSON.stringify(axe.violations.map(v => ({id:v.id,targets:v.nodes.map(n=>n.target)}))));
    }
    if (process.env.KIOSK_MANIFEST_SCREENSHOT) {
      await page.locator('#catalog-error').scrollIntoViewIfNeeded();
      await page.screenshot({path:process.env.KIOSK_MANIFEST_SCREENSHOT});
    }
    assert.deepEqual(errors, []);
    console.log('kiosk installed manifest browser flow: offline, manifest start, removal/reinstall, online policy, empty/error, focus: OK');
  } finally {
    await browser?.close();
    await fetch(fixture.origin + '/__stop', {signal:AbortSignal.timeout(3000)}).catch(() => {});
    fixture.child.kill();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
