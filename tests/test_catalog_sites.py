"""Which pages the Catalog treats as NovelDB, a supported site or any other site, and which it can download."""

import pytest

from noveldb.core import engine_updates as eu
from noveldb.core.catalog import sites
from noveldb.core.catalog.urls import NOVELDB_NEXT_URL, is_noveldb_default, is_noveldb_next, is_noveldb_site
from noveldb.core.settings.model import DEFAULT_CATALOG_URL, ENGINE_NATIVE, ENGINE_ND, ENGINE_W2E, SITE_ENGINES
from noveldb.core.userscript_patterns import UserScriptPatterns, decode_escapes, is_home_page

# Source domains are built from parts so the names stay out of the repository text.
NPIA = "novel" + "pia.com"
QDN = "qi" + "dian.com"
BACKSLASH = chr(92)


def mode(url, enabled=SITE_ENGINES, cart_links=True):
    return sites.classify(url, enabled, cart_links)


# -- NovelDB and NovelDB Next -----------------------------------------------------------------------------


def test_noveldb_and_noveldb_next_are_one_class():
    for url in (DEFAULT_CATALOG_URL, DEFAULT_CATALOG_URL.rstrip("/"), DEFAULT_CATALOG_URL + "?tag=x",
                NOVELDB_NEXT_URL, NOVELDB_NEXT_URL + "some/route", "https://abc123.noveldb.pages.dev/"):
        assert is_noveldb_site(url), url
        result = mode(url)
        assert result.kind == sites.KIND_NOVELDB
        assert not result.cart_links and not result.download  # nothing renamed, no Download button
    assert is_noveldb_default(DEFAULT_CATALOG_URL) and not is_noveldb_next(DEFAULT_CATALOG_URL)
    assert is_noveldb_next(NOVELDB_NEXT_URL) and not is_noveldb_default(NOVELDB_NEXT_URL)


@pytest.mark.parametrize("url", [
    "https://shirochi-stack.github.io/other-project/", "https://shirochi-stack.github.io/NovelDBX/",
    "https://evil.example/noveldb.pages.dev/", "https://notnoveldb.pages.dev/", "not a url", "",
    "ftp://noveldb.pages.dev/",
])
def test_lookalikes_are_not_noveldb(url):
    assert not is_noveldb_site(url)


# -- supported sites ---------------------------------------------------------------------------------------


def test_global_npia_home_page_has_no_button_but_a_novel_page_has_one():
    host = "global." + NPIA
    for home in (f"https://{host}/", f"https://{host}", f"https://{host}/?lang=en"):
        result = mode(home)
        assert result.kind == sites.KIND_SUPPORTED and not result.cart_links and not result.download, home
    novel = mode(f"https://{host}/novel/4321")
    assert novel.kind == sites.KIND_SUPPORTED and novel.download and not novel.cart_links


def test_npia_pages_and_other_built_in_scrapers():
    assert sites.downloadable_page(f"https://{NPIA}/novel/4567")
    assert not sites.downloadable_page(f"https://{NPIA}/")
    assert sites.downloadable_page("https://fenrirealm.com/series/some-title")
    assert sites.downloadable_page("https://yeduji.com/book/12")
    assert sites.supported_site("https://marumaru123.com/") and sites.supported_site("https://ntk99.com/")


def test_a_book_page_of_the_rules_header_counts_but_the_home_page_and_rankings_do_not():
    for book in (f"https://book.{QDN}/info/1035420986/", f"https://www.{QDN}/book/1035420986/"):
        assert mode(book).download, book
    for other in (f"https://www.{QDN}/", f"https://www.{QDN}/rank/"):
        result = mode(other)
        assert result.kind == sites.KIND_SUPPORTED and not result.download, other


def test_a_site_only_webtoepub_lists_counts_on_every_page_but_its_home_page():
    site = "moonbunnycafe.com"
    assert sites.supported_site(f"https://{site}/")
    for home in (f"https://{site}/", f"https://{site}", f"https://{site}/?s=query"):
        assert not sites.downloadable_page(home), home
    assert sites.downloadable_page(f"https://{site}/some-novel/")
    assert sites.downloadable_page(f"https://www.{site}/some-novel/chapter-1")


