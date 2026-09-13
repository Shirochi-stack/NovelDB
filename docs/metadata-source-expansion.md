# Naver, Munpia and Joara metadata integration

Implemented and locally validated on **2026-09-13**. These sources have anonymous collectors, durable history, translation preparation and merging, packaging, browser support and scheduled workflow definitions. New sources remain unavailable in the selector until their manifests exist. **No new-source catalog was published during implementation.**

All 457 existing files in `docs/data` remained byte-for-byte unchanged. Verification used small anonymous samples and mocked translation. No account login, saved profile, authenticated launcher, chapter downloader, translation API, workflow dispatch or publishing operation was run.

## Scope and commands

Collectors retrieve public titles, authors, synopses, cover URLs, genres/tags, completion, available statistics and official destinations. Account and age-verification gates are skipped. Images remain URLs. Identity is `(source, numeric-string ID)`; automatic matching of moved novels is outside this implementation.

Naver covers Challenge, Best and Series Edition on `novel.naver.com`. Series storefront links can be outbound purchase destinations, but that separate storefront is not crawled.

Install `requests` and `beautifulsoup4`, or the existing project requirements. All three entrypoints use the same CLI:

```powershell
python scripts/scrape_naver.py --mode sample --output-dir .cache/naver-sample --state-dir .cache/naver-state
python scripts/scrape_munpia.py --mode sample --output-dir .cache/munpia-sample --state-dir .cache/munpia-state
python scripts/scrape_joara.py --mode sample --output-dir .cache/joara-sample --state-dir .cache/joara-state
python scripts/scrape_naver.py --mode sample --output-dir .cache/naver-plan --dry-run
```

`--dry-run` performs **zero requests and zero output writes**. Importing these modules does not start collection. This differs from the older unauthenticated Novelpia scraper, whose dry run crawls before skipping writes.

| Option | Behavior |
| --- | --- |
| `--mode sample\|catalog\|rankings` | Sample is the collector default; pipeline `run` defaults to catalog |
| `--output-dir` | Required; samples reject the production `docs/data` tree |
| `--state-dir` | Defaults to `<output-dir>/.state`; must remain outside `docs`; schedules use `metadata/state` |
| `--resume` | Continue saved work with overlap and ID deduplication; completed passes restart at the head |
| `--max-pages` | Catalog-page limit per tier, across its partitions, per invocation |
| `--max-details`, `--max-requests`, `--max-runtime` | Detail, total HTTP-attempt and elapsed-second limits; collectors also accept `--max-seconds` |
| `--workers`, `--delay`, `--retries` | Detail concurrency, host request-start interval, and bounded attempts |

Sample defaults are one worker, two seconds between requests, two catalog pages per tier, five details, twenty requests and 300 seconds. Joara bootstrap requests count toward this ceiling. Catalog defaults are four workers, at least 0.5 seconds between host request starts, at most four attempts and 18,000 seconds. Retries honor `Retry-After` and consume the same budgets.

The [common pipeline](../scripts/metadata_pipeline.py) provides `prepare`, `translate`, `merge`, `build`, `promote` and `run`. Replace `naver` with either other source:

```powershell
# Collect and package in staging, without translation or promotion.
python scripts/metadata_pipeline.py run --source naver --mode sample --output-dir .cache/naver-sample --state-dir .cache/naver-state

# Continue a bounded catalog; this can make many requests.
python scripts/metadata_pipeline.py run --source naver --mode catalog --resume --output-dir .cache/metadata-build/naver --state-dir metadata/state --max-runtime 18000

# Independently selectable stages.
python scripts/metadata_pipeline.py prepare --source naver --output-dir .cache/metadata-build/naver --state-dir metadata/state
python scripts/metadata_pipeline.py translate --source naver --output-dir .cache/metadata-build/naver --workers 4
python scripts/metadata_pipeline.py merge --source naver --output-dir .cache/metadata-build/naver --state-dir metadata/state
python scripts/metadata_pipeline.py build --source naver --output-dir .cache/metadata-build/naver --state-dir metadata/state

# Separate publication step after inspecting validated staged artifacts.
python scripts/metadata_pipeline.py promote --source naver --output-dir .cache/metadata-build/naver --target-dir docs/data
```

