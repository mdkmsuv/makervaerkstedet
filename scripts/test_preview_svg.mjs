import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../web/design-inspiration.js', import.meta.url),'utf8');
const start=source.indexOf('function inheritedAttr');
const end=source.indexOf('export function previewSvg');
const helperSource=source.slice(start,end).replace('export function previewPathAttributes','function previewPathAttributes');
const context={};vm.runInNewContext(`${helperSource}\nthis.previewPathAttributes=previewPathAttributes`,context);
const {previewPathAttributes}=context;

function path(attrs, parent = null) {
  return {parentElement: parent, getAttribute(name){return attrs[name] ?? null;}, ownerDocument:{}};
}
const laserGroup=path({stroke:'#b4121a','stroke-width':'0.1','fill':'none'});
const vinylGroup=path({stroke:'#b4121a','stroke-width':'0.3','fill':'none'});
const laser=previewPathAttributes(path({d:'M0 0H10V10Z'}, laserGroup));
const vinyl=previewPathAttributes(path({d:'M1 1L8 1L8 8Z','fill-rule':'evenodd'}, vinylGroup));
for(const result of [laser,vinyl]){
  assert.ok(result.d.length > 0);
  assert.equal(result['stroke-width'],1.4);
  assert.equal(result['vector-effect'],'non-scaling-stroke');
}
assert.equal(vinyl['fill-rule'],'evenodd');
console.log('Preview SVG stroke tests: OK');
