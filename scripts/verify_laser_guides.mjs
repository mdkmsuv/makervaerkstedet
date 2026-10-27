// Isolated headless checks for the laser-only project guide flow.
// Uses a private static server; never connects to the current workshop server,
// a VM, a physical machine, or a visible browser.
import { createServer } from 'node:http';
import { readFile, mkdir, rm, mkdtemp, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = process.cwd();
const checks = [];
const errors = [];
const assert = (value, message) => { if (!value) throw Error(message); };
const mime = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.svg': 'image/svg+xml', '.ttf': 'font/ttf', '.webp': 'image/webp',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'
};

await mkdir(path.join(root, '.runtime'), { recursive: true });
const server = createServer(async (request, response) => {
  const url = new URL(request.url || '/', 'http://localhost');
  if (url.pathname === '/api/laser/health') {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ surface: 'student', inbox: false }));
    return;
  }
  const route = url.pathname === '/folie.html' ? '/laser.html' : url.pathname;
  const webRoot = path.join(root, 'web') + path.sep;
  const file = path.resolve(root, 'web', `.${route}`);
  if (!file.startsWith(webRoot)) { response.writeHead(403).end(); return; }
  try {
    response.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
    response.end(await readFile(file));
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = await mkdtemp(path.join(root, '.runtime', 'laser-guide-check-'));
const browser = await chromium.launchPersistentContext(profile, { headless: true, channel: 'chrome' });
const page = browser.pages()[0] || await browser.newPage();
page.setDefaultTimeout(12000);
page.on('pageerror', error => errors.push(error.message));

const storageKey = 'kloster-laservaerksted-v1';
const state = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)), storageKey);
const visible = selector => page.locator(selector).isVisible();
const ready = () => page.waitForFunction(() =>
  window.ElevhubProjectAdapter && document.querySelector('#save-status')?.textContent === 'Kladde på denne computer');
const goLaser = async () => { await page.goto(`${base}/laser.html`); await ready(); };
const goVinyl = async () => { await page.goto(`${base}/folie.html`); await ready(); };
const clearAndReload = async () => {
  await page.evaluate(key => localStorage.removeItem(key), storageKey);
  await page.reload();
  await ready();
};
const check = async (name, fn) => { await fn(); checks.push(name); console.log(`PASS ${name}`); };
const guideCard = (id = null) => id
  ? page.locator(`#laser-project-dialog [data-guide-id="${id}"]`)
  : page.locator('#laser-project-dialog [data-guide-id]').first();
