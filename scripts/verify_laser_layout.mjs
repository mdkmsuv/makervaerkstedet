import {createServer} from 'node:http';
import {readFile, mkdir, mkdtemp, rm, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {chromium} = require('playwright');
const root = process.cwd();
await mkdir(path.join(root, '.runtime'), {recursive:true});
const server = createServer(async (request, response) => {
  const url = new URL(request.url || '/', 'http://localhost');
  const route = url.pathname === '/folie.html' ? '/laser.html' : url.pathname;
  if (url.pathname === '/api/laser/library/status') {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({enabled: true, version: 1}));
    return;
  }
  if (url.pathname === '/api/laser/library') {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({version: 1, items: []}));
    return;
  }
  const webRoot = path.join(root, 'web') + path.sep;
  const file = path.resolve(root, 'web', `.${route}`);
  if (!file.startsWith(webRoot)) { response.writeHead(403).end(); return; }
  try {
    response.setHeader('Content-Type', file.endsWith('.html') ? 'text/html' : file.endsWith('.js') ? 'text/javascript' : 'text/css');
    response.end(await readFile(file));
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const profile = await mkdtemp(path.join(root, '.runtime', 'laser-layout-review-'));
const browser = await chromium.launchPersistentContext(profile, {headless:true, channel:'chrome'});
const page = browser.pages()[0] || await browser.newPage();
page.setDefaultTimeout(12000);
const checks = [];
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const pass = (name, value) => { if (!value) throw Error(name); checks.push(name); console.log(`PASS ${name}`); };
const rect = selector => page.locator(selector).boundingBox();
const base = `http://127.0.0.1:${server.address().port}`;
try {
  await page.goto(`${base}/laser.html`);
  await page.waitForFunction(() => window.ElevhubProjectAdapter && document.querySelector('#save-status')?.textContent === 'Kladde på denne computer');
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.waitForFunction(() => window.ElevhubProjectAdapter && document.querySelector('#save-status')?.textContent === 'Kladde på denne computer');

  const newDrawing = page.locator('#new-button');
  pass('Ny tegning has one direct 20x20 SVG icon', await newDrawing.evaluate(button => {
    const icons = [...button.querySelectorAll(':scope > svg.laser-action-icon')];
    return icons.length === 1 && icons[0].getAttribute('width') === '20' && icons[0].getAttribute('height') === '20';
  }));
  pass('Ny tegning has no nested or legacy icon', await newDrawing.evaluate(button => {
    const icons = button.querySelectorAll('svg');
    return icons.length === 1 && !button.querySelector(':scope > span[aria-hidden="true"]');
  }));
  for (const id of ['#export-button', '#send-button', '#help-button']) {
    pass(`${id} is directly visible without a More menu`, await page.locator(id).evaluate(button => {
      const actions = button.closest('.project-actions');
      return !!actions && button.parentElement === actions && !button.closest('details') && !button.hidden && !!(button.offsetWidth || button.offsetHeight || button.getClientRects().length);
    }));
  }
  pass('there is no More details menu', await page.locator('.laser-file-menu').count() === 0 && await page.locator('.project-actions details').count() === 0);

  for (const viewport of [{width:1366,height:768},{width:1024,height:768}]) {
    await page.setViewportSize(viewport);
    const editor = await rect('.editor');
    const canvas = await rect('.drawing-panel');
    const inspector = await rect('.laser-inspector');
    pass(`desktop ${viewport.width}: canvas has at least 180px height`, canvas?.height >= 180);
    pass(`desktop ${viewport.width}: inspector sits beside canvas`, editor && inspector && inspector.x + inspector.width <= editor.x + editor.width + 1);
  }

  for (const width of [1920, 1366, 1024, 900, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 768});
    pass(`${width}: page has no horizontal overflow`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1 && document.body.scrollWidth <= innerWidth + 1));
    for (const id of ['#export-button', '#send-button', '#help-button']) {
      pass(`${width}: ${id} stays directly visible`, await page.locator(id).evaluate(button => {
        const actions = button.closest('.project-actions');
        const box = button.getBoundingClientRect();
        return !!actions && button.parentElement === actions && !button.closest('details') && !button.hidden && box.width > 0 && box.height > 0;
      }));
    }
    if (width === 1920 || width === 390) {
      pass(`${width}: Ny tegning label has natural width beside icon`, await page.locator('#new-button').evaluate(button => {
        const icon = button.querySelector(':scope > svg.laser-action-icon');
        const label = button.querySelector(':scope > .laser-action-label');
        if (!icon || !label) return false;
        const iconBox = icon.getBoundingClientRect();
        const labelBox = label.getBoundingClientRect();
        const style = getComputedStyle(label);
        const buttonBox = button.getBoundingClientRect();
        return labelBox.width > 30 && labelBox.height > 0 && style.borderWidth === '0px' && style.borderRadius === '0px' && buttonBox.width >= iconBox.width + labelBox.width + 20;
      }));
    }
  }
  await page.setViewportSize({width:1024,height:768});

  await page.locator('#laser-projects-button').click();
  const dialog = await rect('#laser-project-dialog');
  const cards = await rect('#laser-project-cards');
  pass('project dialog search and cards region are present', !!dialog && !!cards);
  pass('project cards region is independently scrollable', await page.locator('#laser-project-cards').evaluate(node => node.scrollHeight >= node.clientHeight));
  await page.keyboard.press('Escape');

  const select = page.locator('.canvas-toolbar .tool[data-tool="select"]');
  pass('select tool is visible in canvas toolbar on build tab', await select.isVisible());
  await page.locator('#laser-tool-tab-draw').click();
  pass('select tool remains reachable after switching to draw tab', await select.isVisible());
  await page.locator('#laser-tool-tab-build').click();
  await page.waitForFunction(() => !document.querySelector('#library-button')?.hidden);
  pass('SVG library action is reachable in build tools', await page.locator('#library-button').isVisible());
  await page.locator('#laser-tool-tab-draw').click();
  pass('SVG library action is hidden in draw tools', !(await page.locator('#library-button').isVisible()));
  await page.locator('#laser-tool-tab-build').click();
  pass('SVG library action returns in build tools', await page.locator('#library-button').isVisible());
  await page.locator('#library-button').click();
  await page.waitForFunction(() => document.querySelector('#library-dialog')?.open === true);
  pass('SVG library dialog opens from build tools', await page.locator('#library-dialog').isVisible());
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:1024,height:768});
  for (const id of ['#import-button','#save-project','#preview-button']) pass(`1024 top action ${id} remains visible`, await page.locator(id).isVisible());
  await page.setViewportSize({width:390,height:844});
  await page.locator('#laser-tool-tab-build').click();
  pass('mobile Saml tools are hidden outside Saml tab', !(await page.locator('.combine-tools').isVisible()));
  await page.locator('#laser-tool-tab-more').click();
  pass('mobile Saml tools appear in Saml tab', await page.locator('.combine-tools').isVisible());
  await page.locator('#laser-tool-tab-draw').click();
  pass('mobile Saml tools hide again after leaving Saml tab', !(await page.locator('.combine-tools').isVisible()));
  await page.setViewportSize({width:1366,height:768});
  pass('tool tabs fit inside their panel', await page.locator('.laser-tool-tabs').evaluate(node => {
    const r = node.getBoundingClientRect();
    return r.left >= 0 && r.right <= innerWidth + 1;
  }));

  await page.setViewportSize({width:1280,height:600});
  for (const id of ['#undo','#redo','#zoom-reset']) pass(`1280x600 canvas toolbar ${id} remains visible`, await page.locator(id).isVisible());
  await page.locator('#laser-projects-button').click();
  await page.locator('#laser-project-dialog [data-guide-id="nameplate"] button').click();
  await page.waitForFunction(() => !document.querySelector('#laser-guide-panel')?.hidden);
  const guide = await rect('#laser-guide-panel');
  const nav = await rect('.laser-guide-navigation');
  const canvasWithGuide = await rect('.drawing-panel');
  pass('1280x600 guide body can scroll its content', await page.locator('.laser-guide-body').evaluate(node => node.scrollHeight >= node.clientHeight));
  pass('desktop guide keeps canvas at least 180px high', canvasWithGuide?.height >= 180);
  pass('guide navigation stays inside guide panel', guide && nav && nav.y + nav.height <= guide.y + guide.height + 1);
  pass('native progress element has a visible value', await page.locator('#laser-guide-progress').evaluate(node => node.tagName === 'PROGRESS' && node.value > 0));

  for (const width of [900, 850]) {
    await page.setViewportSize({width, height:768});
    pass(`${width}: drawing controls stay within the canvas`, await page.locator('.canvas-toolbar').evaluate(toolbar => {
      const bounds = toolbar.getBoundingClientRect();
      return [...toolbar.querySelectorAll('button')].every(control => {
        const rect = control.getBoundingClientRect();
        return rect.left >= bounds.left && rect.right <= bounds.right + 1 && rect.bottom <= bounds.bottom + 1;
      });
    }));
  }

  await page.setViewportSize({width:390,height:844});
  const mobileGuide = await rect('#laser-guide-panel');
  pass('mobile guide has bounded scroll panel', mobileGuide?.height <= 281);
  pass('mobile page has no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  pass('mobile guide navigation remains in panel', await page.locator('.laser-guide-navigation').evaluate(node => {const p=node.closest('.laser-guide'); const a=node.getBoundingClientRect(), b=p.getBoundingClientRect(); return a.bottom <= b.bottom + 1;}));
  await page.locator('#laser-guide-close').click();
  pass('mobile inspector remains reachable after guide closes', await page.locator('.laser-inspector').evaluate(node => node.getBoundingClientRect().height > 0));
  if (errors.length) throw Error(`browser errors: ${errors.join('; ')}`);
  const result = {checks: checks.length, names: checks, errors};
  await writeFile(path.join(root, '.runtime', 'laser-layout-checks.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
  const profilePath = path.resolve(profile), runtimeRoot = path.resolve(root, '.runtime') + path.sep;
  if (!profilePath.startsWith(runtimeRoot) || !path.basename(profilePath).startsWith('laser-layout-review-')) throw Error('Unexpected test profile path');
  await rm(profilePath, {recursive:true, force:true});
}


