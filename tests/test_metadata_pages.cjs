// Offline checks for the actual github-script block; no GitHub requests are made.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workflowDir = path.join(__dirname, '..', '.github', 'workflows');
const shared = fs.readFileSync(path.join(workflowDir, 'metadata-source-job.yml'), 'utf8');
const match = shared.match(/^          script: \|\r?\n((?: {12}[^\n]*(?:\n|$))+)/m);
assert.ok(match, 'Expected the Pages github-script block');
const script = match[1].split('\n').map(line => line.slice(12)).join('\n');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const requestBuild = new AsyncFunction('github', 'context', 'core', script);

async function run({ site, ref = 'refs/heads/main', apiError } = {}) {
    const calls = [], errors = [], outputs = {};
    const context = { repo: { owner: 'fixture', repo: 'novels' }, ref };
    const github = {
        request: async (route, params) => {
            calls.push({ route, params });
            if (route.startsWith('GET ')) return { data: site ?? {
                build_type: 'legacy', source: { branch: 'main', path: '/docs' },
            } };
            if (apiError) throw new Error(apiError);
            return { data: { status: 'queued' } };
        },
    };
    const core = {
        setFailed: message => errors.push(message),
        info: () => {},
        setOutput: (key, value) => { outputs[key] = value; },
    };
    await requestBuild(github, context, core);
    return { calls, errors, outputs };
}

test('requests the branch-based Pages build explicitly with the workflow repository', async () => {
    const result = await run();
    assert.deepEqual(result.calls, [
        { route: 'GET /repos/{owner}/{repo}/pages', params: { owner: 'fixture', repo: 'novels' } },
        { route: 'POST /repos/{owner}/{repo}/pages/builds', params: { owner: 'fixture', repo: 'novels' } },
    ]);
    assert.deepEqual(result.errors, []);
    assert.equal(result.outputs.status, 'queued');
});

test('does not publish a different branch or silently change Pages configuration', async () => {
    for (const options of [
        { ref: 'refs/heads/feature' },
        { site: { build_type: 'workflow', source: { branch: 'main', path: '/docs' } } },
        { site: { build_type: 'legacy', source: { branch: 'main', path: '/' } } },
        { site: { build_type: 'legacy' } },
    ]) {
        const result = await run(options);
        assert.equal(result.calls.length, 1);
        assert.equal(result.errors.length, 1);
        assert.match(result.errors[0], /no build requested/);
    }
});

test('a rejected Pages build fails the action instead of reporting publication success', async () => {
    await assert.rejects(run({ apiError: '403 insufficient Pages permission' }), /403/);
});

test('build trigger runs after successful pushes, including no-change reruns, never in failure cleanup', () => {
    const commit = shared.indexOf('- name: Commit original metadata or translated metadata independently');
    const trigger = shared.indexOf('- name: Request GitHub Pages build');
    const cleanup = shared.indexOf('- name: Preserve durable source progress after a failed run');
    assert.ok(commit >= 0 && trigger > commit && cleanup > trigger);
    const block = shared.slice(trigger, cleanup);
    assert.match(block, /uses: actions\/github-script@v7/);
    assert.doesNotMatch(block, /if:|continue-on-error:|git diff/);
    assert.match(shared.slice(commit, trigger), /git push\s+fi/);
});

test('all metadata and translation callers grant the reusable workflow Pages write permission', () => {
    for (const file of ['metadata-source-job.yml', 'update-naver-metadata.yml',
        'update-munpia-metadata.yml', 'update-joara-metadata.yml', 'translate-new-metadata.yml']) {
        const yaml = fs.readFileSync(path.join(workflowDir, file), 'utf8');
        assert.match(yaml, /^permissions:\r?\n  contents: write\r?\n  pages: write\r?$/m, file);
        if (file !== 'metadata-source-job.yml') {
            assert.match(yaml, /uses: \.\/\.github\/workflows\/metadata-source-job.yml/, file);
        }
    }
});
