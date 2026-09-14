const { test } = require('node:test');
const assert = require('node:assert/strict');
const { validDecision, dispatchContinuation } = require('../scripts/metadata_continuation.cjs');
const decision = { source: 'naver', eligible: true, scan_id: 'a'.repeat(32), revision: '31', workers: '4' };
const context = { repo: { owner: 'fixture', repo: 'novels' }, payload: {
    repository: { default_branch: 'main' }, workflow_run: { head_branch: 'main', conclusion: 'success' },
} };

test('dispatches the same source and checkpoint only, without depending on translation success', async () => {
    const calls = [];
    const github = { rest: { actions: { createWorkflowDispatch: async (args) => calls.push(args) } } };
    assert.equal(await dispatchContinuation({ github, context, decision, source: 'naver' }), true);
    assert.deepEqual(calls, [{ owner: 'fixture', repo: 'novels', workflow_id: 'update-naver-metadata.yml', ref: 'main',
        inputs: { operation: 'resume', workers: '4', auto_continue: 'true', expected_scan: 'a'.repeat(32), expected_revision: '31' } }]);
});

test('rejects cancellation, failed metadata, another branch, bad source and malformed checkpoints', async () => {
    const github = { rest: { actions: { createWorkflowDispatch: async () => assert.fail('Unexpected dispatch') } } };
    for (const changes of [{ eligible: false }, { source: 'joara' }, { scan_id: '../main' }, { revision: 'x' }, { workers: 17 }]) {
        assert.equal(validDecision({ ...decision, ...changes }, 'naver'), false);
    }
    assert.equal(await dispatchContinuation({ github, context, decision, source: 'naver', cancelled: true }), false);
    for (const run of [{ head_branch: 'feature', conclusion: 'success' }, { head_branch: 'main', conclusion: 'failure' }]) {
        assert.equal(await dispatchContinuation({ github, context: { ...context, payload: { ...context.payload, workflow_run: run } }, decision, source: 'naver' }), false);
    }
});
