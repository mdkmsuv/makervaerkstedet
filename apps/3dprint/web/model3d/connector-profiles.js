import {extrudedProfile, loftedShape, revolvedShape} from './catalog-mesh.js';
import {BALL_CONNECTOR_SIZES, ballConnectorMesh} from './ball-connector.js';

const profiles = [
  {id: 'round-6', label: 'rund Ø6', width: 6, height: 8, round: true},
  {id: 'round-10', label: 'rund Ø10', width: 10, height: 12, round: true},
  {id: 'square-8', label: 'firkant 8', width: 8, height: 8, round: false},
];
const snapProfiles = [
  {id: 'snap-9', label: '9 mm', scale: 1},
  {id: 'snap-13', label: '13 mm', scale: 1.5},
];
const ballProfile = {id: 'ball-9', label: 'Ø9'};
export const FIGURE_SNAP_PROFILE = 'snap-9';
const allowance = .4;
const snapSize = {plug: [8.8, 5.6, 12], socket: [9.4, 6.4, 12.8]};
const printableSockets = [
  {id: 'snap-9-roof-y-negative', side: 0, axisOffset: [0, -2.35], size: [9.4, 11.1, 12.8]},
  {id: 'snap-9-roof-x-positive', side: 1, axisOffset: [1.6, 0], size: [12.6, 6.4, 12.8]},
  {id: 'snap-9-roof-y-positive', side: 2, axisOffset: [0, 2.35], size: [9.4, 11.1, 12.8]},
  {id: 'snap-9-roof-x-negative', side: 3, axisOffset: [-1.6, 0], size: [12.6, 6.4, 12.8]},
  {id: 'snap-9-roof-end', side: null, axisOffset: [0, 0], size: [9.4, 6.4, 16.3]},
].map(entry => ({...entry, scale: 1, label: '9 mm'}));
const profilesById = new Map([...profiles, ...snapProfiles, ...printableSockets, ballProfile].map(profile => [profile.id, profile]));

// A flexible fork with a wide shoulder behind its lead-in taper. The root of
// the slit opens gradually so the arms do not hinge on a single square corner.
const snapFork = [[-3.3, 0], [3.3, 0], [3.3, 8.6], [4.4, 9.3], [4.4, 9.9], [2.9, 12], [1, 12],
  [1, 2.8], [.75, 2.5], [.35, 2.3], [-.35, 2.3], [-.75, 2.5], [-1, 2.8], [-1, 12],
  [-2.9, 12], [-4.4, 9.9], [-4.4, 9.3], [-3.3, 8.6]];
// The narrower straight channel retains the fork. The ramp toward the floor
// leaves a printable transition into the pocket and room for the fork's nose.
const snapChannel = [[4.05, 0], [3.65, .7], [3.65, 8.35], [4.7, 8.75], [4.7, 10.25], [3.65, 10.75], [3.65, 12.8]];
const snapCavity = [...snapChannel, ...snapChannel.map(([x, depth]) => [-x, depth]).reverse()]
  .map(([x, depth]) => [x, 12.8 - depth]);

export function figureConnectorProfile(role, face) {
  if (role !== 'socket' || face.elements[10] > .99) return FIGURE_SNAP_PROFILE;
  if (face.elements[10] < -.99) return printableSockets[4].id;
  const [upX, upY] = [face.elements[2], face.elements[6]];
  if (Math.abs(upX) > .99) return printableSockets[upX > 0 ? 1 : 3].id;
  return Math.abs(upY) > .99 ? printableSockets[upY > 0 ? 2 : 0].id : FIGURE_SNAP_PROFILE;
}

