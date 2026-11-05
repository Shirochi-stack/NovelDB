"""The Catalog's searches of NovelDB's own sites (source_search.py) and the logins its requests carry (logins.py)."""
import json
import types
from urllib.parse import parse_qs, urlsplit

from noveldb.core.catalog import source_search
from noveldb.core.catalog.logins import SFC_HOST, SavedLogins
from noveldb.core.npia.ids import SITE_DOMAIN


def answer(value, url="https://example.test/"):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {"status": 200, "url": url, "text": text, "content": text.encode("utf-8"), "headers": ""}


def recording(reply):
    asked = []

    def fetch(url, **kwargs):
        asked.append((url, kwargs))
        return answer(reply(url) if callable(reply) else reply, url)
    return fetch, asked


def test_npia_keeps_only_books_with_the_exact_tag():
    rows = [{"novel_no": 1, "novel_name": "A", "writer_nick": "w", "novel_genre_arr": ["판타지", "TS"], "count_view": 1200,
             "count_good": 3, "count_book": 10, "is_complete": 1, "last_viewdate": "2026-10-04 10:00:00",
             "start_date": "2023-08-03", "novel_img": "/imagebox/cover/a.file"},
            {"novel_no": 2, "novel_name": "B", "writer_nick": "w", "novel_genre_arr": ["TS있음"]}]
    fetch, asked = recording({"status": 200, "total_cnt": 2, "list": rows})
    cards, more = source_search.search("npia", "tag", "TS", 0, fetch)
    assert [c["title"] for c in cards] == ["A"] and not more
    card = cards[0]
    assert card["url"] == f"https://{SITE_DOMAIN}/novel/1" and card["cover"] == f"https://images.{SITE_DOMAIN}/imagebox/cover/a.file"
    assert (card["views"], card["chapters"], card["status"], card["updated"], card["published"]) == \
        ("1,200", "10", "Completed", "2026-10-04", "2023-08-03")
    assert card["verified"] and card["sourceName"] == "npia"
    params = parse_qs(urlsplit(asked[0][0]).query)
    assert params["search_type"] == ["novel_genre"] and params["search_val"] == ["TS"] and params["page"] == ["1"]


def test_an_author_search_keeps_only_that_authors_books():
    rows = [{"series_id": 5, "title": "Mine", "authors": "백덕수", "sub_category": "현판", "state": "ST61"},
            {"series_id": 6, "title": "Not mine", "authors": "백덕수수"}]
    fetch, asked = recording({"result": {"list": rows, "is_end": True}})
    cards, more = source_search.search("kpage", "author", "백덕수", 0, fetch)
    assert [c["title"] for c in cards] == ["Mine"] and cards[0]["tags"] == ["현판"] and not more
    assert "keyword=" in asked[0][0] and asked[0][1]["headers"]["Referer"].startswith("https://page.")


def test_a_site_whose_books_have_only_genres_is_asked_only_for_a_genre():
    fetch, asked = recording({"result": {"list": [], "is_end": True}})
    assert source_search.search("kpage", "tag", "TS", 0, fetch) == ([], False)
    assert asked == []
    source_search.search("kpage", "tag", "현판", 2, fetch)
    params = parse_qs(urlsplit(asked[0][0]).query)
    assert params["subcategory_uid"] == ["120"] and params["page"] == ["2"]


NWEB_PAGE = """<ul class="default_list"><li class="item">
<a class="link" href="/best/list?novelId=77"><div class="thumbnail"><img src="https://img.test/c.jpg"></div>
<span class="title">The Book</span></a><div class="info_group"><span class="author">Writer</span></div>
<span class="tag"><strong>현대판타지</strong> #현대판타지 #회귀</span><span class="bullet_comp">완결</span></li></ul>"""


def test_nweb_asks_each_league_and_reads_its_list():
    fetch, asked = recording(NWEB_PAGE)
    cards, more = source_search.search("nweb", "tag", "#현대판타지", 0, fetch)
    assert len(asked) == 3 and {parse_qs(urlsplit(u).query)["section"][0] for u, _ in asked} == {"webnovel", "best", "challenge"}
    assert parse_qs(urlsplit(asked[0][0]).query)["keyword"] == ["현대판타지"]
    assert cards[0]["url"].endswith("/best/list?novelId=77") and cards[0]["tags"] == ["현대판타지", "회귀"]
    assert cards[0]["author"] == "Writer" and cards[0]["status"] == "Completed"


def test_a_tag_is_asked_under_each_sites_own_names():
    natives = {"npia": ["TS"], "jara": ["TS", "ts", "TS물"], "sfc": ["性转"]}
    asks = source_search.tag_queries("Gender Bender", natives)
    assert asks == {"npia": ["TS"], "jara": ["TS", "TS물"], "sfc": ["性转"]}
    # A tag NovelDB doesn't list goes to every site as typed; a status is no site's tag.
    assert set(source_search.tag_queries("회귀", {})) == set(source_search.SEARCHERS)
    assert source_search.tag_queries("Completed", natives) == {}


def test_a_failing_site_gives_nothing():
    def broken(url, **kwargs):
        raise OSError("down")
    assert source_search.search("mpia", "tag", "TS", 0, broken) == ([], False)


def test_the_next_pages_are_remembered_per_search():
    source_search.remember("npia", "tag", "Gender Bender", "TS", 1)
    source_search.remember("sfc", "tag", "Gender Bender", "性转", None)
    assert ("npia", "TS", 1) in source_search.pending("tag", "Gender Bender")
    source_search.remember("npia", "tag", "Gender Bender", "TS", None)
    assert source_search.pending("tag", "Gender Bender") == []


def test_logins_the_apps_npia_key_wins_and_cookies_stay_on_their_site():
    reads = []

    def profile(site):
        reads.append(site)
        return [{"domain": "." + site, "path": "/", "name": "LOGINKEY", "value": "browser"},
                {"domain": "." + site, "path": "/", "name": "OTHER", "value": "x"}]

    logins = SavedLogins(lambda: types.SimpleNamespace(loginkey="app", sfc_cookie="a=1; b=2"), profile)
    logins.observe("." + SITE_DOMAIN, "/", "SEEN", "catalog")
    logins.observe("shop.example", "/", "S", "1")
    cookies = logins.cookies(f"https://{SITE_DOMAIN}/novel/1")
    assert cookies == {"SEEN": "catalog", "LOGINKEY": "app", "OTHER": "x"}
    logins.header(f"https://{SITE_DOMAIN}/novel/2")
    assert reads == [SITE_DOMAIN]  # the Enter Browser's cookies are read once a few minutes
    assert logins.header("https://shop.example/x") == "S=1"
    assert logins.header("https://sub.shop.example/x") == ""  # a host's own cookie is not its subdomains'
    assert "a=1" in logins.header(f"https://{SFC_HOST}/Novel/1/")
    logins.forget("shop.example", "/", "S")
    assert logins.header("https://shop.example/x") == ""
