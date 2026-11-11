import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const root = process.cwd();
const bundle = path.join(root, '.runtime', 'design-catalog-test.js');
await build({
  stdin: {contents: `import * as LaserGeometry from ${JSON.stringify(path.join(root, 'web/laser-geometry.js'))};\nimport * as DesignProjects from ${JSON.stringify(path.join(root, 'web/design-projects.js'))};\nwindow.LaserGeometry=LaserGeometry; window.DesignProjects=DesignProjects;`, resolveDir: root, sourcefile:'design-catalog-entry.js'},
  bundle:true, format:'iife', globalName:'DesignCatalogTest', outfile:bundle, sourcemap:false
});

const mime = p => p.endsWith('.js') ? 'text/javascript' : p.endsWith('.ttf') ? 'font/ttf' : 'text/html';
const server = http.createServer((req,res) => {
  let pathname = decodeURIComponent((req.url||'/').split('?')[0]);
  if (pathname === '/folie.html') pathname = '/laser.html';
  if (pathname === '/laser.html' || pathname.startsWith('/assets/')) pathname = `/web${pathname}`;
  const file = path.resolve(root, pathname.replace(/^\//,''));
  if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end('not found'); return; }
  res.writeHead(200, {'content-type':mime(file)}); fs.createReadStream(file).pipe(res);
});
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
const port = server.address().port;
const browser = await chromium.launch({headless:true, channel:'chrome'});
const page = await browser.newPage();
const failures = await (async () => {
  await page.goto(`http://127.0.0.1:${port}/laser.html`);
  await page.addScriptTag({path:bundle});
  return await page.evaluate(async () => {
    const {PROJECTS,getProject,buildProject}=window.DesignProjects;
    const G=window.LaserGeometry, failures=[];
    const fail=(m)=>failures.push(m);
    await G.loadFont();
    if(PROJECTS.filter(p=>p.level===0&&p.modes.includes('laser')).length<4) fail('expected at least 4 laser beginner choices');
    if(PROJECTS.filter(p=>p.level===0&&p.modes.includes('vinyl')).length<2) fail('expected at least 2 vinyl beginner choices');
    for(const card of PROJECTS){
      for(const vinyl of [false,true]){
        if(!card.modes.includes(vinyl?'vinyl':'laser')) continue;
        const a=buildProject(card.id,vinyl), b=buildProject(card.id,vinyl);
        if(a.learning.id!==card.id||a.learning.version!==1) fail(`${card.id}: learning identity`);
        if(a.items.length!==b.items.length||a.items.some((x,i)=>x.d!==b.items[i].d)) fail(`${card.id}/${vinyl?'vinyl':'laser'}: geometry not repeatable`);
        if(new Set(a.items.map(x=>x.id)).size!==a.items.length) fail(`${card.id}: duplicate item id`);
        if(a.items.some(x=>b.items.some(y=>x.id===y.id))) fail(`${card.id}: ids reused between builds`);
        const maxW=vinyl?296:600,maxH=vinyl?298:375;
        if(a.width!== (vinyl?296:(['club-logo','own-silhouette','stencil','place-sign'].includes(card.id)?220:card.id==='pattern-bookmark'?180:180)) && card.id!=='box-for-content') fail(`${card.id}: unexpected canvas width ${a.width}`);
        if(a.width>maxW||a.height>maxH||a.width<1||a.height<1) fail(`${card.id}/${vinyl?'vinyl':'laser'}: canvas bounds`);
        for(const x of a.items){
          if(!x.id||!x.name||!x.d||x.fillRule!=='nonzero') fail(`${card.id}: malformed item`);
          if(vinyl && x.op!=='cut') fail(`${card.id}: vinyl op ${x.op}`);
          if(!vinyl && !['cut','engrave-line','engrave-fill'].includes(x.op)) fail(`${card.id}: laser op ${x.op}`);
          if(!G.closed(x.d)) fail(`${card.id}: open production path ${x.name}`);
          const q=G.bounds(x.d);
          if(q.x < -.01 || q.y < -.01 || q.x+q.width > a.width+.01 || q.y+q.height > a.height+.01) fail(`${card.id}: item outside canvas ${x.name}`);
          if(x.text && (!Number.isFinite(x.text.size)||x.text.size<=0||x.text.value!=='DIT NAVN'&&x.text.value!=='HERHEN'&&!['TRÆ','LIM','VÆRKTØJ'].includes(x.text.value))) fail(`${card.id}: bad text metadata`);
        }
        if(a.learning.frameId && !a.items.some(x=>x.id===a.learning.frameId)) fail(`${card.id}: frame id missing`);
        if(a.learning.contentIds?.some(id=>!a.items.some(x=>x.id===id))) fail(`${card.id}: content id missing`);
        if(card.level===0&&!a.items.some(x=>x.text)) fail(`${card.id}: beginner project has no editable text`);
        if(card.id==='medal'){
          const frame=G.path(a.items[0].d);
          if(frame.contains([90,53])||!frame.contains([90,59])) fail('medal: ribbon slot is not a hole with a material bridge');
          for(const part of a.items.slice(1)){
            const q=G.path(part.d);if(frame.getIntersections(q).length)fail(`medal: ${part.name} crosses edge or ribbon slot`);q.remove();
          }
          frame.remove();
        }
        for(let i=0;i<a.items.length;i++) for(let j=i+1;j<a.items.length;j++) if(a.items[i].op==='cut'&&a.items[j].op==='cut'){
          const p=G.path(a.items[i].d), q=G.path(a.items[j].d);
          const hits=p.getIntersections(q).length; p.remove(); q.remove();
          if(hits) fail(`${card.id}: intersecting duplicate cut paths ${a.items[i].name}/${a.items[j].name}`);
        }
      }
    }
    return failures;
  });
})();
await browser.close(); await new Promise(resolve=>server.close(resolve));
if(failures.length){ console.error(failures.join('\n')); process.exit(1); }
console.log('design catalogue verification passed: beginner choices, all supported modes, repeatable geometry, bounds, paths, metadata and cut intersections');
