"""Publication to the single-commit `data` branch, with real local repositories and no network."""
import base64
import json
from pathlib import Path
import subprocess

import pytest

from scripts import data_branch
from scripts import publish_metadata as publishing
from scripts import source_names

ROOT = Path(__file__).resolve().parents[1]


def git(cwd, *args):
    return subprocess.check_output(['git', '-C', str(cwd), *args], stderr=subprocess.STDOUT).decode().strip()


def configure(repo):
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.test')
    git(repo, 'config', 'core.autocrlf', 'false')


def write(repo, name, content):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def push_data(repo, name, content):
    """Another run publishes to the data branch."""
    git(repo, 'fetch', 'origin', 'data')
    git(repo, 'checkout', '-q', '-B', 'data', 'FETCH_HEAD')
    write(repo, name, content)
    git(repo, 'add', '-f', name)
    git(repo, 'commit', '-q', '-m', 'other run')
    git(repo, 'push', '-q', '--force', 'origin', 'HEAD:data')


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    remote, local, other = (tmp_path / name for name in ('remote.git', 'local', 'other'))
    git(tmp_path, 'init', '-q', '--bare', '--initial-branch=main', str(remote))
    git(tmp_path, 'clone', '-q', str(remote), str(local))
    configure(local)
    write(local, '.gitignore', '/docs/data/\n/metadata/state/\n')
    write(local, 'README.md', 'code')
    git(local, 'add', '.')
    git(local, 'commit', '-q', '-m', 'code')
    git(local, 'push', '-q', 'origin', 'main')
    git(local, 'checkout', '-q', '--orphan', 'seed')
    git(local, 'rm', '-q', '-r', '--cached', '.')
    write(local, 'docs/data/nweb_novels.json', 'initial')
    write(local, 'docs/data/mpia_novels.json', 'initial')
    write(local, 'metadata/state/nweb.json.gz', 'state')
    git(local, 'add', '-f', 'docs/data', 'metadata/state')
    git(local, 'commit', '-q', '-m', 'seed data')
    git(local, 'push', '-q', 'origin', 'seed:data')
    git(local, 'checkout', '-q', '-f', 'main')
    git(local, 'branch', '-q', '-D', 'seed')
    git(tmp_path, 'clone', '-q', str(remote), str(other))
    configure(other)
    monkeypatch.chdir(local)
    monkeypatch.delenv('GH_TOKEN', raising=False)
    data_branch.checkout()
    return local, other, remote


def publish(message, **kwargs):
    return publishing.publish(message, ['docs/data', 'metadata/state'], backoff=0, **kwargs)


def test_checkout_loads_data_without_touching_main(repositories):
    local, _, _ = repositories
    assert (local / 'docs/data/nweb_novels.json').read_text() == 'initial'
    assert (local / 'metadata/state/nweb.json.gz').read_text() == 'state'
    assert git(local, 'status', '--porcelain') == ''


def test_publish_replaces_branch_with_one_commit_and_keeps_other_sources(repositories):
    local, other, remote = repositories
    write(local, 'docs/data/nweb_novels.json', 'scraped')
    push_data(other, 'docs/data/mpia_novels.json', 'remote change')
    commit = publish('Update nweb metadata')
    assert git(remote, 'rev-parse', 'data') == commit
    assert git(remote, 'rev-list', '--count', 'data') == '1'
    assert git(remote, 'show', 'data:docs/data/nweb_novels.json') == 'scraped'
    assert git(remote, 'show', 'data:docs/data/mpia_novels.json') == 'remote change'
    assert git(remote, 'show', 'data:metadata/state/nweb.json.gz') == 'state'
    assert git(remote, 'log', '--format=%s', 'main') == 'code'


def test_publish_stages_new_and_removed_files_by_the_data_rules(repositories):
    local, _, remote = repositories
    write(local, 'docs/data/nweb_chunk_0.json', 'chunk')
    write(local, 'docs/data/nweb_descriptions.txt', 'plaintext stays local')
    write(local, 'docs/data/nweb_titles_untranslated.txt', 'pending stays local')
    (local / 'docs/data/mpia_novels.json').unlink()
    publish('Update nweb metadata')
    files = git(remote, 'ls-tree', '-r', '--name-only', 'data').splitlines()
    assert 'docs/data/nweb_chunk_0.json' in files
    assert 'docs/data/mpia_novels.json' not in files
    assert 'docs/data/nweb_descriptions.txt' not in files
    assert 'docs/data/nweb_titles_untranslated.txt' not in files


def test_publish_only_stages_the_given_paths(repositories):
    local, _, remote = repositories
    write(local, 'docs/data/nweb_novels.json', 'scraped')
    write(local, 'docs/data/mpia_novels.json', 'not this run')
    publishing.publish('nweb', ['docs/data/nweb_*', 'docs/data/tags_extra.json.gz'], backoff=0)
    assert git(remote, 'show', 'data:docs/data/nweb_novels.json') == 'scraped'
    assert git(remote, 'show', 'data:docs/data/mpia_novels.json') == 'initial'


def test_publish_without_changes_leaves_the_branch(repositories):
    _, _, remote = repositories
    before = git(remote, 'rev-parse', 'data')
    assert publish('nothing') == before
    assert git(remote, 'rev-parse', 'data') == before


