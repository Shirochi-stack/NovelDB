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


def test_canonical_state_provenance_history_and_author_invalidation():
    old = {"original": RAW, "english": "I Die If I Cannot Debut"}
    state = {"source": "mpia", "records": {"1": {"title": RAW, "author": AUTHOR, "translations": {"title": old}}}}
    index = nu_titles.TitleIndex(cache())
    assert nu_titles.apply_state(state, index) == 1
    record = state["records"]["1"]
    assert record["translation_history"][0]["english"] == old["english"]
    assert record["translations"]["title"]["origin_url"] == URL
    assert metadata_pipeline.active_translation(record, "title") == "Debut or Die"
    assert nu_titles.apply_state(state, index) == 0
    record["author"] = "다른 작가"
    assert nu_titles.apply_state(state, index) == 1
    assert not metadata_pipeline.active_translation(record, "title")


@pytest.mark.parametrize("source", metadata_pipeline.SOURCES)
def test_prepare_removes_verified_titles_from_paid_api_input(tmp_path, source):
    state_dir, output_dir = tmp_path / "state", tmp_path / "data"
    state = metadata_common.empty_state(source)
    state["records"] = {"1": {"id": "1", "title": RAW, "author": AUTHOR},
                        "2": {"id": "2", "title": "별개의 소설", "author": AUTHOR}}
    metadata_common.save_state(state, state_dir)
    nu_titles.write_cache(state_dir / "nu_titles.json.gz", cache())
    metadata_pipeline.prepare(source, output_dir, state_dir, fields=("title",))
    master = metadata_pipeline.read_corpus(output_dir / f"{source}_titles_en.txt")
    pending = metadata_pipeline.read_corpus(output_dir / f"{source}_titles_untranslated.txt")
    assert master["1"][1] == "Debut or Die"
    assert set(pending) == {"2"}
    record = metadata_common.load_state(source, state_dir)["records"]["1"]
    assert record["translations"]["title"]["origin"] == "novelupdates"


def test_missing_cache_preserves_previously_verified_translation():
    state = {"source": "mpia", "records": {"1": {"title": RAW, "author": AUTHOR}}}
    nu_titles.apply_state(state, nu_titles.TitleIndex(cache()))
    assert nu_titles.apply_state(state, nu_titles.TitleIndex({"series": {}})) == 0
    assert metadata_pipeline.active_translation(state["records"]["1"], "title") == "Debut or Die"


@pytest.mark.parametrize("source", ("npia", "kpage", "sfc"))
def test_legacy_extraction_prefers_verified_titles(tmp_path, monkeypatch, source):
    from scripts import extract_titles
    data = cache()
    if source == "sfc":
        data["series"][URL]["language"] = "Chinese"
    index = nu_titles.TitleIndex(data)
    monkeypatch.setattr(nu_titles.TitleIndex, "load", classmethod(lambda cls: index))
    raw = tmp_path / "novels.json"
    master = tmp_path / "titles_en.txt"
    metadata_common.atomic_json(raw, [["1", RAW, AUTHOR], ["2", "수정된 제목", AUTHOR]])
    master.write_text(f"1|||{RAW}|||Machine Title\n2|||이전 제목|||Stale Title\n", encoding="utf-8")
    monkeypatch.setitem(extract_titles.SOURCES, source, {"data": str(raw), "en": str(master)})
    extract_titles.extract(source)
    corpus = metadata_pipeline.read_corpus(master)
    assert corpus["1"][1] == "Debut or Die"
    assert corpus["2"][1] == ""


def test_collector_resumes_failed_request_and_does_not_cache_missing(tmp_path):
    path = tmp_path / "cache.json.gz"
    listing = f'<div class="search_title"><a href="{URL}">Debut or Die</a></div>'
    calls = []

    def fetch(url):
        calls.append(url)
        return listing if "/opublisher/" in url else "<title>Just a moment</title>"

    with pytest.raises(RuntimeError, match="incomplete"):
        nu_titles.collect(("kpage",), path, 3, fetch=fetch)
    stored = nu_titles.read_cache(path)
    assert stored["series"] == {}
    progress = next(iter(stored["publishers"].values()))
    assert URL in progress["deferred"]
    progress["deferred"][URL]["retry_after"] = 0
    nu_titles.write_cache(path, stored)
    resumed = nu_titles.collect(("kpage",), path, 3, fetch=lambda url: HTML)
    assert resumed["series"][URL]["title"] == "Debut or Die"
    assert next(iter(resumed["publishers"].values()))["complete"]


