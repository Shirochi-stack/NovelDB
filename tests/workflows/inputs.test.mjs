// Evaluate the actual workflow expressions with GitHub's expression engine.
// Run: npm ci --prefix tests/workflows && npm test --prefix tests/workflows
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Parser, Lexer, Evaluator, data } from '@actions/expressions';
import { parse } from 'yaml';

function workflow(name) {
    return parse(readFileSync(new URL(`../../.github/workflows/${name}.yml`, import.meta.url), 'utf8'));
}

function evaluate(value, context) {
    if (typeof value !== 'string' || !value.startsWith('${{')) return value;
    const expression = new Parser(new Lexer(value.slice(3, -2)).lex().tokens, Object.keys(context), []).parse();
    const result = new Evaluator(expression, JSON.parse(JSON.stringify(context), data.reviver)).evaluate();
    return JSON.parse(JSON.stringify(result, data.replacer));
}

test('reproduces the original string-valued worker input failure before conversion', () => {
    assert.equal(typeof evaluate('${{ inputs.workers || 4 }}', { inputs: { workers: '4' } }), 'string');
    assert.equal(typeof evaluate('${{ inputs.workers || 4 }}', { inputs: {} }), 'number');
});

const callee = workflow('metadata-source-job').on.workflow_call.inputs;
for (const source of ['naver', 'munpia', 'joara']) {
    const caller = workflow(`update-${source}-metadata`);
    const bindings = caller.jobs.update.with;
    for (const operation of ['catalog', 'rankings', 'build', 'resume']) {
        for (const workers of ['1', '4', '16', 4]) {
            for (const autoContinue of [true, false]) {
                test(`${source}: manual ${operation}, workers=${JSON.stringify(workers)}, continue=${autoContinue}`, () => {
                    const context = { github: { event_name: 'workflow_dispatch', event: {} }, inputs: {
                        operation, workers, auto_continue: autoContinue,
                        expected_scan: operation === 'resume' ? 'a'.repeat(32) : '',
                        expected_revision: operation === 'resume' ? '31' : '',
                    } };
                    const values = Object.fromEntries(Object.entries(bindings).map(([key, value]) => [key, evaluate(value, context)]));
                    for (const [key, value] of Object.entries(values)) assert.equal(typeof value, callee[key].type, key);
                    assert.equal(values.workers, Number(workers));
                    assert.equal(values.auto_continue, autoContinue);
                    assert.equal(values.operation, operation);
                    assert.equal(values.source, source);
                    assert.equal(values.expected_revision, context.inputs.expected_revision);
                    assert.equal(values.expected_scan, context.inputs.expected_scan);
                });
            }
        }
    }
    for (const schedule of caller.on.schedule) {
        test(`${source}: scheduled ${schedule.cron} uses typed defaults`, () => {
            const context = { github: { event_name: 'schedule', event: { schedule: schedule.cron } }, inputs: {} };
            const values = Object.fromEntries(Object.entries(bindings).map(([key, value]) => [key, evaluate(value, context)]));
            for (const [key, value] of Object.entries(values)) assert.equal(typeof value, callee[key].type, key);
            assert.equal(values.workers, 4);
            assert.equal(values.auto_continue, true);
            assert.equal(values.operation, schedule.cron.endsWith('* * *') ? 'rankings' : 'catalog');
        });
    }
}
