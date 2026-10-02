"""Check tracked paths, text, compressed data, and commit messages for names."""
import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import urllib.request

try:
    from .source_names import NAME_PATTERN, contains_name, escape_names, sanitize_rules, to_codenames
except ImportError:
    from source_names import NAME_PATTERN, contains_name, escape_names, sanitize_rules, to_codenames

ROOT = Path(__file__).resolve().parents[1]


def event_messages():
    event_path = os.environ.get('GITHUB_EVENT_PATH')
    if not event_path:
        return []
    event = json.loads(Path(event_path).read_text(encoding='utf-8'))
    messages = [commit.get('message', '') for commit in event.get('commits', [])]
    number = event.get('pull_request', {}).get('number') or event.get('number')
    if 'pull_request' not in event or not isinstance(number, int):
        return messages
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'codename-policy'}
    if token := os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + token
    repo = os.environ['GITHUB_REPOSITORY']
    page = 1
    while True:
        url = f'https://api.github.com/repos/{repo}/pulls/{number}/commits?per_page=100&page={page}'
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            commits = json.load(response)
        messages.extend(commit['commit']['message'] for commit in commits)
        if len(commits) < 100:
            return messages
        page += 1


def sanitize_staged_data():
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel']).decode().strip())
    paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only',
                                     '--diff-filter=ACMR', '-z'], cwd=root).decode().strip('\0').split('\0')
    changed = []
    for name in filter(None, paths):
        if NAME_PATTERN.search(name):
            raise ValueError('Publication path must use a source codename')
        path = root / name
        if not name.startswith(('docs/data/', 'metadata/state/')) or not path.is_file():
            continue
        raw = path.read_bytes()
        compressed = name.endswith('.gz')
        content = gzip.decompress(raw) if compressed else raw
        text = content.decode('utf-8')
        if not contains_name(text):
            continue
        updated = escape_names(text) if name.endswith(('.json', '.json.gz')) else to_codenames(text)
        if updated != text:
            value = updated.encode('utf-8')
            path.write_bytes(gzip.compress(value, compresslevel=9, mtime=0) if compressed else value)
            changed.append(name)
    if changed:
        subprocess.run(['git', 'add', '--', *changed], cwd=root, check=True)
    return changed


def check(commits=False):
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().strip('\0').split('\0')
    failures = []
    for name in paths:
        path = ROOT / name
        if NAME_PATTERN.search(name):
            failures.append(name + ': path')
        if not path.is_file():
            continue
        opener = gzip.open if name.endswith('.gz') else open
        try:
            with opener(path, 'rt', encoding='utf-8') as stream:
                tail = ''
                while chunk := stream.read(1024 * 1024):
                    value = tail + chunk
                    if contains_name(value):
                        failures.append(name + ': content')
                        break
                    tail = value[-128:]
        except UnicodeDecodeError:
            pass
    if commits:
        messages = subprocess.check_output(['git', 'log', '--all', '--format=%B'], cwd=ROOT).decode('utf-8')
        if NAME_PATTERN.search(messages):
            failures.append('Git commit messages')
        if any(contains_name(message) for message in event_messages()):
            failures.append('Incoming commit messages')
    for failure in failures:
        print(to_codenames(failure))
    print(f'Checked {len(paths)} tracked paths; {len(failures)} violations')
    return bool(failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'sanitize-rules', 'sanitize-data'))
    parser.add_argument('path', nargs='?')
    parser.add_argument('--commits', action='store_true')
    args = parser.parse_args()
    if args.command == 'check':
        return check(args.commits)
    if args.command == 'sanitize-data':
        print(f'Normalized {len(sanitize_staged_data())} staged data files')
        return 0
    if not args.path:
        parser.error('sanitize-rules requires a bundle path')
    path = Path(args.path)
    path.write_text(sanitize_rules(path.read_text(encoding='utf-8')), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
