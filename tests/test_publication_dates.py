from bs4 import BeautifulSoup

from scripts.publication_dates import (
    explicit_publication_date,
    normalize_publication_date,
    structured_publication_date,
)


def test_normalizes_only_real_source_supplied_dates():
    assert normalize_publication_date("20261024123456") == "2026-10-24T12:34:56"
    assert normalize_publication_date("2026.10.24") == "2026-10-24"
    assert normalize_publication_date("20261024") == "2026-10-24"
    assert normalize_publication_date("not a date") is None
    assert explicit_publication_date({"updated": "2026-10-24"}, "published") is None
    assert explicit_publication_date({"published": "2020/01/02"}, "published") == "2020-01-02"


def test_structured_metadata_requires_an_explicit_publication_property():
    soup = BeautifulSoup(
        '<meta property="article:published_time" content="2022-03-04T05:06:07+09:00">',
        "html.parser",
    )
    assert structured_publication_date(soup) == "2022-03-04T05:06:07+09:00"
    assert structured_publication_date(BeautifulSoup('<time datetime="2024-01-01"></time>', "html.parser")) is None
