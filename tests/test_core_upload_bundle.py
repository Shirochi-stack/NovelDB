"""Upload bundling: packing, ZIP names and contents, upload names, the settings and the whole flow on a fake host."""

import io
import json
import os
import zipfile
from dataclasses import replace

import pytest
from upload_fakes import FakeHost, install, make_file

from noveldb.core.settings import desktop_config as dc
from noveldb.core.settings.model import (
    UPLOAD_ZIP_MB_DEFAULT,
    UPLOAD_ZIP_MB_MAX,
    UPLOAD_ZIP_MB_MIN,
    AppSettings,
)
from noveldb.core.upload import UploadContext, UploadItem, catalog, links_text, plan_bundles, upload_items
from noveldb.core.upload.bundle import (
    ENTRY_OVERHEAD,
    MEGABYTE,
    build_zip,
    bundle_zip_name,
    describe,
    entry_compression,
    unique_entry_names,
)
from noveldb.core.upload.hosts import UploadResult
from noveldb.core.upload.transfer import UploadCancelled, ascii_upload_name, book_id_from_url

KOREAN = "재앙급 진조는 방구석에서 꿀 빱니다.epub"


def items_of(folder, sizes, ext=".epub", prefix="book"):
    return [UploadItem(make_file(folder, f"{prefix}{i}{ext}", size), book_id=str(1000 + i), source="npia")
            for i, size in enumerate(sizes)]


def names_of(bundles):
    return [[os.path.basename(item.path) for item in bundle.items] for bundle in bundles]


# -- packing -------------------------------------------------------------------------------------------


def test_first_fit_decreasing_packs_into_as_few_zips_as_possible(tmp_path):
    items = items_of(tmp_path, [5000, 3000, 4000, 2000, 6000])
    bundles = plan_bundles(items, 10_000)
    # Largest first, each file costing 1 KB extra: 6000 opens ZIP 1; 5000 does not fit with it and opens ZIP 2;
    # 4000 fits neither and opens ZIP 3, which takes 3000; 2000 then fits ZIP 2. ZIP 1 holds only 6000, so it is the file.
    assert names_of(bundles) == [["book0.epub", "book3.epub"], ["book1.epub", "book2.epub"], ["book4.epub"]]
    assert [bundle.zip_name for bundle in bundles] == ["NovelDB_2_EPUBs.zip", "NovelDB_2_EPUBs_2.zip", ""]
    for bundle in bundles:
        assert bundle.size + ENTRY_OVERHEAD * len(bundle.items) <= 10_000


def test_every_file_is_planned_exactly_once(tmp_path):
    items = items_of(tmp_path, [900, 4000, 700, 2500, 3300, 100, 5200, 800, 1500])
    bundles = plan_bundles(items, 6000)
    planned = [item.path for bundle in bundles for item in bundle.items]
    assert sorted(planned) == sorted(item.path for item in items)
    assert all(bundle.size + ENTRY_OVERHEAD * len(bundle.items) <= 6000 for bundle in bundles if bundle.is_zip)


def test_a_file_larger_than_the_limit_uploads_alone_and_the_rest_are_zipped(tmp_path):
    items = items_of(tmp_path, [9000, 1000, 1500, 2000])
    bundles = plan_bundles(items, 8000)
    assert names_of(bundles) == [["book0.epub"], ["book1.epub", "book2.epub", "book3.epub"]]
    assert [bundle.is_zip for bundle in bundles] == [False, True]
    assert bundles[1].zip_name == "NovelDB_3_EPUBs.zip"


def test_a_file_that_only_fits_without_the_overhead_also_goes_alone(tmp_path):
    items = items_of(tmp_path, [4500, 100, 200])
    bundles = plan_bundles(items, 5000)  # 4500 + 1 KB of ZIP overhead is over 5000, so that book uploads as itself
    assert [bundle.is_zip for bundle in bundles] == [False, True]
    assert names_of(bundles) == [["book0.epub"], ["book1.epub", "book2.epub"]]


