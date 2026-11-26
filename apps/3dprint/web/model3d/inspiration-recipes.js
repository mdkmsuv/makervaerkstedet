// Editable modules with a common rectangular snap connector. Positions are local
// millimetres and connector +Z points away from its mounting face.
const C = {blue: '#2f6fb5', gold: '#f0bf2c', light: '#e9ecee', dark: '#3b4248', orange: '#e8772e', green: '#3f9a4c', purple: '#7b51ad'};
// The 12 mm fork overlaps its host by 0.35 mm in connectorMount.
const SNAP_REACH = 12 - .35;
// A sideways fork rests on its continuous 5.6 mm extrusion face.
const FORK_AXIS_Z = 2.8;
const number = (key, label, min, max) => ({key, label: `${label} (mm)`, type: 'number', min, max, step: 1});
const part = (kind, size, position, options) => ({kind, size, position, mode: 'solid', ...options});
const box = (size, position, round = 0, color) => part('box', size, position, {
  ...(round ? {params: {round}} : {}), ...(color ? {color} : {}),
});
const socket = (at, rotation = [0, 0, 0]) => ({role: 'socket', at, rotation});
const plug = (at, rotation = [0, 0, 0]) => ({role: 'plug', at, rotation});

function recipe({id, label, icon, color, fit, fields, defaults, make}) {
  function validate(settings) {
    if (!settings || typeof settings !== 'object' || Array.isArray(settings) || Object.keys(settings).length !== fields.length) {
      return {key: fields[0].key, message: 'Kontrollér delens mål.'};
    }
    for (const {key, min, max} of fields) {
      if (!Object.hasOwn(settings, key) || !Number.isFinite(settings[key]) || settings[key] < min || settings[key] > max) {
        return {key, message: `Brug et mål fra ${min} til ${max} mm.`};
      }
    }
    return null;
  }
  return {id, label, icon, color, fit, fields, defaults, validate, build(settings = defaults) {
    return validate(settings) ? null : make(settings);
  }};
}

function torso({width, depth, height}, knight = false) {
  const y = 1, shoulder = height - 10;
  const frontRamp = ([width, depth, height], [x, y, z], color) => part('wedge', [width, depth, 2.8],
    [x, y, z - height / 2 - 1.25], {rotation: [180, 0, 0], color});
  const body = knight ? [
    box([width - 6, depth, height], [0, y, height / 2], 3),
    box([width, depth, 15], [0, y, shoulder], 2),
    ...[-1, 1].map(side => part('wedge', [depth, 6, 6.2],
      [side * (width / 2 - 3), y, shoulder - 10.45], {rotation: [180, 0, -side * 90]})),
    box([width * .5, 2.5, 14], [0, y - depth / 2 - .25, height * .55], 3, C.gold),
    frontRamp([width * .5, 2.5, 14], [0, y - depth / 2 - .25, height * .55], C.gold),
    box([2.5, .6, 9], [0, y - depth / 2 - 1.7, height * .55], .3, C.light),
    box([8, .6, 2.5], [0, y - depth / 2 - 1.7, height * .58], .3, C.light),
  ] : [
    box([width, depth, height], [0, y, height / 2], 2),
    box([width * .54, 2.6, 11], [0, y - depth / 2 - .25, height * .59], 2, C.dark),
    frontRamp([width * .54, 2.6, 11], [0, y - depth / 2 - .25, height * .59], C.dark),
    ...[-1, 1].map(sign => box([3.5, 1.3, 3.5], [sign * width * .13, y - depth / 2 - 1.35, height * .59],
      1, sign === -1 ? C.green : C.gold)),
  ];
  return {
    size: [width, depth + 2, height], parts: body, op: 'difference',
    connectors: [
      socket([0, y, height]),
      socket([-width / 2, y, shoulder - 2.5], [0, -90, -90]),
      socket([width / 2, y, shoulder - 2.5], [0, 90, -90]),
      ...[-1, 1].map(sign => socket([sign * width * .23, y, 0], [0, 180, 0])),
      socket([0, y + depth / 2, height - 24], [-90, 0, -90]),
    ],
  };
}

