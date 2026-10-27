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
    assert not is_noveldb_site(NOVELDB_NEXT_URL)
    assert is_noveldb_next(NOVELDB_NEXT_URL.rstrip("/"))


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
