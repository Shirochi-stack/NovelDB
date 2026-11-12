"""Sites whose owners opted out: nothing reads them, and the reason is logged."""

import hashlib
import re

import pytest

from external_scraper import ExternalScraper
from noveldb.core import site_optouts
from noveldb.core.catalog import generic_search, sites
from noveldb.core.catalog.live_reader import LiveReader

SITE = "optout.example"
BOOK = f"https://{SITE}/novel/some-book/"


@pytest.fixture
def opted(monkeypatch):
    """`optout.example` on the list (the shipped list names no site, so the tests bring their own)."""
    digest = hashlib.sha256(SITE.encode()).hexdigest()
    monkeypatch.setattr(site_optouts, "_DIGESTS", frozenset({digest}))
    site_optouts._host_opted_out.cache_clear()
    yield
    site_optouts._host_opted_out.cache_clear()


def test_shipped_list_is_digests_only():
    assert site_optouts._DIGESTS
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in site_optouts._DIGESTS)


@pytest.mark.parametrize("url", [
    BOOK, f"http://{SITE}", f"https://www.{SITE}/", f"https://m.reader.{SITE}/chapter/1?x=1#top",
    f"https://{SITE.upper()}/", f"https://{SITE}.:443/a", SITE, f"www.{SITE}/novel/1",
])
def test_site_and_its_subdomains_are_opted_out(opted, url):
    assert site_optouts.opted_out(url)


@pytest.mark.parametrize("url", [
    "", "not a url", "https://example/", "https://other.example/", f"https://not{SITE}/",
    f"https://{SITE}.evil.test/", f"https://evil.test/{SITE}/", f"https://evil.test/?u=https://{SITE}/",
    "file:///C:/Library/book.epub", "https://[bad/",
])
def test_other_addresses_are_not(opted, url):
    assert not site_optouts.opted_out(url)


def test_downloader_refuses_and_logs(opted):
    logs = []
    scraper = ExternalScraper(logger=logs.append)
    scraper._parse_book_engines = lambda url: pytest.fail("an engine was asked for " + url)
    with pytest.raises(site_optouts.SiteOptedOut, match="request of the site owner"):
        scraper.parse_book(BOOK)
    assert logs == [site_optouts.MESSAGE]


def test_downloader_refuses_chapters_and_says_so_once(opted):
    logs = []
    scraper = ExternalScraper(logger=logs.append)
    chapter = {"url": f"https://{SITE}/novel/some-book/1/", "name": "One"}
    assert scraper.parse_chapter(0, chapter, interval=0) is None
    assert scraper.parse_chapter_batch([chapter, chapter], interval=0) == [None, None]
    assert scraper.abort_reason == site_optouts.MESSAGE
    assert logs == [site_optouts.MESSAGE]


def test_catalog_reader_and_fetch_ask_nothing(opted, monkeypatch):
    reader = LiveReader(fetch=lambda *_a, **_k: pytest.fail("the reader asked the site"))
    with pytest.raises(site_optouts.SiteOptedOut):
        reader.book(BOOK)

    import requests

    monkeypatch.setattr(requests, "request", lambda *_a, **_k: pytest.fail("a request left for the site"))
    assert generic_search.default_fetch(BOOK) is None


def test_catalog_offers_no_download(opted):
    result = sites.classify(BOOK)
    assert result.kind == sites.KIND_OTHER and not result.download
    assert not sites.supported_site(BOOK) and not sites.downloadable_page(BOOK)