function sideModule(size, parts) {
  const offset = (size[0] - SNAP_REACH) / 2;
  return {
    size: [size[0] + SNAP_REACH, size[1], size[2]],
    parts: parts.map(({position: [x, y, z], ...piece}) => ({...piece, position: [x - offset, y, z]})),
    connectors: [plug([-offset, 0, FORK_AXIS_Z], [0, -90, 90])], op: 'union',
  };
}

const torsoFields = [number('width', 'Bredde', 38, 54), number('depth', 'Dybde', 16, 30), number('height', 'Højde', 35, 48)];
const torsoFit = 'Sæt arme i siderne, hjelmen i toppen og ben under kroppen. Vinge eller hale passer i ryggen.';
const limbLength = number('length', 'Længde uden tap', 19, 40);

function dragonTorso({width, length, height}) {
  const belly = height - 8, wingY = -length * .11, upper = height - 18;
  // Keep the sideways channels clear of the neck and tail, and the two rows
  // of feet clear of each other and the wing channels, at every allowed size.
  const mount = (port, at, rotation) => ({...socket(at, rotation), port});
  return {
    size: [width, length, height], op: 'difference',
    parts: [
      box([width, length, belly], [0, 0, belly / 2], 8),
      part('halfsphere', [width * .86, length * .88, 11], [0, 0, height - 8.5], {color: C.green}),
      ...[-.18, 0, .18].map(y => part('cone', [width * .24, 8, 7], [0, y * length, height - 3.5], {color: C.orange})),
    ],
    connectors: [
      mount('head', [0, -length / 2, upper], [90, 0, 0]),
      mount('left-wing', [-width / 2, wingY, upper], [0, -90, -90]),
      mount('right-wing', [width / 2, wingY, upper], [180, 90, -90]),
      mount('tail', [0, length / 2, height - 18], [-90, 0, -180]),
      ...[-1, 1].flatMap(row => [-1, 1].map(side => mount(
        `${row < 0 ? 'front' : 'rear'}-${side < 0 ? 'left' : 'right'}`,
        [side * width * .27, row * length * .26, 0], [180, 0, 0],
      ))),
    ],
  };
}

function dragonHead({width, length, height}) {
  const y = -SNAP_REACH / 2;
  return {
    size: [width, length + SNAP_REACH, height], op: 'union',
    parts: [
      box([width, length * .64, height * .68], [0, y + length * .18, height * .34], 4),
      box([width * .76, length * .61, height * .49], [0, y - length * .195, height * .245], 4, C.green),
      ...[-1, 1].flatMap(side => [
        part('sphere', [5, 5.5, height * .33], [side * width * .26, y + length * .015, height * .65], {color: C.gold}),
        part('sphere', [2.5, 2.4, height * .17], [side * width * .26, y + length * .015 - 2, height * .7], {color: C.dark}),
        part('cone', [5, 6, height * .45], [side * width * .23, y + length * .27, height * .775], {color: C.orange}),
        part('sphere', [3.2, 3.5, 2.1], [side * width * .19, y - length * .36, height * .48], {color: C.dark}),
      ]),
    ],
    connectors: [plug([0, y + length / 2, FORK_AXIS_Z], [-90, 0, 0])],
  };
}

function dragonFoot({width, length, height}) {
  return {
    size: [width, length, height + SNAP_REACH], op: 'union',
    parts: [
      box([width, length * .85, 5.2], [0, length * .075, 2.6], 2.3),
      box([width - 2, length * .52, height - 3.7], [0, length * .16, (height + 3.7) / 2], 1.7),
      ...[-1, 0, 1].map(toe => box([width * .24, length * .25, 3.3], [toe * width * .27, -length * .375, 1.65], 1.4, C.light)),
    ],
    connectors: [plug([0, length * .16, height])],
  };
}

