import {arc, extrudedProfile, revolvedShape, torusShape} from './catalog-mesh.js';

const field = (key, label, value, min = 3, max = 100, step = 1) => ({key, label, value, min, max, step, type: 'number'});
const width = (value = 30) => field('width', 'Bredde (mm)', value);
const depth = (value = 22) => field('depth', 'Dybde (mm)', value);
const height = (value = 12) => field('height', 'Højde (mm)', value);
const diameter = (value = 30) => field('diameter', 'Diameter (mm)', value);
const thickness = (value = 6) => field('height', 'Tykkelse (mm)', value, 1, 35);
const percent = (key, label, value, min = 10, max = 90) => ({...field(key, label + ' (%)', value, min, max, 1), integer: true});
const whole = (key, label, value, min, max) => ({...field(key, label, value, min, max, 1), integer: true});
const circle = (radius, z) => [radius, radius, z];

function define(id, label, icon, fields, construct, refine = () => null) {
  const defaults = Object.fromEntries(fields.map(({key, value}) => [key, value]));
  function validate(settings) {
    if (!settings || typeof settings !== 'object' || Object.keys(settings).length !== fields.length) return {message: 'Kontrollér figurens felter.'};
    for (const {key, label, min, max, integer} of fields) {
      const value = settings[key];
      if (!Object.hasOwn(settings, key) || !Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
        return {key, message: `${label}: vælg ${integer ? 'et helt tal ' : ''}fra ${min} til ${max}.`};
      }
    }
    return refine(settings);
  }
  return {id, label, icon, fields, defaults, validate, build(settings = defaults) { return validate(settings) ? null : construct(settings); }};
}

function stepped({width, height, depth, count}) {
  const xs = Array.from({length: count + 1}, (_, i) => width * i / count);
  const ys = Array.from({length: count + 1}, (_, i) => height * i / count);
  const outline = xs.map(x => [x, 0]), caps = [];
  for (let i = count - 1; i >= 0; i--) {
    const bottomLeft = [xs[i], 0], bottomRight = [xs[i + 1], 0];
    const topLeft = [xs[i], ys[i + 1]], topRight = [xs[i + 1], ys[i + 1]], previousTop = [xs[i], ys[i]];
    outline.push(topRight, topLeft);
    // Split the strip at the previous tread so both adjacent caps share every edge vertex.
    caps.push([bottomLeft, bottomRight, topRight], [bottomLeft, topRight, previousTop], [previousTop, topRight, topLeft]);
  }
  return extrudedProfile(outline, depth, true, caps);
}

function archShape({width, height, depth, wall}) {
  const outer = width / 2, inner = outer - wall, legs = height - outer;
  const cap = (radius, start, sweep) => arc(radius, start, sweep, 32).map(([x, y]) => [x, y + legs]);
  return extrudedProfile([[-outer, 0], [-inner, 0], ...cap(inner, Math.PI, -Math.PI), [inner, 0], [outer, 0], ...cap(outer, 0, Math.PI)], depth, true);
}

function capSections(radius, height, waist, reverse) {
  return Array.from({length: 13}, (_, index) => {
    const angle = Math.PI * index / 24;
    return reverse
      ? circle(index === 12 ? 0 : radius * Math.cos(angle), waist + (height - waist) * Math.sin(angle))
      : circle(radius * Math.sin(angle), waist * (1 - Math.cos(angle)));
  });
}

const tubeInside = ({diameter, tube}) => tube >= diameter / 2 ? {key: 'tube', message: 'Røret skal være tyndere end halvdelen af diameteren.'} : null;

