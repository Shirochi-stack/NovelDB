"""Generated site data and source state live on the orphan `data` branch, not on main.

The branch holds one commit (docs/data and metadata/state at the same paths as before)
that each publication replaces, so daily bot runs never grow the repository's history.
Main ignores both folders; this module keeps a separate index for them in .git/.

  python scripts/data_branch.py checkout      # fetch the published data into docs/data and metadata/state
  python scripts/data_branch.py seed --from REF   # one-time: create the branch from REF's data folders
"""

import argparse
import os
from pathlib import Path
import subprocess

BRANCH = 'data'
BASE_REF = 'refs/data/base'
DATA_DIRS = ('docs/data', 'metadata/state')
# Ignore rules for files inside DATA_DIRS (main's .gitignore ignores the folders entirely).
IGNORE_FILE = Path(__file__).with_name('data-branch.gitignore')


def git(*args, input=None, env=None, check=True):
    return subprocess.run(['git', *args], input=input, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=check, env=env)


def out(*args, env=None):
    return git(*args, env=env).stdout.decode().strip()


def index_env():
    """Environment whose git commands use the data index instead of main's."""
    path = out('rev-parse', '--git-path', 'data-index')
    return {**os.environ, 'GIT_INDEX_FILE': str(Path(path).resolve())}


def fetch(branch=BRANCH, remote='origin'):
    """Fetch the branch tip (one commit, no history) and return its id."""
    git('fetch', '--no-tags', '--depth=1', remote, f'+refs/heads/{branch}')
    return out('rev-parse', 'FETCH_HEAD')


def checkout(branch=BRANCH, remote='origin'):
    """Write the published data into the working tree and remember it as the publication base."""
    commit = fetch(branch, remote)
    git('update-ref', BASE_REF, commit)
    env = index_env()
    git('read-tree', commit, env=env)
    git('checkout-index', '--all', '--force', '--index', env=env)
    print(f'Loaded {branch} {commit[:12]} into {", ".join(DATA_DIRS)}')
    return commit


def stage(pathspecs):
    """Stage new, changed and removed files under the pathspecs into the data index."""
    env = index_env()
    specs = list(pathspecs)
    # Untracked files, filtered by the data rules only (main ignores the whole folders).
    new = git('ls-files', '-z', '--others', f'--exclude-from={IGNORE_FILE}', '--', *specs,
              env=env).stdout.decode().split('\0')
    # Plus changed and removed published files. (update-index, unlike add, accepts specs matching nothing.)
    known = git('ls-files', '-z', '--modified', '--deleted', '--', *specs, env=env).stdout.decode().split('\0')
    names = sorted({p for p in new + known if p})
    if names:
        git('update-index', '--add', '--remove', '-z', '--stdin', input='\0'.join(names).encode(), env=env)


def seed(ref, branch=BRANCH, remote='origin', force=False):
    """Create the branch as one commit holding REF's data folders."""
    entries = git('ls-tree', '-z', ref, '--', *DATA_DIRS).stdout.decode().split('\0')
    if not any(entries):
        raise ValueError(f'{ref} has no data folders')
    env = index_env()
    git('read-tree', ref, env=env)
    # Drop everything outside the data folders.
    names = git('ls-files', '-z', env=env).stdout.decode().split('\0')
    outside = [n for n in names if n and not n.startswith(tuple(d + '/' for d in DATA_DIRS))]
    if outside:
        git('update-index', '--force-remove', '-z', '--stdin', input='\0'.join(outside).encode(), env=env)
    tree = out('write-tree', env=env)
    commit = out('commit-tree', tree, '-m', f'Published data from {out("rev-parse", "--short", ref)}')
    args = ['push', remote, f'{commit}:refs/heads/{branch}']
    if force:
        args.insert(1, '--force')
    pushed = git(*args, check=False)
    if pushed.returncode:
        raise RuntimeError(pushed.stderr.decode().strip())
    git('update-ref', BASE_REF, commit)
    print(f'Seeded {branch} with {commit} (tree {tree})')
    return commit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    load = commands.add_parser('checkout', help='Load the published data into the working tree')
    load.add_argument('--branch', default=BRANCH)
    create = commands.add_parser('seed', help='Create the data branch from a commit that still holds the data')
    create.add_argument('--from', dest='ref', required=True)
    create.add_argument('--branch', default=BRANCH)
    create.add_argument('--force', action='store_true', help='Replace an existing branch')
    args = parser.parse_args()
    if args.command == 'checkout':
        checkout(args.branch)
    else:
        seed(args.ref, args.branch, force=args.force)
