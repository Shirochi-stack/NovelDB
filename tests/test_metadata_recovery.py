import copy
import base64
import gzip
import json
from pathlib import Path
import subprocess

import pytest

from scripts import metadata_common as common
from scripts import metadata_recovery as recovery
from scripts import source_names


def state(scan='scan', revision=1):
    result = common.empty_state('nweb')
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
    base_path = tmp_path / 'nweb.json.gz'
    translations_path = tmp_path / 'nweb.translations.json.gz'
    assert base_path.exists() and translations_path.exists()
    base = json.loads(gzip.decompress(base_path.read_bytes()))
    sidecar = json.loads(gzip.decompress(translations_path.read_bytes()))
    assert 'translations' not in base['records']['1']
    assert sidecar['translations']['1']['title']['english'] == 'Existing'
    assert common.load_state('nweb', tmp_path) == saved


def test_recovery_newer_same_scan_and_completed_scan():
    current, newer = state(), state(revision=9)
    assert recovery.recover_state(current, newer)[1]['restored_cursor']
    current['progress']['pass_complete'] = True
    assert not recovery.recover_state(current, newer)[1]['restored_cursor']
    assert not recovery.recover_state(newer, state())[1]['restored_cursor']


def test_recovery_rejects_bad_state_and_manifest(tmp_path):
    invalid = state()
    invalid['source'] = 'mpia'
    with pytest.raises(ValueError):
        recovery.recover_state(state(), invalid)
    (tmp_path / 'manifest.json').write_text(json.dumps({'version': 1, 'source': 'mpia'}))
    with pytest.raises(ValueError, match='manifest'):
        recovery.unpack(tmp_path, 'nweb', 'owner/repo', 'main')
    (tmp_path / 'manifest.json').unlink()
    (tmp_path / 'nweb.json.gz').write_bytes(b'bad gzip')
    with pytest.raises(gzip.BadGzipFile):
        recovery.unpack(tmp_path, 'nweb', 'owner/repo', 'main')


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
    (local / 'docs/data/nweb_novels.json').write_text('initial')
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


def test_snapshot_roundtrip_and_partial_translation_recovery(repositories, monkeypatch, tmp_path):
    local, _, _ = repositories
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    monkeypatch.setenv('GITHUB_REF_NAME', 'main')
    saved = state()
    saved['records']['1'].pop('translations')
    common.save_state(saved, 'metadata/state')
    stage = local / '.cache/metadata-build/nweb'
    stage.mkdir(parents=True)
    (stage / 'nweb_titles_untranslated.txt').write_text('1|||원문|||Recovered title\n', encoding='utf-8')
    output = tmp_path / 'backup'
    recovery.snapshot('nweb', output)
    restored = recovery.unpack(output, 'nweb', 'owner/repo', 'main')
    assert restored['records']['1']['translations']['title']['english'] == 'Recovered title'
    assert (output / 'manifest.json').exists()
    manifest = json.loads((output / 'manifest.json').read_text())
    assert 'metadata/state/nweb.translations.json.gz' in manifest['files']