function printableSocket({side, size, axisOffset}) {
  const sections = snapChannel.slice().reverse().map(([x, depth]) => {
    const y = 3.2, points = [[-x, -y], [x, -y], [x, y], [-x, y]];
    if (side !== null) {
      // The rectangular snap channel is unchanged. The outward face continues
      // into a 45 degree roof where it would otherwise have a flat ceiling.
      const peaks = [[0, -y - x], [x + y, 0], [0, y + x], [-x - y, 0]];
      points.splice(side + 1, 0, peaks[side]);
    }
    return points.map(([px, py]) => [px, py, 12.8 - depth]);
  });
  if (side === null) sections.unshift([[-.2, -.2], [.2, -.2], [.2, .2], [-.2, .2]].map(([x, y]) => [x, y, -3.5]));
  return {...loftedShape(sections), size, axisOffset};
}

export function connectorDimensions(profile, role) {
  const entry = profilesById.get(profile);
  if (entry === ballProfile) return Object.hasOwn(BALL_CONNECTOR_SIZES, role) ? BALL_CONNECTOR_SIZES[role] : null;
  if (!entry || !['plug', 'socket'].includes(role)) return null;
  if (entry.size) return role === 'socket' ? entry.size : null;
  if (entry.scale) return snapSize[role].map(value => Number((value * entry.scale).toFixed(2)));
  const extra = role === 'socket' ? allowance : 0;
  return [entry.width + extra, entry.width + extra, entry.height + extra];
}

export function connectorOrigin(profile, role) {
  const size = connectorDimensions(profile, role);
  return size ? [...(profilesById.get(profile).axisOffset || [0, 0]), (role === 'socket' ? -1 : 1) * size[2] / 2] : null;
}

const snapIcons = {
  plug: 'M17 43V21l-4-5 5-12h8v22h8V4h8l5 12-4 5v22Z',
  socket: 'M7 18 17 10h26l10 8v23l-10 5H17L7 41Zm9 3 10-7h8l10 7-10 7h-8Z',
};
const ballIcons = {
  plug: 'M30 3a14 14 0 0 1 6 27v10h8v5H16v-5h8V30a14 14 0 0 1 6-27Z',
  cup: 'M10 16a20 8 0 1 1 40 0v17l-5 9H15l-5-9Zm8 0a12 4 0 1 0 24 0 12 4 0 1 0-24 0Z',
};
function name(entry, role) {
  if (entry === ballProfile) return `${role === 'plug' ? 'Kugletap' : 'Kugleskål'} ${entry.label}`;
  if (entry.scale) return `${role === 'plug' ? 'Snap-tap' : 'Snap-hul'} ${entry.label}`;
  return `${role === 'plug' ? 'Tap' : 'Sokkelhul'} ${entry.label}`;
}

const preset = (entry, role, icon) => ({id: `${entry.id}-${role}`, label: name(entry, role), icon, settings: {profile: entry.id, role}});
export const CONNECTOR_PRESETS = [
  ...snapProfiles.flatMap(entry => ['plug', 'socket'].map(role => preset(entry, role, snapIcons[role]))),
  ...['plug', 'cup'].map(role => preset(ballProfile, role, ballIcons[role])),
];

export function buildConnector({profile, role}) {
  const size = connectorDimensions(profile, role);
  if (!size) return null;
  const entry = profilesById.get(profile), [width, , height] = size, radius = width / 2;
  if (entry === ballProfile) return {...ballConnectorMesh(role), label: name(entry, role), mode: 'solid'};
  if (entry.scale) {
    if (entry.size) return {...printableSocket(entry), label: name(entry, role), mode: 'hole'};
    const outline = role === 'plug' ? snapFork : snapCavity;
    const shape = extrudedProfile(outline.map(point => point.map(value => value * entry.scale)), size[1], true);
    return {...shape, size, label: name(entry, role), mode: role === 'plug' ? 'solid' : 'hole'};
  }
  const shape = entry.round
    ? revolvedShape([[radius, radius, 0], [radius, radius, height - .5], [radius - (role === 'plug' ? .4 : 0), radius - (role === 'plug' ? .4 : 0), height]])
    : extrudedProfile([[-radius, -radius], [radius, -radius], [radius, radius], [-radius, radius]], height);
  return {...shape, size, label: name(entry, role), mode: role === 'plug' ? 'solid' : 'hole'};
}