def test_publish_normalizes_message_and_data_without_changing_wire_urls(repositories):
    local, _, remote = repositories
    provider = base64.b64decode('TmF2ZXI=').decode()
    payload = {'canonical_url': 'https://novel.' + provider.lower() + '.com/webnovel/list?novelId=7'}
    write(local, 'docs/data/nweb_novels.json', json.dumps(payload))
    publish('Update ' + provider + ' metadata')
    raw = git(remote, 'show', 'data:docs/data/nweb_novels.json')
    assert json.loads(raw) == payload
    assert not source_names.contains_name(raw)
    assert git(remote, 'log', '-1', '--format=%s', 'data') == 'Update nweb metadata'


def test_publish_refuses_original_source_filenames(repositories):
    local, _, remote = repositories
    before = git(remote, 'rev-parse', 'data')
    provider = base64.b64decode('bmF2ZXI=').decode()
    write(local, f'docs/data/{provider}_novels.json', '{}')
    with pytest.raises(ValueError, match='codename'):
        publish('metadata')
    assert git(remote, 'rev-parse', 'data') == before


def test_publish_refuses_source_conflict_and_keeps_local_output(repositories):
    local, other, remote = repositories
    push_data(other, 'docs/data/nweb_novels.json', 'remote source')
    write(local, 'docs/data/nweb_novels.json', 'second scrape')
    with pytest.raises(ValueError, match='Remote source data changed'):
        publish('conflict')
    assert (local / 'docs/data/nweb_novels.json').read_text() == 'second scrape'
    assert git(remote, 'show', 'data:docs/data/nweb_novels.json') == 'remote source'


def test_publish_shared_tags_preserves_destination_translation(repositories):
    local, other, remote = repositories
    write(local, 'docs/data/tags_en.txt', '태그|||Local\n새태그|||New\n')
    push_data(other, 'docs/data/tags_en.txt', '태그|||Remote\n')
    publish('tags')
    result = git(remote, 'show', 'data:docs/data/tags_en.txt')
    assert '태그|||Remote' in result and '새태그|||New' in result


def test_publish_retries_and_keeps_output_when_exhausted(repositories, monkeypatch):
    local, _, remote = repositories
    before = git(remote, 'rev-parse', 'data')
    write(local, 'docs/data/nweb_novels.json', 'scraped')
    original = publishing.git
    attempts = []
    def reject(*args, **kwargs):
        if args[0] == 'push':
            attempts.append(args)
            return subprocess.CompletedProcess(args, 1, b'', b'rejected')
        return original(*args, **kwargs)
    monkeypatch.setattr(publishing, 'git', reject)
    with pytest.raises(RuntimeError, match='exhausted'):
        publish('saved locally')
    assert len(attempts) == 5
    assert git(remote, 'rev-parse', 'data') == before
    assert (local / 'docs/data/nweb_novels.json').read_text() == 'scraped'


def test_oversized_data_rejected_before_publication(repositories, monkeypatch):
    local, _, remote = repositories
    before = git(remote, 'rev-parse', 'data')
    write(local, 'docs/data/nweb_novels.json', 'too large')
    monkeypatch.setattr(publishing, 'MAX_BLOB', 5)
    with pytest.raises(ValueError, match='compress'):
        publish('must not publish')
    assert git(remote, 'rev-parse', 'data') == before


def test_push_race_refetches_and_preserves_remote_change(repositories, monkeypatch):
    local, other, remote = repositories
    write(local, 'docs/data/nweb_novels.json', 'scraped')
    original = publishing.git
    pushes = []
    def race(*args, **kwargs):
        if args[0] == 'push':
            pushes.append(args)
            if len(pushes) == 1:
                push_data(other, 'docs/data/mpia_novels.json', 'changed during push')
        return original(*args, **kwargs)
    monkeypatch.setattr(publishing, 'git', race)
    publish('race')
    assert len(pushes) == 2
    assert git(remote, 'show', 'data:docs/data/mpia_novels.json') == 'changed during push'
    assert git(remote, 'show', 'data:docs/data/nweb_novels.json') == 'scraped'


def test_seed_takes_only_the_data_folders(tmp_path, monkeypatch):
    remote, local = tmp_path / 'remote.git', tmp_path / 'local'
    git(tmp_path, 'init', '-q', '--bare', '--initial-branch=main', str(remote))
    git(tmp_path, 'clone', '-q', str(remote), str(local))
    configure(local)
    write(local, 'README.md', 'code')
    write(local, 'docs/index.html', 'site')
    write(local, 'docs/data/nweb_novels.json', 'data')
    write(local, 'metadata/state/nweb.json.gz', 'state')
    git(local, 'add', '.')
    git(local, 'commit', '-q', '-m', 'everything')
    monkeypatch.chdir(local)
    data_branch.seed('HEAD')
    files = git(remote, 'ls-tree', '-r', '--name-only', 'data').splitlines()
    assert files == ['docs/data/nweb_novels.json', 'metadata/state/nweb.json.gz']
    assert git(local, 'status', '--porcelain') == ''


def test_workflows_publish_data_to_the_data_branch_only():
    workflows = ROOT / '.github/workflows'
    for path in workflows.glob('*.yml'):
        text = path.read_text(encoding='utf-8')
        assert 'git push\n' not in text and 'git commit -m' not in text, path.name
        if 'publish_metadata.py' in text:
            assert 'python scripts/data_branch.py checkout' in text, path.name
            assert 'fetch-depth: 0' not in text, path.name
