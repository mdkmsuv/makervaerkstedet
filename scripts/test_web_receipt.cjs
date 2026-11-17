'use strict';
// Browser-disabled imports are esbuild virtual modules, never source files.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {build} = require('esbuild');

(async () => {
  const {writeWebReceipt} = await import(pathToFileURL(path.join(__dirname, 'web-build-receipt.mjs')));
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'mkv-receipt-test-'));
  try {
    await fs.mkdir(path.join(root, 'scripts'));
    for (const name of ['REPOSITORY-LAYOUT.json', 'package-lock.json',
      'scripts/repository-layout.mjs', 'scripts/web-build-receipt.mjs']) {
      await fs.writeFile(path.join(root, name), '{}\n');
    }
    await fs.writeFile(path.join(root, 'package.json'), JSON.stringify({browser:{'./excluded.js':false}}));
    await fs.writeFile(path.join(root, 'entry.js'), "import './excluded.js'; console.log('browser');\n");
    await fs.writeFile(path.join(root, 'excluded.js'), "throw new Error('server-only');\n");
    const output = path.join(root, 'out');
    const result = await build({absWorkingDir:root, entryPoints:['entry.js'],
      outfile:path.join(output, 'bundle.js'), bundle:true, platform:'browser', metafile:true});
    assert(Object.keys(result.metafile.inputs).some(name => name.startsWith('(disabled):')));
    await writeWebReceipt(root, 'fixture', [result.metafile], output, ['bundle.js'], []);
    const receipt = JSON.parse(await fs.readFile(path.join(output, 'web-build-manifest.json'), 'utf8'));
    assert(receipt.source_sha256['entry.js'], 'The real entry must remain bound');
    assert(!Object.keys(receipt.source_sha256).some(name => name.includes('(disabled):')));
    assert(!receipt.source_sha256['excluded.js'], 'Disabled code is not part of the browser build');
    await assert.rejects(writeWebReceipt(root, 'fixture', [{inputs:{'missing-real-source.js':{}}}],
      output, ['bundle.js'], []), {code:'ENOENT'});
    console.log('Browser receipt: virtual imports excluded; real missing inputs rejected.');
  } finally {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert(path.basename(root).startsWith('mkv-receipt-test-'));
    await fs.rm(root, {recursive:true, force:true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
