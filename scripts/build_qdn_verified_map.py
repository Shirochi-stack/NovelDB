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
from scripts.source_names import dumps

FIXTURES = ROOT / 'tests/fixtures'
DESTINATIONS = (
    ROOT / 'data/qdn_verified_maps.json',
    ROOT / 'android/sources/src/main/resources/com/shirochi/npia/sources/hard/qdn/qdn_verified_maps.json',
    ROOT / 'ios/NpiaKit/Sources/NpiaSources/Hard/HardResources/qdn/qdn_verified_maps.json',
)


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


def build_bundled():
    sample = json.loads((FIXTURES / 'qdn_chapter76_capture.json').read_text(encoding='utf-8'))
    expected = json.loads((FIXTURES / 'qdn_chapter76_expected.json').read_text(encoding='utf-8'))['paragraphs']
    result = build(sample, expected)
    excerpts = json.loads((FIXTURES / 'qdn_verified_glyph_excerpts.json').read_text(encoding='utf-8'))
    for excerpt in excerpts:
        digest = excerpt['font_sha256']
        table = result['fonts'].setdefault(digest, {'entries': {}})['entries']
        for source, mirrored, character in excerpt['glyphs']:
            key = f'{ord(source):X}:{int(mirrored)}'
            if key in table and table[key] != character:
                raise ValueError(f'Conflicting verified entry for {digest} / {key}')
            table[key] = character
    result['provenance']['additional_chapters'] = [excerpt['chapter'] for excerpt in excerpts]
    return result


if __name__ == '__main__':
    result = build_bundled()
    encoded = dumps(result, ensure_ascii=False, indent=2).encode('utf-8')
    for destination in DESTINATIONS:
        destination.write_bytes(encoded)
    print('Verified fonts:', len(result['fonts']))
    print('Verified entries:', sum(len(font['entries']) for font in result['fonts'].values()))
