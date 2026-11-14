'use strict';
// Current /laser.html regression. The former combined editor's guide and box
// dialogs are retired; this tests the supported JSON editor, real project
// storage/navigation, the 250 x 167 mm frame and the 500-path export boundary.
// All services/storage are disposable loopback fixtures. Submissions are
// intercepted before the fixture's unreachable upstream; no machine jobs run.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const python = process.env.ELEVHUB_TEST_PYTHON || process.env.PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const fixture = spawn(python, ['-B', '-u', path.join(root, 'linux-elevhub/tests/finish_fixture.py')], {
  stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8'}
});
let fixtureErrors = '', browser, page;
fixture.stderr.on('data', chunk => fixtureErrors += chunk);
const output = process.env.ELEVHUB_LASER_E2_OUTPUT;
const report = {flows: [], retired: ['Former laser-guide and box-dialog flows belonged to the replaced combined editor.'], errors: []};

function ports() {
  return new Promise((resolve, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(Error('finish fixture startup timeout: ' + fixtureErrors)), 15000);
    fixture.stdout.on('data', chunk => {
      text += chunk;
      for (const line of text.split(/\r?\n/)) {
        try { const value = JSON.parse(line); if (value.laser) { clearTimeout(timer); resolve(value); return; } } catch {}
      }
    });
    fixture.once('exit', code => { clearTimeout(timer); reject(Error('finish fixture exited ' + code + ': ' + fixtureErrors)); });
  });
}
async function stopFixture() {
  if (fixture.exitCode !== null) return;
  await new Promise(resolve => {
    const timer = setTimeout(() => { fixture.kill(); resolve(); }, 10000);
    fixture.once('exit', () => { clearTimeout(timer); resolve(); });
    fixture.stdin.end();
  });
}
const project = () => page.evaluate(async () => JSON.parse(await (await window.ElevhubProjectAdapter.export()).text()));
// File validation materialises optional false defaults; compare their meaning.
const geometry = objects => objects.map(object => ({...object,
  ...(object.type === 'rect' ? {rounded: Boolean(object.rounded)} : {}),
  ...(object.type === 'group' ? {libraryGroup: Boolean(object.libraryGroup)} : {}),
  ...(object.children ? {children: geometry(object.children)} : {})}));
