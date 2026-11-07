"""The generic engine and the Catalog's site search on webtoons.com, from saved pages: the series page and the
episode list API, desktop and mobile episode pages, the search under /en/, and the phone engine doing the same."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from noveldb.core.catalog.site_search import SiteSearch, extract_cards
from noveldb.core.engines.generic import (
    GenericEngine,
    soup_of,
    webtoons_chapters,
    webtoons_desktop,
    webtoons_episodes_api,
    webtoons_images,
    webtoons_metadata,
    webtoons_series,
    webtoons_title,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "webtoons"
SERIES = "https://www.webtoons.com/en/fantasy/tower-of-god/list?title_no=95"
EPISODE = "https://www.webtoons.com/en/fantasy/tower-of-god/season-1-ep-0/viewer?title_no=95&episode_no=1"
API = "https://m.webtoons.com/api/v1/webtoon/95/episodes?pageSize=99999"
FIRST_PAGES = ["https://webtoon-phinf.pstatic.net/20200328_249/1585334566015rEpaA_JPEG/15853345629669517.jpg",
               "https://webtoon-phinf.pstatic.net/20200328_277/1585334566029jg5xc_JPEG/15853345634229512.jpg",
               "https://webtoon-phinf.pstatic.net/20200328_202/1585334566015OIWyG_JPEG/15853345629669514.jpg"]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def answer(url: str, text: str, status: int = 200) -> dict:
    return {"status": status, "url": url, "content": text.encode("utf-8"), "text": text,
            "headers": "Content-Type: text/html; charset=utf-8"}


def test_addresses_name_the_series_and_the_desktop_site():
    assert webtoons_title(SERIES) == ("webtoon", "95")
    assert webtoons_title("https://www.webtoons.com/en/canvas/x/list?title_no=726081") == ("canvas", "726081")
    assert webtoons_title("https://example.com/list?title_no=95") is None
    assert webtoons_desktop("https://m.webtoons.com/en/fantasy/tower-of-god/list?title_no=95&webtoon-platform-redirect=true") == SERIES
    assert webtoons_series(EPISODE) == SERIES
    assert webtoons_episodes_api(SERIES) == API


def test_the_episode_list_comes_oldest_first():
    episodes = webtoons_chapters(fixture("episodes.json"))
    assert [e["url"].split("episode_no=")[1] for e in episodes] == ["1", "2", "3", "653"]
    assert episodes[0] == {"url": EPISODE, "name": "[Season 1] Ep. 0"}
    assert webtoons_chapters("not json") == []


def test_the_series_page_gives_title_author_synopsis_and_genre():
    meta = webtoons_metadata(soup_of(fixture("series.html")))
    assert meta["title"] == "Tower of God" and meta["author"] == "SIU" and meta["tags"] == ["Fantasy"]
    assert meta["description"].startswith("What do you desire?")


def test_episode_images_from_the_desktop_and_the_mobile_page():
    desktop = webtoons_images(fixture("episode.html"), EPISODE)
    assert [u.split("?")[0] for u in desktop] == FIRST_PAGES  # the lazy data-url, not the placeholder or a thumbnail
    assert webtoons_images(fixture("episode_mobile.html"), EPISODE.replace("www.", "m.")) == FIRST_PAGES


def test_the_engine_reads_every_episode_from_the_api_and_an_episode_link_opens_its_series():
    asked = []

    def fetch(url, referer="", **_kwargs):
        asked.append(url)
        if url == API:
            return answer(url, fixture("episodes.json"))
        if "/viewer" in url:
            return answer(url, fixture("episode.html"))
        return answer(url, fixture("series.html"))

    engine = GenericEngine(fetch=fetch)
    book = engine.parse_book(EPISODE.replace("www.", "m."))
    assert asked[0] == SERIES and book["bookUrl"] == SERIES
    assert book["bookname"] == "Tower of God" and book["author"] == "SIU"
    assert len(book["chapters"]) == 4 and not any("page=2" in url for url in asked)  # no walk through the list pages
    result = engine.parse_chapter(book["chapters"][0], book)
    assert [part.split('"')[0].split("?")[0] for part in result["contentHtml"].split('<img src="')[1:]] == FIRST_PAGES


def test_the_search_page_under_the_language_home_is_found_and_read():
    def fetch(url, **_kwargs):
        if url == "https://www.webtoons.com/":
            return answer("https://www.webtoons.com/en/", "<html><body><a href='/en/originals'>Originals</a></body></html>")
        if url.startswith("https://www.webtoons.com/en/search?keyword="):
            return answer(url, fixture("search.html"))
        return answer(url, "<html><body>Not found</body></html>", 404)

    cards = SiteSearch(fetch).search("https://www.webtoons.com/", "tower of god")
    assert SERIES in [card["url"] for card in cards]
    assert all(card["cover"] for card in extract_cards(fixture("search.html"), "https://www.webtoons.com/en/search?keyword=x"))


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_phone_engine_reads_the_saved_pages_the_same_way():
    script = """
global.window = {fetch: () => Promise.reject(new Error('offline'))};
global.NodeFilter = {SHOW_TEXT: 4};
const fs = require('fs');
eval(fs.readFileSync('scripts/generic/generic-lib.js', 'utf8'));
const g = window.__GEN_lib, dir = 'tests/fixtures/webtoons/';
const mobile = fs.readFileSync(dir + 'episode_mobile.html', 'utf8');
const listed = /imageList\\s*=\\s*\\[([\\s\\S]*?)\\]\\s*;/.exec(mobile);
console.log(JSON.stringify({
  error: window.__GEN_LOAD_ERROR || null,
  title: g.webtoonsTitle('%(episode)s'),
  series: g.webtoonsSeries('%(episode)s'.replace('www.', 'm.')),
  api: g.webtoonsEpisodesApi('%(series)s'),
  episodes: g.webtoonsChapters(fs.readFileSync(dir + 'episodes.json', 'utf8')).map((e) => e.url),
  mobile: Array.from(listed[1].matchAll(/url\\s*:\\s*"([^"]+)"/g), (m) => m[1]),
}));
""" % {"episode": EPISODE, "series": SERIES}
    out = json.loads(subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out["error"] is None
    assert out["title"] == ["webtoon", "95"]
    assert out["series"] == SERIES and out["api"] == API
    assert out["episodes"] == [e["url"] for e in webtoons_chapters(fixture("episodes.json"))]
    assert out["mobile"] == FIRST_PAGES
