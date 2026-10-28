"""Catalog model: site addresses and the link cart (mobile `link-cart.json` format)."""

import json

from noveldb.core.catalog.cart import CartItem, LinkCart
from noveldb.core.catalog.urls import (
    NOVELDB_NEXT_URL,
    is_noveldb_next,
    is_noveldb_site,
    valid_catalog_url,
)
from noveldb.core.settings.model import DEFAULT_CATALOG_URL


def test_valid_catalog_url():
    assert valid_catalog_url(" https://example.com/catalog ") == "https://example.com/catalog"
    assert valid_catalog_url("http://localhost:8080/") == "http://localhost:8080/"
    assert valid_catalog_url("https://") is None
    assert valid_catalog_url("example.com") is None
    assert valid_catalog_url("ftp://example.com") is None
    assert valid_catalog_url("") is None
    assert valid_catalog_url(None) is None


def test_site_kinds():
    assert is_noveldb_site(DEFAULT_CATALOG_URL)
    assert is_noveldb_site(DEFAULT_CATALOG_URL.rstrip("/"))
    assert is_noveldb_site(NOVELDB_NEXT_URL)  # NovelDB Next is NovelDB too: no renamed links
    assert is_noveldb_next(NOVELDB_NEXT_URL.rstrip("/"))
    assert not is_noveldb_next(DEFAULT_CATALOG_URL)
    assert not is_noveldb_site("https://example.com/")


def test_cart_round_trip_and_dedupe(tmp_path):
    path = tmp_path / "link-cart.json"
    changes = []
    cart = LinkCart(str(path), on_change=lambda: changes.append(1))
    assert cart.add(CartItem("https://example.com/a", "A", "reader"))
    assert not cart.add(CartItem("https://example.com/a", "A again"))
    assert cart.add(CartItem("https://example.com/b"))
    assert len(cart) == 2 and "https://example.com/a" in cart
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"url": "https://example.com/a", "title": "A", "source": "reader"},
        {"url": "https://example.com/b", "title": "", "source": ""},
    ]
    reopened = LinkCart(str(path))
    assert reopened.urls == ["https://example.com/a", "https://example.com/b"]
    reopened.remove_all(["https://example.com/a", "https://example.com/missing"])
    assert LinkCart(str(path)).urls == ["https://example.com/b"]
    assert len(changes) == 2


def test_cart_reads_mobile_files_and_skips_bad_entries(tmp_path):
    path = tmp_path / "link-cart.json"
    path.write_text(json.dumps([{"url": "https://x.test/1"}, {"title": "no url"}, "junk", {"url": ""}]), encoding="utf-8")
    assert LinkCart(str(path)).urls == ["https://x.test/1"]
    path.write_text("not json", encoding="utf-8")
    assert LinkCart(str(path)).items == []


def test_adding_thousands_of_links_writes_once_and_stays_fast(tmp_path):
    import time

    path = tmp_path / "link-cart.json"
    notices = []
    cart = LinkCart(str(path), on_change=lambda: notices.append(1))
    started = time.perf_counter()
    added = cart.add_many(CartItem(f"https://x.test/novel/{i}") for i in range(10_000))
    assert added == 10_000 and len(cart) == 10_000
    assert cart.add_many(CartItem(f"https://x.test/novel/{i}") for i in range(9_990, 10_010)) == 10
    assert time.perf_counter() - started < 2.0
    assert len(notices) == 2  # one notice (and one file write) per call, not per link
    assert "https://x.test/novel/5" in cart
    cart.remove_all([f"https://x.test/novel/{i}" for i in range(5_000)])
    assert len(LinkCart(str(path))) == 5_010 and "https://x.test/novel/5" not in cart
