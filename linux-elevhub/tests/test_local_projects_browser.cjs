const {startFixture,loadPlaywright,loadJSZip}=require('./student_journey_fixture.js');
const path=require('node:path');
// Run against an isolated local.py preview, never a pupil's project store.
const {chromium} = loadPlaywright();
const assert = require('node:assert/strict');
(async () => {
 const fixture=await startFixture();
    const browser = await chromium.launch({headless: true, channel: 'chrome'});
    try {
        const page = await browser.newPage({viewport: {width: 1024, height: 768}});
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        await page.goto(fixture.urls.projekter+'/');
        await page.getByRole('button', {name:'Gem en fil', exact:true}).click();
        await page.locator('#folder').fill('5A · Lamper · gruppe 3');
        await page.locator('#name').fill('Vores læselampe');
        const first = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="40" height="20"/></svg>';
        await page.locator('#file').setInputFiles({name:'lampe.projekt.svg', mimeType:'image/svg+xml', buffer:Buffer.from(first)});
        await page.locator('#upload-submit').click();
        await page.locator('#upload').waitFor({state:'hidden'});
        await page.locator('#search').fill('læselampe');
        const card = page.locator('.project').first();
        assert.match(await card.innerText(), /gruppe 3/);
        await card.locator('summary').click();
        const downloadPromise = page.waitForEvent('download');
        await card.getByRole('link', {name:'Hent fil'}).click();
        const download = await downloadPromise;
        const stream = await download.createReadStream();
        const chunks = [];
        for await (const chunk of stream) chunks.push(chunk);
        assert.equal(Buffer.concat(chunks).toString(), first);
        const second = first.replace('40','60');
        await page.evaluate(async data => {
            const base='/_elevhub/projekter/api/';
            const list=await (await fetch(base+'list')).json();
            const p=list.projects.find(p=>p.name==='Vores læselampe');
            const query=new URLSearchParams({id:p.id, name:p.name, folder:p.folder, kind:p.kind, filename:p.filename, expected:p.revision});
            const r=await fetch(base+'save?'+query,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Elevhub-Project':'1'},body:data});
            if(!r.ok) throw Error(await r.text());
        }, second);
        await page.getByRole('button',{name:'Opdater',exact:true}).click();
        await page.locator('.project').first().locator('summary').click();
        await page.getByRole('button',{name:'Tidligere versioner'}).first().click();
        await page.locator('#versions .version').last().waitFor();
        assert.match(await page.locator('#versions').innerText(), /Version 1/);
        assert.match(await page.locator('#versions').innerText(), /Version 2/);
        await page.locator('#close-history').click();
        await page.locator('#search').fill('findes ikke');
        assert.equal(await page.locator('.project').count(), 0);
        await page.locator('#search').fill('');
        await page.screenshot({path:path.join(fixture.output,'projects-library.png')});
        assert.deepEqual(errors, []);
        console.log('PASS local upload, Danish search, exact file roundtrip, history and 1024×768 UI');
    } finally { await browser.close(); await fixture.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
