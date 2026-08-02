import json
import zipfile
from types import SimpleNamespace

from downloader_core import DownloaderCore
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


def test_chapter_image_urls_reject_non_http_sources():
    assert DownloaderCore.normalize_chapter_image_url("javascript:alert(1)") is None
