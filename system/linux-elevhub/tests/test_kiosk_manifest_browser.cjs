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
        actions: [...document.querySelectorAll('#apps > article')].map(card => {
          const button = card.querySelector('.app-open').getBoundingClientRect();
          const launch = card.querySelector('.launch').getBoundingClientRect();
          const title = card.querySelector('.app-title').getBoundingClientRect();
          return {button:button.toJSON(),launch:launch.toJSON(),title:title.toJSON()};
        })
      }));
      assert(layout.document <= layout.viewport, 'menu must fit the real window viewport at ' + viewport.width);
      assert.equal(layout.actions.length,6,'layout check must exercise every rendered workshop');
      for (const {button,launch,title} of layout.actions) {
        assert(button.width>0 && button.left>=0 && button.right<=layout.viewport,'full card button stays inside viewport');
        for (const target of [launch,title]) assert(target.width>0 && target.left>=button.left && target.right<=button.right && target.top>=button.top && target.bottom<=button.bottom,'title and launch stay inside their full card button');
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
      console.log('kiosk manifest layout: 1004x768 and 1366x900 fit with visible card titles and launch actions: OK');
      return;
    }
    assert.equal(await page.locator('[data-app=microbit] .app-status').innerText(), 'Kræver internet');
    await page.waitForFunction(() => document.title !== 'Makerværkstedet starter');
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
    // Workshops never open from the school server: without a local package the
    // card is not offered on this computer.
    await set('catalog=laser-removed'); await waitCount(3);
    assert.deepEqual(await ids(), ['internet','microbit','scratch'], 'laser without a local package is not offered from the server');
    await set('catalog=installed'); await waitCount(4);
    await set('mode=closed'); await waitCount(1);
    assert.deepEqual(await ids(), ['scratch'], 'online teacher policy closes laser and Internet');
    await set('catalog=broken'); await waitCount(0);
    assert.equal(await page.evaluate(() => window.CreativeHub.isOffline()), false);
    assert.equal(await page.locator('#fejl').innerText(), '', 'catalog error is not a school-offline error');
    assert.match(await page.locator('#catalog-error').innerText(), /kunne ikke læses/);
    // The server answers 403: installed workshops stay, Internet is not offered.
    await set('mode=forbidden&catalog=installed');
    await page.locator('#fejl').filter({hasText:/noget galt med skoleserveren/}).waitFor();
    assert.equal(await page.evaluate(() => window.CreativeHub.isOffline()), true);
    assert.deepEqual(await ids(), ['3dprint','folie','laser','microbit','scratch']);
    if (process.env.KIOSK_REJECTED_SCREENSHOT) {
      const before = page.viewportSize();
      await page.setViewportSize({width:1024,height:688});
      await page.evaluate(() => { location.hash = ''; scrollTo(0, 0); });
      assert.equal(await page.locator('#fejl').evaluate(node => { const box = node.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight; }), true, 'the message is visible without scrolling at 1024x688');
      await page.screenshot({path:process.env.KIOSK_REJECTED_SCREENSHOT});
      await page.setViewportSize(before);
    }
    // Optional school colours apply to actual navigation, launch and focus states.
    const rgb = hex => 'rgb(' + [1,3,5].map(i => parseInt(hex.slice(i, i + 2), 16)).join(', ') + ')';
    const palette = () => page.evaluate(() => {
      const css = selector => getComputedStyle(document.querySelector(selector));
      const action = document.querySelector('[data-app=laser] .app-open');
      return {primaryFocused:document.activeElement === action,
        brand:css('body').getPropertyValue('--brand').trim(), dark:css('body').getPropertyValue('--brand-dark').trim(),
        sidebar:css('.sidebar').backgroundColor, mark:css('.brand-mark').color,
        button:css('[data-app=laser] .launch').backgroundColor, focus:css('[data-app=laser] .app-open').outlineColor,
        focusStyle:css('[data-app=laser] .app-open').outlineStyle, focusWidth:css('[data-app=laser] .app-open').outlineWidth,
        message:css('#kiosk-notice').borderTopColor, selectedFilter:css('[data-filter][aria-pressed=true]').backgroundColor};
    });
    const expectPalette = async (brand, dark, label) => {
      await page.goto(fixture.origin); await waitCount(5);
      await page.locator('#fejl').filter({hasText:/noget galt med skoleserveren/}).waitFor();
      // Keep the pointer away so keyboard focus and hover are checked separately.
      await page.mouse.move(0,0);
      await page.keyboard.press('Shift'); await page.locator('[data-app=laser] .app-open').focus();
      const value = await palette();
      assert.equal(value.primaryFocused, true, label + ': keyboard focus on the actual workshop card');
      assert.deepEqual([value.brand, value.dark], [brand, dark], label + ': custom properties');
      for (const key of ['sidebar','mark','button','focus','message','selectedFilter']) assert.equal(value[key], rgb(brand), label + ': ' + key);
      assert.equal(value.focusStyle,'solid',label+': visible keyboard focus');
      assert(parseFloat(value.focusWidth)>=3,label+': substantial keyboard focus outline');
      assert.equal(await page.locator('[data-app=laser] .availability').innerText(),'Tilgængelig offline',label+': truthful offline status');
      await page.locator('[data-app=laser] .app-open').hover();
      assert.equal(await page.locator('[data-app=laser] .launch').evaluate(node=>getComputedStyle(node).backgroundColor),rgb(dark),label+': dark hover button');
      await page.mouse.move(0,0);
    };
    await expectPalette('#af1724', '#82101b', 'public red theme without school colours');
    await set('colours=invalid');
    await expectPalette('#af1724', '#82101b', 'invalid school colours fall back to the theme');
    await set('colours=custom');
    await expectPalette('#1f5a8c', '#143c5e', 'school colours');
    if (process.env.KIOSK_SKOLEFARVER_SCREENSHOT) {
      const before = page.viewportSize();
      await page.setViewportSize({width:1024,height:688});
      await page.evaluate(() => { location.hash = ''; scrollTo(0, 0); });
      await page.keyboard.press('Shift'); await page.locator('[data-app=laser] .app-open').focus();
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({path:process.env.KIOSK_SKOLEFARVER_SCREENSHOT});
      await page.setViewportSize(before);
    }
    await set('colours=none'); await page.goto(fixture.origin); await waitCount(5);
    console.log('manifest browser: school colours, invalid fallback and public theme passed');
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
