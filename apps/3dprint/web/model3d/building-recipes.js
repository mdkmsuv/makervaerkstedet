const WEB = 1.2;
const number = (key, label, min, max) => ({key, label: `${label} (mm)`, type: 'number', min, max, step: .1});
const option = (value, label) => ({value, label});
const select = (key, label, ...options) => ({key, label, type: 'select', options});
const issue = (key, message) => ({key, message});
const millimeters = value => String(value).replace('.', ',');

function recipe({id, label, icon, fields, defaults, check, make}) {
  function validate(settings) {
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)
        || Object.keys(settings).length !== fields.length) {
      return issue(fields[0].key, 'Opskriften mangler mål. Vælg delene igen.');
    }
    for (const field of fields) {
      const value = settings[field.key];
      if (field.type === 'select') {
        if (!field.options.some(option => option.value === value)) return issue(field.key, 'Vælg en af mulighederne i listen.');
      } else if (!Number.isFinite(value) || value < field.min || value > field.max) {
        return issue(field.key, `Skriv et tal fra ${millimeters(field.min)} til ${millimeters(field.max)} mm.`);
      }
    }
    return check(settings);
  }
  return {id, label, icon, fields, defaults, validate, build(settings = defaults) {
    return validate(settings) ? null : make(settings);
  }};
}

function part(kind, size, position = [0, 0, size[2] / 2], options = {}) {
  return {kind, size, position, mode: 'solid', ...options};
}

const cylinder = (diameter, height, position, options = {}) =>
  part('cylinder', [diameter, diameter, height], position, {params: {sides: 64}, ...options});
const box = (size, position, options) => part('box', size, position, options);
const throughHole = (diameter, height, position, rotation) =>
  cylinder(diameter, height + 2, position, {mode: 'hole', ...(rotation ? {rotation} : {})});
const fitBore = ({diameter, bore}) => bore + 2 * WEB > diameter ? issue('bore', 'Vælg et mindre hul, så der er mindst 1,2 mm materiale hele vejen rundt.') : null;

// The origin is the centre of the assembled footprint at the bottom face.
// Negative parts extend past the face they cut and do not count toward size.
function built(size, parts) {
  return {size, parts, op: parts.some(part => part.mode === 'hole') ? 'difference' : 'union'};
}

const roundWithBore = (diameter, bore, height) => built([diameter, diameter, height], [
  cylinder(diameter, height),
  throughHole(bore, height, [0, 0, height / 2]),
]);

function circularHoles(count, circle, hole, depth, centerZ) {
  return Array.from({length: count}, (_, index) => {
    const angle = index * 2 * Math.PI / count;
    return throughHole(hole, depth, [Math.cos(angle) * circle / 2, Math.sin(angle) * circle / 2, centerZ]);
  });
}

