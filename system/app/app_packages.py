"""Public app input locks and explicit release sets. Never installs software."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath

from .app_catalog import validate_app_descriptor, _relative

BASE_PACKAGES = frozenset(('makervaerkstedet-elev', 'makervaerkstedet-updater',
                           'makervaerkstedet-matchbox'))
LEGACY_PACKAGES = BASE_PACKAGES | {'makervaerkstedet-scratch'}
BUILTIN_APPS = frozenset(('laser', 'folie', '3dprint', 'scratch', 'microbit'))


def build_gate(app_id):
    """Publisher build gates, never imported by the school runtime/updater."""
    if app_id!='3dprint':return None
    source=Path(__file__).resolve().parents[2]/'apps/3dprint/validate_build.py'
    spec=importlib.util.spec_from_file_location('_mkv_model3d_build_gate',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def owned_path(descriptor, target):
    """Only this app's program tree; never configuration, projects or scripts."""
    validate_app_descriptor(descriptor)
    prefixes = [f"usr/share/makervaerkstedet/apps/{descriptor['id']}/"]
    if descriptor['id'] == 'scratch':
        prefixes.append('usr/local/share/elevhub/apps/scratch/')
    return (_relative(target) and any(target.startswith(p) for p in prefixes)
            and target != f"usr/share/makervaerkstedet/apps/{descriptor['id']}/app.json")


def validate_spec(spec):
    if type(spec) is not dict or 'app_descriptor' not in spec:
        raise ValueError('app_descriptor_required')
    descriptor = validate_app_descriptor(spec['app_descriptor'])
    if (spec['package'], spec['version'], spec['architecture']) != (
            descriptor['package'], descriptor['version'], descriptor['architecture']):
        raise ValueError('app_package_identity_mismatch')
    manifest = f"usr/share/makervaerkstedet/apps/{descriptor['id']}/app.json"
    manifests = [item for item in spec['files'] if item['path'] == manifest]
    if len(manifests) != 1 or json.loads(Path(manifests[0]['source']).read_text(encoding='utf-8')) != descriptor:
        raise ValueError('app_descriptor_file_mismatch')
    for item in spec['files']:
        if item['path'] != manifest and not owned_path(descriptor, item['path']):
            raise ValueError('app_file_outside_owned_tree')
    if spec['depends'] != [f"makervaerkstedet-elev (>= {descriptor['min_core']})"]:
        raise ValueError('invalid_app_dependencies')
    return descriptor


def prepare(source_root, descriptor, manifest, stage, *, scratch_lock=None,vendor_built_from=None):
    descriptor = validate_app_descriptor(descriptor)
    gate=build_gate(descriptor['id'])
    if gate is not None:gate.validate_model3d_bundle(source_root,vendor_built_from)
    if descriptor['id']=='folie':
        source=Path(__file__).resolve().parents[2]/'scripts/validate_web_build.py'
        spec=importlib.util.spec_from_file_location('_mkv_web_build_gate',source)
        web_gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(web_gate)
        web_gate.validate_web_build(source_root,'folie',vendor_built_from)
    if type(manifest) is not dict or set(manifest) != {'schema', 'files'} or type(manifest['schema']) is not int or manifest['schema'] != 1 or type(manifest['files']) is not list:
        raise ValueError('invalid_app_file_manifest')
    source_root, stage = Path(source_root).resolve(), Path(stage)
    files = {}; targets = set()
    def add(source, target, mode=0o644, expected=None):
        if not owned_path(descriptor, target) or target.casefold() in targets:
            raise ValueError('invalid_app_file_ownership')
        source = Path(source)
        if not source.is_file() or any(p.is_symlink() for p in (source, *source.parents)):
            raise ValueError('app_source_must_be_regular_file')
        if mode not in (0o644, 0o755): raise ValueError('invalid_app_file_mode')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if expected is not None and expected != digest: raise ValueError('app_source_hash_mismatch')
        files[target] = {'source': str(source), 'path': target, 'sha256': digest, 'mode': mode}
        targets.add(target.casefold())
    for entry in manifest['files']:
        if type(entry) is not dict or set(entry) != {'source', 'path', 'tree'} or not _relative(entry['source']) or type(entry['tree']) is not bool:
            raise ValueError('invalid_app_file_entry')
        source = source_root / entry['source']
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise ValueError('app_source_must_be_regular_file')
        if entry['tree']:
            if not source.is_dir(): raise ValueError('app_source_directory_missing:' + str(source))
            found = False
            for path in sorted(source.rglob('*')):
                if path.is_file():
                    found = True
                    add(path, entry['path'] + '/' + path.relative_to(source).as_posix())
            if not found: raise ValueError('empty_app_source_tree')
        else: add(source, entry['path'])
    if scratch_lock is not None:
        if descriptor['id'] != 'scratch' or scratch_lock['package'] != 'makervaerkstedet-scratch':
            raise ValueError('invalid_scratch_input_lock')
        legacy_targets=set()
        for item in scratch_lock['files']:
            if item['path'].casefold() in legacy_targets:raise ValueError('duplicate_scratch_input_path')
            legacy_targets.add(item['path'].casefold())
            if item['path'] == 'usr/share/makervaerkstedet/apps/scratch/app.json': continue
            if item['path'] in files:continue  # Explicit reviewed app wrapper wins over the old bundled wrapper.
            add(item['source'], item['path'], item['mode'], item['sha256'])
    if not files: raise ValueError('empty_app_package')
    stage.mkdir(parents=True, exist_ok=True)
    path = stage / ('app-' + descriptor['id'] + '.json')
    data = (json.dumps(descriptor, sort_keys=True, ensure_ascii=False) + '\n').encode()
    if path.exists() and path.read_bytes() != data: raise ValueError('app_stage_identity_exists')
    path.write_bytes(data)
    target = f"usr/share/makervaerkstedet/apps/{descriptor['id']}/app.json"
    files[target] = {'source': str(path), 'path': target, 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644}
    return {'schema': 'makervaerkstedet.debian-inputs.v2', 'package': descriptor['package'],
            'version': descriptor['version'], 'architecture': descriptor['architecture'],
            'depends': [f"makervaerkstedet-elev (>= {descriptor['min_core']})"],
            'files': [files[key] for key in sorted(files)], 'app_descriptor': descriptor,
            'provenance': {'component': 'app-' + descriptor['id'], 'source': 'explicit app file manifest'}}


def release_packages(specs, release_set='legacy'):
    """Validate an exact set, including app identity; no arbitrary apt package."""
    names = [s['package'] for s in specs]
    if len(set(names)) != len(names): raise ValueError('duplicate_release_package')
    names = set(names)
    apps = []
    for spec in specs:
        if spec['package'] not in BASE_PACKAGES:
            if release_set == 'legacy' and spec['package'] == 'makervaerkstedet-scratch': continue
            apps.append(validate_spec(spec)['id'])
    if release_set == 'legacy': valid = names == LEGACY_PACKAGES
    elif release_set == 'base': valid = names == BASE_PACKAGES
    elif release_set == 'app': valid = BASE_PACKAGES <= names and len(apps) == 1 and len(names) == 4
    elif release_set == 'migration': valid = BASE_PACKAGES <= names and set(apps) == BUILTIN_APPS and len(names) == 8
    else: raise ValueError('invalid_release_set')
    if not valid: raise ValueError('exact_release_package_set_required')
    return names
