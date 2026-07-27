import json
from types import SimpleNamespace

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


def make_fingerprint_settings(remove_newlines):
    return SimpleNamespace(
        var_compress_images=Setting(False),
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
