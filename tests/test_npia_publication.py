"""The npia workflow publishes through the data-branch publisher (tests/test_data_branch.py covers its races)."""
from pathlib import Path


def test_npia_workflow_uses_race_safe_publisher_and_retains_catalog_artifact():
    source = (Path(__file__).resolve().parents[1] / '.github/workflows/rescrape-npia.yml').read_text(encoding='utf-8')
    publish = source.split('- name: Publish data', 1)[1].split('- name: Continue unfinished', 1)[0]
    assert 'python scripts/publish_metadata.py' in publish and 'git push' not in publish
    assert 'docs/data/npia_authors.json' in publish and 'docs/data/npia_public_state.json' in publish
    recovery = source.split('- name: Preserve author sweep recovery data', 1)[1]
    assert 'docs/data/novels.json' in recovery and 'docs/data/novels_full.json' in recovery
