"""Bilibili Manga's built-in scraper: the app API, locked chapters, and pages saved while their addresses last."""

import base64
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from noveldb.core.engines import bilicomic
from noveldb.core.engines.bilicomic import BiliComicEngine

BOOK = "https://manga.bilibili.com/detail/mc29839"
JPEG = b"\xff\xd8\xff\xe0" + b"page" * 8
PNG = b"\x89PNG\r\n\x1a\n" + b"page" * 8


def episode(ident, order, short, title, locked=False, free=False, gold=0):
    return {"id": ident, "ord": order, "short_title": short, "title": title, "is_locked": locked,
            "is_in_free": free, "pay_mode": 1 if gold else 0, "pay_gold": gold}


DETAIL = {"id": 29839, "title": "A Book", "author_name": ["Writer", " Studio "], "evaluate": "About the book.",
          "vertical_cover": "https://i0.hdslb.com/cover.jpg", "styles": ["Fantasy"],
          "tags": [{"id": 1, "name": "Books"}, {"id": 2, "name": "Fantasy"}],
          "ep_list": [episode(30, 3, "03", "Third", locked=True, gold=79), episode(10, 1, "01", "First"),
                      episode(20, 2, "02", "Second"), episode(40, 4, "Extra", "Extra", locked=True, free=True, gold=79)]}


class Site:
    """The API as the engine asks for it: every request is recorded."""

    def __init__(self, refuse_first=0):
        self.asks = []
        self.fingerprints = 0
        self.refuse = refuse_first

    def fetch(self, url, *, method="GET", data=None, headers=None, binary=False, referer="", cookie=""):
        parts = urlsplit(url)
        self.asks.append((parts.path.rsplit("/", 1)[-1], json.loads(data) if data else None, cookie))
        if url == bilicomic.FINGERPRINT:
            self.fingerprints += 1
            return self.reply({"code": 0, "data": {"b_3": f"DEVICE-{self.fingerprints}-infoc", "b_4": "FOUR-4444"}})
        assert parts.hostname == "manga.bilibili.com" and method == "POST"
        query = parse_qs(parts.query)
        assert query["mobi_app"] == ["android_comic"] and query["platform"] == ["android"]
        assert abs(int(query["ts"][0]) - time.time()) < 60
        assert "Android" in headers["User-Agent"] and headers["Content-Type"] == "application/json"
        if not cookie.startswith("buvid3=DEVICE-"):
            return self.reply({"code": 99, "msg": "refused"})
        if self.refuse:
            self.refuse -= 1
            return self.reply({"code": 99, "msg": "refused"})
        name, payload = self.asks[-1][:2]
        if name == "ComicDetail":
            if payload != {"comicId": 29839}:
                return self.reply({"code": "invalid_argument", "msg": "no such comic"}, 400)
            return self.reply({"code": 0, "data": DETAIL})
        if name == "GetImageIndex":
            if payload["epId"] in (30, 40):
                return self.reply({"code": 1, "msg": "need buy episode", "data": {"images": []}})
            return self.reply({"code": 0, "data": {"images": [{"path": f"/bfs/manga/{payload['epId']}-{n}.jpg"}
                                                              for n in (1, 2, 3)]}})
        if name == "ImageToken":
            paths = json.loads(payload["urls"])  # a JSON list inside a string
            rows = [{"url": "https://manga.hdslb.com" + path, "token": "abc&ts=6b02b691",
                     "complete_url": f"https://manga.hdslb.com{path}?token=abc&ts=6b02b691"} for path in paths]
            rows[-1].pop("complete_url")
            return self.reply({"code": 0, "data": rows})
        raise AssertionError(url)

    @staticmethod
    def reply(body, status=200):
        return {"status": status, "content": json.dumps(body).encode()}


def engine_for(site, **options):
    options.setdefault("download", lambda url: PNG if url.split("?")[0].endswith("-2.jpg") else JPEG)
    return BiliComicEngine(site.fetch, **options)


