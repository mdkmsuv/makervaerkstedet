"""Bounded embedded PNG motifs and teacher-selected engraving exports."""
import base64
from io import BytesIO
import math
import re
import xml.etree.ElementTree as ET
from PIL import Image

NS='http://www.w3.org/2000/svg'
tag=lambda name:f'{{{NS}}}{name}'

def safe_path(d):
    if not isinstance(d,str) or len(d)>150000 or not re.fullmatch(r'[\s\d.,+eEaAcChHlLmMqQsStTvVzZ-]+',d) or not re.match(r'\s*[Mm]',d):
        raise ValueError('Billedets konturer kunne ikke læses.')
    values=re.findall(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?',d)
    if not values or any(not math.isfinite(float(v)) or abs(float(v))>100000 for v in values):
        raise ValueError('Billedets konturer har ugyldige koordinater.')
    return d

def clean_motif(node,op):
    children=list(node)
    images=[p for p in children if p.tag==tag('image')]
    paths=[p for p in children if p.tag==tag('path')]
    if len(images)!=1 or len(paths)>1 or len(children)!=len(images)+len(paths) or any(list(p) for p in children):
        raise ValueError('Billedet skal indeholde én PNG og højst én tracing.')
    image=images[0];src=image.get('href','')
    if len(src)>800000 or not re.fullmatch(r'data:image/png;base64,[A-Za-z0-9+/]+={0,2}',src):
        raise ValueError('Kun indlejrede PNG-billeder kan afleveres.')
    try:
        width,height=int(image.get('width','0')),int(image.get('height','0'))
        if not 1<=width<=1024 or not 1<=height<=1024:raise ValueError()
        raw=base64.b64decode(src.split(',',1)[1],validate=True)
        with Image.open(BytesIO(raw)) as png:
            if png.format!='PNG' or png.size!=(width,height):raise ValueError()
            png.verify()
        with Image.open(BytesIO(raw)) as png:
            png.load()
    except Exception:
        raise ValueError('PNG-billedet har ugyldige data eller mål.') from None
    tr=image.get('transform','');match=re.fullmatch(r'matrix\(([^)]+)\)',tr)
    try:
        matrix=[float(n) for n in re.split(r'[\s,]+',match[1].strip())] if match else []
        if len(matrix)!=6 or any(not math.isfinite(n) or abs(n)>100000 for n in matrix) or abs(matrix[0]*matrix[3]-matrix[1]*matrix[2])<1e-12:raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('Billedets placering er ugyldig.') from None
    motif_id=node.get('data-id','')
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',motif_id):raise ValueError('Billedet mangler et gyldigt nummer.')
    display=node.get('data-display','image')
    try:
        threshold=int(node.get('data-threshold','150'));smoothing=float(node.get('data-smoothing','1'))
        if not 1<=threshold<=254 or smoothing not in (.5,1,2,4) or display not in ('image','trace'):raise ValueError()
    except ValueError:
        raise ValueError('Billedets tracing-indstillinger er ugyldige.') from None
    d=safe_path(paths[0].get('d')) if paths else ''
    if not d and (op!='engrave-fill' or display=='trace'):raise ValueError('Trace billedet, før du bruger konturerne.')
    attrs={'data-image':'1','data-id':motif_id,'data-name':node.get('data-name','Billede')[:60],'data-operation':op,'data-display':display,'data-threshold':str(threshold),'data-smoothing':str(smoothing)}
    if re.fullmatch(r'[a-zA-Z0-9-]{1,80}',node.get('data-group','')):attrs['data-group']=node.get('data-group')
    result=ET.Element(tag('g'),attrs)
    original=display=='image' and op=='engrave-fill'
    ET.SubElement(result,tag('image'),{'href':src,'width':str(width),'height':str(height),'transform':tr,'preserveAspectRatio':'none',**({} if original else {'display':'none'})})
    if d:
        ET.SubElement(result,tag('path'),{'d':d,'transform':tr,'fill-rule':'evenodd','fill':'#256b93' if op=='engrave-fill' else 'none','stroke':'none' if op=='engrave-fill' else '#b4121a' if op=='cut' else '#256b93','stroke-width':'0.3','vector-effect':'non-scaling-stroke',**({'display':'none'} if original else {})})
    return result

def image_details(svg):
    return [{'id':g.get('data-id'),'name':g.get('data-name'),'operation':g.get('data-operation'),'hasTrace':g.find(tag('path')) is not None} for g in ET.fromstring(svg).iter(tag('g')) if g.get('data-image')=='1']

def prepared_svg(source,modes,require_choices=True):
    root=ET.fromstring(source);groups={}
    for op in ('engrave-fill','engrave-line','cut'):
        groups[op]=ET.Element(tag('g'),{'id':op,'data-operation':op,'fill':'#256b93' if op=='engrave-fill' else 'none','stroke':'none' if op=='engrave-fill' else '#b4121a' if op=='cut' else '#256b93','stroke-width':'0.3' if op=='engrave-line' else '0.1'})
    for group in list(root):
        if group.tag!=tag('g'):continue
        for part in group:
            op=part.get('data-operation',group.get('data-operation'))
            if part.get('data-image')!='1':groups[op].append(part);continue
            mode='vector' if op=='cut' else modes.get(part.get('data-id'))
            if not mode:
                if require_choices:raise ValueError('Vælg raster- eller vektorgravering for hvert billede først.')
                mode='raster' if part.get('data-display')=='image' else 'vector'
            if mode=='raster':
                element=part.find(tag('image'));op='engrave-fill'
                raw=base64.b64decode(element.get('href').split(',',1)[1]);output=BytesIO()
                with Image.open(BytesIO(raw)) as source_image:
                    rgba=source_image.convert('RGBA');white=Image.new('RGBA',rgba.size,'white');white.alpha_composite(rgba);white.convert('L').save(output,format='PNG')
                element.set('href','data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii'))
            else:
                element=part.find(tag('path'))
                if element is None:raise ValueError('Billedet har ingen konturer til vektorgravering.')
                op='cut' if op=='cut' else 'engrave-line'
                element.set('fill','none');element.set('stroke','#b4121a' if op=='cut' else '#256b93')
            element.attrib.pop('display',None)
            element.set('data-operation',op);element.set('data-name',part.get('data-name','Billede'))
            groups[op].append(element)
        root.remove(group)
    for group in groups.values():
        if len(group):root.append(group)
    return ET.tostring(root,encoding='unicode')
