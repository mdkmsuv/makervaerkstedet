// Folie owns its source graph and generated browser assets.
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {repoRoot, sourcePath} from '../../scripts/repository-layout.mjs';
import {assertPinnedDependencies,writeWebReceipt} from '../../scripts/web-build-receipt.mjs';

export async function buildFolie({root = repoRoot(import.meta.url), dependencies} = {}) {
  const require = createRequire(import.meta.url);
  dependencies ??= path.dirname(path.dirname(require.resolve('esbuild/package.json')));
  const {build, version} = require(path.join(dependencies, 'esbuild'));
  const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
  if (version !== '0.28.2' || manifest.devDependencies.esbuild !== '0.28.2') {
    throw new Error('Pinned Folie esbuild dependency does not match');
  }
  await assertPinnedDependencies(root,dependencies);
  const metas = [];
  for (const name of ['laser', 'laser-trace-worker']) {
    const result = await build({absWorkingDir: root, entryPoints: [sourcePath(`web/${name}.js`, root)],
      nodePaths: [dependencies], outfile: sourcePath(`web/vendor/${name}.js`, root),
      bundle: true, minify: true,metafile:true});
    metas.push(result.metafile);
  }
  await writeWebReceipt(root,'folie',metas,path.dirname(sourcePath('web/vendor/laser.js',root)),
    ['laser.js','laser-trace-worker.js'],['apps/folie/build.mjs']);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.length && (args.length !== 2 || args[0] !== '--dependencies')) {
    throw new Error('Use --dependencies with an existing pinned package directory');
  }
  await buildFolie({dependencies: args.length ? path.resolve(args[1]) : undefined});
}
