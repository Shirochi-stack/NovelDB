"""npia tag search helpers: labels, query groups, Top 100 summaries and saved lists."""

from noveldb.core.npia import tags
from noveldb.core.npia.ids import extract_novel_id, novel_url


class FakeCore:
    def __init__(self, auth, log):
        self.stop_signal = False
        self.calls = []

    def fetch_novels_by_tags(self, groups, **kwargs):
        self.calls.append(("tags", groups, kwargs))
        return {"3", "1"}

    def fetch_all_novels(self, **kwargs):
        self.calls.append(("all", kwargs))
        return ["9"]

    def fetch_top100_rankings(self, age_filter=""):
        return {"daily all": ["1", "2"], "weekly all": ["2", "3"]}


def test_site_tag_is_the_real_korean_tag_not_the_codename():
    assert tags.SITE_TAG != "npia" and not tags.SITE_TAG.isascii()
    assert tags.display_name(tags.SITE_TAG) == "npia"
    assert tags.display_name("TS") == "TS (Genderbend)"


def test_tag_label_and_groups():
    fantasy = next(tag for tag, label in tags.COMMON_TAGS if label == "Fantasy")
    assert tags.tag_label([[fantasy, "TS"], ["a/b"]]) == "Fantasy_OR_TS+a_b"
    assert tags.tag_label(None) == "all_novels"
    assert tags.groups_for([["x", "y"]], ["ignored"], "also ignored") == [["x", "y"]]
    assert tags.groups_for([], ["a", "b"], "c, a") == [["a"], ["b"], ["c"]]


def test_top100_summary_and_files(tmp_path):
    rankings = {"daily teen": ["1", "2"], "weekly teen": ["2", "3"], "daily all": ["4"]}
    unique, text = tags.top100_summary(rankings)
    assert unique == {"1", "2", "3", "4"}
    assert text.splitlines() == ["Top 100: 4 unique novels", "  teen: daily: 2, weekly: 2", "  all: daily: 1"]
    single = tmp_path / "top.txt"
    tags.write_top100(rankings, path=str(single))
    assert single.read_text(encoding="utf-8").startswith("# Daily Teen Top 100\n1\n2\n\n")
    written = tags.write_top100(rankings, folder=str(tmp_path))
    assert sorted(p.split("\\")[-1].split("/")[-1] for p in written) == [
        "daily_all_top100.txt", "daily_teen_top100.txt", "weekly_teen_top100.txt"]


def test_tag_search_runs_core_calls_with_clamped_values():
    search = tags.TagSearch(None, lambda text: None, core_factory=FakeCore)
    assert sorted(search.search([["a"]], "15", delay=-1, threads=0)) == ["1", "3"]
    _kind, _groups, kwargs = search.core.calls[0]
    assert kwargs == {"delay": 0.0, "age_filter": "15", "mode": "GROUPS", "threads": 1}
    search.scrape_all(max_queries=10_000)
    assert search.core.calls[1][1]["max_queries"] == tags.DEFAULT_SEARCH_QUERY_COUNT
    search.stop()
    assert search.stopped


def test_novel_url_round_trips():
    assert extract_novel_id(novel_url(419368)) == "419368"
