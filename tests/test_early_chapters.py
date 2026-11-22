"""Folder and ZIP chapters are written (and stitched) while the other chapters are still read."""
from dataclasses import replace
from io import BytesIO

from PIL import Image

from noveldb.core.external.early_chapters import EarlyChapters
from noveldb.core.external.job import ExternalJob
from noveldb.core.external.kpage_images import ImageLookahead
from noveldb.core.settings.model import AppSettings


def page(color, size=(40, 60)):
    out = BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def make_job(tmp_path, **images):
    settings = AppSettings()
    job = ExternalJob("https://example.test/book", replace(settings, images=replace(settings.images, **images)),
                      output_dir=str(tmp_path))
    job._book_data = {"bookname": "Book"}
    job._try_cover_from_images = lambda *_args: False
    return job


def messages(job):
    return [str(message) for kind, message in job._msg_queue.messages if kind == "log"]


def test_a_chapter_is_written_as_soon_as_its_pages_are_in_and_the_export_does_the_rest(tmp_path):
    job = make_job(tmp_path, compress=False)
    site = {f"https://example.test/{name}.png": page(name) for name in ("red", "green", "blue")}
    asked = []

    def download(address, *_args, use_prefetch=True, **_options):
        if use_prefetch:
            held = job._kpage_images.read(address) if job._kpage_images else None
            if held:
                return held
        asked.append(address)
        return site.get(address)

    job._download_image_python = download
    saved = tmp_path / "saved-page.png"
    saved.write_bytes(page("white"))
    chapters = [
        {"chapterName": "One", "contentText": "", "images": [{"url": "https://example.test/red.png"},
                                                              {"url": "https://example.test/green.png"}]},
        # Pages its source saved itself (Bilibili Manga): nothing to wait for.
        {"chapterName": "Two", "contentText": "", "images": [{"url": "https://example.test/x", "data": f"file:{saved}"}]},
        # A page the lookahead could not get: the chapter is left for the export step.
        {"chapterName": "Three", "contentText": "", "images": [{"url": "https://example.test/blue.png"},
                                                                {"url": "https://example.test/gone.png"}]},
        {"_locked": True, "chapterName": "Four"},
    ]
    job._kpage_images = ImageLookahead(job, "https://example.test/book", 2)
    job._early_chapters = early = EarlyChapters(job, str(tmp_path))
    try:
        for index, chapter in enumerate(chapters):
            job._offer_read_chapter(index, chapter)
            job._offer_read_chapter(index, chapter)  # offered again (a retry): queued once
        early.finish()
        assert early.writes.submitted == {0, 1}
        assert early.waiting() == 0
        # Written before the export step began: the two chapters' pages are already there.
        written = sorted(path.name for path in early.writes.root.iterdir())
        assert written == ["0001 One - 001.png", "0001 One - 002.png", "0002 Two - 001.png"]
        assert not any("Writing chapter 3" in line for line in messages(job))

        job._chapter_results = chapters
        job._generate_formats(job._book_data, ("folder",), "Book", "Author")
    finally:
        early.close()
        job._kpage_images.close()

    lines = messages(job)
    assert any("2 chapters written as they arrived." in line for line in lines)
    assert sum("Writing chapter" in line for line in lines) == 3  # each chapter once
    book = tmp_path / "Book.folder"
    files = sorted(book.iterdir())
    assert [path.name for path in files] == ["0001 One - 001.png", "0001 One - 002.png", "0002 Two - 001.png",
                                             "0003 Three - 001.png"]
    assert [path.read_bytes() for path in files] == [page("red"), page("green"), page("white"), page("blue")]
    # Each page was asked for once here; only the refused one again, by the export step.
    assert sorted(asked) == sorted([*site, "https://example.test/gone.png", "https://example.test/gone.png"])
    assert not list(tmp_path.glob(".noveldb-chapters-*"))


def test_webtoon_chapters_are_stitched_early_and_the_width_limit_applies(tmp_path):
    job = make_job(tmp_path, compress=True, webtoon_export=True, stitch_images=True, max_width=620, format="JPEG")
    saved = []
    for number, color in enumerate(("red", "blue")):
        path = tmp_path / f"p{number}.png"
        path.write_bytes(page(color, size=(1000, 500)))
        saved.append({"url": f"https://example.test/{number}", "data": f"file:{path}"})
    job._early_chapters = early = EarlyChapters(job, str(tmp_path))
    try:
        job._offer_read_chapter(0, {"chapterName": "Episode", "contentText": "", "images": saved})
        early.finish()
        assert early.writes.submitted == {0}
        assert any("Stitching chapter 1" in line for line in messages(job))
        strips = [path for path in early.writes.root.rglob("*") if path.is_file()]
        assert len(strips) == 1
        with Image.open(strips[0]) as strip:
            assert strip.format == "JPEG" and strip.size == (620, 620)  # two 1000x500 pages, scaled to 620 wide
    finally:
        early.close()


def test_a_stopped_download_leaves_its_chapters_to_the_export_step(tmp_path):
    job = make_job(tmp_path, compress=False)
    job._download_cancelled = True
    job._early_chapters = early = EarlyChapters(job, str(tmp_path))
    try:
        job._offer_read_chapter(0, {"chapterName": "One", "contentText": "Text", "images": []})
        early.finish()
        assert early.writes.submitted == set()
    finally:
        early.close()
