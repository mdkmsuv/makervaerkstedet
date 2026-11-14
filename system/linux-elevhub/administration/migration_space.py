"""Bounded inert allocation guard for already authenticated school .deb files.

No extraction, maintainer scripts, apt calls or filesystem writes. Debian OS
dependencies selected by apt still require apt's own download/install checks.
"""
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import threading
import time

MAX_EXPANDED=2*1024**3
MAX_FILES=100000
MAX_ARCHIVES=32
MAX_SECONDS=60
MAX_METADATA=64*1024
MAX_PATH_BYTES=32*1024**2


def require(condition,code):
    if not condition:raise ValueError(code)


def _plain(path,*,directory=False):
    path=Path(path).absolute()
    require('..' not in path.parts,'unsafe_migration_input')
    for current in (path,*path.parents):
        info=os.lstat(current)
        require(not stat.S_ISLNK(info.st_mode),'migration_input_symlink')
        require(stat.S_ISDIR(info.st_mode) if current!=path or directory else stat.S_ISREG(info.st_mode),
                'migration_regular_input_required')
    return path


def _destination(root,name,*,directory=False):
    """Nearest existing directory, checking every existing target component."""
    current=root;nearest=root;device=os.lstat(root).st_dev
    parts=PurePosixPath(name).parts
    for index,part in enumerate(parts):
        current=current/part
        try:info=os.lstat(current)
        except FileNotFoundError:break
        require(not stat.S_ISLNK(info.st_mode),'migration_target_symlink')
        last=index==len(parts)-1
        if not last or directory:
            require(stat.S_ISDIR(info.st_mode),'migration_target_not_directory')
            nearest=current;device=info.st_dev
        else:
            require(stat.S_ISREG(info.st_mode),'migration_target_not_regular')
    return device,nearest


