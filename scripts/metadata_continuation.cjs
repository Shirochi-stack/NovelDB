// Dispatch only a progressing catalog checkpoint after the translation attempt.
function validDecision(decision, source) {
    return ['naver', 'munpia', 'joara', 'ridi', 'naverseries'].includes(source)
        && decision?.source === source && decision.eligible === true
        && /^[a-f0-9]{32}$/.test(decision.scan_id || '')
        && /^\d+$/.test(String(decision.revision))
        && Number.isSafeInteger(Number(decision.revision))
        && Number(decision.workers) >= 1 && Number(decision.workers) <= 16
        && Number.isInteger(Number(decision.workers));
}

async function dispatchContinuation({ github, context, decision, source, cancelled = false }) {
    if (cancelled || !validDecision(decision, source)) return false;
    const branch = context.payload.repository.default_branch;
    if (context.payload.workflow_run?.head_branch !== branch
        || context.payload.workflow_run?.conclusion !== 'success') return false;
    await github.rest.actions.createWorkflowDispatch({
        ...context.repo, workflow_id: `update-${source}-metadata.yml`, ref: branch,
        inputs: { operation: 'resume', workers: String(decision.workers), auto_continue: 'true',
            expected_scan: decision.scan_id, expected_revision: String(decision.revision) },
    });
    return true;
}

module.exports = { validDecision, dispatchContinuation };