def test_the_site_is_a_built_in_source_that_needs_no_browser():
    from external_scraper import ExternalScraper
    from noveldb.core.catalog.sites import downloadable_page
    from noveldb.core.external.job import needs_browser_start
    from noveldb.core.sources import detect_source

    for address in (BOOK, "https://manga.bilibili.com/mc29839", "https://manga.bilibili.com/mc29839/585738",
                    "https://manga.bilibili.com/m/detail/mc29839?from=x"):
        assert detect_source(address) == "bilicomic", address
    scraper = ExternalScraper()
    assert scraper._native_book_parser(BOOK) is not None
    assert not needs_browser_start(scraper, BOOK)
    assert downloadable_page(BOOK, ["native"])
    for other in ("https://manga.bilibili.com/", "https://manga.bilibili.com/ranking",
                  "https://www.bilibili.com/video/mc29839", "https://manga.bilibili.com.evil.test/detail/mc1"):
        assert not BiliComicEngine.knows(other), other


def test_the_book_lists_every_chapter_in_order_and_says_which_are_locked():
    site = Site()
    book = engine_for(site).parse_book("https://manga.bilibili.com/mc29839/20")
    assert book["bookUrl"] == BOOK and book["bookname"] == "A Book"
    assert book["author"] == "Writer, Studio" and book["introduction"] == "About the book."
    assert book["coverUrl"] == "https://i0.hdslb.com/cover.jpg" and book["tags"] == ["Fantasy", "Books"]
    assert [(c["name"], c["url"].rsplit("/", 1)[-1], c["number"]) for c in book["chapters"]] == [
        ("01 First", "10", 1), ("02 Second", "20", 2), ("03 Third", "30", 3), ("Extra", "40", 4)]
    # Bought-or-free chapters are open; a paid one the account has not bought is locked. One the site
    # is giving away for now is asked for, whatever its lock says.
    assert [(c["isVIP"], c["isPaid"], c["isAccessible"]) for c in book["chapters"]] == [
        (False, False, True), (False, False, True), (True, True, False), (False, False, True)]
    assert book["warnings"] and book["warnings"][0].startswith("1 of 4 chapters are locked")
    # The device cookie is asked for once and goes with the request (beside the profile's sign-in).
    assert [ask[0] for ask in site.asks] == ["spi", "ComicDetail"]
    assert site.asks[1][2] == "buvid3=DEVICE-1-infoc; buvid4=FOUR-4444"


def test_a_refused_device_cookie_is_replaced_once():
    site = Site(refuse_first=1)
    assert engine_for(site).parse_book(BOOK)["chapterCount"] == 4
    assert [ask[0] for ask in site.asks] == ["spi", "ComicDetail", "spi", "ComicDetail"]
    assert site.asks[-1][2].startswith("buvid3=DEVICE-2-")

    always = Site(refuse_first=5)
    with pytest.raises(RuntimeError, match="code 99"):
        engine_for(always).parse_book(BOOK)
    assert len(always.asks) == 4  # not asked over and over


def test_a_missing_comic_is_an_error_with_the_sites_words():
    with pytest.raises(RuntimeError, match="no such comic"):
        engine_for(Site()).parse_book("https://manga.bilibili.com/detail/mc5")


def test_a_chapters_pages_are_saved_as_it_is_read():
    site = Site()
    engine = engine_for(site)
    book = engine.parse_book(BOOK)
    chapter = engine.parse_chapter(book["chapters"][0], book)
    assert chapter["chapterName"] == "01 First" and chapter["comic"] and chapter["imageCount"] == 3
    assert chapter["imageReferer"] == "https://manga.bilibili.com/mc29839/10"
    urls = [image["url"] for image in chapter["images"]]
    assert urls == [f"https://manga.hdslb.com/bfs/manga/10-{n}.jpg?token=abc&ts=6b02b691" for n in (1, 2, 3)]
    assert [image["name"] for image in chapter["images"]] == ["page_0001.jpg", "page_0002.png", "page_0003.jpg"]
    # The pages are on disk for the saver (the addresses run out long before the book is built).
    paths = [image["data"][len("file:"):] for image in chapter["images"]]
    assert all(image["data"].startswith("file:") for image in chapter["images"])
    assert [open(path, "rb").read() for path in paths] == [JPEG, PNG, JPEG]
    assert chapter["contentHtml"].count("<img") == 3 and "token=abc&amp;ts=6b02b691" in chapter["contentHtml"]
    assert chapter["contentHtml"].index("10-1.jpg") < chapter["contentHtml"].index("10-3.jpg")

    from noveldb.core.external.pipeline import ExternalPipelineMixin

    assert ExternalPipelineMixin._decode_image_data_url(chapter["images"][1]["data"]) == PNG

    # The same book again keeps the pages; another book's start removes them.
    engine.parse_book(BOOK)
    assert os.path.exists(paths[0])
    engine.close()
    assert not os.path.exists(paths[0])


