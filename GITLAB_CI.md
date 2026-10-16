# GitLab workflow alternatives

These workflows run only after **Build > Pipelines > New pipeline** (`web`).
All 19 GitHub workflow files, runtime modules, tests, remotes and history remain
unchanged. No schedules, runners, credentials, releases or Pages settings were
created. Pushes, tags and upstream workflow completion do not start this pipeline.

## Task mapping

| Existing GitHub family | GitLab selection |
| --- | --- |
| `codename-policy.yml` | Runs in every pipeline; `CI_TASK=policy` selects checks alone |
| `build-macos.yml` | `CI_TASK=macos`, native arm64/x86_64 matrix, Full and Lite DMGs |
| `metadata-source-job.yml`, `update-{nweb,jara,mpia,rbooks,nseries}-metadata.yml` | `CI_TASK=metadata`, select `METADATA_SOURCE` and `METADATA_OPERATION` |
| `translate-new-metadata.yml` | `CI_TASK=metadata`, operation `translate`; no automatic chaining |
| `rescrape-npia.yml`, `update-rankings.yml`, `update-kpage.yml`, `update-sfc.yml`, `update-sfc-rankings.yml` | `CI_TASK=legacy`, choose the exact `LEGACY_WORKFLOW` |
| `translate-npia-top.yml`, `translate-kpage.yml`, `translate-sfc.yml`, `translate-tags.yml` | `CI_TASK=legacy`, choose the exact translation workflow |
| `release-codenames.yml` | `CI_TASK=release-report` or `release-apply`, one existing `RELEASE_TAG` |
| Existing data commits and Pages refresh steps | Separate manual `publish-data` job, then a new `CI_TASK=pages` pipeline at the reviewed revision |

The adapter loads an explicit allowlist of original shell steps from
`.gitlab/workflow_map.json`. It reuses original scraping, extraction, merging,
chunking, metadata and build scripts. GitHub marketplace actions, API-key echoing,
GitHub publication commands and dispatch logic are not executed by this path.
Unknown workflow expressions and missing steps fail immediately.

## Runners and checks

Use an isolated Linux runner tagged `migration-linux-py311` with Python 3.11,
Git and Bash. PyYAML is pinned in `.gitlab/requirements.txt`. Work jobs install
the same project dependencies as the original shell steps. Allow outbound
connections only as appropriate for the explicitly chosen work. No task was
scraped, translated, built or published while preparing this alternative.

Offline helper checks:

```sh
python -m pip install -r .gitlab/requirements.txt
python -B .gitlab/test_bridge.py
python -B .gitlab/test_npia.py
python -B .gitlab/test_release.py
python scripts/codename_policy.py check --commits
```

These checks use command plans, temporary fixtures and mocked API calls. They
create no Git commits and do not test native binaries or external services.
Original project tests remain included. There is no existing test-suite workflow
to claim as a passing application suite here. Run GitLab CI Lint in the target
project before first use, then validate each selected task on its target runner.

## Translation and recovery

Paid translation requires a protected ref, `TRANSLATION_APPROVED=true`, and
explicitly starting the blocking manual translation job. An owner must separately
provision a protected, masked existing translation API variable. Never put a key
in pipeline text inputs. The adapter reads the existing provider variables from
the environment; it neither generates credentials nor persists them in scripts.
Provider/model/endpoint compatibility remains an owner validation step.

The initial worker count is 4 instead of the original legacy default of 67;
`TRANSLATION_WORKERS`, `TRANSLATION_DELAY`, `MODEL`, token limit and compression
factor remain adjustable. Translation rows are checked with the original script's
English validation functions. Any untranslated numeric row fails the job, even
when the underlying API script returned exit status zero. A failed or partial
job cannot use the publication job. Valid partial work is kept in artifacts for
review and recovery.

