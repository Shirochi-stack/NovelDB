"""Read-first release text normalization and explicit GitLab Pages packaging."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import quote
from bridge import ROOT, gate, main_guard, require
sys.path.insert(0, str(ROOT))
from scripts import source_names


def release_changes(release):
    patch = {}
    for key in ('name', 'description'):
        old = release.get(key) or ''
        value = source_names.to_codenames(old)
        if old != value:
            patch[key] = value
    links = []
    for link in release.get('assets', {}).get('links', []):
        value = source_names.to_codenames(link['name'], preserve_case=True)
        if value != link['name']:
            links.append((link['id'], value))
    return patch, links


def releases(env, apply=False, api=None):
    if apply:
        gate(env, 'AUTOMATION_WRITE_APPROVED')
    from gitlab_api import GitLab
    api = api or GitLab(env)
    tag = env.get('RELEASE_TAG', '')
    require(tag and '/' not in tag, 'Select one existing RELEASE_TAG to inspect.')
    path = api.project + '/releases/' + quote(tag, safe='')
    release = api.request('GET', path)
    patch, links = release_changes(release)
    report = ROOT / 'artifacts/release-policy'
    report.mkdir(parents=True, exist_ok=True)
    (report / 'review.json').write_text(source_names.dumps({'tag': tag, 'release_patch': patch, 'link_name_updates': links, 'applied': False}, indent=2))
    if apply:
        require(api.request('GET', path) == release, 'Release changed during review; rerun instead of overwriting.')
        if patch:
            api.request('PUT', path, patch)
        for ident, value in links:
            api.request('PUT', path + '/assets/links/' + str(ident), {'name': value})
        (report / 'applied.json').write_text(source_names.dumps({'release_fields': len(patch), 'links': len(links)}, indent=2))


def pages(env, root=ROOT):
    gate(env, 'PAGES_APPROVED')
    # Publish only committed docs at this revision. A separate reviewed data
    # commit must be incorporated before rebuilding Pages with those results.
    require(not subprocess.check_output(['git', 'diff', '--name-only', 'HEAD', '--', 'docs'], cwd=root), 'Docs have uncommitted changes.')
    names = subprocess.check_output(['git', 'ls-files', '-z', '--', 'docs'], cwd=root).decode().strip('\0').split('\0')
    require('docs/index.html' in names, 'No tracked docs/index.html to publish.')
    out = root / 'artifacts/site'
    require(not out.exists(), 'Pages output already exists; use a clean checkout.')
    for name in names:
        path = root / name
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to((root / 'docs').resolve()), 'Unsafe or missing Pages source.')
        target = out / Path(name).relative_to('docs')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=('release-report', 'release-apply', 'pages'))
    args = parser.parse_args()
    if args.task == 'pages':
        pages(dict(os.environ))
    else:
        releases(dict(os.environ), args.task == 'release-apply')


if __name__ == '__main__':
    main_guard(main)
