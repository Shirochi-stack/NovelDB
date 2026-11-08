from base64 import b64encode
from dataclasses import replace
from io import BytesIO
from threading import Event
from zipfile import ZIP_STORED, ZipFile

import pytest
from PIL import Image

from noveldb.core import library
from noveldb.core.chapter_folder import chapter_directory, folder_export, stitch_panels, write_chapter
from noveldb.core.external.kpage_images import KpageImages
from noveldb.core.external.job import ExternalJob
from noveldb.core.settings.model import AppSettings
from noveldb.core.upload.bundle import zip_folder


def panel(color, height=1000):
    encoded = BytesIO()
    Image.new("RGB", (24, height), color).save(encoded, "PNG")
    return "png", encoded.getvalue()


def test_stitches_in_order_and_caps_strip_height():
    pages = [panel("red"), panel("blue")] + [panel("green")] * 15
    strips = list(stitch_panels(pages))
    assert len(strips) == 2
    with Image.open(BytesIO(strips[0][1])) as strip:
        assert strip.size == (24, 16_000)
        assert strip.getpixel((12, 500))[0] > 230
        assert strip.getpixel((12, 1500))[2] > 230
    assert strips[1] == pages[-1]


def test_precise_limit_groups_multiple_strips_and_leaves_single_strips_flat(tmp_path):
    pages = list(enumerate([panel("red", 1000), panel("blue", 1000), panel("green", 700)], 1))
    folder = tmp_path / "Book.folder"
    with folder_export(folder) as stage:
        write_chapter(stage, 8, "Episode", "", pages, max_height=2001)
        write_chapter(stage, 9, "Single strip", "", pages[:2], max_height=2001)
    files = sorted((folder / "0008 Episode").iterdir())
    assert [file.name for file in files] == ["001.png", "002.png"]
    assert (folder / "0009 Single strip - 001.png").is_file()
    with Image.open(files[0]) as merged:
        assert merged.size == (24, 2000)
    assert files[1].read_bytes() == pages[2][1][1]

    with folder_export(folder) as stage:
        write_chapter(stage, 8, "Episode", "", pages)
    assert [file.read_bytes() for file in sorted(folder.iterdir())] == [image[1] for _, image in pages]


def test_unlimited_stitches_a_chapter_taller_than_the_slider_cap(tmp_path):
    pages = list(enumerate([panel("red")] + [panel("green")] * 165 + [panel("blue")], 1))
    with folder_export(tmp_path / "Book.folder") as stage:
        write_chapter(stage, 1, "Episode", "", pages, max_height=0)
    files = list((tmp_path / "Book.folder").iterdir())
    assert [file.name for file in files] == ["0001 Episode - 001.png"]
    with Image.open(files[0]) as strip:
        strip.verify()
    with Image.open(files[0]) as strip:
        assert strip.size == (24, 167_000)
        assert strip.getpixel((12, 500)) == (255, 0, 0)
        assert strip.getpixel((12, 166_500)) == (0, 0, 255)


def test_folder_and_zip_use_the_same_chapters_and_folder_upload_keeps_them(tmp_path):
    folder = tmp_path / "Book.folder"
    archive = tmp_path / "Book.zip"
    for target in (folder, archive):
        with folder_export(target) as stage:
            path = chapter_directory(stage, 8, "Episode: Eight")
            (path / "0001.jpg").write_bytes(b"sample")
            (chapter_directory(stage, 10, "Episode Ten") / "chapter.txt").write_text("Prose", encoding="utf-8")
    assert [book.name for book in library.scan(tmp_path)] == ["Book"]
    assert {item.format for item in library.scan(tmp_path)[0].files} == {"Folder", "ZIP"}
    assert (folder / "0008 Episode_ Eight" / "0001.jpg").read_bytes() == b"sample"
    with ZipFile(archive) as z:
        assert z.namelist() == ["0008 Episode_ Eight/0001.jpg", "0010 Episode Ten/chapter.txt"]
    with ZipFile(zip_folder(str(folder), str(tmp_path))) as z:
        assert "Book.folder/0008 Episode_ Eight/0001.jpg" in z.namelist()


def test_failed_folder_replacement_keeps_the_existing_export(tmp_path):
    target = tmp_path / "Book.folder"
    target.mkdir()
    keep = target / "keep.txt"
    keep.write_text("original", encoding="utf-8")
    with pytest.raises(RuntimeError, match="stop"), folder_export(target) as stage:
        (stage / "partial.txt").touch()
        raise RuntimeError("stop")
    assert keep.read_text(encoding="utf-8") == "original"