const totalObjects = objects => objects.reduce((total, object) => total + 1 + (object.children ? totalObjects(object.children) : 0), 0);
async function importFile(data, name='boundary.laser.json') {
  await page.locator('#projectFileInput').setInputFiles({name,mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
  await page.waitForFunction(() => !document.querySelector('#projectFileInput').value);
}
async function namedStart() {
  await page.locator('#project-classroom').fill('6.B');
  await page.locator('#project-student').fill('Prøveelev');
  await page.locator('#project-title').fill('E2 gemt geometri');
  await page.locator('#project-start-confirm').click();
  await page.waitForFunction(() => !document.querySelector('#elevhub-project-dialog').open);
}
async function blankDrawing() {
  await page.locator('#newProjectButton').click();
  await page.locator('#confirmNewProjectButton').click();
  await page.waitForFunction(() => document.querySelector('#objectLayer').childElementCount === 0);
}
async function review() {
  await page.locator('#reviewButton').click();
  await page.locator('#reviewStudentName').fill('Prøveelev');
  await page.locator('#reviewClass').fill('6.B');
}
async function finish() {
  await page.evaluate(() => { window.__finishResult = null; window.ElevhubProjectSession.finish().then(value => window.__finishResult = value); });
}
async function main() {
  const workshop = await ports();
  browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || (process.platform === 'win32' ? 'chrome' : undefined)});
  const context = await browser.newContext({viewport: {width: 1366, height: 900}});
  const outside = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (['http:', 'https:'].includes(url.protocol) && !['127.0.0.1', 'localhost'].includes(url.hostname)) { outside.push(url.origin); return route.abort(); }
    return route.continue();
  });
  page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  const loaded = await page.goto(`http://127.0.0.1:${workshop.laser}/laser.html`);
  assert.equal(loaded.status(), 200);
  await page.locator('#designCanvas').waitFor();
  await page.waitForFunction(() => window.ElevhubProjectAdapter?.inline.active && window.ElevhubProjectSession);
  assert.equal(await page.locator('#designCanvas').getAttribute('viewBox'), '0 0 250 167');
  await namedStart();
  await blankDrawing();
  await page.locator('#projectName').fill('E2 gemt geometri');
  await page.locator('#projectName').press('Tab');
  for (const kind of ['rect', 'circle', 'heart', 'star']) await page.locator(`[data-add-type="${kind}"]`).click();
  const initial = await project();
  assert.equal(initial.project.objects.length, 4);
  assert(initial.project.objects.every(o => o.x >= 0 && o.y >= 0 && o.x + o.width <= 250 && o.y + o.height <= 167));
  const last = initial.project.objects.at(-1);
  const point = await page.evaluate(id => {
    const graphic = document.querySelector(`#objectLayer [data-object-id="${id}"] .geometry-node`);
    const bounds = graphic.getBBox();
    const p = new DOMPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2).matrixTransform(graphic.getScreenCTM());
    return {x:p.x, y:p.y, hit:document.elementFromPoint(p.x, p.y)?.closest('[data-object-id]')?.dataset.objectId};
  }, last.id);
  assert.equal(point.hit, last.id, 'closed shape interior must hit its current drawing object');
  await page.mouse.move(point.x, point.y); await page.mouse.down();
  await page.mouse.move(point.x + 18, point.y + 12, {steps: 3}); await page.mouse.up();
  const moved = await project();
  assert.notDeepEqual(moved.project.objects.at(-1), last, 'shape must drag through its rendered hit area');
  await page.locator('#undoButton').click();
  assert.deepEqual((await project()).project.objects, initial.project.objects);
  await page.locator('#redoButton').click();
  assert.deepEqual((await project()).project.objects, moved.project.objects);
  report.flows.push('Current entrypoint, fixed frame, actual closed-shape hit/drag and undo/redo');

  const saveReply = page.waitForResponse(r => r.url().includes('/_elevhub/projekter/api/save?') && r.request().method() === 'POST');
  await page.locator('#saveProjectButton').click();
  assert.equal((await saveReply).status(), 200);
  await page.waitForFunction(() => document.querySelector('#statusMessage').textContent.includes('Gemt på denne computer'));
  const saved = await project();
  const pickerReply = page.waitForResponse(r => r.url().includes('/_elevhub/projekter/?pick=laser'));
  await page.locator('#openProjectButton').click();
  const picker = await pickerReply;
  assert.match(picker.headers()['content-security-policy'] || '', /frame-ancestors 'self'/);
  const shelf = page.frameLocator('#elevhub-project-dialog iframe');
  await shelf.getByRole('heading', {name:'Dine projekter'}).waitFor();
  await page.locator('#project-library-close').click();
  assert.deepEqual((await project()).project.objects, saved.project.objects, 'closing the shelf must preserve the drawing');

  // The same lifecycle API is called by kiosk return/navigation. Exercise its
  // real visible save/cancel dialog and real archive, rather than approving it.
  await page.locator('[data-add-type="rect"]').click();
  const unsaved = await project();
  await finish(); await page.locator('#project-finish-continue').click();
  await page.waitForFunction(() => window.__finishResult === false);
  assert.deepEqual((await project()).project.objects, unsaved.project.objects);
  await finish();
  const finishSave = page.waitForResponse(r => r.url().includes('/_elevhub/projekter/api/save?') && r.request().method() === 'POST');
  await page.locator('#project-finish-save').click();
  assert.equal((await finishSave).status(), 200);
  await page.waitForFunction(() => window.__finishResult === true);
  assert.notDeepEqual((await project()).project.objects, unsaved.project.objects, 'finish must clear the previous pupil drawing');
  await page.locator('#openProjectButton').click();
  await shelf.getByRole('button', {name:'Åbn her: E2 gemt geometri', exact:true}).click();
  await page.waitForFunction(() => !document.querySelector('#elevhub-project-dialog').open);
  assert.deepEqual(geometry((await project()).project.objects), geometry(unsaved.project.objects), 'saved JSON must reopen with exact geometry/processes');
  report.flows.push('Same-origin project shelf CSP, safe return cancellation, save-and-finish, exact JSON reopen');

  const beforeInvalid = await project();
  await page.locator('#projectFileInput').setInputFiles({name:'wrong-frame.laser.json', mimeType:'application/json', buffer:Buffer.from(JSON.stringify({...beforeInvalid, workspace:{widthMm:180,heightMm:120}}))});
  await page.locator('#fileErrorDialog[open]').waitFor();
  assert.match(await page.locator('#fileErrorMessage').innerText(), /250.*167/);
  assert.deepEqual((await project()).project.objects, beforeInvalid.project.objects, 'invalid file must not erase the current drawing');
  await page.locator('#closeFileErrorButton').click();
  report.flows.push('Wrong-frame project is rejected with the current drawing intact');

  // Data-preserving export validation and network-error recovery use only an
  // intercepted loopback request; the fixture never contacts a school queue.
  const submissions = [];
  await page.route('**/api/laser/submit', route => { submissions.push(route.request().postDataJSON()); return route.abort('failed'); });
  await review(); await page.locator('#submitTestButton').click();
  await page.locator('#reviewError').filter({hasText:'Der kom ikke svar'}).waitFor();
  assert.equal(submissions.length, 1);
  assert.deepEqual((await project()).project.objects, beforeInvalid.project.objects);
  await page.unroute('**/api/laser/submit');
  await page.route('**/api/laser/submit', route => { submissions.push(route.request().postDataJSON()); return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({id:'e2-loopback-only'})}); });
  await page.locator('#submitTestButton').click();
  await page.locator('#reviewSuccess:not([hidden])').waitFor();
  assert.equal(submissions[0].requestId, submissions[1].requestId, 'unchanged retry must reuse its request id');
  assert.match(submissions[1].svg, /viewBox="0 0 250 167"/);
  await page.locator('#finishReviewButton').click();
  report.flows.push('Export and lost-response retry preserve the drawing, pupil details and request id');

  for (const width of [1150,1280,1366,1920]) {
    await page.setViewportSize({width,height:700});
    await page.locator('#helpButton').click();
    const close = page.locator('#closeHelpButton'); await close.scrollIntoViewIfNeeded();
    const box = await close.boundingBox(); assert(box && box.y >= 0 && box.y + box.height <= 700, 'help close reachable at ' + width);
    await close.click();
    assert(await page.locator('#helpButton').evaluate(node => node === document.activeElement), 'help must return focus');
  }
  report.flows.push('Current help is reachable and returns keyboard focus at all four desktop widths');

  await page.setViewportSize({width:1366,height:900});
  await blankDrawing();
  // Reach the export cap through actual add/group handlers. Grouping keeps
  // each root list under 100; no private editor state or cap is overwritten.
  for (const count of [100,99,99,99,99]) {
    await page.evaluate(count => { const button=document.querySelector('[data-add-type="rect"]'); for(let i=0;i<count;i++) button.click(); }, count);
    await page.locator('#designCanvas').focus(); await page.keyboard.press('Control+a');
    await page.locator('#groupButton').click();
  }
  for(let i=0;i<4;i++) await page.locator('[data-add-type="rect"]').click();
  await review();
  const countBefore=submissions.length;
  await page.locator('#submitTestButton').click();
  await page.locator('#reviewSuccess:not([hidden])').waitFor();
  const countPaths = svg => (svg.match(/<path /g)||[]).length;
  assert.equal(submissions.length,countBefore+1);
  assert.equal(countPaths(submissions.at(-1).svg),500,'exactly 500 paths must export');
  await page.locator('#finishReviewButton').click();
  await page.locator('[data-add-type="rect"]').click();
  const capped = await project();
  // A drawing created by supported UI handlers must reopen from its own saved
  // JSON, including group overhead above the former 240-object import cap.
  await page.locator('#projectFileInput').setInputFiles({name:'grouped-roundtrip.laser.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(capped))});
  await page.waitForFunction(() => !document.querySelector('#projectFileInput').value);
  assert.equal(await page.locator('#fileErrorDialog').evaluate(node => node.open),false,'UI-created grouped project must reopen from its own file');
  assert.deepEqual(geometry((await project()).project.objects),geometry(capped.project.objects));
  await review(); await page.locator('#submitTestButton').click();
  await page.locator('#reviewError').filter({hasText:'for mange dele'}).waitFor();
  assert.equal(submissions.length,countBefore+1,'501 paths must be rejected before any request');
  assert.deepEqual(geometry((await project()).project.objects),geometry(capped.project.objects));
  await page.locator('#cancelReviewButton').click();
  report.flows.push('500 real UI-created paths export; 501 are rejected before sending without erasing the drawing');

  // Boundary files use the public JSON input. Rejected editing operations must
  // not allocate IDs or modify objects, so saved files remain reopenable.
  let id=0;
  const rect=()=>({id:'limit-'+(++id),type:'rect',name:'Grænsedel '+id,x:10,y:10,width:1,height:1,rotation:0,process:'cut',lockRatio:false});
  const group=children=>({id:'limit-'+(++id),type:'group',name:'Grænsegruppe '+id,x:0,y:0,width:100,height:100,rotation:0,process:'cut',lockRatio:true,children});
  const payload=objects=>({...capped,project:{name:'Grænseprøve',nextId:10000,objects,selectedIds:[objects[0].id]}});
  const at599=payload(Array.from({length:6},(_,index)=>group(Array.from({length:index===5?98:99},rect))));
  assert.equal(totalObjects(at599.project.objects),599);
  await importFile(at599);
  assert.equal(await page.locator('#fileErrorDialog').evaluate(node=>node.open),false);
  await page.locator('[data-add-type="rect"]').click();
  const at600=await project();assert.equal(totalObjects(at600.project.objects),600);
  const unchanged=async()=>{const now=await project();assert.deepEqual(now.project.objects,at600.project.objects);assert.equal(now.project.nextId,at600.project.nextId);};
  await page.locator('[data-add-type="rect"]').click();await unchanged();
  assert.match(await page.locator('#statusMessage').innerText(),/600/);
  await page.locator('#designCanvas').focus();await page.keyboard.press('Control+d');await unchanged();
  await page.locator('#copyButton').click();await page.locator('#pasteButton').click();await unchanged();
  await page.locator('#designCanvas').focus();await page.keyboard.press('Control+a');await page.locator('#groupButton').click();await unchanged();
  await page.locator('#libraryButton').click();await page.locator('#libraryGrid [data-motif-id="cat"]').click();
  assert.match(await page.locator('#libraryStatus').innerText(),/600/);await unchanged();
  if(output) await page.screenshot({path:path.join(output,'limit-library.png'),fullPage:true});
  await page.locator('#finishLibraryButton').click();
  await importFile(at600);assert.equal(await page.locator('#fileErrorDialog').evaluate(node=>node.open),false);
  assert.deepEqual(geometry((await project()).project.objects),geometry(at600.project.objects));
  const over600=structuredClone(at600);over600.project.objects.push(rect());await importFile(over600);
  await page.locator('#fileErrorDialog[open]').waitFor();assert.match(await page.locator('#fileErrorMessage').innerText(),/for mange indlejrede dele/);
  assert.deepEqual(geometry((await project()).project.objects),geometry(at600.project.objects));await page.locator('#closeFileErrorButton').click();

  const rootBoundary=payload([group([rect(),rect()]),...Array.from({length:99},rect)]);
  await importFile(rootBoundary);const rootsBefore=await project();await page.locator('#ungroupButton').click();
  assert.match(await page.locator('#statusMessage').innerText(),/100/);assert.deepEqual((await project()).project.objects,rootsBefore.project.objects);
  let nested=rect();for(let depth=0;depth<6;depth++)nested=group([nested]);
  await importFile(payload([nested,rect()]));const depthBefore=await project();
  await page.locator('#designCanvas').focus();await page.keyboard.press('Control+a');await page.locator('#groupButton').click();
  assert.match(await page.locator('#statusMessage').innerText(),/6 niveauer/);assert.deepEqual((await project()).project.objects,depthBefore.project.objects);
  if(output) await page.screenshot({path:path.join(output,'limit-depth.png'),fullPage:true});
  report.flows.push('Shared limits: 600 total objects round-trip; add, copy, paste, library, grouping, ungrouping and excessive nesting reject before data mutation');

  // Legal path whitespace gives a cheap, real file near the byte boundary
  // without forcing the browser to render hundreds of thousands of segments.
  const bytePayload=payload(Array.from({length:15},(_,index)=>({...rect(),type:'path',name:'Detalje '+index,
    pathData:'M0 0L100 0L100 100Z',fillRule:'evenodd',closed:true})));
  bytePayload.project.selectedIds=bytePayload.project.objects.map(object=>object.id);
  let padding=1_999_900-Buffer.byteLength(JSON.stringify(bytePayload,null,2));
  bytePayload.project.objects.forEach((object,index)=>{const size=Math.floor(padding/(15-index));object.pathData+=' '.repeat(size);padding-=size;});
  const beforeBytes=await project();
  await importFile(bytePayload);assert.equal(await page.locator('#fileErrorDialog').evaluate(node=>node.open),false);
  const byteBefore=await project(),byteSize=Buffer.byteLength(JSON.stringify(byteBefore,null,2));
  assert(byteSize>1_990_000&&byteSize<=2_000_000);
  const undoDisabled=await page.locator('#undoButton').isDisabled();
  const sameBytes=async()=>{assert.deepEqual((await project()).project,byteBefore.project);assert.equal(await page.locator('#undoButton').isDisabled(),undoDisabled);};
  await page.locator('#designCanvas').focus();await page.keyboard.press('Control+d');await sameBytes();
  assert.match(await page.locator('#statusMessage').innerText(),/for stor.*gemmes og åbnes/);
  await page.locator('#projectName').fill('Æ'.repeat(80));await page.locator('#projectName').press('Tab');await sameBytes();
  const nameError=await page.evaluate(()=>{try{window.ElevhubProjectAdapter.setName('Æ'.repeat(80));return '';}catch(error){return error.message;}});
  assert.match(nameError,/kortere navn/);await sameBytes();
  await page.locator('#copyButton').click();await page.locator('#pasteButton').click();await sameBytes();
  await page.locator('[data-add-type="rect"]').click();await sameBytes();
  await page.locator('#designCanvas').focus();await page.keyboard.press('Control+a');
  const byteSelected=await project();await page.locator('#groupButton').click();
  assert.deepEqual((await project()).project,byteSelected.project);
  await page.locator('#libraryButton').click();await page.locator('#libraryGrid [data-motif-id="cat"]').click();
  assert.match(await page.locator('#libraryStatus').innerText(),/for stor.*gemmes og åbnes/);
  assert.deepEqual((await project()).project,byteSelected.project);
  if(output) await page.screenshot({path:path.join(output,'limit-bytes.png'),fullPage:true});
  await page.locator('#finishLibraryButton').click();
  await page.locator('#undoButton').click();
  assert.deepEqual((await project()).project.objects,beforeBytes.project.objects,'refused edits must not add undo history after importing the file');
  await page.locator('#redoButton').click();assert.deepEqual((await project()).project.objects,byteBefore.project.objects);
  await importFile(await project());assert.equal(await page.locator('#fileErrorDialog').evaluate(node=>node.open),false);
  assert.deepEqual((await project()).project.objects,byteBefore.project.objects,'near-limit project must still reopen after refused edits');
  const compactOverflow=structuredClone(byteBefore);compactOverflow.project.objects[0].pathData+=' '.repeat(500);
  assert(Buffer.byteLength(JSON.stringify(compactOverflow))<2_000_000);
  assert(Buffer.byteLength(JSON.stringify(compactOverflow,null,2))>2_000_000);
  await importFile(compactOverflow);await page.locator('#fileErrorDialog[open]').waitFor();
  assert.match(await page.locator('#fileErrorMessage').innerText(),/for detaljeret/);
  assert.deepEqual((await project()).project.objects,byteBefore.project.objects);await page.locator('#closeFileErrorButton').click();
  report.flows.push('UTF-8 2 MB save budget: oversized add, duplicate, paste, grouping and library insertion keep data/IDs/history intact; own file reopens');
  assert.deepEqual(outside, []); assert.deepEqual(report.errors, []);
  if(output) await page.screenshot({path:path.join(output,'current-editor.png'),fullPage:true});
  report.status='passed'; console.log('PASS current laser E2: '+report.flows.length+' flow groups');
}
if(output) fs.mkdirSync(output,{recursive:true});
main().catch(async error => {
  report.status='failed';report.failure=error.stack||String(error);console.error(error);
  if(output&&page) { await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{}); fs.writeFileSync(path.join(output,'failure-dom.txt'),await page.locator('body').innerText().catch(()=>'')); }
  process.exitCode=1;
}).finally(async()=>{
  if(browser) await browser.close(); await stopFixture();
  if(output) { fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(output,'fixture.log'),fixtureErrors); }
});
