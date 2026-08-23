import json
import zipfile
from types import SimpleNamespace

from downloader_core import DownloaderCore, _without_tesseract_dll_directories
from epub_generator import EpubGenerator
from gui import NovelpiaGUI, extract_chapter_content_and_images


def render_segments(segments, **options):
    payload = json.dumps({"s": [{"text": text} for text in segments]})
    chapter_html, images, failures = extract_chapter_content_and_images(
        payload,
        font_mapper=None,
        session=None,
        compress_images=False,
        jpeg_quality=80,
        image_format="WEBP",
        logger=lambda _message: None,
        next_image_no=lambda: 1,
        **options,
    )
    assert images == []
    assert failures == 0
    return chapter_html


def test_newlines_and_br_are_preserved_by_default():
    chapter_html = render_segments(
        ["<p>First<br>Second\n\nThird</p>", "\n"],
    )

    assert chapter_html == (
        "<p>First<br/>Second<br/><br/>Third</p>"
        "<p><br/></p>"
    )


def test_remove_newlines_restores_compact_behavior():
    chapter_html = render_segments(
        ["<p>First<br>Second\n\nThird</p>", "\n"],
        remove_newlines=True,
    )

    assert chapter_html == "<p>FirstSecondThird</p>"


def test_remove_leading_spaces_does_not_remove_line_breaks():
    chapter_html = render_segments(
        [" \t\nText"],
        strip_leading_spaces=True,
    )

    assert chapter_html == "<p><br/>Text</p>"


class Setting:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def make_fingerprint_settings(remove_newlines, save_image_urls_only=False):
    return SimpleNamespace(
        var_compress_images=Setting(False),
        var_save_image_urls_only=Setting(save_image_urls_only),
        var_jpeg_quality=Setting(80),
        var_image_format=Setting("WEBP"),
        var_convert_gifs=Setting(False),
        var_static_only=Setting(False),
        var_strip_leading_spaces=Setting(False),
        var_remove_newlines=Setting(remove_newlines),
    )


def test_newline_setting_invalidates_processed_chapter_cache():
    keep_newlines = NovelpiaGUI._image_cache_fingerprint(
        make_fingerprint_settings(False)
    )
    remove_newlines = NovelpiaGUI._image_cache_fingerprint(
        make_fingerprint_settings(True)
    )

    assert keep_newlines != remove_newlines


def test_image_url_mode_keeps_remote_previews_without_network_requests():
    payload = json.dumps(
        {
            "s": [
                {
                    "text": (
                        '<p><img src="//images.novelpia.com/imagebox/'
                        'sample.file?size=large&amp;page=1"></p>'
                    )
                },
                {
                    "text": (
                        "<img src='https://cdn.example.com/second.webp'>"
                    )
                },
            ]
        }
    )

    class NoNetworkSession:
        def get(self, *_args, **_kwargs):
            raise AssertionError("URL-only mode must not request image bytes")

    chapter_html, images, failures = extract_chapter_content_and_images(
        payload,
        font_mapper=None,
        session=NoNetworkSession(),
        compress_images=True,
        jpeg_quality=80,
        image_format="WEBP",
        logger=lambda _message: None,
        next_image_no=lambda: 1,
        save_image_urls_only=True,
    )

    assert images == []
    assert failures == 0
    assert chapter_html.count('class="remote-image"') == 2
    assert 'width="100%"' not in chapter_html
    assert (
        'src="https://images.novelpia.com/imagebox/'
        'sample.file?size=large&amp;page=1"'
    ) in chapter_html
    assert 'src="https://cdn.example.com/second.webp"' in chapter_html


def test_image_url_mode_changes_processed_cache_fingerprint():
    downloaded_images = NovelpiaGUI._image_cache_fingerprint(
        make_fingerprint_settings(False, False)
    )
    remote_images = NovelpiaGUI._image_cache_fingerprint(
        make_fingerprint_settings(False, True)
    )

    assert downloaded_images != remote_images


def test_image_url_mode_disables_and_restores_image_controls():
    class Widget:
        def __init__(self):
            self.state = None

        def configure(self, *, state):
            self.state = state

    normal_widget = Widget()
    readonly_widget = Widget()
    settings = SimpleNamespace(
        var_save_image_urls_only=Setting(True),
        _image_url_disabled_widgets=[
            (normal_widget, "normal"),
            (readonly_widget, "readonly"),
        ],
    )

    NovelpiaGUI._sync_image_url_option_states(settings)
    assert normal_widget.state == "disabled"
    assert readonly_widget.state == "disabled"

    settings.var_save_image_urls_only.value = False
    NovelpiaGUI._sync_image_url_option_states(settings)
    assert normal_widget.state == "normal"
    assert readonly_widget.state == "readonly"


