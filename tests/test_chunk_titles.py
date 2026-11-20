import gzip
import json
from pathlib import Path

import yaml

from scripts import chunk_titles, nu_titles

URL = "https://www.novelupdates.com/series/debut-or-die/"


def shard(data_dir, source, novel_id):
    name = f"{source}_titles_shard_{int(novel_id) % 128:03d}.json.gz"
    return json.loads(gzip.decompress((data_dir / name).read_bytes()))


def test_each_work_is_found_in_the_one_shard_its_id_names(tmp_path):
    data_dir, state_dir = tmp_path / "data", tmp_path / "state"
    data_dir.mkdir()
    # 130 shares shard 2 with 2; 3 has no translation yet; 4 is confirmed but not yet translated.
    (data_dir / "titles_en.txt").write_text(
        "2|||원제|||A Machine Title\n130|||다른 원제|||Another \"Quoted\" Title\n3|||번역 전|||\n4|||확인된 작품|||\n",
        encoding="utf-8")
    (data_dir / "kpage_titles_en.txt").write_text("2|||다른 출처 작품|||A Different Work\n", encoding="utf-8")
    nu_titles.write_addresses(state_dir / nu_titles.ADDRESSES_NAME, "npia",
                              {"2": {"english": "Debut or Die", "url": URL},
                               "4": {"english": "Confirmed Only", "url": URL}})
    addresses = chunk_titles.read_addresses(state_dir / nu_titles.ADDRESSES_NAME)
    manifest = chunk_titles.build("npia", data_dir, addresses)
    assert manifest == {"shards": 128, "titles": 2, "verified": 2, "algorithm": "numeric-modulo-v1",
                        "format": "id-to-title-json",
                        "files": [f"npia_titles_shard_{index:03d}.json.gz" for index in range(128)]}
    assert json.loads((data_dir / "npia_titles_shard_manifest.json").read_text(encoding="utf-8")) == manifest
    assert shard(data_dir, "npia", 2) == {
        "titles": {"130": 'Another "Quoted" Title', "2": "A Machine Title"},
        "nu": {"2": {"title": "Debut or Die", "url": URL}}}
    assert shard(data_dir, "npia", 3) == {"titles": {}, "nu": {}}
    assert shard(data_dir, "npia", 4) == {"titles": {}, "nu": {"4": {"title": "Confirmed Only", "url": URL}}}
    # Every shard exists, so a reader never has to tell an empty one from a missing one.
    assert all((data_dir / name).exists() for name in manifest["files"])
    # A source holds its own IDs: the same number is another work elsewhere.
    chunk_titles.build("kpage", data_dir, addresses)
    assert shard(data_dir, "kpage", 2) == {"titles": {"2": "A Different Work"}, "nu": {}}
    # Unchanged input writes the same bytes, so a daily build publishes nothing new.
    before = {name: (data_dir / name).read_bytes() for name in manifest["files"]}
    chunk_titles.build("npia", data_dir, addresses)
    assert before == {name: (data_dir / name).read_bytes() for name in manifest["files"]}


def test_titles_are_built_before_any_work_is_confirmed(tmp_path):
    (tmp_path / "sfc_titles_en.txt").write_text("750700|||原标题|||An English Title\n", encoding="utf-8")
    assert chunk_titles.read_addresses(tmp_path / "missing.json.gz") == {}
    assert chunk_titles.build("sfc", tmp_path, {})["titles"] == 1
    assert shard(tmp_path, "sfc", 750700) == {"titles": {"750700": "An English Title"}, "nu": {}}


def test_daily_workflow_builds_the_shards_after_recording_and_publishes_them():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1]
                               / ".github/workflows/canonical-titles.yml").read_text(encoding="utf-8"))
    runs = [step.get("run", "") for step in workflow["jobs"]["canonical-titles"]["steps"]]
    record = next(index for index, run in enumerate(runs) if "nu_titles.py record" in run)
    build = next(index for index, run in enumerate(runs) if "chunk_titles.py" in run)
    publish = next(index for index, run in enumerate(runs) if "publish_metadata.py" in run)
    assert record < build < publish
    assert "'docs/data/*titles_shard_*'" in runs[publish]
