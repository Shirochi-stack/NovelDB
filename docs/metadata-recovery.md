# Metadata publishing and recovery

Catalog and detail requests now share the `workers` setting (1–16). Numbered
catalogs prefetch a bounded window; their durable cursor advances in page order.
Joara cursor feeds run concurrently with one request per feed. Host-wide pacing
remains 0.5 seconds between request starts, so eight workers does not imply eight
requests per half-second. Logs show requests in flight and processed pages/minute.

## Publishing failures

The metadata, Kakao, and shared Korean tag workflows upload
`recovery-SOURCE-RUN_ID-ATTEMPT` before committing or pushing. The artifact contains
a checksum manifest and `payload.tar.gz`, including source state, generated data,
and available translation patches. Retention is 30 days. Failed collection and
translation steps also reach the backup steps when the runner remains available.

Publishing checks staged file sizes and retries five times. Each attempt starts
from the latest remote tree, retains unrelated changes, and adds only the local
data changes. Existing remote tag translations win. Conflicting source files stop
publication and leave the recovery artifact available; there is no force push.
The original local commit remains intact during retries. Pages and continuation
follow successful publication only. The job summary links to the recovery run.

On the next new-source run, recovery checks unexpired artifacts from failed runs
of the same repository, branch, and source. Already applied artifacts are recorded
in state and skipped. Newer revisions of the same unfinished scan restore their
cursor. Other scans contribute missing records and matching translations without
moving the active scan backward. Saved translation patches are merged before
state recovery. Explicit `recovery_run_id` also permits recovery from successful
runs when needed.

To restore a particular new-source run locally:

```powershell
python scripts/metadata_recovery.py restore --source naver --repo Shirochi-stack/NpiaDownloader67 --branch main --run-id 34857217647
python scripts/metadata_pipeline.py prepare --source naver --output-dir .cache/recovered-build/naver --state-dir metadata/state
python scripts/metadata_pipeline.py build --source naver --output-dir .cache/recovered-build/naver --state-dir metadata/state
python scripts/metadata_pipeline.py promote --source naver --output-dir .cache/recovered-build/naver --target-dir docs/data --allow-partial
```

Kakao and tag artifacts retain their source inputs and translated patch files for
manual recovery; their legacy formats do not use the new-source checkpoint runner.
Download the artifact from the run and inspect its manifest before restoring data.
Do not replace newer data wholesale with an older archive.

## Kakao and shared tags

`kakao_descriptions.txt.gz` is canonical. Run `python scripts/kakao_descriptions.py`
to migrate legacy text: both inputs are read, missing rows/translations are retained,
and the gzip output is verified before removing the plain file. Conflicting saved
English translations stop migration for review. Scraping and translation operate
on gzip directly; the website continues using description shards.

**Translate Korean Catalog Tags** now extracts a deduplicated queue from Novelpia,
Kakao, Naver, Joara, Munpia, Ridibooks, and Naver Series. Existing text, gzip, extra,
and bundled translations are excluded. SFACG remains in its Chinese workflow.
The new-source translator uses the same tag discovery and additive merge rules.

## Recovery performed September 15, 2026

- Naver run `34857217647`: 188,585 saved records; recovered 186,968 missing records.
  The rebuilt catalog has 188,585 entries in ten chunks.
- Munpia run `34777105215`: 423 saved records; recovered three missing records.
  The rebuilt catalog has 21,340 entries in two chunks.
- Both builds passed source, ID, chunk, synopsis-shard, and top-bundle validation.
  Current scan positions were retained.
- Kakao migration verified 70,164 descriptions and 66,426 existing translations.

Artifacts are time-limited backups. Runner destruction before an upload completes
cannot be recovered from a later upload step.
