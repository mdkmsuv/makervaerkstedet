"""Integration check: all three quality choices on all three real slicer profiles.

Creates G-code locally. Never connects to or starts any physical printer.
"""
import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.profiles import MODELS, QUALITIES
from app.slicer import Slicer
from scripts.smoke_slice import cube

root=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser()
parser.add_argument('--studio',type=Path,default=Path(r'C:\Program Files\Bambu Studio\bambu-studio.exe'))
parser.add_argument('--work',type=Path,default=root/'.runtime'/'slice-tests')
parser.add_argument('--all-pla',action='store_true')
parser.add_argument('--model',choices=tuple(MODELS),help='Limit verification to one printer model.')
args=parser.parse_args()
work=args.work
work.mkdir(parents=True,exist_ok=True)
source=work/'validation-cube.stl';source.write_bytes(cube())
studio=args.studio.resolve()
slicer=Slicer(studio,studio.parent/'resources'/'profiles'/'BBL',work)
results=[]
cases=[(model, side, material) for model in ([args.model] if args.model else MODELS) for side in ((0, 1) if model == 'H2D' else (0,)) for material in (('GFA00','GFA01') if args.all_pla else ('GFA00',))]
for model, extruder, material in cases:
    for quality,preset in QUALITIES.items():
        job={'id':model.replace(' ','-').lower()+'-'+quality+'-'+str(extruder)+'-'+material,'quality':quality,'profile':slicer.profiles.find_filament(model,material),'actual_colour':'#3678DC','remote_name':'test.gcode.3mf'}
        path=slicer.slice(job,{'model':model, 'extruder':extruder},source)
        with zipfile.ZipFile(path) as archive:
            config=json.loads(archive.read('Metadata/project_settings.config'))
            info=ET.fromstring(archive.read('Metadata/slice_info.config'))
            assert float(config['layer_height'])==float(preset['layer'])
            assert config['sparse_infill_density']==preset['infill']
            assert config['filament_type']==['PLA']
            assert config['enable_support']=='1'
            model_id=info.find(".//plate/metadata[@key='printer_model_id']").get('value')
            assert model_id==slicer.profiles.items[f'Bambu Lab {model}']['model_id']
            if model=='H2D':
                assert info.find(".//plate/metadata[@key='filament_maps']").get('value')==str(extruder + 1)
            estimate=info.find(".//plate/metadata[@key='prediction']").get('value')
            results.append({'model':model,'extruder':extruder,'material':material,'quality':quality,'layer_height':config['layer_height'],'infill':config['sparse_infill_density'],'model_id':model_id,'estimated_seconds':estimate,'gcode_3mf':str(path)})
        print(f'OK: {model} / {quality} / nozzle {extruder} / {material}',flush=True)
(work/'verification.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print(f'All {len(results)} slicing combinations passed. No printer was contacted.')
