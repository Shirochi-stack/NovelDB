"""Explicit GitLab tasks reusing existing build and metadata scripts."""
import argparse
import ast
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import yaml
from bridge import ROOT, Runner, gate, main_guard, require, tools

MAP = json.loads((Path(__file__).parent / 'workflow_map.json').read_text())
NEW_SOURCES = ('nweb', 'jara', 'mpia', 'rbooks', 'nseries')
PLAN_CASES = [['policy'], ['macos', '--arch', 'arm64'], ['macos', '--arch', 'x86_64']]
PLAN_CASES += [['legacy', '--workflow', name] for name in MAP]
PLAN_CASES += [['metadata', '--source', source, '--operation', operation]
               for source in NEW_SOURCES for operation in ('catalog', 'rankings', 'resume', 'build', 'translate')]


def translation_validator():
    tree = ast.parse((ROOT / 'scripts/translate_with_grok.py').read_text(encoding='utf-8'))
    names = {'has_cjk', 'has_ascii_letter', 'is_valid_english_translation'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    require({node.name for node in nodes} == names, 'Translation validator functions changed.')
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<original translation validators>', 'exec'), namespace)
    return namespace['is_valid_english_translation']


def verify_translation(path):
    valid = translation_validator()
    missing = 0
    for line in path.read_text(encoding='utf-8').splitlines():
        parts = line.split('|||', 2)
        if parts[0].strip().isdigit() and (len(parts) < 3 or not valid(parts[2])):
            missing += 1
    require(missing == 0, f'{missing} rows remain untranslated; checkpoints retained, publication blocked.')


def translate(runner, path, language, kind):
    if not runner.plan:
        gate(runner.env, 'TRANSLATION_APPROVED')
        require(any(runner.env.get(k) for k in ('TRANSLATION_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'GROK_API_KEY', 'XAI_API_KEY')),
                'Owner-provisioned protected/masked translation credential is required.')
    runner.command([sys.executable, 'scripts/translate_with_grok.py', path, '--lang', language, '--type', kind,
                    '--model', runner.env.get('MODEL', 'gpt-6-luna'), '--workers', runner.env.get('TRANSLATION_WORKERS', '4'),
                    '--delay', runner.env.get('TRANSLATION_DELAY', '5')], label='Translate ' + kind)
    if not runner.plan:
        verify_translation(ROOT / path)


def legacy(runner, name):
    item = MAP[name]
    steps = yaml.safe_load((ROOT / '.github/workflows' / name).read_text(encoding='utf-8'))['jobs'][item['job']]['steps']
    for title in item['steps']:
        step = next(step for step in steps if step.get('name') == title)
        if step['run'].startswith('python scripts/translate_with_grok.py '):
            # Parse only the fixed command prefix. Credentials and user input
            # never become shell text or persisted script files.
            prefix = shlex.split(step['run'].split(' --model ', 1)[0])
            require(len(prefix) == 7 and prefix[3] == '--lang' and prefix[5] == '--type', 'Translation command changed.')
            translate(runner, prefix[2], prefix[4], prefix[6])
        else:
            runner.step(name, item['job'], title)


def metadata(runner, source, operation):
    runner.step('metadata-source-job.yml', 'metadata', 'Install metadata dependencies')
    stage = '.cache/metadata-build/' + source
    common = ['--source', source, '--output-dir', stage, '--state-dir', 'metadata/state']
    def command(task, extra=()):
        runner.command([sys.executable, 'scripts/metadata_pipeline.py', task, *common, *extra], label='Metadata ' + task)
    if operation in ('build', 'translate'):
        command('prepare')
        if operation == 'translate':
            for kind in ('titles', 'descriptions', 'tags'):
                translate(runner, stage + '/' + source + '_' + kind + '_untranslated.txt', 'korean', kind)
            command('merge')
        command('build')
    else:
        command('run', ['--mode', 'catalog' if operation == 'resume' else operation, '--max-runtime', '18000',
                        '--workers', runner.env.get('METADATA_WORKERS', '4'), *(['--resume'] if operation in ('catalog', 'resume') else [])])
    runner.command([sys.executable, 'scripts/metadata_pipeline.py', 'promote', '--source', source, '--output-dir', stage,
                    '--target-dir', 'docs/data', '--allow-partial', *(['--include-tags'] if operation == 'translate' else [])], label='Validate and promote metadata')


def macos(runner, arch):
    if not runner.plan:
        require(sys.platform == 'darwin' and platform.machine() == arch, 'A native macOS runner for the selected architecture is required.')
        gate(runner.env, 'MACOS_ADHOC_APPROVED')
        tools('brew', 'sips', 'iconutil', 'hdiutil', 'codesign', 'bash')
        # The existing spec checks HOME before PLAYWRIGHT_BROWSERS_PATH. Refuse
        # that unscoped cache rather than altering a shared runner home.
        require(not (Path.home() / 'Library/Caches/ms-playwright').exists(), 'Use an ephemeral runner without a shared Playwright home cache.')
        for package in ('pango', 'gobject-introspection', 'cairo', 'gdk-pixbuf', 'libffi', 'harfbuzz', 'fontconfig', 'freetype', 'librsvg'):
            runner.command(['brew', 'list', '--versions', package], label='Check Homebrew dependency ' + package)
    runner.env['PLAYWRIGHT_BROWSERS_PATH'] = str(runner.work / 'playwright')
    runner.context.update({'matrix.arch': arch, 'steps.version.outputs.app_name': 'ND-plan', 'steps.version.outputs.app_name_lite': 'ND-plan_Lite'})
    for name in ('Load app version', 'Install Python dependencies', 'Install Playwright Chromium', 'Convert icon.ico to icon.icns',
                 'Build ${{ steps.version.outputs.app_name }} (Full + Playwright)', 'Build ${{ steps.version.outputs.app_name_lite }} (No Playwright)',
                 'Create DMG (Full)', 'Create DMG (Lite)'):
        runner.step('build-macos.yml', 'build', name)
        if name == 'Install Playwright Chromium':
            strip_browser(runner)
    runner.collect([runner.context['steps.version.outputs.app_name'] + '-macos-' + arch + '.dmg',
                    runner.context['steps.version.outputs.app_name_lite'] + '-macos-' + arch + '.dmg'])


def strip_browser(runner):
    """Keep the original compatibility step strictly inside this job's download."""
    if runner.plan:
        runner.operations.append('Prepare downloaded browser signatures within this job only')
        return
    cache = Path(runner.env['PLAYWRIGHT_BROWSERS_PATH'])
    require(cache.is_dir() and not cache.is_symlink() and cache.resolve().is_relative_to(runner.work.resolve()), 'Browser cache escapes this job.')
    entries = list(cache.rglob('*'))
    require(all(item.resolve().is_relative_to(cache.resolve()) for item in entries), 'Browser cache link escapes the job; review before stripping signatures.')
    # Remove only metadata produced by the immediately preceding browser install.
    for directory in sorted((p for p in entries if p.is_dir() and not p.is_symlink() and p.name == '_CodeSignature'), key=lambda p: len(p.parts), reverse=True):
        shutil.rmtree(directory)
    for path in entries:
        if path.is_symlink() or not path.is_file():
            continue
        if path.name.endswith('.signature'):
            path.unlink()
            continue
        with path.open('rb') as stream:
            magic = stream.read(4)
        if magic in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'):
            result = subprocess.run(['codesign', '--remove-signature', str(path)], env=runner.env, capture_output=True, text=True)
            require(result.returncode == 0 or 'not signed at all' in result.stderr.lower(), 'Downloaded browser signature removal failed.')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=('policy', 'legacy', 'metadata', 'macos'))
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--workflow', choices=tuple(MAP))
    parser.add_argument('--source', choices=NEW_SOURCES)
    parser.add_argument('--operation', choices=('catalog', 'rankings', 'resume', 'build', 'translate'))
    parser.add_argument('--arch', choices=('arm64', 'x86_64'))
    args = parser.parse_args(argv)
    source = args.source if args.task == 'metadata' else MAP.get(args.workflow, {}).get('source')
    if not args.plan:
        require(sys.version_info[:2] == (3, 11), 'Use Python 3.11 for build and metadata compatibility.')
        require(not os.environ.get('RECOVERY_RUN_ID'), 'GitHub recovery IDs are not GitLab job IDs; review/import a retained checkpoint before resuming.')
        if args.operation == 'translate' or str(args.workflow).startswith('translate-'):
            gate(os.environ, 'TRANSLATION_APPROVED')
    runner = Runner('macos-' + args.arch.replace('_', '-') if args.task == 'macos' and args.arch else 'data' if source else 'policy', args.plan)
    # Keep this GitLab path independent of ambient GitHub workflow credentials/events.
    for key in tuple(runner.env):
        if key.startswith('GITHUB_') or key in ('GH_TOKEN', 'AUTO_CONTINUE'):
            runner.env.pop(key)
    runner.env['AUTO_CONTINUE'] = 'false'
    complete = False
    try:
        if args.task == 'policy':
            runner.command([sys.executable, 'scripts/codename_policy.py', 'check', '--commits'])
        elif args.task == 'legacy':
            require(args.workflow, 'Select a legacy workflow.')
            legacy(runner, args.workflow)
        elif args.task == 'metadata':
            require(args.source and args.operation, 'Select metadata source and operation.')
            metadata(runner, args.source, args.operation)
        else:
            require(args.arch, 'Select the native architecture.')
            macos(runner, args.arch)
        complete = True
    finally:
        if source and not args.plan:
            from data import pack
            pack(ROOT, source, runner.env.get('CI_COMMIT_SHA', ''), runner.report, complete)
            # Preserve intermediate corpora/checkpoints on failed runs as well.
            if source in NEW_SOURCES or source in ('kpage', 'tags'):
                runner.command([sys.executable, 'scripts/metadata_recovery.py', 'snapshot', '--source', source,
                                '--output', str(runner.report / 'recovery')], label='Save recovery checkpoint')


if __name__ == '__main__':
    main_guard(main)