Data artifacts retain a scoped `candidate.zip`, per-file hashes, source commit,
completion flag, and supported original recovery snapshots for 30 days. No
unrelated local files or credentials are included. Review/download them before
expiration. GitHub recovery run IDs are explicitly rejected. Automatic recovery
from prior GitLab jobs and continuation dispatch are not implemented: an owner
must inspect and merge the saved checkpoint into a clean checkout, commit the
reviewed state, and select `resume` or translation again. Existing five-source
checkpoint merge logic in `scripts/metadata_recovery.py` remains available for
that review. This prevents silently treating a failed partial run as published.

## Data publication and Pages

`AUTOMATION_WRITE_APPROVED=true` exposes a separate blocking manual data job on
a protected branch. The owner must provision a protected/masked
`GITLAB_AUTOMATION_TOKEN` with API permission and permitted branch access. This
job checks artifact hashes, completion and commit identity; it refuses a branch
that has moved before publication. Every existing target file also uses GitLab's
`last_commit_id` conflict check. It stages only scoped data in the disposable CI
checkout, runs the required `sanitize-data` and `check --commits` policy, then
requests one normal GitLab API commit. It never pushes to origin, signs a local
commit, deletes a file, rewrites history, or replaces unrelated source files.
Concurrent unrelated changes may be preserved in the same branch; data conflicts
fail instead of being resolved automatically.

The API path is bounded to 1,000 files and 64 MiB of unencoded changed payload
inputs. Larger candidates fail with their artifacts retained for owner review
and manual import; instance request limits may be lower. Existing obsolete
shards remain because this alternative performs no deletion actions. A lost
response is not automatically retried: inspect the branch before repeating.

Pages is separate: at the desired reviewed revision select `CI_TASK=pages`,
`PAGES_APPROVED=true` on a protected ref, then start its blocking manual job.
Only tracked `docs/` files are copied, and an index file is required. The owner
must confirm GitLab Pages is enabled, its visibility and base URL are appropriate,
and any existing site URLs are compatible before deployment. No hosting settings
are changed by the helper. It publishes committed docs; unpublished work from an
earlier job is not silently deployed.

## macOS and releases

Use disposable native macOS 15 runners tagged `migration-macos15-py311-arm64`
and `migration-macos15-py311-x86_64`, with matching Python 3.11, Bash, Homebrew,
Xcode command-line tools, `sips`, `iconutil`, `hdiutil`, and `codesign`.
Preinstall the original Pango/Cairo/Homebrew dependency set; the helper checks it.
The runner must have no shared `~/Library/Caches/ms-playwright` because the
existing spec prefers that path. Chromium downloads go into the checkout's
`.gitlab-work` directory. Browser signature preparation is restricted to that
job's new download and rejects paths outside it; the original broad home-cache
signature-removal step is not executed. Compatibility must be confirmed by a
native build. Links escaping the job cache fail; internal framework symlinks are
preserved without following them during signature preparation.

PyInstaller can ad-hoc sign local binaries. Accordingly, `MACOS_ADHOC_APPROVED=true`
and a protected ref expose blocking manual build jobs. No Developer ID identity,
certificate, keychain, notarization credential or release-signing account is
configured. Both Full and Lite builds must succeed; a Lite app is never relabeled
as Full. Both architecture/variant DMGs are required and hashed.

For upload, create the matching GitLab release on its protected version tag
first. `PUBLISH_ARTIFACTS=true` exposes a separate blocking manual upload job.
The job-token permissions must allow Generic Packages and Release Links API.
The helper checks the release commit and hashes, refuses existing links/package
paths, and serializes uploads. Review partial uploads before rerunning.

Release text policy uses the repository's `source_names` rules. A report reads
one existing `RELEASE_TAG`. Applying it also requires a protected ref,
`AUTOMATION_WRITE_APPROVED=true`, the owner-provisioned API token and a manual
job. Only title/description/link names change; tag identity and asset URLs remain.
No release is created, renamed to another tag, deleted or automatically normalized.

References: [GitLab YAML](https://docs.gitlab.com/ci/yaml/),
[Commits API](https://docs.gitlab.com/api/commits/),
[Releases API](https://docs.gitlab.com/api/releases/).