def test_export_job_uses_manga_by_default_and_applies_webtoon_options(tmp_path):
    settings = AppSettings()
    assert not settings.images.webtoon_export
    assert settings.images.stitch_height == 16_000
    images = [panel("red")[1], panel("blue")[1], panel("green")[1]]
    for webtoon, output in ((False, "folder"), (True, "zip")):
        options = replace(settings.images, webtoon_export=webtoon, compress=False, stitch_height=2001)
        job = ExternalJob("https://example.test/book", replace(settings, images=options), output_dir=str(tmp_path))
        job._chapter_results = [{"chapterName": "Episode", "images": [
            {"data": "data:image/png;base64," + b64encode(image).decode("ascii")} for image in images
        ]}]
        job._generate_folder("Book", output)

    files = sorted((tmp_path / "Book.folder").iterdir())
    assert [file.name for file in files] == [f"0001 Episode - {i:03}.png" for i in range(1, 4)]
    assert [file.read_bytes() for file in files] == images
    with ZipFile(tmp_path / "Book.zip") as archive:
        assert archive.namelist() == ["0001 Episode/001.png", "0001 Episode/002.png"]
        with Image.open(BytesIO(archive.read("0001 Episode/001.png"))) as strip:
            assert strip.size == (24, 2000)
        assert archive.read("0001 Episode/002.png") == images[2]


@pytest.mark.parametrize("initial_format", ["epub", "cbz", None])
@pytest.mark.parametrize("compress", [False, True])
def test_combined_exports_download_and_encode_each_page_once(tmp_path, initial_format, compress):
    settings = AppSettings()
    images = replace(settings.images, compress=compress, format="JPEG")
    job = ExternalJob("https://example.test/book", replace(settings, images=images), output_dir=str(tmp_path))
    raw = panel("green")[1]
    url = "https://example.test/page.png"
    job._book_data = {"bookname": "Book"}
    job._chapter_results = [{"chapterName": "Episode", "images": [{"url": url, "name": "page.png"}]}]
    downloads = []
    encodes = []
    encode = job._compress_image

    def download(address, *_args, **_kwargs):
        downloads.append(address)
        return raw

    def compress_page(data, quality, image_format):
        encodes.append(image_format)
        return encode(data, quality, image_format)

    job._download_image_python = download
    job._compress_image = compress_page
    job._try_cover_from_images = lambda *_args: False
    formats = (initial_format, "folder", "zip") if initial_format else ("folder", "zip")
    job._generate_formats(job._book_data, formats, "Book", "Author")

    assert downloads == [url]
    assert encodes == (["JPEG"] if compress else [])
    messages = [str(message) for kind, message in job._msg_queue.messages if kind == "log"]
    if initial_format == "epub":
        preparing = next(i for i, message in enumerate(messages) if "Preparing EPUB chapter" in message)
        writing = next(i for i, message in enumerate(messages) if "Writing EPUB to disk" in message)
        ready = next(i for i, message in enumerate(messages) if "Chapter images ready:" in message)
        assert preparing < ready < writing
    output, = (tmp_path / "Book.folder").iterdir()
    assert output.suffix == (".jpg" if compress else ".png")
    with ZipFile(tmp_path / "Book.zip") as archive:
        assert archive.namelist() == [output.name]
        assert archive.getinfo(output.name).compress_type == ZIP_STORED
        assert archive.read(output.name) == output.read_bytes()


def test_unlimited_webp_compresses_the_stitched_output_within_the_codec_limit(tmp_path):
    pages = list(enumerate([panel("red")] * 16 + [panel("blue")] * 2, 1))
    with folder_export(tmp_path / "Book.folder") as stage:
        write_chapter(stage, 1, "Episode", "", pages, max_height=0, compression=("WEBP", 80))
    files = sorted((tmp_path / "Book.folder" / "0001 Episode").iterdir())
    assert [file.name for file in files] == ["001.webp", "002.webp"]
    for file, expected in zip(files, (16_000, 2_000)):
        with Image.open(file) as image:
            assert image.format == "WEBP"
            assert image.height == expected


def test_epub_and_webtoon_use_original_panels_for_stitching(tmp_path):
    settings = AppSettings()
    images = replace(settings.images, compress=True, webtoon_export=True, format="WEBP")
    job = ExternalJob("https://example.test/book", replace(settings, images=images), output_dir=str(tmp_path))
    job._book_data = {"bookname": "Book"}
    job._chapter_results = [{"chapterName": "Episode", "images": [
        {"data": "data:image/png;base64," + b64encode(panel(color)[1]).decode("ascii"), "name": color + ".png"}
        for color in ("red", "blue")
    ]}]
    job._compress_image = lambda *_args: panel("black")[1]
    job._try_cover_from_images = lambda *_args: False
    job._generate_formats(job._book_data, ("epub", "folder"), "Book", "Author")
    with Image.open(tmp_path / "Book.folder" / "0001 Episode - 001.webp") as image:
        assert image.size == (24, 2_000)
        assert image.getpixel((12, 500))[0] > 230
        assert image.getpixel((12, 1_500))[2] > 230

