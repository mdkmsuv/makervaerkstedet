"""Recover MakeCode source locally and use its supported #project import.

Format: https://makecode.com/source-embedding
Import: microsoft/pxt webapp/src/app.tsx, importProjectFromFileAsync.
No project upload, URL fetching or execution is performed by this module.
"""
import base64
import json
import lzma
import struct

MAGIC = bytes.fromhex('41140E2FB82FA2BB')
MAX_SOURCE = 4 * 1024 * 1024


def decompress(data):
    if len(data) < 13:
        raise ValueError('HEX-filen mangler en del af projektet.')
    size = int.from_bytes(data[5:13], 'little')
    if size != (1 << 64) - 1 and size > MAX_SOURCE:
        raise ValueError('Projektet er for stort.')
    # LZMA-JS writes both a known size and an end marker. liblzma rejects that
    # combination in some MakeCode exports. Decode to the marker, then check size.
    stream = data[:5] + b'\xff' * 8 + data[13:]
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=32 * 1024 * 1024)
    result = decoder.decompress(stream, max_length=MAX_SOURCE + 1)
    if len(result) > MAX_SOURCE or not decoder.eof:
        raise ValueError('Projektet er for stort eller HEX-filen er beskadiget.')
    if size != (1 << 64) - 1 and len(result) != size:
        raise ValueError('HEX-filen mangler en del af projektet.')
    return result.decode('utf-8')


def source_project(data):
    try:
        header_size = text_size = 0
        payload = bytearray()
        found = False
        for line in data.decode('ascii').splitlines():
            if not line.strip():
                continue
            if not line.startswith(':'):
                raise ValueError('HEX-filen kunne ikke læses.')
            record = bytes.fromhex(line[1:])
            if len(record) < 5 or len(record) != record[0] + 5 or sum(record) & 255:
                raise ValueError('HEX-filen er beskadiget. Prøv at gemme den igen i MakeCode.')
            if record[3] not in (0, 14):  # regular / universal HEX data
                continue
            block = record[4:-1]
            if not found and len(block) >= 16 and block.startswith(MAGIC):
                header_size, text_size = struct.unpack_from('<HI', block, 8)
                if not header_size or not text_size or header_size + text_size > MAX_SOURCE:
                    raise ValueError('HEX-filen indeholder et ugyldigt projekt.')
                found = True
                payload.extend(block[16:])
            elif found:
                payload.extend(block)
            if found and len(payload) >= header_size + text_size:
                break
        if not found:
            raise ValueError('Denne HEX-fil indeholder ikke et MakeCode-projekt. Brug HEX-filen, du gemte fra MakeCode.')
        if len(payload) < header_size + text_size:
            raise ValueError('HEX-filen mangler en del af projektet.')
        header = json.loads(payload[:header_size])
        if not isinstance(header, dict):
            raise ValueError('Ugyldigt MakeCode-projekt.')
        body = bytes(payload[header_size:header_size + text_size])
        if header.get('compression') == 'LZMA':
            unpacked = decompress(body)
            # MakeCode counts JavaScript UTF-16 code units, including emoji.
            units = header.get('headerSize') or header.get('metaSize') or 0
            if type(units) is not int or units < 0:
                raise ValueError('Ugyldigt MakeCode-projekt.')
            utf16 = unpacked.encode('utf-16-le')
            meta = utf16[:units * 2].decode('utf-16-le')
            source = utf16[units * 2:].decode('utf-16-le')
            if meta:
                header.update(json.loads(meta))
        elif not header.get('compression'):
            source = body.decode('utf-8')
        else:
            raise ValueError('Denne HEX-fil bruger et ukendt projektformat.')
        files = json.loads(source)
        if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            raise ValueError('Ugyldige projektfiler.')
        config = json.loads(files['pxt.json'])
        if not isinstance(config, dict):
            raise ValueError('Ugyldig projektbeskrivelse.')
        cloud = header.get('cloudId', '')
        if cloud not in ('', 'pxt/microbit') or not any(k in files for k in ('main.ts', 'main.blocks', 'main.py')):
            raise ValueError('HEX-filen hører ikke til micro:bit i MakeCode.')
        header.update(cloudId='pxt/microbit', name=header.get('name') or config.get('name') or 'Mit micro:bit-projekt',
                      editor=header.get('editor') or config.get('preferredEditor') or 'blocksprj')
        return header, files
    except (lzma.LZMAError, UnicodeError, KeyError, TypeError, struct.error, json.JSONDecodeError) as error:
        raise ValueError('HEX-filen kunne ikke åbnes som et MakeCode-projekt.') from error


def editable_project(data):
    """Decode existing HEX files for the same guarded editor as new projects."""
    header, files = source_project(data)
    versions = header.get('targetVersions')
    return {'header': {'name': header['name'], 'editor': header['editor'],
                       'target': 'microbit', 'targetVersion': versions.get('target', '0.0.0')
                       if isinstance(versions, dict) else '0.0.0'}, 'text': files}


def project_url(data):
    """Compatibility for callers outside the local, guarded workshop."""
    header, files = source_project(data)
    project = json.dumps({'meta': header, 'source': json.dumps(files, ensure_ascii=False)},
                         ensure_ascii=False, separators=(',', ':')).encode()
    packed = lzma.compress(project, format=lzma.FORMAT_ALONE, preset=6)
    return 'https://makecode.microbit.org/?lang=da#project:' + base64.b64encode(packed).decode('ascii')
