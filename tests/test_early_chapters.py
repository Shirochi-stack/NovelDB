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


def test_a_stop_ends_the_stitching_between_two_strips_and_removes_the_chapters_files(tmp_path):
    import pytest

    from noveldb.core.chapter_folder import ExportStopped, write_chapter

    pages = [(number, ("png", page("red", size=(100, 400)))) for number in range(1, 9)]  # 8 pages, 2 a strip
    calls = []

    def stopped():
        calls.append(1)
        return len(calls) > 6  # the third strip is never finished

    (tmp_path / "kept.txt").write_text("another chapter's file")
    with pytest.raises(ExportStopped):
        write_chapter(tmp_path, 3, "Episode", "Text", pages, max_height=800, chapter_folders=True, stopped=stopped)
    # Nothing of the chapter is left (its folder too); what was there before stays.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["kept.txt"]
    # Without a stop the same chapter is written whole.
    write_chapter(tmp_path, 3, "Episode", "Text", pages, max_height=800, chapter_folders=True, stopped=lambda: False)
    assert len(list((tmp_path / "0003 Episode").glob("*.png"))) == 4


def test_a_stop_while_files_are_generated_ends_the_chapter_in_the_works(tmp_path):
    job = make_job(tmp_path, compress=False, webtoon_export=True, stitch_images=True, stitch_height=800)
    pages = [(number, ("png", page("blue", size=(100, 400)))) for number in range(1, 9)]
    chapter = {"chapterName": "Episode", "contentText": "", "images": []}
    stage = tmp_path / "stage"
    stage.mkdir()
    # The export step's own chapters: a Stop pressed during the build ends the stitching quietly
    # (the step then ends at its own check), and leaves no half-written chapter.
    job._generation_stop_baseline = False
    strips = []
    original = job._log

    def log(message):
        original(message)
        if "Stitching chapter" in str(message):
            job._download_cancelled = True  # Stop, right after the stitching began

    job._log = log
    job._write_export_chapter(stage, 0, chapter, pages, (), 800, False, None)
    strips = list(stage.rglob("*.png"))
    assert strips == []
    assert any("Stitching chapter 1" in line for line in messages(job))
    # Chapters still queued are not started at all.
    job._write_export_chapter(stage, 1, chapter, pages, (), 800, False, None)
    assert sum("Stitching chapter" in line for line in messages(job)) == 1


def test_a_stop_while_chapters_are_read_ends_the_early_stitching(tmp_path):
    job = make_job(tmp_path, compress=False, webtoon_export=True, stitch_images=True, stitch_height=800)
    saved = []
    for number in range(8):
        path = tmp_path / f"p{number}.png"
        path.write_bytes(page("red", size=(100, 400)))
        saved.append({"url": f"https://example.test/{number}", "data": f"file:{path}"})
    original = job._log

    def log(message):
        original(message)
        if "Stitching chapter" in str(message):
            job._download_cancelled = True

    job._log = log
    job._early_chapters = early = EarlyChapters(job, str(tmp_path))
    try:
        job._offer_read_chapter(0, {"chapterName": "One", "contentText": "", "images": saved})
        job._offer_read_chapter(1, {"chapterName": "Two", "contentText": "", "images": saved})
        early.finish()
        # Neither chapter counts as written, and nothing of them is left behind.
        assert early.writes.submitted == set()
        assert [path for path in early.writes.root.rglob("*") if path.is_file()] == []
        assert sum("Stitching chapter" in line for line in messages(job)) == 1
    finally:
        early.close()

def test_a_chapters_images_report_their_progress_while_they_come_in(monkeypatch):
    # Before, the log was silent from one chapter line to the next, however long its images took.
    from noveldb.core.engines import bilicomic
    from noveldb.core.external import kpage_images

    monkeypatch.setattr(kpage_images, "PROGRESS_SECONDS", 0)
    monkeypatch.setattr(bilicomic, "PROGRESS_SECONDS", 0)
    lines = []

    class Job:
        _download_cancelled = False

        def _log(self, message):
            lines.append(message)

        def _copy_browser_cookies_to_session(self, session):
            pass

        def _download_image_python(self, url, **options):
            return b"x" * 1048576

    lookahead = ImageLookahead(Job(), "https://example.test/book", 1)
    try:
        lookahead.offer({"chapterName": "Episode 7",
                         "images": [{"url": f"https://example.test/{number}.png"} for number in range(4)]})
        assert all(lookahead.read(f"https://example.test/{number}.png") for number in range(4))
    finally:
        lookahead.close()
    assert [line for line in lines if "/4 (" in line] == [
        f"    Images of Episode 7: {count}/4 ({count}.0 MB, " + line.split(", ")[-1]
        for count, line in zip((1, 2, 3), [line for line in lines if "/4 (" in line])]
    assert len([line for line in lines if "/4 (" in line]) == 3
    assert lines[-1].startswith("  Images of Episode 7: 4 downloaded (4.0 MB")

    # Bilibili Manga, whose pages come in with the chapter.
    said = []
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 2000
    engine = bilicomic.BiliComicEngine(fetch=None, log=said.append, download=lambda url: jpeg, workers=1)
    try:
        pages = engine._save_pages(9, [f"https://example.test/{number}.jpg" for number in range(3)], "03 Name")
    finally:
        engine.close()
    assert len(pages) == 3
    assert [line.split(" (")[0] for line in said] == ["    [Bilibili Manga] 03 Name: 1/3 pages",
                                                      "    [Bilibili Manga] 03 Name: 2/3 pages"]


def test_a_fast_chapter_gets_no_progress_lines():
    lines = []

    class Job:
        _download_cancelled = False

        def _log(self, message):
            lines.append(message)

        def _copy_browser_cookies_to_session(self, session):
            pass

        def _download_image_python(self, url, **options):
            return b"x" * 1024

    lookahead = ImageLookahead(Job(), "https://example.test/book", 2)
    try:
        lookahead.offer({"chapterName": "Quick", "images": [{"url": f"https://example.test/{n}.png"} for n in range(6)]})
        assert all(lookahead.read(f"https://example.test/{n}.png") for n in range(6))
    finally:
        lookahead.close()
    assert len(lines) == 1 and lines[0].startswith("  Images of Quick: 6 downloaded")

def test_strips_rendered_side_by_side_come_out_in_order_and_unchanged():
    from noveldb.core.chapter_folder import ExportStopped, stitch_panels, stitch_workers

    assert 1 <= stitch_workers() <= 4
    colors = ["red", "green", "blue", "white", "black", "yellow", "purple", "orange", "pink", "gray"]
    pages = [("png", page(color, size=(100, 400))) for color in colors]  # two pages a strip at 800
    one = list(stitch_panels(iter(pages), 800, compression=("JPEG", 80), workers=1))
    several = list(stitch_panels(iter(pages), 800, compression=("JPEG", 80), workers=4))
    assert len(one) == 5 and several == one
    # A stop reaches the caller from whichever strip meets it, and nothing is left running.
    import pytest

    calls = []

    def stopped():
        calls.append(1)
        if len(calls) > 4:
            raise ExportStopped()

    with pytest.raises(ExportStopped):
        list(stitch_panels(iter(pages), 800, compression=("JPEG", 80), stopped=stopped, workers=3))