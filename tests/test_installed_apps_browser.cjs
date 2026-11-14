'use strict';
// Actual app bytes, exact package manifests and installed runtime. No legacy editor handlers.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {spawn} = require('node:child_process');
const {createHash} = require('node:crypto');
const root = path.resolve(__dirname, '..');
const canonical = path.resolve(root, '../..');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || path.join(canonical, 'node_modules/playwright'));
const report = {startedAt: new Date().toISOString(), scope: process.env.MKV_INSTALLED_APP_FILTER || 'all-five-installed-apps', flows: [], projectFlows: [], failures: [], errors: [], missing: [], console: [], responses: [], appearance: []};
let child, browser, urls, stderr = '';

async function namedStart(page, title) {
  await page.locator('#project-classroom').fill('QA-6');
  await page.locator('#project-student').fill('Syntetisk elev');
  await page.locator('#project-title').fill(title);
  await page.locator('#project-start-confirm').click();
  await page.waitForFunction(() => !document.querySelector('#elevhub-project-dialog')?.open);
}
async function saved(page, click) {
  const response = page.waitForResponse(r => r.url().includes('/_elevhub/projekter/api/save?') && r.request().method() === 'POST');
  await click(); const reply = await response;
  assert.equal(reply.status(), 200, await reply.text());
  return reply.json();
}
async function exported(page) {
  return page.evaluate(async () => await (await window.ElevhubProjectAdapter.export()).text());
}
async function newContext() {
  const context = await browser.newContext({viewport: {width: 1440, height: 950}});
  await context.route('**/*', async route => {
    const target = new URL(route.request().url());
    // Fulfil only the launcher's fixed policy/control requests with public QA
    // fixture payloads; never contact another WSL service or a school hub.
    if (target.origin === 'http://127.0.0.1:8790' || target.origin === 'https://127.0.0.1:8773') {
      const headers = {'Access-Control-Allow-Origin':'*', 'Access-Control-Allow-Headers':'*', 'Access-Control-Allow-Methods':'GET,POST,OPTIONS', 'Access-Control-Allow-Private-Network':'true'};
      if (route.request().method() === 'OPTIONS') return route.fulfill({status:204, headers});
      const endpoint = target.pathname === '/apps' ? urls.catalogue : target.pathname === '/api/status' ? urls.policy : null;
      const body = endpoint ? await (await context.request.get(endpoint)).text() : JSON.stringify({ok:true});
      return route.fulfill({status:200, contentType:'application/json', headers, body});
    }
    return /^https?:$/.test(target.protocol) && !['localhost', '127.0.0.1'].includes(target.hostname) ? route.abort() : route.continue();
  });
  return context;
}
async function appearance(page, id, isApp = true) {
  const api = isApp ? '/api/school-appearance' : '/_elevhub/projekter/api/config';
  const reply = await page.request.get(new URL(api, page.url()).href); assert.equal(reply.status(),200);
  const value = await reply.json(), profile = isApp ? value : value.appearance;
  assert.equal(profile.school_name, 'Syntetisk QA-skole'); assert.equal(profile.font, 'manrope');
  assert.equal(profile.brand, '#157347');
  await page.getByText('Syntetisk QA-skole', {exact:true}).first().waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('.school-logo,[data-school-logo],#school-logo')].some(img => img.complete && img.naturalWidth === 64 && img.naturalHeight === 48 && img.getBoundingClientRect().width > 0 && img.getBoundingClientRect().height > 0));
  await page.waitForFunction(() => /Manrope|SchoolProfile/.test(getComputedStyle(document.body).fontFamily));
  await page.evaluate(() => document.fonts.ready);
  const state = await page.evaluate(() => ({family:getComputedStyle(document.body).fontFamily,
    fonts:[...document.fonts].map(font=>({family:font.family,status:font.status})),
    logos:[...document.querySelectorAll('.school-logo,[data-school-logo],#school-logo')].filter(img=>img.getBoundingClientRect().width>0).map(img=>({src:img.currentSrc,width:img.naturalWidth,height:img.naturalHeight})),
    resources:performance.getEntriesByType('resource').filter(resource=>/\.ttf(?:$|\?)/.test(resource.name)).map(resource=>resource.name)}));
  assert(state.fonts.some(font => /Manrope|SchoolProfile/.test(font.family) && font.status === 'loaded'), 'Selected school font must actually load');
  const fontURL = state.resources.find(url=>/\/school-profile-assets\/managed-[a-f0-9]{64}\.ttf$/.test(url)) || state.resources.find(url=>/Manrope\.ttf/.test(url)); assert(fontURL, 'Rendered UI must retrieve the bundled Manrope font');
  const bytes = await page.request.get(fontURL); assert.equal(bytes.status(),200);
  const expected = createHash('sha256').update(fs.readFileSync(path.join(root,'linux-elevhub/server/elevhub/web/assets/Manrope.ttf'))).digest('hex');
  assert.equal(createHash('sha256').update(await bytes.body()).digest('hex'),expected, 'Loaded UI font is the pinned native Manrope bytes');
  if (isApp) {
    const logo = await page.request.get(new URL('/school-appearance/logo',page.url()).href); assert.equal(logo.status(),200); assert.match(logo.headers()['content-type'],/^image\/png/);
    assert.equal((await logo.body()).subarray(0,8).toString('hex'),'89504e470d0a1a0a');
  }
  report.appearance.push({surface:id, logicalFont:profile.font, ...state, fontSHA256:expected});
}
function diagnostics(page,id) {
  page.on('pageerror', error => report.errors.push({app: id, error: error.message}));
  page.on('console', message => {if (message.type() === 'error') report.console.push({app:id, error:message.text()});});
  page.on('response', response => {if (response.status() === 404) report.missing.push({app: id, url: response.url()});});
}
async function microbitLayout(page) {
  const layouts = [];
  report.microbitNative={onlineMakeCode:'Blocked; external editor and completed native project session remain untested',layouts};
  for (const viewport of [{width:1440,height:950},{width:390,height:844}]) {
    await page.setViewportSize(viewport);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const state = await page.evaluate(() => {
      const box = selector => {const r=document.querySelector(selector).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
      return {header:box('.school-identity'),frame:box('#editor'),bar:box('#elevhub-project-bar'),logo:box('.school-logo'),
        title:document.querySelector('.school-identity h1').textContent,school:document.querySelector('[data-school-name]').textContent,
        documentWidth:document.documentElement.scrollWidth,modal:document.querySelector('#elevhub-project-dialog').open,
        barFont:getComputedStyle(document.querySelector('#elevhub-project-bar')).fontFamily,
        buttonFont:getComputedStyle(document.querySelector('#elevhub-project-save')).fontFamily,
        headerFont:getComputedStyle(document.querySelector('.school-identity')).fontFamily};
    });
    const first=page.locator('dialog[open] button').first(); await first.focus(); const tab=[];
    for(let index=0;index<7;index++){
      tab.push(await page.evaluate(()=>({id:document.activeElement.id,tag:document.activeElement.tagName,inModal:Boolean(document.activeElement.closest('#elevhub-project-dialog'))})));
      await page.keyboard.press('Tab');
    }
    assert(tab.some(item=>item.id==='project-start-retry'),'Offline startup modal keeps its retry action keyboard accessible');
    layouts.push({viewport,...state,tab});
    if(process.env.MKV_BROWSER_REPORT)await page.screenshot({path:path.join(process.env.MKV_BROWSER_REPORT,`microbit-${viewport.width}-native.png`),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:950});
  for(const state of layouts){
    assert.equal(state.title,'micro:bit'); assert.equal(state.school,'Syntetisk QA-skole');
    assert(state.logo.width>0 && state.logo.height>0 && state.logo.height<=48);
    assert(state.logo.y>=state.header.y && state.logo.bottom<=state.header.bottom,'Whole PNG logo remains inside the native identity header');
    assert.equal(state.bar.y,0,'Native project bar starts at the viewport top without obsolete fixed-bar padding');
    assert(Math.abs(state.header.y-state.bar.bottom)<=1,'Native identity header follows the project bar without overlap or blank padding');
    assert(/Manrope|SchoolProfile/.test(state.barFont),'Native project bar uses the selected school UI font');
    assert(/Manrope|SchoolProfile/.test(state.buttonFont),'Native project buttons use the selected school UI font');
    assert(Math.abs(state.frame.y-state.header.bottom)<=1,'MakeCode frame follows the compact native header without a gap');
    assert(Math.abs(state.frame.bottom-state.viewport.height)<=2,'MakeCode frame consumes the remaining viewport');
    assert(state.frame.height>state.viewport.height/2); assert(state.documentWidth<=state.viewport.width,'Native outer document fits the viewport');
  }
}
async function run(id, flow) {
  if (process.env.MKV_INSTALLED_APP_FILTER && process.env.MKV_INSTALLED_APP_FILTER !== id) return;
  const context = await newContext();
  const page = await context.newPage();
  diagnostics(page,id);
  try {
    const response = await page.goto(urls[id]); assert.equal(response.status(), 200);
    report.responses.push({app:id, url:response.url(), headers:response.headers()});
    await page.waitForFunction(() => window.ElevhubApp && window.ElevhubProjectAdapter && window.ElevhubProjectSession, null, {timeout: 45000});
    assert.equal(await page.evaluate(() => window.ElevhubApp.id), id);
    assert.equal(await page.evaluate(() => window.ElevhubProjectAdapter.type), id, 'Installed metadata and editor project mode must agree');
    await flow(page);
    report.projectFlows.push(id);
    await appearance(page,id);
    if(id==='microbit') await microbitLayout(page);
    if (process.env.MKV_BROWSER_REPORT) await page.screenshot({path: path.join(process.env.MKV_BROWSER_REPORT, id + '-installed.png'), fullPage: true});
    report.flows.push(id); console.log('PASS installed ' + id);
  } catch (error) {
    report.failures.push({app: id, error: error.stack, state: await page.evaluate(() => ({title:document.title, status:document.querySelector('#status')?.textContent, adapter:typeof window.ElevhubProjectAdapter, startup:window.ElevhubProjectStartupError})).catch(() => null)}); console.error('FAIL installed ' + id + ': ' + error.message);
    if (process.env.MKV_BROWSER_REPORT) await page.screenshot({path: path.join(process.env.MKV_BROWSER_REPORT, id + '-failed.png'), fullPage: true}).catch(() => {});
  } finally {await context.close();}
}

async function launcherAndShelf() {
  const context = await newContext();
  for (const [id,url] of [['start',urls.home],['shelf',urls.shelf]]) {
    const page = await context.newPage();
    diagnostics(page,id);
    try {
      assert.equal((await page.goto(url)).status(),200);
      await appearance(page,id,false);
      if (id === 'start') {
        await page.waitForFunction(() => document.querySelectorAll('[data-app]').length >= 5);
        assert.equal(await page.locator('[data-app]').count(),5);
      } else {assert.equal(await page.locator('article.project').count(),4, 'All four locally saved app projects appear on the real shelf');}
      report.flows.push(id); console.log('PASS profile ' + id);
      if (process.env.MKV_BROWSER_REPORT) await page.screenshot({path:path.join(process.env.MKV_BROWSER_REPORT,id+'-profile.png'),fullPage:true});
    } catch(error) {
      report.failures.push({app:id,error:error.stack}); console.error('FAIL profile '+id+': '+error.message);
      if (process.env.MKV_BROWSER_REPORT) await page.screenshot({path:path.join(process.env.MKV_BROWSER_REPORT,id+'-failed.png'),fullPage:true}).catch(()=>{});
    }
    await page.close();
  }
  await context.close();
}

async function main() {
  if (process.env.MKV_BROWSER_REPORT) fs.mkdirSync(process.env.MKV_BROWSER_REPORT, {recursive: true});
  child = process.platform === 'win32' ? spawn('wsl.exe', ['-u', 'root', '--cd', root, '--', 'env', ...(process.env.MKV_INSTALLED_APP_FILTER ? ['MKV_INSTALLED_APP_FILTER='+process.env.MKV_INSTALLED_APP_FILTER] : []), 'python3', '-B', 'tests/fixtures/installed_app_server.py'], {stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true}) : spawn('python3', ['-B', 'tests/fixtures/installed_app_server.py'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
  child.stderr.on('data', data => stderr += data);
  urls = await new Promise((resolve, reject) => {
    let buffer = ''; const timer = setTimeout(() => reject(Error('Installed fixture startup timeout: ' + stderr)), 240000);
    child.stdout.on('data', data => {buffer += data; for (const line of buffer.split('\n')) {try {const value = JSON.parse(line); if (value.shelf) {clearTimeout(timer); resolve(value);}} catch {}}});
    child.once('exit', code => {clearTimeout(timer); reject(Error('Installed fixture exited ' + code + ': ' + stderr));});
  });
  browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || (process.platform === 'win32' ? 'chrome' : undefined)});
  report.browser = browser.version();
  await run('laser', async page => {
    await namedStart(page, 'Installeret laser');
    await page.locator('#newProjectButton').click(); await page.locator('#confirmNewProjectButton').click();
    await page.locator('[data-add-type="rect"]').click();
    const expected = JSON.parse(await exported(page)); assert.equal(expected.project.objects.length, 1);
    const meta = await saved(page, () => page.locator('#saveProjectButton').click());
    await page.evaluate(() => window.ElevhubProjectSession.reset());
    assert.notDeepEqual(JSON.parse(await exported(page)).project.objects, expected.project.objects, 'Reset removes the previous pupil drawing and restores the starter');
    await page.goto(urls.laser + '?elevhub_project=' + meta.id);
    await page.waitForFunction(() => document.querySelector('#objectLayer')?.childElementCount === 1);
    const reopened = JSON.parse(await exported(page));
    const geometry = object => ({...object, rounded: Boolean(object.rounded)});
    assert.deepEqual(reopened.project.objects.map(geometry), expected.project.objects.map(geometry));
  });
  await run('folie', async page => {
    await namedStart(page, 'Installeret folie');
    await page.waitForFunction(() => document.documentElement.style.getPropertyValue('--school-brand') === '#157347');
    await page.locator('#library-button').click();
    await page.getByRole('button', {name: 'Tilføj skæremotiv', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#library-status')?.textContent.includes('er tilføjet'));
    await page.getByRole('button', {name: 'Tegn videre', exact: true}).click();
    const expected = await exported(page); assert.match(expected, /<svg/);
    const meta = await saved(page, () => page.locator('#save-project').click());
    await page.evaluate(() => window.ElevhubProjectSession.reset());
    assert.equal(await page.locator('#items > g').count(), 0);
    await page.goto(urls.folie + '?elevhub_project=' + meta.id);
    await page.waitForFunction(() => document.querySelector('#items')?.childElementCount > 0);
    assert.match(await exported(page), /<path/);
  });
  await run('3dprint', async page => {
    await namedStart(page, 'Installeret 3D');
    await page.locator('[data-add=box]:not([data-mode])').click();
    await page.waitForFunction(() => document.querySelector('#object-list')?.children.length === 1);
    const expected = JSON.parse(await exported(page)); assert.equal(expected.format, 'makervaerkstedet-3d-project');
    const meta = await saved(page, () => page.locator('#elevhub-project-save').click());
    await page.evaluate(() => window.ElevhubProjectSession.reset());
    assert.equal(await page.locator('#object-list button').count(), 0);
    await page.goto(urls['3dprint'] + '?elevhub_project=' + meta.id);
    await page.waitForFunction(() => document.querySelector('#object-list')?.children.length === 1);
    assert.deepEqual(JSON.parse(await exported(page)).scene, expected.scene);
  });
  await run('scratch', async page => {
    await page.waitForFunction(() => document.querySelector('#creative-app-header [data-school-name]')?.textContent === 'Syntetisk QA-skole');
    await namedStart(page, 'Installeret Scratch');
    const sprite = page.getByPlaceholder('Navn', {exact: true}); await sprite.fill('Installeret kat'); await sprite.press('Enter');
    await page.waitForFunction(() => window.ElevhubProjectAdapter.signature().includes('Installeret kat'));
    const meta = await saved(page, () => page.locator('#elevhub-project-save').click());
    const file = await page.request.get(urls.scratch + '_elevhub/projekter/api/file?id=' + meta.id);
    const bytes = await file.body(); assert.equal(bytes.subarray(0, 2).toString(), 'PK');
    // Python stdlib verifies ZIP contents; no new browser/test dependencies.
    const zip = process.platform === 'win32' ? spawn('wsl.exe', ['-u','root','--','python3','-c','import io,json,sys,zipfile; z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())); p=json.loads(z.read("project.json")); assert any(t["name"]=="Installeret kat" for t in p["targets"]); assert any(n.endswith(".svg") for n in z.namelist()); assert any(n.endswith(".wav") for n in z.namelist())'], {stdio:['pipe','ignore','pipe'],windowsHide:true}) : spawn('python3',['-c','import io,json,sys,zipfile; z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())); p=json.loads(z.read("project.json")); assert any(t["name"]=="Installeret kat" for t in p["targets"]); assert any(n.endswith(".svg") for n in z.namelist()); assert any(n.endswith(".wav") for n in z.namelist())'],{stdio:['pipe','ignore','pipe']});
    let zipError='';zip.stderr.on('data', d=>zipError+=d); const checked=new Promise(resolve=>zip.once('exit', resolve)); zip.stdin.end(bytes); assert.equal(await checked,0,zipError);
    await page.goto(urls.scratch + '?elevhub_project=' + meta.id);
    await page.waitForFunction(() => window.ElevhubProjectAdapter?.signature().includes('Installeret kat'));
  });
  await run('microbit', async page => {
    assert.match(await page.locator('#editor').getAttribute('src'), /^https:\/\/makecode.microbit.org/);
    const result = await page.evaluate(async () => {try {await window.ElevhubProjectAdapter.prepareSave(); return '';} catch (error) {return error.message;}});
    assert.match(result, /kræver internet/);
    const response = await page.request.get(urls.microbit + '_elevhub/projekter/api/list');
    assert(!(await response.json()).projects.some(p => p.kind === 'microbit'), 'Offline shell must not fabricate a project');
  });
  if (!process.env.MKV_INSTALLED_APP_FILTER) await launcherAndShelf();
  assert.deepEqual(report.missing, [], 'All installed package assets must exist');
  assert.deepEqual(report.errors, [], 'No unhandled app errors');
  assert.deepEqual(report.failures, [], 'Every installed app flow must pass');
}
main().then(() => {report.status = 'passed';}).catch(error => {report.status = 'failed'; report.failure = error.stack; console.error(error); process.exitCode = 1;}).finally(async () => {
  await browser?.close();
  if (child) {
    const exited = child.exitCode === null ? new Promise(resolve => child.once('exit', resolve)) : Promise.resolve();
    if (urls) await fetch(urls.cleanup, {method: 'POST'}).catch(error => {report.cleanupError = error.message; child.kill();}); else child.kill();
    const timer = setTimeout(() => child.kill(), 15000); await exited; clearTimeout(timer);
  }
  report.finishedAt = new Date().toISOString();
  if (process.env.MKV_BROWSER_REPORT) fs.writeFileSync(path.join(process.env.MKV_BROWSER_REPORT, 'installed-apps.json'), JSON.stringify(report, null, 2) + '\n');
});
