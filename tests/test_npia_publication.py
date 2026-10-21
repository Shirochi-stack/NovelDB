"""Exercise publication races with real local repositories, without network access."""
from pathlib import Path
import subprocess

import pytest

from scripts import publish_metadata as publication


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, encoding='utf-8').strip()


def commit(root, path, text, message):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    git(root, 'add', '--', path)
    git(root, 'commit', '-m', message)
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    remote, local, other = (tmp_path / name for name in ('remote.git', 'local', 'other'))
    subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(remote)], check=True, capture_output=True)
    subprocess.run(['git', 'clone', str(remote), str(local)], check=True, capture_output=True)
    for root in (local,):
        git(root, 'config', 'user.name', 'Fixture')
        git(root, 'config', 'user.email', 'fixture@example.invalid')
        git(root, 'config', 'core.autocrlf', 'false')
    commit(local, 'docs/data/npia_authors.json', '{"version":1,"authors":{}}\n', 'Seed npia registry')
    git(local, 'push', 'origin', 'main')
    subprocess.run(['git', 'clone', str(remote), str(other)], check=True, capture_output=True)
    git(other, 'config', 'user.name', 'Fixture')
    git(other, 'config', 'user.email', 'fixture@example.invalid')
    git(other, 'config', 'core.autocrlf', 'false')
    monkeypatch.chdir(local)
    temporary = publication.tempfile.TemporaryDirectory
    monkeypatch.setattr(publication.tempfile, 'TemporaryDirectory', lambda: temporary(dir=tmp_path))
    return remote, local, other


def stage_registry(local):
    path = local / 'docs/data/npia_authors.json'
    path.write_text('{"version":1,"authors":{"50169":{}}}\n', encoding='utf-8')
    git(local, 'add', '--', 'docs/data/npia_authors.json')


def test_remote_code_update_does_not_discard_completed_author_backfill(repositories):
    remote, local, other = repositories
    stage_registry(local)
    upstream = commit(other, 'scraper.py', '# Updated login\n', 'Update npia login')
    git(other, 'push', 'origin', 'main')
    published = publication.publish('main', 'Update npia registry', backoff=0)
    assert git(remote, 'rev-parse', 'main') == published
    assert git(remote, 'show', 'main:scraper.py') == '# Updated login'
    assert '50169' in git(remote, 'show', 'main:docs/data/npia_authors.json')
    assert git(remote, 'rev-parse', 'main^') == upstream


def test_push_race_refetches_and_keeps_both_updates(repositories, monkeypatch):
    remote, local, other = repositories
    stage_registry(local)
    original = publication.git
    pushes = []
    def raced(*args, **kwargs):
        if args[0] == 'push':
            if not pushes:
                commit(other, 'scraper.py', '# Concurrent fix\n', 'Fix npia scraper')
                git(other, 'push', 'origin', 'main')
            pushes.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(publication, 'git', raced)
    published = publication.publish('main', 'Update npia registry', backoff=0)
    assert len(pushes) == 2 and git(remote, 'rev-parse', 'main') == published
    assert git(remote, 'show', 'main:scraper.py') == '# Concurrent fix'
    assert '50169' in git(remote, 'show', 'main:docs/data/npia_authors.json')


def test_overlapping_metadata_is_retained_locally_instead_of_overwritten(repositories):
    remote, local, other = repositories
    stage_registry(local)
    upstream = commit(other, 'docs/data/npia_authors.json',
                      '{"version":1,"authors":{"99":{}}}\n', 'Update npia registry elsewhere')
    git(other, 'push', 'origin', 'main')
    with pytest.raises(ValueError, match='recover the saved artifact'):
        publication.publish('main', 'Update npia registry', backoff=0)
    assert git(remote, 'rev-parse', 'main') == upstream
    assert '50169' in git(local, 'show', 'HEAD:docs/data/npia_authors.json')


def test_npia_workflow_uses_race_safe_publisher_and_retains_catalog_artifact():
    source = (Path(__file__).resolve().parents[1] / '.github/workflows/rescrape-npia.yml').read_text(encoding='utf-8')
    publish = source.split('- name: Commit and push', 1)[1].split('- name: Continue unfinished', 1)[0]
    assert 'python scripts/publish_metadata.py' in publish and '\n          git push' not in publish
    recovery = source.split('- name: Preserve author sweep recovery data', 1)[1]
    assert 'docs/data/novels.json' in recovery and 'docs/data/novels_full.json' in recovery
