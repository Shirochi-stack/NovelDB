"""Build font-specific qdn mappings from aligned, verified text; no matching.

The fixture records painted text/pseudos separately from unpainted attributes.
This is a chapter-backed partial map, not the provider's complete source map.
"""
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qdn_font_decoder as qdn
from scripts.source_names import dump


def build(sample, expected):
    payload = {key: sample[key] for key in ('faces', 'fontData')}
    decoder = qdn.QdnFontDecoder(payload)
    mappings = {}
    if len(sample['atoms']) != len(expected):
        raise ValueError('Paragraph counts differ')
    literals = 0
    for index, (atoms, truth) in enumerate(zip(sample['atoms'], expected), 1):
        units = [(ch, atom) for atom in atoms if atom['src'] != 'attr' for ch in atom['text']]
        while units and units[0][0].isspace():
            units.pop(0)
        while units and units[-1][0].isspace():
            units.pop()
        if len(units) != len(truth):
            raise ValueError(f'Unaligned paragraph {index}')
        for (source, atom), character in zip(units, truth):
            families = [part.strip().strip('\"\'') for part in atom['fonts'].split(',')]
            cipher = decoder._cipher_for(source, families)
            if cipher is None:
                if source != character:
                    raise ValueError(f'Literal mismatch in paragraph {index}')
                literals += 1
                continue
            face = next(face for face in sample['faces'] if face['family'] == cipher.family)
            digest = hashlib.sha256(base64.b64decode(sample['fontData'][face['url']])).hexdigest()
            key = f'{ord(source):X}:{int(atom["mirrored"])}'
            table = mappings.setdefault(digest, {})
            if key in table and table[key] != character:
                raise ValueError(f'Conflicting font mapping in paragraph {index}')
            table[key] = character
    return {'schema': 1, 'provenance': {
        'method': 'Aligned captured glyph codes with manually verified full-page screenshot text',
        'chapter': '1048859382-931898911', 'paragraphs': len(expected),
        'characters': sum(map(len, expected)), 'literal_characters': literals,
        'coverage': 'Only observed font/code/mirror combinations; not a complete source mapping',
    }, 'fonts': {digest: {'entries': table} for digest, table in mappings.items()}}


if __name__ == '__main__':
    sample = json.loads((ROOT / 'tests/fixtures/qdn_chapter76_capture.json').read_text(encoding='utf-8'))
    expected = json.loads((ROOT / 'tests/fixtures/qdn_chapter76_expected.json').read_text(encoding='utf-8'))['paragraphs']
    result = build(sample, expected)
    destination = ROOT / 'data/qdn_verified_maps.json'
    with destination.open('w', encoding='utf-8') as stream:
        dump(result, stream, ensure_ascii=False, indent=2)
    print('Verified fonts:', len(result['fonts']))
    print('Verified entries:', sum(len(font['entries']) for font in result['fonts'].values()))
