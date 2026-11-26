import assert from 'node:assert/strict';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {before, after, test} from 'node:test';
import {build} from 'esbuild';
import {strFromU8, strToU8, unzipSync, zipSync} from 'fflate';
import {chromium} from 'playwright';
import {export3mf, MAX_3MF_BYTES} from '../apps/3dprint/web/model3d/three-mf.js';

const root = fileURLToPath(new URL('../', import.meta.url));
const CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02';
const OPC_RELS = 'http://schemas.openxmlformats.org/package/2006/relationships';
const OPC_TYPES = 'http://schemas.openxmlformats.org/package/2006/content-types';
const START = 'http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel';
const TYPE = 'application/vnd.ms-package.3dmanufacturing-3dmodel+xml';
const PRODUCTION = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06';
const tetraPositions = Float32Array.from([0,0,0, 1,0,0, 0,1,0, 0,0,1]);
const tetraFaces = Uint32Array.from([0,2,1, 0,1,3, 0,3,2, 1,2,3]);
const tetra = '<mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/><vertex x="0" y="0" z="1"/></vertices><triangles>'
  + '<triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/><triangle v1="0" v2="3" v3="2"/><triangle v1="1" v2="2" v3="3"/></triangles></mesh>';
const model = (objects, items, attrs = '') => `<model xmlns="${CORE}" ${attrs}><resources>${objects}</resources><build>${items}</build></model>`;
const singleModel = model(`<object id="1">${tetra}</object>`, '<item objectid="1"/>');

function container(xml = singleModel, target = '/3D/Placering.model', extra = {}, targetMode = '') {
  return zipSync({
    '[Content_Types].xml': strToU8(`<Types xmlns="${OPC_TYPES}"><Default Extension="model" ContentType="${TYPE}"/></Types>`),
    '_rels/.rels': strToU8(`<Relationships xmlns="${OPC_RELS}"><Relationship Id="r" Type="${START}" Target="${target}"${targetMode}/></Relationships>`),
    '3D/Placering.model': strToU8(xml),
    ...extra,
  });
}

let browser, page;
before(async () => {
  // Use the actual browser XML parser used by the app without adding a second XML dependency.
  const {outputFiles} = await build({
    stdin: {contents: "export {read3mf} from './three-mf.js'", resolveDir: path.join(root, 'apps/3dprint/web/model3d')},
    bundle: true, format: 'iife', globalName: 'threeMF', platform: 'browser', write: false,
  });
  browser = await chromium.launch({headless: true, channel: 'chrome'});
  page = await browser.newPage();
  await page.addScriptTag({content: outputFiles[0].text});
});
after(async () => { await browser?.close(); });

const read = bytes => page.evaluate(bytes => {
  const result = threeMF.read3mf(Uint8Array.from(bytes));
  return {...result, parts: result.parts.map(part => ({...part, positions: Array.from(part.positions)}))};
}, Array.from(bytes));
const close = (actual, expected) => assert(Math.abs(actual - expected) < .0001, `${actual} != ${expected}`);
const closeVector = (actual, expected) => actual.forEach((value, index) => close(value, expected[index]));

function signedVolume(positions) {
  let result = 0;
  for (let i = 0; i < positions.length; i += 9) {
    const [a,b,c,d,e,f,g,h,j] = positions.slice(i, i + 9);
    result += a * (e * j - f * h) + b * (f * g - d * j) + c * (d * h - e * g);
  }
  return result / 6;
}

test('primary relationship, metric conversion, nested rotation and winding survive assembly import', async () => {
  const objects = '<basematerials id="9"><base name="Blå" displaycolor="#2F6FB5FF"/></basematerials>'
    + `<object id="1" name="Del &amp; del" type="model" pid="9" pindex="0">${tetra}</object>`
    + '<object id="2"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 1 2 3"/>'
    + '<component objectid="1" transform="-1 0 0 0 1 0 0 0 1 4 2 3"/></components></object>';
  const xml = model(objects, '<item objectid="2" transform="0 1 0 -1 0 0 0 0 1 10 0 0"/><item objectid="1" printable="false"/>', 'unit="inch"');
  const loaded = await read(container(xml, '/3D/Placering.model', {'3D/3dmodel.model': strToU8('<wrong/>')}));
  assert.equal(loaded.parts.length, 2);
  assert(loaded.parts.every(part => part.color === '#2f6fb5' && part.name === 'Del & del'));
  closeVector(loaded.bounds.min, [177.8, 25.4, 76.2]);
  closeVector(loaded.bounds.max, [203.2, 101.6, 101.6]);
  closeVector(loaded.parts[0].size, [25.4, 25.4, 25.4]);
  closeVector(loaded.parts[0].center, [190.5, 38.1, 88.9]);
  for (const part of loaded.parts) assert(Math.abs(signedVolume(part.positions) - 25.4 ** 3 / 6) < .01);
});

