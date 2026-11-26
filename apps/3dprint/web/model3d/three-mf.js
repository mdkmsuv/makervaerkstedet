import {Inflate, zipSync} from 'fflate';
import {Matrix4, Vector3} from 'three';

// 3MF Core and Open Packaging Conventions. Model data stays entirely local.
// https://github.com/3MFConsortium/spec_core/blob/master/3MF%20Core%20Specification.md
const CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02';
const PRODUCTION = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06';
const RELS = 'http://schemas.openxmlformats.org/package/2006/relationships';
const TYPES = 'http://schemas.openxmlformats.org/package/2006/content-types';
const START = 'http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel';
const MODEL_TYPE = 'application/vnd.ms-package.3dmanufacturing-3dmodel+xml';
const MODEL_PATH = '3D/3dmodel.model';
const CONTENT_TYPES = '[Content_Types].xml';
const ROOT_RELS = '_rels/.rels';
const MIB = 1024 * 1024;
export const MAX_3MF_BYTES = 25 * MIB;
const MAX_PACKAGE = 64 * MIB, MAX_MODEL = 48 * MIB, MAX_REL = 256 * 1024;
const MAX_ENTRIES = 512, MAX_PIECES = 512, MAX_GEOMETRY = 500_000, MAX_REFERENCES = 8192, MAX_DEPTH = 32;
const MAX_POSITION = 1_000_000;
const UNITS = new Map([['micron', .001], ['millimeter', 1], ['centimeter', 10], ['inch', 25.4], ['foot', 304.8], ['meter', 1000]]);
const utf8 = new TextDecoder('utf-8', {fatal: true});
const encode = new TextEncoder();
const badZip = '3MF-filen indeholder et ugyldigt ZIP-arkiv.';
const badXml = '3MF-filen indeholder ugyldig XML.';
const badModel = '3MF-filen indeholder en ugyldig model eller placering.';
const tooLarge = '3MF-filen er for stor eller indeholder for mange dele.';

function need(condition, message = badModel) {
  if (!condition) throw new Error(message);
}

