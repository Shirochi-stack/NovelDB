import hashlib

import requests

from external_scraper import ExternalScraper, SHUBA_API_KEY


def make_scraper():
    messages = []
    return ExternalScraper(logger=messages.append), messages


def test_69shuba_url_detection_accepts_supported_pages_only():
    assert ExternalScraper.is_69shuba(
        "https://69shuba.tw/book/380314/"
    )
    assert ExternalScraper.is_69shuba(
        "https://69shuba.tw/indexlist/380314"
    )
    assert ExternalScraper.is_69shuba(
        "https://www.69shuba.tw/read/380314/71876"
    )
    assert not ExternalScraper.is_69shuba("https://69shuba.tw/")
    assert not ExternalScraper.is_69shuba(
        "https://example.com/indexlist/380314"
    )


def test_69shuba_api_request_signs_the_official_client_payload(monkeypatch):
    scraper, _messages = make_scraper()
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(url, data, headers, timeout):
        captured.update({
            "url": url,
            "data": data,
            "headers": headers,
            "timeout": timeout,
        })
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("external_scraper.time.time", lambda: 1_700_000_000)

    result = scraper._69shuba_api_request(
        "api_indexlist.php", {"aid": "380314"}
    )

    assert result == {"ok": True}
    assert captured["url"].endswith("/json/api_indexlist.php")
    assert captured["data"]["aid"] == "380314"
    assert captured["data"]["lang"] == "ft"
    assert captured["data"]["token"] == hashlib.md5(
        f"{SHUBA_API_KEY}1700000000".encode("utf-8")
    ).hexdigest()


def test_69shuba_index_url_parses_book_and_catalog(monkeypatch):
    scraper, messages = make_scraper()
    responses = {
        "api_info.php": {
            "articlename": "Test & Novel",
            "author": "Author",
            "intro_des": "A synopsis",
            "img_url": "//p.69shuba.tw/cover.jpg",
            "keywords": "tag one|tag two",
            "sortname": "Fantasy",
            "isfull": "Ongoing",
        },
        "api_indexlist.php": {
            "list": [
                {
                    "chapterid": 11,
                    "chaptername": "Chapter 1",
                    "chapter_url": "/read/380314/11",
                },
                {
                    "chapterid": 12,
                    "chaptername": "Chapter 2",
                    "chapter_url": "/read/380314/12",
                },
            ]
        },
    }
    monkeypatch.setattr(
        scraper,
        "_69shuba_api_request",
        lambda endpoint, params=None, timeout=30: responses[endpoint],
    )

    book = scraper.parse_book("https://69shuba.tw/indexlist/380314")

    assert book["bookname"] == "Test & Novel"
    assert book["author"] == "Author"
    assert book["chapterCount"] == 2
    assert book["chapters"][0]["url"] == (
        "https://69shuba.tw/read/380314/11"
    )
    assert book["chapters"][1]["_chapterId"] == "12"
    assert book["coverUrl"] == "https://p.69shuba.tw/cover.jpg"
    assert book["tags"] == ["tag one", "tag two"]
    assert book["_69shuba"] is True
    assert any("2 chapters" in message for message in messages)


def test_69shuba_chapter_payload_is_html_escaped():
    result = ExternalScraper._69shuba_build_chapter_result(
        {
            "chaptername": "Chapter 1",
            "paragraphs": ["First <line>", "Second & final"],
            "contentMissing": False,
        },
        "Source name",
    )

    assert result["chapterName"] == "Chapter 1"
    assert result["sourceChapterName"] == "Source name"
    assert result["contentText"] == "First <line>\nSecond & final"
    assert "First &lt;line&gt;" in result["contentHtml"]
    assert "Second &amp; final" in result["contentHtml"]
    assert result["images"] == []


def test_69shuba_missing_chapter_is_rejected():
    assert ExternalScraper._69shuba_build_chapter_result(
        {"content": "", "contentMissing": True},
        "Missing",
    ) is None