export const INSPIRATION_RECIPES = [
  recipe({
    id: 'robot-torso', label: 'Robotkrop', color: C.blue,
    icon: 'M11 13h9l3-6h14l3 6h9v9h-6v17h-8v6h-4v-6h-2v6h-4v-6h-8V22h-6Zm12 7v11h14V20Zm3 4h3v3h-3Zm5 0h3v3h-3Z',
    fit: torsoFit, fields: torsoFields, defaults: {width: 40, depth: 20, height: 36}, make: torso,
  }),
  recipe({
    id: 'knight-torso', label: 'Ridderkrop', color: C.purple,
    icon: 'M8 18 17 9h7l2-5h8l2 5h7l9 9-7 7h-5v14h-6v6h-4v-6h-4v6h-4v-6h-2V25h-5Zm15-2v11l7 6 7-6V16Zm5 3v4h-3v3h3v4h4v-4h3v-3h-3v-4Z',
    fit: torsoFit, fields: torsoFields, defaults: {width: 42, depth: 23, height: 37}, make: values => torso(values, true),
  }),
  recipe({
    id: 'figure-helmet', label: 'Hjelm', color: C.gold,
    icon: 'M10 30v-4C10 14 18 5 30 5s20 9 20 21v4l-6 2v7h-9v6H25v-6h-9v-7Zm8-8v6h24v-6Z',
    fit: 'Sæt hjelmen i hullet øverst på kroppen.',
    fields: [number('width', 'Bredde', 18, 32), number('depth', 'Dybde', 11, 20)],
    defaults: {width: 24, depth: 13},
    make({width, depth}) {
      const crown = 22, y = SNAP_REACH / 2;
      return {
        size: [width, crown + SNAP_REACH, depth + 2],
        parts: [
          box([width, crown, depth], [0, y, depth / 2], 6),
          box([width - 6, 5.5, 2.6], [0, y + 2, depth + .7], 2, C.dark),
          ...[-1, 1].map(sign => box([3, 2.5, 1], [sign * width * .19, y + 2, depth + 1.5], .5, C.light)),
          box([width * .45, 2.3, 1.2], [0, y - 4, depth + .2], .8, C.purple),
        ],
        connectors: [plug([0, y - crown / 2, FORK_AXIS_Z], [90, 0, 0])], op: 'union',
      };
    },
  }),
  recipe({
    id: 'robot-arm', label: 'Robotarm', color: C.blue,
    icon: 'M5 20h9v-6h13v4h11v-6h13l5 6v13l-5 5H38v-6H27v4H14v-7H5Zm37-1v10h8V19Z',
    fit: 'Robotarmen passer i et af hullerne i siden af kroppen.',
    fields: [limbLength, number('width', 'Bredde', 10, 17)], defaults: {length: 24, width: 11},
    make({length, width}) {
      return sideModule([length, width + 3, 12], [
        box([length - 3, width, 10], [(length - 3) / 2, 0, 5], 1),
        box([5, width + 1.8, 11], [length * .48, 0, 5.5], 1.2, C.light),
        box([8, width + 3, 12], [length - 4, 0, 6], 2, C.gold),
      ]);
    },
  }),
  recipe({
    id: 'claw-arm', label: 'Kloarm', color: C.orange,
    icon: 'M5 20h9v-6h13v5h10l7-9h11v7h-8l-4 7 4 7h8v7H44l-7-9H27v5H14v-7H5Z',
    fit: 'Kloarmen passer i et af hullerne i siden af kroppen.',
    fields: [limbLength], defaults: {length: 27},
    make({length}) {
      return sideModule([length, 15, 12], [
        box([length - 7, 12, 10], [(length - 7) / 2, 0, 5], 1),
        box([7, 15, 12], [length - 12, 0, 6], 1.5, C.dark),
        ...[-1, 1].flatMap(sign => [
          box([10, 4, 8], [length - 5, sign * 4.6, 4], 1.5),
          box([4, 4.4, 8], [length - 2, sign * 3, 4], 1.5),
        ]),
      ]);
    },
  }),
  recipe({
    id: 'boot-leg', label: 'Støvleben', color: C.purple,
    icon: 'M24 3h12v8h5v18l9 1 5 6v7H13v-9l8-7V11h3Zm1 13v10h12V16Z',
    fit: 'Sæt en støvle i hvert af de to huller under kroppen.',
    fields: [number('height', 'Højde', 13, 27), number('depth', 'Støvlelængde', 17, 29)],
    defaults: {height: 18, depth: 21},
    make({height, depth}) {
      return {
        size: [14, depth, height + SNAP_REACH],
        parts: [
          box([14, depth, 6], [0, 0, 3], 3),
          box([12, 12, height - 4], [0, 2, (height + 4) / 2], 1.3),
          box([13, 12.8, 3], [0, 2, 8], 1.4, C.light),
        ],
        connectors: [plug([0, 2, height])], op: 'union',
      };
    },
  }),
  recipe({
    id: 'dragon-wing', label: 'Dragevinge', color: C.green,
    icon: 'M5 18h9v-5h9L54 5 48 18l-11 1 3 7-9 1 1 9-11-4h-7v-7H5Zm18 1v7l16-11Z',
    fit: 'Sæt vingen i ryggen eller i et af skulderhullerne.',
    fields: [number('span', 'Vingelængde', 22, 48), number('depth', 'Vingebredde', 16, 30)],
    defaults: {span: 32, depth: 26},
    make({span, depth}) {
      return sideModule([span, depth, 10], [
        box([12, 10, 10], [6, 0, 5], 1.2),
        part('wedge', [4, depth, span], [span / 2, 0, 2], {rotation: [0, 90, 0]}),
        part('wedge', [3, depth * .50, span * .85 - 2], [(span * .85 + 2) / 2, -depth * .20, 5.1],
          {rotation: [0, 90, 0], color: C.gold}),
      ]);
    },
  }),
  recipe({
    id: 'dragon-tail', label: 'Dragehale', color: C.green,
    icon: 'M5 22h9v-5h10l5-9 5 8 4-6 5 6 4-4 8 9-7 12-10 4-12-2 11-5 3-7-14 5H14v-1H5Z',
    fit: 'Sæt halen i hullet på ryggen af kroppen.',
    fields: [number('length', 'Længde uden tap', 23, 46)], defaults: {length: 33},
    make({length}) {
      return sideModule([length, 12, 13], [
        box([10, 11, 10], [5, 0, 5], 1.2),
        part('wedge', [5, 6.2, length], [length / 2, -2.9, 2.5], {rotation: [180, 90, 0]}),
        part('wedge', [5, 6.2, length], [length / 2, 2.9, 2.5], {rotation: [0, 90, 0]}),
        ...[[.34, 4.6], [.56, 3.6], [.77, 2.3]].map(([at, width]) =>
          part('cone', [width, width, 8.4], [length * at, 0, 8.8], {color: C.orange})),
      ]);
    },
  }),
  recipe({
    id: 'dragon-torso', label: 'Dragekrop', color: '#32834b',
    icon: 'M7 30V19l9-6 5-6 6 5 5-7 6 7 4-4 5 8 6 6v8l-8 5h-6v7h-5v-7h-8v7h-5v-7h-6Zm8-10v7h5v-7Zm25 0v7h5v-7Z',
    fit: 'Sæt hovedet foran, halen bagpå og en vinge i hver side. Der er fire huller til fødderne under kroppen.',
    fields: [number('width', 'Bredde', 38, 52), number('length', 'Længde', 60, 86), number('height', 'Højde', 33, 45)],
    defaults: {width: 40, length: 66, height: 36}, make: dragonTorso,
  }),
  recipe({
    id: 'dragon-head', label: 'Dragehoved', color: '#32834b',
    icon: 'M50 35h6v7H43v-4H18L5 32v-9l9-4h8l3-7 3 4 6-1 3-11 6 10 5-4 2 13ZM31 22v5h6v-5ZM9 25v3h4v-3Z',
    fit: 'Sæt hovedet i det forreste hul på dragekroppen.',
    fields: [number('width', 'Bredde', 19, 31), number('length', 'Længde uden tap', 28, 45), number('height', 'Højde', 14, 24)],
    defaults: {width: 24, length: 32, height: 18}, make: dragonHead,
  }),
  recipe({
    id: 'dragon-foot', label: 'Dragefod', color: '#32834b',
    icon: 'M22 5h6v10h4V5h6v12h4v12l8 6v8h-7v-5h-6v6h-7v-6h-6v6h-7v-6h-6v5H5v-8l12-6V17h5Z',
    fit: 'Sæt fødderne i hullerne under dragekroppen med kløerne fremad.',
    fields: [number('width', 'Bredde', 12, 17), number('length', 'Længde', 15, 24), number('height', 'Højde uden tap', 12, 24)],
    defaults: {width: 15, length: 20, height: 16}, make: dragonFoot,
  }),
];
