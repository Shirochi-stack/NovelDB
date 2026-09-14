# Naver, Munpia, Joara and Ridibooks metadata integration

Initially implemented on **2026-09-13**, with collection, workflow and website fixes validated on **2026-09-14**. These sources have anonymous collectors, durable history, translation preparation and merging, packaging, browser support and scheduled workflow definitions. New sources remain unavailable in the selector until their manifests exist. **No new-source catalog was published during implementation.**

The initial implementation preserved all 457 then-existing files in `docs/data`. The September 14 validation separately preserved all **879 files across `docs/data` and `metadata/state`** byte-for-byte. Verification used small anonymous samples and mocked translation. No account login, saved profile, authenticated launcher, chapter downloader, translation API, workflow dispatch or publishing operation was run.

## Scope and commands

Collectors retrieve public titles, authors, synopses, cover URLs, genres/tags, completion, available statistics and official destinations. Account and age-verification gates are skipped. Images remain URLs. Identity is `(source, numeric-string ID)`; automatic matching of moved novels is outside this implementation.

Naver covers Challenge, Best and Series Edition on `novel.naver.com`. Series storefront links can be outbound purchase destinations, but that separate storefront is not crawled.

Install the project requirements (`requests`, `beautifulsoup4`, and `curl_cffi` for Ridibooks). All four entrypoints use the same CLI:

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

The [common pipeline](../scripts/metadata_pipeline.py) provides `prepare`, `translate`, `merge`, `build`, `promote` and `run`. Replace `naver` with `munpia`, `joara`, or `ridi`:

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

`translate` explicitly calls the existing Korean translator and uses `gpt-5.6-luna` by default and forwards explicit provider/model overrides. `run --translate` opts in; otherwise `run` collects, prepares and builds only. `run --dry-run` stops before all downstream writes. Incomplete catalogs need explicit `--allow-partial` promotion; scheduled jobs make that choice so validated partial originals are usable while discovery continues. Shared-tag promotion separately requires `--include-tags`.

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

[app.js](app.js), [metadata-core.js](metadata-core.js) and [index.html](index.html) register all seven sources. All Sources iterates the registry and isolates failures. New manifests determine chunk counts; missing manifests leave options unavailable. Progressive callbacks check cancellation. Incomplete coverage or failed chunks produce a partial-results indication.

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

Offline tests cover mappings, pagination/end conditions/repetition, malformed/restricted responses, units, booleans/dates, interruption/resume/history, failed boards, equal cross-platform IDs, reversible text and stale patches, schema/manifests/shards/top, import safety, dry runs and allowlists. Node tests cover browser helpers. Fully intercepted Chromium tests exercise all seven sources, ranks, hash restoration, lazy synopses, missing manifests, failed chunks and source switching, without external browser requests.

The final run passed **151 Python tests** (including three Chromium scenarios and existing Novelpia metadata regressions) and **six Node tests**. Workflow YAML, exact source schedules, documentation links and the complete 42-module script inventory were checked separately.

```powershell
python -m pytest tests/test_metadata_common.py tests/test_metadata_naver.py tests/test_metadata_munpia.py tests/test_metadata_joara.py tests/test_metadata_pipeline.py tests/test_metadata_frontend_browser.py tests/test_novelpia_metadata.py -q
node --test tests/test_metadata_frontend.cjs
node --test tests/test_metadata_pages.cjs
```

The Pages trigger has five additional offline tests that execute its workflow script with mocked GitHub responses, checking request order, configuration/branch guards, failure handling and permission propagation. No live build request was sent while testing this fix.

Full-catalog completeness, sustained production crawl rates, every ranking window and all restricted/publication tiers remain untested. Selected Joara deep-page limits were reproduced on September 14 as documented below. Moving catalogs overlap and totals change; sample counts are observations, not platform totals. Naver league-promotion continuity and Joara client-asset changes need ongoing verification. Separate local processes must not write the same source state concurrently; scheduled jobs use the shared lock. These limitations remain visible in coverage and outcomes; they do not establish deletion or cross-platform matching.

See the [complete pipeline reference](metadata-pipeline.md) for all legacy scripts and source schemas, or return to the [README](../README.md).


## September 14: throughput, coverage and operational fixes

The local Naver snapshot of 1,420 records came from a **rankings** operation, not a catalog sweep; its saved state had no catalog partitions. Munpia's 21,316-record catalog run reached its five-hour budget after 536 pages and 21,304 details. Joara's earlier catalog encountered pagination errors; a subsequent successful ranking refresh did not prove catalog completeness.