def test_collector_continues_to_a_valid_series_after_an_unavailable_one(tmp_path):
    path = tmp_path / "cache.json.gz"
    failed = URL.replace("debut-or-die", "unavailable-series")
    listing = f'<div class="search_title"><a href="{failed}">Unavailable</a><a href="{URL}">Debut or Die</a></div>'

    def fetch(url):
        if "/opublisher/" in url:
            return listing
        return HTML if url == URL else "<title>Just a moment</title>"

    with pytest.raises(RuntimeError):
        nu_titles.collect(("kpage",), path, 4, fetch=fetch)
    stored = nu_titles.read_cache(path)
    assert stored["series"][URL]["title"] == "Debut or Die"
    assert failed not in stored["series"]


def test_apply_rebuilds_browser_bundles_and_preserves_raw_rows(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache_path = tmp_path / "cache.json.gz"
    nu_titles.write_cache(cache_path, cache())
    row = ["1", RAW, AUTHOR, "", [], 0, 0, 0, 0, "", 0, "", "", 0, {}, {}]
    rows = [row]
    metadata_common.atomic_json(data_dir / "kpage_novels.json", rows)
    (data_dir / "kpage_titles_en.txt").write_text(f"1|||{RAW}|||Old Machine Title\n\nlegacy|||incomplete\n", encoding="utf-8")
    payload = {"novels": rows, "translations": {"1": "Old Machine Title"}, "descriptions": {"1": "Synopsis"}}
    nu_titles.write_cache(data_dir / "kpage_chunk_0.json.gz", payload)
    metadata_common.atomic_json(data_dir / "kpage_chunk_manifest.json", {
        "files": ["kpage_chunk_0.json.gz"], "chunks": 1, "totalEntries": 1, "embedded": True,
    })
    report = nu_titles.apply_catalog("kpage", data_dir, tmp_path / "state", cache_path, rebuild=True)
    assert report["changed"] == report["matched"] == 1
    assert report["changes"]["1"]["previous_english"] == "Old Machine Title"
    assert (data_dir / "kpage_titles_en.txt").read_text(encoding="utf-8").endswith("\n\nlegacy|||incomplete\n")
    packed = json.loads(gzip.decompress((data_dir / "kpage_chunk_0.json.gz").read_bytes()))
    assert packed["novels"] == rows
    assert packed["descriptions"] == payload["descriptions"]
    assert packed["translations"]["1"] == "Debut or Die"
    preview = json.loads(gzip.decompress((data_dir / "kpage_chunk_preview_sorts.json.gz").read_bytes()))
    assert preview["translations"]["1"] == "Debut or Die"
    manifest = json.loads((data_dir / "kpage_chunk_manifest.json").read_text(encoding="utf-8"))
    assert manifest["chunkHashes"] == [hashlib.sha256((data_dir / manifest["files"][0]).read_bytes()).hexdigest()]
    before = (data_dir / "kpage_chunk_manifest.json").read_bytes()
    assert nu_titles.apply_catalog("kpage", data_dir, tmp_path / "state", cache_path, rebuild=True)["changed"] == 0
    assert (data_dir / "kpage_chunk_manifest.json").read_bytes() == before


def test_chinese_native_title_and_author():
    data = cache()
    record = data["series"][URL]
    record.update(language="Chinese", aliases=["测试小说"], authors=["作者甲"])
    index = nu_titles.TitleIndex(data)
    assert index.match("测试小说", "作者甲", "sfc")["title"] == "Debut or Die"
    assert index.match("测试小说续集", "作者甲", "sfc") is None
    assert index.match("测试小说", "作者乙", "sfc") is None