function archivePath(name, allowLeadingSlash = false) {
  need(typeof name === 'string' && name.length > 0 && name.length <= 1024, badZip);
  if (allowLeadingSlash && name.startsWith('/')) name = name.slice(1);
  return name.split('/').map(segment => {
    let decoded;
    try { decoded = decodeURIComponent(segment); } catch { throw new Error(badZip); }
    need(decoded && decoded !== '.' && decoded !== '..' && !/[\\/:?#\u0000-\u001f\u007f]/u.test(decoded), badZip);
    return decoded.toLowerCase();
  }).join('/');
}

function zipDirectory(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const word = offset => view.getUint16(offset, true), long = offset => view.getUint32(offset, true);
  const wide = offset => {
    need(long(offset + 4) === 0, tooLarge);
    return long(offset);
  };
  let end = bytes.length - 22;
  const first = Math.max(0, end - 65535);
  while (end >= first && !(long(end) === 0x06054b50 && end + 22 + word(end + 20) === bytes.length)) end--;
  need(end >= first && !word(end + 4) && !word(end + 6), badZip);
  let count = word(end + 10), offset = long(end + 16), length = long(end + 12), directoryEnd = end;
  if (end >= 20 && long(end - 20) === 0x07064b50) {
    const locator = end - 20, zip64 = wide(locator + 8);
    need(!long(locator + 4) && long(locator + 16) === 1 && zip64 + 56 <= locator && long(zip64) === 0x06064b50, badZip);
    need(wide(zip64 + 4) >= 44 && zip64 + 12 + wide(zip64 + 4) === locator && !long(zip64 + 16) && !long(zip64 + 20), badZip);
    count = wide(zip64 + 32);
    need(wide(zip64 + 24) === count, badZip);
    length = wide(zip64 + 40); offset = wide(zip64 + 48); directoryEnd = zip64;
  } else need(word(end + 8) === count, badZip);
  need(count > 0 && count <= MAX_ENTRIES, tooLarge);
  need(offset + length === directoryEnd, badZip);
  const entries = new Map(), names = new Set();
  let p = offset, total = 0;
  for (let index = 0; index < count; index++) {
    need(p + 46 <= directoryEnd && long(p) === 0x02014b50, badZip);
    const flags = word(p + 8), compression = word(p + 10), crc = long(p + 16);
    need(!(flags & 0x2041) && (compression === 0 || compression === 8), badZip);
    let packed = long(p + 20), expanded = long(p + 24), start = long(p + 42), disc = word(p + 34);
    const nameStart = p + 46, extraStart = nameStart + word(p + 28), extraEnd = extraStart + word(p + 30);
    const next = extraEnd + word(p + 32);
    need(next <= directoryEnd, badZip);
    if ([packed, expanded, start].includes(0xffffffff) || disc === 0xffff) {
      let extra = extraStart;
      while (extra + 4 <= extraEnd && word(extra) !== 1) extra += 4 + word(extra + 2);
      need(extra + 4 <= extraEnd && word(extra) === 1, badZip);
      const extraSize = word(extra + 2), end64 = extra + 4 + extraSize;
      need(end64 <= extraEnd, badZip);
      let field = extra + 4;
      const nextWide = () => { need(field + 8 <= end64, badZip); const value = wide(field); field += 8; return value; };
      if (expanded === 0xffffffff) expanded = nextWide();
      if (packed === 0xffffffff) packed = nextWide();
      if (start === 0xffffffff) start = nextWide();
      if (disc === 0xffff) { need(field + 4 <= end64, badZip); disc = long(field); }
    }
    need(!disc && start + 30 <= offset && packed <= offset - start - 30 && (compression || packed === expanded), badZip);
    const rawName = utf8.decode(bytes.subarray(nameStart, extraStart));
    const directory = rawName.endsWith('/'), name = archivePath(directory ? rawName.slice(0, -1) : rawName);
    need(!names.has(name) && (!directory || expanded === 0), badZip);
    names.add(name);
    if (!directory) entries.set(name, {start, packed, expanded, flags, compression, crc});
    total += expanded;
    need(total <= MAX_PACKAGE, tooLarge);
    p = next;
  }
  need(p === directoryEnd, badZip);
  return {entries, offset, word, long};
}

const crcTable = Uint32Array.from({length: 256}, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit++) value = value >>> 1 ^ (value & 1 ? 0xedb88320 : 0);
  return value;
});

function archiveReader(bytes) {
  const {entries, offset, word, long} = zipDirectory(bytes);
  return (name, maxSize) => {
    const entry = entries.get(name), {start, packed, expanded, flags, compression, crc} = entry || {};
    need(entry, '3MF-filen mangler en påkrævet del.');
    need(expanded <= maxSize, tooLarge);
    need(long(start) === 0x04034b50 && word(start + 6) === flags && word(start + 8) === compression, badZip);
    const headerNameEnd = start + 30 + word(start + 26), dataStart = headerNameEnd + word(start + 28);
    need(dataStart + packed <= offset && archivePath(utf8.decode(bytes.subarray(start + 30, headerNameEnd))) === name, badZip);
    const compressed = bytes.subarray(dataStart, dataStart + packed);
    let data = compressed;
    if (compression === 8) {
      data = new Uint8Array(expanded);
      let written = 0;
      const inflate = new Inflate(chunk => {
        need(written + chunk.length <= expanded, badZip);
        data.set(chunk, written); written += chunk.length;
      });
      // Limit output per push even when an archive understates its expanded size.
      for (let i = 0; i < packed; i += 4096) inflate.push(compressed.subarray(i, i + 4096), i + 4096 >= packed);
      need(written === expanded, badZip);
    }
    let checksum = 0xffffffff;
    for (const byte of data) checksum = checksum >>> 8 ^ crcTable[(checksum ^ byte) & 255];
    need(((checksum ^ 0xffffffff) >>> 0) === crc, badZip);
    return data;
  };
}

