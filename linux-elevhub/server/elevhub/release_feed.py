"""GitHub release discovery and verified downloads, independent of AI/browser life.

Only the background worker accesses GitHub or protected staging. A teacher can
select an owner/repository, request a check, or queue a published package. This
module never extracts or executes downloaded code. Kreditering: Klostermarksskolen.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

INTERVAL=24*60*60
MAX_METADATA=2*1024*1024
MAX_MANIFEST=256*1024
MAX_PACKAGE=2*1024**3
ROLES={'linux_student':'linux-queue-v1','windows_laser_teacher':'laser-code-v1','windows_server':'school-server-v1'}
REPOSITORY=re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')
ASSET=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.zip')
HOSTS={'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}


class FeedConflict(ValueError): pass
class FeedInvalid(ValueError): pass
class FeedCancelled(Exception): pass


def plain(path):
    path=Path(path).absolute()
    for item in (path,*path.parents):
        try: info=item.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise FeedInvalid('linked_path')
    return path


def strict_json(value):
    def pairs(items):
        result={}
        for key,item in items:
            if key in result: raise FeedInvalid('duplicate_json')
            result[key]=item
        return result
    try: result=json.loads(value,object_pairs_hook=pairs)
    except (ValueError,UnicodeError) as error: raise FeedInvalid('invalid_json') from error
    if not isinstance(result,dict): raise FeedInvalid('invalid_json')
    return result


def valid_asset(name):
    return (isinstance(name,str) and ASSET.fullmatch(name)
        and name.split('.')[0].upper() not in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]})


def validate_download_row(row,repository):
    """Treat shared-database rows as untrusted at the SYSTEM boundary."""
    if (not isinstance(row.get('id'),str) or not re.fullmatch('[a-f0-9]{32}',row['id'])
            or not isinstance(row.get('repository'),str) or not REPOSITORY.fullmatch(row['repository'])
            or row['repository']!=repository or type(row.get('release_id')) is not int or row['release_id']<=0
            or not valid_asset(row.get('asset')) or not isinstance(row.get('package'),str)
            or len(row['package'])>MAX_MANIFEST):raise FeedInvalid('invalid_download_record')
    package=strict_json(row['package'])
    if (set(package)!={'role','asset','format','sha256','size','url','version'}
            or not isinstance(package.get('role'),str) or package['role'] not in ROLES
            or package.get('format')!=ROLES[package['role']] or package.get('asset')!=row['asset']
            or not isinstance(package.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',package['sha256'])
            or type(package.get('size')) is not int or not 0<package['size']<MAX_PACKAGE
            or not isinstance(package.get('version'),str) or not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?',package['version'])
            or not isinstance(package.get('url'),str)
            or not re.fullmatch(r'https://github\.com/'+re.escape(repository)+r'/releases/download/[A-Za-z0-9][A-Za-z0-9_.-]{0,120}/'+re.escape(row['asset']),package['url'])):
        raise FeedInvalid('invalid_download_record')
    try:datetime.datetime.strptime(package['version'][:10],'%Y.%m.%d')
    except ValueError as error:raise FeedInvalid('invalid_download_record') from error
    trusted_url(package['url'],repository)
    return package


def trusted_url(url,repository):
    if not isinstance(url,str) or len(url)>8192 or any(ord(c)<=32 or c=='\\' for c in url):
        raise FeedInvalid('untrusted_url')
    try:
        parsed=urllib.parse.urlsplit(url)
        if (parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password
                or parsed.port not in (None,443) or parsed.fragment): raise FeedInvalid('untrusted_url')
    except ValueError as error: raise FeedInvalid('untrusted_url') from error
    if parsed.hostname=='api.github.com' and (parsed.path!=f'/repos/{repository}/releases/latest' or parsed.query):
        raise FeedInvalid('untrusted_api_url')
    if parsed.hostname=='github.com' and not parsed.path.startswith(f'/{repository}/releases/download/'):
        raise FeedInvalid('untrusted_asset_url')
    return url


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self,repository): self.repository=repository;self.redirects=0
    def redirect_request(self,request,fp,code,message,headers,newurl):
        self.redirects+=1
        if self.redirects>5: raise FeedInvalid('too_many_redirects')
        newurl=urllib.parse.urljoin(request.full_url,newurl)
        trusted_url(newurl,self.repository)
        return super().redirect_request(request,fp,code,message,headers,newurl)


def open_github(url,repository):
    trusted_url(url,repository)
    request=urllib.request.Request(url,headers={'User-Agent':'Makervaerkstedet-Updater/1',
        'Accept':'application/vnd.github+json' if url.startswith('https://api.github.com/') else 'application/octet-stream',
        'X-GitHub-Api-Version':'2026-03-10'})
    opener=urllib.request.build_opener(GitHubRedirect(repository))
    response=opener.open(request,timeout=30)
    try:
        trusted_url(response.geturl(),repository)
        if response.status!=200: raise FeedInvalid('unexpected_http_status')
    except Exception:
        response.close();raise
    return response


def bounded_json(url,repository,maximum,opener):
    with opener(url,repository) as response:
        content=response.read(maximum+1)
    if len(content)>maximum: raise FeedInvalid('metadata_too_large')
    return strict_json(content)


def release_manifest(metadata,manifest,repository):
    if (type(metadata.get('id')) is not int or metadata['id']<=0
            or metadata.get('draft') is not False or metadata.get('prerelease') is not False):
        raise FeedInvalid('no_stable_release')
    tag=metadata.get('tag_name')
    if not isinstance(tag,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}',tag):
        raise FeedInvalid('invalid_release_tag')
    if (set(manifest)!={'schema','product','version','notes','packages'} or type(manifest.get('schema')) is not int or manifest['schema']!=1
            or manifest.get('product')!='Makervaerkstedet'
            or not isinstance(manifest.get('version'),str)
            or not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?',manifest['version'])
            or not isinstance(manifest.get('notes'),str) or len(manifest['notes'])>20000
            or not isinstance(manifest.get('packages'),list) or not 1<=len(manifest['packages'])<=3):
        raise FeedInvalid('invalid_manifest')
    try: datetime.datetime.strptime(manifest['version'][:10],'%Y.%m.%d')
    except ValueError as error: raise FeedInvalid('invalid_version') from error
    assets={}
    if not isinstance(metadata.get('assets'),list) or len(metadata['assets'])>200: raise FeedInvalid('invalid_assets')
    for asset in metadata['assets']:
        if not isinstance(asset,dict) or not isinstance(asset.get('name'),str) or asset['name'] in assets:
            raise FeedInvalid('duplicate_asset')
        assets[asset['name']]=asset
    packages=[];roles=set();names=set()
    for package in manifest['packages']:
        if (not isinstance(package,dict) or set(package)!={'role','asset','sha256','size','format'}
                or not isinstance(package.get('role'),str) or package['role'] not in ROLES or package.get('format')!=ROLES[package['role']]
                or not valid_asset(package.get('asset'))
                or not isinstance(package.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',package['sha256'])
                or type(package.get('size')) is not int or not 0<package['size']<MAX_PACKAGE
                or package['role'] in roles or package['asset'].casefold() in names):
            raise FeedInvalid('invalid_package')
        asset=assets.get(package['asset'],{})
        expected=f'https://github.com/{repository}/releases/download/{tag}/{package["asset"]}'
        if (asset.get('state')!='uploaded' or type(asset.get('size')) is not int
                or asset['size']!=package['size'] or asset.get('browser_download_url')!=expected):
            raise FeedInvalid('asset_binding_mismatch')
        trusted_url(expected,repository)
        packages.append({**package,'url':expected});roles.add(package['role']);names.add(package['asset'].casefold())
    return {'repository':repository,'release_id':metadata['id'],'version':manifest['version'],
            'notes':manifest['notes'],'packages':packages}


class ReleaseFeed:
    def __init__(self,data_root,clock=time.time,opener=open_github,cancel_event=None):
        self.root=plain(Path(data_root)/'updates');self.root.mkdir(parents=True,exist_ok=True)
        self.path=plain(self.root/'feed.sqlite3');self.clock=clock;self.opener=opener
        self.cancel_event=cancel_event
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1),last_checked REAL NOT NULL DEFAULT 0,worker_checked REAL NOT NULL DEFAULT 0,requested INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',latest TEXT);
            INSERT OR IGNORE INTO state(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS downloads(id TEXT PRIMARY KEY,repository TEXT NOT NULL,release_id INTEGER NOT NULL,asset TEXT NOT NULL,package TEXT NOT NULL,status TEXT NOT NULL,updated REAL NOT NULL,path TEXT,error TEXT NOT NULL DEFAULT '',UNIQUE(repository,release_id,asset));
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS importer_state(id INTEGER PRIMARY KEY CHECK(id=1),checked REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL,actor TEXT,action TEXT,target TEXT);''')
    def ensure_running(self):
        if self.cancel_event is not None and self.cancel_event.is_set():raise FeedCancelled()
    @contextmanager
    def response(self,url,repository):
        self.ensure_running()
        with self.opener(url,repository) as response:
            self.ensure_running()
            yield response
    @contextmanager
    def connect(self):
        plain(self.path);db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def config(self):
        path=plain(self.root/'feed.json')
        if not path.exists(): return None
        if path.stat().st_size>4096: raise FeedInvalid('invalid_feed_configuration')
        value=strict_json(path.read_bytes())
        if (set(value)!={'schema','repository','enabled'} or type(value.get('schema')) is not int or value['schema']!=1
                or type(value.get('enabled')) is not bool or not isinstance(value.get('repository'),str)
                or not REPOSITORY.fullmatch(value['repository'])): raise FeedInvalid('invalid_feed_configuration')
        return value
    def snapshot(self):
        config=self.config()
        with self.connect() as db:
            row=dict(db.execute('SELECT * FROM state WHERE id=1').fetchone())
            downloads=[{k:r[k] for k in ('id','release_id','asset','status','updated','error')} for r in db.execute('SELECT * FROM downloads ORDER BY updated DESC LIMIT 100')]
            importer=db.execute('SELECT checked FROM importer_state WHERE id=1').fetchone()
        latest=json.loads(row['latest']) if row['latest'] else None
        if latest and (not config or latest['repository']!=config['repository']): latest=None
        if latest:
            latest={**latest,'packages':[{k:v for k,v in p.items() if k!='url'} for p in latest['packages']]}
        return {'schema':1,'repository':config['repository'] if config else None,'enabled':bool(config and config['enabled']),
            'state':'not_configured' if not config else 'disabled' if not config['enabled'] else 'check_failed' if row['error'] else 'available' if latest else 'not_checked' if not row['last_checked'] else 'no_release',
            'last_checked':row['last_checked'] or None,'error':row['error'] or None,'check_requested':bool(row['requested']),
            'worker_online':self.clock()-row['worker_checked']<180,
            'importer_online':bool(importer and self.clock()-importer['checked']<180),
            'latest':latest,'downloads':downloads}
    def importer_heartbeat(self):
        """Liveness only; this does not authorize code, imports or installation."""
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO importer_state VALUES(1,?)',(self.clock(),))
    def execute(self,actor,body):
        allowed={'configure':{'repository'},'check':set(),'download':{'release_id','asset'}}
        if (not isinstance(body,dict) or set(body)!={'request_id','action','params'}
                or not isinstance(body['request_id'],str) or not re.fullmatch('[a-f0-9]{32}',body['request_id'])
                or body['action'] not in allowed or not isinstance(body['params'],dict) or set(body['params'])!=allowed[body['action']]):
            raise FeedInvalid('invalid_command')
        action,params=body['action'],body['params'];payload=json.dumps({'actor':actor,**body},sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM requests WHERE id=?',(body['request_id'],)).fetchone()
            if old:
                if old['payload']!=payload: raise FeedConflict('Anmodnings-ID er allerede brugt.')
                return json.loads(old['result'])
            config=self.config();result={'ok':True}
            if action=='configure':
                repository=params['repository']
                if not isinstance(repository,str) or not REPOSITORY.fullmatch(repository): raise FeedInvalid('invalid_repository')
                if config and config['repository']!=repository: raise FeedConflict('Udgivelseskilden er allerede valgt. Kontakt skolens IT ved ændring.')
                if not config:
                    path=plain(self.root/'feed.json');temporary=plain(self.root/('feed-'+secrets.token_hex(12)+'.tmp'))
                    try:
                        with temporary.open('x',encoding='utf-8') as stream:
                            json.dump({'schema':1,'repository':repository,'enabled':True},stream);stream.flush();os.fsync(stream.fileno())
                        os.replace(temporary,path)
                    finally: temporary.unlink(missing_ok=True)
                db.execute('UPDATE state SET requested=1 WHERE id=1')
            elif not config or not config['enabled']: raise FeedInvalid('feed_not_enabled')
            elif action=='check': db.execute('UPDATE state SET requested=1 WHERE id=1')
            else:
                row=db.execute('SELECT latest FROM state WHERE id=1').fetchone();latest=json.loads(row[0]) if row[0] else None
                if (not latest or type(params['release_id']) is not int or params['release_id']!=latest['release_id']
                        or latest['repository']!=config['repository']): raise FeedInvalid('release_not_available')
                package=next((p for p in latest['packages'] if p['asset']==params['asset']),None)
                if not package: raise FeedInvalid('package_not_available')
                existing=db.execute('SELECT id,status FROM downloads WHERE repository=? AND release_id=? AND asset=?',
                    (config['repository'],latest['release_id'],package['asset'])).fetchone()
                identifier=existing['id'] if existing else secrets.token_hex(16)
                if not existing:
                    db.execute('INSERT INTO downloads(id,repository,release_id,asset,package,status,updated) VALUES(?,?,?,?,?,?,?)',
                        (identifier,config['repository'],latest['release_id'],package['asset'],json.dumps({**package,'version':latest['version']}),'queued',self.clock()))
                elif existing['status'] in ('failed','blocked'):
                    db.execute("UPDATE downloads SET status='queued',error='',updated=? WHERE id=?",(self.clock(),identifier))
                result['download_id']=identifier
            db.execute('INSERT INTO audit(at,actor,action,target) VALUES(?,?,?,?)',(self.clock(),actor,action,params.get('asset',params.get('repository','feed'))))
            db.execute('INSERT INTO requests VALUES(?,?,?)',(body['request_id'],payload,json.dumps(result)))
        return result
    def discover(self,repository):
        metadata=bounded_json(f'https://api.github.com/repos/{repository}/releases/latest',repository,MAX_METADATA,self.response)
        self.ensure_running()
        if metadata.get('draft') is not False or metadata.get('prerelease') is not False: raise FeedInvalid('no_stable_release')
        assets=metadata.get('assets',[])
        if not isinstance(assets,list): raise FeedInvalid('invalid_assets')
        manifest_assets=[a for a in assets if isinstance(a,dict) and a.get('name')=='makervaerkstedet-update.json']
        if len(manifest_assets)!=1: raise FeedInvalid('manifest_missing')
        asset=manifest_assets[0];tag=metadata.get('tag_name')
        if not isinstance(tag,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}',tag): raise FeedInvalid('invalid_release_tag')
        expected=f'https://github.com/{repository}/releases/download/{tag}/makervaerkstedet-update.json'
        if (asset.get('browser_download_url')!=expected or asset.get('state')!='uploaded'
                or type(asset.get('size')) is not int or not 0<asset['size']<=MAX_MANIFEST): raise FeedInvalid('manifest_asset_invalid')
        manifest=bounded_json(expected,repository,MAX_MANIFEST,self.response)
        self.ensure_running()
        return release_manifest(metadata,manifest,repository)
    @contextmanager
    def worker_lock(self,filename='feed-worker.lock'):
        if filename not in ('feed-worker.lock','feed-process.lock'):raise FeedInvalid('invalid_lock')
        with plain(self.root/filename).open('a+b') as stream:
            if stream.tell()==0:stream.write(b'0');stream.flush()
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
    def tick(self,staging_root):
        self.ensure_running()
        self.staging_root=plain(staging_root)
        with self.worker_lock():
            config=self.config();now=self.clock()
            with self.connect() as db:
                row=dict(db.execute('SELECT * FROM state WHERE id=1').fetchone())
                db.execute('UPDATE state SET worker_checked=? WHERE id=1',(now,))
            if not config or not config['enabled']: return
            if row['requested'] or not row['last_checked'] or now-row['last_checked']>=INTERVAL:
                error='';latest=row['latest']
                try: latest=json.dumps(self.discover(config['repository']))
                except urllib.error.HTTPError as failure:
                    error='no_release' if failure.code==404 else 'github_rate_limited' if failure.code in (403,429) else 'github_unavailable'
                    if failure.code==404:latest=None
                except FeedInvalid as failure:
                    error=str(failure) if str(failure) in ('manifest_missing','no_stable_release') else 'invalid_release'
                    latest=None
                except (OSError,TimeoutError,urllib.error.URLError):error='network_unavailable'
                self.ensure_running()
                with self.connect() as db:
                    db.execute('UPDATE state SET last_checked=?,requested=0,error=?,latest=? WHERE id=1',(self.clock(),error,latest))
            with self.connect() as db:
                db.execute("UPDATE downloads SET status='queued' WHERE status='downloading'")
                waiting=[dict(r) for r in db.execute("SELECT * FROM downloads WHERE status='queued' AND repository=? ORDER BY updated LIMIT 3",(config['repository'],))]
            for download in waiting:
                self.ensure_running()
                self.download(download,plain(staging_root))
    def download(self,row,staging_root):
        try:
            config=self.config()
            if not config:raise FeedInvalid('feed_not_configured')
            package=validate_download_row(row,config['repository'])
        except (FeedInvalid,KeyError,TypeError):
            with self.connect() as db:db.execute("UPDATE downloads SET status='blocked',error='invalid_download_record',updated=? WHERE id=?",(self.clock(),row.get('id')))
            return
        folder=plain(staging_root/row['id']);folder.mkdir(parents=True,exist_ok=True)
        if os.name!='nt':folder.chmod(0o700)
        destination=plain(folder/row['asset']);temporary=plain(folder/(row['asset']+'.part'))
        with self.connect() as db:db.execute("UPDATE downloads SET status='downloading',updated=? WHERE id=?",(self.clock(),row['id']))
        try:
            digest=hashlib.sha256();size=0;started=time.monotonic()
            with self.response(package['url'],row['repository']) as response,temporary.open('wb') as stream:
                length=response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length)!=package['size']):raise FeedInvalid('download_size_mismatch')
                while True:
                    self.ensure_running()
                    block=response.read(64*1024)
                    self.ensure_running()
                    if not block:break
                    size+=len(block)
                    if size>package['size'] or time.monotonic()-started>7200:raise FeedInvalid('download_limit')
                    stream.write(block);digest.update(block)
                stream.flush();os.fsync(stream.fileno())
            if size!=package['size'] or digest.hexdigest()!=package['sha256']:raise FeedInvalid('download_integrity_failed')
            self.ensure_running()
            os.replace(temporary,destination)
            self.ensure_running()
            with self.connect() as db:db.execute("UPDATE downloads SET status='ready',path=?,error='',updated=? WHERE id=?",(str(destination),self.clock(),row['id']))
        except FeedCancelled:
            temporary.unlink(missing_ok=True)
            raise
        except (OSError,TimeoutError,urllib.error.URLError,FeedInvalid):
            temporary.unlink(missing_ok=True)
            self.ensure_running()
            with self.connect() as db:db.execute("UPDATE downloads SET status='failed',error='download_not_verified',updated=? WHERE id=?",(self.clock(),row['id']))
    def ready_packages(self,staging_root=None):
        staging_root=plain(staging_root or getattr(self,'staging_root',None)) if staging_root or getattr(self,'staging_root',None) else None
        if staging_root is None:raise FeedInvalid('staging_root_required')
        config=self.config();repository=config['repository'] if config else ''
        with self.connect() as db: rows=[dict(r) for r in db.execute("SELECT * FROM downloads WHERE status='ready' ORDER BY updated")]
        result=[]
        for row in rows:
            try:
                package=validate_download_row(row,repository)
                # Never trust the path column written by a less privileged process.
                path=plain(staging_root/row['id']/row['asset'])
                path.relative_to(staging_root)
                if not path.is_file() or path.stat().st_size!=package['size']:raise FeedInvalid('staged_package_changed')
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest()!=package['sha256']:raise FeedInvalid('staged_package_changed')
                result.append({**package,'download_id':row['id'],'repository':row['repository'],'release_id':row['release_id'],'path':str(path)})
            except (OSError,ValueError,KeyError,TypeError):
                with self.connect() as db:db.execute("UPDATE downloads SET status='blocked',error='staged_package_not_verified',updated=? WHERE id=?",(self.clock(),row['id']))
        return result
    def mark_outcome(self,download_id,status,code=''):
        if (not isinstance(download_id,str) or not re.fullmatch('[a-f0-9]{32}',download_id)
                or status not in ('imported','blocked') or not re.fullmatch('[a-z_]{0,80}',code)):raise FeedInvalid('invalid_outcome')
        with self.connect() as db:
            db.execute("UPDATE downloads SET status=?,error=?,updated=? WHERE id=? AND status='ready'",(status,code,self.clock(),download_id))


class ReleaseFeedRunner:
    """Owned by the hub process, with bounded shutdown and no browser trigger."""
    def __init__(self,service,staging_root,interval=30):
        self.service=service;self.staging_root=plain(staging_root);self.interval=interval
        self.stop_event=threading.Event();self.started_event=threading.Event();self.running=False
        self.thread=None;service.cancel_event=self.stop_event
    def start(self):
        if self.thread is None:
            self.thread=threading.Thread(target=self.run,name='release-feed',daemon=True)
            self.thread.start()
        return self
    def run(self):
        try:
            # The same lifetime lock is used by the optional standalone CLI.
            with self.service.worker_lock('feed-process.lock'):
                self.running=True;self.started_event.set()
                while not self.stop_event.is_set():
                    try:self.service.tick(self.staging_root)
                    except FeedCancelled:break
                    except (OSError,sqlite3.Error,ValueError,TypeError):
                        # No raw URL, credential, filesystem path or exception
                        # text is published from this background thread.
                        if self.stop_event.is_set():break
                    self.stop_event.wait(self.interval)
        except OSError:
            # Another legitimate worker may already own this feed.
            pass
        finally:
            self.running=False;self.started_event.set()
    def stop(self,timeout=1):
        self.stop_event.set()
        if self.thread is not None:self.thread.join(timeout)
        return self.thread is None or not self.thread.is_alive()


def handle(handler,path,body=None):
    if not path.startswith('/api/updates/feed/'): return False
    from makerspace_api import _actor
    try:
        actor=_actor(handler,None)
        if actor is None:handler.svar(401,{'error':'Log ind på kontrolpanelet.'});return True
        service=getattr(handler.server,'release_feed',None)
        if service is None:handler.svar(503,{'error':'Udgivelseskilden er ikke tilgængelig.'});return True
        if handler.command in ('GET','HEAD') and path=='/api/updates/feed/state':result=service.snapshot()
        elif handler.command=='POST' and path=='/api/updates/feed/commands':result=service.execute(actor['id'],body)
        else:handler.svar(404,{'error':'Ukendt udgivelsesfunktion.'});return True
        handler.svar(200,result)
    except FeedConflict as error:handler.svar(409,{'error':str(error)})
    except PermissionError as error:handler.svar(403,{'error':str(error)})
    except (ValueError,TypeError,KeyError):handler.svar(400,{'error':'Kontrollér GitHub-lager, udgivelse og pakkevalg.'})
    except (OSError,sqlite3.Error):handler.svar(503,{'error':'Udgivelsesstatus kunne ikke bekræftes. Genbrug samme anmodning ved genforsøg.'})
    return True


def main():
    parser=argparse.ArgumentParser(description='Makerværkstedets selvstændige GitHub-kontrol og pakkedownload.')
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--staging-root',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if not args.data_root.is_absolute() or not args.staging_root.is_absolute():
        parser.error('Datamappe og stagingmappe skal være absolutte serverstier.')
    service=ReleaseFeed(args.data_root)
    with service.worker_lock('feed-process.lock'):
        while True:
            try:service.tick(args.staging_root)
            except (OSError,sqlite3.Error,ValueError,TypeError):
                print('release_feed_worker_unavailable',file=sys.stderr,flush=True)
                if args.once:return 1
            if args.once:return 0
            time.sleep(30)


if __name__=='__main__':
    raise SystemExit(main())
