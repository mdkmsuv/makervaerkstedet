"""Apply the versioned school shell to an existing local Scratch or Datatræner build.

Adds only school-owned CSS/JS/assets and keeps upstream bundles and credit intact.
The original entrypoint is retained as index.before-creative.html.
"""
from pathlib import Path
import argparse, shutil, zipfile, json, hashlib, re
HERE=Path(__file__).resolve().parent
def apply(build: Path, workshop: str):
    if workshop not in ('scratch','datatraener'):raise ValueError('Unknown workshop')
    index=build/'index.html'
    if not index.is_file():raise ValueError('Build has no index.html')
    backup=build/'index.before-creative.html'
    if not backup.exists():shutil.copy2(index,backup)
    assets=HERE/'elevhub/web/assets';target=build/'creative';target.mkdir(exist_ok=True)
    # workshops.js supplies these filenames to the help dialog and data mission.
    image='workshop-code-bauhaus-v1.png' if workshop=='scratch' else 'workshop-data-bauhaus-v1.png'
    for name in ('creative-app.css','creative-app.js','workshops.js','metrophobic-latin.ttf','klostermarksskolen-logo.png',image):shutil.copy2(assets/name,target/name)
    html=backup.read_text(encoding='utf-8-sig').replace('lang="en"','lang="da"')
    html=re.sub(r'<title>.*?</title>',f'<title>{"Scratch" if workshop=="scratch" else "Datatræneren"} · Klostermarksskolen</title>',html)
    html=html.replace('</head>','<link rel="stylesheet" href="creative/creative-app.css"></head>')
    html=html.replace('</body>',f'<script src="creative/workshops.js" defer></script><script src="creative/creative-app.js" data-workshop="{workshop}" defer></script></body>')
    index.write_text(html,encoding='utf-8')
    # Distribute the integration source alongside the existing corresponding source.
    if workshop=='scratch' and (build/'source.zip').is_file():
        archive=build/'source.zip'
        additions=[(Path(__file__),'integration/creative/style-workshop.py')]+[(target/n,'integration/creative/'+n) for n in ('creative-app.css','creative-app.js','workshops.js')]
        with zipfile.ZipFile(archive) as z:
            changed=any(name not in z.namelist() or z.read(name)!=file.read_bytes() for file,name in additions)
        if changed:
            temp=build/'source.creative-staged.zip'
            names={name for _,name in additions}
            with zipfile.ZipFile(archive) as old,zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as out:
                for entry in old.infolist():
                    if entry.filename not in names:out.writestr(entry,old.read(entry.filename))
                for file,name in additions:out.write(file,name)
            temp.replace(archive)
        manifest=HERE/'scratch/installation.json'
        if manifest.is_file():
            data=json.loads(manifest.read_text(encoding='utf-8-sig'));data['sourceArchiveSha256']=hashlib.sha256(archive.read_bytes()).hexdigest();data['sourceArchiveBytes']=archive.stat().st_size;data['creativeShell']='creative/creative-app.css and creative-app.js';manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{workshop}: school shell installed at {build}')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('workshop',choices=['scratch','datatraener']);p.add_argument('build',type=Path);a=p.parse_args();apply(a.build,a.workshop)