export const BASIC_RECIPES = [
  define('trapezoid', 'Trapezprisme', 'M7 37 16 12H43l10 25Z', [width(), depth(), height(), percent('top', 'Kort side', 60, 15, 95)],
    ({width: w, depth: d, height: h, top}) => extrudedProfile([[-w / 2, -d / 2], [w / 2, -d / 2], [w * top / 200, d / 2], [-w * top / 200, d / 2]], h)),
  define('parallelogram', 'Parallelogram', 'M6 37 22 11h33L40 37Z', [width(), depth(), height(), field('offset', 'Forskydning (mm)', 8, -25, 25)],
    ({width: w, depth: d, height: h, offset}) => extrudedProfile([[0, 0], [w, 0], [w + offset, d], [offset, d]], h)),
  define('frustum', 'Keglestub', 'M8 34 17 13c0-9 26-9 26 0l9 21c0 12-44 12-44 0Zm13-21c0 4 18 4 18 0s-18-4-18 0Z',
    [diameter(26), height(19), percent('top', 'Topdiameter', 55, 10, 95), whole('sides', 'Antal sider', 48, 5, 64)],
    ({diameter: d, height: h, top, sides}) => revolvedShape([circle(d / 2, 0), circle(d * top / 200, h)], sides)),
  define('torus', 'Torus', 'M6 24a24 16 0 1 1 48 0 24 16 0 1 1-48 0Zm12 0a12 6 0 1 0 24 0 12 6 0 1 0-24 0Z',
    [diameter(34), field('tube', 'Rørbredde (mm)', 8, 2, 40), height(8)], ({diameter, tube, height}) => torusShape(diameter, tube, height), tubeInside),
  define('torus-arc', 'Ringbue', 'M44 10a19 15 0 1 0 0 28l-6-6a11 8 0 1 1 0-16Z',
    [diameter(36), field('tube', 'Rørbredde (mm)', 7, 2, 40), height(7), whole('angle', 'Bue (°)', 235, 35, 325)],
    ({diameter, tube, height, angle}) => torusShape(diameter, tube, height, angle), tubeInside),
  define('capsule', 'Kapsel', 'M18 14a10 10 0 0 1 10-10h4a10 10 0 0 1 10 10v20a10 10 0 0 1-10 10h-4a10 10 0 0 1-10-10Z',
    [diameter(16), height(30), percent('ends', 'Endernes runding', 80, 20, 100)], ({diameter: d, height: h, ends}) => {
      const cap = d * ends / 200;
      return revolvedShape([...capSections(d / 2, h, cap, false), ...capSections(d / 2, h, h - cap, true)]);
    }, ({diameter: d, height: h, ends}) => d * ends / 100 >= h ? {key: 'height', message: 'Højden skal være større end de to rundede ender tilsammen.'} : null),
  define('octahedron', 'Oktaeder', 'M30 3 53 24 30 45 7 24Zm0 6-7 15 7 15 7-15Z', [width(26), depth(26), height(30), percent('waist', 'Midterkantens højde', 50, 15, 85)],
    ({width, depth, height, waist}) => revolvedShape([[0, 0, 0], [width / 2, depth / 2, height * waist / 100], [0, 0, height]], 4)),
  define('arrow', 'Pil', 'M5 17h26V7l23 17-23 17V31H5Z', [width(40), depth(22), thickness(), percent('shaft', 'Skaftets bredde', 40, 15, 80), percent('head', 'Spidsens længde', 45, 15, 85)],
    ({width: w, depth: d, height: h, shaft, head}) => {
      const s = d * shaft / 200, shoulder = w * (1 - head / 100);
      return extrudedProfile([[0, -s], [shoulder, -s], [shoulder, -d / 2], [w, 0], [shoulder, d / 2], [shoulder, s], [0, s]], h);
    }),
  define('crescent', 'Månesegl', 'M44 7c-28-12-48 21-26 35 9 6 20 2 26-3C20 35 20 12 44 7Z', [diameter(36), thickness(), percent('body', 'Fylde', 53, 20, 85)],
    ({diameter, height, body}) => {
      const r = diameter / 2, start = Math.PI / 3, tipX = r * .5, tipY = r * Math.sqrt(3) / 2;
      const outer = arc(r, start, 4 * Math.PI / 3, 48);
      const inner = Array.from({length: 31}, (_, i) => (i + 1) / 16 - 1).map(t => [tipX - r * body / 63 * (1 - t ** 2), tipY * t]);
      return extrudedProfile([...outer, ...inner], height);
    }),
  define('flower', 'Blomst', 'M30 3c7 0 10 7 8 12 5-4 13-2 14 5s-6 11-12 10c4 6 1 13-5 15-6 2-10-4-11-9-5 5-12 3-15-2-3-7 3-12 8-13-4-6 0-13 6-14 3 0 6 2 7 5Z',
    [diameter(32), thickness(), whole('petals', 'Antal kronblade', 6, 3, 10), percent('scallop', 'Fordybning', 28, 10, 48)],
    ({diameter, height, petals, scallop}) => extrudedProfile(Array.from({length: petals * 16}, (_, index) => {
      const angle = index * 2 * Math.PI / (petals * 16), radius = diameter / 2 * (1 - scallop / 200 * (1 - Math.cos(petals * angle)));
      return [radius * Math.cos(angle), radius * Math.sin(angle)];
    }), height)),
  define('cross', 'Kors', 'M23 5h14v12h13v14H37v12H23V31H10V17h13Z', [width(30), depth(30), thickness(), percent('arm', 'Armenes bredde', 38, 15, 80)],
    ({width, depth, height, arm}) => {
      const w = width / 2, d = depth / 2, x = w * arm / 100, y = d * arm / 100;
      return extrudedProfile([[-x, -d], [x, -d], [x, -y], [w, -y], [w, y], [x, y], [x, d], [-x, d], [-x, y], [-w, y], [-w, -y], [-x, -y]], height);
    }),
  define('drop', 'Dråbe', 'M30 3C23 13 13 23 13 31a17 14 0 0 0 34 0C47 23 37 13 30 3Z',
    [width(24), field('length', 'Længde (mm)', 34), thickness(), percent('round', 'Runding', 36, 22, 60)],
    ({width: w, length, height, round}) => {
      const curve = arc(1, Math.PI * .84, Math.PI * 1.32, 40).map(([x, y]) => [x * w / 2, (y + 1) * length * round / 100]);
      return extrudedProfile([[0, length], ...curve], height);
    }),
  define('stairs', 'Trappe', 'M5 40h50V7H43v11H30v10H17v7H5Z',
    [width(36), height(24), depth(14), whole('count', 'Antal trin', 4, 2, 12)], stepped),
  define('d-profile', 'D-profil', 'M13 5h11a19 19 0 0 1 0 38H13Z',
    [diameter(30), thickness(), whole('angle', 'Rund side (°)', 200, 90, 250)],
    ({diameter, height, angle}) => extrudedProfile(arc(diameter / 2, -Math.PI * angle / 360, Math.PI * angle / 180, 60), height)),
  define('lens', 'Linse', 'M5 24C17 0 43 0 55 24 43 48 17 48 5 24Z',
    [diameter(33), height(13), percent('waist', 'Midterkantens højde', 42, 12, 88)],
    ({diameter, height, waist}) => revolvedShape([...capSections(diameter / 2, height, height * waist / 100, false), ...capSections(diameter / 2, height, height * waist / 100, true).slice(1)])),
  define('kite', 'Drageform', 'M30 3 50 17 30 45 10 17Z',
    [width(27), field('length', 'Længde (mm)', 36), thickness(), percent('shoulder', 'Bredeste punkts placering', 35, 15, 85)],
    ({width, length, height, shoulder}) => extrudedProfile([[0, 0], [width / 2, length * shoulder / 100], [0, length], [-width / 2, length * shoulder / 100]], height)),
  define('arch', 'Bue', 'M8 43V23a22 22 0 0 1 44 0v20H41V23a11 11 0 0 0-22 0v20Z',
    [width(32), height(26), depth(10), field('wall', 'Vægtykkelse (mm)', 4, 1, 30)], archShape,
    ({width, height, wall}) => height <= width / 2 ? {key: 'height', message: 'Højden skal være større end halvdelen af bredden.'}
      : wall >= width / 2 ? {key: 'wall', message: 'Væggen skal være tyndere end halvdelen af bredden.'} : null),
];
