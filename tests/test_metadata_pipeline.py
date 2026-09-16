"""Offline pipeline checks: no scraper, browser session, or translation API."""

import gzip
import json
from pathlib import Path

import pytest

from scripts import metadata_common as common
from scripts import metadata_pipeline as pipeline


def record(source="naver", ident="129"):
    urls = {"naver": "https://novel.naver.com/best/list?novelId=",
            "joara": "https://www.joara.com/book/", "munpia": "https://www.munpia.com/novel/detail/", "ridi": "https://ridibooks.com/books/", "naverseries": "https://series.naver.com/novel/detail.series?productNo="}
    return {"id": ident, "title": "제목|||원문", "author": "작가", "cover": "",
            "tags": ["판타지"], "genres": ["판타지"], "synopsis": "첫 줄\n둘째|||줄",
            "views": None, "likes": 0, "episodes": None, "complete": None,
            "updated": None, "age": None, "canonical_url": urls[source] + ident,
            "tier": "best", "purchase_url": None, "metrics": {"favorites": 12}}


def save(tmp_path, source="naver", records=None, complete=True):
    state_dir = tmp_path / "state"
    state = common.load_state(source, state_dir)
    state["records"] = records or {"129": record(source)}
    state["coverage"] = {"complete": complete, "has_complete_baseline": complete,
                         "mode": "catalog", "status": "complete" if complete else "partial"}
    state["boards"] = {"best": {"label": "Best", "observed_at": "2026-09-13T00:00:00Z",
                                "stale": False, "records": [{"id": "129", "rank": 1}]}}
    common.save_state(state, state_dir)
    return state_dir


def patch(output, source, suffix, nid, original, english):
    common.atomic_text(output / f"{source}_{suffix}",
                       f"{nid}|||{pipeline.clean_field(original)}|||{english}\n")


def test_prepare_preserves_raw_and_invalidates_changed_translation(tmp_path):
    state_dir = save(tmp_path)
    output = tmp_path / "output"
    state = common.load_state("naver", state_dir)
    current = state["records"]["129"]
    current["translations"] = {
        "title": {"original": "옛 제목", "english": "Old title", "translated_at": "before"},
        "synopsis": {"original": current["synopsis"], "english": "First line\\nSecond line", "translated_at": "before"},
    }
    common.save_state(state, state_dir)
    pipeline.prepare("naver", output, state_dir, shared_tags=tmp_path / "none")
    after = common.load_state("naver", state_dir)["records"]["129"]
    assert after["title"] == "제목|||원문"
    assert after["synopsis"] == "첫 줄\n둘째|||줄"
    assert "title" not in after["translations"]
    assert after["translation_history"][0]["english"] == "Old title"
    assert after["translation_history"][0]["field"] == "title"
    assert pipeline.read_corpus(output / "naver_titles_untranslated.txt")["129"] == (pipeline.clean_field(record()["title"]), "")
    assert pipeline.decode_field(pipeline.read_corpus(output / "naver_descriptions.txt")["129"][1]) == "First line\\nSecond line"
    assert not pipeline.read_corpus(output / "naver_descriptions_untranslated.txt")


def test_merge_rejects_stale_patch_and_adds_tags_without_overwrite(tmp_path):
    state_dir = save(tmp_path)
    output = tmp_path / "output"
    shared = tmp_path / "existing_tags.txt"
    common.atomic_text(shared, "판타지|||Fantasy\n")
    pipeline.prepare("naver", output, state_dir, shared_tags=shared)
    patch(output, "naver", "titles_untranslated.txt", "129", "old original", "Wrong title")
    patch(output, "naver", "descriptions_untranslated.txt", "129", record()["synopsis"], "Good synopsis")
    common.atomic_text(output / "naver_tags_untranslated.txt", "0|||판타지|||Replacement\n1|||not-in-source|||Wrong\n")
    result = pipeline.merge("naver", output, state_dir, shared_tags=shared)
    assert result == {"accepted": 1, "rejected_stale": 1}
    assert pipeline.read_corpus(output / "naver_titles_en.txt")["129"][1] == ""
    assert pipeline.read_corpus(output / "naver_descriptions.txt")["129"][1] == "Good synopsis"
    assert pipeline.read_tags(output / "tags_en.txt") == {"판타지": "Fantasy"}
    assert shared.read_text(encoding="utf-8") == "판타지|||Fantasy\n"


