"""Add translated tags to the shared dictionary, preserving saved translations."""
import argparse
from pathlib import Path
try:
    from .metadata_pipeline import known_tags, read_corpus, decode_field, valid_english, write_tags
except ImportError:
    from metadata_pipeline import known_tags, read_corpus, decode_field, valid_english, write_tags


def merge(data_dir, patch=None):
    data_dir = Path(data_dir)
    tags = known_tags(data_dir, data_dir / 'tags_en.txt')
    before = len(tags)
    if patch:
        for original, english in read_corpus(patch).values():
            tag, english = decode_field(original), decode_field(english)
            if tag.strip() and valid_english(english):
                tags.setdefault(tag, english)
    write_tags(data_dir, tags)
    print(f'Added {len(tags) - before} tags; retained {before} existing translations')
    return tags


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('patch', nargs='?', default='docs/data/tags_untranslated.txt')
    parser.add_argument('--data-dir', default='docs/data')
    parser.add_argument('--recompress-only', action='store_true')
    args = parser.parse_args()
    merge(args.data_dir, None if args.recompress_only else args.patch)