Collection now discovers listings before detail enrichment. A persistent detail executor refills workers as each future finishes; it no longer waits for each batch of four or recompresses the complete state after every batch. Pending IDs use an ordered dictionary for efficient membership/removal. Defaults remain four workers, a shared 0.5-second minimum between request starts per host, and four bounded attempts respecting `Retry-After`. More workers cannot exceed that host pacing floor. Discovery is sequential within each pagination chain. Full enrichment can still require several five-hour runs.

Checkpoints are atomic compressed metadata state, written after 60 seconds or 500 changed records and forced at phase boundaries/budget stops/finalization. State adds scan IDs, checkpoint revisions, continuation claims and separate catalog/discovery, enrichment and ranking coverage. Existing state loads without a destructive migration; older partition cursors reconstruct catalog coverage when available. A daily ranking refresh cannot erase catalog errors or invalidate its continuation checkpoint. Restrictions and explicit unavailability preserve known metadata; absence never establishes deletion.

Live logs identify source, operation, worker/pacing settings, partition/page, rows/new IDs/cumulative records, available upstream totals, request rate, remaining time, retries, detail outcomes, checkpoint duration, stop reason and continuation status. Detail progress appears at least every ten seconds while workers are active. Workflows run Python unbuffered and write a readable job summary; diagnostic logs exclude account/application keys and response bodies.

### Joara boundary evidence

The old numbered latest-book requests reset at page 101. This was a scraper pagination defect, not a hard public catalog limit. Joara's public client uses `use_cursor_pagination=y`, keeps API `page=1`, and passes the response's top-level `cursor_point` into the next request. All five latest partitions now use this protocol. Completed-list pagination is unchanged.

Resume saves the opaque cursor together with the logical next-page checkpoint. Old latest-list checkpoints restart from the head to establish valid cursors; known novels and translations remain saved, and finished-list checkpoints remain intact. Missing/non-advancing cursors, repeated pages and invalid responses stop with diagnostics. A bounded live check traversed 103 Free latest batches: 2,060 unique novels, including 20 new rows each at batches 100, 101, 102 and 103, with no duplicate IDs. This verifies passage beyond the previous failure, not a completed backfill.

The saved 41,911-record Joara snapshot stopped seven catalog partitions: all five latest-book windows at page 101, Free finished at page 621, and Noblesse finished at page 75. Premium finished reached its end. A later check of the two failed finished pages found usable rows alongside a blank-title entry: ID `212133` on Free page 621 and ID `1556465` on Noblesse page 75. Previously, one such entry rejected its entire page and left all later pages in that partition unscanned. The collector now retains valid rows and continues past these specific blank-title entries, recording each omitted ID, page and row in coverage. These omissions still prevent a complete-catalog claim. Repeated whole pages, reset pagination, malformed envelopes and unexplained empty pages still stop that partition with explicit diagnostics. Overlapping catalog windows and retained historical records mean the number of missing unique novels cannot be inferred from page totals.

Resuming a scan retains its recorded row omissions. To retry omitted listings after the source fixes them, run a fresh local catalog pass without `--resume`, for example `python scripts/metadata_pipeline.py run --source joara --mode catalog --output-dir .cache/metadata-build/joara --state-dir metadata/state`. A fresh pass resets pagination checkpoints while retaining known records and translations; the hosted catalog/resume workflows continue existing checkpoints.

### Workflow and translation controls

| Operation | Behavior |
| --- | --- |
| `catalog` | Resume an unfinished catalog scan; after a completed pass, start a fresh scan from the head; discover first, enrich details, then refresh native boards |
| `resume` | Explicit continuation using the same durable scan behavior |
| `rankings` | Refresh native boards and missing metadata for ranked records; retain independent catalog coverage |
| `build` | Prepare and package saved metadata without scraping |

Manual source workflows expose `workers` (default 4, maximum 16) and `auto_continue` (default true). Leave `expected_scan` and `expected_revision` blank for manual runs; automatic continuations populate them. The shared job checks/claims that checkpoint under `data-write-lock` before collecting, rejecting stale or duplicate continuations.

A successful original-metadata update publishes independently, then triggers the translation workflow. After that translation attempt—even if translation fails or its key is missing—the coordinator dispatches an eligible resumed catalog. Eligibility requires a budget stop, advancing durable progress and no collection failures/coverage limitations. Cancellation, completion or no progress stop automatic dispatch. To pause a chain, cancel its active translation/continuation run and any already queued resume; to work manually, launch with `auto_continue=false`. After resolving a failure, manually choose `resume`. No PAT is needed: continuation uses `GITHUB_TOKEN` with `actions: write` in the coordinator.

