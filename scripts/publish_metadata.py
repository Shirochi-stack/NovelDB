"""Publish staged data to the `data` branch without losing other sources' updates.

Data is loaded with `python scripts/data_branch.py checkout`, which records the
publication base. Staged changes since that base are applied on top of the latest
branch tip and pushed as a single replacement commit (compare-and-swap), so the
branch never accumulates history. Recovery artifacts are uploaded by callers
before invoking this command. Never resolve source data conflicts by choosing an
arbitrary side.
"""

try:
    from . import source_names
    from . import data_branch
    from .codename_policy import sanitize_staged_data
except ImportError:
    import source_names
    import data_branch
    from codename_policy import sanitize_staged_data

import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

TAG_PATHS = {'docs/data/tags_en.txt', 'docs/data/tags_en.txt.gz', 'docs/data/tags_extra.json.gz'}
MAX_BLOB = 100 * 1024 * 1024


def git(*args, input=None, env=None, check=True):
    return subprocess.run(['git', *args], input=input, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=check, env=env)


def changed(base, head):
    return set(git('diff', '--name-only', '-z', base, head).stdout.decode().strip('\0').split('\0')) - {''}


def blob(ref, path):
    result = git('show', f'{ref}:{path}', check=False)
    return result.stdout if result.returncode == 0 else None


def tags_at(ref):
    tags = {}
    for path in sorted(TAG_PATHS):
        raw = blob(ref, path)
        if raw is None:
            continue
        if path.endswith('.gz'):
            raw = gzip.decompress(raw)
        if path.endswith('.json.gz'):
            values = json.loads(raw)
        else:
            values = dict(line.split('|||', 1) for line in raw.decode('utf-8').splitlines() if '|||' in line)
        for tag, english in values.items():
            if tag and isinstance(english, str) and english.strip():
                tags.setdefault(tag, english)
    return tags


def validate_index(base=None):
    paths = git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z',
                *([base] if base else [])).stdout.decode().split('\0')
    paths = list(filter(None, paths))
    sizes = git('cat-file', '--batch-check=%(objectsize)',
                input=''.join(f':{p}\n' for p in paths).encode()).stdout.decode().splitlines()
    for path, value in zip(paths, sizes):
        size = int(value)
        if size >= MAX_BLOB:
            raise ValueError(f'{path} is {size:,} bytes; compress it before committing (100 MiB limit)')


