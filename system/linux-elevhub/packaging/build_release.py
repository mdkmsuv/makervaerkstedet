#!/usr/bin/env python3
"""Build/reuse all four pinned inputs, then require the clean Debian13 gate.

Individual build_debian.py archives are intermediate artifacts. A releasable
set requires this command to finish and produce release-build.json. No signing
or repository publishing occurs here.
"""
import argparse,json,subprocess
from pathlib import Path
from build_debian import build,sha
from check_install import check,PACKAGES
from app.app_packages import release_packages

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--input-locks",nargs='+',type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--debian-keyring",type=Path,required=True);p.add_argument("--report-dir",type=Path,required=True);p.add_argument('--release-set',choices=('legacy','base','app','migration'),default='legacy')
    a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True)
    marker=a.output_dir/"release-build.json";marker.unlink(missing_ok=True)
    specs=[json.loads(f.read_text(encoding="utf-8-sig")) for f in a.input_locks]
    release_packages(specs,a.release_set)
    outputs=[]
    for s in specs:
        output=a.output_dir/(s["package"]+"_"+s["version"]+"_"+s["architecture"]+".deb")
        if output.exists():
            receipt=json.loads(output.with_suffix(".inputs.json").read_text())
            if receipt["sha256"]!=sha(output) or receipt["files"]!=[{k:v for k,v in e.items() if k!="source"} for e in s["files"]] or receipt["version"]!=s["version"]:raise ValueError("existing_output_receipt_mismatch")
            dependencies=subprocess.check_output(["dpkg-deb","--field",str(output),"Depends"],text=True).strip()
            if dependencies!=", ".join(s["depends"]):raise ValueError("existing_output_dependencies_mismatch")
        else:build(s,output)
        outputs.append(output)
    result=check(outputs,a.debian_keyring,a.report_dir) if a.release_set == 'legacy' else check(outputs,a.debian_keyring,a.report_dir,a.release_set,specs)
    marker.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"gate":"passed","packages":result["packages"],"actual_install":False}))
if __name__=="__main__":main()