All data-lock workflows use `group: data-write-lock` and `cancel-in-progress: false`, and check out the current branch head after acquiring the lock. The lock is claimed once by the reusable job, never by a caller waiting on it. Standard concurrency retains one active and one pending run; additional arrivals can replace a pending run. If a pending continuation is canceled, manually choose `resume`; durable metadata is preserved. Metadata publication still requests the existing Pages build explicitly. Translation success is not required for original metadata publication.

All seven platforms' translator defaults and scheduled/manual fallback models are **`gpt-5.6-luna`**. Set repository secret **`OPENAI_API_KEY`** in Settings → Secrets and variables → Actions. Recognized GPT models use OpenAI credentials; a DeepSeek key is never substituted. A configured `TRANSLATION_API_KEY` or explicit provider/model/base-URL override retains its existing precedence. Luna uses Chat Completions with `reasoning_effort=none` and `max_completion_tokens`, omitting sampling temperature. Missing credentials produce a provider-specific error. Existing valid English remains active while its original is unchanged; this change does not bulk retranslate historical English.

Translation output defaults to **16,384 tokens per request** across all platforms, including Naver, Joara and Munpia. Every Translate workflow exposes `output_token_limit` under **Run workflow**. For automatic runs, set the Actions repository variable `TRANSLATION_OUTPUT_TOKEN_LIMIT`; manual input takes precedence, and clearing it uses that variable (or 16,384 if unset). Use a positive integer supported by the selected provider/model. Local translation accepts the same environment variable or `--output-token-limit`, with the CLI option taking precedence. With the default compression factor of 2.0, the input chunk target is 8,192 tokens; complete rows remain unsplit.

### Website loading and packaging

Cards are reconciled by `(source, ID)` as progressive catalog updates are coalesced. Unchanged cards, image nodes and loaded synopses survive; changing fields update their card without clearing cover URLs. Native board identifiers and complete labels remain in manifests/tooltips, while compact option labels and a bounded Sort control leave room for Audience beside it.

**Load descriptions** is enabled by default and saved as `noveldb.loadDescriptions` in local storage. Disabled mode hides descriptions, disconnects lazy observation, cancels queued/in-flight synopsis work, ignores late responses, and skips description-bearing top bundles in favor of catalog chunks. Re-enabling fetches missing visible synopses. This is a browser preference independent of URL filters. Existing source data formats remain compatible.

Coverage messages distinguish ranking-only initialization, active catalog collection, missing detail enrichment and unavailable catalog pages. Failed browser chunks are reported separately. Catalogs still use source-specific `.json.gz` chunks, top bundles and **128 gzip synopsis shards**; positional metadata-v1 rows are unchanged.

### September 14 validation

| Source | Catalog pages | Unique staged records | Successful details | HTTP requests |
| --- | ---: | ---: | ---: | ---: |
| Naver | 3: one per tier | 60 | 2 | 8 |
| Munpia | 1 | 40 | 2 | 3 |
| Joara | 3: one per store | 59 | 0: listing metadata sufficient | 5 |

Each source was capped at ten requests, one page per tier and two details. All samples passed extraction, mocked translation, merging, gzip packaging and manifest validation; each produced 139 validated artifacts including 128 synopsis shards. Staging evidence is in ignored `.cache/metadata-fixes-validation/validation.json`. Hashes confirmed all 879 production catalog/corpus/state files unchanged. Separate bounded public-asset/category probes established the Joara evidence above.

Offline regression tests cover discovery-before-details, continuous worker refilling, bounded checkpoint frequency, resume across ranking refreshes, failed-board/history preservation, no-progress stopping, duplicate/stale dispatch, Luna credential/payload routing, and Joara reset/short/duplicate responses. Intercepted browser fixtures cover all seven sources, failed chunks, switching during loading, native ranks, persistent description suppression, late-response cancellation and card/image node identity. No full backfill, account login, paid translation, production workflow dispatch or publication was performed.

The first September 14 update added `queue: max` and suppressed its local validation error. Removing that setting did **not** resolve the instant failures: Joara runs 34820049974 and 34820029571 used the corrected commit `867af0f` and still failed with zero jobs. GitHub returned no error annotation, so the earlier attribution to concurrency was not confirmed.

