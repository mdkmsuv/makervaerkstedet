const SVG_NS = 'http://www.w3.org/2000/svg';

const ICON_PATHS = {
  mail: [
    ['rect', {x: '3', y: '5', width: '18', height: '14', rx: '2'}],
    ['path', {d: 'm4 7 8 6 8-6'}]
  ],
  save: [
    ['path', {d: 'M5 3h11l3 3v15H5z'}],
    ['path', {d: 'M8 3v6h8V3M8 21v-7h8v7'}]
  ],
  folder: [
    ['path', {d: 'M3 6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'}]
  ],
  eye: [
    ['path', {d: 'M2.5 12s3.5-5 9.5-5 9.5 5 9.5 5-3.5 5-9.5 5-9.5-5-9.5-5Z'}],
    ['circle', {cx: '12', cy: '12', r: '2.5'}]
  ],
  book: [
    ['path', {d: 'M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17H6.5A2.5 2.5 0 0 0 4 21.5z'}],
    ['path', {d: 'M4 4.5v17M8 6h8'}]
  ],
  'plus-file': [
    ['path', {d: 'M6 3h8l4 4v14H6zM14 3v5h4'}],
    ['path', {d: 'M12 12v6M9 15h6'}]
  ],
  download: [
    ['path', {d: 'M12 3v12M7 10l5 5 5-5M4 20h16'}]
  ],
  help: [
    ['circle', {cx: '12', cy: '12', r: '9'}],
    ['path', {d: 'M9.5 9a2.5 2.5 0 1 1 4.2 1.8c-1 .8-1.7 1.2-1.7 2.7M12 17h.01'}]
  ],
  library: [
    ['path', {d: 'M5 4h3v16H5zM10.5 4h3v16h-3zM16 4h3v16h-3z'}],
    ['path', {d: 'M3 20h18'}]
  ],
  target: [
    ['circle', {cx: '12', cy: '12', r: '8'}],
    ['circle', {cx: '12', cy: '12', r: '3'}],
    ['path', {d: 'M12 2v3M12 19v3M2 12h3M19 12h3'}]
  ]
};

function createIcon(name) {
  const paths = ICON_PATHS[name];
  if (!paths) return null;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'laser-action-icon');
  svg.setAttribute('width', '20');
  svg.setAttribute('height', '20');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  for (const [tag, attributes] of paths) {
    const child = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attributes)) child.setAttribute(key, value);
    svg.append(child);
  }
  return svg;
}

export function addActionIcon(element, name) {
  if (!element || !ICON_PATHS[name]) return element;
  if (element.querySelector(':scope > .laser-action-icon')) return element;

  element.querySelectorAll(':scope > span[aria-hidden="true"]').forEach(node => node.remove());
  const label = document.createElement('span');
  label.className = 'laser-action-label';
  while (element.firstChild) label.append(element.firstChild);
  element.append(createIcon(name), label);
  element.classList.add('laser-action-button');
  return element;
}

export default addActionIcon;