def test_a_zip_that_would_hold_one_file_is_uploaded_as_that_file(tmp_path):
    items = items_of(tmp_path, [4000, 4000, 4000])
    bundles = plan_bundles(items, 6000)  # each fits a ZIP alone, no two fit together
    assert [bundle.zip_name for bundle in bundles] == ["", "", ""]
    assert names_of(bundles) == [["book0.epub"], ["book1.epub"], ["book2.epub"]]


def test_bundling_off_or_a_single_file_changes_nothing(tmp_path):
    items = items_of(tmp_path, [10, 20, 30])
    off = plan_bundles(items, 10_000_000, enabled=False)
    assert [bundle.zip_name for bundle in off] == ["", "", ""] and len(off) == 3
    one = plan_bundles(items[:1], 10_000_000)
    assert len(one) == 1 and not one[0].is_zip


def test_eleven_books_become_a_few_links(tmp_path):
    items = items_of(tmp_path, [90_000] * 11)
    bundles = plan_bundles(items, int(0.3 * MEGABYTE))  # three per ZIP
    assert [len(bundle.items) for bundle in bundles] == [3, 3, 3, 2]
    assert [bundle.zip_name for bundle in bundles] == [
        "NovelDB_3_EPUBs.zip", "NovelDB_3_EPUBs_2.zip", "NovelDB_3_EPUBs_3.zip", "NovelDB_2_EPUBs.zip"]


def test_folder_items_count_as_one_file_of_their_size(tmp_path):
    folder = tmp_path / "Book (TXT)"
    folder.mkdir()
    make_file(folder, "001.txt", 700)
    make_file(folder, "002.txt", 800)
    book = UploadItem(make_file(tmp_path, "b.epub", 500))
    bundles = plan_bundles([UploadItem(str(folder)), book], 100_000)
    assert len(bundles) == 1 and bundles[0].size == 2000 and bundles[0].zip_name == "NovelDB_2_files.zip"


# -- ZIP names -----------------------------------------------------------------------------------------


def test_zip_names_follow_the_entries_and_repeat_with_a_counter(tmp_path):
    epubs = items_of(tmp_path, [1, 1, 1, 1])
    mixed = epubs[:2] + [UploadItem(make_file(tmp_path, "notes.txt", 1))]
    taken = set()
    assert bundle_zip_name(epubs, taken) == "NovelDB_4_EPUBs.zip"
    assert bundle_zip_name(epubs, taken) == "NovelDB_4_EPUBs_2.zip"
    assert bundle_zip_name(epubs, taken) == "NovelDB_4_EPUBs_3.zip"
    assert bundle_zip_name(mixed, taken) == "NovelDB_3_files.zip"
    assert bundle_zip_name(mixed, taken) == "NovelDB_3_files_2.zip"


def test_zip_names_are_plain_ascii_and_epub_detection_ignores_case(tmp_path):
    shouting = [UploadItem(make_file(tmp_path, "A.EPUB", 1)), UploadItem(make_file(tmp_path, KOREAN, 1))]
    name = bundle_zip_name(shouting, set())
    assert name == "NovelDB_2_EPUBs.zip" and name.isascii()


def test_describe_says_what_a_zip_holds():
    assert describe("NovelDB_3_EPUBs.zip", 3, True) == "NovelDB_3_EPUBs.zip (3 books)"
    assert describe("NovelDB_3_files.zip", 3, False) == "NovelDB_3_files.zip (3 files)"
    assert describe("Book.epub", 1, True) == "Book.epub"


def test_entry_names_stay_distinct_inside_one_zip():
    assert unique_entry_names(["a.epub", "A.epub", "b.epub", "a.epub"]) == [
        "a.epub", "A (2).epub", "b.epub", "a (3).epub"]


# -- ZIP contents (a real ZIP, written and read back) ----------------------------------------------------