def test_equal_ids_stay_with_their_source(tmp_path):
    for source, english in (("naver", "Naver title"), ("joara", "Joara title")):
        state_dir = save(tmp_path, source)
        output = tmp_path / source
        pipeline.prepare(source, output, state_dir, shared_tags=tmp_path / "none")
        patch(output, source, "titles_untranslated.txt", "129", record(source)["title"], english)
        pipeline.merge(source, output, state_dir, shared_tags=tmp_path / "none")
    assert common.load_state("naver", tmp_path / "state")["records"]["129"]["translations"]["title"]["english"] == "Naver title"
    assert common.load_state("joara", tmp_path / "state")["records"]["129"]["translations"]["title"]["english"] == "Joara title"


@pytest.fixture
def built(tmp_path):
    state_dir = save(tmp_path)
    output = tmp_path / "staged"
    pipeline.prepare("naver", output, state_dir, shared_tags=tmp_path / "none")
    patch(output, "naver", "titles_untranslated.txt", "129", record()["title"], "English title")
    pipeline.merge("naver", output, state_dir, shared_tags=tmp_path / "none")
    pipeline.build("naver", output, state_dir)
    return output, state_dir


def test_build_schema_manifest_shards_and_top(built):
    output, state_dir = built
    files, manifest = pipeline.validate_artifacts("naver", output)
    row = json.loads((output / "naver_novels.json").read_text(encoding="utf-8"))[0]
    assert len(row) == 16
    assert row[0] == "129" and row[5] is None and row[6] == 0
    assert row[7] is None and row[8] is None and row[10] is None
    assert row[14] == {"favorites": 12, "synopsis_available": True} and row[15] == {"best": 1}
    assert manifest["format"] == "metadata-v1"
    assert manifest["descriptionShardCount"] == 128
    assert len([name for name in files if "descriptions_shard_" in name and name.endswith(".gz")]) == 128
    shard = pipeline.read_gzip_json(output / "naver_descriptions_shard_001.json.gz")
    assert shard == {"129": record()["synopsis"]}
    top = pipeline.read_gzip_json(output / "naver_top.json.gz")
    assert top["novels"] == [row]
    assert top["translations"] == {"129": "English title"}
    assert top["descriptions"]["129"] == record()["synopsis"]


def test_build_does_not_discard_unmerged_translation_patch(built):
    output, state_dir = built
    patch(output, "naver", "descriptions_untranslated.txt", "129", record()["synopsis"], "Pending English")
    before = (output / "naver_descriptions_untranslated.txt").read_bytes()
    pipeline.build("naver", output, state_dir)
    assert (output / "naver_descriptions_untranslated.txt").read_bytes() == before


def test_promote_changes_only_source_and_explicit_shared_tags(built, tmp_path):
    output, _ = built
    target = tmp_path / "public"
    target.mkdir()
    for name in ("novels.json", "kakao_novels.json", "sfacg_novels.json", "titles_en.txt"):
        (target / name).write_bytes(b"existing source data")
    (target / "tags_en.txt").write_text("existing|||Original\n", encoding="utf-8")
    (output / "joara_novels.json").write_bytes(b"foreign staged data")
    pipeline.write_tags(output, {"existing": "Changed", "new": "New"})
    old = {path.name: path.read_bytes() for path in target.iterdir()}
    pipeline.promote("naver", output, target)
    assert all((target / name).read_bytes() == raw for name, raw in old.items())
    assert not (target / "joara_novels.json").exists()
    assert not (target / ".state").exists()
    pipeline.promote("naver", output, target, include_tags=True)
    assert pipeline.read_tags(target / "tags_en.txt") == {"existing": "Original", "new": "New"}


