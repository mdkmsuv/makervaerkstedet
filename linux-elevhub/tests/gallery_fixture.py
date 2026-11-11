"""Isolated gallery server used only by test_project_gallery.cjs."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from lokal import make_server
from projektarkiv import Projektarkiv


def main():
    root = Path(sys.argv[1]).resolve()
    if not root.name.startswith('elevhub-gallery-'):
        raise ValueError('Only a dedicated temporary gallery directory is allowed.')
    store = Projektarkiv(root / '.elevhub')
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80"><rect x="10" y="10" width="80" height="60" rx="6" fill="#b4121a"/><circle cx="50" cy="40" r="18" fill="white"/></svg>'
    for i in range(7):
        store.save(svg.replace('#b4121a', ['#b4121a', '#387149', '#27638e'][i % 3]).encode(),
                   project=f'{i+1:032x}', name=f'Laserskilt {i+1}', folder='5A · Gruppe 3',
                   kind='laser', filename=f'skilt-{i}.svg', expected=0)
    store.save(svg.replace('<svg ', '<svg data-workshop="vinyl" ').encode(),
               project='a'*32, name='Vores mærke', folder='6B · Gruppe 1',
               kind='folie', filename='maerke.svg', expected=0)
    store.save(b'$ 1 0.000005 10.2 50 5 50\nr 80 80 200 80 0 100\nw 200 80 200 160 0\n',
               project='b'*32, name='Min første strømkreds', folder='Kredsløb · Gruppe 4',
               kind='kredsloeb', filename='kreds.txt', expected=0)
    store.save('Mine noter'.encode(), project='c'*32, name='<script>Langt navn & æøå</script>',
               folder='Vores egne noter', kind='andet', filename='noter.txt', expected=0)
    for project, kind, filename, data in [
        ('d'*32, 'scratch', 'spil.sb3', b'Scratch fixture'),
        ('e'*32, 'cadoodle', 'figur.klosterskitse', b'{"version":1,"height":3,"marks":[]}'),
        ('f'*32, 'microbit', 'robot.hex', b'MakeCode fixture'),
        ('9'*32, 'datatraener', 'model.json', b'{"format":"elevhub-datatraener-project","version":1,"gestureData":[]}'),
    ]:
        store.save(data, project=project, name='Projekt i '+kind, folder='7A · Gruppe 2',
                   kind=kind, filename=filename, expected=0)
    with store.connect() as db:
        db.execute('UPDATE projects SET modified=? WHERE id=?', (time.time()-29*86400, 'a'*32))
    server = make_server(0, store)
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}'}), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