def test_zip_keeps_utf8_names_and_stores_compressed_formats(tmp_path):
    files = {
        KOREAN: b"EPUB" * 500,
        "만화 1화.cbz": b"CBZ" * 500,
        "문서.pdf": b"%PDF" * 500,
        "nested.zip": b"ZIP" * 500,
        "챕터 001.txt": ("가나다라 " * 2000).encode("utf-8"),
        "page.html": b"<p>hello</p>" * 500,
    }
    items = [UploadItem(make_file(tmp_path, name, 1, fill=data)) for name, data in files.items()]
    for item, data in zip(items, files.values()):  # real contents, not filler
        with open(item.path, "wb") as handle:
            handle.write(data)
    out = tmp_path / "out"
    out.mkdir()
    bundle = plan_bundles(items, 10 * MEGABYTE)[0]
    path = build_zip(bundle, str(out))
    assert os.path.basename(path) == "NovelDB_6_files.zip" and os.listdir(out) == ["NovelDB_6_files.zip"]

    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == list(files)  # original names, no folders, same order
        for name, data in files.items():
            info = archive.getinfo(name)
            assert archive.read(name) == data
            assert info.flag_bits & 0x800  # the UTF-8 name flag
            stored = name.lower().endswith((".epub", ".cbz", ".pdf", ".zip"))
            assert info.compress_type == (zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED), name
            assert (info.compress_size == info.file_size) == stored
    raw = open(path, "rb").read()
    assert KOREAN.encode("utf-8") in raw  # the bytes are UTF-8, not a legacy code page


def test_entry_compression_depends_on_the_extension():
    assert entry_compression("A.EPUB") == zipfile.ZIP_STORED
    assert entry_compression("a.CBZ") == zipfile.ZIP_STORED
    assert entry_compression("a.txt") == zipfile.ZIP_DEFLATED
    assert entry_compression("a.html") == zipfile.ZIP_DEFLATED


def test_zip_with_a_folder_item_nests_that_folders_zip_stored(tmp_path):
    folder = tmp_path / "책 (TXT)"
    folder.mkdir()
    make_file(folder, "001.txt", 300)
    make_file(folder, "002.txt", 300)
    items = [UploadItem(str(folder)), UploadItem(make_file(tmp_path, "b.epub", 400))]
    out = tmp_path / "out"
    out.mkdir()
    path = build_zip(plan_bundles(items, 100_000)[0], str(out))
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["책 (TXT).zip", "b.epub"]
        assert archive.getinfo("책 (TXT).zip").compress_type == zipfile.ZIP_STORED
        inner = zipfile.ZipFile(io.BytesIO(archive.read("책 (TXT).zip")))
        assert inner.namelist() == ["책 (TXT)/001.txt", "책 (TXT)/002.txt"]