def test_promote_rejects_unsafe_manifest_before_writes(built, tmp_path):
    output, _ = built
    path = output / "naver_chunk_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"] = ["../novels.json"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    target = tmp_path / "public"
    with pytest.raises(ValueError, match="unsafe"):
        pipeline.promote("naver", output, target)
    assert not target.exists()


def test_promote_requires_explicit_partial_choice(built, tmp_path):
    output, _ = built
    path = output / "naver_chunk_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["coverage"] = {"complete": False, "has_complete_baseline": False, "mode": "catalog"}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    target = tmp_path / "public"
    with pytest.raises(ValueError, match="baseline"):
        pipeline.promote("naver", output, target)
    pipeline.promote("naver", output, target, allow_partial=True)
    assert (target / "naver_novels.json").exists()


def test_translation_subprocess_is_explicit_and_skips_completed_files(tmp_path, monkeypatch):
    output = tmp_path / "staged"
    output.mkdir()
    patch(output, "naver", "titles_untranslated.txt", "129", "원문", "")
    patch(output, "naver", "descriptions_untranslated.txt", "129", "소개", "Already done")
    calls = []
    monkeypatch.setattr(pipeline, "run_python", lambda script, args: calls.append((script, args)))
    pipeline.translate("naver", output, ["--workers", "1"])
    assert len(calls) == 1
    assert calls[0][0] == "translate_with_grok.py"
    assert "korean" in calls[0][1] and "titles" in calls[0][1]


def test_import_does_not_load_translator_or_scraper():
    import sys
    import subprocess
    # Inspect a fresh interpreter, independent of which other tests import adapters.
    subprocess.run([sys.executable, "-B", "-c",
                    "import sys; import scripts.metadata_pipeline; "
                    "assert 'scripts.translate_with_grok' not in sys.modules; "
                    "assert 'translate_with_grok' not in sys.modules; "
                    "assert not any(n.startswith('scripts.scrape_') for n in sys.modules)"],
                   cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True)


@pytest.mark.parametrize("left,right", [("a\nb", r"a\nb"), ("a|||b", "a｜｜｜b"),
                                       (r"a\u007cb", "a|b"), ('a"b', r'a\"b')])
def test_corpus_escape_is_reversible_and_does_not_collide(left, right):
    assert pipeline.clean_field(left) != pipeline.clean_field(right)
    assert pipeline.decode_field(pipeline.clean_field(left)) == left
    assert pipeline.decode_field(pipeline.clean_field(right)) == right


def test_literal_newline_collision_cannot_accept_stale_patch(tmp_path):
    state_dir = save(tmp_path)
    output = tmp_path / "staged"
    state = common.load_state("naver", state_dir)
    state["records"]["129"]["synopsis"] = r"a\nb"
    common.save_state(state, state_dir)
    pipeline.prepare("naver", output, state_dir, shared_tags=tmp_path / "none")
    patch(output, "naver", "descriptions_untranslated.txt", "129", "a\nb", "Stale English")
    result = pipeline.merge("naver", output, state_dir, shared_tags=tmp_path / "none")
    assert result["rejected_stale"] == 1


def test_special_tag_translation_uses_additive_json_map(tmp_path):
    state_dir = save(tmp_path)
    state = common.load_state("naver", state_dir)
    state["records"]["129"]["tags"] = ["a|||b\nc"]
    common.save_state(state, state_dir)
    output = tmp_path / "staged"
    pipeline.prepare("naver", output, state_dir, shared_tags=tmp_path / "none")
    patch(output, "naver", "tags_untranslated.txt", "0", "a|||b\nc", pipeline.clean_field("Special|||tag\nEnglish"))
    pipeline.merge("naver", output, state_dir, shared_tags=tmp_path / "none")
    assert pipeline.read_extra_tags(output / "tags_extra.json.gz") == {"a|||b\nc": "Special|||tag\nEnglish"}
    assert not (output / "tags_en.txt").exists()
    state = common.load_state("naver", state_dir)
    assert state["tag_translations"] == {"a|||b\nc": "Special|||tag\nEnglish"}
    next_output = tmp_path / "next_run"
    pipeline.prepare("naver", next_output, state_dir, shared_tags=tmp_path / "none")
    assert not pipeline.read_corpus(next_output / "naver_tags_untranslated.txt")
    pipeline.merge("naver", next_output, state_dir, shared_tags=tmp_path / "none")
    assert pipeline.read_extra_tags(next_output / "tags_extra.json.gz") == state["tag_translations"]


def test_run_dry_run_skips_downstream_writes_and_keeps_sample_defaults(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "run_python", lambda script, args: calls.append((script, args)))
    monkeypatch.setattr(pipeline, "prepare", lambda *args: pytest.fail("dry run prepared files"))
    monkeypatch.setattr(pipeline, "build", lambda *args: pytest.fail("dry run built files"))
    assert pipeline.main(["run", "--source", "naver", "--mode", "sample", "--output-dir",
                          str(tmp_path / "staged"), "--dry-run"]) == 0
    assert len(calls) == 1 and calls[0][0] == "scrape_naver.py"
    assert "--dry-run" in calls[0][1]
    assert "--max-runtime" not in calls[0][1]
    assert not (tmp_path / "staged").exists()


@pytest.mark.parametrize("operation", [pipeline.prepare, pipeline.build])
def test_pipeline_rejects_state_inside_website_before_writes(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    output = tmp_path / "staging"
    with pytest.raises(ValueError, match="outside the website"):
        operation("naver", output, tmp_path / "docs" / "data" / ".state")
    assert not output.exists()
    assert not (tmp_path / "docs").exists()


def test_ridi_translation_merge_and_artifact_joins(tmp_path):
    state_dir = save(tmp_path, "ridi")
    output = tmp_path / "ridi-stage"
    pipeline.prepare("ridi", output, state_dir, shared_tags=tmp_path / "none")
    patch(output, "ridi", "titles_untranslated.txt", "129", record("ridi")["title"], "Translated Ridibooks title")
    assert pipeline.merge("ridi", output, state_dir, shared_tags=tmp_path / "none")["accepted"] == 1
    pipeline.build("ridi", output, state_dir)
    files, manifest = pipeline.validate_artifacts("ridi", output)
    assert manifest["source"] == "ridi" and manifest["descriptionShardCount"] == 128
    assert len([f for f in files if "descriptions_shard_" in f and f.endswith(".gz")]) == 128
    top = pipeline.read_gzip_json(output / "ridi_top.json.gz")
    assert top["translations"]["129"] == "Translated Ridibooks title"
    assert top["novels"][0][11] == "https://ridibooks.com/books/129"


def test_naver_waits_for_synopses_without_removing_recovered_records(tmp_path):
    missing = record(ident="130")
    missing["synopsis"] = None
    state_dir = save(tmp_path, records={"129": record(), "130": missing})
    state = common.load_state("naver", state_dir)
    state["progress"]["pending_details"] = ["130"]
    common.save_state(state, state_dir)
    output = tmp_path / "published"
    pipeline.prepare("naver", output, state_dir)
    manifest = pipeline.build("naver", output, state_dir)
    pipeline.validate_artifacts("naver", output)
    rows = json.loads((output / "naver_novels.json").read_text(encoding="utf-8"))
    assert [row[0] for row in rows] == ["129", "130"]
    assert [row[14]["synopsis_available"] for row in rows] == [True, False]
    assert manifest["coverage"]["publication"] == {"discovered": 2, "published": 2, "awaiting_synopsis": 1}
    assert manifest["coverage"]["enrichment"]["pending"] == 1
    state = common.load_state("naver", state_dir)
    assert set(state["records"]) == {"129", "130"} and state["progress"]["pending_details"] == ["130"]
    state["records"]["130"]["synopsis"] = "Recovered synopsis"
    state["progress"]["pending_details"] = []
    common.save_state(state, state_dir)
    manifest = pipeline.build("naver", output, state_dir)
    pipeline.validate_artifacts("naver", output)
    assert manifest["totalEntries"] == 2 and manifest["coverage"]["publication"]["awaiting_synopsis"] == 0


def test_naver_validator_rejects_missing_synopsis(built):
    output, _ = built
    pipeline.gzip_json(output / "naver_descriptions_shard_001.json.gz", {})
    with pytest.raises(ValueError, match="without a synopsis"):
        pipeline.validate_artifacts("naver", output)
