// Build the system viewer, then delegate the Folie assets to their app.
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {repoRoot, sourcePath} from './repository-layout.mjs';
import {buildFolie} from '../apps/folie/build.mjs';
import {assertPinnedDependencies,writeWebReceipt} from './web-build-receipt.mjs';

const root = repoRoot(import.meta.url);
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== '--dependencies')) {
  throw new Error('Use --dependencies with an existing pinned package directory');
}
const require = createRequire(import.meta.url);
const dependencies = args.length ? path.resolve(args[1]) : path.dirname(path.dirname(require.resolve('esbuild/package.json')));
const {build, version} = require(path.join(dependencies, 'esbuild'));
const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
if (version !== '0.28.2' || manifest.devDependencies.esbuild !== '0.28.2') {
  throw new Error('Pinned system esbuild dependency does not match');
}
await assertPinnedDependencies(root,dependencies);
const result = await build({absWorkingDir: root, entryPoints: [sourcePath('web/viewer.js', root)],
  nodePaths: [dependencies], outfile: sourcePath('web/vendor/viewer.js', root),
  bundle: true, minify: true,metafile:true});
await writeWebReceipt(root,'system',[result.metafile],path.dirname(sourcePath('web/vendor/viewer.js',root)),
  ['viewer.js'],['scripts/build_web.mjs']);
await buildFolie({root, dependencies});