def _scan(path,state,visit):
    """Read tar headers/data through dpkg-deb; cap metadata before tar parses it."""
    class BoundedInfo(tarfile.TarInfo):
        def _proc_member(item,archive):
            state['headers']+=1
            require(state['headers']<=MAX_FILES and 0<=item.size<=MAX_EXPANDED,'migration_archive_limit')
            require(item.type!=tarfile.GNUTYPE_SPARSE,'migration_archive_entry_forbidden')
            if item.type in (tarfile.XHDTYPE,tarfile.XGLTYPE,tarfile.GNUTYPE_LONGNAME,tarfile.GNUTYPE_LONGLINK):
                require(item.size<=MAX_METADATA,'migration_archive_limit')
            return super()._proc_member(archive)
        def _proc_gnusparse_00(self,*args):raise ValueError('migration_archive_entry_forbidden')
        def _proc_gnusparse_01(self,*args):raise ValueError('migration_archive_entry_forbidden')
        def _proc_gnusparse_10(self,*args):raise ValueError('migration_archive_entry_forbidden')

    remaining=state['deadline']-time.monotonic()
    require(remaining>0,'migration_archive_timeout')
    process=subprocess.Popen(['/usr/bin/dpkg-deb','--fsys-tarfile',str(path)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    timer=threading.Timer(remaining,process.kill);timer.daemon=True;timer.start()
    class BoundedStream:
        def read(self,size=-1):
            require(time.monotonic()<state['deadline'],'migration_archive_timeout')
            require(0<=size<=1024**2,'migration_archive_limit')
            data=process.stdout.read(size);state['stream_bytes']+=len(data)
            require(state['stream_bytes']<=MAX_EXPANDED+(MAX_FILES+1)*4096,'migration_archive_limit')
            return data
    seen=set();regular=0
    try:
        with tarfile.open(fileobj=BoundedStream(),mode='r|',tarinfo=BoundedInfo) as stream:
            for item in stream:
                require(time.monotonic()<state['deadline'],'migration_archive_timeout')
                require(0<=item.size<=MAX_EXPANDED and item.uid==0 and item.gid==0 and not item.sparse and
                        not any(key.startswith('GNU.sparse') for key in item.pax_headers),'migration_archive_entry_forbidden')
                name=item.name.removeprefix('./').rstrip('/')
                if name in ('','.'):require(item.isdir() and item.mode==0o755,'migration_archive_entry_forbidden');continue
                require(name and '\\' not in name and ':' not in name and not any(ord(c)<32 or ord(c)==127 for c in name),
                        'unsafe_migration_path')
                relative=PurePosixPath(name)
                require(not relative.is_absolute() and relative.as_posix()==name and '..' not in relative.parts,
                        'unsafe_migration_path')
                encoded=name.encode('utf-8');state['path_bytes']+=len(encoded)+1
                require(len(encoded)<=4096 and all(len(part.encode('utf-8'))<=255 for part in relative.parts)
                        and state['path_bytes']<=MAX_PATH_BYTES,'migration_archive_limit')
                require(name.casefold() not in seen,'duplicate_migration_path');seen.add(name.casefold())
                require((item.isdir() and item.mode==0o755 and item.size==0) or
                        (item.isfile() and item.mode in (0o644,0o755)),'migration_archive_entry_forbidden')
                state['expanded']+=item.size
                require(state['expanded']<=MAX_EXPANDED,'migration_archive_limit')
                visit(name,item)
                regular+=int(item.isfile())
        require(process.wait(timeout=min(5,max(0.01,state['deadline']-time.monotonic())))==0,'invalid_migration_archive')
        require(regular>0,'empty_migration_archive')
    except (tarfile.TarError,EOFError,UnicodeError,subprocess.TimeoutExpired) as error:
        raise ValueError('invalid_migration_archive') from error
    finally:
        timer.cancel()
        if process.poll() is None:process.kill()
        process.stdout.close();process.wait()


def check_migration_space(files,*,root=Path('/'),reserve=128*1024**2):
    """Budget every school archive together, by the real destination device.

    Two block-rounded copies cover new payload plus dpkg temporary files.
    Shared directories are charged once; dpkg database/file-list and apt cache
    overhead are charged to their own destination volumes. Diagnostics contain
    device IDs and numeric totals only, never private paths.
    """
    require(type(reserve) is int and reserve>=0,'invalid_migration_reserve')
    files=list(files)
    require(0<len(files)<=MAX_ARCHIVES,'migration_archive_limit')
    root=_plain(root,directory=True)
    state=dict(headers=0,expanded=0,path_bytes=0,stream_bytes=0,deadline=time.monotonic()+MAX_SECONDS)
    volumes={};owners={};directories=set();inputs=set()
    def volume(name,directory=False):
        device,parent=_destination(root,name,directory=directory)
        info=os.statvfs(parent);block=info.f_frsize or info.f_bsize
        require(type(block) is int and 0<block<=1024**2,'invalid_migration_block_size')
        free=shutil.disk_usage(parent).free
        require(type(free) is int and free>=0,'invalid_migration_free_space')
        row=volumes.setdefault(device,dict(device=device,required_bytes=reserve,available_bytes=free,
                                          regular_files=0,directories=0,block_size=block))
        row['available_bytes']=min(row['available_bytes'],free)
        return row,block
    def charge(name,size,*,directory=False,copies=2):
        row,block=volume(name,directory)
        row['required_bytes']+=max(1,(size+block-1)//block)*block*copies
        return row
    for value in files:
        path=_plain(value)
        require(path not in inputs,'duplicate_migration_archive');inputs.add(path)
        before=state['path_bytes']
        def visit(name,item):
            parts=PurePosixPath(name).parts
            for count in range(1,len(parts)+int(item.isdir())):
                parent='/'.join(parts[:count]);key=parent.casefold()
                require(owners.get(key)!='file','duplicate_migration_path')
                owners[key]='directory'
                if key not in directories:
                    directories.add(key)
                    row,block=volume(parent,True);row['required_bytes']+=block;row['directories']+=1
            if item.isfile():
                require(name.casefold() not in owners,'duplicate_migration_path')
                owners[name.casefold()]='file'
                row=charge(name,item.size);row['regular_files']+=1
        _scan(path,state,visit)
        charge('var/lib/dpkg',64*1024+state['path_bytes']-before,directory=True)
        charge('var/cache/apt/archives',path.stat().st_size,directory=True,copies=1)
    result=dict(archives=len(files),unpacked_bytes=state['expanded'],volumes=[volumes[key] for key in sorted(volumes)])
    require(all(row['required_bytes']<=row['available_bytes'] for row in result['volumes']),'insufficient_migration_space')
    return result
