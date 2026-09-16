import copy
import gzip
import json
from pathlib import Path
import subprocess

import pytest

from scripts import metadata_common as common
from scripts import metadata_recovery as recovery
from scripts import publish_metadata as publishing


def state(scan='scan', revision=1):
    result = common.empty_state('naver')
    result['progress'] = {'scan_id': scan, 'revision': revision, 'partitions': {'p': {'next_page': revision}}}
    result['records']['1'] = {'id': '1', 'title': '원문', 'translations': {'title': {'original': '원문', 'english': 'Existing'}}}
    return result


def test_recovery_preserves_translations_and_different_scan_cursor():
    current = state()
    old = state('older', 90)
    old['records']['1']['translations']['title']['english'] = 'Replacement'
    old['records']['2'] = {'id': '2', 'title': 'Missing'}
    merged, report = recovery.recover_state(current, old)
    assert report['added_records'] == 1
    assert merged['progress']['partitions'] == current['progress']['partitions']
    assert merged['records']['1']['translations']['title']['english'] == 'Existing'
    again, repeated = recovery.recover_state(merged, old)
    assert repeated['added_records'] == 0 and again == merged


def test_state_translations_roundtrip_through_sidecar(tmp_path):
    saved = state()
    common.save_state(saved, tmp_path)
    base_path = tmp_path / 'naver.json.gz'
    translations_path = tmp_path / 'naver.translations.json.gz'
    assert base_path.exists() and translations_path.exists()
    base = json.loads(gzip.decompress(base_path.read_bytes()))
    sidecar = json.loads(gzip.decompress(translations_path.read_bytes()))
    assert 'translations' not in base['records']['1']
    assert sidecar['translations']['1']['title']['english'] == 'Existing'
    assert common.load_state('naver', tmp_path) == saved


def test_recovery_newer_same_scan_and_completed_scan():
    current, newer = state(), state(revision=9)
    assert recovery.recover_state(current, newer)[1]['restored_cursor']
    current['progress']['pass_complete'] = True
    assert not recovery.recover_state(current, newer)[1]['restored_cursor']
    assert not recovery.recover_state(newer, state())[1]['restored_cursor']


def test_recovery_rejects_bad_state_and_manifest(tmp_path):
    invalid = state()
    invalid['source'] = 'munpia'
    with pytest.raises(ValueError):
        recovery.recover_state(state(), invalid)
    (tmp_path / 'manifest.json').write_text(json.dumps({'version': 1, 'source': 'munpia'}))
    with pytest.raises(ValueError, match='manifest'):
        recovery.unpack(tmp_path, 'naver', 'owner/repo', 'main')
    (tmp_path / 'manifest.json').unlink()
    (tmp_path / 'naver.json.gz').write_bytes(b'bad gzip')
    with pytest.raises(gzip.BadGzipFile):
        recovery.unpack(tmp_path, 'naver', 'owner/repo', 'main')


def git(cwd, *args):
    return subprocess.check_output(['git', '-C', str(cwd), *args], stderr=subprocess.STDOUT).decode().strip()


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    remote, local, other = (tmp_path / name for name in ('remote.git', 'local', 'other'))
    git(tmp_path, 'init', '--bare', str(remote))
    git(tmp_path, 'clone', str(remote), str(local))
    for path in (local,):
        git(path, 'config', 'user.name', 'Test')
        git(path, 'config', 'user.email', 'test@example.test')
    git(local, 'checkout', '-b', 'main')
    (local / 'docs/data').mkdir(parents=True)
    (local / 'docs/data/naver_novels.json').write_text('initial')
    git(local, 'add', '.')
    git(local, 'commit', '-m', 'initial')
    git(local, 'push', 'origin', 'main')
    git(tmp_path, 'clone', '--branch', 'main', str(remote), str(other))
    git(other, 'config', 'user.name', 'Test')
    git(other, 'config', 'user.email', 'test@example.test')
    monkeypatch.chdir(local)
    return local, other, remote


def update(repo, name, content, push=False):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    git(repo, 'add', name)
    if push:
        git(repo, 'commit', '-m', name)
        git(repo, 'push', 'origin', 'main')


def test_publish_preserves_unrelated_remote_commit(repositories):
    local, other, remote = repositories
    update(local, 'docs/data/naver_novels.json', 'scraped')
    update(other, 'README.md', 'remote change', push=True)
    publishing.publish('main', 'data', backoff=0)
    assert git(remote, 'show', 'main:README.md') == 'remote change'
    assert git(remote, 'show', 'main:docs/data/naver_novels.json') == 'scraped'


