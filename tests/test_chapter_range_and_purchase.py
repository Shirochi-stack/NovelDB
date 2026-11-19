"""One-field chapter ranges, auto purchase and "Upload chapters" (desktop core)."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from noveldb.core import chapter_range
from noveldb.core.external.pipeline import ExternalPipelineMixin
from noveldb.core.jobs import JobDispatcher
from noveldb.core.queue.model import Job
from noveldb.core.settings.desktop_config import export_config, import_config
from noveldb.core.settings.legacy_vars import Value, external_values
from noveldb.core.settings.model import AppSettings, SharedSettings


@pytest.mark.parametrize("typed, expected", [
    ("4", (4, 4)), (" 4 ", (4, 4)), ("4-7", (4, 7)), ("4 - 7", (4, 7)), ("4–7", (4, 7)),
    ("7-3", (7, 7)), ("3-", (3, 3)), ("-7", (7, 7)), ("", (None, None)), ("all", (None, None)),
    ("0", (None, None)), ("4-7-9", (4, 7)), ("ch 12", (12, 12)),
])
def test_a_range_is_typed_like_pages_and_bad_ranges_keep_their_first_number(typed, expected):
    assert chapter_range.parse(typed) == expected


def test_a_stored_range_shows_as_the_text_that_gives_it_back():
    for stored in ((4, 4), (4, 7), (None, None)):
        assert chapter_range.parse(chapter_range.text(*stored)) == stored
    assert chapter_range.text(4, None) == "4-"  # an open end of older settings
    assert chapter_range.text(None, 7) == "1-7"
    assert chapter_range.label(4, None) == "4-end"
    assert chapter_range.label(4, 4) == "4" and chapter_range.label(4, 7) == "4-7"
    assert chapter_range.contains(4, 4, 4) and not chapter_range.contains(4, 4, 5)
    assert not chapter_range.contains(None, None, 1)


@pytest.mark.parametrize("typed, expected", [
    ("4,7", (4, 7, "4,7")), ("7, 4", (4, 7, "4,7")), ("1-3,9", (1, 9, "1-3,9")), ("4-7", (4, 7, "")),
    ("4", (4, 4, "")), ("4,5,6", (4, 6, "")), ("1-3,2-5", (1, 5, "")), ("4,,x,7-6", (4, 7, "4,7")),
    ("", (None, None, "")), ("4，7", (4, 7, "4,7")),
])
def test_a_comma_list_names_separate_chapters(typed, expected):
    assert chapter_range.read(typed) == expected


def test_a_list_chooses_only_its_chapters_and_is_ignored_once_the_range_moved():
    assert [n for n in range(1, 10) if chapter_range.selects(4, 7, "4,7", n)] == [4, 7]
    assert [n for n in range(1, 10) if chapter_range.selects(1, 9, "1-3,9", n)] == [1, 2, 3, 9]
    assert chapter_range.shown(4, 7, "4,7") == "4,7" and chapter_range.named(4, 7, "4,7") == "4,7"
    # The range was changed elsewhere without the list: the list no longer belongs to it.
    assert [n for n in range(1, 10) if chapter_range.selects(2, 5, "4,7", n)] == [2, 3, 4, 5]
    assert chapter_range.shown(2, 5, "4,7") == "2-5"


def test_a_list_leaves_out_the_chapters_between_its_parts(tmp_path):
    settings = AppSettings(shared=replace(SharedSettings(), range_from=4, range_to=7, range_list="4,7"))
    job = SimpleNamespace(**external_values(settings, "https://example.com/b"))
    assert ExternalPipelineMixin._range_label(job) == "4,7"
    span = _chapters(8)[3:7]
    assert ExternalPipelineMixin._chapters_left_out(job, span, 3) == [1, 2]
    plain = SimpleNamespace(**external_values(AppSettings(shared=replace(SharedSettings(), range_from=4, range_to=7)), "u"))
    assert ExternalPipelineMixin._chapters_left_out(plain, span, 3) == []


def test_auto_purchase_follows_a_list():
    job = Purchaser(low=4, high=7)
    job._var_purchase_list = Value("4,7")
    ExternalPipelineMixin._auto_purchase(job, _chapters(8), 0, [None] * 8)
    assert job.bought == ["c4", "c7"]


def test_auto_purchase_settings_survive_the_config_file():
    settings = AppSettings(shared=replace(SharedSettings(), auto_purchase=True, purchase_from=4, purchase_to=4))
    again = import_config(export_config(settings))
    assert (again.shared.auto_purchase, again.shared.purchase_from, again.shared.purchase_to) == (True, 4, 4)
    cleared = import_config(export_config(replace(settings, shared=replace(settings.shared, purchase_from=None, purchase_to=None))))
    assert cleared.shared.purchase_from is None and cleared.shared.purchase_to is None
    assert import_config({}).shared.auto_purchase is False


class Purchaser:
    """A pipeline with just what `_auto_purchase` reads."""

    def __init__(self, on=True, low=4, high=4, kpage=True, answers=None):
        self.lines = []
        self.bought = []
        self._downloading = True
        self._var_auto_purchase = Value(on)
        self._var_purchase_from = Value(low)
        self._var_purchase_to = Value(high)
        self._book_data = {"_kpage": kpage}
        answers = answers or {}

        def purchase(chapter):
            self.bought.append(chapter["name"])
            return answers.get(chapter["name"], True)

        self._scraper = SimpleNamespace(kpage_auto_purchase=purchase)

    def _log(self, text):
        self.lines.append(text)


def _chapters(count, free=3):
    return [{"name": f"c{n}", "isVIP": n > free, "isPaid": n > free, "isAccessible": n <= free}
            for n in range(1, count + 1)]


def test_auto_purchase_unlocks_only_locked_chapters_inside_its_range():
    job = Purchaser()
    chapters = _chapters(8)
    ExternalPipelineMixin._auto_purchase(job, chapters, 0, [None] * 8)
    assert job.bought == ["c4"]
    assert chapters[3]["isAccessible"] is True and chapters[3]["isVIP"] is False
    assert chapters[4]["isAccessible"] is False  # chapter 5 is outside the range


def test_auto_purchase_counts_chapters_from_the_book_not_the_download_range():
    job = Purchaser(low=4, high=5)
    chapters = _chapters(8)[3:6]  # the download range is 4-6
    ExternalPipelineMixin._auto_purchase(job, chapters, 3, [None] * 3)
    assert job.bought == ["c4", "c5"]


def test_auto_purchase_leaves_free_cached_and_failed_chapters_alone():
    job = Purchaser(low=1, high=5, answers={"c4": False})
    chapters = _chapters(5)
    results = [None, None, None, None, {"chapterName": "cached"}]
    ExternalPipelineMixin._auto_purchase(job, chapters, 0, results)
    assert job.bought == ["c4"]  # 1-3 are free, 5 is cached
    assert chapters[3]["isAccessible"] is False


def test_auto_purchase_does_nothing_when_off_without_a_range_or_on_another_source():
    chapters = _chapters(6)
    for job in (Purchaser(on=False), Purchaser(low=None, high=None), Purchaser(kpage=False)):
        ExternalPipelineMixin._auto_purchase(job, chapters, 0, [None] * 6)
        assert job.bought == []
    assert all(not chapter["isAccessible"] for chapter in chapters[3:])


def test_auto_purchase_stops_when_the_account_is_signed_out():
    job = Purchaser(low=4, high=6, answers={"c4": "login"})
    ExternalPipelineMixin._auto_purchase(job, _chapters(6), 0, [None] * 6)
    assert job.bought == ["c4"]


def test_a_ranged_download_is_named_after_its_range(tmp_path):
    settings = AppSettings(shared=replace(SharedSettings(), range_from=4, range_to=7))
    job = SimpleNamespace(**external_values(settings, "https://example.com/b"))
    assert ExternalPipelineMixin._range_label(job) == "4-7"
    whole = SimpleNamespace(**external_values(AppSettings(), "https://example.com/b"))
    assert ExternalPipelineMixin._range_label(whole) == ""


def test_upload_chapters_runs_the_book_for_just_that_range():
    seen = {}

    class External:
        def __init__(self, url, settings, source, events, token, output_dir):
            seen["shared"] = settings.effective(source)
            self._log = lambda text: None

        def run(self, scraper):
            return {"ok": True}

    class Browser:
        def submit(self, call, log=None):
            return SimpleNamespace(result=lambda timeout=None: call(None))

    base = AppSettings(shared=replace(SharedSettings(), range_from=1, range_to=2, use_cache=False,
                                      auto_purchase=True, formats=("txt", "epub")))
    dispatcher = JobDispatcher(lambda: base, None, Browser(), lambda job: None, external_job_factory=External)
    job = Job("https://example.com/b", source="kpage", upload_range=[4, 7, "4,7"])
    dispatcher(job, SimpleNamespace(cancelled=False))
    shared = seen["shared"]
    assert (shared.range_from, shared.range_to, shared.range_list) == (4, 7, "4,7")
    assert shared.use_cache is True and shared.auto_purchase is False
    assert shared.formats == ("txt", "epub")
    assert Job.from_dict(job.to_dict()).upload_range == [4, 7, "4,7"]
