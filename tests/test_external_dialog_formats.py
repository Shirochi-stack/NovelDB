import json
import queue
import zipfile
from types import SimpleNamespace

from downloader_core import DownloaderCore
from external_dialog import ExternalNovelDialog
from external_scraper import ExternalScraper


class Setting:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def make_dialog(formats, *, long_image_layout=False, has_long_images=False):
    selected = set(formats)
    calls = []
    logs = []
    dialog = SimpleNamespace(
        _book_data={"bookname": "Example", "author": "Author"},
        _var_format=Setting("epub"),
        _var_format_epub=Setting("epub" in selected),
        _var_format_txt=Setting("txt" in selected),
        _var_format_pdf=Setting("pdf" in selected),
        _var_format_cbz=Setting("cbz" in selected),
        _var_long_image_layout=Setting(long_image_layout),
        _log=logs.append,
        _has_long_image_chapters=lambda: has_long_images,
        _generate_epub=lambda title, author: calls.append(("epub", title, author)),
        _generate_txt=lambda title, author: calls.append(("txt", title, author)),
        _generate_pdf=lambda title, author: calls.append(("pdf", title, author)),
        _generate_image_archive=lambda title, author: calls.append(
            ("cbz", title, author)
        ),
    )
    dialog._selected_output_formats = lambda: (
        ExternalNovelDialog._selected_output_formats(dialog)
    )
    return dialog, calls, logs


def test_external_formats_are_returned_in_stable_ui_order():
    dialog, _calls, _logs = make_dialog(["cbz", "txt", "epub"])

    assert ExternalNovelDialog._selected_output_formats(dialog) == [
        "epub",
        "txt",
        "cbz",
    ]


def test_external_output_generates_every_selected_format_once():
    dialog, calls, logs = make_dialog(["epub", "txt", "pdf", "cbz"])

    ExternalNovelDialog._generate_output(dialog)

    assert [call[0] for call in calls] == ["epub", "txt", "pdf", "cbz"]
    assert all(call[1:] == ("Example", "Author") for call in calls)
    assert any("EPUB, TXT, PDF, CBZ" in message for message in logs)


def test_external_output_uses_selection_snapshotted_at_download_start():
    dialog, calls, _logs = make_dialog(["epub"])
    dialog._active_output_formats = ["pdf", "cbz"]

    ExternalNovelDialog._generate_output(dialog)

    assert [call[0] for call in calls] == ["pdf", "cbz"]


def test_long_image_mode_preserves_legacy_epub_to_cbz_conversion():
    dialog, calls, _logs = make_dialog(
        ["epub"], long_image_layout=True, has_long_images=True
    )

    ExternalNovelDialog._generate_output(dialog)

    assert [call[0] for call in calls] == ["cbz"]


def test_explicit_epub_and_cbz_selection_generates_both_in_long_image_mode():
    dialog, calls, _logs = make_dialog(
        ["epub", "cbz"], long_image_layout=True, has_long_images=True
    )

    ExternalNovelDialog._generate_output(dialog)

    assert [call[0] for call in calls] == ["epub", "cbz"]


def test_external_retry_passes_use_main_gui_setting():
    dialog = SimpleNamespace(_retry_variable=Setting(10))

    assert ExternalNovelDialog._get_retry_passes(dialog) == 10


def test_external_retry_passes_have_safe_minimum_and_default():
    assert ExternalNovelDialog._get_retry_passes(
        SimpleNamespace(_retry_variable=Setting(0))
    ) == 1
    assert ExternalNovelDialog._get_retry_passes(
        SimpleNamespace(_retry_variable=None)
    ) == 5


def test_external_interval_range_normalizes_like_main_downloader():
    assert ExternalScraper._normalize_interval_range(2.0, 0.5) == (0.5, 2.0)
    assert ExternalScraper._normalize_interval_range(-4, 500) == (0.0, 300.0)
    assert ExternalScraper._normalize_interval_range(1.25) == (1.25, 1.25)