function xmlRoot(bytes, parseXml, namespace, name) {
  let xml, document;
  try { xml = utf8.decode(bytes); } catch { throw new Error(badXml); }
  need(!/<!DOCTYPE|<!ENTITY/i.test(xml), badXml);
  const encoding = xml.match(/^\s*<\?xml\s+[^?]*\bencoding\s*=\s*["']([^"']+)["']/i)?.[1];
  need(!encoding || encoding.toLowerCase() === 'utf-8', badXml);
  try { document = parseXml(xml); } catch { throw new Error(badXml); }
  const root = document?.documentElement;
  need(!document?.doctype && root?.namespaceURI === namespace && root.localName === name
    && !document.getElementsByTagName('parsererror').length, badXml);
  return root;
}

function elements(parent, name, namespace = CORE) {
  return Array.from(parent?.childNodes || []).filter(node => node.nodeType === 1 && node.namespaceURI === namespace && node.localName === name);
}

function modelRelationships(read, parseXml, source = '') {
  const directory = source.slice(0, source.lastIndexOf('/') + 1);
  const part = source ? `${directory}_rels/${source.slice(directory.length)}.rels` : ROOT_RELS;
  const rels = xmlRoot(read(part, MAX_REL), parseXml, RELS, 'Relationships');
  return elements(rels, 'Relationship', RELS).filter(rel => rel.getAttribute('Type') === START).map(rel => {
    need(!rel.hasAttribute('TargetMode') || rel.getAttribute('TargetMode') === 'Internal', badModel);
    const target = rel.getAttribute('Target');
    return target?.startsWith('/') ? archivePath(target, true) : directory + archivePath(target);
  });
}

function modelContentTypes(read, parseXml) {
  const types = xmlRoot(read(CONTENT_TYPES.toLowerCase(), MAX_REL), parseXml, TYPES, 'Types');
  const overrides = new Map(), defaults = new Map();
  for (const part of elements(types, 'Override', TYPES)) {
    const path = archivePath(part.getAttribute('PartName'), true);
    need(!overrides.has(path), badModel);
    overrides.set(path, part.getAttribute('ContentType'));
  }
  for (const type of elements(types, 'Default', TYPES)) {
    const extension = type.getAttribute('Extension')?.toLowerCase();
    need(extension && !defaults.has(extension), badModel);
    defaults.set(extension, type.getAttribute('ContentType'));
  }
  return path => {
    const extension = path.slice(path.lastIndexOf('.') + 1);
    need((overrides.get(path) ?? defaults.get(extension)) === MODEL_TYPE, badModel);
  };
}

// The Production extension permits links from the primary model only.
// https://github.com/3MFConsortium/spec_production/blob/master/3MF%20Production%20Extension.md
function partPath(element, primary) {
  const path = element.getAttributeNS(PRODUCTION, 'path');
  if (path === null || !element.hasAttributeNS(PRODUCTION, 'path')) return null;
  need(primary && path.startsWith('/'), badModel);
  return archivePath(path, true);
}

const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
function number(text) {
  need(typeof text === 'string' && text.length <= 64 && decimal.test(text.trim()));
  const value = Number(text);
  need(Number.isFinite(value) && Math.abs(value) <= 1_000_000_000);
  return value;
}

function integer(text, min = 0, max = 2_147_483_647) {
  need(typeof text === 'string' && text.length <= 32 && /^\+?\d+$/.test(text.trim()));
  const value = Number(text);
  need(value >= min && value <= max);
  return value;
}

function determinant(matrix) {
  const m = matrix.elements, product = Math.hypot(m[0], m[1], m[2]) * Math.hypot(m[4], m[5], m[6]) * Math.hypot(m[8], m[9], m[10]);
  const det = matrix.determinant();
  need(Number.isFinite(product) && Math.abs(det) > product * 1e-12 && m.every(value => Number.isFinite(value) && Math.abs(value) <= 1e12));
  return det;
}

