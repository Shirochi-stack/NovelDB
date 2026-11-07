"""The generic engine on Asura's comics, rawkuma and fanfox's http links; which sites it goes first on."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from noveldb.core.engines.generic import asura_address, asura_pages, clean_name, dm5_desktop
from noveldb.core.engines.scraper_mixin import generic_first

ROOT = Path(__file__).resolve().parents[1]
PAGE = ('<astro-island props="{&quot;pages&quot;:[1,[[0,{&quot;url&quot;:[0,&quot;https://cdn.asurascans.com/asura-images/'
        'chapters/x/1/001.webp?v=1&quot;],&quot;width&quot;:[0,800]}],[0,{&quot;url&quot;:[0,&quot;https://cdn.asurascans.com/'
        'asura-images/chapters/x/1/002.webp?v=1&quot;]}]]],&quot;cover&quot;:[0,&quot;https://cdn.asurascans.com/asura-images/'
        'covers/x.webp&quot;]}"></astro-island>')


def test_asuras_old_comic_addresses_become_its_new_ones():
    """asuracomic.net/series/... leads to the home page now (read as a book called "Series", 463 chapters)."""
    assert asura_address("https://asuracomic.net/series/chronicles-of-the-demon-faction-7f873ca6") == \
        "https://asurascans.com/comics/chronicles-of-the-demon-faction-7f873ca6"
    assert asura_address("https://asuracomic.net/series/x-1/chapter/3") == "https://asurascans.com/comics/x-1/chapter/3"
    assert asura_address("https://asurascans.com/novels/x/chapter/1") == "https://asurascans.com/novels/x/chapter/1"
    assert asura_address("https://example.com/series/x") == "https://example.com/series/x"


def test_an_asura_chapters_pictures_come_from_its_page_data_without_the_cover():
    assert asura_pages(PAGE) == ["https://cdn.asurascans.com/asura-images/chapters/x/1/001.webp?v=1",
                                 "https://cdn.asurascans.com/asura-images/chapters/x/1/002.webp?v=1"]
    assert asura_pages("<p>a novel</p>") == []


def test_a_fanfox_link_is_read_over_https():
    """An http chapter page held no pages: a book opened from an http link read none."""
    assert dm5_desktop("http://fanfox.net/manga/one_piece/v01/c001/1.html") == "https://fanfox.net/manga/one_piece/v01/c001/1.html"
    assert dm5_desktop("http://www.mangahere.cc/manga/x/") == "https://www.mangahere.cc/manga/x/"
    assert dm5_desktop("http://example.com/manga/x/") == "http://example.com/manga/x/"


def test_a_chapters_views_and_comments_after_its_age_go():
    assert clean_name("Chapter 1197 5 days ago 9.0K 4") == "Chapter 1197"
    assert clean_name("Chapter 5 Title 2020") == "Chapter 5 Title 2020"


def test_the_generic_engine_goes_first_on_rawkuma_and_asuras_comics_not_its_novels():
    assert generic_first("https://rawkuma.net/manga/one-piece/")
    assert generic_first("https://asurascans.com/comics/x-ab39de10")
    assert generic_first("https://asuracomic.net/series/x-1")
    assert not generic_first("https://asurascans.com/novels/x")
    assert not generic_first("https://example.com/comics/x")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_phone_engine_reads_asura_the_same_way():
    script = """
global.window = {fetch: () => Promise.reject(new Error('offline'))};
eval(require('fs').readFileSync('scripts/generic/generic-lib.js', 'utf8'));
const g = window.__GEN_lib;
console.log(JSON.stringify({pages: g.asuraPages(%s), address: g.asuraAddress('https://asuracomic.net/series/x-1/chapter/3'),
                            fanfox: g.dm5Desktop('http://fanfox.net/manga/x/c001/1.html')}));
""" % json.dumps(PAGE)
    out = json.loads(subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out["pages"] == asura_pages(PAGE)
    assert out["address"] == "https://asurascans.com/comics/x-1/chapter/3"
    assert out["fanfox"] == "https://fanfox.net/manga/x/c001/1.html"