`translate` explicitly calls the existing Korean translator and forwards provider/model settings without changing its defaults. `run --translate` opts in; otherwise `run` collects, prepares and builds only. `run --dry-run` stops before all downstream writes. Incomplete catalogs need explicit `--allow-partial` promotion; scheduled jobs make that choice so validated partial originals are usable while discovery continues. Shared-tag promotion separately requires `--include-tags`.

[metadata_site.bat](../metadata_site.bat) accepts a source and `catalog`, `rankings`, `resume`, `prepare`, `translate`, `merge`, `build` or `promote`, using `.cache/metadata-build/<source>` and `metadata/state`. Use the Python CLI for custom limits/paths. [rebuild_site.bat](../rebuild_site.bat) retains the original build steps and additionally builds sources whose durable state exists. Local builds do not trigger scraping.

## Verified adapters

### Naver Web Novel

[scrape_naver.py](../scripts/scrape_naver.py) uses HTTP and BeautifulSoup. It discovers exposed genre links per tier, follows `genreMain` links into actual catalogs, enumerates only `ul.card_list`, and follows the smallest higher numbered page. The “next” block control can jump ten pages. Recommendation carousels are excluded; all-works and completed partitions are separate.

Public [Best League catalogs](https://novel.naver.com/best/genre?genre=102) and equivalent Challenge/Series Edition pages expose title, author, cover, episode counts and some metrics. Details use the observed tier's `/list?novelId=…` route. Synopses, tags, contributors, rating out of ten, favorites and optional Series links are parsed where present. Unloaded reaction zeroes are not likes, and notice dates are not novel-update dates. Abbreviated counts are not invented exact numbers. Completion and age remain unknown when not explicit.

Native keys are `best_<genre>_<daily|weekly>_<free|paid>`. Seven genres produce 28 boards: romance, romance fantasy, fantasy, modern fantasy, martial arts, mystery and light novel. Labels retain genre, category and period. Source-provided positions are used; no aggregate or monthly board is fabricated.

### Munpia

[scrape_munpia.py](../scripts/scrape_munpia.py) calls `/api/v1/pc/remocon/novels` from page zero with forty rows and `adultMode=false`. It validates `items`, `total`, `hasNext` and access policy. Listings contain few metrics, so `/api/v1/pc/novel-detail/{id}` supplies enrichment. Links use the [current detail route](https://www.munpia.com/novel/detail/216129).

`viewCount`, `likeCount`, `chapterCount`, `finish` and `adult` map to shared fields. Favorites, free units, characters and `unitType` remain separate metrics. Ebook/paid/free flags determine tier while native flags remain in state. The declared unit count can describe ebooks rather than serial episodes. Authors and illustrators stay separate. No chapter-list, reader or account-preference endpoint is requested. The saved-profile desktop parser is not reused.

`/api/v1/main/best24` supplies `TODAY_BEST`, `PLATINUM_TODAY_BEST` and `CONTEST_BEST`. The previous completed hourly snapshot uses Korea time. Explicit ranks, observation/window information and all rows beyond 100 are retained. Window views/favorites/score stay inside ranking observations, separate from lifetime detail metrics.

### Joara

[scrape_joara.py](../scripts/scrape_joara.py) bootstraps the current public [Joara client](https://www.joara.com/latestbooks), follows its asset references and extracts public application configuration. Asset hashes and keys are not hardcoded. A fresh device identifier is used, with no account token, saved cookies or user profile. Logs omit key/device values.

`/v2/book/latest_book` and `/v2/book/finish_book` cover `series` (Free), `nobless` (Noblesse) and `premium`. Pagination begins at one; `offset` means page size. V2 supplies substantial metadata. Missing or potentially truncated intros are enriched through `/v1/book/detail.joa`; a list intro reaching 1,000 characters needs enrichment.

V2 JSON booleans and V1 `TRUE`/`FALSE` strings are normalized explicitly, including false. Compact V1 and V2 date formats retain source precision in state. Read counts, favorites and recommendations stay separate; recommendations do not become likes. V1 `total_bytes` means bytes, not characters. Unknown completion/age remains null.

`/v2/book/best_book` supplies `all_today`, `all_weekly` and `all_monthly`. Positions follow native Best order, verified against the public client's index-based numbering. The displayed top-100 scope is retained. Latest-books ordering never becomes a ranking.

## Shared contract and history

[metadata_common.py](../scripts/metadata_common.py) owns the import-safe runner, guest HTTP sessions, budgets, host pacing, workers, historical merges and atomic checkpoints. Adapters own route allowlists, pagination, selectors and field meanings. Redirects are checked again. Ambient `.netrc` credentials and saved browser state are not used.

Collectors write `<source>_novels.json` and staged `<source>_run_report.json`. Durable `<state-dir>/<source>.json.gz` contains `version`, `source`, `records`, `boards`, `progress` and `coverage`. State is compressed metadata, without raw responses or credentials. Sanitized diagnostic logs stay under ignored `.cache` and in Actions artifacts.

The new **metadata-v1** decoder leaves the original three layouts unchanged:

```text
[id, title, author, cover, tags, views, likes, episodes,
 complete, updated, age, canonicalUrl, tier, purchaseUrl,
 metrics, rankings]
```

IDs are numeric strings scoped by source. Counts, completion and age support null; completion is otherwise `0` or `1`. Metrics preserve favorites, recommendations, rating/scale, characters, bytes and publication units separately. Rankings map native board keys to positions; timestamps/staleness accompany the manifest. Normalized state also keeps synopsis, genres/keywords, contributors/roles, source dates/precision, publication flags, translations and observation history.

Missing values, restrictions and failures preserve last-known metadata. Explicit unavailability is a dated outcome, not removal from search. Absence never adds `deleted`. Cursors, pending details and coverage survive bounded runs. Resume overlaps moving pages and deduplicates IDs; repeated pages, malformed envelopes and unexplained empty results prevent false completion. Changed/missing details are refreshed; otherwise successful details are revalidated after thirty days. Failed boards keep prior positions/timestamps and become stale.

## Translation and packaging

Preparation creates source-prefixed title/synopsis masters and pending title/synopsis/tag files. New corpora keep `id|||original|||English`, using reversible JSON string-body escapes. Pipes become `\u007c`; actual newlines and literal backslashes remain distinguishable. State and browser JSON retain exact text. Use the common pipeline for these corpora rather than the older source-specific mergers.

English is bound to its exact original. Changed originals invalidate/archive active translations and return to the queue; late patches for superseded originals are rejected. Unchanged originals retain English. Shared tag merges only add missing mappings. Tags containing delimiters/newlines use optional additive `tags_extra.json.gz`, because the legacy two-column map cannot represent them losslessly.

The pipeline reuses [catalog chunking](../scripts/chunk_and_compress.py), [synopsis sharding](../scripts/chunk_descriptions.py) and [gzip utilities](../scripts/gzip_text_files.py), then fills new-source text payloads from raw state to avoid legacy escape conversion. Outputs are:

- `<source>_chunk_<n>.json.gz`: approximately 20,000 rows per chunk with embedded English titles;
- `<source>_chunk_manifest.json`: format, source, files, count, boards and coverage;
- 128 `<source>_descriptions_shard_<000..127>.json.gz` files using numeric ID modulo 128, plus a manifest;
- `<source>_top.json.gz`: up to 100 unique native-ranked records, with available titles/synopses;
- independent master/pending corpora and compressed synopsis text.

Full ranking observations remain in state. Validation checks schema, filenames/counts, canonical destinations, chunk equality, shard placement and top identity. Promotion copies only selected-source validated artifacts, with manifests last. Shared tags require `--include-tags`; state and request logs are not website artifacts.

## Website and workflows

[app.js](app.js), [metadata-core.js](metadata-core.js) and [index.html](index.html) register all six sources. All Sources iterates the registry and isolates failures. New manifests determine chunk counts; missing manifests leave options unavailable. Progressive callbacks check cancellation. Incomplete coverage or failed chunks produce a partial-results indication.

Source-and-ID keys protect deduplication, translations, focused cards and lazy synopsis joins. Original/English title, author and ID search, tag inclusion/exclusion, pagination and hash restoration work across sources. Unknown numbers sort last in either direction; unknown age/status does not falsely match narrow filters. Native rankings identify source and scope. Cards retain the existing layout and use tier-specific canonical links.

| Workflow | Weekly catalog, 08:00 UTC | Daily ranking time UTC |
| --- | --- | --- |
| [Update Naver Metadata](../.github/workflows/update-naver-metadata.yml) | Monday | 16:00 |
| [Update Munpia Metadata](../.github/workflows/update-munpia-metadata.yml) | Tuesday | 17:00 |
| [Update Joara Metadata](../.github/workflows/update-joara-metadata.yml) | Wednesday | 18:00 |

Thin source workflows expose catalog/rankings/build/resume dispatch choices. The [shared job](../.github/workflows/metadata-source-job.yml) claims `data-write-lock` once, limits collection to five hours inside six hours, and commits validated originals independently of translation. Catalog schedules resume durable state. State is included in data commits; failed runs can separately commit validated progress.

[Translate New Metadata Sources](../.github/workflows/translate-new-metadata.yml) chains only after upstream success, or manual invocation. It uses the same shared job/lock to prepare, translate, merge and repackage. Workflow definitions were checked locally; none were dispatched or published during implementation.

After a successful metadata or translation push, the shared job explicitly requests a GitHub Pages build through `POST /repos/{owner}/{repo}/pages/builds`. This is necessary because ordinary `GITHUB_TOKEN` pushes do not trigger branch-based Pages builds. The shared job and all four callers grant `pages: write`; no extra deployment secret is needed. The step verifies branch-based `/docs` publishing and requires the run to target the configured publishing branch (`main` here). It does not change Pages settings. A rejected build request fails visibly; a no-change rerun still requests a build, allowing recovery after a previous request failure. An accepted request queues GitHub's asynchronous Pages build; its eventual status is visible in Pages build/deployment runs. See the [official Pages build API](https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build).

## Validation and remaining limits

Default bounded catalog samples produced:

| Source | Catalog pages | Distinct catalog IDs | Detail requests | Total requests including bootstrap |
| --- | ---: | ---: | ---: | ---: |
| Naver | 6: two per tier | 120 | 5 | 14 |
| Munpia | 2 | 79 | 5 | 7 |
| Joara | 6: two per store | 118 | 0: list metadata available | 8 |

Additional ranking checks used three Naver requests, four Munpia requests including one detail, and three Joara requests. They verified six Naver boards, Munpia's three boards with 103/103/218 rows, and Joara Today Best with 100 rows. Separate fixture capture used eight anonymous requests and included Joara V1 detail metadata. Request reports contain only permitted catalog/detail/ranking routes and necessary public bootstrap assets.

Catalog plus ranking-discovered staged data contained **491 Naver, 481 Munpia and 214 Joara records**. All passed preparation, mocked English translation, merging, packaging and artifact validation, producing 128 synopsis shards and a deduplicated 100-record top bundle per source. Mocked English was not promoted. All 457 production data files remained unchanged.

After the final resume/allowlist fixes, a smaller fresh smoke check used seven Naver, two Munpia and six Joara requests. It observed 60/40/59 listings respectively, with one successful detail request each, including Joara's V1 endpoint. Those records also passed the complete mocked translation/build validation. These checks remained isolated in `.cache/metadata-implementation`.

Offline tests cover mappings, pagination/end conditions/repetition, malformed/restricted responses, units, booleans/dates, interruption/resume/history, failed boards, equal cross-platform IDs, reversible text and stale patches, schema/manifests/shards/top, import safety, dry runs and allowlists. Node tests cover browser helpers. Fully intercepted Chromium tests exercise all six sources, ranks, hash restoration, lazy synopses, missing manifests, failed chunks and source switching, without external browser requests.

The final run passed **151 Python tests** (including three Chromium scenarios and existing Novelpia metadata regressions) and **six Node tests**. Workflow YAML, exact source schedules, documentation links and the complete 42-module script inventory were checked separately.

```powershell
python -m pytest tests/test_metadata_common.py tests/test_metadata_naver.py tests/test_metadata_munpia.py tests/test_metadata_joara.py tests/test_metadata_pipeline.py tests/test_metadata_frontend_browser.py tests/test_novelpia_metadata.py -q
node --test tests/test_metadata_frontend.cjs
node --test tests/test_metadata_pages.cjs
```

The Pages trigger has five additional offline tests that execute its workflow script with mocked GitHub responses, checking request order, configuration/branch guards, failure handling and permission propagation. No live build request was sent while testing this fix.

Full-catalog completeness, deep-page limits, sustained crawl rates, every ranking window and all restricted/publication tiers remain untested. Moving catalogs overlap and totals change; sample counts are observations, not platform totals. Naver league-promotion continuity and Joara client-asset changes need ongoing verification. Separate local processes must not write the same source state concurrently; scheduled jobs use the shared lock. These limitations remain visible in coverage and outcomes; they do not establish deletion or cross-platform matching.

See the [complete pipeline reference](metadata-pipeline.md) for all legacy scripts and source schemas, or return to the [README](../README.md).
