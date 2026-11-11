// Runs only against the explicitly supplied isolated demo service.
const fs = require('node:fs');
const {createRequire} = require('node:module');
const {chromium} = createRequire(__filename)('playwright');
const base = process.env.PRINT_AUDIT_BASE;
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '')) throw Error('Isolated loopback demo URL required');
const checks = [], errors = [];
const assert = (ok, text) => { if (!ok) throw Error(text); };
(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const context = await browser.newContext({viewport:{width:1280,height:656}});
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  const check = async (name, run) => { await run(); checks.push({name,status:'PASS'}); console.log('PASS',name); };
  try {
    const state = await (await context.request.get(base+'/api/state')).json();
    assert(state.demo, 'Refusing to submit to non-demo service');
    await page.goto(base);
    await page.locator('[data-colour="any"]').waitFor();
    await check('No model cannot submit; invalid file gives understandable error', async () => {
      assert(await page.locator('#send-button').isDisabled(), 'Empty submit enabled');
      await page.locator('#file-input').setInputFiles({name:'invalid.txt',mimeType:'text/plain',buffer:Buffer.from('test')});
      await page.locator('#upload-error').waitFor();
      assert((await page.locator('#upload-error').innerText()).includes('STL'), 'Missing invalid-format guidance');
    });
    await check('STL preview has correct millimetres; colour and quality can change', async () => {
      await page.locator('#file-input').setInputFiles(process.env.PRINT_AUDIT_STL);
      await page.waitForFunction(()=>document.querySelector('#file-size').textContent.includes('20 × 20 × 20'));
      assert(await page.locator('#model-canvas canvas').count() === 1, 'No rendered preview');
      await page.locator('#colours [data-colour]').nth(1).click();
      await page.locator('[data-quality="beautiful"]').click();
      assert(await page.locator('[data-quality="beautiful"]').getAttribute('aria-pressed')==='true','Quality not selected');
      assert(await page.locator('#send-button').isEnabled(),'Valid model not submit-ready');
    });
    let submitted;
    await check('Demo submission yields one job and receipt, retained after reload', async () => {
      await page.locator('#send-button').click();
      await page.locator('#print-confirmation[open]').waitFor();
      const result = await (await context.request.get(base+'/api/state')).json();
      assert(result.jobs.length===1 && result.jobs[0].quality==='beautiful','Wrong submitted job');
      submitted = result.jobs[0];
      assert(submitted.meta.size.every(n=>n===20),'Wrong submitted model size');
      await page.locator('#print-confirmation-close').click();
      await page.reload();
      await page.locator('#receipt').waitFor();
      assert((await page.locator('#receipt').innerText()).includes('i kø'),'Receipt status lost');
      assert((await page.locator('#mini-queue-list .is-mine').innerText()).includes(submitted.filename),'Submitted file lost from personal queue');
    });
    await check('Teacher pause, resume and cancellation update demo queue', async () => {
      const teacher = await context.newPage();
      teacher.on('pageerror', e=>errors.push(e.message));
      await teacher.goto(base+'/laerer');
      await teacher.locator('#admin-token').fill(process.env.PRINT_AUDIT_TOKEN);
      await teacher.locator('#login-form button').click();
      await teacher.locator('#teacher-content').waitFor();
      await teacher.locator('#pause-queue').click();
      await teacher.waitForFunction(()=>document.querySelector('#pause-queue').textContent.includes('Start'));
      assert((await (await context.request.get(base+'/api/state')).json()).paused,'Pause failed');
      await teacher.locator('#pause-queue').click();
      await teacher.waitForFunction(()=>document.querySelector('#pause-queue').textContent.includes('pause'));
      assert(!(await (await context.request.get(base+'/api/state')).json()).paused,'Resume failed');
      await teacher.locator('#teacher-queue [data-resolve="'+submitted.id+'"]').click();
      await teacher.waitForFunction(()=>document.querySelector('#teacher-queue').textContent.includes('Ingen print'));
      assert((await (await context.request.get(base+'/api/state')).json()).jobs.find(j=>j.id===submitted.id).status==='cancelled','Queue cancellation failed');
      await teacher.close();
    });
    await check('Connection loss and recovery show truthful status', async () => {
      await context.setOffline(true);
      await page.locator('#connection-error').waitFor({timeout:15000});
      await context.setOffline(false);
      await page.locator('#connection-error').waitFor({state:'hidden',timeout:15000});
    });
    await check('Laptop layout fits horizontally; no browser exceptions', async () => {
      const size=await page.evaluate(()=>({body:document.documentElement.scrollWidth,viewport:innerWidth}));
      assert(size.body<=size.viewport+1,'Horizontal overflow');
      assert(errors.length===0,errors.join('\n'));
      await page.screenshot({path:'.runtime/workshop-audit-print/browser.png',fullPage:true});
    });
  } catch (error) {
    checks.push({name:'Stopped at failure',status:'FAIL',error:String(error)});
    process.exitCode=1;
    console.error(error.message);
  } finally {
    await browser.close();
    fs.writeFileSync('output/windows-maker-20260922/workshop-audit-print-browser.json', JSON.stringify({checks,errors,demoOnly:true,physicalPrinterContacted:false},null,2));
  }
})();
