// Refresh the checked-in pictograms from the same default geometry used by the editor.
import {readFile, writeFile} from 'node:fs/promises';
import {crc32, deflateSync} from 'node:zlib';
import Module from 'manifold-3d';
import opentype from 'opentype.js';
import {Vector3} from 'three';
import {catalogChildren} from './web/model3d/catalog-children.js';
import {CATALOG_RECIPES} from './web/model3d/catalog-recipes.js';
import {buildConnector} from './web/model3d/connector-profiles.js';
import {DEFAULT_FONT, FONT_BY_KEY} from './web/model3d/fonts.js';
import {defaultGamePiece, gamePieceChildren} from './web/model3d/game-piece.js';
import {createGeometryKernel} from './web/model3d/geometry.js';
import {SceneModel} from './web/model3d/scene.js';
import {SHAPE_LIBRARY, insertShapePreset} from './web/model3d/shape-library.js';
import {defaultMechanicalPart} from './web/model3d/mechanical-parts.js';

const native = ['box', 'cylinder', 'sphere', 'cone', 'pyramid', 'roof', 'wedge', 'halfsphere', 'cylinder-6',
  'star', 'heart', 'tube', 'text', 'cylinder-hole', 'box-hole'];
const presets = Object.entries(SHAPE_LIBRARY).flatMap(([category, cards]) => cards.map(preset => ({category, ...preset})));
const tiles = [...native, ...presets.map(({id}) => id)];
const columns = 8, rows = Math.ceil(tiles.length / columns), cellWidth = 96, cellHeight = 80;
const width = columns * cellWidth, height = rows * cellHeight;
const image = Buffer.alloc(width * height * 4);
const media = path => new URL(`./web/assets/model3d/${path}`, import.meta.url);
const fontBytes = await readFile(media(FONT_BY_KEY.get(DEFAULT_FONT).file));
const font = opentype.parse(Uint8Array.from(fontBytes).buffer);
const gameData = JSON.parse(await readFile(media('game-piece-data.json'), 'utf8'));
const wasm = await Module();
wasm.setup();
const kernel = createGeometryKernel(wasm, {fonts: {[DEFAULT_FONT]: font}});

function addMesh(model, assets, shape, mode = shape.mode) {
  const asset = 'stl-form-thumbnail';
  assets.set(asset, shape.positions);
  model.add('mesh', {size: shape.size, asset, mode});
}

function meshesFor(id, preset) {
  const model = new SceneModel(), assets = new Map();
  if (!preset) {
    const [kind, option] = id.split('-');
    model.add(kind, {params: option === '6' ? {sides: 6} : {}, mode: option === 'hole' ? 'hole' : 'solid'});
  } else if (preset.configure === 'game-piece') {
    const {name, layout} = defaultGamePiece(gameData);
    const children = gamePieceChildren(layout, assets);
    model.addMeshGroup(name, children, [0, 0, layout.size[2] / 2]);
  } else if (preset.configure === 'connector') {
    addMesh(model, assets, buildConnector(preset.settings));
  } else if (preset.configure === 'catalog-shape') {
    const recipe = CATALOG_RECIPES.get(id), shape = recipe.build(recipe.defaults);
    if (shape.parts) {
      const children = catalogChildren(shape, recipe.color, assets);
      model.addMeshGroup(recipe.label, children, [0, 0, shape.size[2] / 2], null, shape.op);
    } else addMesh(model, assets, shape);
  } else if (preset.configure) {
    addMesh(model, assets, defaultMechanicalPart(preset.configure).mesh);
  } else insertShapePreset(model, preset, [0, 0]);

  const meshes = kernel.evaluateScene(model.data, assets).meshes;
  return meshes.flatMap(mesh => {
    if (mesh.mode === 'hole') return [{...mesh, color: '#bbb2d3'}];
    const paint = preset?.category === 'hardware' ? '#7b8794' : '#c2555d';
    return mesh.paints?.length ? mesh.paints.map(layer => ({...layer, color: layer.color === '#c92e35' ? paint : layer.color}))
      : [{...mesh, color: paint}];
  });
}

// Orthographic projection and a depth buffer retain bores and overlapping parts.
const forward = new Vector3(.72, -1, 1.12).normalize();
const right = new Vector3(-forward.y, forward.x, 0).normalize();
const up = new Vector3().crossVectors(forward, right);
const light = new Vector3(-.55, -.3, 1).normalize();
const edge = (a, b, x, y) => (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]);