def test_a_locked_chapter_is_reported_as_locked_and_nothing_is_bought():
    site = Site()
    engine = engine_for(site)
    book = engine.parse_book(BOOK)
    site.asks.clear()
    with pytest.raises(RuntimeError) as locked:
        engine.parse_chapter(book["chapters"][2], book)
    assert locked.value.locked is True and site.asks == []  # the list already said so: not asked for
    # A chapter the list calls open, refused by the site all the same (bought state changed).
    with pytest.raises(RuntimeError) as refused:
        engine.parse_chapter(book["chapters"][3], book)
    assert refused.value.locked is True
    assert [ask[0] for ask in site.asks] == ["GetImageIndex"]
    assert not any("Buy" in ask[0] for ask in site.asks)


def test_a_page_that_fails_fails_the_chapter_and_leaves_no_files():
    site = Site()
    engine = engine_for(site, download=lambda url: None if "-3.jpg" in url else JPEG)
    book = engine.parse_book(BOOK)
    with pytest.raises(RuntimeError, match="page 3 of 3 could not be downloaded"):
        engine.parse_chapter(book["chapters"][0], book)
    assert os.listdir(engine._folder) == []
    not_image = engine_for(Site(), download=lambda url: b"<html>refused</html>")
    with pytest.raises(RuntimeError, match="is not an image"):
        not_image.parse_chapter(book["chapters"][0], book)


def test_stop_during_a_chapter_is_not_a_failure():
    site = Site()
    stopped = []
    engine = engine_for(site, stopped=lambda: bool(stopped), download=lambda url: stopped.append(1) or JPEG)
    book = engine.parse_book(BOOK)
    assert engine.parse_chapter(book["chapters"][0], book) is None


def test_the_catalog_reader_takes_the_addresses_without_saving_pages():
    site = Site()
    asked = []
    engine = engine_for(site, reading=lambda: True, download=asked.append)
    book = engine.parse_book(BOOK)
    chapter = engine.parse_chapter(book["chapters"][1], book)
    assert chapter["images"] == [] and chapter["contentHtml"].count("<img") == 3 and asked == []
    assert engine._folder is None