function transform(element, unit) {
  const matrix = new Matrix4(), raw = element.getAttribute('transform');
  if (raw === null || !element.hasAttribute('transform')) return matrix;
  need(raw.length < 1024);
  const values = raw.trim().split(/\s+/).map(number);
  need(values.length === 12);
  const [a,b,c,d,e,f,g,h,i,x,y,z] = values;
  matrix.fromArray([a,b,c,0,d,e,f,0,g,h,i,0,x*unit,y*unit,z*unit,1]);
  determinant(matrix);
  return matrix;
}

function partColor(triangle, object, materials) {
  const pid = triangle.getAttribute('pid') || object.getAttribute('pid');
  const palette = materials.get(Number(pid));
  if (!palette) return undefined;
  const first = triangle.getAttribute('p1') || object.getAttribute('pindex');
  if (first === null || first === '') return undefined;
  const color = palette[Number(first)];
  return color && ['p2', 'p3'].every(key => !triangle.hasAttribute(key) || palette[Number(triangle.getAttribute(key))] === color) ? color : undefined;
}

function modelResources(model, limit, primary, unit) {
  const resources = elements(model, 'resources'), materials = new Map(), objects = new Map(), ids = new Set();
  need(resources.length === 1);
  const materialNodes = elements(resources[0], 'basematerials'), objectNodes = elements(resources[0], 'object');
  limit.references += materialNodes.length + objectNodes.length;
  need(limit.references <= MAX_REFERENCES, tooLarge);
  function resourceId(node) {
    const id = integer(node.getAttribute('id'), 1);
    need(!ids.has(id)); ids.add(id);
    return id;
  }
  for (const node of materialNodes) {
    const colors = elements(node, 'base');
    limit.references += colors.length;
    need(limit.references <= MAX_REFERENCES, tooLarge);
    materials.set(resourceId(node), colors.map(base => base.getAttribute('displaycolor')?.match(/^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i)?.[0].slice(0, 7).toLowerCase()));
  }
  for (const node of objectNodes) {
    const id = resourceId(node), meshes = elements(node, 'mesh'), components = elements(node, 'components');
    need(meshes.length + components.length === 1);
    const type = node.getAttribute('type') || 'model', name = node.getAttribute('name')?.trim().slice(0, 40) || undefined;
    need(['model', 'support', 'solidsupport', 'surface', 'other'].includes(type));
    if (components.length) {
      const children = elements(components[0], 'component');
      limit.references += children.length;
      need(children.length > 0 && limit.references <= MAX_REFERENCES, tooLarge);
      const refs = children.map(child => ({id: integer(child.getAttribute('objectid'), 1), path: partPath(child, primary), matrix: transform(child, unit)}));
      objects.set(id, {type, name, children: refs});
      continue;
    }
    const vertexLists = elements(meshes[0], 'vertices'), triangleLists = elements(meshes[0], 'triangles');
    need(vertexLists.length === 1 && triangleLists.length === 1);
    const vertices = elements(vertexLists[0], 'vertex'), triangles = elements(triangleLists[0], 'triangle');
    limit.vertices += vertices.length; limit.triangles += triangles.length;
    need(limit.vertices <= MAX_GEOMETRY && limit.triangles <= MAX_GEOMETRY, tooLarge);
    need(vertices.length && triangles.length);
    const positions = new Float64Array(vertices.length * 3), indices = new Uint32Array(triangles.length * 3);
    for (let i = 0; i < vertices.length; i++) positions.set(['x','y','z'].map(axis => number(vertices[i].getAttribute(axis))), i * 3);
    let color;
    for (let i = 0; i < triangles.length; i++) {
      const triangle = triangles[i], face = ['v1','v2','v3'].map(key => integer(triangle.getAttribute(key), 0, vertices.length - 1));
      need(face[0] !== face[1] && face[1] !== face[2] && face[0] !== face[2]);
      indices.set(face, i * 3);
      const candidate = partColor(triangle, node, materials);
      if (i === 0) color = candidate;
      else if (color !== candidate) color = undefined;
    }
    objects.set(id, {type, name, color, positions, indices});
  }
  return objects;
}