def request_pages_deploy():
    """Ask the Pages workflow to redeploy the site with the new data (Actions runs only)."""
    repo, ref = os.environ.get('GITHUB_REPOSITORY'), os.environ.get('PAGES_REF', 'main')
    if not (repo and os.environ.get('GH_TOKEN')):
        return
    result = subprocess.run(['gh', 'workflow', 'run', 'pages.yml', '--repo', repo, '--ref', ref],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # The data is published; the scheduled Pages run deploys it later.
        print(f'::warning::Pages deployment was not requested: {result.stderr.decode().strip()}', flush=True)
    else:
        print('Requested a Pages deployment', flush=True)


def publish(message, pathspecs=(), branch=data_branch.BRANCH, attempts=5, backoff=2, deploy=True):
    message = source_names.to_codenames(message)
    # Every git command below, including the sanitizer's, works on the data index.
    os.environ['GIT_INDEX_FILE'] = data_branch.index_env()['GIT_INDEX_FILE']
    try:
        if pathspecs:
            data_branch.stage(pathspecs)
        base = git('rev-parse', '--verify', '--quiet', data_branch.BASE_REF, check=False).stdout.decode().strip()
        if not base:
            raise RuntimeError('No data loaded; run python scripts/data_branch.py checkout first')
        sanitize_staged_data(base)
        validate_index(base)
        local = git('write-tree').stdout.decode().strip()
    finally:
        os.environ.pop('GIT_INDEX_FILE', None)
    paths = changed(base, local)
    if not paths:
        print('Published: nothing changed since the loaded data')
        if deploy:
            request_pages_deploy()
        return base
    if any(not p.startswith(tuple(d + '/' for d in data_branch.DATA_DIRS)) for p in paths):
        raise ValueError('Publication contains files outside the data folders')
    for attempt in range(attempts):
        if attempt:
            time.sleep(backoff * (attempt + 1))
        fetched = git('fetch', '--no-tags', '--depth=1', 'origin', f'refs/heads/{branch}', check=False)
        if fetched.returncode:
            print(f'Fetch attempt {attempt + 1}/{attempts} failed: {fetched.stderr.decode().strip()}', flush=True)
            continue
        remote = git('rev-parse', 'FETCH_HEAD').stdout.decode().strip()
        conflicts = [p for p in (paths & changed(base, remote)) - TAG_PATHS
                     if blob(local, p) != blob(remote, p)]
        if conflicts:
            raise ValueError('Remote source data changed; recover the saved artifact instead of overwriting: '
                             + ', '.join(sorted(conflicts)))
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, 'GIT_INDEX_FILE': str(Path(temp) / 'index')}
            git('read-tree', remote, env=env)
            for path in sorted(paths - TAG_PATHS):
                entry = git('ls-tree', local, '--', path).stdout.decode().strip()
                if entry:
                    mode, _, sha = entry.split('\t')[0].split()
                    git('update-index', '--add', '--cacheinfo', mode, sha, path, env=env)
                else:
                    git('update-index', '--force-remove', '--', path, env=env)
            if paths & TAG_PATHS:
                tags = tags_at(remote)
                for tag, english in tags_at(local).items():
                    tags.setdefault(tag, english)
                ordinary, extra = {}, {}
                for tag, english in sorted(tags.items()):
                    target = extra if any(t in tag or t in english for t in ('|||', '\n', '\r')) else ordinary
                    target[tag] = english
                raw = ''.join(f'{tag}|||{en}\n' for tag, en in ordinary.items()).encode()
                data = {'docs/data/tags_en.txt': raw,
                        'docs/data/tags_en.txt.gz': gzip.compress(raw, compresslevel=9, mtime=0),
                        'docs/data/tags_extra.json.gz': gzip.compress(source_names.dumps(extra, ensure_ascii=False).encode(), compresslevel=9, mtime=0)}
                for path, content in data.items():
                    sha = git('hash-object', '-w', '--stdin', input=content).stdout.decode().strip()
                    git('update-index', '--add', '--cacheinfo', '100644', sha, path, env=env)
            tree = git('write-tree', env=env).stdout.decode().strip()
            if tree == git('rev-parse', f'{remote}^{{tree}}').stdout.decode().strip():
                print('Published: destination already contains this data')
                if deploy:
                    request_pages_deploy()
                return remote
            # No parent: the branch is replaced, never extended.
            commit = git('commit-tree', tree, '-m', message).stdout.decode().strip()
        pushed = git('push', f'--force-with-lease=refs/heads/{branch}:{remote}', 'origin',
                     f'{commit}:refs/heads/{branch}', check=False)
        if pushed.returncode == 0:
            print(f'Published {commit} (attempt {attempt + 1})')
            if deploy:
                request_pages_deploy()
            return commit
        print(f'Push attempt {attempt + 1}/{attempts} failed: {pushed.stderr.decode().strip()}', flush=True)
    raise RuntimeError('Push retries exhausted. The working tree and uploaded recovery artifact contain the results.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--branch', default=data_branch.BRANCH)
    parser.add_argument('--message', required=True)
    parser.add_argument('--paths', nargs='*', default=[],
                        help='Git pathspecs under docs/data or metadata/state to stage before publishing')
    parser.add_argument('--no-deploy', action='store_true', help='Do not request a Pages deployment')
    args = parser.parse_args()
    publish(args.message, args.paths, args.branch, deploy=not args.no_deploy)