const openGuides = async () => {
  await page.locator('#laser-projects-button').click();
  await assert(await visible('#laser-project-dialog'), 'laser project dialog did not open');
};
const chooseGuide = async (id = null) => {
  const card = guideCard(id);
  assert(await card.count() === 1, `missing guide card ${id || 'first'}`);
  await card.getByRole('button', { name: 'Vælg projekt', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#laser-guide-panel')?.hidden === false);
};
const guideState = () => page.evaluate(key => JSON.parse(localStorage.getItem(key))?.learning, storageKey);
const itemsJSON = () => page.evaluate(key => JSON.stringify(JSON.parse(localStorage.getItem(key))?.items || []), storageKey);
const itemGeometryJSON = () => page.evaluate(key => JSON.stringify((JSON.parse(localStorage.getItem(key))?.items || []).map(item => ({ name: item.name, d: item.d, op: item.op, fillRule: item.fillRule, text: item.text || null, gear: item.gear || null })) ), storageKey);
const itemIds = () => page.evaluate(key => (JSON.parse(localStorage.getItem(key))?.items || []).map(item => item.id), storageKey);
const exportCurrent = () => page.evaluate(async () => (await window.ElevhubProjectAdapter.export()).text());
const importProject = source => page.evaluate(async text => {
  const adapter = window.ElevhubProjectAdapter;
  if (typeof adapter.import !== 'function') throw Error('project adapter has no import method');
  await adapter.import(new Blob([text], { type: 'image/svg+xml' }));
}, source);
const guideProgress = async () => page.locator('#laser-guide-progress');
const guideStepCount = async () => Number(await (await guideProgress()).getAttribute('max'));
// The native progress element is one-based for display; learning.stepIndex is zero-based.
const currentStep = async () => page.locator('#laser-guide-progress').evaluate(node => Number(node.value) - 1);
const visibleStepCount = () => page.locator('#laser-guide-panel h3:visible').count();
const visibleListItems = () => page.locator('#laser-guide-panel li:visible').count();
const allGuideIds = () => page.locator('#laser-project-dialog [data-guide-id]').evaluateAll(nodes => nodes.map(node => node.dataset.guideId));

try {
  await goLaser();

  await check('laser starts empty on fresh localStorage and guide dialog is explicit', async () => {
    await clearAndReload();
    const initial = await state();
    assert(Array.isArray(initial.items) && initial.items.length === 0, 'fresh laser state generated drawing parts');
    assert(!initial.learning, 'fresh laser state already has learning metadata');
    assert(await page.locator('#laser-guide-panel').isHidden(), 'guide panel visible without selected guide');
    await openGuides();
    const guideIds = await allGuideIds();
    assert(guideIds.length === 11, `expected 11 laser guides, got ${guideIds.length}`);
    assert(await page.locator('#laser-project-dialog [data-guide-id] button').allTextContents().then(xs => xs.every(x => x.trim() === 'Vælg projekt')), 'guide cards expose unexpected action');
  });

  await check('choosing a guide preserves the drawing and writes guide state without autogeneration', async () => {
    await page.keyboard.press('Escape');
    await page.locator('[data-add="rect"]').click();
    const beforeItems = await itemsJSON();
    await openGuides();
    await chooseGuide('nameplate');
    const after = await state();
    assert(JSON.stringify(after.items) === beforeItems, 'guide choice changed drawing parts');
    assert(after.learning?.id === 'nameplate' && after.learning.version === 1 && after.learning.guide === true, 'guide state missing identity');
    assert(after.learning.stepIndex === 0 && after.learning.helpHidden === false && after.learning.completed !== true, 'guide state has wrong initial progress');
  });

  await check('guide navigation respects boundaries and pause/resume', async () => {
    const count = await guideStepCount();
    assert(count > 1, 'guide has no multiple steps');
    assert(await currentStep() === 0, 'guide did not start at first step');
    assert(await page.locator('#laser-guide-prev').isDisabled(), 'previous is enabled before first step');
    assert(await visibleStepCount() === 1, 'more than one step heading is visible');
    assert(await visibleListItems() <= 1, 'all guide steps are rendered as visible list items');
    await page.locator('#laser-guide-next').click();
    assert(await currentStep() === 1, 'next did not advance one step');
    while (await currentStep() < count - 1) {
      await page.locator('#laser-guide-next').click();
      assert(await currentStep() <= count - 1, 'next crossed the final step');
    }
    assert(await page.locator('#laser-guide-next').isHidden().catch(() => true) || await page.locator('#laser-guide-next').isDisabled(), 'next is available at final step');
    assert(await page.locator('#laser-guide-finish').isVisible(), 'finish is not visible at final step');
    assert(await visibleStepCount() === 1, 'more than one final step heading is visible');
    assert(await visibleListItems() <= 1, 'hidden steps are exposed as visible final list items');
    await page.locator('#laser-guide-close').click();
    assert(await page.locator('#laser-guide-panel').isHidden(), 'close did not hide guide panel');
    await page.locator('#laser-guide-resume').click();
    assert(!(await page.locator('#laser-guide-panel').isHidden()), 'resume did not reopen guide panel');
    assert((await guideState()).stepIndex === count - 1 && await currentStep() === count - 1, 'pause/resume lost current step');
  });

  await check('locate only focuses an existing control and finish closes without insertion', async () => {
    await page.locator('#laser-guide-prev').click();
    while (await currentStep() > 0) await page.locator('#laser-guide-prev').click();
    const beforeItems = await itemsJSON();
    assert(await page.locator('#laser-guide-locate').isEnabled(), 'first step with target has disabled locate');
    await page.locator('#laser-guide-locate').click();
    const afterLocate = await state();
    assert(JSON.stringify(afterLocate.items) === beforeItems, 'locate changed drawing');
    assert(!(await page.locator('#preview-dialog').isVisible()), 'locate opened preview dialog');
    assert(!(await page.locator('#text-dialog').isVisible()), 'locate opened text dialog');
    assert(afterLocate.learning?.completed !== true, 'locate completed guide');
    while (await currentStep() < (await guideStepCount()) - 1) await page.locator('#laser-guide-next').click();
    assert(await page.locator('#laser-guide-locate').isHidden() || await page.locator('#laser-guide-locate').isDisabled(), 'final nameplate step exposes a target locate action');
    await page.locator('#laser-guide-finish').click();
    const finished = await guideState();
    assert(finished.completed === true, 'finish did not persist completed state');
    assert(await page.locator('#laser-guide-panel').isHidden(), 'finish did not close guide panel');
    assert(await itemsJSON() === beforeItems, 'finish inserted or removed drawing parts');
  });

  await check('all laser guides start without generated geometry or box dialog', async () => {
    await clearAndReload();
    await openGuides();
    const ids = await allGuideIds();
    assert(ids.length === 11, `expected 11 guide cards, got ${ids.length}`);
    for (const id of ids) {
      await page.keyboard.press('Escape');
      await openGuides();
      await chooseGuide(id);
      const selected = await state();
      assert(selected.items.length === 0, `guide ${id} generated geometry on selection`);
      assert(await page.locator('#box-dialog').isHidden(), `guide ${id} opened box dialog`);
      await clearAndReload();
    }
  });

  await check('nameplate guide supports explicit self-build, editable text, laser choice and project roundtrip', async () => {
    await clearAndReload();
    await openGuides();
    await chooseGuide('nameplate');
    const emptyItems = await itemsJSON();
    await page.locator('#laser-guide-locate').click();
    assert(await page.locator('[data-add="rect"]').evaluate(node => node.classList.contains('laser-tool-target')), 'first guide target did not mark rectangle control');
    await page.locator('[data-add="rect"]').click();
    assert((await state()).items.length === 1, 'explicit rectangle click did not add one part');
    assert((await itemsJSON()) !== emptyItems, 'rectangle insertion was not observable in state');

    await page.locator('#lock-ratio').uncheck();
    await page.locator('#object-width').fill('110'); await page.locator('#object-width').press('Tab');
    await page.locator('#object-height').fill('35'); await page.locator('#object-height').press('Tab');
    const sized = await state();
    assert(sized.items.length === 1 && sized.items[0].d, 'width/height edit lost the selected form');

    await page.locator('#add-text').click();
    await page.locator('#text-value').fill('ÅSE'); await page.locator('#text-value').press('Tab');
    await page.locator('[data-op="engrave"]').click();
    const textState = await state();
    const textItem = textState.items.find(item => item.text?.value === 'ÅSE');
    assert(textItem && String(textItem.op).startsWith('engrave'), 'editable ÅSE text was not engraved');

    const beforeMove = await itemsJSON();
    const rows = page.locator('#object-list button');
    await rows.nth(0).click(); await page.locator('#workspace').focus(); await page.keyboard.press('ArrowRight');
    await rows.nth(1).click(); await page.locator('#workspace').focus(); await page.keyboard.press('ArrowDown');
    assert(await itemsJSON() !== beforeMove, 'keyboard movement did not change geometry');
    const beforeRoundtrip = await itemGeometryJSON();
    const source = await exportCurrent();
    await page.locator('#new-button').click(); await page.locator('#confirm-new').click();
    assert((await state()).items.length === 0, 'new drawing did not clear self-built parts');
    await importProject(source);
    assert(await itemGeometryJSON() === beforeRoundtrip, 'project import changed self-built geometry or text');
  });

  await check('saml tab unites and subtracts existing parts; draw tab polyline undo/redo preserves geometry', async () => {
    await page.locator('#new-button').click(); await page.locator('#confirm-new').click();
    await page.locator('#laser-tool-tab-build').click();
    await page.locator('[data-add="rect"]').click(); await page.locator('[data-add="circle"]').click();
    await page.locator('#laser-tool-tab-more').click();
    let rows = page.locator('#object-list button');
    await rows.nth(0).click(); await rows.nth(1).click({ modifiers: ['Shift'] });
    const beforeUnite = (await state()).items.length;
    await page.locator('#unite').click();
    assert((await state()).items.length === beforeUnite - 1, 'Saml tab unite did not combine selected parts');
    const united = await itemsJSON();
    await page.locator('#undo').click(); assert((await state()).items.length === beforeUnite, 'undo did not restore unite inputs');
    await page.locator('#redo').click(); assert(await itemsJSON() === united, 'redo did not restore united geometry');
    await page.locator('#undo').click();
    rows = page.locator('#object-list button');
    await rows.nth(0).click(); await rows.nth(1).click({ modifiers: ['Shift'] });
    const beforeSubtract = (await state()).items.length;
    await page.locator('#subtract').click();
    assert((await state()).items.length === beforeSubtract - 1, 'Saml tab subtract did not combine selected parts');
    const subtracted = await itemsJSON();
    await page.locator('#undo').click(); assert((await state()).items.length === beforeSubtract, 'undo did not restore subtract inputs');
    await page.locator('#redo').click(); assert(await itemsJSON() === subtracted, 'redo did not restore subtracted geometry');

    await page.locator('#new-button').click(); await page.locator('#confirm-new').click();
    await page.locator('#laser-tool-tab-draw').click();
    const polyline = page.locator('[data-tool="polyline"]');
    await polyline.click();
    assert(await polyline.getAttribute('aria-pressed') === 'true' || await polyline.evaluate(node => node.classList.contains('active')), 'polyline tool did not activate');
    const box = await page.locator('#paper').boundingBox();
    assert(box, 'drawing paper has no geometry');
    for (const [x, y] of [[box.width * .25, box.height * .25], [box.width * .5, box.height * .65], [box.width * .75, box.height * .3]]) await page.locator('#paper').click({ position: { x, y } });
    await page.keyboard.press('Enter');
    const drawn = await itemsJSON();
    assert((await state()).items.length === 1, 'polyline points did not create one figure');
    await page.locator('#undo').click(); assert((await state()).items.length === 0, 'undo did not remove polyline');
    await page.locator('#redo').click(); assert(await itemsJSON() === drawn, 'redo did not restore polyline geometry');
  });

  await check('tool tabs expose build and draw controls at the matching levels', async () => {
    await page.locator('#laser-tool-tab-build').click();
    assert(await page.locator('#laser-tool-tab-build').getAttribute('aria-selected') === 'true' || await page.locator('#laser-tool-tab-build').evaluate(n => n.classList.contains('active')), 'build tab did not activate');
    assert(await page.locator('#laser-tool-tab-draw').click().then(() => true), 'draw tab could not be clicked');
    assert(await page.locator('#laser-tool-tab-draw').getAttribute('aria-selected') === 'true' || await page.locator('#laser-tool-tab-draw').evaluate(n => n.classList.contains('active')), 'draw tab did not activate');
    const level = await page.evaluate(() => Number(document.querySelector('.level-bar button.active')?.dataset.level));
    assert(level >= 2, `draw tab did not select a drawing level (${level})`);
  });

  await check('guide step and reflection survive export/import and reload; new drawing clears learning', async () => {
    await openGuides();
    const secondGuideId = await page.locator('#laser-project-dialog [data-guide-id]').nth(1).getAttribute('data-guide-id');
    await chooseGuide(secondGuideId);
    const count = await guideStepCount();
    while (await currentStep() < count - 1) await page.locator('#laser-guide-next').click();
    const reflection = page.locator('#laser-guide-reflection');
    assert(await reflection.count() === 1, 'last guide step has no reflection field');
    await reflection.fill('Prøve og forbedring'); await reflection.press('Tab');
    const expected = await guideState();
    assert(expected.reflection === 'Prøve og forbedring', 'reflection was not persisted before export');
    const source = await exportCurrent();
    await page.locator('#new-button').click();
    await page.locator('#confirm-new').click();
    assert(!(await state()).learning, 'new drawing retained guide learning');
    await importProject(source);
    const restored = await guideState();
    assert(restored.id === expected.id && restored.stepIndex === expected.stepIndex, 'import lost guide step');
    await page.reload(); await ready();
    const reloaded = await guideState();
    assert(reloaded.id === expected.id && reloaded.stepIndex === expected.stepIndex, 'reload lost guide step');
    assert((await guideState()).reflection === 'Prøve og forbedring', 'reflection was not restored after reload');
  });

  await check('undo restores pre-guide drawing state and mobile layouts do not overflow', async () => {
    await page.locator('#laser-tool-tab-build').click();
    const beforeAdd = await itemIds();
    await page.locator('[data-add="circle"]').click();
    const afterAdd = await itemIds();
    assert(afterAdd.length === beforeAdd.length + 1, 'test shape was not added');
    await page.locator('#undo').click();
    assert(JSON.stringify(await itemIds()) === JSON.stringify(beforeAdd), 'undo did not restore the pre-add drawing');
    for (const [width, height] of [[1366, 768], [1024, 768], [1280, 600], [390, 844]]) {
      await page.setViewportSize({ width, height });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `horizontal overflow at ${width}px`);
      assert(await page.locator('#laser-guide-panel').count() === 1, `guide panel missing at ${width}px`);
    }
  });

  await check('vinyl starter sanity remains unchanged', async () => {
    await goVinyl();
    assert(await page.locator('#laser-projects-button').count() === 0, 'laser project button leaked into vinyl');
    assert(await page.locator('#laser-guide-panel').count() === 0, 'laser guide panel leaked into vinyl');
    assert(await page.locator('#starter-projects .studio-starter-card').count() > 0, 'vinyl starter sidebar disappeared');
  });

  assert(!errors.length, `browser errors: ${errors.join('; ')}`);
  const result = { passed: checks.length, checks, errors };
  await writeFile(path.join(root, '.runtime', 'laser-guide-checks.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
  const runtimeRoot = path.resolve(root, '.runtime') + path.sep;
  const profilePath = path.resolve(profile);
  if (!profilePath.startsWith(runtimeRoot) || !path.basename(profilePath).startsWith('laser-guide-check-')) throw Error('Refusing to remove a profile outside the test folder');
  await rm(profilePath, { recursive: true, force: true });
}
