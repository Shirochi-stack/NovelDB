"""Publish staged data against the latest branch without losing unrelated commits.

The original local commit is retained on failure. Recovery artifacts are uploaded
by callers before invoking this command. Never force-push or resolve source data
conflicts by choosing an arbitrary side.
"""
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


def validate_index():
    paths = git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z').stdout.decode().split('\0')
    paths = list(filter(None, paths))
    sizes = git('cat-file', '--batch-check=%(objectsize)',
                input=''.join(f':{p}\n' for p in paths).encode()).stdout.decode().splitlines()
    for path, value in zip(paths, sizes):
        size = int(value)
        if size >= MAX_BLOB:
            raise ValueError(f'{path} is {size:,} bytes; compress it before committing (100 MiB limit)')


def publish(branch, message, attempts=5, backoff=2):
    validate_index()
    if git('diff', '--cached', '--quiet', check=False).returncode:
        git('commit', '-m', message)
    local = git('rev-parse', 'HEAD').stdout.decode().strip()
    # An earlier invocation may already have committed the output before push failed.
    base = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(backoff * (attempt + 1))
        fetched = git('fetch', 'origin', f'refs/heads/{branch}', check=False)
        if fetched.returncode:
            print(f'Fetch attempt {attempt + 1}/{attempts} failed: {fetched.stderr.decode().strip()}', flush=True)
            continue
        remote = git('rev-parse', 'FETCH_HEAD').stdout.decode().strip()
        if base is None:
            base = git('merge-base', local, remote).stdout.decode().strip()
            paths = changed(base, local)
            if any(not (p.startswith('docs/data/') or p.startswith('metadata/state/')) for p in paths):
                raise ValueError('Publication contains non-data commits; publish code separately first')
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
                        'docs/data/tags_extra.json.gz': gzip.compress(json.dumps(extra, ensure_ascii=False).encode(), compresslevel=9, mtime=0)}
                for path, content in data.items():
                    sha = git('hash-object', '-w', '--stdin', input=content).stdout.decode().strip()
                    git('update-index', '--add', '--cacheinfo', '100644', sha, path, env=env)
            tree = git('write-tree', env=env).stdout.decode().strip()
            if tree == git('rev-parse', f'{remote}^{{tree}}').stdout.decode().strip():
                print('Published: destination already contains this data')
                return remote
            commit = git('commit-tree', tree, '-p', remote, '-m', message).stdout.decode().strip()
        pushed = git('push', 'origin', f'{commit}:refs/heads/{branch}', check=False)
        if pushed.returncode == 0:
            print(f'Published {commit} (attempt {attempt + 1})')
            return commit
        print(f'Push attempt {attempt + 1}/{attempts} failed: {pushed.stderr.decode().strip()}', flush=True)
    raise RuntimeError('Push retries exhausted. The local commit and uploaded recovery artifact contain the results.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', default=os.environ.get('GITHUB_REF_NAME', 'main'))
    parser.add_argument('--message', required=True)
    args = parser.parse_args()
    publish(args.branch, args.message)
