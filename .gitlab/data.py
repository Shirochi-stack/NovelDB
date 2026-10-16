"""Scoped metadata handoff with hashes; never pack credentials or unrelated files."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile
from urllib.parse import quote
from bridge import ROOT, Failure, gate, require

sys.path.insert(0, str(ROOT))
from scripts import source_names

SOURCES = ('npia', 'kpage', 'sfc', 'nweb', 'jara', 'mpia', 'rbooks', 'nseries', 'tags')


def allowed(name, source):
    path = PurePosixPath(name)
    if source not in SOURCES or '..' in path.parts or '\\' in name or path.is_absolute():
        return False
    if path.parent == PurePosixPath('metadata/state'):
        return path.name in (source + '.json.gz', source + '.translations.json.gz')
    if path.parent != PurePosixPath('docs/data'):
        return False
    if path.name in ('tags_en.txt', 'tags_en.txt.gz', 'tags_extra.json.gz'):
        return True
    if source == 'npia':
        return path.name.startswith(('npia_', 'descriptions', 'titles_')) or path.name == 'novels.json'
    if source == 'tags':
        return path.name.startswith(('tags', 'npia_chunk'))
    return path.name.startswith(source + '_')


def paths(root, source):
    return sorted(p for directory in ('docs/data', 'metadata/state')
                  for p in (root / directory).glob('*') if p.is_file()
                  and allowed(p.relative_to(root).as_posix(), source))


def normalize(path):
    compressed = path.name.endswith('.gz')
    raw = gzip.decompress(path.read_bytes()) if compressed else path.read_bytes()
    text = raw.decode('utf-8')
    text = source_names.escape_names(text) if path.name.endswith(('.json', '.json.gz')) else source_names.to_codenames(text)
    data = text.encode('utf-8')
    return gzip.compress(data, compresslevel=9, mtime=0) if compressed else data


def pack(root, source, commit, output, complete):
    require(source in SOURCES, 'Unknown metadata source.')
    output.mkdir(parents=True, exist_ok=True)
    files = paths(root, source)
    require(files, 'No source metadata exists for the artifact.')
    manifest = {'version': 1, 'source': source, 'source_commit': commit, 'complete': complete, 'files': {}}
    with zipfile.ZipFile(output / 'candidate.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), 'Unsafe data path.')
            name = path.relative_to(root).as_posix()
            data = normalize(path)
            manifest['files'][name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    manifest['archive_sha256'] = hashlib.sha256((output / 'candidate.zip').read_bytes()).hexdigest()
    (output / 'data-manifest.json').write_text(source_names.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def verify(directory, commit, require_complete=True):
    manifest = json.loads((directory / 'data-manifest.json').read_text(encoding='utf-8'))
    require(manifest.get('version') == 1 and manifest.get('source_commit') == commit, 'Data belongs to a different source commit.')
    require(not require_complete or manifest.get('complete') is True, 'Incomplete job output cannot be published.')
    archive_path = directory / 'candidate.zip'
    require(hashlib.sha256(archive_path.read_bytes()).hexdigest() == manifest.get('archive_sha256'), 'Data archive digest mismatch.')
    entries = manifest.get('files', {})
    require(entries and len(entries) <= 2000, 'Invalid or oversized data manifest.')
    require(sum(x['size'] for x in entries.values()) <= 1024**3, 'Data archive exceeds the 1 GiB extraction limit.')
    contents = {}
    with zipfile.ZipFile(archive_path) as archive:
        require(len(archive.infolist()) == len(entries) and set(archive.namelist()) == set(entries), 'Unexpected or duplicate data member.')
        for info in archive.infolist():
            name = info.filename
            require(allowed(name, manifest.get('source')) and info.file_size == entries[name]['size'], 'Invalid data member.')
            require(info.external_attr >> 16 & 0o170000 != 0o120000, 'Symlink data member.')
            raw = archive.read(info)
            require(hashlib.sha256(raw).hexdigest() == entries[name]['sha256'], 'Data member checksum mismatch.')
            contents[name] = raw
    return manifest, contents


def publish(env, root=ROOT, api=None):
    gate(env, 'AUTOMATION_WRITE_APPROVED')
    require(env.get('CI_COMMIT_BRANCH') and not env.get('CI_COMMIT_TAG'), 'Data publication requires a branch.')
    manifest, contents = verify(root / 'artifacts/data', env['CI_COMMIT_SHA'])
    from gitlab_api import GitLab
    api = api or GitLab(env)
    branch = quote(env['CI_COMMIT_BRANCH'], safe='')
    current = api.request('GET', api.project + '/repository/branches/' + branch)
    require(current['commit']['id'] == env['CI_COMMIT_SHA'], 'Branch moved; review and rebuild instead of overwriting new work.')
    # Stage only explicit artifact paths in this disposable CI checkout. The
    # existing policy sanitizer is required before any data commit.
    for name, raw in contents.items():
        target = root / name
        require(target.resolve().is_relative_to(root.resolve()) and not target.is_symlink(), 'Unsafe destination.')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    subprocess.run(['git', 'add', '-f', '--', *contents], cwd=root, check=True)
    subprocess.run([sys.executable, 'scripts/codename_policy.py', 'sanitize-data'], cwd=root, check=True)
    subprocess.run([sys.executable, 'scripts/codename_policy.py', 'check', '--commits'], cwd=root, check=True)
    changed = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z'], cwd=root).decode().strip('\0').split('\0')
    changed = list(filter(None, changed))
    require(set(changed) <= set(contents), 'Index includes an unrelated change.')
    require(len(changed) <= 1000 and sum((root / name).stat().st_size for name in changed) <= 64 * 1024**2,
            'API commit exceeds 1000 changed files/64 MiB; use the retained artifact for an owner-reviewed import.')
    actions = []
    for name in changed:
        exists = subprocess.run(['git', 'cat-file', '-e', 'HEAD:' + name], cwd=root, capture_output=True).returncode == 0
        action = {'action': 'update' if exists else 'create', 'file_path': name,
                  'encoding': 'base64', 'content': base64.b64encode((root / name).read_bytes()).decode('ascii')}
        if exists:
            remote = api.request('GET', api.project + '/repository/files/' + quote(name, safe='') + '?ref=' + branch)
            local_sha = hashlib.sha256(subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=root)).hexdigest()
            require(remote.get('content_sha256') == local_sha, 'Destination data changed; refusing overwrite.')
            action['last_commit_id'] = remote['last_commit_id']
        actions.append(action)
    if not actions:
        print('No metadata changes to publish.')
        return
    # GitLab creates one normal commit. No local commit/signing, history rewrite,
    # delete action, force push, or GitHub origin mutation is performed.
    result = api.request('POST', api.project + '/repository/commits', {
        'branch': env['CI_COMMIT_BRANCH'], 'commit_message': 'Update ' + manifest['source'] + ' metadata [skip ci]', 'actions': actions})
    require(result.get('id'), 'GitLab did not confirm the metadata commit.')
    (root / 'artifacts/data/publication.json').write_text(source_names.dumps({'commit': result['id'], 'files': len(actions)}, indent=2))
    print('GitLab confirmed one metadata commit.')


if __name__ == '__main__':
    from bridge import main_guard
    main_guard(lambda: publish(dict(os.environ)))