function draw(index, meshes) {
  const min = [Infinity, Infinity], max = [-Infinity, -Infinity], point = new Vector3();
  const depth = new Float32Array(cellWidth * cellHeight).fill(-Infinity);
  const originX = index % columns * cellWidth, originY = Math.floor(index / columns) * cellHeight;
  const projected = meshes.map(({positions, indices, color, mode}) => {
    const points = [];
    for (let i = 0; i < positions.length; i += 3) {
      point.fromArray(positions, i);
      const position = [point.dot(right), -point.dot(up), point.dot(forward)];
      for (let axis = 0; axis < 2; axis++) {
        min[axis] = Math.min(min[axis], position[axis]);
        max[axis] = Math.max(max[axis], position[axis]);
      }
      points.push(position);
    }
    const tone = color.match(/[a-f\d]{2}/gi).map(pair => .84 * parseInt(pair, 16) + .16 * 168);
    return {points, positions, indices, tone, hole: mode === 'hole'};
  });
  const scale = Math.min((cellWidth - 16) / (max[0] - min[0]), (cellHeight - 14) / (max[1] - min[1]));
  const cx = cellWidth / 2 - (min[0] + max[0]) * scale / 2;
  const cy = cellHeight / 2 - (min[1] + max[1]) * scale / 2;
  const normal = new Vector3(), second = new Vector3(), third = new Vector3();
  let painted = 0;
  for (const {points, positions, indices, tone, hole} of projected) {
    for (const p of points) { p[0] = p[0] * scale + cx; p[1] = p[1] * scale + cy; }
    for (let i = 0; i < indices.length; i += 3) {
      const ia = indices[i], ib = indices[i + 1], ic = indices[i + 2];
      const a = points[ia], b = points[ib], c = points[ic], area = edge(a, b, c[0], c[1]);
      if (area >= -1e-7) continue;
      point.fromArray(positions, ia * 3);
      second.fromArray(positions, ib * 3).sub(point);
      third.fromArray(positions, ic * 3).sub(point);
      normal.crossVectors(second, third).normalize();
      const shade = .67 + .46 * Math.max(0, normal.dot(light));
      const rgb = tone.map(channel => Math.min(255, Math.round(channel * shade)));
      for (let y = Math.floor(Math.min(a[1], b[1], c[1])); y <= Math.ceil(Math.max(a[1], b[1], c[1])); y++) {
        for (let x = Math.floor(Math.min(a[0], b[0], c[0])); x <= Math.ceil(Math.max(a[0], b[0], c[0])); x++) {
          const u = edge(b, c, x + .5, y + .5) / area, v = edge(c, a, x + .5, y + .5) / area, w = 1 - u - v;
          if (Math.min(u, v, w) < -1e-7) continue;
          const current = u * a[2] + v * b[2] + w * c[2], pixel = y * cellWidth + x;
          if (current <= depth[pixel]) continue;
          depth[pixel] = current;
          const stripe = (x - y + cellHeight) % 12;
          const fill = !hole ? rgb : stripe < 3 ? [65, 48, 91] : stripe < 5 ? [224, 217, 236] : rgb;
          image.set([...fill, 255], ((originY + y) * width + originX + x) * 4);
          painted++;
        }
      }
    }
  }
  if (!painted) throw Error(`Empty form thumbnail: ${tiles[index]}`);
}

function pngChunk(type, bytes) {
  const chunk = Buffer.concat([Buffer.alloc(4), Buffer.from(type), bytes, Buffer.alloc(4)]);
  chunk.writeUInt32BE(bytes.length);
  chunk.writeUInt32BE(crc32(chunk.subarray(4, -4)), chunk.length - 4);
  return chunk;
}

const byId = new Map(presets.map(preset => [preset.id, preset]));
tiles.forEach((id, i) => draw(i, meshesFor(id, byId.get(id))));
const header = Buffer.from([0, 0, 0, 0, 0, 0, 0, 0, 8, 6, 0, 0, 0]);
header.writeUInt32BE(width);
header.writeUInt32BE(height, 4);
const stride = 1 + width * 4, scanlines = Buffer.alloc(stride * height);
for (let y = 0; y < height; y++) image.copy(scanlines, y * stride + 1, y * width * 4, (y + 1) * width * 4);
await writeFile(media('form-thumbnails.png'), Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), pngChunk('IHDR', header),
  pngChunk('IDAT', deflateSync(scanlines, {level: 9})), pngChunk('IEND', Buffer.alloc(0))]));
await writeFile(new URL('./web/model3d/form-thumbnail-slots.js', import.meta.url),
  `// Generated by render-form-thumbnails.mjs\nexport const thumbnailColumns = ${columns};\nexport const thumbnailRows = ${rows};\n`
  + `export const thumbnailSlots = new Map(${JSON.stringify(tiles)}.map((id, index) => [id, index]));\n`);
console.log(`Rendered ${tiles.length} form thumbnails.`);
