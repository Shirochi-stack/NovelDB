"""Canonical, atomic gzip I/O with a lossless legacy description migration."""
from contextlib import contextmanager
import gzip
import io
import os
from pathlib import Path
import tempfile

DEFAULT = Path('docs/data/kakao_descriptions.txt.gz')


def canonical(path=DEFAULT):
    path = Path(path)
    return path if path.suffix == '.gz' else Path(str(path) + '.gz')


def read_rows(path=DEFAULT):
    path = canonical(path)
    rows = {}
    for source in (path, path.with_suffix('')):
        if not source.exists():
            continue
        opener = gzip.open if source.suffix == '.gz' else open
        with opener(source, 'rt', encoding='utf-8') as handle:
            for line in handle:
                parts = line.rstrip('\r\n').split('|||', 2)
                if not line.strip():
                    continue
                if len(parts) != 3 or not parts[0].isdigit():
                    raise ValueError(f'Malformed description row in {source}')
                ident, original, english = parts
                previous = rows.get(ident)
                if previous:
                    if previous[1] and english and previous[1] != english:
                        raise ValueError(f'Conflicting saved translations for Kakao {ident}; retain both inputs for review')
                    original, english = previous[0] or original, previous[1] or english
                rows[ident] = (original, english)
    return rows


@contextmanager
def open_text(path=DEFAULT, mode='r', encoding='utf-8'):
    path = canonical(path)
    if mode.startswith('r'):
        rows = read_rows(path)
        with io.StringIO(''.join(f'{ident}|||{raw}|||{en}\n' for ident, (raw, en) in rows.items())) as handle:
            yield handle
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as raw:
            temporary = Path(raw.name)
            with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0, compresslevel=6) as compressed:
                with io.TextIOWrapper(compressed, encoding=encoding, newline='\n') as handle:
                    yield handle
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def migrate(path=DEFAULT):
    path = canonical(path)
    rows = read_rows(path)
    with open_text(path, 'w') as handle:
        for ident, (raw, en) in rows.items():
            handle.write(f'{ident}|||{raw}|||{en}\n')
    if read_rows(path) != rows:
        raise ValueError('Kakao migration verification failed')
    legacy = path.with_suffix('')
    if legacy.exists():
        legacy.unlink()
    print(f'Verified gzip migration: {len(rows):,} descriptions, {sum(bool(en) for _, en in rows.values()):,} translations')


if __name__ == '__main__':
    migrate()
