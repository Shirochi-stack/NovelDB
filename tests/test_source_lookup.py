"""A NovelDB card's live figures: each of NovelDB's sites asked for one book by its id
(source_search.lookup), and the card refreshed without touching what NovelDB translated."""

import json
import re
from pathlib import Path

from noveldb.core.catalog import source_search
from noveldb.core.catalog.generic_search import GenericSearch, refreshed

ROOT = Path(__file__).resolve().parents[1]

NSERIES_PAGE = """<html><body><div class="end_head"><h2>군필 여대생의 게임방송</h2></div>
<ul class="end_info"><li class="info_lst"><ul><li class="ing"><span>연재중</span></li>
<li><span><a href="/novel/categoryProductList.series?categoryTypeCode=genre&genreCode=208">현판</a></span></li>
<li><span>글</span><a href="/search/search.series?q=x">안현</a></li></ul></li></ul>
<div class="pic_area"><img src="https://covers.test/n.jpg"></div><p>총 143 화</p></body></html>"""
NWEB_PAGE = """<html><body><div class="section_area_info"><span class="thumbnail"><img src="https://covers.test/w.png"></span>
<div class="info_top"><h2 class="title">촛불</h2><div class="info_group"><span class="item">자유장르</span>
<span class="item"><a href="/search?keyword=Two.J&target=author">Two.J</a></span></div></div>
<p class="summary"><span id="summaryText">로맨스+일상</span></p>
<div class="_reactionModule"><em class="_count">12</em></div></div>
<div class="end_tag_area"><div class="tag_collection"><a class="tag">#일상</a></div></div></body></html>"""


def fetcher(reads):
    def fetch(url, headers=None, referer=None, **_kwargs):
        reads.append(url)
        if "/novel-detail/573666" in url:
            body = {"result": {"novelInfo": {"title": "군필 여대생의 게임방송", "authorName": "안현™", "coverUrl": "https://covers.test/m",
                                             "genres": ["현대판타지"], "tags": [{"title": "TS"}], "viewCount": 667747,
                                             "likeCount": 16420, "chapterCount": 143, "finish": False,
                                             "updatedAt": "2026-11-05T08:10:00", "createdAt": "2026-05-28T21:31:57"}}}
        elif "/content/overview" in url:
            body = {"result": {"content": {"title": "비", "authors": "나의마음", "thumbnail": "kid", "sub_category": "BL",
                                           "on_sale_count": 25, "state": "ST61", "service_property": {"view_count": 836},
                                           "last_slide_added_dt": "2021-10-16T00:00:05+09:00"}}}
        elif "/novels/567122" in url:
            body = {"data": {"novelId": 567122, "novelName": "屠龙失败", "authorName": "mika", "viewTimes": 48407817,
                             "markCount": 198064, "isFinish": True, "lastUpdateTime": "2025-10-05T13:51:21",
                             "expand": {"typeName": "魔幻", "sysTags": [{"tagName": "异世界"}]}}}
        elif "detail.series" in url:
            return {"status": 200, "text": NSERIES_PAGE, "url": url}
        elif "webnovel/list" in url:
            return {"status": 200, "text": NWEB_PAGE, "url": url.replace("webnovel", "challenge")}
        elif "/proc/novel" in url:
            body = {"total_cnt": 2, "list": [
                {"novel_no": 99, "novel_name": "Other", "writer_nick": "작가"},
                {"novel_no": 610, "novel_name": "창작물 속으로", "writer_nick": "작가", "count_view": 116672175,
                 "count_good": 5, "count_book": 3300, "is_complete": 0, "novel_genre_arr": ["판타지"]}]}
        else:
            return {"status": 404, "text": ""}
        return {"status": 200, "text": json.dumps(body), "url": url}
    return fetch


def test_each_site_is_asked_once_for_one_book():
    asks = [("mpia", "573666", "", "군필 여대생의 게임방송", "143"), ("kpage", "57928301", "", "비", "25"),
            ("sfc", "567122", "", "屠龙失败", None), ("nseries", "14672427", "", "군필 여대생의 게임방송", "143"),
            ("nweb", "325870", "", "촛불", None), ("npia", "610", "작가", "창작물 속으로", "3,300")]
    for source, book, author, title, chapters in asks:
        reads = []
        found = source_search.lookup(source, book, fetcher(reads), author)
        assert found and found["title"] == title, source
        assert found.get("chapters") == chapters, source
        assert len(reads) == 1, (source, reads)
    assert source_search.lookup("mpia", "573666", fetcher([]))["views"] == "667,747"
    assert source_search.lookup("nweb", "325870", fetcher([]))["url"].endswith("/challenge/list?novelId=325870")
    # npia finds a book by its author: none given, nothing asked.
    reads = []
    assert source_search.lookup("npia", "610", fetcher(reads)) is None and reads == []
    assert source_search.lookup("mpia", "1", fetcher([])) is None


def test_a_refreshed_card_keeps_what_noveldb_translated():
    card = {"url": "https://example.test/1", "title": "A Military-Trained College Girl's Gaming Stream",
            "originalTitle": "군필 여대생의 게임방송", "tags": ["Modern Fantasy"], "description": "A professional gamer…",
            "views": "655,161", "chapters": "123", "cover": "https://covers.test/old"}
    live = source_search.lookup("mpia", "573666", fetcher([]))
    out = refreshed(card, live)
    assert out["fresh"] and out["views"] == "667,747" and out["chapters"] == "143" and out["updated"] == "2026-11-05"
    assert out["title"] == card["title"] and out["tags"] == ["Modern Fantasy"] and out["description"] == card["description"]
    assert out["originalTitle"] == "군필 여대생의 게임방송" and out["cover"] == "https://covers.test/old"
    assert refreshed(card, None) == card


def test_a_noveldb_cards_details_carry_its_live_figures():
    class Index:
        def description(self, _card):
            return "Synopsis."

    search = GenericSearch(fetch=fetcher([]), sites=[], noveldb=Index())
    card = {"url": "https://example.test/1", "title": "English", "originalTitle": "군필 여대생의 게임방송", "source": "noveldb",
            "noveldb": {"source": "mpia", "id": "573666"}, "views": "1"}
    out = search.details(card)
    assert out["description"] == "Synopsis." and out["views"] == "667,747" and out["title"] == "English"


def test_the_phones_look_up_the_same_sites():
    text = (ROOT / "scripts" / "generic" / "catalog-app.js").read_text(encoding="utf-8")
    block = text[text.index("var SOURCE_LOOKUPS = {"):text.index("function sourceLookup")]
    assert set(re.findall(r"^\s{8}(\w+): function", block, re.M)) == set(source_search.LOOKERS) == set(source_search.SEARCHERS)