def test_external_interval_ui_keeps_legacy_single_value_fixed():
    legacy_dialog = SimpleNamespace(_var_interval=Setting(1.25))
    range_dialog = SimpleNamespace(
        _var_interval=Setting(2.0),
        _var_interval_max=Setting(0.5),
    )

    assert ExternalNovelDialog._get_interval_range(legacy_dialog) == (
        1.25,
        1.25,
    )
    assert ExternalNovelDialog._get_interval_range(range_dialog) == (0.5, 2.0)


def test_external_config_migrates_legacy_interval_to_fixed_range(
    monkeypatch, tmp_path
):
    (tmp_path / 'config.json').write_text(
        json.dumps({'ext_interval': 1.75}),
        encoding='utf-8',
    )
    monkeypatch.setattr(
        'external_dialog._get_base_dir', lambda: str(tmp_path)
    )
    setting_names = (
        '_url_var', '_var_format_epub', '_var_format_txt',
        '_var_format_pdf', '_var_format_cbz', '_var_format',
        '_var_ext_threads', '_var_ext_image_workers', '_var_interval',
        '_var_interval_max', '_var_from_enabled', '_var_to_enabled',
        '_var_from', '_var_to', '_var_skip_paid', '_var_regular_browser',
        '_var_ntk_novelpia_cover', '_var_syosetu_amazon_cover',
        '_var_long_image_layout', '_var_kakao_skip_last_page',
        '_var_kakao_keep_filler', '_var_kakao_dedupe_images',
        '_var_kakao_dedupe_leading_images',
    )
    dialog = SimpleNamespace(**{
        name: Setting(None) for name in setting_names
    })
    dialog._spn_kakao_dedupe_leading = SimpleNamespace(
        configure=lambda **_kwargs: None
    )

    ExternalNovelDialog._load_ext_config(dialog)

    assert dialog._var_interval.get() == 1.75
    assert dialog._var_interval_max.get() == 1.75


def test_external_download_forwards_range_and_randomizes_each_batch(
    monkeypatch,
):
    calls = []
    sleeps = []

    class Scraper:
        _context = True

        @staticmethod
        def parse_chapter_batch(chapters, interval=0,
                                interval_max=None):
            calls.append((len(chapters), interval, interval_max))
            return [
                {'chapterName': chapter['name']}
                for chapter in chapters
            ]

    monkeypatch.setattr(
        'external_scraper.random.uniform',
        lambda minimum, maximum: maximum,
    )
    dialog = SimpleNamespace(
        _scraper=Scraper(),
        _book_data={},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=lambda seconds: sleeps.append(seconds) or True,
        _log=lambda _message: None,
    )

    ExternalNovelDialog._do_download(
        dialog,
        [
            {'name': 'Chapter 1'},
            {'name': 'Chapter 2'},
            {'name': 'Chapter 3'},
        ],
        0,
        3,
        0.25,
        num_threads=2,
        interval_max=0.75,
    )

    assert calls == [(2, 0.25, 0.75), (1, 0.25, 0.75)]
    assert sleeps == [0.75]


def test_external_novelpia_notices_follow_main_download_setting():
    scraper = SimpleNamespace()
    dialog = SimpleNamespace(
        _scraper=scraper,
        _parent_gui=SimpleNamespace(var_include_notices=Setting(False)),
        _var_kakao_skip_last_page=Setting(False),
        _var_kakao_keep_filler=Setting(False),
        _var_ntk_novelpia_cover=Setting(False),
        _var_syosetu_amazon_cover=Setting(False),
    )

    ExternalNovelDialog._apply_scraper_options(dialog)

    assert scraper.novelpia_include_notices is False


def test_external_download_runs_configured_number_of_retry_passes():
    logs = []

    class Scraper:
        _context = True
        retry_calls = 0

        @staticmethod
        def parse_chapter_batch(_chapters, interval=0):
            return [None]

        def parse_chapter(self, _index, _chapter, interval=0,
                          log_errors=True):
            self.retry_calls += 1
            return None

    scraper = Scraper()
    dialog = SimpleNamespace(
        _scraper=scraper,
        _book_data={},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=lambda _seconds: True,
        _log=logs.append,
    )

    ExternalNovelDialog._do_download(
        dialog,
        [{"name": "Failed chapter"}],
        0,
        1,
        0,
        retry_passes=3,
    )

    assert scraper.retry_calls == 3
    assert any("Failed to fetch, retry 3/3" in message for message in logs)
    assert not any("Retry pass" in message for message in logs)