def test_publish_handles_precommitted_data_and_refuses_source_conflict(repositories):
    local, other, remote = repositories
    update(local, 'docs/data/naver_novels.json', 'scraped')
    git(local, 'commit', '-m', 'already committed')
    publishing.publish('main', 'retry', backoff=0)
    assert git(remote, 'show', 'main:docs/data/naver_novels.json') == 'scraped'
    # Independently advance the destination with conflicting source output.
    git(other, 'pull', '--ff-only', 'origin', 'main')
    update(other, 'docs/data/naver_novels.json', 'remote source', push=True)
    update(local, 'docs/data/naver_novels.json', 'second scrape')
    with pytest.raises(ValueError, match='Remote source data changed'):
        publishing.publish('main', 'conflict', backoff=0)
    assert (local / 'docs/data/naver_novels.json').read_text() == 'second scrape'
    assert git(remote, 'show', 'main:docs/data/naver_novels.json') == 'remote source'


def test_publish_shared_tags_preserves_destination_translation(repositories):
    local, other, remote = repositories
    update(local, 'docs/data/tags_en.txt', '태그|||Local\n새태그|||New\n')
    update(other, 'docs/data/tags_en.txt', '태그|||Remote\n', push=True)
    publishing.publish('main', 'tags', backoff=0)
    result = git(remote, 'show', 'main:docs/data/tags_en.txt')
    assert '태그|||Remote' in result and '새태그|||New' in result


def test_publish_retries_and_retains_commit_when_exhausted(repositories, monkeypatch):
    local, _, _ = repositories
    update(local, 'docs/data/naver_novels.json', 'scraped')
    original = publishing.git
    attempts = []
    def reject(*args, **kwargs):
        if args[0] == 'push':
            attempts.append(args)
            return subprocess.CompletedProcess(args, 1, b'', b'rejected')
        return original(*args, **kwargs)
    monkeypatch.setattr(publishing, 'git', reject)
    with pytest.raises(RuntimeError, match='exhausted'):
        publishing.publish('main', 'saved locally', backoff=0)
    assert len(attempts) == 5
    assert git(local, 'show', 'HEAD:docs/data/naver_novels.json') == 'scraped'


def test_oversized_data_rejected_before_commit(repositories, monkeypatch):
    local, _, _ = repositories
    update(local, 'docs/data/naver_novels.json', 'too large')
    head = git(local, 'rev-parse', 'HEAD')
    monkeypatch.setattr(publishing, 'MAX_BLOB', 5)
    with pytest.raises(ValueError, match='compress'):
        publishing.publish('main', 'must not commit', backoff=0)
    assert git(local, 'rev-parse', 'HEAD') == head


def test_oversized_renamed_blob_is_rejected(repositories, monkeypatch):
    local, _, _ = repositories
    git(local, 'mv', 'docs/data/naver_novels.json', 'docs/data/naver_renamed.json')
    monkeypatch.setattr(publishing, 'MAX_BLOB', 5)
    with pytest.raises(ValueError, match='compress'):
        publishing.validate_index()


def test_push_race_refetches_and_preserves_remote_change(repositories, monkeypatch):
    local, other, remote = repositories
    update(local, 'docs/data/naver_novels.json', 'scraped')
    original = publishing.git
    pushes = []
    def race(*args, **kwargs):
        if args[0] == 'push':
            pushes.append(args)
            if len(pushes) == 1:
                update(other, 'README.md', 'changed during push', push=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(publishing, 'git', race)
    publishing.publish('main', 'race', backoff=0)
    assert len(pushes) == 2
    assert git(remote, 'show', 'main:README.md') == 'changed during push'
    assert git(remote, 'show', 'main:docs/data/naver_novels.json') == 'scraped'


def test_snapshot_roundtrip_and_partial_translation_recovery(repositories, monkeypatch, tmp_path):
    local, _, _ = repositories
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    monkeypatch.setenv('GITHUB_REF_NAME', 'main')
    saved = state()
    saved['records']['1'].pop('translations')
    common.save_state(saved, 'metadata/state')
    stage = local / '.cache/metadata-build/naver'
    stage.mkdir(parents=True)
    (stage / 'naver_titles_untranslated.txt').write_text('1|||원문|||Recovered title\n', encoding='utf-8')
    output = tmp_path / 'backup'
    recovery.snapshot('naver', output)
    restored = recovery.unpack(output, 'naver', 'owner/repo', 'main')
    assert restored['records']['1']['translations']['title']['english'] == 'Recovered title'
    assert (output / 'manifest.json').exists()
    manifest = json.loads((output / 'manifest.json').read_text())
    assert 'metadata/state/naver.translations.json.gz' in manifest['files']
