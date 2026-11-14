'use strict';
// Real editor, inbox, teacher session and HTTP replies; disposable pupil data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {createHash, randomUUID} = require('node:crypto');
const {startFixture, loadPlaywright} = require('../system/linux-elevhub/tests/student_journey_fixture.js');
const root = path.resolve(__dirname, '..');
const output = process.env.ELEVHUB_LASER_SUBMISSION_OUTPUT || path.join(root, 'output/maturity-final-20261013/root');
const {chromium} = loadPlaywright();

(async () => {
  const report = {startedAt: new Date().toISOString(),
    reason: 'Real pupil retry after a lost accepted reply, quota feedback and teacher completion freeing capacity',
    flows: [], inputs: {}, errors: []};
  for (const name of ['system/app/laser_server.py', 'apps/laser/web/laservaerksted/laser-editor.js', 'apps/laser/web/laservaerksted/index.html',
    'tests/test_laser_submission_browser.cjs', 'system/linux-elevhub/tests/student_journey_fixture.py']) {
    report.inputs[name] = createHash('sha256').update(await fs.readFile(path.join(root, name))).digest('hex');
  }
  let fixture, browser, teacher, page;
  try {
    fixture = await startFixture();
    const launch = {headless: true};
    if (process.env.PLAYWRIGHT_BROWSER_CHANNEL || process.platform === 'win32')
      launch.channel = process.env.PLAYWRIGHT_BROWSER_CHANNEL || 'chrome';
    browser = await chromium.launch(launch);
    const context = await browser.newContext({viewport: {width: 1366, height: 900}});
    page = await context.newPage();
    const textIncludes = (selector, text) => page.waitForFunction(
      ({selector, text}) => document.querySelector(selector)?.textContent.includes(text), {selector, text});
    page.on('pageerror', e => report.errors.push(e.message));
    teacher = await browser.newContext();
    const login = await teacher.request.post(fixture.urls.laserTeacher + '/api/laser/login',
      {data: {code: fixture.urls.laserTeacherCode}});
    assert.equal(login.status(), 200);
    const jobs = async () => {
      const response = await teacher.request.get(fixture.urls.laserTeacher + '/api/laser/jobs');
      assert.equal(response.status(), 200);
      return (await response.json()).jobs;
    };
    await page.goto(fixture.urls.design + '/laser.html', {waitUntil: 'domcontentloaded'});
    await page.locator('#designCanvas').waitFor();
    await page.waitForFunction(() => window.ElevhubProjectAdapter);
    await page.locator('[data-add-type="rect"]').click();
    await page.locator('#projectName').fill('Første tegning');
    await page.locator('#projectName').press('Tab');
    await page.locator('#reviewButton').click();
    await page.locator('#reviewStudentName').fill('Åse');
    await page.locator('#reviewClass').fill('3.A');
    const drawing = await page.evaluate(() => window.ElevhubProjectAdapter.signature());
    const submissions = [];
    let drop = true;
    await page.route('**/api/laser/submit', async route => {
      submissions.push(route.request().postDataJSON());
      if (drop) {
        drop = false;
        const accepted = await route.fetch();
        assert.equal(accepted.status(), 200);
        await route.abort('failed');
      } else await route.continue();
    });
    await page.locator('#submitTestButton').click();
    await textIncludes('#reviewError', 'Der kom ikke svar fra afleveringen');
    assert.equal((await jobs()).length, 1);
    assert.equal(await page.locator('#reviewStudentName').inputValue(), 'Åse');
    assert.equal(await page.locator('#projectName').inputValue(), 'Første tegning');
    assert.equal(await page.evaluate(() => window.ElevhubProjectAdapter.signature()), drawing);
    await page.locator('#submitTestButton').click();
    await page.locator('#reviewSuccess:not([hidden])').waitFor();
    assert.equal(submissions[0].requestId, submissions[1].requestId);
    assert.equal((await jobs()).length, 1);
    report.flows.push('Lost accepted response retains the draft and retries the same request without duplicate jobs');

    const first = (await jobs())[0];
    const payload = submissions[0];
    // The current inbox permits 100 pending jobs per pupil (the historical
    // 10-job fixture expectation predates the current queue contract).
    const pupilLimit = 100;
    for (let i = 1; i < pupilLimit; i++) {
      const response = await context.request.post(fixture.urls.design + '/api/laser/submit',
        {data: {...payload, projekt: `Kvoteprøve ${i}`, requestId: randomUUID()}});
      assert.equal(response.status(), 200, await response.text());
    }
    await page.locator('#finishReviewButton').click(); // Return from the real success dialog.
    await page.locator('#projectName').fill('Tegningen efter fuld kø');
    await page.locator('#projectName').press('Tab');
    const blockedDrawing = await page.evaluate(() => window.ElevhubProjectAdapter.signature());
    await page.locator('#reviewButton').click();
    const quotaReply = page.waitForResponse(r => new URL(r.url()).pathname === '/api/laser/submit');
    await page.locator('#submitTestButton').click();
    const quota = await quotaReply;
    report.quotaReply = {status: quota.status(), body: await quota.text()};
    await textIncludes('#reviewError', 'bed læreren afslutte de gamle afleveringer');
    await page.locator('#reviewSuccess').waitFor({state: 'hidden'});
    assert.equal(await page.locator('#projectName').inputValue(), 'Tegningen efter fuld kø');
    assert.equal(await page.evaluate(() => window.ElevhubProjectAdapter.signature()), blockedDrawing);
    assert.equal((await jobs()).length, pupilLimit);
    report.flows.push('Full pupil quota gives actionable Danish feedback and keeps the unsent drawing');

    const done = await teacher.request.post(fixture.urls.laserTeacher + `/api/laser/jobs/${first.id}/status`,
      {data: {status: 'done'}});
    assert.equal(done.status(), 200, await done.text());
    const heldRequest = submissions.at(-1).requestId;
    await page.locator('#submitTestButton').click();
    await page.locator('#reviewSuccess:not([hidden])').waitFor();
    assert.equal(submissions.at(-1).requestId, heldRequest);
    const finished = await jobs();
    assert.equal(finished.filter(item => item.status !== 'done').length, pupilLimit);
    assert.equal(finished.filter(item => item.project === 'Tegningen efter fuld kø').length, 1);
    report.flows.push('Teacher completion frees one place; the unchanged blocked submission then succeeds exactly once');

    const other = await context.request.post(fixture.urls.design + '/api/laser/submit',
      {data: {...payload, elevnavn: 'Bo', projekt: 'Bos tegning', requestId: randomUUID()}});
    assert.equal(other.status(), 200);
    report.flows.push('Another pupil can submit while the first pupil has a full quota');
    assert.deepEqual(report.errors, []);
    report.status = 'passed';
    console.log(`PASS laser submission browser: ${report.flows.length} real flow groups`);
  } catch (error) {
    report.status = 'failed'; report.failure = error.stack || String(error);
    if (page) {
      await fs.mkdir(output, {recursive:true});
      await page.screenshot({path:path.join(output,'submission-failure.png'),fullPage:true}).catch(()=>{});
      report.visible = await page.locator('body').innerText().catch(()=>'');
    }
    console.error(report.failure); process.exitCode = 1;
  } finally {
    if (teacher) await teacher.close();
    if (browser) await browser.close();
    if (fixture) await fixture.close();
    report.finishedAt = new Date().toISOString();
    await fs.mkdir(output, {recursive: true});
    await fs.writeFile(path.join(output, 'laser-browser-' + report.startedAt.replaceAll(/[:.]/g, '-') + '.json'),
      JSON.stringify(report, null, 2) + '\n');
  }
})();
