import pytest

from external_scraper import ExternalScraper


@pytest.mark.parametrize(
    "url",
    [
        "https://ntk1.com/novel/123",
        "https://www.ntk42.com/webtoon/456/789",
        "https://ntk1.org/novel/123",
        "https://www.ntk42.org/webtoon/456/789",
        "https://newtoki1.org/novel/58669",
        "https://www.newtoki42.com/webtoon/456/789",
    ],
)
def test_ntk_url_detection_accepts_numbered_ntk_and_newtoki_domains(url):
    assert ExternalScraper.is_ntk_novel(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://ntk.org/novel/123",
        "https://ntk1.net/novel/123",
        "https://newtoki.org/novel/58669",
        "https://newtoki1.net/novel/58669",
        "https://example.org/novel/123",
        "https://ntk1.org/not-a-novel/123",
    ],
)
def test_ntk_url_detection_rejects_unsupported_domains_and_paths(url):
    assert not ExternalScraper.is_ntk_novel(url)
