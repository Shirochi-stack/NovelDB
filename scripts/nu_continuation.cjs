async function continueCatchUp({ github, context, report }) {
    const branch = context.payload.repository.default_branch;
    if (context.ref !== `refs/heads/${branch}` || report.catch_up_active !== true
        || report.continue_required !== true || report.failed !== 0
        || !(report.succeeded > 0) || report.stopped_after_failures !== false) return false;
    await github.rest.actions.createWorkflowDispatch({
        ...context.repo, workflow_id: 'canonical-titles.yml', ref: branch,
        // A short manual verification batch must not produce an endless chain of tiny batches.
        inputs: { max_requests: '1500' },
    });
    return true;
}

module.exports = { continueCatchUp };
