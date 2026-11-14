"""Require ignored viewer/Folie outputs to match the selected public source."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import re

TARGETS={
    'system':('system/web/vendor',{'viewer.js'},
              {'scripts/build_web.mjs','system/web/viewer.js','sdk/web/stl-validation.mjs'}),
    'folie':('apps/folie/web/vendor',{'laser.js','laser-trace-worker.js'},
             {'apps/folie/build.mjs','apps/folie/web/laser.js','apps/folie/web/laser-trace-worker.js'}),
}
COMMON={'REPOSITORY-LAYOUT.json','package.json','package-lock.json',
        'scripts/repository-layout.mjs','scripts/web-build-receipt.mjs'}


def _path(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name:raise ValueError('invalid_web_source_path')
    path=PurePosixPath(name)
    if path.is_absolute() or path.as_posix()!=name or any(p in ('.','..','.git','node_modules') for p in path.parts):
        raise ValueError('invalid_web_source_path')
    return name


def _text_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def validate_web_build(root,target,built_from=None):
    if target not in TARGETS:raise ValueError('unknown_web_build_target')
    root=Path(root);built_from=Path(built_from) if built_from is not None else root
    directory,names,required=TARGETS[target]
    receipt=root/directory/'web-build-manifest.json'
    if not receipt.is_file() or receipt.stat().st_size>1024*1024:raise ValueError('web_build_receipt_required:'+target)
    value=json.loads(receipt.read_text(encoding='utf-8'))
    if (not isinstance(value,dict) or set(value)!={'schema','target','source_sha256','outputs'}
            or type(value['schema']) is not int or value['schema']!=1 or value['target']!=target
            or not isinstance(value['source_sha256'],dict) or not isinstance(value['outputs'],dict)
            or set(value['outputs'])!=names or not COMMON|required<=set(value['source_sha256'])):
        raise ValueError('invalid_web_build_receipt:'+target)
    for name,item in value['outputs'].items():
        output=root/directory/name
        if (not isinstance(item,dict) or set(item)!={'sha256','size'} or type(item['size']) is not int
                or not 0<=item['size']<=128*1024*1024 or not re.fullmatch('[a-f0-9]{64}',str(item['sha256']))
                or not output.is_file() or output.stat().st_size!=item['size']
                or hashlib.sha256(output.read_bytes()).hexdigest()!=item['sha256']):
            raise ValueError('web_output_hash_mismatch:'+name)
    for name,want in value['source_sha256'].items():
        _path(name)
        if not re.fullmatch('[a-f0-9]{64}',str(want)):raise ValueError('invalid_web_source_digest')
        source,built=root/name,built_from/name
        if not source.is_file() or not built.is_file() or _text_hash(source)!=want or _text_hash(built)!=want:
            raise ValueError('web_source_hash_mismatch:'+name)
    return value
