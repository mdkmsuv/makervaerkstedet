// Reads FLUX's built-in material presets for one machine model from the installed
// Beam Studio and writes them as JSON for the laser station. Nothing is changed in
// Beam Studio; the app archive is only read.
//
//   node scripts/extract_beam_presets.mjs [model] [app.asar] [output.json]
//
// Default model fbb2 (Beambox II). Run again after a Beam Studio update.
import { openSync, readSync, writeFileSync, closeSync } from 'node:fs';
import path from 'node:path';

const model = process.argv[2] || 'fbb2';
const asarPath = process.argv[3] || 'C:/Program Files/Beam Studio/resources/app.asar';
const output = process.argv[4] || path.resolve('app', `laser_presets_${model}.json`);

function readAsar(file) {
  const fd = openSync(file, 'r');
  const head = Buffer.alloc(16);
  readSync(fd, head, 0, 16, 0);
  const headerSize = head.readUInt32LE(4);
  const jsonSize = head.readUInt32LE(12);
  const json = Buffer.alloc(jsonSize);
  readSync(fd, json, 0, jsonSize, 16);
  const header = JSON.parse(json.toString('utf8'));
  const base = 8 + headerSize;
  return {
    read(name) {
      let node = header;
      for (const part of name.split('/').filter(Boolean)) node = node.files?.[part];
      if (!node || node.files || node.unpacked) throw new Error(`Findes ikke i app.asar: ${name}`);
      const data = Buffer.alloc(node.size);
      readSync(fd, data, 0, node.size, base + Number(node.offset));
      return data;
    },
    close: () => closeSync(fd),
  };
}

const asar = readAsar(asarPath);
const version = JSON.parse(asar.read('package.json').toString('utf8')).version;
const map = JSON.parse(asar.read('public/js/dist/main.bundle.js.map').toString('utf8'));
asar.close();

const source = (suffix) => {
  const index = map.sources.findIndex((s) => s.endsWith(suffix));
  if (index < 0 || !map.sourcesContent?.[index]) throw new Error(`Kildefil mangler: ${suffix}`);
  return map.sourcesContent[index];
};

const presetSource = source('/app/constants/presets.ts');
const danish = source('/app/lang/da.ts');

// Each top-level preset: "  key: {" … up to the next top-level key.
const blocks = [...presetSource.matchAll(/^ {2}([a-z0-9_]+): \{([\s\S]*?)(?=^ {2}[a-z0-9_]+: \{|^\};)/gm)];
const pattern = new RegExp(`${model}: \\{ \\[LayerModule\\.LASER_UNIVERSAL\\]: \\{([^}]*)\\} \\}`);
const presets = {};
for (const [, key, body] of blocks) {
  const hit = body.match(pattern);
  if (!hit) continue;
  const values = Object.fromEntries([...hit[1].matchAll(/(\w+): ([\d.]+)/g)].map(([, k, v]) => [k, Number(v)]));
  if (!Number.isFinite(values.power) || !Number.isFinite(values.speed)) continue;
  // The metric names come last in da.ts; the imperial ones (1/8″ …) first.
  const names = [...danish.matchAll(new RegExp(`^\\s+${key}: '([^']+)'`, 'gm'))].map((m) => m[1]);
  presets[key] = { name: names.at(-1) || key, power: values.power, speed: values.speed, repeat: values.repeat || 1 };
}

if (!Object.keys(presets).length) throw new Error(`Ingen forudindstillinger fundet for ${model}.`);
const result = {
  model,
  source: `Beam Studio ${version}, app/constants/presets.ts og lang/da.ts`,
  units: { power: '%', speed: 'mm/s' },
  presets,
};
writeFileSync(output, JSON.stringify(result, null, 2) + '\n', 'utf8');
console.log(`${Object.keys(presets).length} forudindstillinger for ${model} fra Beam Studio ${version} → ${output}`);