def test_multiple_output_formats_are_returned_in_stable_order():
    settings = SimpleNamespace(
        var_save_epub=Setting(True),
        var_save_txt=Setting(False),
        var_save_pdf=Setting(True),
    )

    assert NovelpiaGUI._selected_output_formats(settings) == ["epub", "pdf"]


def test_multiple_output_paths_share_the_save_dialog_base_name(tmp_path):
    anchor = tmp_path / "book.epub"

    paths = NovelpiaGUI._output_paths_from_anchor(
        str(anchor), ["epub", "txt", "pdf"]
    )

    assert paths == {
        "epub": str(tmp_path / "book.epub"),
        "txt": str(tmp_path / "book.txt"),
        "pdf": str(tmp_path / "book.pdf"),
    }


def test_single_output_path_preserves_custom_filename(tmp_path):
    anchor = tmp_path / "book.custom"

    paths = NovelpiaGUI._output_paths_from_anchor(str(anchor), ["txt"])

    assert paths == {"txt": str(anchor)}


def test_multiple_output_paths_keep_an_unrecognized_suffix_in_base(tmp_path):
    anchor = tmp_path / "book.translation"

    paths = NovelpiaGUI._output_paths_from_anchor(
        str(anchor), ["epub", "pdf"]
    )

    assert paths == {
        "epub": str(tmp_path / "book.translation.epub"),
        "pdf": str(tmp_path / "book.translation.pdf"),
    }


def test_epub_cover_can_use_a_remote_image_url(tmp_path):
    output = tmp_path / "remote-cover.epub"
    generator = EpubGenerator(
        {"title": "Test", "author": "Author"},
        str(output),
        "",
        remote_cover_url="https://images.example.com/cover.jpg?x=1&y=2",
    )
    generator.add_chapter(
        "Chapter",
        '<p><img class="remote-image" src="https://images.example.com/1.jpg"/></p>',
    )
    generator.generate()

    with zipfile.ZipFile(output) as archive:
        archive_names = archive.namelist()
        cover_page = archive.read("OEBPS/Text/cover.html").decode("utf-8")
        chapter_page = archive.read("OEBPS/Text/chapter0001.xhtml").decode("utf-8")

    assert (
        'src="https://images.example.com/cover.jpg?x=1&amp;y=2"'
        in cover_page
    )
    assert 'src="https://images.example.com/1.jpg"' in chapter_page
    assert not any(name.startswith("OEBPS/Images/") for name in archive_names)


def test_epub_chapter_filename_can_preserve_source_range_number(tmp_path):
    output = tmp_path / "chapter-range.epub"
    generator = EpubGenerator(
        {"title": "Test", "author": "Author"}, str(output), ""
    )

    generator.add_chapter("Chapter 42", "<p>Content</p>", chapter_number=42)
    generator.add_chapter("Chapter 43", "<p>Content</p>")
    generator.generate()

    with zipfile.ZipFile(output) as archive:
        archive_names = archive.namelist()

    assert "OEBPS/Text/chapter0042.xhtml" in archive_names
    assert "OEBPS/Text/chapter0043.xhtml" in archive_names
    assert "OEBPS/Text/chapter0001.xhtml" not in archive_names


def test_gui_range_keeps_source_number_after_notice_and_failed_chapter(tmp_path):
    output = tmp_path / "gui-range.epub"
    gui = SimpleNamespace(
        _output_format="epub",
        _output_path=str(output),
        var_zip_compress_images=SimpleNamespace(get=lambda: False),
        var_save_image_urls_only=SimpleNamespace(get=lambda: False),
    )
    results = [
        ("Notice", "<p>Notice</p>", [], True),
        None,
        ("Chapter 21", "<p>Content</p>", [], False),
    ]

    NovelpiaGUI._build_output(
        gui,
        results,
        {"title": "Test", "author": "Author"},
        "",
        None,
        None,
        chapter_start=20,
        notice_count=1,
    )

    with zipfile.ZipFile(output) as archive:
        archive_names = archive.namelist()

    assert "OEBPS/Text/chapter_notice0001.xhtml" in archive_names
    assert "OEBPS/Text/chapter0021.xhtml" in archive_names
    assert "OEBPS/Text/chapter0001.xhtml" not in archive_names


def test_chapter_image_urls_reject_non_http_sources():
    assert DownloaderCore.normalize_chapter_image_url("javascript:alert(1)") is None


def test_pdf_dll_path_excludes_tesseract_without_changing_other_entries():
    original = (
        r"C:\Python312;C:\Program Files\Tesseract-OCR;"
        r"C:\msys64\mingw64\bin;C:\Tools\OCR"
    )

    cleaned, removed = _without_tesseract_dll_directories(
        original, separator=';'
    )

    assert cleaned == (
        r"C:\Python312;C:\msys64\mingw64\bin;C:\Tools\OCR"
    )
    assert removed == [r"C:\Program Files\Tesseract-OCR"]