test('Core export has the required OPC files and preserves multiple objects, colours and placement', async () => {
  const moved = tetraPositions.map((value, index) => value + [5, -3, 2][index % 3]);
  const bytes = export3mf([{positions: tetraPositions, indices: tetraFaces, color: '#2f6fb5', name: 'Et <emne>'},
    {positions: moved, indices: tetraFaces, name: 'Anden del'}]);
  assert(bytes instanceof Uint8Array && bytes.length <= MAX_3MF_BYTES);
  const zipped = unzipSync(bytes);
  assert(zipped['[Content_Types].xml'] && zipped['_rels/.rels']);
  assert.match(strFromU8(zipped['_rels/.rels']), /3dmanufacturing\/2013\/01\/3dmodel/);
  const loaded = await read(bytes);
  assert.deepEqual(loaded.parts.map(({name, color}) => [name, color]), [['Et <emne>', '#2f6fb5'], ['Anden del', undefined]]);
  closeVector(loaded.bounds.min, [0, -3, 0]);
  closeVector(loaded.bounds.max, [6, 1, 3]);
  assert(loaded.parts.every(part => signedVolume(part.positions) > 0));
});

test('invalid XML, non-local payloads, bad indices, invalid transforms and cyclic components are rejected', async () => {
  const cyclic = model('<object id="1"><components><component objectid="2"/></components></object><object id="2"><components><component objectid="1"/></components></object>', '<item objectid="1"/>');
  for (const bytes of [
    container('<modell>'),
    container(singleModel.slice(0, -'</model>'.length)),
    container('<!DOCTYPE model>' + singleModel),
    container(singleModel, 'https://example.org/model.model', {}, ' TargetMode="External"'),
    container(singleModel, '/3D/%2e%2e/Placering.model'),
    container(singleModel.replace('v3="1"', 'v3="81"')),
    container(singleModel.replace('<item objectid="1"/>', '<item objectid="1" transform="1 0 0 0 1 0 0 0 0 0 0 0"/>')),
    container(cyclic),
    new Uint8Array([1, 2, 3]),
  ]) await assert.rejects(read(bytes), /3MF/);
});

test('compressed limit and malformed advertised expansion are rejected before parsing a model', async () => {
  await assert.rejects(page.evaluate(max => threeMF.read3mf(new Uint8Array(max + 1)), MAX_3MF_BYTES), /3MF/);
  const bytes = container(), view = new DataView(bytes.buffer);
  const centralOffset = view.getUint32(bytes.length - 22 + 16, true);
  view.setUint32(centralOffset + 24, 65 * 1024 * 1024, true);
  await assert.rejects(read(bytes), /3MF/);
});

test('Production assembles distinct model parts by their relationship and ignores a child build', async () => {
  const sourcePath = '/3D/Objects/part%20one.model';
  const root = model(`<object id="1"><components><component p:path="${sourcePath}" objectid="1" transform="1 0 0 0 1 0 0 0 1 2 0 0"/></components></object>`,
    `<item objectid="1"/><item p:path="${sourcePath}" objectid="2"/>`, `xmlns:p="${PRODUCTION}" requiredextensions="p" unit="inch"`);
  const source = model(`<object id="1" name="Barn">${tetra}</object><object id="2"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 0 3 0"/></components></object>`,
    '<item objectid="999"/>', 'unit="centimeter"');
  const rels = `<Relationships xmlns="${OPC_RELS}"><Relationship Id="part" Type="${START}" Target="Objects/part%20one.model"/></Relationships>`;
  const extra = {'3D/Objects/part%20one.model': strToU8(source), '3D/_rels/Placering.model.rels': strToU8(rels)};
  const result = await read(container(root, undefined, extra));
  assert.equal(result.parts.length, 2);
  closeVector(result.parts[0].center, [55.8, 5, 5]);
  closeVector(result.parts[1].center, [5, 35, 5]);
  assert.equal(result.parts[0].name, 'Barn');

  await assert.rejects(read(container(root, undefined, {'3D/Objects/part%20one.model': strToU8(source)})), /3MF/);
  const cycle = model(`<object id="1"><components><component objectid="1" p:path="/3D/Placering.model"/></components></object>`, '', `xmlns:p="${PRODUCTION}"`);
  await assert.rejects(read(container(root, undefined, {...extra, '3D/Objects/part%20one.model': strToU8(cycle)})), /3MF/);
});
