"""Server-only signed GitHub snapshot descriptor. Student wire is unchanged."""
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import quote
if __package__ == 'app':
    from .simple_release import (_minisign, _duplicates, require, ReleaseVerificationError,
                                 REPOSITORY, MAX_SIGNATURE, MAX_ARTIFACT)
else:
    # Legacy Windows teacher entry has no product root in sys.path. Load only
    # the explicit sibling shared file, never search another checkout.
    _spec=importlib.util.spec_from_file_location('_mkv_feed_shared',Path(__file__).absolute().with_name('simple_release.py'))
    _shared=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_shared)
    _minisign,_duplicates,require=_shared._minisign,_shared._duplicates,_shared.require
    ReleaseVerificationError=_shared.ReleaseVerificationError
    REPOSITORY,MAX_SIGNATURE,MAX_ARTIFACT=_shared.REPOSITORY,_shared.MAX_SIGNATURE,_shared.MAX_ARTIFACT

ASSET='makervaerkstedet-apt-repository.zip'
DESCRIPTOR='makervaerkstedet-updates.json'

def verify_descriptor(raw: bytes, signature: bytes, *, minisign_exe: Path, minisign_sha256: str,
                      public_key: str, now: datetime, highest_sequence: int=0,
                      highest_sha256: str|None=None) -> dict:
    require(type(raw) is bytes and 0<len(raw)<=65536,'invalid_feed_size')
    require(type(signature) is bytes and 0<len(signature)<=MAX_SIGNATURE,'invalid_signature_size')
    _minisign(raw,signature,minisign_exe,minisign_sha256,public_key)
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ReleaseVerificationError('invalid_json_number')))
    except ReleaseVerificationError:raise
    except (ValueError,UnicodeError,RecursionError) as error:raise ReleaseVerificationError('invalid_feed_json') from error
    require(type(value) is dict and set(value)=={'schema','repository','release_tag','sequence','snapshot'},'invalid_feed_fields')
    require(type(value['schema']) is int and value['schema']==1,'unsupported_feed_schema')
    require(value['repository']==REPOSITORY,'feed_repository_mismatch')
    require(isinstance(value['release_tag'],str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+~\-]{0,127}',value['release_tag']) is not None and '..' not in value['release_tag'],'invalid_release_tag')
    require(type(value['sequence']) is int and 0<value['sequence']<=2**63-1,'invalid_feed_sequence')
    require(type(highest_sequence) is int and highest_sequence>=0,'invalid_feed_highwater')
    # now is retained for API compatibility; project releases do not expire.
    snapshot=value['snapshot']
    require(type(snapshot) is dict and set(snapshot)=={'name','sha256','size'},'invalid_snapshot_fields')
    require(snapshot['name']==ASSET,'invalid_snapshot_name')
    require(isinstance(snapshot['sha256'],str) and re.fullmatch('[a-f0-9]{64}',snapshot['sha256']) is not None,'invalid_snapshot_hash')
    require(type(snapshot['size']) is int and 0<snapshot['size']<=MAX_ARTIFACT,'invalid_snapshot_size')
    digest=hashlib.sha256(raw).hexdigest()
    require(value['sequence']>=highest_sequence,'feed_rollback_rejected')
    if highest_sha256 is not None:
        require(re.fullmatch('[a-f0-9]{64}',highest_sha256) is not None,'invalid_feed_highwater_hash')
        require(value['sequence']!=highest_sequence or digest==highest_sha256,'feed_sequence_collision')
    value['_descriptor_sha256']=digest
    value['_snapshot_url']='https://github.com/'+REPOSITORY+'/releases/download/'+quote(value['release_tag'],safe='')+'/'+ASSET
    return value