The new manual `workers` input also crossed the reusable-workflow boundary without conversion. Dispatch numeric inputs can arrive as strings, while `workflow_call` requires a number ([reported Actions issue](https://github.com/actions/runner/issues/2848)). All three callers now use `fromJSON(format('{0}', inputs.workers || 4))` to handle manual/API strings, numeric values, and missing scheduled inputs. Offline tests using GitHub's `@actions/expressions` engine reproduce the old string result and check the actual caller expressions against the reusable workflow's declared types, including operation, continuation flags and checkpoints. Run `npm ci --prefix tests/workflows` then `npm test --prefix tests/workflows`. Syntax checks also run with `actionlint` without ignored diagnostics. This fixes a reproduced input-type defect; confirmation that it resolves these particular GitHub runs requires a new run after pushing the patch. No workflow was dispatched or published during validation.


## Ridibooks integration (September 14)

`scrape_ridi.py` uses the public website's `/v2/category/books` and `/v2/category/books/total-count` API routes. Four webnovel categories are included: Romance (1650), Romance Fantasy (6050), Fantasy (1750), and BL (4150). Ebooks, comics and episode content are outside this collector's scope. Catalog discovery uses the All tab, `order_by=recent`, and 60-row offsets; totals and row counts must agree. Weekly/monthly bestseller boards are separate native top-100 lists per genre.

The mapping follows the official frontend's serial renderer: canonical `bookId`, whole-work `serial.title`, cover and episode total, contributor roles, categories, public introduction, explicit completion/age values, and weighted native rating/count. Missing views, likes, dates and synopses remain unknown. Listing introductions come from the API's description field rather than the page's truncated preview. No account session or age-verification bypass is used.

The source key is `ridi`, including in `metadata_site.bat` and `metadata_pipeline.py`. `Update Ridibooks Metadata` schedules weekly catalogs on Thursday at 08:00 UTC and daily rankings at 19:00 UTC. It uses the common resume, continuation, translation, validation and packaging stages; translation inherits the configurable 16,384-token default. The site enables Ridibooks only after a validated manifest is published.

**Live access verified with the browser-compatible transport:** standard Python Requests received Cloudflare HTTP 403, whereas a fresh anonymous `curl_cffi` Chrome-compatible connection returned JSON successfully. Ridibooks now supplies this session through the common HTTP client, preserving URL/redirect allowlists, request/runtime budgets, pacing, bounded retries, and sanitized logs. No browser profile, account login, or saved cookies are needed. The workflow installs the same transport dependency.

A live staged run collected 480 unique novels and 480 synopses, then built the catalog and all 128 synopsis shards. Further probes checked the first two pages of all four genres and deep pages. **The API separately requires offsets below 6,000.** Fantasy reports 15,309 works, so this collector cannot claim a complete fantasy catalog from its main category: it records a coverage error at page 101 rather than treating the first 6,000 as the entire source. This is an explicit API validation response, independent of the resolved HTTP 403 issue. Staged validation does not publish data or run paid translation.

```powershell
python scripts/metadata_pipeline.py run --source ridi --mode sample --output-dir .cache/ridi-sample --state-dir .cache/ridi-state
```


## R19 support and Naver Series (September 14)

- **Ridibooks:** already collects explicit R19 flags and public synopses anonymously. A 240-record genre sample contained 106 R19 works with synopses.
- **Naver Series (`naverseries`):** newly added as a separate source, preserving its independent product ID namespace. Collects eight public novel genre lists, including explicit `.n19` badges, titles, authors, ratings, volume/episode counts, and synopsis previews. A staged 50-record sample contained 38 R19 works; eight first-page genre probes returned 200 records, including 49 R19 listings. Full adult details redirected to Naver login/age verification; public previews remain available and are labelled “Synopsis preview” on the site. Tested non-adult details returned full synopses and an explicit age rating. No reader or account routes are fetched.
- **Joara:** a known R19 detail request explicitly required login and verification. The parser now classifies that response as restricted and records age 19 for already-known works instead of reporting a generic parse failure. Anonymous adult-catalog discovery remains unimplemented.
- **Munpia:** anonymous `adultOnly=true` requests returned HTTP 400 even with complete catalog parameters. `adultMode=true` with `adultOnly=false` returned the default public policy/catalog. Adult-specific discovery remains unverified and is not enabled.

Series uses the common prepare/translate/build/promote stages, compressed synopsis shards, and the existing Audience → Adult Only (19+) filter. `Update Naver Series Metadata` collects weekly on Friday at 08:00 UTC and offers manual catalog/build/resume. The shared translation workflow accepts `naverseries`. No native Series ranking board was verified, so catalog sorting is not presented as a ranking. Sources that explicitly lack ranking support can complete catalog collection without fabricated ranking data.

```powershell
python scripts/metadata_pipeline.py run --source naverseries --mode sample --output-dir .cache/naverseries-sample --state-dir .cache/naverseries-state
```

Live validation used staging only; no production catalog or translation API was changed.
