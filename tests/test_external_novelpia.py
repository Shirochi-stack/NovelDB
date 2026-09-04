import json

import pytest

from external_scraper import ExternalScraper


def make_scraper():
    messages = []
    return ExternalScraper(logger=messages.append), messages


def test_novelpia_url_detection_is_scoped_to_supported_pages():
    assert ExternalScraper.is_novelpia(
        "https://novelpia.com/novel/346898"
    )
    assert ExternalScraper.is_novelpia(
        "https://www.novelpia.com/viewer/4459905?x=1"
    )
    assert not ExternalScraper.is_novelpia(
        "https://novelpia.com/search"
    )
    assert not ExternalScraper.is_novelpia(
        "https://example.com/novel/346898"
    )


def test_episode_html_is_converted_and_deduplicated():
    source = """
    <tr class="ep_style5">
      <td><i id="bookmark_101"></i>First chapter</b></td>
    </tr>
    <tr class="ep_style5 b_plus">
      <td><i id="bookmark_102"></i><span>Second</span> chapter</b></td>
    </tr>
    <tr><td><i id="bookmark_101"></i>Duplicate</b></td></tr>
    """

    chapters = ExternalScraper._novelpia_parse_episode_html(source)

    assert [chapter["id"] for chapter in chapters] == ["101", "102"]
    assert chapters[0]["name"] == "First chapter"
    assert chapters[0]["isVIP"] is False
    assert chapters[1]["name"] == "Second chapter"
    assert chapters[1]["isVIP"] is True
    assert chapters[1]["isAccessible"] is True


@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('include_notices', [False, True])
def test_novelpia_book_includes_author_notices_oldest_first(
    complete, include_notices,
):
    class Page:
        def goto(self, *_args, **_kwargs):
            return None

        def content(self):
            badge = '<span class="b_comp s_inv">완결</span>' if complete else ''
            return (
                '<option>완결까지 파이팅</option>'
                '<div class="epnew-novel-info"><p class="in-badge">'
                f'<span class="b_plus">PLUS</span>{badge}</p></div>'
            ) + """
                <table class="notice_table">
                  <tr><td onclick="location='/viewer/202'"><b>Notice 2</b></td></tr>
                  <tr><td onclick="location='/viewer/201'"><b>Notice 1</b></td></tr>
                </table>
            """

        def evaluate(self, script, args=None):
            if '/proc/episode_list' not in script:
                return {
                    "title": "Example",
                    "author": "Author",
                    "introduction": "Summary",
                    "tags": [],
                    "cover": "",
                }
            if args["pageNo"] == 0:
                return {
                    "status": 200,
                    "text": (
                        '<tr><td><i id="bookmark_301"></i>'
                        'Chapter 1</b></td></tr>'
                    ),
                }
            return {"status": 200, "text": ""}

    scraper, messages = make_scraper()
    scraper._page = Page()
    scraper._start_novelpia_browser = lambda _url: True
    scraper.novelpia_include_notices = include_notices

    book = scraper._novelpia_parse_book("https://novelpia.com/novel/123")

    assert book["chapterCount"] == 1
    assert book["status"] == ('Completed' if complete else 'Ongoing')
    if not include_notices:
        assert book['noticeCount'] == 0
        assert [chapter['id'] for chapter in book['chapters']] == ['301']
        return
    assert book["noticeCount"] == 2
    assert [chapter["id"] for chapter in book["chapters"]] == [
        "201",
        "202",
        "301",
    ]
    assert [chapter.get("isNotice", False) for chapter in book["chapters"]] == [
        True,
        True,
        False,
    ]
    assert book["chapters"][2]["_novelpiaChapterNumber"] == 1
    assert any("2 author notice(s)" in message for message in messages)


def test_valid_viewer_json_becomes_external_chapter_data():
    scraper, messages = make_scraper()
    payload = json.dumps(
        {
            "s": [
                {"text": "<p>Hello<br>world</p>"},
                {
                    "text": (
                        '<p><img src="//images.novelpia.com/imagebox/'
                        'sample.file"></p>'
                    )
                },
            ]
        }
    )

    result = scraper._novelpia_build_chapter_result(
        payload,
        "Chapter 1",
        "https://novelpia.com/viewer/101",
    )

    assert result is not None
    assert result["chapterName"] == "Chapter 1"
    assert "Hello" in result["contentHtml"]
    assert "world" in result["contentText"]
    assert result["images"][0]["url"].startswith(
        "https://images.novelpia.com/"
    )
    assert not messages


def test_status_500_viewer_json_is_rejected_not_embedded():
    scraper, messages = make_scraper()
    payload = json.dumps(
        {
            "status": 500,
            "code": 500,
            "errmsg": "잘못된 접근입니다.",
        },
        ensure_ascii=False,
    )

    result = scraper._novelpia_build_chapter_result(
        payload,
        "Broken chapter",
        "https://novelpia.com/viewer/102",
    )

    assert result is None
    assert any("Viewer rejected" in message for message in messages)


def test_string_code_500_is_also_rejected():
    scraper, messages = make_scraper()
    payload = json.dumps(
        {
            "code": "500",
            "errmsg": "rejected",
        }
    )

    result = scraper._novelpia_build_chapter_result(
        payload,
        "Broken chapter",
        "https://novelpia.com/viewer/103",
    )

    assert result is None
    assert any("Viewer rejected" in message for message in messages)


def test_novelpia_batch_is_sequential_and_does_not_retry():
    scraper, _messages = make_scraper()
    scraper._book_data = {"_novelpia": True}
    scraper._page = object()
    calls = []

    def fake_parse(url, name, page=None):
        calls.append((url, name, page))
        if url.endswith("/2"):
            return None
        return {"chapterName": name, "contentHtml": "<p>ok</p>"}

    scraper._novelpia_parse_chapter = fake_parse
    chapters = [
        {"url": "https://novelpia.com/viewer/1", "name": "One"},
        {"url": "https://novelpia.com/viewer/2", "name": "Two"},
    ]

    completed = []
    results = scraper.parse_chapter_batch(
        chapters,
        interval=0,
        success_callback=lambda index, data: completed.append(
            (index, data["chapterName"])
        ),
    )

    assert len(calls) == 2
    assert results[0]["chapterName"] == "One"
    assert results[1] is None
    assert completed == [(0, "One")]