function setBounds(bounds, x, y, z) {
  const point = [x, y, z];
  for (let axis = 0; axis < 3; axis++) {
    bounds.min[axis] = Math.min(bounds.min[axis], point[axis]);
    bounds.max[axis] = Math.max(bounds.max[axis], point[axis]);
  }
}
const emptyBounds = () => ({min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity]});

function expandedMesh(object, matrix, unit, name) {
  const mirrored = determinant(matrix) < 0, bounds = emptyBounds(), point = new Vector3();
  const vertices = new Float32Array(object.positions.length);
  for (let i = 0; i < vertices.length; i += 3) {
    point.fromArray(object.positions, i).multiplyScalar(unit).applyMatrix4(matrix);
    need(Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z)
      && Math.max(Math.abs(point.x), Math.abs(point.y), Math.abs(point.z)) <= MAX_POSITION);
    point.toArray(vertices, i);
  }
  const positions = new Float32Array(object.indices.length * 3);
  for (let i = 0; i < object.indices.length; i++) {
    const j = i % 3, index = 3 * object.indices[mirrored && j ? i + (j === 1 ? 1 : -1) : i];
    const x = vertices[index], y = vertices[index + 1], z = vertices[index + 2];
    positions.set(vertices.subarray(index, index + 3), i * 3);
    setBounds(bounds, x, y, z);
  }
  const size = bounds.max.map((value, axis) => value - bounds.min[axis]);
  need(size.every(value => value > 1e-6));
  const center = bounds.min.map((value, axis) => value + size[axis] / 2);
  return {part: {positions, size, center, name, color: object.color}, bounds};
}

export function read3mf(buffer, {parseXml = text => new DOMParser().parseFromString(text, 'application/xml')} = {}) {
  const bytes = buffer instanceof ArrayBuffer ? new Uint8Array(buffer) : buffer;
  need(bytes instanceof Uint8Array && bytes.length >= 22, badZip);
  need(bytes.length <= MAX_3MF_BYTES, tooLarge);
  try {
    return readModelPackage(archiveReader(bytes), parseXml);
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('3MF-')) throw error;
    throw new Error(badZip);
  }
}

function readModelPackage(read, parseXml) {
  const primary = modelRelationships(read, parseXml);
  need(primary.length === 1, badModel);
  const [primaryPath] = primary, checkType = modelContentTypes(read, parseXml);
  const limit = {vertices: 0, triangles: 0, references: 0};
  const files = new Map();
  let related;
  function openModel(path) {
    if (files.has(path)) return files.get(path);
    const primary = path === primaryPath;
    if (!primary) {
      related ??= new Set(modelRelationships(read, parseXml, primaryPath));
      need(related.has(path), badModel);
    }
    checkType(path);
    const model = xmlRoot(read(path, MAX_MODEL), parseXml, CORE, 'model');
    const required = model.getAttribute('requiredextensions')?.trim();
    need(!required || required.split(/\s+/).every(prefix => model.lookupNamespaceURI(prefix) === PRODUCTION),
      '3MF-filen kræver en udvidelse, som værkstedet ikke understøtter.');
    const unit = UNITS.get(model.getAttribute('unit') || 'millimeter');
    need(unit);
    const file = {unit, objects: modelResources(model, limit, primary, unit)};
    if (primary) {
      const build = elements(model, 'build');
      need(build.length === 1);
      file.items = elements(build[0], 'item');
      need(file.items.length <= MAX_PIECES, tooLarge);
    }
    files.set(path, file);
    return file;
  }
  const {unit, items} = openModel(primaryPath);
  const parts = [], bounds = emptyBounds(), visiting = new Set();
  let visited = 0, triangles = 0;
  function add(path, id, matrix, name, depth) {
    need(depth <= MAX_DEPTH && ++visited <= MAX_REFERENCES, tooLarge);
    const {objects, unit} = openModel(path);
    const object = objects.get(id);
    need(object && !visiting.has(object));
    if (object.type !== 'model') return;
    if (object.children) {
      visiting.add(object);
      for (const child of object.children) add(child.path || path, child.id, matrix.clone().multiply(child.matrix), object.name || name, depth + 1);
      visiting.delete(object);
      return;
    }
    triangles += object.indices.length / 3;
    need(parts.length < MAX_PIECES && triangles <= MAX_GEOMETRY, tooLarge);
    const placed = expandedMesh(object, matrix, unit, object.name || name);
    parts.push(placed.part);
    setBounds(bounds, ...placed.bounds.min);
    setBounds(bounds, ...placed.bounds.max);
  }
  for (const item of items) {
    const printable = item.getAttribute('printable');
    if (printable === 'false' || printable === '0') continue;
    add(partPath(item, true) || primaryPath, integer(item.getAttribute('objectid'), 1), transform(item, unit), undefined, 0);
  }
  need(parts.length, '3MF-filen indeholder ingen modeller til print.');
  return {parts, bounds};
}