def test_external_retry_happens_before_the_next_batch():
    events = []

    class Scraper:
        _context = True

        @staticmethod
        def parse_chapter_batch(chapters, interval=0):
            name = chapters[0]["name"]
            events.append(f"batch:{name}")
            if name == "First":
                return [None]
            return [{"chapterName": name}]

        @staticmethod
        def parse_chapter(_index, chapter, interval=0, log_errors=True):
            events.append(f"retry:{chapter['name']}")
            return {"chapterName": chapter["name"]}

    dialog = SimpleNamespace(
        _scraper=Scraper(),
        _book_data={},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=lambda _seconds: True,
        _log=lambda _message: None,
    )

    ExternalNovelDialog._do_download(
        dialog,
        [{"name": "First"}, {"name": "Second"}],
        0,
        2,
        0,
        num_threads=1,
        retry_passes=3,
    )

    assert events == ["batch:First", "retry:First", "batch:Second"]


def test_external_download_preserves_selected_range_numbers():
    class Scraper:
        _context = True

        @staticmethod
        def parse_chapter_batch(chapters, interval=0):
            return [
                {"chapterName": chapter["name"]}
                for chapter in chapters
            ]

    dialog = SimpleNamespace(
        _scraper=Scraper(),
        _book_data={},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=lambda _seconds: True,
        _log=lambda _message: None,
    )
    chapters = [{"name": f"Chapter {number}"} for number in range(1, 8)]

    ExternalNovelDialog._do_download(
        dialog, chapters, 4, 7, 0, num_threads=3
    )

    assert dialog._chapter_number_start == 5
    assert [
        result["_chapter_number"] for result in dialog._chapter_results
    ] == [5, 6, 7]


def test_novelpia_range_prepends_notices_without_shifting_chapter_numbers():
    calls = []

    class Scraper:
        _context = True

        @staticmethod
        def parse_chapter_batch(
            chapters, interval=0, success_callback=None
        ):
            calls.extend(chapter["name"] for chapter in chapters)
            results = [
                {"chapterName": chapter["name"]}
                for chapter in chapters
            ]
            for index, result in enumerate(results):
                success_callback(index, result)
            return results

    dialog = SimpleNamespace(
        _scraper=Scraper(),
        _book_data={"_novelpia": True},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=lambda _seconds: True,
        _log=lambda _message: None,
    )
    chapters = [
        {
            "name": "Notice 1",
            "isNotice": True,
            "_novelpiaNoticeNumber": 1,
        },
        {
            "name": "Notice 2",
            "isNotice": True,
            "_novelpiaNoticeNumber": 2,
        },
        {"name": "Chapter 1", "_novelpiaChapterNumber": 1},
        {"name": "Chapter 2", "_novelpiaChapterNumber": 2},
        {"name": "Chapter 3", "_novelpiaChapterNumber": 3},
    ]

    ExternalNovelDialog._do_download(
        dialog, chapters, 1, 3, 0, num_threads=8
    )

    assert calls == ["Notice 1", "Notice 2", "Chapter 2", "Chapter 3"]
    assert [result["_is_notice"] for result in dialog._chapter_results] == [
        True,
        True,
        False,
        False,
    ]
    assert [
        result["_chapter_number"] for result in dialog._chapter_results[2:]
    ] == [2, 3]


def test_external_result_number_prefers_preserved_source_position():
    dialog = SimpleNamespace(_chapter_number_start=20)

    assert ExternalNovelDialog._result_chapter_number(
        dialog, 2, {"_chapter_number": 57}
    ) == 57
    assert ExternalNovelDialog._result_chapter_number(dialog, 2, {}) == 22