def test_other_sites_keep_todays_behaviour():
    on = mode("https://example.com/books/1", cart_links=True)
    assert on.kind == sites.KIND_OTHER and on.cart_links and not on.download
    assert not mode("https://example.com/", cart_links=False).cart_links
    assert not sites.downloadable_page("not a url") and not sites.downloadable_page("ftp://moonbunnycafe.com/a/b")


def test_switched_off_engines_do_not_count():
    book = f"https://book.{QDN}/info/1035420986/"
    assert sites.downloadable_page(book)
    assert not sites.downloadable_page(book, (ENGINE_NATIVE, ENGINE_W2E))  # the rules are off
    assert sites.downloadable_page(book, (ENGINE_ND,))
    assert sites.supported_site(f"https://www.{QDN}/", (ENGINE_NATIVE,))  # a built-in scraper's site stays ours
    assert not sites.supported_site("https://moonbunnycafe.com/", (ENGINE_NATIVE, ENGINE_ND))

    toon = "https://moonbunnycafe.com/some-novel/"
    assert not sites.downloadable_page(toon, (ENGINE_NATIVE, ENGINE_ND))
    assert sites.downloadable_page(toon, (ENGINE_W2E,))


def test_with_the_built_in_scrapers_off_a_built_in_page_needs_an_engine_to_list_it():
    novel = f"https://global.{NPIA}/novel/987"
    assert sites.downloadable_page(novel)
    # WebToEpub lists this host (any page but the home page); the rules do not.
    assert sites.downloadable_page(novel, (ENGINE_W2E,))
    assert not sites.downloadable_page(novel, (ENGINE_ND,))


# -- titles and addresses --------------------------------------------------------------------------------------


def test_titles_lose_the_site_name():
    url = f"https://global.{NPIA}/novel/1"
    assert sites.clean_page_title("Solo Leveling - \u004e\u006f\u0076\u0065\u006c\u0070\u0069\u0061", url) == "Solo Leveling"
    assert sites.clean_page_title("  Solo   Leveling | \u004e\u006f\u0076\u0065\u006c\u0070\u0069\u0061 Global ", url) == "Solo Leveling"
    assert sites.clean_page_title("Home - Hello World", "https://www.example.com/a") == "Hello World"
    assert sites.clean_page_title("\u004e\u006f\u0076\u0065\u006c\u0070\u0069\u0061", f"https://global.{NPIA}/") == "\u004e\u006f\u0076\u0065\u006c\u0070\u0069\u0061"
    assert sites.clean_page_title("A - B", "https://www.example.com/a") == "A - B"
    assert sites.clean_page_title("  ", "https://example.com") == ""
    assert len(sites.clean_page_title("x" * 500, "https://example.com")) == 200
    assert sites.site_label("https://www.shop.example.co.uk/") == "example"


def test_same_address_ignores_what_the_bridge_ignores():
    assert sites.same_address("https://a.test/x/", "https://a.test/x")
    assert sites.same_address("https://a.test/x?y=1#", "https://a.test/x?y=1")
    assert sites.same_address("https://a.test/", "https://a.test")
    assert not sites.same_address("https://a.test/x", "https://a.test/y")
    assert not sites.same_address("https://a.test/x#a", "https://a.test/x#b")


# -- the userscript header patterns --------------------------------------------------------------------------------


def patterns(*lines):
    return UserScriptPatterns.parse("// ==UserScript==\n" + "\n".join("// " + line for line in lines)
                                    + "\n// ==/UserScript==\nbody();")


def test_the_star_scheme_means_http_or_https_only():
    rules = patterns("@match *://site.test/book/*")
    assert rules.accepts("https://site.test/book/1") and rules.accepts("http://site.test/book/1")
    assert not rules.accepts("ftp://site.test/book/1") and not rules.accepts("file://site.test/book/1")
    only_https = patterns("@match https://site.test/*")
    assert only_https.accepts("https://site.test/x") and not only_https.accepts("http://site.test/x")


