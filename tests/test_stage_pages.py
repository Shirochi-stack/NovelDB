import pytest

from scripts.stage_pages import stage
from scripts.source_names import dumps


def source_site(tmp_path):
    source = tmp_path / "docs"
    data = source / "data"
    data.mkdir(parents=True)
    (source / "index.html").write_text("<h1>Catalog</h1>", encoding="utf-8")
    manifest = {"files": ["sfc_chunk_0.json.gz"], "previewUrl": "sfc_chunk_preview.json.gz",
                "descriptionShardPrefix": "sfc_descriptions_shard_", "descriptionShardCount": 1,
                "detailsShardPrefix": "sfc_details_shard_", "detailsShardCount": 1}
    (data / "sfc_chunk_manifest.json").write_text(dumps(manifest), encoding="utf-8")
    for name in [*manifest["files"], manifest["previewUrl"], "sfc_descriptions_shard_000.json.gz",
                 "sfc_details_shard_000.json.gz", "sfc_novels.json", "sfc_titles_en.txt"]:
        (data / name).write_bytes(b"fixture")
    return source


def test_staging_excludes_bulk_synopses_and_keeps_runtime_data(tmp_path):
    source = source_site(tmp_path)
    excluded = ["descriptions.txt", "descriptions.txt.gz", "sfc_descriptions.txt.gz",
                "sfc_descriptions_chunk_0.txt.gz", "sfc_descriptions_chunk_manifest.json"]
    for name in excluded:
        (source / "data" / name).write_bytes(b"bulk")
    output = tmp_path / "site"
    size = stage(source, output)
    assert size == sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    for path in source.rglob("*"):
        if path.is_file():
            target = output / path.relative_to(source)
            if path.name in excluded:
                assert not target.exists() and path.read_bytes() == b"bulk"
            else:
                assert target.read_bytes() == path.read_bytes()


def test_missing_shards_prevent_deployment(tmp_path):
    source = source_site(tmp_path)
    (source / "data/sfc_descriptions_shard_000.json.gz").unlink()
    with pytest.raises(ValueError, match="references missing files"):
        stage(source, tmp_path / "site")
    assert not (tmp_path / "site").exists()


def test_oversized_site_is_rejected_before_copying(tmp_path):
    source = source_site(tmp_path)
    with pytest.raises(ValueError, match="above the .* Pages limit"):
        stage(source, tmp_path / "site", limit=1)
    assert not (tmp_path / "site").exists()


def test_staging_cannot_write_into_source_or_reuse_a_directory(tmp_path):
    source = source_site(tmp_path)
    with pytest.raises(ValueError, match="outside the source"):
        stage(source, source / "build")
    with pytest.raises(ValueError, match="new staging directory"):
        stage(source, tmp_path)


def test_pages_retry_uses_a_distinct_upload_and_deployment_name():
    from pathlib import Path
    import yaml
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/pages.yml").read_text())
    job = workflow["jobs"]["deploy"]
    assert "${{ github.run_attempt }}" in job["env"]["PAGES_ARTIFACT_NAME"]
    upload = next(step for step in job["steps"] if "upload-pages-artifact" in step.get("uses", ""))
    deploy = next(step for step in job["steps"] if "deploy-pages" in step.get("uses", ""))
    assert upload["with"]["name"] == deploy["with"]["artifact_name"] == "${{ env.PAGES_ARTIFACT_NAME }}"
    assert int(deploy["with"]["timeout"]) == 600000