def test_external_epub_filename_uses_preserved_range_number(tmp_path):
    dialog = object.__new__(ExternalNovelDialog)
    dialog._book_data = {"bookname": "Range Test", "author": "Author"}
    dialog._chapter_results = [{
        "chapterName": "Chapter 25",
        "contentHtml": "<p>Content</p>",
        "_chapter_number": 25,
    }]
    dialog._parent_gui = SimpleNamespace()
    dialog._scraper = None
    dialog._var_long_image_layout = Setting(False)
    dialog._var_kakao_dedupe_images = Setting(False)
    dialog._var_ext_image_workers = Setting(1)
    dialog._get_output_dir = lambda: str(tmp_path)
    dialog._log = lambda _message: None
    dialog._generate_txt = lambda *_args: None

    dialog._generate_epub("Range Test", "Author")

    with zipfile.ZipFile(tmp_path / "Range Test.epub") as archive:
        archive_names = archive.namelist()

    assert "OEBPS/Text/chapter0025.xhtml" in archive_names
    assert "OEBPS/Text/chapter0001.xhtml" not in archive_names


def test_external_epub_uses_separate_notice_filenames(tmp_path):
    dialog = object.__new__(ExternalNovelDialog)
    dialog._book_data = {"bookname": "Notice Test", "author": "Author"}
    dialog._chapter_results = [
        {
            "chapterName": "Notice 1",
            "contentHtml": "<p>Notice</p>",
            "_chapter_number": 1,
            "_is_notice": True,
        },
        {
            "chapterName": "Chapter 25",
            "contentHtml": "<p>Content</p>",
            "_chapter_number": 25,
            "_is_notice": False,
        },
    ]
    dialog._parent_gui = SimpleNamespace()
    dialog._scraper = None
    dialog._var_long_image_layout = Setting(False)
    dialog._var_kakao_dedupe_images = Setting(False)
    dialog._var_ext_image_workers = Setting(1)
    dialog._get_output_dir = lambda: str(tmp_path)
    dialog._log = lambda _message: None
    dialog._generate_txt = lambda *_args: None

    dialog._generate_epub("Notice Test", "Author")

    with zipfile.ZipFile(tmp_path / "Notice Test.epub") as archive:
        archive_names = archive.namelist()

    assert "OEBPS/Text/chapter_notice0001.xhtml" in archive_names
    assert "OEBPS/Text/chapter0025.xhtml" in archive_names


def test_external_scraper_range_results_retain_source_numbers():
    scraper = SimpleNamespace(
        _stop_requested=False,
        log=lambda _message: None,
        parse_chapter=lambda index, chapter, interval=0: {
            "chapterName": chapter["name"],
            "parsedIndex": index,
        },
    )
    chapters = [{"name": f"Chapter {number}"} for number in range(1, 8)]

    results = ExternalScraper.parse_all_chapters(
        scraper, chapters, interval=0, start_idx=4, end_idx=7
    )

    assert [result["_chapter_number"] for result in results] == [5, 6, 7]
    assert [result["parsedIndex"] for result in results] == [4, 5, 6]


def test_ntk_progress_is_logged_only_after_successful_content_fetch():
    logs = []
    events = []

    class Scraper:
        _context = True

        @staticmethod
        def parse_chapter_batch(chapters, interval=0, success_callback=None):
            assert not logs
            results = []
            for index, chapter in enumerate(chapters):
                events.append(chapter["name"])
                result = {
                    "chapterName": chapter["name"],
                    "contentText": "content",
                }
                results.append(result)
                success_callback(index, result)
                assert logs[-1].endswith(chapter["name"])
            return results

    dialog = SimpleNamespace(
        _scraper=Scraper(),
        _book_data={"_ntk_novel": True},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=lambda _seconds: True,
        _log=logs.append,
    )

    ExternalNovelDialog._do_download(
        dialog,
        [{"name": "Chapter 1"}, {"name": "Chapter 2"}],
        0,
        2,
        0,
        num_threads=2,
    )

    assert events == ["Chapter 1", "Chapter 2"]
    assert logs == ["  [1/2] Chapter 1", "  [2/2] Chapter 2"]