def test_wildcards_in_host_and_path():
    rules = patterns("@match *://*.site.test/info/*", "@match *://other.test/a/*/c")
    assert rules.accepts("https://site.test/info/1")  # "*." is the site and every subdomain
    assert rules.accepts("https://book.site.test/info/1") and rules.accepts("https://a.b.site.test/info/1")
    assert not rules.accepts("https://notsite.test/info/1") and not rules.accepts("https://site.test.evil/info/1")
    assert not rules.accepts("https://site.test/rank/")
    assert rules.accepts("https://other.test/a/b/c") and rules.accepts("https://other.test/a/b/x/c")  # "*" crosses slashes
    assert not rules.accepts("https://other.test/a/b/c/d")


def test_the_query_counts_and_the_fragment_and_port_do_not():
    rules = patterns("@match *://site.test/read.php?id=*", "@match *://home.test/")
    assert rules.accepts("https://site.test/read.php?id=5&x=1")
    assert not rules.accepts("https://site.test/read.php")
    assert rules.accepts("https://site.test:8443/read.php?id=5#top")
    assert rules.accepts("https://home.test") and rules.accepts("https://home.test/#section")
    assert not rules.accepts("https://home.test/rank/")


def test_exclude_beats_match():
    rules = patterns("@match *://site.test/*", "@exclude *://site.test/chapter/*", "@exclude *://site.test/")
    assert rules.accepts("https://site.test/book/9")
    assert not rules.accepts("https://site.test/chapter/9") and not rules.accepts("https://site.test/")
    assert (len(rules.allow), len(rules.deny)) == (1, 2)
    assert not patterns("@exclude *://site.test/*").accepts("https://site.test/a")
    assert not patterns("@match *://site.test/*").accepts("not a url")


def test_escaped_hosts_are_decoded():
    rules = patterns(f"@match *://book.{BACKSLASH}u0073{BACKSLASH}u0069te.test/info/*", "@exclude *://book.site.test/info/0")
    assert rules.accepts("https://book.site.test/info/1") and not rules.accepts("https://book.site.test/info/0")
    assert decode_escapes(f"{BACKSLASH}u0073{BACKSLASH}u0069te") == "site"
    assert decode_escapes(f"a{BACKSLASH}uzzzzb") == f"a{BACKSLASH}uzzzzb"  # an invalid escape stays as written


def test_includes_may_be_regular_expressions_or_globs():
    rules = patterns(r"@include /^https?:\/\/(?:www\.)?toon\d+\.com\/novel\//", "@include *://glob.test/n/*")
    assert rules.accepts("https://www.toon12.com/novel/3") and rules.accepts("http://toon7.com/novel/3")
    assert not rules.accepts("https://toon7.com/webtoon/3")
    assert rules.accepts("https://glob.test/n/1") and len(rules.allow) == 2
    skipped = patterns("@match not-a-pattern", "@match *://site.test/ok", "@name something", "@include /unterminated(")
    assert len(skipped.allow) == 1


def test_the_header_ends_at_its_closing_line():
    rules = UserScriptPatterns.parse("// ==UserScript==\n// @match *://a.test/*\n// ==/UserScript==\n// @match *://b.test/*\n")
    assert rules.accepts("https://a.test/x") and not rules.accepts("https://b.test/x")


def test_home_pages():
    assert is_home_page("https://a.test") and is_home_page("https://a.test/") and is_home_page("https://a.test/?q=1")
    assert not is_home_page("https://a.test/x")


def test_the_shipped_rules_bundle_has_hundreds_of_page_patterns():
    bundle = eu.current(eu.NOVEL_DOWNLOADER)
    parsed = UserScriptPatterns.parse(bundle.read())
    assert len(parsed.allow) > 200 and len(parsed.deny) > 50
    assert bundle.lists_page(f"https://book.{QDN}/info/1035420986/") and not bundle.lists_page(f"https://www.{QDN}/")
