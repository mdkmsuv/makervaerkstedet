"""APT config for empty-status, hook-free /tmp verification only."""
import json,re
from pathlib import Path

def isolated_config(root,source,keyring):
    for name in ("state/lists/partial","cache/archives/partial","log","etc/apt.conf.d","etc/sources.list.d","etc/trusted.gpg.d","etc/auth.conf.d","etc/preferences.d"):
        (root/name).mkdir(parents=True,exist_ok=True)
    (root/"state/status").write_bytes(b"")
    values={"Dir":str(root),"Dir::State":str(root/"state"),"Dir::State::lists":str(root/"state/lists"),
        "Dir::State::status":str(root/"state/status"),"Dir::State::extended_states":str(root/"state/extended_states"),
        "Dir::Cache":str(root/"cache"),"Dir::Cache::archives":str(root/"cache/archives"),
        "Dir::Cache::pkgcache":"","Dir::Cache::srcpkgcache":"", "Dir::Log":str(root/"log"),
        "Dir::Log::Terminal":str(root/"log/term.log"),"Dir::Log::History":str(root/"log/history.log"),
        "Dir::Etc":str(root/"etc"),"Dir::Etc::sourcelist":str(source),"Dir::Etc::sourceparts":str(root/"etc/sources.list.d"),
        "Dir::Etc::main":str(root/"etc/unused-main.conf"),"Dir::Etc::parts":str(root/"etc/apt.conf.d"),
        "Dir::Etc::trusted":str(root/"etc/unused-trusted.gpg"),"Dir::Etc::trustedparts":str(root/"etc/trusted.gpg.d"),
        "Dir::Etc::netrc":str(root/"etc/auth.conf"),"Dir::Etc::netrcparts":str(root/"etc/auth.conf.d"),
        "Dir::Bin::methods":"/usr/lib/apt/methods","Dir::Bin::dpkg":"/usr/bin/dpkg"}
    path=root/"isolated.conf"
    text="\n".join(k+" "+json.dumps(v)+";" for k,v in values.items())+"\n"
    text+="#clear APT::Update::Post-Invoke;\n#clear APT::Update::Post-Invoke-Success;\n#clear DPkg::Pre-Invoke;\n#clear DPkg::Post-Invoke;\n#clear DPkg::Pre-Install-Pkgs;\n"
    path.write_text(text)
    return path

def prove_isolation(dump,root):
    values=dict(re.findall(r'^(Dir(?:::[^ ]+)?) "([^"\n]*)";',dump,re.MULTILINE))
    required=("Dir","Dir::State","Dir::State::lists","Dir::State::status","Dir::State::extended_states","Dir::Cache","Dir::Cache::archives","Dir::Log","Dir::Etc","Dir::Etc::sourcelist","Dir::Etc::trusted","Dir::Etc::netrc")
    for key in required:
        value=values.get(key)
        if not value or not Path(value).is_relative_to(root):raise RuntimeError("unisolated_apt_path:"+key)
    for key,value in values.items():
        if key.startswith(("Dir::State", "Dir::Cache", "Dir::Log", "Dir::Etc")) and value:
            if value=="-":continue
            if Path(value).is_absolute() and not Path(value).is_relative_to(root):raise RuntimeError("host_apt_write_path:"+key)
            if ".." in Path(value).parts:raise RuntimeError("unsafe_relative_apt_path")
    if re.search(r'(?:Pre-Invoke|Post-Invoke|Pre-Install-Pkgs).*"[^"\n]+"',dump):raise RuntimeError("host_hooks_present")
    if "AllowUnauthenticated \"true\"" in dump or "AllowInsecureRepositories \"true\"" in dump:raise RuntimeError("insecure_apt_option")
    return values

