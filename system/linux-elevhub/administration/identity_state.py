"""Retain publisher trust while separating enrolled devices' operation journals."""
from copy import deepcopy
import hashlib
import json
import re
from template_activation import atomic,json_bytes,plain,protected

HEX=re.compile('[a-f0-9]{64}\\Z')


def _metadata(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('invalid_update_identity')
            result[key]=value
        return result
    try:return json.loads(raw,object_pairs_hook=pairs)
    except (ValueError,UnicodeError,RecursionError):raise ValueError('invalid_update_identity') from None


def scoped_ledger(state,ledger,identity,*,kind):
    if identity is None:return ledger  # Synthetic adapters without credentials.
    if not isinstance(identity,str) or HEX.fullmatch(identity) is None:raise ValueError('invalid_update_identity')
    if kind not in ('core','apps'):raise ValueError('invalid_identity_journal')
    scope=plain(state/('scope-'+kind+'.json'))
    metadata=_metadata(protected(scope,private=True).read_bytes()) if scope.exists() else None
    if metadata is not None and (type(metadata) is not dict or set(metadata)!={'schema','identity_sha256'}
                                 or type(metadata['schema']) is not int or metadata['schema']!=1):
        raise ValueError('invalid_update_identity')
    embedded=ledger.get('identity_sha256')  # Normalize unreleased development journals.
    previous=metadata['identity_sha256'] if metadata is not None else embedded
    if previous is not None and (not isinstance(previous,str) or HEX.fullmatch(previous) is None):
        raise ValueError('invalid_update_identity')
    if embedded is not None and embedded!=previous:raise ValueError('identity_scope_conflict')
    if previous==identity and embedded is None:return ledger
    # The released schema1 core updater has no identity sidecar or migration
    # journals. Adopt its existing self-update receipt instead of discarding it
    # and restarting the pupil's kiosk a second time on the first bridge poll.
    legacy=(previous is None and kind=='core'
            and all(type(job) is dict and 'migration' not in job for job in ledger['jobs'].values()))
    retain=previous==identity or legacy
    # Archive complete prior operation/outbox state BEFORE switching authority.
    # The same content-addressed snapshot makes an interrupted switch repeatable.
    if not retain and (ledger['jobs'] or ledger['outbox']):
        directory=plain(state/'identities');directory.mkdir(mode=0o700,exist_ok=True)
        protected(directory,private=True,directory=True)
        encoded=json_bytes(ledger);digest=hashlib.sha256(encoded).hexdigest()
        archive=plain(directory/(kind+'-'+(previous or 'legacy')+'-'+digest+'.json'))
        if archive.exists():
            if protected(archive,private=True).read_bytes()!=encoded:raise ValueError('identity_archive_conflict')
        else:atomic(archive,encoded,0o600)
    value=deepcopy(ledger);value.pop('identity_sha256',None)
    if not retain:value.update(jobs={},outbox=[])
    # Keep the exact four-field core schema readable by the released updater
    # after an explicitly authorized rollback. Commit cleared active authority
    # BEFORE its new scope. A crash between these writes only repeats the
    # harmless scope switch against an already-empty active journal.
    active=plain(state/('ledger.json' if kind=='core' else 'apps-ledger.json'))
    atomic(active,json_bytes(value),0o600)
    atomic(scope,json_bytes({'schema':1,'identity_sha256':identity}),0o600)
    return value
