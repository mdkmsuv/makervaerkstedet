"""Read Debian control/data streams without extracting or executing app code."""
import json
from pathlib import PurePosixPath
import re
import subprocess
import tarfile
import threading
from urllib.parse import urlsplit
from app_catalog import validate_app_descriptor, _relative

MAX_EXPANDED=2*1024**3


def require(ok,code):
    if not ok:raise ValueError(code)


def _stream(path,flag,visit):
    process=subprocess.Popen(['/usr/bin/dpkg-deb',flag,str(path)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    timer=threading.Timer(60,process.kill);timer.daemon=True;timer.start()
    total=0;count=0
    try:
        with tarfile.open(fileobj=process.stdout,mode='r|') as stream:
            for member in stream:
                count+=1;total+=member.size
                require(count<=100000 and total<=MAX_EXPANDED,'app_archive_expansion_limit')
                visit(stream,member)
        require(process.wait(timeout=5)==0,'invalid_app_archive')
    except (tarfile.TarError,EOFError) as error:raise ValueError('invalid_app_archive') from error
    finally:
        timer.cancel()
        if process.poll() is None:process.kill()
        process.stdout.close();process.wait()


def inspect_deb(path,descriptor):
    app=validate_app_descriptor(descriptor);control={}
    def control_member(stream,item):
        name=item.name.removeprefix('./')
        if item.isdir():
            require(name in ('','.','/'),'app_control_entry_forbidden');return
        require(item.isfile() and name in ('control','md5sums') and name not in control
                and item.size<=1024**2,'app_control_entry_forbidden')
        control[name]=stream.extractfile(item).read()
    _stream(path,'--ctrl-tarfile',control_member)
    require('control' in control,'app_control_required')
    fields={};current=None
    for line in control['control'].decode('utf-8').splitlines():
        if line.startswith((' ','\t')):
            require(current=='Description','invalid_app_control');continue
        require(': ' in line,'invalid_app_control')
        key,value=line.split(': ',1)
        require(key not in fields,'duplicate_app_control_field')
        fields[key]=value;current=key
    require(set(fields)<=set(('Package','Version','Architecture','Depends','Maintainer','Section','Priority','Description','Breaks','Replaces')),'app_control_field_forbidden')
    require((fields.get('Package'),fields.get('Version'),fields.get('Architecture'))==
            (app['package'],app['version'],app['architecture']),'app_archive_identity_mismatch')
    require(fields.get('Depends')==f"makervaerkstedet-elev (>= {app['min_core']})",'app_archive_dependencies_mismatch')
    for key in ('Breaks','Replaces'):
        require(key not in fields or fields[key]=='makervaerkstedet-elev (<< 2.1.0~)','app_control_relationship_forbidden')
    prefix=f"usr/share/makervaerkstedet/apps/{app['id']}/"
    metadata=prefix+'app.json'
    prefixes=[prefix]
    if app['id']=='scratch':prefixes.append('usr/local/share/elevhub/apps/scratch/')
    seen=set();regular=set();manifest=None
    allocation={key:{'bytes':0,'files':0,'directories':0} for key in ('app','scratch')}
    def data_member(stream,item):
        nonlocal manifest
        name=item.name.removeprefix('./').rstrip('/')
        if name in ('','.'):
            require(item.isdir() and item.mode==0o755 and item.uid==0 and item.gid==0,'app_archive_path_forbidden')
            return
        require(_relative(name) and name.casefold() not in seen,'app_archive_path_forbidden')
        seen.add(name.casefold())
        group='scratch' if app['id']=='scratch' and (name.startswith(prefixes[-1]) or prefixes[-1].startswith(name+'/')) and not prefix.startswith(name+'/') else 'app'
        require(item.uid==0 and item.gid==0,'app_archive_owner_forbidden')
        if item.isdir():
            require(item.mode==0o755 and any(name.startswith(p) or p.startswith(name+'/') for p in prefixes),'app_archive_path_forbidden')
            allocation[group]['directories']+=1
            return
        require(item.isfile() and item.mode in (0o644,0o755) and any(name.startswith(p) for p in prefixes),'app_archive_path_forbidden')
        regular.add(name)
        allocation[group]['bytes']+=item.size;allocation[group]['files']+=1
        if name==metadata:
            require(item.size<=16384,'app_descriptor_too_large')
            manifest=json.loads(stream.extractfile(item).read())
    _stream(path,'--fsys-tarfile',data_member)
    require(manifest==app,'app_archive_descriptor_mismatch')
    start=urlsplit(app['start']).path.lstrip('/') or 'index.html'
    if urlsplit(app['start']).path=='/':
        start={'laser':'laservaerksted/index.html','folie':'laser.html','3dprint':'model3d.html'}.get(app['id'],'index.html')
    require(prefix+'www/'+start in regular or (app['id']=='scratch' and prefixes[-1]+start in regular),'app_archive_start_missing')
    return {'regular_files':len(regular),'unpacked_bytes':sum(item['bytes'] for item in allocation.values()),'allocation':allocation}
