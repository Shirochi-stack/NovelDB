"""The "Max width" image option: chapter images wider than it are scaled down when compressed."""
import io
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from noveldb.core.external.job import ExternalJob
from noveldb.core.images import _encode_image_bytes, _encode_with, _max_width_value, _resize_to_max_width
from noveldb.core.npia.pipeline import NpiaPipelineMixin
from noveldb.core.settings import desktop_config
from noveldb.core.settings.legacy_vars import Value, external_values, npia_values
from noveldb.core.settings.model import IMAGE_WIDTHS, AppSettings


def picture(width, height, kind="PNG"):
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, kind)
    return out.getvalue()


def size_of(data):
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def with_width(width):
    settings = AppSettings()
    return replace(settings, images=replace(settings.images, max_width=width))


def test_a_wider_image_is_scaled_down_and_a_narrower_one_is_left_alone():
    wide = Image.new("RGB", (2400, 3600))
    assert _resize_to_max_width(wide, 840).size == (840, 1260)
    assert _resize_to_max_width(wide, "620").size == (620, 930)
    small = Image.new("RGB", (500, 700))
    assert _resize_to_max_width(small, 620) is small  # never enlarged, and not copied
    for original in (None, 0, "Original", "", "wide"):
        assert _resize_to_max_width(wide, original) is wide
        assert _max_width_value(original) is None
    assert _max_width_value(Value("1024")) == 1024 and _max_width_value(Value("Original")) is None

    data, ext = _encode_image_bytes(wide, "WEBP", 50, max_width=1200)
    assert ext == "webp" and size_of(data) == (1200, 1800)
    assert size_of(_encode_image_bytes(wide, "WEBP", 50)[0]) == (2400, 3600)
    # A stitched export's compression carries the width as its third value; two values keep the size.
    assert size_of(_encode_with(wide, ("JPEG", 60, 1024))[0]) == (1024, 1536)
    assert size_of(_encode_with(wide, ("JPEG", 60))[0]) == (2400, 3600)


def test_the_option_is_saved_as_the_tk_window_writes_it_and_reaches_both_pipelines():
    assert AppSettings().images.max_width == 0 and IMAGE_WIDTHS[0] == 0
    for stored, width in (("Original", 0), ("840", 840), (1200, 1200), ("900", 0), (None, 0), ("x", 0)):
        loaded = desktop_config.import_config({} if stored is None else {"image_max_width": stored})
        assert loaded.images.max_width == width, stored
    saved = desktop_config.export_config(with_width(1024))
    assert saved["image_max_width"] == "1024"
    assert desktop_config.export_config(AppSettings())["image_max_width"] == "Original"

    assert npia_values(with_width(620), "1")["var_image_max_width"].get() == "620"
    assert npia_values(AppSettings(), "1")["var_image_max_width"].get() == "Original"
    parent = external_values(with_width(840), "https://example.test/book")["_parent_gui"]
    assert parent.var_image_max_width.get() == "840"


def test_the_other_sources_scale_chapter_images_down_but_not_the_cover(tmp_path):
    job = ExternalJob("https://example.test/book", with_width(840), output_dir=str(tmp_path))
    assert size_of(job._compress_image(picture(2000, 1000), 50, "WEBP")) == (840, 420)
    assert size_of(job._compress_image(picture(600, 900), 50, "WEBP")) == (600, 900)
    assert size_of(job._compress_image(picture(2000, 1000), 90, "JPEG", cover=True)) == (2000, 1000)
    assert job._stitch_compression() == ("WEBP", 50, 840)

    original = ExternalJob("https://example.test/book", AppSettings(), output_dir=str(tmp_path))
    assert size_of(original._compress_image(picture(2000, 1000), 50, "WEBP")) == (2000, 1000)
    assert original._stitch_compression() == ("WEBP", 50, None)


def test_only_a_chosen_width_changes_the_npia_image_cache_fingerprint():
    def host(**extra):
        return SimpleNamespace(
            var_compress_images=Value(True), var_save_image_urls_only=Value(False), var_jpeg_quality=Value(50),
            var_image_format=Value("WEBP"), var_convert_gifs=Value(False), var_static_only=Value(False),
            var_strip_leading_spaces=Value(False), var_remove_newlines=Value(False), **extra)

    fingerprint = NpiaPipelineMixin._image_cache_fingerprint
    # A host without the option, and the original width: what the fingerprint was before the option.
    before = fingerprint(host())
    assert fingerprint(host(var_image_max_width=Value("Original"))) == before
    assert fingerprint(host(var_image_max_width=Value("840"))) != before
    assert fingerprint(host(var_image_max_width=Value("840"))) != fingerprint(host(var_image_max_width=Value("620")))


@pytest.mark.parametrize("width, expected", [(None, (900, 300)), (620, (620, 207))])
def test_a_kept_gif_is_scaled_down_only_when_it_is_wider(width, expected):
    from noveldb.core import text

    frames = [Image.new("RGB", (900, 300), color) for color in ((200, 0, 0), (0, 200, 0), (0, 0, 200))]
    source = io.BytesIO()
    frames[0].save(source, "GIF", save_all=True, append_images=frames[1:], duration=[50, 60, 70], loop=0)
    logs = []

    def download(session, url, logger, **_):
        return source.getvalue()

    original = text._download_image_with_progress
    text._download_image_with_progress = download
    try:
        content = json.dumps({"s": [{"text": '<img src="https://example.test/a.gif">'}]})
        _html, images, failures = text.extract_chapter_content_and_images(
            content, None, None, True, 50, "WEBP", logs.append, lambda: 1, image_max_width=width)
    finally:
        text._download_image_with_progress = original
    assert failures == 0 and len(images) == 1
    name, data = images[0]
    assert name.endswith(".gif")
    with Image.open(io.BytesIO(data)) as result:
        assert result.size == expected and result.n_frames == 3
