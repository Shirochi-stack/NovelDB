import queue
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