@pytest.mark.parametrize("initial_format", ["epub", "cbz", None])
def test_stitching_starts_while_the_next_chapter_downloads(tmp_path, monkeypatch, initial_format):
    from noveldb.core import chapter_folder

    settings = AppSettings()
    images = replace(settings.images, compress=False, webtoon_export=True, stitch_height=1_500)
    job = ExternalJob("https://example.test/book", replace(settings, images=images), output_dir=str(tmp_path))
    job._book_data = {"bookname": "Book"}
    job._chapter_results = [
        {"chapterName": f"Episode {chapter}", "images": [
            {"url": f"https://example.test/{chapter}/{page}.png", "name": f"{page}.png"}
            for page in (1, 2, 3)
        ]}
        for chapter in (1, 2)
    ]
    first_stitch = Event()
    second_download = Event()
    originals = panel("blue", height=700)[1]
    downloads = []

    def download(url, *_args, **_kwargs):
        downloads.append(url)
        if "/2/" in url:
            assert first_stitch.wait(3), "the first chapter should be stitching already"
            second_download.set()
        return originals

    def write(stage, number, title, text, pages, links, **options):
        if number == 1:
            assert options["max_height"] == 1_500
            first_stitch.set()
            assert second_download.wait(3), "downloading the next chapter should overlap stitching"
        return write_chapter(stage, number, title, text, pages, links, **options)

    monkeypatch.setattr(chapter_folder, "write_chapter", write)
    job._download_image_python = download
    job._try_cover_from_images = lambda *_args: False
    formats = (initial_format, "folder", "zip") if initial_format else ("folder", "zip")
    job._generate_formats(job._book_data, formats, "Book", "Author")
    assert len(downloads) == len(set(downloads)) == 6
    assert first_stitch.is_set() and second_download.is_set()
    with ZipFile(tmp_path / "Book.zip") as archive:
        assert archive.namelist() == [
            "0001 Episode 1/001.png", "0001 Episode 1/002.png",
            "0002 Episode 2/001.png", "0002 Episode 2/002.png",
        ]
    if initial_format:
        assert (tmp_path / f"Book.{initial_format}").is_file()


def test_desktop_layout_controls_follow_formats_and_open_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path))
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow

    app = widgets.QApplication.instance() or widgets.QApplication([])
    context = AppContext(start_workers=False)
    window = MainWindow(context)
    try:
        download = window.download
        images = window.settings_page.forms["Images"]
        slider = images.findChild(widgets.QSlider, "stitchHeightSlider")
        height = images.findChild(widgets.QSpinBox, "stitchHeightInput")
        assert slider.value() == height.value() == 16_000
        slider.setValue(slider.maximum())
        assert context.settings.images.stitch_height == 0
        assert height.text() == "Unlimited"
        context.save_now()
        assert context.store.load().images.stitch_height == 0
        height.setValue(27_363)
        assert slider.value() == context.settings.images.stitch_height == 27_363
        assert download.export_layout_row.isHidden()
        for output in ("folder", "zip"):
            download.format_chips[output].click()
            assert not download.export_layout_row.isHidden()
            assert download.manga_layout.isChecked()
            assert download.stitching_settings.isHidden()
            download.webtoon_layout.click()
            assert download.webtoon_layout.isChecked()
            assert not download.stitching_settings.isHidden()
            download.stitching_settings.click()
            assert window.stack.currentWidget() is window.settings_page
            assert window.settings_page.categories.currentItem().text() == "Images"
            download.manga_layout.click()
            download.format_chips[output].click()
            assert download.export_layout_row.isHidden()
    finally:
        context._save_timer.stop()
        window.deleteLater()
        app.processEvents()


def test_kpage_image_lookahead_reuses_saved_bytes():
    from threading import Event

    started = Event()
    release = Event()
    calls = []

    class Job:
        _download_cancelled = False

        def _copy_browser_cookies_to_session(self, session):
            pass

        def _download_image_python(self, url, **options):
            calls.append((url, options))
            started.set()
            assert release.wait(3)
            return b"image"

    lookahead = KpageImages(Job(), "https://example.test/book", 2)
    url = "https://example.test/p1.jpg"
    try:
        lookahead.offer({"images": [{"url": url}, {"url": url}]})
        assert started.wait(3)
        release.set()
        assert lookahead.read(url) == b"image"
        assert lookahead.read(url) == b"image"
        assert lookahead.read("https://example.test/other.jpg") is None
        assert len(calls) == 1
        assert calls[0][1]["use_prefetch"] is False
    finally:
        release.set()
        lookahead.close()
