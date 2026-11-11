#!/usr/bin/env python3
"""Render installed program templates from local UI configuration, without network.

The renderer never reads a root token. All targets are fixed, program/config paths. A
firstboot without pairing gets local workshop defaults and keeps the updater off.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import tempfile
from urllib.parse import urlsplit
from jinja2 import Environment, StrictUndefined

TEMPLATES = {"start.html.j2": ("usr/local/share/elevhub/start.html", 0o644),
    "kiosk-session.j2": ("usr/local/lib/elevhub/kiosk-session", 0o755),
    "elevhub-lokal.service.j2": ("etc/systemd/system/elevhub-lokal.service", 0o644),
    "elevhub-design.service.j2": ("etc/systemd/system/elevhub-design.service", 0o644)}
APP_URLS = {"projekter":"http://127.0.0.1:8791/", "scratch":"http://127.0.0.1:8792/",
    "microbit":"http://127.0.0.1:8797/"}

class ActivationError(ValueError):
    pass

def plain(path):
    path = Path(path)
    for p in (path, *path.parents):
        if p.is_symlink():
            raise ActivationError("linked_path")
    return path

def protected(path, *, private=False, directory=False):
    path = plain(path)
    for ancestor in path.parents:
        info=ancestor.stat()
        # Root-owned sticky /tmp is safe for an exclusively created private test
        # root; ordinary writable ancestors can substitute protected files.
        if info.st_uid!=0 or (info.st_mode & 0o022 and not info.st_mode & 0o1000):
            raise ActivationError("unprotected_parent")
    stat = path.stat()
    if stat.st_uid != 0 or stat.st_mode & (0o077 if private else 0o022):
        raise ActivationError("unprotected_configuration")
    if directory != path.is_dir():
        raise ActivationError("invalid_path_type")
    return path

def atomic(path, data, mode):
    path = plain(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    protected(path.parent, directory=True)
    fd, name = tempfile.mkstemp(prefix=".mkv-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(name): os.unlink(name)

def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()

def default_config():
    return {"computer":socket.gethostname(), "computer_visning":socket.gethostname(),
        "server":"127.0.0.1", "hub":"https://127.0.0.1:8773", "protokol":"https",
        "ca_fil":"/etc/elevhub/ca.pem", "ui_token":secrets.token_urlsafe(32),
        "elev":"elev", "elev_porte":[8770,8771,8772,8773,8791,8792,8795,8796,8797],
        "lokale_app_urler":APP_URLS, "internet_app":"internet", "theme":"red",
        "school_name":"", "webproxy_required":False, "usikker_test":False}

def existing_project_paths(root,user):
    result={"projects":"/home/"+user+"/Projekter","state":"/home/"+user+"/Projekter/.elevhub"}
    unit=root/"etc/systemd/system/elevhub-lokal.service"
    if unit.exists():
        lines=protected(unit).read_text(encoding="utf-8").splitlines()
        commands=[line[len("ExecStart="):] for line in lines if line.startswith("ExecStart=")]
        if len(commands)!=1:raise ActivationError("ambiguous_legacy_project_service")
        argv=shlex.split(commands[0])
        for flag,key in (("--projects","projects"),("--state","state")):
            if argv.count(flag)>1:raise ActivationError("ambiguous_legacy_project_paths")
            if flag in argv:
                index=argv.index(flag)
                if index+1>=len(argv):raise ActivationError("invalid_legacy_project_paths")
                value=argv[index+1]
                if not re.fullmatch(r"/[A-Za-z0-9_./-]+",value) or ".." in Path(value).parts:
                    raise ActivationError("invalid_legacy_project_paths")
                result[key]=value
    return result

def context(config, root):
    # Explicit projection: even a legacy file with extra credentials cannot pass
    # a root token to the template engine or to the pupil-readable start page.
    server = config.get("server", "127.0.0.1")
    if not isinstance(server,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}",server) or ":" in server:
        raise ActivationError("invalid_server_host")
    computer=config.get("computer")
    if not isinstance(computer,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}",computer):
        raise ActivationError("invalid_computer")
    ui=config.get("ui_token")
    if not isinstance(ui,str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}",ui):
        raise ActivationError("invalid_ui_token")
    if config.get("protokol","https") != "https":
        raise ActivationError("https_required")
    user=config.get("elev","elev")
    if not isinstance(user,str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}",user):
        raise ActivationError("invalid_student_user")
    urls=config.get("lokale_app_urler",APP_URLS)
    if isinstance(urls,dict):
        urls={k:v for k,v in urls.items() if k not in {"datatraener","kredsloeb","skolegpt"}}
    allowed={**APP_URLS,"laser":"http://127.0.0.1:8795/","folie":"http://127.0.0.1:8796/",
             "3dprint":"https://"+server+":8770/"}
    if not isinstance(urls,dict) or any(k not in allowed or v.rstrip("/")!=allowed[k].rstrip("/") for k,v in urls.items()):
        raise ActivationError("unexpected_local_workshop_urls")
    urls={**allowed,**urls}
    theme=config.get("theme","red")
    school=config.get("school_name","")
    if theme not in {"red","green","yellow"} or not isinstance(school,str) or len(school)>120 or any(ord(c)<32 for c in school):
        raise ActivationError("invalid_appearance")
    # This variable only controls the optional --ca-file argument in the local
    # design unit. Omitting a missing CA retains Python's normal HTTPS validation.
    ca_available=(root/"etc/elevhub/ca.pem").is_file()
    data=existing_project_paths(root,user)
    keyboard=config.get("tastatur","dk")
    if not isinstance(keyboard,str) or not re.fullmatch(r"[a-z]{2}(?:,[a-z]{2})?",keyboard):raise ActivationError("invalid_keyboard")
    for key in ("mesa_gl","web_bluetooth"):
        if key in config and type(config[key]) is not bool:raise ActivationError("invalid_session_option")
    for key in ("screen_output","screen_mode"):
        if not isinstance(config.get(key,""),str) or not re.fullmatch(r"[A-Za-z0-9_.@+-]{0,80}",config.get(key,"")):raise ActivationError("invalid_screen_option")
    return {"inventory_hostname":computer,"kiosk_enhed":{"ui_token":ui}, "kiosk_server":server,
        "kiosk_protokol":"https", "kiosk_hub_port":8773, "kiosk_printvaerksted_port":8770, "kiosk_theme":theme,
        "kiosk_school_name":school,"kiosk_bruger":user,"kiosk_lokale_app_urler":urls,
        "kiosk_internet_start":"https://www.google.com/imghp?hl=da&safe=active",
        "kiosk_startside_mappe":"/usr/local/share/elevhub", "kiosk_tastatur":keyboard,
        "kiosk_mesa_gl":config.get("mesa_gl",False),"kiosk_web_bluetooth":config.get("web_bluetooth",False),"kiosk_skaerm_output":config.get("screen_output",""),"kiosk_skaerm_mode":config.get("screen_mode",""),
        "kiosk_usikker_test":not ca_available,"kiosk_lokal_design_dest":"/usr/local/lib/elevhub/designvaerksted",
        "kiosk_laservaerksted_port":8771,"kiosk_lokal_design_laser_port":8795,"kiosk_lokal_design_folie_port":8796,
        "kiosk_lokal_service_dest":"/usr/local/lib/elevhub/projekter/lokal.py",
        "kiosk_lokale_apps_root":"/usr/local/share/elevhub/apps", "kiosk_lokale_projects":data["projects"],
        "kiosk_lokale_state":data["state"], "kiosk_lokal_package_dest":"/usr/local/lib/elevhub/projekter"}

TRUST={"minisign.pub":("etc/makervaerkstedet/release.pub","44de94f24b5a84e5dd35d81d7a90052e45a9950c51f93584ad79c4e4019f4aa5"),
       "makervaerkstedet.gpg":("etc/apt/keyrings/makervaerkstedet.gpg","5b9ee5f0ca01ff2d52c311e99f71b6eefd69ed8d9149ef7cef5d80cf89a27f1f")}

def trust_inputs(root):
    values=[]
    for name,(target,pin) in TRUST.items():
        source=protected(root/"usr/share/makervaerkstedet/trust"/name)
        data=source.read_bytes()
        if hashlib.sha256(data).hexdigest()!=pin:raise ActivationError("public_bootstrap_key_hash_mismatch")
        values.append((root/target,data))
    return values

def bootstrap(root,config,trust):
    # Only approved PUBLIC bootstrap keys are packaged. The project apt keyring
    # follows its validated updater payload so future public-key renewal works.
    # Minisign trust and school CA/identity remain locally managed.
    for target,data in trust:
        if target.exists():
            protected(target)
            if target==root/"etc/apt/keyrings/makervaerkstedet.gpg" and target.read_bytes()!=data:
                atomic(target,data,0o644)
        else:atomic(target,data,0o644)
    target=root/"etc/makervaerkstedet/apt-client.json"
    if target.exists():protected(target,private=True);return True
    device=root/"etc/elevhub/enhed.json";ca=root/"etc/elevhub/ca.pem"
    if not device.exists() or not ca.exists():return False
    protected(device,private=True);protected(ca)
    parsed=urlsplit(config.get("hub",""))
    if parsed.scheme!="https" or parsed.hostname!=config["server"].lower() or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ActivationError("invalid_local_https_origin")
    origin=config["hub"].rstrip("/")
    source=root/"etc/apt/sources.list.d/makervaerkstedet.sources"
    if source.exists():protected(source)
    else:atomic(source,("Types: deb\nURIs: "+origin+"/apt/\nSuites: ./\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/makervaerkstedet.gpg\n").encode(),0o644)
    atomic(target,json_bytes({"schema":1,"server_origin":origin,"ca_file":"/etc/elevhub/ca.pem",
        "device_file":"/etc/elevhub/enhed.json","public_key_file":"/etc/makervaerkstedet/release.pub",
        "state_root":"/var/lib/makervaerkstedet-updater"}),0o600)
    return True

def activate(root=Path("/")):
    root=protected(root,directory=True)
    templates=protected(root/"usr/share/makervaerkstedet/templates",directory=True)
    config_path=root/"etc/elevhub/kiosk.json"
    if config_path.exists():
        config=json.loads(protected(config_path).read_text(encoding="utf-8"))
    else:
        config=default_config()
    values=context(config,root)
    trust=trust_inputs(root)
    environment=Environment(undefined=StrictUndefined,autoescape=False)
    environment.filters["bool"]=bool
    environment.filters["quote"]=shlex.quote
    environment.filters["to_json"]=lambda v: json.dumps(v,ensure_ascii=True).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    prepared=[]
    for name,(target,mode) in TEMPLATES.items():
        source=protected(templates/name).read_text(encoding="utf-8")
        data=environment.from_string(source).render(**values).encode()
        if b"fresh_session.py" in data or b"start_cad" in data:
            raise ActivationError("retired_template_binding")
        prepared.append((plain(root/target),data,mode))
    # Render all outputs before the first write. A crash mid-commit is repaired
    # by the same offline firstboot pass before local services/login next boot.
    state=root/"var/lib/makervaerkstedet-updater"
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    protected(state,private=True,directory=True)
    atomic(state/"activation.json",json_bytes({"phase":"committing"}),0o600)
    if not config_path.exists():atomic(config_path,json_bytes(config),0o644)
    hashes={}
    for target,data,mode in prepared:
        atomic(target,data,mode)
        hashes[target.relative_to(root).as_posix()]=hashlib.sha256(data).hexdigest()
    paired=bootstrap(root,config,trust)
    atomic(state/"activation.json",json_bytes({"phase":"complete","files":hashes}),0o600)
    return {"phase":"complete","files":hashes,"paired":paired}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("/"))
    args=parser.parse_args()
    if os.geteuid()!=0:raise ActivationError("root_required")
    print(json.dumps(activate(args.root)))

if __name__=="__main__":main()