def test_native_progress_briefly_buffers_completions_into_chapter_order():
    logs = []

    class Scraper:
        _context = True

        @staticmethod
        def parse_chapter_batch(
            chapters, interval=0, success_callback=None
        ):
            results = [
                {"chapterName": "Viewer One", "contentText": "content"},
                {"chapterName": "Viewer Two", "contentText": "content"},
            ]
            success_callback(1, results[1])
            success_callback(0, results[0])
            return results

    dialog = SimpleNamespace(
        _scraper=Scraper(),
        _book_data={"_global_novelpia": True},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=lambda _seconds: True,
        _log=logs.append,
    )

    ExternalNovelDialog._do_download(
        dialog,
        [{"name": "Queued One"}, {"name": "Queued Two"}],
        0,
        2,
        0,
        num_threads=2,
    )

    assert logs == ["  [1/2] Viewer One", "  [2/2] Viewer Two"]
    assert not any("Queued" in message or "OK:" in message for message in logs)


def test_external_pdf_embeds_ntk_cover_and_remote_chapter_images(
    monkeypatch, tmp_path
):
    cover_bytes = b"\xff\xd8\xff\xe0pdf-cover"
    figure_bytes = b"\x89PNG\r\n\x1a\npdf-figure"
    captured = {}
    fetched = []
    logs = []

    class Scraper:
        @staticmethod
        def fetch_ntk_binary(url, referer=None):
            fetched.append((url, referer))
            if url.endswith("cover.jpg"):
                return cover_bytes
            if url.endswith("figure.png"):
                return figure_bytes
            return None

    def capture_pdf(
        _self, metadata, output_path, chapters, css, **kwargs
    ):
        captured.update({
            "metadata": metadata,
            "output_path": output_path,
            "chapters": chapters,
            "css": css,
            **kwargs,
        })

    monkeypatch.setattr(DownloaderCore, "generate_pdf", capture_pdf)

    parent_gui = SimpleNamespace()
    dialog = SimpleNamespace(
        _book_data={
            "bookname": "Example",
            "author": "Author",
            "bookUrl": "https://sbxh9.com/novel/58410",
            "coverUrl": "https://aws-cdn9.site/cover.jpg",
            "introductionHTML": "<p>Introduction</p>",
            "_ntk_novel": True,
        },
        _chapter_results=[{
            "chapterName": "Episode 1",
            "chapterUrl": "https://sbxh9.com/novel/58410/100001",
            "contentHtml": (
                '<p>Text</p><img data-src-address="figure.png" '
                'alt="figure.png">'
            ),
            "images": [{
                "name": "figure.png",
                "url": "https://aws-cdn9.site/figure.png",
            }],
        }],
        _scraper=Scraper(),
        _parent_gui=parent_gui,
        _get_output_dir=lambda: str(tmp_path),
        _download_image_python=lambda *_args, **_kwargs: None,
        _copy_browser_cookies_to_session=lambda _session: None,
        _kakao_extra_css=lambda: '',
        _generate_txt=lambda *_args: None,
        _log=logs.append,
    )
    dialog._image_ext_from_bytes = lambda raw, default="jpg": (
        ExternalNovelDialog._image_ext_from_bytes(dialog, raw, default)
    )
    dialog._fix_nd_img_tags = lambda html_str, rename_map=None: (
        ExternalNovelDialog._fix_nd_img_tags(dialog, html_str, rename_map)
    )

    ExternalNovelDialog._generate_pdf(dialog, "Example", "Author")

    assert captured["cover_image"] == {
        "filename": "cover.jpg",
        "data": cover_bytes,
    }
    assert captured["image_map"] == {"figure.png": figure_bytes}
    assert 'src="../Images/figure.png"' in captured["chapters"][0]["html"]
    assert captured["info_html"] == "<p>Introduction</p>"
    assert fetched == [
        (
            "https://aws-cdn9.site/cover.jpg",
            "https://sbxh9.com/novel/58410",
        ),
        (
            "https://aws-cdn9.site/figure.png",
            "https://sbxh9.com/novel/58410/100001",
        ),
    ]
    assert any("PDF cover: OK" in line for line in logs)


def test_failed_fetch_console_stack_is_suppressed():
    logs = []
    scraper = SimpleNamespace(log=logs.append)
    message = SimpleNamespace(
        text=(
            "TypeError: Failed to fetch\n"
            "    at tryFetch (...)\n"
            "    at window.fetch (...)"
        ),
        type="error",
    )

    ExternalScraper._on_console(scraper, message)

    assert logs == []
