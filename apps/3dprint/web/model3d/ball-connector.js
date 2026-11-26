import {revolvedShape} from './catalog-mesh.js';

const BALL_RADIUS = 4.5;
const CAVITY_RADIUS = BALL_RADIUS + .3;
const QUARTER = Math.PI / 2;
const FACETS_PER_PETAL = 18;
const BALL_FACETS = FACETS_PER_PETAL * 4;
const SLOT_ROOT = 3.85;
const SLOT_HALF_ANGLE = .105;
const STEM_RADIUS = 1.9;
const LIP_RISE = 2.1;

export const BALL_CONNECTOR_CENTERS = Object.freeze({plug: 10.8, cup: 7});
export const BALL_CONNECTOR_SIZES = Object.freeze({
  plug: Object.freeze([2 * BALL_RADIUS, 2 * BALL_RADIUS, BALL_CONNECTOR_CENTERS.plug + BALL_RADIUS]),
  cup: Object.freeze([13, 13, 10]),
});

function ballPlug() {
  const center = BALL_CONNECTOR_CENTERS.plug;
  const tangent = BALL_RADIUS * Math.SQRT1_2;
  // The 45° neck meets the spherical surface tangentially below the equator.
  const neckTop = center - 2 * tangent + STEM_RADIUS;
  const ballSurface = Array.from({length: 37}, (_, step) => {
    const angle = -Math.PI / 4 + Math.PI * step / 48;
    const radius = step === 36 ? 0 : BALL_RADIUS * Math.cos(angle);
    return [radius, radius, center + BALL_RADIUS * Math.sin(angle)];
  });
  const base = [[4, 4, 0], [4.35, 4.35, .4], [4.35, 4.35, 1.2],
    [STEM_RADIUS, STEM_RADIUS, 2.45], [STEM_RADIUS, STEM_RADIUS, neckTop]];
  return revolvedShape([...base, ...ballSurface], BALL_FACETS).positions;
}

const outerProfile = [[0, 6.1], [.45, 6.5], [1.3, 6.5], [2.4, 5.95], [3.85, 5.95],
  [5.45, 6.02], [7, 6.1], [8.65, 5.86], [9.1, 5.68], [10, 5.55]];

function outerRadius(z) {
  for (let i = 1; i < outerProfile.length; i++) {
    const [high, last] = outerProfile[i];
    if (z <= high) {
      const [low, first] = outerProfile[i - 1];
      return first + (last - first) * (z - low) / (high - low);
    }
  }
  return outerProfile.at(-1)[1];
}

function cupRim(z, center, top) {
  const lip = center + LIP_RISE;
  const sphericalRadius = height => Math.sqrt(Math.max(0, CAVITY_RADIUS ** 2 - (height - center) ** 2));
  if (z <= lip) return sphericalRadius(z);
  const mouth = sphericalRadius(lip);
  return mouth + (4.58 - mouth) * (z - lip) / (top - lip);
}

function petalRing(radius, z, sector) {
  const gap = SLOT_HALF_ANGLE * Math.max(0, Math.min(1, (z - SLOT_ROOT) / .8));
  const start = sector * QUARTER + gap, end = (sector + 1) * QUARTER - gap;
  return Array.from({length: FACETS_PER_PETAL + 1}, (_, index) => {
    // Reuse identical coordinates where neighbouring petals still share a seam.
    const angle = index === FACETS_PER_PETAL ? end % (4 * QUARTER) : start + (end - start) * index / FACETS_PER_PETAL;
    return [radius * Math.cos(angle), radius * Math.sin(angle), z];
  });
}

function ballCup() {
  const center = BALL_CONNECTOR_CENTERS.cup, top = BALL_CONNECTOR_SIZES.cup[2];
  const floor = center - CAVITY_RADIUS;
  const heights = [...new Set([floor, SLOT_ROOT, SLOT_ROOT + .4, SLOT_ROOT + .8, center, center + LIP_RISE, top,
    ...outerProfile.map(([z]) => z),
    ...Array.from({length: 29}, (_, i) => center - CAVITY_RADIUS * Math.cos(Math.PI * i / 56)),
    ...Array.from({length: 14}, (_, i) => center + LIP_RISE * i / 14),
  ])].sort((a, b) => a - b);
  const positions = [];

  function triangle(a, b, c) {
    // The first cavity ring is a single pole, so one triangle in each fan quad collapses.
    if (a.every((v, i) => v === b[i]) || b.every((v, i) => v === c[i]) || c.every((v, i) => v === a[i])) return;
    positions.push(...a, ...b, ...c);
  }
  function quad(a, b, c, d, reverse = false) {
    if (reverse) { triangle(a, c, b); triangle(a, d, c); }
    else { triangle(a, b, c); triangle(a, c, d); }
  }
  function join(low, high, reverse = false) {
    for (let i = 0; i < FACETS_PER_PETAL; i++) quad(low[i], low[i + 1], high[i + 1], high[i], reverse);
  }

  for (let sector = 0; sector < 4; sector++) {
    let outside = petalRing(outerRadius(0), 0, sector), inside = null;
    for (let i = 0; i < FACETS_PER_PETAL; i++) triangle([0, 0, 0], outside[i + 1], outside[i]);
    for (const z of heights.slice(1)) {
      const upperOuter = petalRing(outerRadius(z), z, sector);
      join(outside, upperOuter);
      if (z >= floor) {
        const upperInner = petalRing(cupRim(z, center, top), z, sector);
        if (inside) {
          join(inside, upperInner, true);
          if (z > SLOT_ROOT) {
            const last = FACETS_PER_PETAL;
            quad(outside[0], upperOuter[0], upperInner[0], inside[0]);
            quad(outside[last], inside[last], upperInner[last], upperOuter[last]);
          }
        }
        inside = upperInner;
      }
      outside = upperOuter;
    }
    join(outside, inside);
  }
  return Float32Array.from(positions);
}

export function ballConnectorMesh(role) {
  if (role !== 'plug' && role !== 'cup') return null;
  const positions = role === 'plug' ? ballPlug() : ballCup();
  return {positions, size: [...BALL_CONNECTOR_SIZES[role]]};
}
