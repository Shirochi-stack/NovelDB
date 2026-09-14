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
for (const source of ['naver', 'munpia', 'joara', 'ridi', 'naverseries']) {
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

const tokenLimitCases = [
    { name: 'manual default', event: 'workflow_dispatch', input: '16384', variable: '', expected: '16384' },
    { name: 'manual override', event: 'workflow_dispatch', input: '32768', variable: '24576', expected: '32768' },
    { name: 'manual default overrides repository variable', event: 'workflow_dispatch', input: '16384', variable: '24576', expected: '16384' },
    { name: 'manual blank uses repository variable', event: 'workflow_dispatch', input: '', variable: '24576', expected: '24576' },
    { name: 'manual blank uses fallback', event: 'workflow_dispatch', input: '', variable: '', expected: '16384' },
    { name: 'automated uses repository variable', event: 'workflow_run', variable: '24576', expected: '24576' },
    { name: 'automated uses fallback', event: 'workflow_run', variable: '', expected: '16384' },
];

function tokenLimitContext(scenario) {
    const inputs = scenario.input === undefined ? {} : { output_token_limit: scenario.input };
    return {
        github: { event_name: scenario.event, event: scenario.event === 'workflow_dispatch' ? { inputs } : {} },
        inputs,
        vars: { TRANSLATION_OUTPUT_TOKEN_LIMIT: scenario.variable },
    };
}

for (const name of ['translate-kakao', 'translate-novelpia-top', 'translate-sfacg', 'translate-tags']) {
    const definition = workflow(name);
    const translationSteps = Object.values(definition.jobs).flatMap(job => job.steps || [])
        .filter(step => step.run?.includes('scripts/translate_with_grok.py'));
    test(`${name}: exposes a 16k manual output token limit`, () => {
        assert.equal(definition.on.workflow_dispatch.inputs.output_token_limit.type, 'string');
        assert.equal(definition.on.workflow_dispatch.inputs.output_token_limit.default, '16384');
        assert.ok(translationSteps.length > 0, 'must inspect at least one translation step');
    });
    for (const scenario of tokenLimitCases) {
        test(`${name}: output token limit ${scenario.name}`, () => {
            for (const step of translationSteps) {
                assert.equal(evaluate(step.env?.TRANSLATION_OUTPUT_TOKEN_LIMIT, tokenLimitContext(scenario)),
                    scenario.expected, step.name);
            }
        });
    }
}

const translationCaller = workflow('translate-new-metadata');
const translationJob = workflow('metadata-source-job');
test('new metadata translation exposes a 16k string input accepted by its reusable workflow', () => {
    const manualInput = translationCaller.on.workflow_dispatch.inputs.output_token_limit;
    const reusableInput = translationJob.on.workflow_call.inputs.output_token_limit;
    assert.equal(manualInput.type, 'string');
    assert.equal(manualInput.default, '16384');
    assert.equal(reusableInput.type, 'string');
    assert.equal(reusableInput.default, '');
});
for (const scenario of tokenLimitCases) {
    test(`new metadata translation: output token limit ${scenario.name}`, () => {
        const context = tokenLimitContext(scenario);
        const value = evaluate(translationCaller.jobs.translate.with.output_token_limit, context);
        assert.equal(typeof value, translationJob.on.workflow_call.inputs.output_token_limit.type);
        assert.equal(value, scenario.expected);
        assert.equal(evaluate(translationJob.jobs.metadata.env.TRANSLATION_OUTPUT_TOKEN_LIMIT,
            { inputs: { output_token_limit: value }, vars: context.vars }), scenario.expected);
    });
}
for (const variable of ['', '24576']) {
    test(`reusable metadata job resolves output token limit for callers that omit it, variable=${JSON.stringify(variable)}`, () => {
        assert.equal(evaluate(translationJob.jobs.metadata.env.TRANSLATION_OUTPUT_TOKEN_LIMIT, {
            inputs: { output_token_limit: translationJob.on.workflow_call.inputs.output_token_limit.default },
            vars: { TRANSLATION_OUTPUT_TOKEN_LIMIT: variable },
        }), variable || '16384');
    });
}