def test_two_files_with_one_name_do_not_overwrite_each_other_in_the_zip(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    items = [UploadItem(make_file(first, "Book.epub", 10, b"1")), UploadItem(make_file(second, "Book.epub", 10, b"2"))]
    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(build_zip(plan_bundles(items, 100_000)[0], str(out))) as archive:
        assert archive.namelist() == ["Book.epub", "Book (2).epub"]
        assert archive.read("Book.epub") == b"1" * 10 and archive.read("Book (2).epub") == b"2" * 10


def test_building_a_zip_reports_progress_and_cancel_removes_the_partial_file(tmp_path):
    items = items_of(tmp_path, [3 * MEGABYTE, 3 * MEGABYTE])
    bundle = plan_bundles(items, 100 * MEGABYTE)[0]
    out = tmp_path / "out"
    out.mkdir()
    seen = []
    build_zip(bundle, str(out), lambda done, total: seen.append((done, total)))
    assert seen[-1] == (6 * MEGABYTE, 6 * MEGABYTE) and seen == sorted(seen)

    calls = []

    def stop_after_first_chunk():
        calls.append(1)
        return len(calls) > 1

    os.remove(out / bundle.zip_name)
    with pytest.raises(UploadCancelled):
        build_zip(bundle, str(out), stopped=stop_after_first_chunk)
    assert os.listdir(out) == []


# -- upload names (the phone showed /epub, /-_.epub, /VR_.epub) ---------------------------------------------


@pytest.mark.parametrize("title", [
    KOREAN, "VR 게임.epub", "- 소설.epub", "1 게임 2.epub", "한글.epub", "A1.epub", "-_.epub",
    "100일 후에 거세 당하는 황녀님.epub", "1234.epub",
])
def test_titles_that_lose_their_letters_upload_as_source_and_book_id(tmp_path, title):
    assert ascii_upload_name(str(tmp_path / title), "123456", "npia") == "npia_123456.epub"


def test_titles_keep_their_ascii_part_when_it_is_enough(tmp_path):
    assert ascii_upload_name(str(tmp_path / "My Book.epub"), "1", "npia") == "My Book.epub"
    assert ascii_upload_name(str(tmp_path / "Dragon 용 Slayer.epub"), "1", "npia") == "Dragon_Slayer.epub"
    assert ascii_upload_name(str(tmp_path / "재앙 Reborn 2부.txt"), "1", "npia") == "Reborn 2.txt"
    assert ascii_upload_name(str(tmp_path / "NovelDB_3_EPUBs.zip"), "", "") == "NovelDB_3_EPUBs.zip"


def test_names_without_a_source_use_the_book_id_alone(tmp_path):
    assert ascii_upload_name(str(tmp_path / KOREAN), "419368") == "419368.epub"
    assert ascii_upload_name(str(tmp_path / KOREAN), "") == "book.epub"
    assert ascii_upload_name(str(tmp_path / "한글.epub"), "a b/c", "global_npia") == "global_npia_a-b-c.epub"


def test_book_ids_come_from_the_path_then_the_query():
    assert book_id_from_url("https://example.com/novel/419368") == "419368"
    assert book_id_from_url("https://example.com/viewer?novelId=555") == "555"
    assert book_id_from_url("https://example.com/read/12/chapter/34?x=9") == "34"
    assert book_id_from_url("https://example.com/book/abc-def") == "book-abc-def"


def test_lone_files_of_a_bulk_upload_get_source_and_id_names(tmp_path, monkeypatch):
    """The bulk path must name files like the single path does (Korean titles reached the host as '/epub')."""
    host = install(monkeypatch, FakeHost())
    items = [
        UploadItem(make_file(tmp_path, KOREAN, 20), book_id="419368", source="npia"),
        UploadItem(make_file(tmp_path, "VR 게임.epub", 20), book_id="77", source="kpage"),
        UploadItem(make_file(tmp_path, "Plain Title.epub", 20), book_id="5", source="sfc"),
    ]
    upload_items(items, ["fake"], UploadContext(), bundle=False)
    assert [sent.name for sent in host.sent] == ["npia_419368.epub", "kpage_77.epub", "Plain Title.epub"]


# -- settings ----------------------------------------------------------------------------------------


def test_zip_settings_default_on_with_one_gigabyte():
    up = AppSettings().upload
    assert up.zip_bundle is True and up.zip_max_mb == 1024 == UPLOAD_ZIP_MB_DEFAULT
    assert (UPLOAD_ZIP_MB_MIN, UPLOAD_ZIP_MB_MAX) == (50, 4096)
    exported = dc.export_config(AppSettings())
    assert exported["upload_zip_bundle"] is True and exported["upload_zip_max_mb"] == 1024


def test_zip_settings_round_trip():
    s = AppSettings()
    s = replace(s, upload=replace(s.upload, zip_bundle=False, zip_max_mb=300))
    out = dc.export_config(s)
    assert out["upload_zip_bundle"] is False and out["upload_zip_max_mb"] == 300
    again = dc.import_config(json.loads(json.dumps(out)))
    assert again.upload.zip_bundle is False and again.upload.zip_max_mb == 300
    assert dc.export_config(again)["upload_zip_max_mb"] == 300


def test_zip_settings_missing_keep_the_defaults_and_odd_values_are_clamped():
    assert dc.import_config({}).upload.zip_max_mb == 1024 and dc.import_config({}).upload.zip_bundle is True
    base = dc.import_config({"upload_zip_max_mb": 10, "upload_zip_bundle": False})
    assert base.upload.zip_max_mb == 50 and base.upload.zip_bundle is False
    assert dc.import_config({"upload_zip_bundle": "false"}).upload.zip_bundle is True  # only real booleans count
    assert dc.import_config({"upload_zip_max_mb": 99999}).upload.zip_max_mb == 4096
    assert dc.import_config({"upload_zip_max_mb": "2048"}).upload.zip_max_mb == 2048
    assert dc.import_config({"upload_zip_max_mb": "lots"}).upload.zip_max_mb == 1024
    assert dc.import_config({"upload_zip_max_mb": None}).upload.zip_max_mb == 1024


# -- the limit: the setting, or the host's own maximum -----------------------------------------------------


def test_the_hosts_own_maximum_beats_the_setting_when_it_is_smaller(monkeypatch):
    host = install(monkeypatch, FakeHost(max_bytes=5000))
    assert catalog.zip_limit(1024, ["fake"], UploadContext()) == 5000
    host.max_bytes = 5 * 1024 * MEGABYTE
    assert catalog.zip_limit(1024, ["fake"], UploadContext()) == 1024 * MEGABYTE
    host.max_bytes = None  # unknown or unlimited: the setting stands
    assert catalog.zip_limit(300, ["fake"], UploadContext()) == 300 * MEGABYTE


def test_the_limit_is_read_from_the_first_host_that_is_ready():
    context = UploadContext()
    # Pixeldrain has no key, so uploads start at the next host; Catbox accepts 200 MB files.
    assert catalog.zip_limit(1024, ["pixeldrain", "catbox", "tempsh"], context) == 200_000_000
    assert catalog.zip_limit(1024, ["pixeldrain", "gofile"], context) == 1024 * MEGABYTE  # Gofile: no maximum
    assert catalog.zip_limit(1024, ["tempsh"], context) == 1024 * MEGABYTE  # 4 GB host, 1 GB setting
    assert catalog.zip_limit(4096, ["tempsh"], context) == 4_000_000_000


# -- the whole flow on a fake host ---------------------------------------------------------------------------


def run(items, tmp_path, **kwargs):
    work = tmp_path / "tmp"
    work.mkdir(exist_ok=True)
    events = {"prepare": [], "host": [], "progress": [], "outcome": []}
    outcomes = upload_items(
        items, kwargs.pop("hosts", ["fake"]), kwargs.pop("context", UploadContext()), temp_dir=str(work),
        on_prepare=lambda *a: events["prepare"].append(a), on_host=lambda *a: events["host"].append(a),
        progress=lambda *a: events["progress"].append(a), on_outcome=events["outcome"].append, **kwargs)
    assert os.listdir(work) == []  # every ZIP and staging folder is gone, whatever happened
    return outcomes, events


def test_several_epubs_upload_as_one_zip_with_one_link(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost())
    items = [UploadItem(make_file(tmp_path, f"{title}.epub", 50, bytes([65 + i])), book_id=str(i), source="npia",
                        title=title) for i, title in enumerate(["제목 하나", "제목 둘", "Third"])]
    history = catalog.UploadHistory(str(tmp_path / "history.json"))
    outcomes, events = run(items, tmp_path, history=history)

    assert len(outcomes) == 1 and len(host.sent) == 1
    sent = host.sent[0]
    assert sent.name == "NovelDB_3_EPUBs.zip" and sent.is_zip
    assert sent.entry_names == ["제목 하나.epub", "제목 둘.epub", "Third.epub"]
    assert all(sent.archive.getinfo(n).compress_type == zipfile.ZIP_STORED for n in sent.entry_names)
    outcome = outcomes[0]
    assert outcome.label == "NovelDB_3_EPUBs.zip (3 books)" and outcome.is_zip and not outcome.error
    assert outcome.titles == ["제목 하나", "제목 둘", "Third"]
    assert outcome.link == "https://fake.test/1/NovelDB_3_EPUBs.zip"
    saved = history.load()
    assert len(saved) == 1 and saved[0].file_name == "NovelDB_3_EPUBs.zip" and saved[0].entries == 3
    assert saved[0].titles == ["제목 하나", "제목 둘", "Third"] and saved[0].epubs

    (index, count, name, done, total) = events["prepare"][-1]
    assert (index, count, name, done == total) == (0, 1, "NovelDB_3_EPUBs.zip", True) and total > 0
    assert events["host"] == [(0, 1, "Fake")]
    assert events["progress"][-1][:2] == (0, 1) and events["progress"][-1][2] == events["progress"][-1][3]
    assert events["outcome"] == outcomes


def test_bundling_off_uploads_every_file_on_its_own(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost())
    items = items_of(tmp_path, [10, 20, 30])
    outcomes, events = run(items, tmp_path, bundle=False)
    assert [sent.name for sent in host.sent] == ["book0.epub", "book1.epub", "book2.epub"]
    assert not any(sent.is_zip for sent in host.sent) and not events["prepare"]
    assert [o.label for o in outcomes] == ["book0.epub", "book1.epub", "book2.epub"]
    assert links_text(outcomes) == "\n".join(o.link for o in outcomes)  # bare links, as before


def test_one_file_is_never_zipped(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost())
    outcomes, _events = run(items_of(tmp_path, [100]), tmp_path)
    assert host.sent[0].name == "book0.epub" and not host.sent[0].is_zip and outcomes[0].entries == 1


def test_a_small_host_limit_splits_the_upload_into_several_zips(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost(max_bytes=7000))
    items = items_of(tmp_path, [2400] * 5 + [9000])  # 9000 is over the host's maximum even alone
    outcomes, events = run(items, tmp_path, max_mb=1024)
    # 2400 + 1 KB per file: two per ZIP; five books give 2 + 2 + a lone book; the 9000 byte file goes up alone.
    names = [sent.name for sent in host.sent]
    assert names == ["NovelDB_2_EPUBs.zip", "NovelDB_2_EPUBs_2.zip", "book4.epub"]
    assert all(len(sent.data) <= 7000 for sent in host.sent)
    ok = [o for o in outcomes if o.link]
    assert [o.entries for o in ok] == [2, 2, 1]
    failed = [o for o in outcomes if o.error]
    assert len(failed) == 1 and failed[0].name == "book5.epub" and "accepts files up to" in failed[0].error
    assert [e[:2] for e in events["prepare"][:1]] == [(0, 4)]  # the first of four uploads is prepared first
    assert {(e[0], e[1]) for e in events["host"]} == {(0, 4), (1, 4), (2, 4)}


def test_the_setting_limits_the_zip_when_the_host_has_none(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost())
    items = items_of(tmp_path, [90_000] * 7)
    outcomes, _events = run(items, tmp_path, max_mb=0.3)
    assert [o.entries for o in outcomes] == [3, 3, 1]
    assert [s.name for s in host.sent] == ["NovelDB_3_EPUBs.zip", "NovelDB_3_EPUBs_2.zip", "book6.epub"]


def test_mixed_formats_get_the_files_name_and_a_stored_epub(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost())
    items = [UploadItem(make_file(tmp_path, "a.epub", 2000)), UploadItem(make_file(tmp_path, "챕터.txt", 3000, b"ab"))]
    outcomes, _events = run(items, tmp_path)
    sent = host.sent[0]
    assert sent.name == "NovelDB_2_files.zip" and outcomes[0].label == "NovelDB_2_files.zip (2 files)"
    assert sent.archive.getinfo("a.epub").compress_type == zipfile.ZIP_STORED
    assert sent.archive.getinfo("챕터.txt").compress_type == zipfile.ZIP_DEFLATED
    assert links_text(outcomes) == "NovelDB_2_files.zip (2 files)\nhttps://fake.test/1/NovelDB_2_files.zip"


def test_a_failed_zip_is_reported_and_does_not_stop_the_others(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost(max_bytes=7000, reject=lambda name: name == "NovelDB_2_EPUBs.zip"))
    outcomes, _events = run(items_of(tmp_path, [2400] * 4), tmp_path)
    assert [bool(o.link) for o in outcomes] == [False, True]
    assert "no thanks" in outcomes[0].error and outcomes[0].label == "NovelDB_2_EPUBs.zip (2 books)"
    assert [s.name for s in host.sent] == ["NovelDB_2_EPUBs_2.zip"]


def test_hosts_are_tried_in_order_for_a_zip_too(tmp_path, monkeypatch):
    broken = FakeHost(id="first", display_name="First", reject=lambda name: True)
    spare = FakeHost(id="second", display_name="Second")
    monkeypatch.setattr(catalog, "uploader", lambda host_id: {"first": broken, "second": spare}.get(host_id))
    outcomes, events = run(items_of(tmp_path, [100, 200]), tmp_path, hosts=["first", "second"])
    assert outcomes[0].link and spare.sent[0].name == "NovelDB_2_EPUBs.zip"
    assert [e[2] for e in events["host"]] == ["First", "Second"]  # each host is announced just before it is tried


def test_cancelling_between_uploads_stops_the_rest(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost(max_bytes=7000))
    stop = []
    context = UploadContext(stopped=lambda: bool(stop))
    outcomes = upload_items(items_of(tmp_path, [2400] * 4), ["fake"], context, on_outcome=lambda o: stop.append(1))
    assert len(outcomes) == 1 and len(host.sent) == 1


def test_cancelling_while_building_a_zip_cleans_up(tmp_path, monkeypatch):
    install(monkeypatch, FakeHost())
    work = tmp_path / "tmp"
    work.mkdir()
    context = UploadContext(stopped=lambda: True)
    # stopped() is already true at the start, so nothing is attempted
    assert upload_items(items_of(tmp_path, [10, 20]), ["fake"], context, temp_dir=str(work)) == []
    assert os.listdir(work) == []
    state = {"calls": 0}

    def stop_during_build():
        state["calls"] += 1
        return state["calls"] > 1

    outcomes = upload_items(items_of(tmp_path, [10, 20]), ["fake"], UploadContext(stopped=stop_during_build),
                            temp_dir=str(work))
    assert [o.error for o in outcomes] == ["Cancelled"] and os.listdir(work) == []


def test_a_folder_of_text_files_uploads_zipped_when_alone_and_nested_when_bundled(tmp_path, monkeypatch):
    host = install(monkeypatch, FakeHost())
    folder = tmp_path / "소설 한 권"
    folder.mkdir()
    make_file(folder, "001.txt", 100)
    alone, _events = run([UploadItem(str(folder), book_id="9", source="npia")], tmp_path)
    assert host.sent[0].name == "npia_9.zip" and host.sent[0].entry_names == ["소설 한 권/001.txt"]
    assert alone[0].entries == 1
    both, _events = run([UploadItem(str(folder)), UploadItem(make_file(tmp_path, "b.epub", 10))], tmp_path)
    assert host.sent[1].name == "NovelDB_2_files.zip" and host.sent[1].entry_names == ["소설 한 권.zip", "b.epub"]
    assert both[0].entries == 2


def test_unknown_hosts_report_an_error_for_every_upload(tmp_path):
    items = items_of(tmp_path, [10, 20])
    outcomes = upload_items(items, ["nowhere"], UploadContext(), bundle=False)
    assert all(o.error for o in outcomes) and not any(o.link for o in outcomes)


def test_history_keeps_working_with_old_and_newer_entries(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps([
        {"host_id": "gofile", "file_name": "old.epub", "link": "https://x/old"},
        {"host_id": "gofile", "file_name": "new.zip", "link": "https://x/new", "entries": 4, "titles": ["a"],
         "epubs": True, "from_the_future": 1},
    ]), encoding="utf-8")
    history = catalog.UploadHistory(str(path))
    old, new = history.load()
    assert old.entries == 1 and old.titles == [] and not old.epubs
    assert new.entries == 4 and new.titles == ["a"]
    history.add(UploadResult("tempsh", "NovelDB_2_EPUBs.zip", "https://x/z", entries=2, titles=["p", "q"], epubs=True))
    assert [r.file_name for r in history.load()] == ["NovelDB_2_EPUBs.zip", "old.epub", "new.zip"]
