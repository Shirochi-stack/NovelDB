import gzip
import hashlib
import json

import pytest

from scripts import metadata_common, metadata_pipeline, nu_titles

URL = "https://www.novelupdates.com/series/debut-or-die/"
RAW = "데뷔 못 하면 죽는 병 걸림"
AUTHOR = "백덕수"
HTML = f'''<link rel="canonical" href="{URL}">
<div class="seriestitlenu">Debut or Die</div>
<div id="editassociated">Other Title<br>데뷔 못하면 죽는병 걸림</div>
<div id="showauthors"><a>Baek Deok-su</a><a>{AUTHOR}</a></div>
<div id="showlang">Korean</div>'''


def cache():
    return {"version": 1, "series": {URL: nu_titles.parse_series(HTML, URL)}, "publishers": {}}


def test_snapshot_import_retains_live_titles_and_all_historical_aliases(tmp_path):
    stored = cache()
    stored["series"][URL]["nu_id"] = "45"
    target = tmp_path / "cache.json.gz"
    nu_titles.write_cache(target, stored)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps([
        {"id": 45, "name": "Old English Name", "original_language": "korean",
         "assoc_names": [RAW, "이전 제목"], "authors": [AUTHOR]},
        {"id": 46, "name": "Another Book", "original_language": "korean",
         "assoc_names": ["다른 작품"], "authors": ["다른 작가"]},
        {"id": 47, "name": "Malformed", "original_language": "korean", "assoc_names": "bad", "authors": []},
    ]), encoding="utf-8")
    report = nu_titles.import_snapshot(snapshot, target, "https://example.test/snapshot", "2025-08-21T00:00:00Z")
    loaded = nu_titles.read_cache(target)
    assert report["imported"] == 1
    assert len(loaded["series"]) == 2
    assert loaded["series"][URL]["title"] == "Debut or Die"
    index = nu_titles.TitleIndex(loaded)
    assert index.match("이전 제목", AUTHOR, "npia")["title"] == "Debut or Die"
    assert index.match("다른 작품", "다른 작가", "npia")["title"] == "Another Book"
    assert index.match("다른 작품", AUTHOR, "npia") is None
    assert loaded["series"]["https://www.novelupdates.com/?p=46"]["checked_at"] == "2025-08-21T00:00:00Z"
    assert report["snapshot_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()


def test_live_refresh_of_snapshot_id_keeps_historical_aliases_without_ambiguous_duplicates():
    stored = cache()
    stored["series"][URL]["nu_id"] = "45"
    historical = "https://www.novelupdates.com/?p=45"
    stored["series"][historical] = {**stored["series"][URL], "url": historical, "title": "Old English Title",
                                   "aliases": ["이전 제목"], "checked_at": "2025-08-21T00:00:00Z"}
    index = nu_titles.TitleIndex(stored)
    assert index.match("이전 제목", AUTHOR, "kpage")["title"] == "Debut or Die"
    assert index.match(RAW, AUTHOR, "kpage")["title"] == "Debut or Die"


def test_live_metadata_selectors_and_exact_matching():
    index = nu_titles.TitleIndex(cache())
    assert index.match(RAW, AUTHOR, "kpage")["title"] == "Debut or Die"
    for raw, author, source in [
        (RAW + " [단행본]", AUTHOR, "kpage"),
        (RAW + " : 라그나로크", AUTHOR, "kpage"),
        ("[" + RAW + "] 팬픽", AUTHOR, "npia"),
        (RAW, "다른 작가", "kpage"),
        (RAW, "", "kpage"),
        (RAW, AUTHOR, "sfc"),
        ("Other Title", AUTHOR, "kpage"),
    ]:
        assert index.match(raw, author, source) is None


def test_series_addresses_drop_tracking_and_preserve_valid_slugs():
    assert nu_titles.nu_url(URL + "?utm_source=search#reviews", series=True) == URL
    assert nu_titles.nu_url(URL.replace("debut-or-die", "a_title"), series=True).endswith("/a_title/")
    with pytest.raises(ValueError):
        nu_titles.nu_url("https://example.com/series/debut-or-die/", series=True)
    assert nu_titles.nu_url("https://www.novelupdates.com/?p=45524", series=True).endswith("?p=45524")
    record = nu_titles.parse_series(HTML + '<input id="mypostid" value="45524">',
                                   "https://www.novelupdates.com/?p=45524")
    assert record["url"] == URL
    assert record["nu_id"] == "45524"


def test_duplicate_alias_author_is_ambiguous():
    data = cache()
    second = URL.replace("debut-or-die", "another-edition")
    data["series"][second] = {**data["series"][URL], "url": second}
    assert nu_titles.TitleIndex(data).match(RAW, AUTHOR, "kpage") is None
    data["series"][second]["authors"] = ["다른 작가"]
    assert nu_titles.TitleIndex(data).match(RAW, AUTHOR, "kpage")["url"] == URL


def test_all_renamed_native_titles_match_the_same_series():
    html = HTML.replace("Other Title<br>데뷔 못하면 죽는병 걸림",
                        "<span>원래 <b>소설</b> 제목</span><br>바뀐 소설 제목<br>데뷔 못하면 죽는병 걸림")
    data = cache()
    data["series"][URL] = nu_titles.parse_series(html, URL)
    index = nu_titles.TitleIndex(data)
    for title in ("원래 소설 제목", "바뀐 소설 제목", RAW):
        assert index.match(title, AUTHOR, "kpage")["url"] == URL
    assert index.match("원래", AUTHOR, "kpage") is None


@pytest.mark.parametrize("author", ("[백덕수]", "[[백덕수]]", "[[[백덕수]]]", "백덕수.", "백덕수[다른필명]"))
def test_decorated_author_names_keep_exact_identity(author):
    index = nu_titles.TitleIndex(cache())
    assert index.match(RAW, author, "jara")["title"] == "Debut or Die"
    assert index.match(RAW, "[[다른작가]]", "jara") is None


def test_incomplete_challenged_and_wrong_identity_pages_are_rejected():
    for html in ["<title>Just a moment</title>", HTML.replace('class="seriestitlenu"', 'class="missing"'),
                 HTML.replace(URL, URL.replace("debut-or-die", "different-work"))]:
        with pytest.raises(ValueError):
            nu_titles.parse_series(html, URL)


def test_missing_native_metadata_is_cached_without_a_match():
    data = cache()
    data["series"][URL] = nu_titles.parse_series(HTML.replace('id="editassociated"', 'id="missing"'), URL)
    assert nu_titles.TitleIndex(data).match(RAW, AUTHOR, "kpage") is None


def test_title_that_sources_give_to_different_authors_is_not_taken_alone(tmp_path):
    data = cache()
    data["series"][URL]["authors"] = ["Baek Deok-su"]
    index = nu_titles.TitleIndex(data)
    metadata_common.atomic_json(tmp_path / "kpage_novels.json", [["1", RAW, AUTHOR], ["2", "다른 작품", "다른 작가"]])
    metadata_common.atomic_json(tmp_path / "nweb_novels.json", [["7", RAW, AUTHOR]])
    # The same author on two sources: one work, confirmed on each.
    holders = nu_titles.catalog_holders(index, tmp_path)
    assert [row[0] for row, match in index.match_rows([["1", RAW, AUTHOR]], "kpage", holders)] == ["1"]
    assert [row[0] for row, match in index.match_rows([["7", RAW, AUTHOR]], "nweb", holders)] == ["7"]
    # Another author's work of that title on any source leaves both unconfirmed.
    metadata_common.atomic_json(tmp_path / "nweb_novels.json", [["7", RAW, "다른 작가"]])
    holders = nu_titles.catalog_holders(index, tmp_path)
    assert list(index.match_rows([["1", RAW, AUTHOR]], "kpage", holders)) == []
    assert list(index.match_rows([["7", RAW, "다른 작가"]], "nweb", holders)) == []
    # The other language's catalog is a different title space.
    metadata_common.atomic_json(tmp_path / "sfc_novels.json", [["9", RAW, "다른 작가"]])
    metadata_common.atomic_json(tmp_path / "nweb_novels.json", [["7", RAW, AUTHOR]])
    holders = nu_titles.catalog_holders(index, tmp_path)
    assert [row[0] for row, match in index.match_rows([["1", RAW, AUTHOR]], "kpage", holders)] == ["1"]


def verified(original=RAW, english="Debut or Die"):
    """A title translation as written while verified titles replaced machine titles."""
    return {"original": original, "english": english, "author": AUTHOR, "origin": "novelupdates",
            "origin_url": URL, "translated_at": "2026-11-10T00:00:00Z"}


def test_restore_returns_the_machine_title_from_the_backup_then_history_else_retranslates():
    machine = {"original": RAW, "english": "I Die If I Cannot Debut", "translated_at": "2026-01-01T00:00:00Z"}
    state = {"source": "mpia", "records": {
        # 1: its own history holds the machine title; 2: only the backup does; 3: neither.
        "1": {"title": RAW, "author": AUTHOR, "translations": {"title": verified()},
              "translation_history": [{**machine, "field": "title", "replaced_at": "2026-11-10T00:00:00Z"}]},
        "2": {"title": RAW, "author": AUTHOR, "translations": {"title": verified()}},
        "3": {"title": RAW, "author": AUTHOR, "translations": {"title": verified()},
              # A title translated for an earlier original is not this title's machine translation.
              "translation_history": [{"original": "이전 제목", "english": "Earlier", "field": "title"}]},
        "4": {"title": RAW, "author": AUTHOR, "translations": {"title": dict(machine)}},
    }}
    restore = {"mpia": {"2": {"original": RAW, "english": "Backup Machine Title"},
                        "3": {"original": "이전 제목", "english": "Stale Backup"}}}
    changed, pending = nu_titles.restore_state(state, restore)
    assert (changed, pending) == (3, {"3"})
    records = state["records"]
    assert records["1"]["translations"]["title"] == machine
    assert metadata_pipeline.active_translation(records["2"], "title") == "Backup Machine Title"
    assert "origin" not in records["2"]["translations"]["title"]
    assert "title" not in records["3"]["translations"]
    assert records["4"]["translations"]["title"] == machine
    # The verified title stays in the record's history, marked as reverted.
    assert all(records[nid]["translation_history"][-1]["origin_url"] == URL
               and records[nid]["translation_history"][-1]["reverted_at"] for nid in "123")
    assert nu_titles.restore_state(state, restore) == (0, set())


@pytest.mark.parametrize("source", metadata_pipeline.SOURCES)
def test_prepare_translates_every_title_and_never_takes_a_verified_one(tmp_path, source):
    state_dir, output_dir = tmp_path / "state", tmp_path / "data"
    state = metadata_common.empty_state(source)
    state["records"] = {"1": {"id": "1", "title": RAW, "author": AUTHOR, "translations": {"title": verified()}},
                        "2": {"id": "2", "title": "별개의 소설", "author": AUTHOR},
                        "3": {"id": "3", "title": RAW, "author": AUTHOR}}
    metadata_common.save_state(state, state_dir)
    nu_titles.write_cache(state_dir / "nu_titles.json.gz", cache())
    # The master corpus still shows the verified title for 1: it must not be taken back as a translation.
    output_dir.mkdir()
    (output_dir / f"{source}_titles_en.txt").write_text(f"1|||{RAW}|||Debut or Die\n", encoding="utf-8")
    metadata_pipeline.prepare(source, output_dir, state_dir, fields=("title",))
    master = metadata_pipeline.read_corpus(output_dir / f"{source}_titles_en.txt")
    pending = metadata_pipeline.read_corpus(output_dir / f"{source}_titles_untranslated.txt")
    assert master["1"][1] == "" and set(pending) == {"1", "2", "3"}
    records = metadata_common.load_state(source, state_dir)["records"]
    assert not any(record.get("translations", {}).get("title") for record in records.values())
    # Its translation arrives like any other title's.
    (output_dir / f"{source}_titles_untranslated.txt").write_text(f"1|||{RAW}|||A Machine Title\n", encoding="utf-8")
    metadata_pipeline.merge(source, output_dir, state_dir)
    record = metadata_common.load_state(source, state_dir)["records"]["1"]
    assert metadata_pipeline.active_translation(record, "title") == "A Machine Title"


@pytest.mark.parametrize("source", ("npia", "kpage", "sfc"))
def test_legacy_extraction_keeps_machine_titles(tmp_path, monkeypatch, source):
    from scripts import extract_titles
    data = cache()
    if source == "sfc":
        data["series"][URL]["language"] = "Chinese"
    index = nu_titles.TitleIndex(data)
    monkeypatch.setattr(nu_titles.TitleIndex, "load", classmethod(lambda cls: index))
    raw = tmp_path / "novels.json"
    master = tmp_path / "titles_en.txt"
    metadata_common.atomic_json(raw, [["1", RAW, AUTHOR], ["2", "수정된 제목", AUTHOR], ["3", RAW, AUTHOR]])
    master.write_text(f"1|||{RAW}|||Machine Title\n2|||이전 제목|||Stale Title\n", encoding="utf-8")
    monkeypatch.setitem(extract_titles.SOURCES, source, {"data": str(raw), "en": str(master)})
    extract_titles.extract(source)
    corpus = metadata_pipeline.read_corpus(master)
    assert corpus["1"][1] == "Machine Title"
    assert corpus["2"][1] == "" and corpus["3"][1] == ""


def listing(*entries, next_page=None):
    """A listing page of (series number, address) entries, as the language listings show them."""
    boxes = "".join(f'<div class="search_title"><span class="rl_icons_en" id="sid{number}"></span>'
                    f'<a href="{url}">Title</a></div>' for number, url in entries)
    pager = f'<div class="digg_pagination"><a class="next_page" rel="next" href="{next_page}"> →</a></div>'
    return boxes + (pager if next_page else "")


def series_page(url, title, number):
    return HTML.replace(URL, url).replace("Debut or Die", title) + f'<input id="mypostid" value="{number}">'


def test_collector_resumes_failed_request_and_does_not_cache_missing(tmp_path):
    path = tmp_path / "cache.json.gz"

    def fetch(url):
        return listing((45, URL)) if "/language/" in url else "<title>Just a moment</title>"

    with pytest.raises(RuntimeError, match="incomplete"):
        nu_titles.collect(("kpage",), path, 3, fetch=fetch)
    stored = nu_titles.read_cache(path)
    assert stored["series"] == {}
    assert stored["deferred"][URL]["failures"] == 1
    # A page that failed is not queued again by the listings: it waits for its retry.
    assert all(progress["complete"] and not progress["pending"] for progress in stored["listings"].values())
    stored["deferred"][URL]["retry_after"] = 0
    nu_titles.write_cache(path, stored)
    with pytest.raises(RuntimeError):
        nu_titles.collect(("kpage",), path, 3, fetch=fetch)
    stored = nu_titles.read_cache(path)
    # Each failure waits longer than the last.
    assert stored["deferred"][URL]["failures"] == 2
    stored["deferred"][URL]["retry_after"] = 0
    nu_titles.write_cache(path, stored)
    resumed = nu_titles.collect(("kpage",), path, 3, fetch=lambda url: HTML)
    assert resumed["series"][URL]["title"] == "Debut or Die"
    assert resumed["deferred"] == {}


def test_collector_continues_to_a_valid_series_after_an_unavailable_one(tmp_path):
    path = tmp_path / "cache.json.gz"
    failed = URL.replace("debut-or-die", "unavailable-series")

    def fetch(url):
        if "/language/" in url:
            return listing((44, failed), (45, URL))
        return HTML if url == URL else "<title>Just a moment</title>"

    with pytest.raises(RuntimeError):
        nu_titles.collect(("kpage",), path, 4, fetch=fetch)
    stored = nu_titles.read_cache(path)
    assert stored["series"][URL]["title"] == "Debut or Die"
    assert failed not in stored["series"]


def test_sweeps_follow_each_language_listing_and_read_only_series_never_held(tmp_path):
    path = tmp_path / "cache.json.gz"
    snapshot = "https://www.novelupdates.com/?p=45"
    stored = cache()
    # Held from a snapshot under its number alone: the listing names it by address.
    stored["series"] = {snapshot: {**stored["series"][URL], "url": snapshot, "nu_id": "45",
                                   "checked_at": "2025-08-21T00:00:00Z"}}
    nu_titles.write_cache(path, stored)
    second, third = (URL.replace("debut-or-die", slug) for slug in ("second-work", "third-work"))
    recent = "https://www.novelupdates.com/language/korean/?sort=8&order=2&status=1"
    by_title = "https://www.novelupdates.com/language/korean/?sort=7&order=1&status=1"
    pages = {
        recent: listing((45, URL), (46, second), next_page="//www.novelupdates.com/language/korean/"
                                                           "?sort=8&order=2&status=1&pg=2"),
        recent + "&pg=2": listing((47, third)),
        by_title: listing((45, URL), (46, second), (47, third)),
        second: series_page(second, "Second Work", 46), third: series_page(third, "Third Work", 47),
    }
    calls = []

    def fetch(url):
        calls.append(url)
        return pages[url]

    result = nu_titles.collect(("npia", "kpage"), path, 20, fetch=fetch)
    # The recently updated first, page by page; then every series by title, with nothing left to read.
    assert calls == [recent, second, recent + "&pg=2", third, by_title]
    assert set(result["series"]) == {snapshot, second, third}
    assert result["series"][third]["nu_id"] == "47"
    assert set(result["listings"]) == {"Korean/recent", "Korean/all"}
    assert all(progress["complete"] for progress in result["listings"].values())
    # Nothing is due again the same day.
    calls.clear()
    nu_titles.collect(("npia",), path, 20, fetch=fetch)
    assert calls == []
    # The other language has its own listing.
    with pytest.raises(RuntimeError, match="language/chinese/"):
        nu_titles.collect(("sfc",), path, 1, fetch=lambda url: "<title>Just a moment</title>")


def test_recent_sweep_ends_once_its_pages_hold_nothing_new(tmp_path, monkeypatch):
    path = tmp_path / "cache.json.gz"
    stored = cache()
    stored["series"][URL]["nu_id"] = "45"
    nu_titles.write_cache(path, stored)
    monkeypatch.setattr(nu_titles, "ORDERS", {"recent": nu_titles.ORDERS["recent"]})
    calls = []

    def fetch(url):
        calls.append(url)
        return listing((45, URL), next_page=f"//www.novelupdates.com/language/korean/?pg={len(calls) + 1}")

    result = nu_titles.collect(("npia",), path, 50, fetch=fetch)
    assert len(calls) == nu_titles.QUIET_PAGES
    assert result["listings"]["Korean/recent"]["complete"]


def test_monthly_sweep_reads_old_series_again_from_its_second_pass(tmp_path, monkeypatch):
    path = tmp_path / "cache.json.gz"
    snapshot = "https://www.novelupdates.com/?p=45"
    stored = cache()
    stored["series"] = {snapshot: {**stored["series"][URL], "url": snapshot, "nu_id": "45", "aliases": ["이전 제목"],
                                   "checked_at": "2025-08-21T00:00:00Z"}}
    stored["parser_version"] = nu_titles.PARSER_VERSION
    stored["listings"] = {"Korean/all": {"next": None, "pending": [], "complete": True, "completed_at": 1}}
    nu_titles.write_cache(path, stored)
    monkeypatch.setattr(nu_titles, "ORDERS", {"all": nu_titles.ORDERS["all"]})
    calls = []

    def fetch(url):
        calls.append(url)
        return listing((45, URL)) if "/language/" in url else series_page(URL, "Debut or Die", 45)

    result = nu_titles.collect(("npia",), path, 5, fetch=fetch)
    assert calls[1:] == [URL]
    index = nu_titles.TitleIndex(result)
    # The fresh page and the snapshot are one series: both titles lead to its address.
    assert index.match(RAW, AUTHOR, "npia")["url"] == index.match("이전 제목", AUTHOR, "npia")["url"] == URL
    assert result["listings"]["Korean/all"]["completed_at"] > 1


def test_collection_stops_when_every_request_is_turned_away(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return "<title>Just a moment</title>"

    # The failure names the page that came back, which tells a challenge from a changed layout.
    with pytest.raises(RuntimeError, match='incomplete.*listing page \\(page titled "Just a moment"\\)'):
        nu_titles.collect(("npia", "sfc"), tmp_path / "cache.json.gz", 100, fetch=fetch)
    assert len(calls) == nu_titles.TURNED_AWAY
    with pytest.raises(ValueError, match="untitled page"):
        nu_titles.parse_series("<p>Nothing</p>", URL)


def test_long_title_held_once_matches_a_series_without_a_native_author():
    data = cache()
    record = data["series"][URL]
    record["authors"] = ["Baek Deok-su"]
    index = nu_titles.TitleIndex(data)
    assert index.match(RAW, AUTHOR, "kpage") is None
    assert [row[0] for row, match in index.match_rows([["1", RAW, AUTHOR], ["2", "다른 작품", AUTHOR]], "kpage")
            if match["url"] == URL] == ["1"]
    # Two authors hold that title in the catalog: the series cannot say which work it is.
    assert list(index.match_rows([["1", RAW, AUTHOR], ["2", RAW, "다른 작가"]], "kpage")) == []
    # One author's two editions are the same work.
    assert len(list(index.match_rows([["1", RAW, AUTHOR], ["2", RAW, f"[{AUTHOR}]"]], "kpage"))) == 2
    # No author to compare is not enough for a short title, which many works share.
    record["aliases"] = ["짧은 제목"]
    assert list(nu_titles.TitleIndex(data).match_rows([["1", "짧은 제목", AUTHOR]], "kpage")) == []
    # A native author that differs is never set aside, nor are two series of one title.
    data = cache()
    assert list(nu_titles.TitleIndex(data).match_rows([["1", RAW, "다른 작가"]], "kpage")) == []
    data["series"][URL]["authors"] = []
    second = URL.replace("debut-or-die", "another-edition")
    data["series"][second] = {**data["series"][URL], "url": second}
    assert list(nu_titles.TitleIndex(data).match_rows([["1", RAW, AUTHOR]], "kpage")) == []


def test_record_keeps_addresses_and_restores_machine_titles_in_corpus_and_bundles(tmp_path):
    data_dir, state_dir = tmp_path / "data", tmp_path / "state"
    data_dir.mkdir()
    cache_path = tmp_path / "cache.json.gz"
    nu_titles.write_cache(cache_path, cache())
    row = lambda nid: [nid, RAW, AUTHOR, "", [], 0, 0, 0, 0, "", 0, "", "", 0, {}, {}]
    # 1 and 2 show the verified title from the earlier replacement (2 has no backup); 3 already shows
    # a machine title; 4 was translated to the same words by the machine.
    rows = [row("1"), row("2"), row("3"), row("4")]
    metadata_common.atomic_json(data_dir / "kpage_novels.json", rows)
    (data_dir / "kpage_titles_en.txt").write_text(
        f"1|||{RAW}|||Debut or Die\n2|||{RAW}|||Debut or Die\n3|||{RAW}|||Newer Machine Title\n"
        f"4|||{RAW}|||Debut or Die\n\nlegacy|||incomplete\n", encoding="utf-8")
    payload = {"novels": rows, "translations": {"1": "Debut or Die", "2": "Debut or Die", "3": "Newer Machine Title",
                                                "4": "Debut or Die"}, "descriptions": {"1": "Synopsis"}}
    nu_titles.write_cache(data_dir / "kpage_chunk_0.json.gz", payload)
    metadata_common.atomic_json(data_dir / "kpage_chunk_manifest.json", {
        "files": ["kpage_chunk_0.json.gz"], "chunks": 1, "totalEntries": 4, "embedded": True,
    })
    nu_titles.write_cache(state_dir / nu_titles.RESTORE_NAME, {"version": 1, "sources": {"kpage": {
        "1": {"original": RAW, "english": "Old Machine Title"}, "3": {"original": RAW, "english": "Old Machine Title"},
        "4": {"original": RAW, "english": "Debut or Die"}}}})
    report = nu_titles.record_catalog("kpage", data_dir, state_dir, cache_path, rebuild=True)
    assert (report["matched"], report["restored"], report["pending"]) == (4, 1, 1)
    assert report["changes"]["1"] == {"original": RAW, "author": AUTHOR, "english": "Debut or Die", "url": URL,
                                      "previous_english": "Debut or Die", "restored": "Old Machine Title"}
    corpus = metadata_pipeline.read_corpus(data_dir / "kpage_titles_en.txt")
    # 2 is emptied for translation; a machine title that is already shown is never overwritten.
    assert [corpus[nid][1] for nid in "1234"] == ["Old Machine Title", "", "Newer Machine Title", "Debut or Die"]
    assert (data_dir / "kpage_titles_en.txt").read_text(encoding="utf-8").endswith("\n\nlegacy|||incomplete\n")
    packed = json.loads(gzip.decompress((data_dir / "kpage_chunk_0.json.gz").read_bytes()))
    assert packed["novels"] == rows
    assert packed["descriptions"] == payload["descriptions"]
    assert packed["translations"] == {"1": "Old Machine Title", "2": "Debut or Die", "3": "Newer Machine Title",
                                      "4": "Debut or Die"}
    preview = json.loads(gzip.decompress((data_dir / "kpage_chunk_preview_sorts.json.gz").read_bytes()))
    assert preview["translations"]["1"] == "Old Machine Title"
    manifest = json.loads((data_dir / "kpage_chunk_manifest.json").read_text(encoding="utf-8"))
    assert manifest["chunkHashes"] == [hashlib.sha256((data_dir / manifest["files"][0]).read_bytes()).hexdigest()]
    # The verified title and its address stay recorded as gzip beside the published state.
    addresses = state_dir / "nu_title_addresses.json.gz"
    assert json.loads(gzip.decompress(addresses.read_bytes())) == {
        "version": 1, "sources": {"kpage": {nid: {"title": "Debut or Die", "url": URL} for nid in "1234"}}}
    before = (data_dir / "kpage_chunk_manifest.json").read_bytes()
    stored, text = addresses.read_bytes(), (data_dir / "kpage_titles_en.txt").read_bytes()
    again = nu_titles.record_catalog("kpage", data_dir, state_dir, cache_path, rebuild=True)
    assert (again["restored"], again["pending"], again["changes"]) == (0, 0, {})
    assert (data_dir / "kpage_chunk_manifest.json").read_bytes() == before
    assert addresses.read_bytes() == stored and (data_dir / "kpage_titles_en.txt").read_bytes() == text
    # Another source adds its own entry; a work that no longer matches leaves the file.
    nu_titles.write_addresses(addresses, "npia", {"9": {"english": "Other", "url": URL}})
    nu_titles.write_addresses(addresses, "kpage", {})
    assert json.loads(gzip.decompress(addresses.read_bytes()))["sources"] == {
        "kpage": {}, "npia": {"9": {"title": "Other", "url": URL}}}


def test_chinese_native_title_and_author():
    data = cache()
    record = data["series"][URL]
    record.update(language="Chinese", aliases=["测试小说"], authors=["作者甲"])
    index = nu_titles.TitleIndex(data)
    assert index.match("测试小说", "作者甲", "sfc")["title"] == "Debut or Die"
    assert index.match("测试小说续集", "作者甲", "sfc") is None
    assert index.match("测试小说", "作者乙", "sfc") is None
