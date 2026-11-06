// Offline checks that the site is deployed from main's docs/ plus the data branch.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workflowDir = path.join(__dirname, '..', '.github', 'workflows');
const read = file => fs.readFileSync(path.join(workflowDir, file), 'utf8').replace(/\r\n/g, '\n');

test('the Pages workflow assembles docs/ with the data branch and deploys it', () => {
    const pages = read('pages.yml');
    assert.match(pages, /pages: write\n  id-token: write/);
    assert.match(pages, /git fetch --no-tags --depth=1 origin data/);
    assert.match(pages, /git archive FETCH_HEAD docs\/data \| tar -x/);
    assert.match(pages, /uses: actions\/upload-pages-artifact@v3\n        with:\n          path: docs/);
    assert.match(pages, /uses: actions\/deploy-pages@v4/);
    assert.match(pages, /workflow_dispatch:/);
});

test('no workflow requests a branch-based Pages build any more', () => {
    for (const file of fs.readdirSync(workflowDir).filter(name => name.endsWith('.yml'))) {
        assert.doesNotMatch(read(file), /pages\/builds/, file);
    }
});

test('metadata callers grant the reusable job the right to request a deployment', () => {
    for (const file of ['metadata-source-job.yml', 'update-nweb-metadata.yml', 'update-mpia-metadata.yml',
        'update-jara-metadata.yml', 'update-rbooks-metadata.yml', 'update-nseries-metadata.yml',
        'translate-new-metadata.yml']) {
        assert.match(read(file), /^permissions:\n  contents: write\n  actions: write$/m, file);
    }
    const shared = read('metadata-source-job.yml');
    const publish = shared.indexOf('- name: Commit original metadata or translated metadata independently');
    const backup = shared.indexOf('- name: Back up source before publication');
    assert.ok(backup >= 0 && publish > backup);
    assert.match(shared.slice(publish), /python scripts\/publish_metadata.py[^\n]*--paths/);
    assert.match(shared, /GH_TOKEN: \$\{\{ github.token \}\}/);
});
