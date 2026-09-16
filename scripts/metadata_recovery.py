"""Back up publication inputs and recover source checkpoints from trusted runs."""
import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time

try:
    from .metadata_common import SOURCE_LABELS, load_state, save_state, atomic_json, valid_id
except ImportError:
    from metadata_common import SOURCE_LABELS, load_state, save_state, atomic_json, valid_id


def gh(*args):
    for attempt in range(3):
        try:
            return subprocess.check_output(['gh', *args], text=True, encoding='utf-8', timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def validate_state(state, source):
    if (state.get('version') != 1 or state.get('source') != source
            or not isinstance(state.get('records'), dict) or not isinstance(state.get('boards'), dict)
            or not isinstance(state.get('progress', {}), dict)):
        raise ValueError('Invalid recovery state')
    for ident, record in state['records'].items():
        if not isinstance(record, dict) or valid_id(ident) != ident or str(record.get('id')) != ident:
            raise ValueError('Invalid recovery record identity')


def recover_state(current, recovered):
    """Restore a newer same-scan checkpoint; otherwise keep current scan position."""
    try:
        from .metadata_pipeline import active_translation, valid_english
    except ImportError:
        from metadata_pipeline import active_translation, valid_english
    validate_state(current, current['source'])
    validate_state(recovered, current['source'])
    before = len(current['records'])
    result = copy.deepcopy(current)
    old, new = current.get('progress', {}), recovered.get('progress', {})
    newer = (not old.get('scan_id') or (old.get('scan_id') == new.get('scan_id')
             and (new.get('revision') or 0) > (old.get('revision') or 0) and not old.get('pass_complete')))
    if newer:
        result['progress'] = copy.deepcopy(new)
        result['coverage'] = copy.deepcopy(recovered.get('coverage', {}))
        result['boards'] = copy.deepcopy(recovered['boards'])
    translations = 0
    for ident, record in recovered['records'].items():
        if ident not in result['records']:
            result['records'][ident] = copy.deepcopy(record)
        else:
            existing = result['records'][ident]
            # Keep newer successful metadata, but never discard an existing translation.
            if (record.get('history', {}).get('last_success', '') > existing.get('history', {}).get('last_success', '')):
                existing = {**existing, **copy.deepcopy(record), 'translations': copy.deepcopy(existing.get('translations', {}))}
                result['records'][ident] = existing
            for field, translation in record.get('translations', {}).items():
                if not active_translation(existing, field) and isinstance(translation, dict):
                    if (translation.get('original') == str(existing.get(field) or '')
                            and valid_english(translation.get('english', ''))):
                        existing.setdefault('translations', {})[field] = copy.deepcopy(translation)
                        translations += 1
    for tag, english in recovered.get('tag_translations', {}).items():
        if isinstance(english, str) and english.strip():
            result.setdefault('tag_translations', {}).setdefault(tag, english)
    pending = set(result['progress'].get('pending_details', []))
    pending.update(ident for ident in result['records'] if ident not in current['records']
                   and not result['records'][ident].get('history', {}).get('last_success'))
    pending.update(ident for ident in recovered.get('progress', {}).get('pending_details', [])
                   if ident in result['records'] and not result['records'][ident].get('history', {}).get('last_success'))
    result['progress']['pending_details'] = sorted(pending)
    return result, {'added_records': len(result['records']) - before, 'added_translations': translations,
                    'restored_cursor': newer, 'total_records': len(result['records'])}


def snapshot(source, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    files = list(Path('docs/data').glob(f'{source}_*')) if source != 'tags' else []
    files += list(Path('docs/data').glob('tags*'))
    state_dir = Path('metadata/state')
    for state_path in (state_dir / f'{source}.json.gz', state_dir / f'{source}.translations.json.gz'):
        if state_path.exists():
            files.append(state_path)
    stage = Path('.cache/metadata-build') / source
    files += list(stage.glob('*'))
    files = sorted({p for p in files if p.is_file()})
    state = load_state(source, 'metadata/state') if source in SOURCE_LABELS else {}
    manifest = {'version': 1, 'source': source, 'repository': os.environ.get('GITHUB_REPOSITORY', ''),
                'branch': os.environ.get('GITHUB_REF_NAME', ''),
                'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'scan_id': state.get('progress', {}).get('scan_id'),
                'revision': state.get('progress', {}).get('revision'),
                'files': {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with tarfile.open(output / 'payload.tar.gz', 'w:gz') as archive:
        for path in files:
            archive.add(path, arcname=path.as_posix(), recursive=False)
    atomic_json(output / 'manifest.json', manifest)
    print(f'Backed up {len(files)} files for {source}')


def unpack(directory, source, repo, branch):
    directory = Path(directory)
    manifest_path = directory / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if any(manifest.get(k) != v for k, v in {'version': 1, 'source': source, 'repository': repo, 'branch': branch}.items()):
            raise ValueError('Recovery manifest does not match repository, branch, or source')
        seen = set()
        with tarfile.open(directory / 'payload.tar.gz') as archive:
            for member in archive:
                name = member.name
                path = directory / name
                if (not member.isfile() or name not in manifest['files'] or name in seen
                        or not path.resolve().is_relative_to(directory.resolve())):
                    raise ValueError('Unsafe recovery archive member')
                content = archive.extractfile(member).read()
                if hashlib.sha256(content).hexdigest() != manifest['files'][name]:
                    raise ValueError('Recovery checksum mismatch')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                seen.add(name)
        if seen != set(manifest['files']):
            raise ValueError('Incomplete recovery archive')
    candidates = list(directory.rglob(f'{source}.json.gz'))
    if len(candidates) != 1:
        raise ValueError('Recovery artifact must contain exactly one source checkpoint')
    state = load_state(source, candidates[0].parent)
    validate_state(state, source)
    stage = directory / '.cache' / 'metadata-build' / source
    if stage.exists() and state['records']:
        try:
            from .metadata_pipeline import merge
        except ImportError:
            from metadata_pipeline import merge
        merge(source, stage, candidates[0].parent, directory / 'docs/data/tags_en.txt')
        state = load_state(source, candidates[0].parent)
    return state


def restore(source, repo, branch, state_dir, run_id=None):
    if run_id:
        runs = [int(run_id)]
    else:
        pages = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repo}/actions/artifacts?per_page=100'))
        artifacts = [artifact for page in pages for artifact in page['artifacts']]
        runs = list(dict.fromkeys(a['workflow_run']['id'] for a in artifacts if not a['expired']
                    and (a['name'].startswith(f'recovery-{source}-') or a['name'].startswith(f'metadata-{source}-'))))
    reports = []
    for ident in runs:
        run = json.loads(gh('api', f'repos/{repo}/actions/runs/{ident}'))
        if (run['repository']['full_name'] != repo or (run.get('head_repository') or {}).get('full_name') != repo
                or run['head_branch'] != branch or run['event'] not in ('schedule', 'workflow_dispatch', 'workflow_run')
                or run['status'] != 'completed'):
            if run_id:
                raise ValueError('Recovery run is not a trusted completed run on this branch')
            continue
        if not run_id and run['conclusion'] == 'success':
            continue
        if run['path'].split('@')[0] not in (f'.github/workflows/update-{source}-metadata.yml', '.github/workflows/translate-new-metadata.yml'):
            raise ValueError('Unexpected originating workflow')
        artifacts = json.loads(gh('api', f'repos/{repo}/actions/runs/{ident}/artifacts'))['artifacts']
        choices = [a for a in artifacts if not a['expired'] and a['name'].startswith(f'recovery-{source}-')]
        if not choices:
            choices = [a for a in artifacts if not a['expired'] and a['name'] == f'metadata-{source}-{ident}']
        if not choices:
            if run_id:
                raise ValueError('No recovery artifact remains for this run')
            continue
        choice = sorted(choices, key=lambda a: a['id'])[-1]
        current = load_state(source, state_dir)
        if str(choice['id']) in current.get('recovered_artifacts', []):
            if run_id:
                print(f'Recovery artifact {choice["id"]} was already applied')
            continue
        with tempfile.TemporaryDirectory() as temp:
            gh('run', 'download', str(ident), '--repo', repo, '--name', choice['name'], '--dir', temp)
            recovered = unpack(temp, source, repo, branch)
            result, report = recover_state(current, recovered)
            result.setdefault('recovered_artifacts', []).append(str(choice['id']))
            save_state(result, state_dir)
        print(json.dumps({'recovery_run_id': ident, **report}))
        reports.append(report)
    if not reports:
        print('No unpublished recovery checkpoint found')
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['snapshot', 'restore'])
    parser.add_argument('--source', required=True, choices=[*SOURCE_LABELS, 'kakao', 'tags'])
    parser.add_argument('--output', default='.cache/recovery')
    parser.add_argument('--state-dir', default='metadata/state')
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--branch', default=os.environ.get('GITHUB_REF_NAME', 'main'))
    parser.add_argument('--run-id', default=os.environ.get('RECOVERY_RUN_ID') or None)
    args = parser.parse_args()
    if args.command == 'snapshot':
        snapshot(args.source, args.output)
    else:
        if args.source not in SOURCE_LABELS:
            parser.error('Automatic state recovery supports the five metadata sources; Kakao/tag backups require manual merging')
        restore(args.source, args.repo, args.branch, args.state_dir, args.run_id)