def test_the_phone_apps_python_page_reads_the_book_and_hands_over_page_addresses(monkeypatch):
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("noveldb_mobile_bilicomic", root / "scripts" / "python_host" / "noveldb_mobile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    site, sent = Site(), []

    def transport(request):
        sent.append(request)
        body = base64.b64decode(request["dataBase64"]).decode() if request.get("dataBase64") else None
        reply = site.fetch(request["url"], method=request["method"], data=body, headers=request["headers"],
                           cookie=request["headers"].get("Cookie", ""))
        return {"status": reply["status"], "finalUrl": request["url"], "responseHeaders": "content-type: application/json",
                "responseBase64": base64.b64encode(reply["content"]).decode()}

    monkeypatch.setattr(module, "_transport", transport)
    book = json.loads(module.parse_book("bilicomic", "https://manga.bilibili.com/mc29839/20"))
    assert book["bookname"] == "A Book" and book["pageUrl"] == BOOK and len(book["chapters"]) == 4
    # Locked chapters are the ones the apps treat as paid ("Skip paid", "refused").
    assert [c["isVIP"] for c in book["chapters"]] == [False, False, True, False]
    # The device cookie goes as the request's Cookie header (the apps join the web view's sign-in to it).
    assert all(r["headers"].get("Cookie", "").startswith("buvid3=DEVICE-") for r in sent if "/twirp/" in r["url"])

    def read(index):
        chapter = book["chapters"][index]
        return json.loads(module.parse_chapter("bilicomic", json.dumps(
            {"url": chapter["url"], "name": chapter["name"], "bookUrl": BOOK})))

    first = read(0)
    # Addresses only: the app fetches the pages itself (no files, no threads in the phones' Python).
    assert first["images"] == [] and first["comic"] is True and first["contentHtml"].count("<img") == 3
    assert "manga.hdslb.com/bfs/manga/10-1.jpg?token=abc&amp;ts=6b02b691" in first["contentHtml"]
    asked = len(sent)
    locked = read(2)
    assert locked["error"].startswith("Locked:") and len(sent) == asked  # the list said so: not asked for
    # The module is among those the apps load into the page.
    assert "'bilicomic.py'" in (root / "scripts" / "python_host" / "python-core.js").read_text(encoding="utf-8")
    assert '"bilicomic.py"' in (root / "scripts" / "fetch_python_host.py").read_text(encoding="utf-8")


def test_a_protected_page_is_decoded():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    image = JPEG + bytes(range(256)) * 100  # longer than the encrypted start
    key, iv = bytes(range(16)), bytes(range(16, 32))
    # The first 20480 bytes are encrypted with one block of padding (20496 bytes); the rest is plain.
    padded = image[:20480] + bytes([16]) * 16
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    body = encryptor.update(padded) + encryptor.finalize() + image[20480:]
    stored = b"\x01" + len(body).to_bytes(4, "big") + body + key
    cpx = base64.b64encode(bytes(60) + iv + bytes(8)).decode().rstrip("=")
    url = "https://manga.hdslb.com/bfs/manga/a.jpg?token=t&ts=6b02b691&cpx=" + cpx.replace("+", "%2B")
    assert bilicomic.decrypt_page(stored, url) == image
    assert bilicomic.decrypt_page(JPEG, url) == JPEG  # already an image
    assert bilicomic.decrypt_page(stored, url.split("&cpx")[0]) == stored  # not a protected address
    with pytest.raises(RuntimeError, match="unknown layout"):
        bilicomic.decrypt_page(b"\x02" + stored[1:], url)


def test_a_cached_chapter_whose_page_addresses_ran_out_is_read_again():
    from noveldb.core.external.pipeline import ExternalPipelineMixin

    expired = ExternalPipelineMixin._cached_image_addresses_expired
    now = 0x6b02b691 - 300
    address = "https://manga.hdslb.com/bfs/manga/a.jpg?token=abc&amp;ts=6b02b691"
    # Fifteen minutes is less than the rest of a download takes: never reused, however fresh.
    assert expired({"images": [{"url": address}]}, now=now - 600)
    assert expired({"images": [{"url": address}]}, now=now + 86400)
    assert not expired({"images": [{"url": address, "data": "data:image/jpeg;base64,AAAA"}]}, now=now + 86400)
    # "ts" without a token is some other site's parameter.
    assert not expired({"images": [{"url": "https://cdn.test/a.jpg?ts=6b02b691"}]}, now=now + 86400)


def test_engine_requests_send_the_extra_cookie_with_the_profiles_cookies():
    from external_scraper import ExternalScraper

    seen = {}

    class Reply:
        status_code, reason, headers, url, content, text = 200, "OK", {}, "https://manga.bilibili.com/x", b"{}", "{}"

    class Session:
        def request(self, method, url, headers=None, data=None, **options):
            seen.update(method=method, cookie=headers.get("Cookie"), data=data)
            return Reply()

    scraper = ExternalScraper()
    scraper._gm_session = lambda: Session()
    scraper._storage_cookies_for_url = lambda url: [{"name": "SESSDATA", "value": "signed-in"},
                                                    {"name": "buvid3", "value": "the-browser's"}]
    result = scraper.engine_fetch("https://manga.bilibili.com/x", method="POST", data="{}", cookie="buvid3=engine")
    assert result["status"] == 200
    assert seen["method"] == "POST" and seen["cookie"] == "buvid3=engine; SESSDATA=signed-in"
