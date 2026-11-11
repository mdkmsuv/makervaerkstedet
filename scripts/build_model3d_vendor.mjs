// Only the standalone model3d assets are built here. Existing workshops stay untouched.
import {readFile, writeFile, mkdir, copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';

const root = fileURLToPath(new URL('../', import.meta.url));
const vendor = path.join(root, 'web/vendor');
const licenses = path.join(root, 'web/assets/licenses/model3d');
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== '--dependencies')) throw new Error('Use --dependencies with an existing pinned package directory');
const dependencies = args.length ? path.resolve(args[1]) : path.join(root, 'node_modules');
const sourcePath = name => name.startsWith('node_modules/') ? path.join(dependencies, name.slice(13)) : path.join(root, name);
const require = createRequire(import.meta.url);
const {build, version: esbuildVersion} = require(path.join(dependencies, 'esbuild'));
const integrity = 'sha512-WWsme5HabwLHVcWPHT9m+s7mdmDVSEvCny+uXH1E7jEc8FbFUgM75C3Dh6NVw57Eeh3Ow7+xHCuXfxDMOemi9A==';
const pins = {
  'manifold.js': 'ca3c8a88df63bb3083013d1eb7f700bf23216594f6e98a2566e34f55d7341ce8',
  'manifold.wasm': '73e3b419ad31294f6b1cc478173944e50efc4a34bfdeeff0107bdfc12975f11c',
  LICENSE: 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4',
};
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const json = async name => JSON.parse(await readFile(sourcePath(name), 'utf8'));
const manifest = await json('package.json');
const lock = await json('package-lock.json');
const manifold = await json('node_modules/manifold-3d/package.json');
const three = await json('node_modules/three/package.json');
const opentype = await json('node_modules/opentype.js/package.json');
if (manifest.dependencies['manifold-3d'] !== '3.5.4' || manifold.version !== '3.5.4'
    || lock.packages['node_modules/manifold-3d'].integrity !== integrity
    || manifest.dependencies.three !== '0.183.2' || three.version !== '0.183.2'
    || manifest.dependencies['opentype.js'] !== '2.0.0' || opentype.version !== '2.0.0'
    || lock.packages['node_modules/opentype.js'].integrity !== 'sha512-kCyjv6xdDY1W/jLWZ/L3QhhTlKUqDZMQ5+Jdlw12b3dXkKNpYBqqlMMj0YDQPShWFTMwgZI1hG14kN3XUDSg/A=='
    || esbuildVersion !== '0.28.2') throw new Error('Pinned model3d dependencies do not match');

const textPins = {
  'node_modules/opentype.js/dist/opentype.mjs': 'e139522ac432bb38fb4878d772012f0ad351993c36d7815d65a9890fb3fe68fb',
  'node_modules/opentype.js/LICENSE': 'a5b87c9bc191e7f9c06b07bd7cbbed3ed846dfdb50e859484b2c245aa1ab2e61',
  'web/assets/model3d/Metrophobic-Regular.ttf': '34e4ac3257307c28e6dd14f5d77bb296211f0ddd217607228a5375416ed8ac58',
  'web/assets/model3d/Metrophobic-OFL.txt': '4eaa1fae0ddbce35cfbc42ec1079f7fd78e4146b1d322630600af07a1e3212a9',
};
for (const [name, expected] of Object.entries(textPins)) {
  if (hash(await readFile(sourcePath(name))) !== expected) throw new Error(`Text source pin mismatch: ${name}`);
}

const sources = {};
for (const [name, expected] of Object.entries(pins)) {
  sources[name] = await readFile(path.join(dependencies, 'manifold-3d', name));
  if (hash(sources[name]) !== expected) throw new Error(`Manifold source pin mismatch: ${name}`);
}
const entries = [
  ['web/model3d/app.js', 'model3d-app.js'],
  ['web/model3d/geometry-worker.js', 'model3d-geometry-worker.js'],
];
// Reading every entry first gives a useful failure before any partial output.
for (const [entry] of entries) await readFile(path.join(root, entry));
const generated = [];
for (const [entry, output] of entries) {
  const result = await build({absWorkingDir: root, entryPoints: [entry], nodePaths: [dependencies],
    outfile: path.join(vendor, output), bundle: true, format: 'esm',
    platform: 'browser', target: 'es2022', minify: true, sourcemap: false,
    legalComments: 'external', external: ['node:*'], write: false,
    define: {'process.env.NODE_ENV': '"production"'}});
  generated.push(...result.outputFiles);
}
await mkdir(vendor, {recursive: true});
await mkdir(licenses, {recursive: true});
for (const file of generated) await writeFile(file.path, file.contents);
await writeFile(path.join(vendor, 'model3d-manifold.wasm'), sources['manifold.wasm']);
await writeFile(path.join(licenses, 'manifold-3d-LICENSE.txt'), sources.LICENSE);
await copyFile(path.join(dependencies, 'three/LICENSE'), path.join(licenses, 'three-LICENSE.txt'));
await copyFile(path.join(dependencies, 'opentype.js/LICENSE'), path.join(licenses, 'opentype-LICENSE.txt'));
await copyFile(path.join(root, 'web/assets/model3d/Metrophobic-OFL.txt'), path.join(licenses, 'Metrophobic-OFL.txt'));
const sourceFiles = ['package.json', 'package-lock.json', 'scripts/build_model3d_vendor.mjs',
  'web/model3d.html', 'web/model3d.css'];
const {readdir} = await import('node:fs/promises');
async function collect(directory) {
  for (const entry of await readdir(path.join(root, directory), {withFileTypes: true})) {
    const name = directory + '/' + entry.name;
    if (entry.isDirectory()) await collect(name);
    else if (entry.isFile() && entry.name.endsWith('.js')) sourceFiles.push(name);
  }
}
await collect('web/model3d');
const source_sha256 = {};
for (const name of sourceFiles.sort()) source_sha256[name] = hash(await readFile(path.join(root, name)));
const license_sha256 = {};
for (const name of ['manifold-3d-LICENSE.txt','three-LICENSE.txt','opentype-LICENSE.txt','Metrophobic-OFL.txt']) license_sha256[name] = hash(await readFile(path.join(licenses, name)));
const outputs = {};
for (const name of generated.map(file => path.basename(file.path)).concat('model3d-manifold.wasm')) {
  const bytes = await readFile(path.join(vendor, name));
  outputs[name] = {sha256: hash(bytes), size: bytes.length};
}
await writeFile(path.join(vendor, 'model3d-vendor-manifest.json'), JSON.stringify({
  schema: 1, source_sha256, license_sha256, manifold: {version: '3.5.4', integrity, license: 'Apache-2.0',
    source: 'https://github.com/elalish/manifold/releases/tag/v3.5.4'},
  three: {version: '0.183.2', license: 'MIT'}, esbuild: esbuildVersion, outputs,
  text: {opentype: '2.0.0', font: 'Metrophobic', fontKey: 'mkv-sans',
    fontUrl: '../assets/model3d/Metrophobic-Regular.ttf', input_sha256: textPins,
    source: 'https://github.com/googlefonts/MetrophobicFont/tree/d4da54632ddd53631332a5a469183c887b4ea3e1'},
  runtime_network: 'same-origin local files only',
}, null, 2) + '\n');
console.log(JSON.stringify({model3d: 'built', outputs: Object.keys(outputs)}));
