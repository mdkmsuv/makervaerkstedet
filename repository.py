"""Build/SDK source paths. Installed program paths never pass this resolver."""
import json
from pathlib import Path, PurePosixPath

MARKER='REPOSITORY-LAYOUT.json'


def _relative(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value:
        raise ValueError('invalid_source_path')
    path=PurePosixPath(value)
    if path.is_absolute() or path.as_posix()!=value or any(part in ('.','..') for part in path.parts):
        raise ValueError('invalid_source_path')
    return value


def repo_root(start=__file__):
    path=Path(start).resolve()
    if not path.is_dir():path=path.parent
    for candidate in (path,*path.parents):
        marker=candidate/MARKER
        if marker.is_file():
            _layout(candidate)
            return candidate
    raise ValueError('repository_layout_missing')


def _layout(root):
    path=Path(root)/MARKER
    if path.stat().st_size>65536:raise ValueError('repository_layout_too_large')
    value=json.loads(path.read_text(encoding='utf-8'))
    if set(value)!={'schema','system','apps','sdk','source_aliases'} or value['schema']!=1:
        raise ValueError('invalid_repository_layout')
    if (value['system'],value['apps'],value['sdk'])!=('system','apps','sdk') or not isinstance(value['source_aliases'],dict):
        raise ValueError('invalid_repository_layout')
    for old,new in value['source_aliases'].items():_relative(old);_relative(new)
    return value


def source_path(logical,root=None):
    """Resolve historical build names without changing installation targets."""
    logical=_relative(logical);root=Path(root) if root is not None else repo_root()
    if root.name=='system' and (root.parent/MARKER).is_file():root=root.parent
    if not (root/MARKER).is_file():return root/logical  # Explicit legacy/isolated build fixture.
    aliases=_layout(root)['source_aliases']
    for old in sorted(aliases,key=len,reverse=True):
        if logical==old or logical.startswith(old+'/'):
            return root/(aliases[old]+logical[len(old):])
    return root/logical


def system_root(root=None):return (Path(root) if root is not None else repo_root())/'system'