function escapeXml(text) {
  return text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/gu, '').replace(/[&<>"]/g, value => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[value]));
}

const xmlStart = '<?xml version="1.0" encoding="UTF-8"?>';
export function export3mf(meshes) {
  if (!Array.isArray(meshes)) meshes = [meshes];
  need(meshes.length > 0 && meshes.length <= MAX_PIECES, tooLarge);
  let vertices = 0, triangles = 0;
  const colors = new Map(), objects = [], items = [];
  for (const [index, mesh] of meshes.entries()) {
    const {positions, indices, name, color} = mesh || {};
    need(positions instanceof Float32Array && indices instanceof Uint32Array && positions.length >= 12 && indices.length >= 12
      && positions.length % 3 === 0 && indices.length % 3 === 0);
    vertices += positions.length / 3; triangles += indices.length / 3;
    need(vertices <= MAX_GEOMETRY && triangles <= MAX_GEOMETRY, tooLarge);
    const id = index + 2;
    let material = '';
    if (typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color)) {
      const rgb = color.toUpperCase();
      if (!colors.has(rgb)) colors.set(rgb, colors.size);
      material = ` pid="1" pindex="${colors.get(rgb)}"`;
    }
    const title = typeof name === 'string' && name ? ` name="${escapeXml(name.slice(0, 80))}"` : '';
    objects.push(`<object id="${id}" type="model"${title}${material}><mesh><vertices>`);
    for (let i = 0; i < positions.length; i += 3) {
      const x = positions[i], y = positions[i+1], z = positions[i+2];
      need([x,y,z].every(value => Number.isFinite(value) && Math.abs(value) <= MAX_POSITION));
      objects.push(`<vertex x="${x}" y="${y}" z="${z}"/>`);
    }
    objects.push('</vertices><triangles>');
    for (let i = 0; i < indices.length; i += 3) {
      const a = indices[i], b = indices[i+1], c = indices[i+2];
      need(a !== b && a !== c && b !== c && Math.max(a,b,c) < positions.length / 3);
      objects.push(`<triangle v1="${a}" v2="${b}" v3="${c}"/>`);
    }
    objects.push('</triangles></mesh></object>');
    items.push(`<item objectid="${id}"/>`);
  }
  const materials = colors.size ? `<basematerials id="1">${Array.from(colors.keys(), color => `<base name="${color}" displaycolor="${color}FF"/>`).join('')}</basematerials>` : '';
  const model = encode.encode(`${xmlStart}<model xmlns="${CORE}" unit="millimeter" xml:lang="da-DK"><resources>${materials}${objects.join('')}</resources><build>${items.join('')}</build></model>`);
  need(model.length <= MAX_MODEL, tooLarge);
  const bytes = zipSync({
    [CONTENT_TYPES]: encode.encode(`${xmlStart}<Types xmlns="${TYPES}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="${MODEL_TYPE}"/></Types>`),
    [ROOT_RELS]: encode.encode(`${xmlStart}<Relationships xmlns="${RELS}"><Relationship Id="model" Type="${START}" Target="/${MODEL_PATH}"/></Relationships>`),
    [MODEL_PATH]: model,
  });
  need(bytes.length <= MAX_3MF_BYTES, tooLarge);
  return bytes;
}
