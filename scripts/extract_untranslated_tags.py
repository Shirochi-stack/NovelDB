"""Extract untranslated tags from every Korean catalog into one shared queue."""
import argparse
import json
from pathlib import Path

try:
    from .metadata_pipeline import known_tags, clean_field
    from .tag_helpers import pending_tags, legacy_tags
except ImportError:
    from metadata_pipeline import known_tags, clean_field
    from tag_helpers import pending_tags, legacy_tags

# Kept for consumers importing the legacy helper.
load_tag_map_from_js = legacy_tags
SOURCES = ('novelpia', 'kakao', 'naver', 'joara', 'munpia', 'ridi', 'naverseries')


def extract(data_dir):
    data_dir = Path(data_dir)
    records = []
    for source in SOURCES:
        path = data_dir / ('novels.json' if source == 'novelpia' else f'{source}_novels.json')
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding='utf-8'))
        records.extend({'tags': row[4]} for row in rows if len(row) > 4 and isinstance(row[4], list))
    known = known_tags(data_dir, data_dir / 'tags_en.txt')
    for tag, english in legacy_tags(data_dir.parent / 'app.js').items():
        known.setdefault(tag, english)
    tags = pending_tags(records, known)
    output = data_dir / 'tags_untranslated.txt'
    output.write_text(''.join(f'{i}|||{clean_field(tag)}|||\n' for i, tag in enumerate(tags)), encoding='utf-8')
    print(f'Extracted {len(tags)} untranslated tags across Korean catalogs')
    return tags


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='docs/data')
    extract(parser.parse_args().data_dir)