export const BUILDING_RECIPES = [
  recipe({
    id: 'washer', label: 'Spændeskive',
    icon: 'M6 24a24 15 0 1 0 48 0a24 15 0 1 0-48 0Zm14 0a10 6 0 1 1 20 0a10 6 0 1 1-20 0Z',
    fields: [number('diameter', 'Ydre diameter', 8, 90), number('bore', 'Huldiameter', 1, 75), number('thickness', 'Tykkelse', 1.5, 12)],
    defaults: {diameter: 18, bore: 9, thickness: 2.5},
    check: fitBore,
    make: ({diameter, bore, thickness}) => roundWithBore(diameter, bore, thickness),
  }),
  recipe({
    id: 'spacer', label: 'Afstandsstykke',
    icon: 'M14 11c0-9 32-9 32 0v25c0 10-32 10-32 0Zm0 0c0 9 32 9 32 0M22 11c0 5 16 5 16 0s-16-5-16 0Z',
    fields: [number('diameter', 'Ydre diameter', 8, 60), number('bore', 'Huldiameter', 1, 48), number('length', 'Længde', 3, 100)],
    defaults: {diameter: 12, bore: 6, length: 20},
    check: fitBore,
    make: ({diameter, bore, length}) => roundWithBore(diameter, bore, length),
  }),
  recipe({
    id: 'angle-bracket', label: 'Vinkelbeslag',
    icon: 'M7 8h24v23l22 4v8L7 36Zm9 13a3 4 0 1 0 6 0a3 4 0 1 0-6 0Zm19 16a4 2 0 1 0 8 0a4 2 0 1 0-8 0Z',
    fields: [number('width', 'Bredde', 18, 110), number('depth', 'Dybde', 18, 100), number('height', 'Højde', 18, 100),
      number('thickness', 'Godstykkelse', 2, 12), number('hole', 'Huldiameter', 2, 16)],
    defaults: {width: 40, depth: 32, height: 30, thickness: 5, hole: 5.5},
    check: ({width, depth, height, thickness, hole}) =>
      hole + 2 * WEB > Math.min(width, depth - thickness, height - thickness)
        ? issue('hole', 'Gør hullerne mindre eller beslagets flader større, så hullerne bliver inden for kanterne.') : null,
    make: ({width, depth, height, thickness, hole}) => {
      const back = (depth - thickness) / 2;
      return built([width, depth, height], [
        box([width, depth, thickness]),
        box([width, thickness, height], [0, back, height / 2]),
        throughHole(hole, thickness, [0, -thickness / 2, thickness / 2]),
        throughHole(hole, thickness, [0, back, (height + thickness) / 2], [90, 0, 0]),
      ]);
    },
  }),
  recipe({
    id: 'mounting-plate', label: 'Monteringsplade',
    icon: 'M5 11h50v26H5Zm6 6v4h4v-4Zm34 0v4h4v-4ZM11 27v4h4v-4Zm34 0v4h4v-4Z',
    fields: [number('width', 'Længde', 20, 140), number('depth', 'Bredde', 20, 140), number('thickness', 'Tykkelse', 2, 20),
      number('hole', 'Huldiameter', 2, 14), number('edge', 'Fra kant til hulcenter', 2, 50),
      select('corner', 'Hjørner', option('square', 'Lige'), option('round', 'Afrundede'))],
    defaults: {width: 58, depth: 38, thickness: 5, hole: 5, edge: 7, corner: 'square'},
    check: ({width, depth, hole, edge}) => edge < hole / 2 + WEB || 2 * edge + hole + WEB > Math.min(width, depth)
      ? issue('edge', 'Vælg en anden afstand til kanten, så alle fire huller har plads.') : null,
    make: ({width, depth, thickness, hole, edge, corner}) => {
      const size = [width, depth, thickness], outline = corner === 'round' ? {params: {round: Math.min(4, edge)}} : {};
      const x = width / 2 - edge, y = depth / 2 - edge;
      return built(size, [box(size, undefined, outline), ...[[-x, -y], [x, -y], [x, y], [-x, y]]
        .map(([x, y]) => throughHole(hole, thickness, [x, y, thickness / 2]))]);
    },
  }),
  recipe({
    id: 'pulley', label: 'Remskive',
    icon: 'M9 16c0-7 42-7 42 0v5l-7 1v6l7 1v4c0 8-42 8-42 0v-4l7-1v-6l-7-1Zm14 0a7 3 0 1 0 14 0a7 3 0 1 0-14 0Z',
    fields: [number('diameter', 'Ydre diameter', 18, 100), number('bore', 'Akselhul', 2, 50), number('width', 'Samlet bredde', 6, 40),
      number('lip', 'Kanttykkelse', 1.5, 10), number('grooveDepth', 'Rillens dybde', 1, 12)],
    defaults: {diameter: 36, bore: 6, width: 15, lip: 3, grooveDepth: 3},
    check: ({diameter, bore, width, lip, grooveDepth}) => {
      if (2 * lip + 2 > width) return issue('lip', 'Gør kanterne tyndere eller skiven bredere, så der er plads til rillen.');
      return bore + 2 * (grooveDepth + WEB) > diameter
        ? issue('grooveDepth', 'Vælg en grundere rille, så akselhullet stadig har materiale omkring sig.') : null;
    },
    make: ({diameter, bore, width, lip, grooveDepth}) => built([diameter, diameter, width], [
      cylinder(diameter - 2 * grooveDepth, width),
      cylinder(diameter, lip),
      cylinder(diameter, lip, [0, 0, width - lip / 2]),
      throughHole(bore, width, [0, 0, width / 2]),
    ]),
  }),
  recipe({
    id: 'wheel', label: 'Hjul',
    icon: 'M8 24a22 18 0 1 0 44 0a22 18 0 1 0-44 0Zm8 0a14 11 0 1 1 28 0a14 11 0 1 1-28 0Zm10 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0Z',
    fields: [number('diameter', 'Hjuldiameter', 26, 130), number('width', 'Hjulbredde', 4, 36), number('hub', 'Navdiameter', 8, 70),
      number('bore', 'Akselhul', 2, 48), select('style', 'Skive', option('solid', 'Hel skive'), option('vented', 'Med fire åbninger'))],
    defaults: {diameter: 48, width: 11, hub: 18, bore: 6, style: 'solid'},
    check: ({diameter, hub, bore, style}) => {
      if (hub + 2 * WEB > diameter) return issue('hub', 'Navet skal være mindre end hjulet.');
      if (bore + 2 * WEB > hub) return issue('bore', 'Vælg et mindre akselhul, så der er plads i navet.');
      return style === 'vented' && diameter - hub < 12
        ? issue('style', 'Der skal være mere plads mellem nav og hjulkant til åbningerne.') : null;
    },
    make: ({diameter, width, hub, bore, style}) => {
      const height = width + 2;
      const holes = style === 'vented'
        ? circularHoles(4, (diameter + hub) / 2, Math.min(10, (diameter - hub) / 2 - 4), width, width / 2) : [];
      return built([diameter, diameter, height], [
        cylinder(diameter, width), cylinder(hub, height),
        throughHole(bore, height, [0, 0, height / 2]), ...holes,
      ]);
    },
  }),
  recipe({
    id: 'flange', label: 'Flange',
    icon: 'M5 29a25 13 0 1 0 50 0a25 13 0 1 0-50 0Zm7 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0Zm30 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0ZM20 15c0-8 20-8 20 0v15c0 6-20 6-20 0Zm6 0a4 2 0 1 0 8 0a4 2 0 1 0-8 0Z',
    fields: [number('diameter', 'Flangediameter', 30, 130), number('thickness', 'Flangetykkelse', 2, 22),
      number('hub', 'Navdiameter', 10, 65), number('height', 'Samlet højde', 5, 55), number('bore', 'Akselhul', 2, 45),
      number('mountingHole', 'Monteringshul', 2, 12), number('boltCircle', 'Hulcirklens diameter', 12, 120),
      select('holeCount', 'Antal monteringshuller', option('4', '4'), option('6', '6'))],
    defaults: {diameter: 54, thickness: 6, hub: 24, height: 15, bore: 9, mountingHole: 5, boltCircle: 40, holeCount: '4'},
    check: ({diameter, thickness, hub, height, bore, mountingHole, boltCircle, holeCount}) => {
      if (height - thickness < 2) return issue('height', 'Gør navet mindst 2 mm højere end flangen.');
      if (bore + 2 * WEB > hub) return issue('bore', 'Vælg et mindre akselhul, så der er plads i navet.');
      if (boltCircle + mountingHole + 2 * WEB > diameter || boltCircle - mountingHole - 2 * WEB < hub
          || boltCircle * Math.sin(Math.PI / Number(holeCount)) < mountingHole + WEB) {
        return issue('boltCircle', 'Flyt hulcirklen, eller gør monteringshullerne mindre, så de ligger mellem nav og kant.');
      }
      return null;
    },
    make: ({diameter, thickness, hub, height, bore, mountingHole, boltCircle, holeCount}) => built([diameter, diameter, height], [
      cylinder(diameter, thickness), cylinder(hub, height), throughHole(bore, height, [0, 0, height / 2]),
      ...circularHoles(Number(holeCount), boltCircle, mountingHole, thickness, thickness / 2),
    ]),
  }),
  recipe({
    id: 'knob', label: 'Drejeknop',
    icon: 'M12 16l9-8h18l9 8v16l-8 7H20l-8-7Zm0 0 9 6h18l9-6M21 22v16m18-16v16',
    fields: [number('diameter', 'Grebets diameter', 18, 65), number('height', 'Højde', 9, 38), number('bore', 'Akselhul', 2, 35),
      number('socketDepth', 'Huldybde', 2, 34), select('grip', 'Greb', option('fluted', '12 sider'), option('hex', 'Sekskantet'))],
    defaults: {diameter: 32, height: 16, bore: 7, socketDepth: 10, grip: 'fluted'},
    check: ({diameter, height, bore, socketDepth, grip}) => {
      const innerClearance = grip === 'hex' ? .84 : .94;
      if (bore + 2 * WEB > diameter * .9 * innerClearance) return issue('bore', 'Vælg et mindre akselhul, så grebet har plads til en væg.');
      return socketDepth + 2 > height ? issue('socketDepth', 'Lad mindst 2 mm være tilbage over hullet.') : null;
    },
    make: ({diameter, height, bore, socketDepth, grip}) => built([diameter, diameter, height], [
      cylinder(diameter, 2.5),
      cylinder(diameter * .9, height, undefined, {params: {sides: grip === 'hex' ? 6 : 12}}),
      cylinder(bore, socketDepth + 1, [0, 0, (socketDepth - 1) / 2], {mode: 'hole'}),
    ]),
  }),
  recipe({
    id: 'shaft-coupling', label: 'Akselkobling',
    icon: 'M15 12c0-8 30-8 30 0v24c0 9-30 9-30 0Zm0 0c0 8 30 8 30 0m-23 0c0 4 16 4 16 0s-16-4-16 0Zm10 14a3 3 0 1 0 6 0a3 3 0 1 0-6 0Z',
    fields: [number('diameter', 'Ydre diameter', 12, 58), number('length', 'Længde', 12, 90), number('bore', 'Første akselhul', 2, 40),
      number('otherBore', 'Andet akselhul', 2, 40), number('sideHole', 'Sidehul', 2, 14)],
    defaults: {diameter: 25, length: 32, bore: 8, otherBore: 6, sideHole: 4},
    check: ({diameter, length, bore, otherBore, sideHole}) => fitBore({diameter, bore})
      ?? (otherBore + 2 * WEB > diameter ? issue('otherBore', 'Vælg et mindre akselhul, så der er materiale omkring det.') : null)
      ?? (sideHole + 2 * WEB > Math.min(length / 2, diameter - WEB)
        ? issue('sideHole', 'Vælg et mindre sidehul, så det bliver inden for koblingens kanter.') : null),
    make: ({diameter, length, bore, otherBore, sideHole}) => built([diameter, diameter, length], [
      cylinder(diameter, length),
      throughHole(bore, length / 2, [0, 0, length / 4]),
      throughHole(otherBore, length / 2, [0, 0, 3 * length / 4]),
      throughHole(sideHole, diameter / 2, [diameter / 4, 0, length / 4], [0, 90, 0]),
    ]),
  }),
  recipe({
    id: 'hook', label: 'Krog',
    icon: 'M9 6h13v25h18V21h12v21H9Zm4 6v7h5v-7Z',
    fields: [number('width', 'Bredde', 12, 70), number('depth', 'Dybde', 20, 90), number('height', 'Højde', 18, 80),
      number('thickness', 'Godstykkelse', 3, 12), number('hole', 'Monteringshul', 2, 14)],
    defaults: {width: 24, depth: 36, height: 36, thickness: 5, hole: 5},
    check: ({width, depth, height, thickness, hole}) => {
      if (2 * thickness + 4 > Math.min(depth, height)) return issue('thickness', 'Gør krogens vægge tyndere for at få plads i åbningen.');
      return hole + 2 * WEB > Math.min(width, height * .5)
        ? issue('hole', 'Vælg et mindre monteringshul, så det bliver inden for krogens bagplade.') : null;
    },
    make: ({width, depth, height, thickness, hole}) => {
      const back = (depth - thickness) / 2;
      return built([width, depth, height], [
        box([width, depth, thickness]),
        box([width, thickness, height], [0, back, height / 2]),
        box([width, thickness, height / 2], [0, -back, height / 4]),
        throughHole(hole, thickness, [0, back, .75 * height], [90, 0, 0]),
      ]);
    },
  }),
  recipe({
    id: 'corner-support', label: 'Støttehjørne',
    icon: 'M8 38 46 8l6 4v28l-38 4Zm11-3 26-20v20Zm20-2v4h5v-4ZM14 37v4h5v-4Z',
    fields: [number('width', 'Bredde', 20, 100), number('depth', 'Dybde', 25, 110), number('height', 'Højde', 20, 90),
      number('thickness', 'Godstykkelse', 3, 12), number('hole', 'Monteringshul', 2, 12)],
    defaults: {width: 42, depth: 42, height: 35, thickness: 5, hole: 4.5},
    check: ({width, depth, height, thickness, hole}) =>
      hole + 2 * WEB > Math.min((width - thickness) / 2, depth / 2, height - thickness)
        ? issue('hole', 'Gør hullerne mindre, så de ligger mellem afstivningen og yderkanterne.') : null,
    make: ({width, depth, height, thickness, hole}) => {
      const back = (depth - thickness) / 2, x = (width + thickness) / 4;
      return built([width, depth, height], [
        box([width, depth, thickness]),
        box([width, thickness, height], [0, back, height / 2]),
        part('wedge', [depth, thickness, height], [0, 0, height / 2], {rotation: [0, 0, 90]}),
        ...[-x, x].flatMap(x => [
          throughHole(hole, thickness, [x, -depth / 4, thickness / 2]),
          throughHole(hole, thickness, [x, back, (height + thickness) / 2], [90, 0, 0]),
        ]),
      ]);
    },
  }),
];

export const BUILDING_RECIPES_BY_ID = new Map(BUILDING_RECIPES.map(item => [item.id, item]));
