const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const start = fs.readFileSync(path.join(root, 'ansible', 'roles', 'kiosk', 'templates', 'start.html.j2'), 'utf8');
const creative = fs.readFileSync(path.join(root, 'server', 'elevhub', 'web', 'assets', 'creative-hub.js'), 'utf8');

for (const id of ['3dprint', 'laser', 'folie', 'microbit', 'datatraener', 'skolegpt', 'scratch', 'kredsloeb', 'internet']) {
  assert.match(start, new RegExp("id: '" + id + "'"), `offline catalog is missing ${id}`);
}
assert.match(start, /OFFLINE_APPS/);
assert.match(start, /fejl\.name === 'AbortError' \|\| fejl\.httpStatus >= 500 \|\| fejl instanceof TypeError/);
assert.match(start, /app\.offline_unavailable/);
assert.match(creative, /offline = false/);
assert.match(creative, /connection\(ok,offlineMode=false\)/);
assert.match(creative, /Tilgængelig offline/);
assert.match(start, /id: 'microbit',[^\n]+requires_internet: true/);
assert.match(start, /id: 'skolegpt',[^\n]+requires_internet: true/);
assert.match(creative, /app\.requires_internet \? 'Kræver internet'/);
assert.match(fs.readFileSync(path.join(root, 'server', 'elevhub', 'web', 'assets', 'workshops.js'), 'utf8'), /'3dprint':[^\n]+image:'workshop-3d-bauhaus-v1\.png'/);
console.log('kiosk offline catalog regression: ok');
