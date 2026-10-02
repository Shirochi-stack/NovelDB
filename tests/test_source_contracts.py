"""Integration checks for codenames and unchanged external connection values."""
import base64
import json

import pytest

from external_scraper import ExternalScraper
from scripts import source_names
from scripts.metadata_common import SOURCE_LABELS


@pytest.mark.parametrize('method, encoded, path', [
    ('is_npia', 'bm92ZWxwaWEuY29t', '/novel/346898'),
    ('is_global_npia', 'Z2xvYmFsLm5vdmVscGlhLmNvbQ==', '/novel/123'),
    ('is_kpage', 'cGFnZS5rYWthby5jb20=', '/content/123'),
    ('is_mpia', 'd3d3Lm11bnBpYS5jb20=', '/novel/detail/123'),
    ('is_jara', 'd3d3LmpvYXJhLmNvbQ==', '/book/123'),
    ('is_rbooks', 'cmlkaWJvb2tzLmNvbQ==', '/books/1234567890'),
    ('is_nweb_novel', 'bm92ZWwubmF2ZXIuY29t', '/webnovel/list?novelId=123'),
    ('is_nweb_series', 'c2VyaWVzLm5hdmVyLmNvbQ==', '/novel/detail.series?productNo=123'),
    ('is_floo', 'Yi5mYWxvby5jb20=', '/724903.html'),
])
def test_codename_detectors_recognize_real_wire_hosts(method, encoded, path):
    host = base64.b64decode(encoded).decode()
    url = 'https://' + host + path
    assert getattr(ExternalScraper, method)(url)
    stored = source_names.dumps({'canonical_url': url, 'title': '테스트 작품'})
    assert not source_names.NAME_PATTERN.search(stored)
    assert json.loads(stored) == {'canonical_url': url, 'title': '테스트 작품'}


def test_metadata_source_ids_use_codenames():
    assert set(SOURCE_LABELS) == {'nweb', 'mpia', 'jara', 'rbooks', 'nseries'}


def test_policy_does_not_rename_ordinary_words():
    text = 'riding, ridiculous, Riding, RIDING, RIDICULOUS, meridian, gridItems'
    assert source_names.to_codenames(text) == text
    assert not source_names.contains_name(text)
