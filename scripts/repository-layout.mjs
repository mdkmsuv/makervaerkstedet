// Publisher/developer source paths; one JSON mapping shared with Python.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const marker='REPOSITORY-LAYOUT.json';
export function repoRoot(start=import.meta.url){
  let current=start.startsWith('file:')?fileURLToPath(start):path.resolve(start);
  if(!fs.statSync(current).isDirectory())current=path.dirname(current);
  for(;;){
    if(fs.existsSync(path.join(current,marker))){readLayout(current);return current;}
    const parent=path.dirname(current);if(parent===current)throw Error('repository_layout_missing');current=parent;
  }
}
function relative(value){if(typeof value!=='string'||!value||value.includes('\\')||value.includes(':')||value.startsWith('/')||value.split('/').some(p=>!p||p==='.'||p==='..'))throw Error('invalid_source_path');return value;}
function readLayout(root){
  const value=JSON.parse(fs.readFileSync(path.join(root,marker),'utf8'));
  if(value.schema!==1||value.system!=='system'||value.apps!=='apps'||value.sdk!=='sdk'||typeof value.source_aliases!=='object')throw Error('invalid_repository_layout');
  for(const [old,next] of Object.entries(value.source_aliases)){relative(old);relative(next);}return value;
}
export function sourcePath(logical,root=repoRoot()){
  relative(logical);const aliases=readLayout(root).source_aliases;
  for(const old of Object.keys(aliases).sort((a,b)=>b.length-a.length))if(logical===old||logical.startsWith(old+'/'))return path.join(root,aliases[old]+logical.slice(old.length));
  return path.join(root,logical);
}
