// Content receipts bind ignored browser outputs to their exact public sources.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';

const sha = data => createHash('sha256').update(data).digest('hex');

export async function assertPinnedDependencies(root, dependencies) {
  const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
  for (const [name, version] of Object.entries({...manifest.dependencies, esbuild:manifest.devDependencies.esbuild})) {
    const actual = JSON.parse(await readFile(path.join(dependencies, name, 'package.json'), 'utf8'));
    if (actual.version !== version) throw new Error(`Pinned browser dependency does not match: ${name}`);
  }
}

export async function writeWebReceipt(root, target, metas, outputDir, outputNames, builders) {
  const inputs = new Set(['REPOSITORY-LAYOUT.json', 'package.json', 'package-lock.json',
    'scripts/repository-layout.mjs', 'scripts/web-build-receipt.mjs', ...builders]);
  for (const meta of metas) {
    for (const name of Object.keys(meta.inputs)) {
      // esbuild records browser:false imports as virtual empty modules.
      // Their names are not paths (and may contain a colon on Windows).
      if (name.startsWith('(disabled):')) continue;
      const relative = path.relative(root, path.resolve(root,name));
      if (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`)
          && !relative.split(path.sep).includes('node_modules')) inputs.add(relative.split(path.sep).join('/'));
    }
  }
  const source_sha256 = {};
  for (const name of [...inputs].sort()) {
    source_sha256[name] = sha((await readFile(path.join(root,name))).toString('utf8').replace(/\r\n/g,'\n'));
  }
  const outputs = {};
  for (const name of [...outputNames].sort()) {
    const data = await readFile(path.join(outputDir,name));
    outputs[name] = {sha256:sha(data),size:data.length};
  }
  await mkdir(outputDir,{recursive:true});
  await writeFile(path.join(outputDir,'web-build-manifest.json'),
    JSON.stringify({schema:1,target,source_sha256,outputs},null,2)+'\n');
}
