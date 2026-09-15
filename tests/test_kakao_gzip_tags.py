import gzip
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import kakao_descriptions as descriptions
from scripts.extract_untranslated_tags import extract
from scripts.merge_translated_tags import merge
from scripts.metadata_pipeline import read_tags, write_tags

ROOT = Path(__file__).resolve().parents[1]


def test_kakao_migration_merges_missing_rows_and_preserves_english(tmp_path):
    path = tmp_path / 'kakao_descriptions.txt'
    path.write_text('1|||원문|||Existing\n2|||둘|||Second\n', encoding='utf-8')
    compressed = Path(str(path) + '.gz')
    compressed.write_bytes(gzip.compress('1|||원문|||\n3|||셋|||Third\n'.encode()))
    descriptions.migrate(compressed)
    assert not path.exists()
    assert descriptions.read_rows(compressed) == {'1': ('원문', 'Existing'), '2': ('둘', 'Second'), '3': ('셋', 'Third')}
    before = compressed.read_bytes()
    descriptions.migrate(compressed)
    assert before == compressed.read_bytes()


def test_kakao_atomic_writer_retains_previous_file_on_failure(tmp_path):
    path = tmp_path / 'descriptions.txt.gz'
    with descriptions.open_text(path, 'w') as handle:
        handle.write('1|||original|||English\n')
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        with descriptions.open_text(path, 'w') as handle:
            handle.write('partial')
            raise RuntimeError('interrupted')
    assert path.read_bytes() == before


def test_kakao_gzip_only_extraction_merge_and_sharding(tmp_path):
    data = tmp_path / 'docs/data'
    data.mkdir(parents=True)
    path = data / 'kakao_descriptions.txt.gz'
    with descriptions.open_text(path, 'w') as handle:
        handle.write('1|||이것은 소설의 한국어 설명입니다|||\n2|||기존 설명|||Existing translation\n')
    def run(name, *args):
        subprocess.run([sys.executable, str(ROOT / 'scripts' / name), *args], cwd=tmp_path, check=True, capture_output=True)
    run('extract_untranslated_kakao_descriptions.py')
    patch = data / 'kakao_descriptions_untranslated.txt'
    patch.write_text('1|||이것은 소설의 한국어 설명입니다|||New translation\n2|||기존 설명|||Replacement\n', encoding='utf-8')
    run('merge_translated_kakao_descriptions.py')
    run('chunk_descriptions.py', str(path), '--prefix', 'kakao_descriptions_shard', '--output-dir', str(data), '-n', '128')
    assert descriptions.read_rows(path)['1'][1] == 'New translation'
    assert descriptions.read_rows(path)['2'][1] == 'Existing translation'
    assert not (data / 'kakao_descriptions.txt').exists()
    assert len(list(data.glob('kakao_descriptions_shard_*.json.gz'))) == 128


def test_all_korean_tag_sources_and_existing_translations(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    for source in ('joara', 'ridi'):
        (data / f'{source}_novels.json').write_text(json.dumps([[1, 'Title', '', '', ['공유', source + '태그', '기존']]]), encoding='utf-8')
    write_tags(data, {'기존': 'Keep me'})
    tags = extract(data)
    assert tags.count('공유') == 1 and {'joara태그', 'ridi태그'}.issubset(tags)
    assert '기존' not in tags
    patch = data / 'patch.txt'
    patch.write_text('0|||공유|||Shared\n1|||기존|||Overwrite\n', encoding='utf-8')
    merge(data, patch)
    assert read_tags(data / 'tags_en.txt') == {'기존': 'Keep me', '공유': 'Shared'}
    before = (data / 'tags_en.txt.gz').read_bytes()
    merge(data, patch)
    assert (data / 'tags_en.txt.gz').read_bytes() == before
