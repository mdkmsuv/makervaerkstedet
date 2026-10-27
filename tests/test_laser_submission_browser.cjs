'use strict';
// Real editor, inbox, teacher session and HTTP replies; disposable pupil data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {createHash, randomUUID} = require('node:crypto');
const {startFixture, loadPlaywright} = require('../linux-elevhub/tests/student_journey_fixture.js');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'output/maturity-final-20261013/root');
const {chromium} = loadPlaywright();

(async () => {
  const report = {startedAt: new Date().toISOString(),
    reason: 'Real pupil retry after a lost accepted reply, quota feedback and teacher completion freeing capacity',
    flows: [], inputs: {}, errors: []};
  for (const name of ['app/laser_server.py', 'web/laser.js', 'web/laser.html',
    'tests/test_laser_submission_browser.cjs', 'linux-elevhub/tests/student_journey_fixture.py']) {
    report.inputs[name] = createHash('sha256').update(await fs.readFile(path.join(root, name))).digest('hex');
  }
  let fixture, browser, teacher;
  try {
    fixture = await startFixture();
    const launch = {headless: true};
    if (process.env.PLAYWRIGHT_BROWSER_CHANNEL || process.platform === 'win32')
      launch.channel = process.env.PLAYWRIGHT_BROWSER_CHANNEL || 'chrome';
    browser = await chromium.launch(launch);
    const context = await browser.newContext({viewport: {width: 1366, height: 900}});
    const page = await context.newPage();
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
    await page.locator('[data-add="rect"]').click();
    await page.waitForFunction(() => !document.querySelector('#send-button').disabled);
    await page.locator('#send-button').click();
    await page.locator('#student-name').fill('Åse');
    await page.locator('#student-class').fill('3.A');
    await page.locator('#student-project').fill('Første tegning');
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
    await page.locator('#confirm-send').click();
    await textIncludes('#send-status', 'Forbindelsen til læreren blev afbrudt');
    assert.equal((await jobs()).length, 1);
    assert.equal(await page.locator('#student-name').inputValue(), 'Åse');
    assert.equal(await page.locator('#student-project').inputValue(), 'Første tegning');
    await page.locator('#confirm-send').click();
    await page.locator('#send-success-title').waitFor({state: 'visible'});
    assert.equal(submissions[0].requestId, submissions[1].requestId);
    assert.equal((await jobs()).length, 1);
    report.flows.push('Lost accepted response retains the draft and retries the same request without duplicate jobs');

    const first = (await jobs())[0];
    const payload = submissions[0];
    for (let i = 1; i < 10; i++) {
      const response = await context.request.post(fixture.urls.design + '/api/laser/submit',
        {data: {...payload, projekt: `Kvoteprøve ${i}`, requestId: randomUUID()}});
      assert.equal(response.status(), 200, await response.text());
    }
    await page.locator('#confirm-send').click(); // Return from the real success dialog.
    await page.locator('#send-button').click();
    await page.locator('#student-project').fill('Tegningen efter fuld kø');
    await page.locator('#confirm-send').click();
    await textIncludes('#send-status', 'bed læreren afslutte de gamle afleveringer');
    await page.locator('#send-success-title').waitFor({state: 'hidden'});
    assert.equal(await page.locator('#student-project').inputValue(), 'Tegningen efter fuld kø');
    assert.equal((await jobs()).length, 10);
    report.flows.push('Full pupil quota gives actionable Danish feedback and keeps the unsent drawing');

    const done = await teacher.request.post(fixture.urls.laserTeacher + `/api/laser/jobs/${first.id}/status`,
      {data: {status: 'done'}});
    assert.equal(done.status(), 200, await done.text());
    const heldRequest = submissions.at(-1).requestId;
    await page.locator('#confirm-send').click();
    await page.locator('#send-success-title').waitFor({state: 'visible'});
    assert.equal(submissions.at(-1).requestId, heldRequest);
    const finished = await jobs();
    assert.equal(finished.filter(item => item.status !== 'done').length, 10);
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
