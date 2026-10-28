# Hard sources: qdn and rbooks

Contract for the two browser-driven sources, shared by the Android and iOS apps. This is a copy of `android/docs/hard-sources.md` with an [iOS section](#ios) at the end; the Android implementation is the reference; the desktop code it ports is `_qdn_*` / `_rbooks_*` in `external_scraper.py` and `qdn_font_decoder.py`.

Both sources need a real rendering engine and the user's own signed-in session:

- **qdn** encrypts bought chapters, decrypts them with the site's own script, and then draws the text with scrambled fonts.
- **rbooks** reads chapters through the site's web viewer, lists episodes through a book API that only a real browser navigation can reach, and serves comics as page images.

The split is deliberate. **All page-side logic is one JavaScript file** that runs unchanged in Android `WebView` and iOS `WKWebView`. The native side only navigates, keeps the session, paces loads and turns the script's JSON into `BookInfo` / `ChapterResult`. Nothing here says what the Swift types look like; it says what to call, what comes back, and what must hold.

## Files

| File (under `android/sources/src/main/resources/com/shirochi/npia/sources/hard/`) | What |
|---|---|
| `hard-sources.js` | The shared script (about 100 KB, plain ES2020, no dependencies). Copy it byte for byte into the iOS bundle. |
| `qdn/NotoSansCJKsc-Regular-hanzi.otf` | Reference font for glyph matching (2.2 MB, OFL, `qdn/OFL.txt`). |
| `qdn/zh_char_frequency.txt` | `<char>\t<count>` per line (80 KB). Settles look-alike glyphs. Optional but expected. |
| `qdn/qdn_verified_maps.json` | Glyph answers checked against screenshots for exact font bytes (13 KB). Optional. |

Native Kotlin lives in `sources/.../hard/HardScripts.kt` (loading and calling), `qdn/` (`QdnAdapter`, `QdnDecoder`, `QdnSite`, `QdnModels`), `rbooks/`, and `session/` (`SiteSessions`, `HardSessions`). Site names and cookie names are stored as Unicode escapes in `QdnSite.kt` and `RbooksSite.kt`; read the exact strings there. This document uses codenames.

Tests: `tests/test_hard_sources_js.py` runs the script in Playwright Chromium (`python -m pytest tests/test_hard_sources_js.py`). `tests/hard_sources_support.py` builds the synthetic reader and the saved-page fixtures in `tests/fixtures/hard_sources/`. Kotlin tests are `QdnAdapterTest`, `RbooksAdapterTest`, `SiteSessionsTest` in `:sources`, driven by a scripted fake page (`HardTestSupport.kt`) that answers the entry points below with canned JSON.

## Loading the script

`window.__npiaHard` is the namespace: `{version: 1, qdn, rbooks, util, tasks}`. The file is idempotent and safe to run any number of times.

Two ways to load it, both used:

1. **Document-start user script on every page of the site** (`addDocumentStartJavaScript` / `WKUserScript` at document start, main frame only). This matters for qdn: the script installs a `FontFace` hook before the reader runs, because the reader builds one cipher font in script and that font is in no stylesheet. The hook records `{family, source bytes, descriptors}` in `window.__npiaFontFaces` and leaves the real constructor working. Pages without document-start scripts still work for everything except that one scripted font, so the native side falls back to evaluating the file after load (the Android engine does).
2. **Plain evaluate into a page that has no such script**, for example the blank decoder page. `(0, eval)(source)`; check `!!window.__npiaHard` first so the 100 KB text is only sent when needed.

Entry points return JSON-able values or promises of them. Native code calls them with an async function body: `return await window.__npiaHard.qdn.pageState();`. A call that can run longer than the web view's script timeout (60 s on Android) must go through **tasks**:

```
tasks.start(path, argsArray) -> {ticket}
tasks.poll(ticket) -> {state: "running"} | {state: "done", json: "<JSON text of the result>"} | {state: "error", error}
```

`path` is a dotted entry point (`"qdn.extract"`, `"qdn.decoder.decodeJson"`). The result arrives as JSON text so it can be passed on without being parsed. A finished ticket is collected once.

## Desktop identity (native)

Both sites serve a phone a different site, or none that works. Every page that runs a source uses:

- a desktop Chrome user agent (the constant in `QdnSite.kt` / `RbooksSite.kt`; Chrome 137 on Windows), and
- a **layout viewport of 1280 x 800 CSS px** (900 for rbooks). Android: view size = CSS size x density, `useWideViewPort = false`, so the viewport tracks the view. iOS: a `WKWebView` with a 1280-wide frame and `preferredContentMode = .desktop`, plus the user agent.

`WebPageDriving.emulateDesktop(userAgent, width, height)` is the Android seam for this; call it before the first `load`.

Pages must stay "visible" to the engine, or timers and animation frames stall and the reader never finishes (Android: attached behind the activity; iOS: in a window, as the existing `WKWebEngine` does). They throttle when the app goes to the background; see Limits.

## qdn

### URLs

The desktop host is `www.<domain>`; a phone is redirected to `m.<domain>`. Book pages are `/book/<id>/` and chapter pages `/chapter/<book id>/<chapter id>/` on **both** hosts, and on the bare domain. `QdnSite.matches`: host is the domain or a subdomain, and the path starts `/book/<digits>` or `/chapter/<digits>`. The home page, rankings, search, listings and `/book/` without an id do not match.

`canonicalUrl(url)` turns any matching link into `https://www.<domain>/book/<id>/`. It is applied to anything the user adds (Add screen text, share sheet, link cart, queue), and `parseBook` reads the desktop address whatever it was given. So a link found in the Browser tab on the phone site downloads and shows "Download this book".

The same parent-domain cookies serve both hosts (see Session); the session reads cookies for `www` and `m`.

### Book (`parseBook`)

1. Restore the saved login once per process (Session).
2. Open a page (desktop identity, script at document start), load the desktop book URL, `DOM_CONTENT_LOADED`, 45 s; a load timeout is a warning, not an error.
3. Poll `qdn.pageState()` once a second, up to 45 times, until `state != "waiting"`.
   - `"ready"`: go on.
   - `"verification"` (a captcha): throw `LoginRequired` once per book, so the pipeline shows the sign-in browser on the book page where the user passes the check, then starts over. The second time for the same book, throw `Blocked` ("complete it in the Browser tab"). A later download asks afresh.
   - `"blocked"` (the site's firewall page): throw `Blocked`; waiting is the only cure.
   - still `"waiting"`: `Parse` error with the page title and text.
4. If any login cookie is present, call `qdn.userInfo()`. `{live: false}` means the remembered login expired: remove the login cookies (the site renews from its remember-me cookie only when they are absent) and log a warning.
5. `qdn.bookInfo()`, then, **when `totalChapters > chapters.length` and `catalogUrl` is set**, load `catalogUrl`, poll `qdn.catalogState()` until ready (`verification` / `blocked` handled as above), and use `qdn.catalogInfo().chapters` if it has more. A long book's page lists only its first chapters (a 1,418-chapter book listed 35), and the desktop reads only those.
6. Build `BookInfo`: language `zh`, chapter id = last number in the chapter URL, `isPaid = isVip`, URL = the desktop book address.
7. **Sign-in prompt.** If a VIP chapter is in the selected range (range and "skip paid" from the run settings), the session is not usable, and this book has not asked yet: throw `LoginRequired`. The pipeline opens the sign-in browser and calls `parseBook` again; the second call goes on whether or not the user signed in, and the key is cleared so a later download asks again. A usable session clears the key and saves a fresh copy.

### Chapter (`parseChapter`)

Per chapter, on a pooled page. At most 3 pages whatever "threads" says (each is a full desktop reader).

1. **Pace.** Page loads are at least 2.5 s apart across all workers (a shared gate). The user's rate-limit pause still applies after each chapter.
2. `load(chapterUrl, DOM_CONTENT_LOADED, 45)`; a timeout is a warning.
3. `qdn.chapterState()` -> `{ready, blocked, verification, encrypted}`. If `blocked`: wait out the firewall with cooldowns of 60, 180, 420, 900 s and load again; after the last, fail the chapter ("blocked by the firewall; wait a while").
4. Poll `chapterState` once a second, up to 60 times, until `ready`. Timeout fails the chapter.
5. `qdn.encryptionState()` -> `{encrypted, rendered, failed, blocked, words}`.
   - Not encrypted: `qdn.readChapter()`.
   - Encrypted: the bought-chapter flow below.
6. `qdn.readChapter()` returns `{lines: [...]}` or `{error, reason?, chars?, expected?}`:
   - `error: "locked"`, `reason: "login"` -> **Refused**: "preview only: the app is not signed in to qdn (Settings > Enter App)" plus `(chars/expected characters)`.
   - `error: "locked"`, `reason: "purchase"` -> **Refused**: "preview only: this account has not bought the chapter".
   - `error: "locked"` without a reason -> **Refused**: "locked, or sign-in required".
   - `error: "verification"` -> **Failed**: open the Browser tab, complete the check, retry.
   - other errors, and no lines -> **Failed**.
   Lines become `<p>` paragraphs in a `<div class="qdn-content">` (a first paragraph equal to the chapter title is dropped).

### Bought (encrypted) chapters

The reader decrypts the chapter in the page, which takes 10 to 60 s, then draws it with two cipher fonts. Five protections, each handled in the script:

1. The text is encrypted and the site's own script decrypts it. Needs a real engine; headless engines fail. Native: poll `encryptionState` (every 300 ms, up to 500 polls) until `rendered`, `failed` (page text "章节加载失败") or `blocked`.
2. **Cipher fonts.** A stylesheet face for U+4E00-9FA5 (its URL rotates) and a per-chapter blob face for U+3400-4DB5, built in script (the hook). Each code point is drawn with another character's outline, jittered. The script matches them by rendering (below).
3. **Mirrored glyphs** (`transform: matrix(-1...)`), characters in random tags ordered by CSS `order`, `::before/::after{content: attr()}`, hidden decoys (`display:none`, zero-size clipped boxes, `opacity:0`). The extractor reads what is **painted**, by on-screen position, never DOM order.
4. The reader builds its own FontFace in script (the hook above).
5. **Off-screen paragraphs hold poisoned text** (antonym and pronoun swaps) and swap to the real text only within about 200 px of the viewport. The extractor zooms the page out so the chapter fits one screen and nudges the scroll (which reveals everything at once), then steps a screen at a time for anything missed, and reads each paragraph only after its rendering stops changing (at least 1 s, quiet for 150 ms). Paragraphs that never settle are reported in `unstableParagraphs`.

**Flow** (`QdnAdapter.decodeEncrypted`):

1. Start the decoder preparing in the background (below) while the reader decrypts.
2. After `rendered`: `tasks.start("qdn.extract", [{known: [<sha256 hex of fonts the decoder holds>]}])`, poll every 500 ms (up to 240). Result is the **payload**:

   ```
   {faces: [{family, range, url, urls, scripted?}],      // only fonts actually used
    fontData: {<url>: <base64>},                          // omitted for fonts in `known`; "ERR ..." on a fetch failure
    fontDigests: {<url>: <sha256 hex>},
    paragraphs: [[{parts: [{text, fonts}], mirrored, x, y, h, w, src}...]...],   // visual order, one entry per painted character
    dropped, duplicates, unstableParagraphs: [index...], skipped, scriptedCount, url, title}
   ```
   or `{error: "no reader"}`.
3. `decoder.decode(payload JSON text)` (below) -> `{lines, stats}`.
4. If `unstableParagraphs` or `missingFonts` is non-empty, extract and decode **once more** and keep the better one: any lines, then fewer issues, then more text.
5. `incomplete = words >= 500 && textLength < 0.8 * words`. Log a warning line listing `uncertain_glyphs` (low confidence), `suspicious_passthrough`, `missing_fonts`, `unstable_paragraphs`, `incomplete` when any is non-zero. The best text is saved anyway.
6. Result: paragraphs as above. A decoder failure fails only that chapter.

### The glyph decoder

The decoder is independent of the page it runs in, and Android runs it in a **dedicated blank page** (`about:blank`) that lives for the whole download, so the reference (a few seconds to build) is built once. Chapter pages only extract.

Resources go in by `qdn.decoder.putResource(name, index, count, base64Chunk)`, any order, chunks of about 190 KB of binary (**every chunk but the last must be a multiple of 4 base64 characters**; `name` is `font`, `frequency` or `verified`). Then call `qdn.decoder.prepare(budgetMs)` repeatedly (8 s budget) until it returns `{done: true, built, total}`. About 9,850 reference characters; 1.2 s on a desktop Chromium.

`qdn.decoder.knownFonts()` returns the SHA-256 digests of the cipher fonts the decoder holds; pass them to `extract` so each chapter does not resend the large stylesheet font.

`qdn.decoder.decodeJson(payloadJson)` (run it as a **task**; about 2 to 5 s on a desktop, longer on a phone for the first chapter) returns:

```
{lines: [string...],
 stats: {decoded, verifiedGlyphs, shapeMatchedGlyphs, lowConfidence, fonts: [family...],
         missingFonts: [{family, reason}...], suspiciousPassthrough, unstableParagraphs: [...],
         thresholds}}
```

How it decides (ported from `qdn_font_decoder.py`):

- Per character, pick the cipher font: walk the CSS font-family list; within a family the **last defined** face wins; a face covers a character if the code point is in its `unicode-range` and the font really draws it (drawing it with the font differs from drawing it with a family that does not exist; this works for woff2 as well as TrueType).
- **Verified map first**: an entry for the font's SHA-256, the code point and the mirror state gives the character directly. Only exact font bytes use it.
- Otherwise **shape matching**: render the glyph on a canvas at 96 px, crop to its ink, pad to a square, resample to 56 x 56, blur (sigma 0.5), mirror if the element was mirrored, mean-centre and normalise. Do the same for every reference character, then take the best by cosine similarity. A 14 x 14 version narrows the ~9,850 candidates to 48 first (this is only for speed on a phone; on the real captured chapter it changes nothing), then the 56 x 56 comparison picks. Among the top 5 within 0.01 of the best, the frequency prior (weight 0.1) breaks ties (好 against 奸).
- **Component refinement** for weak matches (best score below 0.90): if a candidate's left third matches almost exactly (>= 0.95) and the leader's does not (< 0.85), and the overall balance is within 0.02 of the leader, take it (借 against 惜, the person against the heart radical). The 0.02 slack is the one deliberate change from the desktop: a canvas rasterises a little differently from the desktop's FreeType and flipped a 0.003 near tie.
- A match is "low confidence" when its score is below 0.90 or its margin over the runner-up below 0.03.
- Characters in no cipher font stay as they are. If they are CJK and inside a cipher face's `unicode-range` they count as `suspiciousPassthrough` (legitimate fallback text, kept, but reported).

Accuracy on the only real data available: the captured chapter fixture (94 paragraphs, 2,197 characters, 1,729 cipher glyphs) decodes exactly with the verified table switched **off**, and exactly with it on without ever building the reference. See Limits for what that does and does not prove.

## rbooks

### URLs

`/books/<id>` (product), `/books/<id>/view` (viewer), `/library/books/<id>` (the user's library) on the site and its `www.` host. Category pages (`/category/books/...`) and the home page do not match. `RbooksSite.isViewerUrl` is `/books/<id>/view` or `/library/books/<id>`; `isAccountGate` is a path containing `/account/login` or `/account/verify-adult`, or a host starting `account.`.

### Book (`parseBook`)

1. Restore the saved login once per process.
2. **Library URLs** (`/library/books/<id>`): load it; if it landed on an account gate, or the session is not usable, throw `LoginRequired` (once per book, then `Blocked`). Poll `rbooks.libraryProbe()` up to 15 times until `viewer` or `product` is set. A viewer means an owned single book: one chapter whose URL is the library URL. A product link means: go on with that product id. `unavailable` / `adult` are logged ("complete adult verification in the Browser tab").
3. **Product page**: load `/books/<id>` (45 s, a timeout is a warning). Poll `rbooks.productReady()` up to 20 times, checking each time for an account gate (`LoginRequired`/`Blocked` as above). If never ready and the page title says "Just a moment" (a Cloudflare check): `LoginRequired` once, then `Blocked`.
4. `rbooks.productInfo()` -> `{title, author, synopsis, cover, publisher, tags, links, titleById, seriesId, diagnostics}`. `links` are the rendered `/books/<id>/view` links, `titleById` the titles near them.
5. **Episode list** (needs `seriesId`): open a second page (desktop identity) at `book-api.<domain>/books/<seriesId>` **as a top-level navigation**; the product page's CSP blocks the fetch and a non-browser client gets a separate Cloudflare challenge. Poll `rbooks.apiState()` (30 times) until `json: true`; `challenge: true` at the end means `LoginRequired` once, then `Blocked`. Then call `rbooks.discover(input)` in slices:

   ```
   input: {seriesId, renderedLinks, budgetMs}                 first call
          {seriesId, renderedLinks, budgetMs, state}          each later call (state = the one returned)
          {state, giveUp: true}                               stop with what was found
   result: {done, stalled, state, isSerial, error, diagnostics, titleById, links}
   ```

   Keep calling (budget 20 s per call) while `done` is false and `stalled` is false. `stalled` means the API stopped answering at `state.nextId`: reload the API page at `/books/<state.nextId>` and continue, up to 3 times, then call with `giveUp`. `diagnostics` carry progress lines; the state has `ids` and `total` for a progress message.

   What `discover` does: reads the root book, `total_book_count`, `is_serial`. A serial series whose ids are provably contiguous (the rendered run is `start..start+k`, and the root's `next_books` is `start+1`) is generated from the root and validated by one request for the last id (it must be serial, have the same total and no next book). Otherwise it follows `series.property.next_books` one request at a time (about 0.4 to 0.7 s each; 439 episodes took about 5 minutes on the emulator), pausing 300 ms every 20. Non-serial volumes follow the chain up to 100. Titles are generated for a contiguous catalog from the first title ("... 1화").
6. Chapters: `rendered links`, replaced by the API list when it is longer; titles from `titleById` (API wins), else "Episode N"; ids from the URL, de-duplicated. The catalog does not say whether the account owns an episode; the viewer decides.
7. Errors: nothing found and a discovery error -> `Blocked` ("complete verification in the Browser tab"); nothing found at all -> `Parse`.

### Chapter (`parseChapter`)

On a pooled page, at most 3 pages; the rate-limit pause applies after each chapter.

1. `load(viewerUrl, DOM_CONTENT_LOADED, 45)`. A timeout is tolerated only if the page is already the requested viewer.
2. If the URL is not a viewer URL: an account gate means **Refused** "signed out of rbooks: sign in in Settings > Enter App"; anywhere else means **Refused** "locked or not bought (redirected to <host>)".
3. Up to 3 attempts: poll `rbooks.contentState()` every 700 ms (43 times) until the viewer's text is >= 500 characters **and keeps the same length for two polls** (it is drawn in pieces). `refused: true` ends the wait at once. On failure call `rbooks.accessWall()`:
   - `wall && refused`: the title is refused by the viewer ("웹 뷰어에서 지원하지 않는 작품입니다", which it says for owned and unowned volumes alike): **ask the library** (next section).
   - `wall`: **Refused** "locked or not bought".
   - no wall: log "Content validation failed (attempt n/3)" and reload.
   After 3 attempts: **Failed** "the viewer did not finish drawing".
4. `rbooks.extract({fallbackTitle, chapterIndex})` -> `{title, content, contentText, images: [{url, name}]}` or `null`. `content` is an **XHTML fragment** (XML-serialised, no classes/ids/styles/`data-` attributes, `&#160;` not `&nbsp;`, zero-width characters removed, `class="<prefix>border"` divs became `blockquote`, centre divs became `<p align="center">`). Images are renamed `images/<chapter index, 4 digits>_<n, 2 digits>.<ext>` in the markup and listed with their absolute URLs.
5. Build `ChapterContent`: `<div class="rbooks-content">` + `content`. The chapter title is the viewer's, unless it equals the book title (a series title is never an episode title), then the catalog's. Images are downloaded **by the adapter with `Referer: <book URL>`** and the desktop user agent, because comic pages may not load without it (up to the image-worker count in parallel); a page that fails keeps its URL with no bytes, so the image stage retries; `data:` URLs are decoded in place; with "save image URLs only" nothing is downloaded.

### Asking the library

When the viewer refuses a title, `rbooks.ownedIds(ids)` (run on a page loaded at `library.<domain>/`, the origin whose CORS rules allow it) posts `{b_ids: [...]}` (at most 500: the book's episode ids plus the book id) to the library API with the session cookies and returns `{owned: [id...]}` or `{owned: null}`. Asked **once per download** and cached. Then:

- `owned` is null and the session is not usable: **Refused** "sign in in Settings > Enter App".
- `owned` is null and it is: **Failed** "the library could not verify access; retry".
- the id is not in `owned`: **Refused** "not in this account's library: buy or rent it first".
- the id is in `owned`: **Refused** "owned, but rbooks opens it only in its own apps or PC viewer; it cannot be exported on a phone".

## Session

A session is a set of cookies in the cookie store that web pages **and** the HTTP client share (Android `CookieManager` through `WebViewCookieJar`; iOS `HTTPCookieStorage.shared` with `WKWebsiteDataStore.default()`). Downloads never ask for a password.

| | qdn | rbooks |
|---|---|---|
| login cookies | `ywkey`, `ywguid`, `ywopenid` | access token, refresh token, global-service token (names end in `-at`, `-rt`, `-gs-at`; exact strings in `RbooksSite.kt`) |
| usable when | `ywkey` **and** `ywguid` present | any of the three present |
| cookie domain | `.<domain>` | `.<domain>` |
| read from | `www.<domain>`, `m.<domain>` | `<domain>`, `account.<domain>`, `library.<domain>` |
| sign-in page | `m.<domain>/user/` (the phone site's account page; signed out it redirects to the site's own passport sign-in with a return address, so the cookies land on the site) | `<domain>/account/login` |

Why the phone host's account page: a bare passport sign-in page would sign the user in at the passport domain without setting qdn's own cookies. The return address on that redirect does. The cookies are on the parent domain, so no separate handling of the phone host is needed beyond reading both hosts.

**Sign-in** is the app's own browser: the Enter App screen (and the pipeline's sign-in prompt) call `WebEngineProviding.presentLogin(url, profile)`, which shows a full-screen web view with Cancel and Done. The password manager can fill it. Cookies it sets are the cookies downloads use.

**Persistence.** qdn's `ywkey` / `ywguid` are session cookies. The desktop snapshots and restores them for that reason. On Android we measured the WebView storing session cookies on disk (`is_persistent = 0` rows survive a force-stop and relaunch), so a restart alone does not lose them; the saved copy is insurance for when they are lost anyway (cleared storage, a different WebView build, iOS). `SiteSessions`:

- `snapshot(site)`: store `{saved, cookies: {name: value}}` (login cookies only) as one value in the secret store (Keystore-backed on Android, Keychain on iOS), key `<source>.sessionCookies`. Nothing present keeps the earlier copy.
- `restore(site)`: for each saved cookie the store lacks, `setCookie(url, name, value, domain: ".<domain>")`; never overwrites. `restoreOnce` does it at most once per process per site, at app start and at the start of `parseBook`, so it can only bring back a login lost to a restart, never one the user ended in this run.
- `signOut(site)`: expire every login cookie for every read-from URL on the host and on each parent domain down to the registrable domain (`Max-Age=0` with and without the `domain` attribute), delete the saved copy. The Browser tab's own sign-out (per site, or "all sites") must call `forgetSavedCopy`, or the next start would restore the login.
- Called after: a sign-in browser closes (Enter App or pipeline prompt), a book is read signed in, a batch ends.

Verified on the emulator with placeholder values (no account): cookies set through the WebView show "Signed in (found ywguid, ywkey)"; the WebView alone keeps them across a force-stop; with the WebView's cookie database deleted and the encrypted copy kept they come back at launch; Sign out removes them and a restart does not bring them back.

**sfc** is unchanged: username and password go to the app API (`SfcAdapter.login`), or a pasted cookie header containing `session_APP` is saved (`saveAppCookie`); the cookie lives in the secret store under `sfc.appCookie`; Sign out clears it.

## Enter App screen

Reachable from **Settings** (row "Enter App", under npia login) and from the **Add** screen (an "Enter App" button under Download, as the desktop Download page has it). Settings-style column of sections:

- **sfc**: status line ("App session saved..." or "No app session saved..."), Username, Password, **Sign in**, an "App cookie" field with the hint `.SFCommunity=...; session_APP=...`, **Save cookie**, **Sign out** when a session exists, and a note that it is stored only in encrypted storage. Sign-in is disabled until both fields are filled; the password field is cleared after use.
- **qdn** and **rbooks**: status ("Signed in (found ...)", "Partly signed in: found X, but ywkey and ywguid are needed", "Not signed in. A saved copy exists...", "Not signed in. Free chapters still download."), help text, **Sign in** ("Sign in again" when signed in) which opens the site's page in the app's browser and re-reads the status when it closes, and **Sign out** (clears the cookies and the saved copy).

The status comes from the cookies (`SiteSessions.status`); it does not ask the site. A cookie can still be expired (the site's own check, `qdn.userInfo()`, runs at the start of a qdn book).

## Limits and what differs from the desktop

- **Only free chapters were exercised live.** No account was used. Everything about bought content (decryption in a WebView, the cipher fonts of a real chapter, the real poisoned paragraphs, ownership answers) is covered only by the captured fixture and the synthetic reader, not by the live sites. The captured chapter's DOM was reconstructed, not preserved, so it proves the decoder, not complete live-site capture accuracy.
- **Whether the reader decrypts in a WebView is unknown.** The desktop needs a real headed Chrome (headless fails). Android WebView and WKWebView are real engines, but the site may behave differently, for example by its user agent or by checks for embedded browsers.
- **Viewport.** The first page a fresh Android process creates has reported twice the requested layout viewport (2560 x 1600 on the emulator; later pages report 1282 x 801). Nothing in the script depends on it (the decoder and extractor were also run in a 980 x 612 page and in the emulator's WebView), `qdn.pageState().viewport` reports it and the adapter logs it ("Reading as a desktop browser (W x H px)").
- **Background.** Web views throttle timers when the app is not visible. A bought chapter's 10 to 60 s decryption and the paragraph scrolling may stall if the user leaves the app; keep the app open (the existing "keep screen awake while downloading" setting helps).
- **Pace.** Chapters load at least 2.5 s apart, and a firewall block waits up to 60, 180, 420, 900 s per chapter. A long VIP book takes a long time by design.
- **Not possible on a phone.** The desktop's Windows-only PC-viewer export of titles the web viewer refuses ("app only") has no counterpart: such chapters are refused with an explanation. The desktop `Referer` on page loads is not set (the web view APIs used here have no per-load referer); the sites read both without it.
- **Full catalog.** The desktop reads the chapters the book page lists, which is only the first part of a long book. Android reads the catalog page when the book page announces more.
- **A selector the desktop lost.** The viewer container `[id^="<prefix>_c"]` was renamed by a codename pass and no longer matched; the script tries both the codename and the real prefix (the other container selectors cover the viewer anyway).
- **The reference font** is the same Noto Sans CJK SC subset the desktop bundles. Characters outside it (about 555 rare characters in the frequency list) cannot be matched and pass through.

## iOS checklist

1. Copy `hard-sources.js` and the three `qdn/` resources into the app bundle unchanged.
2. A page factory with desktop identity (user agent, 1280 px width), visible to the engine; a blank decoder page; the document-start user script (main frame only); an evaluate fallback.
3. `tasks.start` / `tasks.poll` helper with a poll interval and a poll limit per call.
4. Decoder: push the three resources in chunks, `prepare` in slices, `knownFonts`, `decodeJson` as a task.
5. `SiteSessions` over `HTTPCookieStorage` and the Keychain, including `remove` (expire on the host and parent domains), `restoreOnce`, and the Browser tab sign-out hooks.
6. The two adapters following the flows above; `LoginRequired` once per book for sign-in or verification; the pacing (2.5 s gate, 60/180/420/900 s cooldowns); at most 3 pages.
7. `canonicalUrl` for qdn applied to every entry point that accepts a link; the Enter App screen and the Settings and Add entries.
8. Reuse the Playwright suite as the check that the file is unchanged; add a WKWebView smoke test that runs `window.__npiaHard.qdn.decoder` on `tests/fixtures/qdn_chapter76_capture.json` to see that canvas matching works under WebKit (the one part that differs by engine).

## iOS

The iOS app (`ios/NpiaKit`, branch `ios/hard-sources`) follows the design above. This section says where things are, what WebKit needed beyond the contract, and what was verified.

### Where things are

| What | Where (under `ios/NpiaKit/Sources/`) |
|---|---|
| The shared script and its resources | `NpiaSources/Hard/HardResources/` (`hard-sources.js`, `qdn/NotoSansCJKsc-Regular-hanzi.otf`, `qdn/OFL.txt`, `qdn/zh_char_frequency.txt`, `qdn/qdn_verified_maps.json`), bundled as a SwiftPM resource folder |
| Loading and calling the script | `NpiaSources/Hard/HardScripts.swift` (`ensureHard`, `hardObject`, `hardTask`, `HardJSON`), `HardSupport.swift` (pacer, once-per-book prompts, page factory, concurrency) |
| qdn | `NpiaSources/Qdn/` (`QdnSite`, `QdnModels`, `QdnDecoder`, `QdnAdapter`) |
| rbooks | `NpiaSources/Rbooks/` (`RbooksSite`, `RbooksModels`, `RbooksAdapter`) |
| Sessions | `NpiaSources/Session/` (`SiteSessions`, `HardSessions`), `NpiaCore/SessionCookies.swift` (`SessionCookieAccess`, `CookieDomain`, `HTTPCookieStorageAccess`), `NpiaWebEngine/SessionCookies.swift` (`WebKitSessionCookies`) |
| Desktop identity in WKWebView | `NpiaWebEngine/WKWebEngine.swift` (`emulateDesktop`, `addMainFrameInitScript`) |
| Enter App screen | `ios/App/Views/EnterAppView.swift`; entries in `SettingsView` (Downloads > Enter App) and `AddURLView` (under Download) |

The script and its four resources are the Android bytes (they are marked `-text` in `.gitattributes`, so no line-ending change reaches them). `HardResourcesTests` checks their SHA-256 against Android's committed blobs:

| File | SHA-256 |
|---|---|
| `hard-sources.js` | `f1b4f3e53c6e999eac4ae21ff6864fddb469eb3d86f5069764ce1a7ba0f6c289` |
| `qdn/NotoSansCJKsc-Regular-hanzi.otf` | `81805f27acfad19ca7509a17e0b8d2fdf0bb944558612de120047b194f195eaa` |
| `qdn/OFL.txt` | `acc077d31316a689a0b0512998c47331278e4ae61d134e1edeae5a7a95916961` |
| `qdn/qdn_verified_maps.json` | `9e6ff0c465fa50b511eede08bf07272fa39d7a32d49ff836af3e315892b90793` |
| `qdn/zh_char_frequency.txt` | `555c0d7862cf307c7aeacf3091e081dc8e80ca6e60baf79b93d7ff26af1ff7e2` |

(On a Windows checkout with `core.autocrlf=true`, the working copies of the three text files are CRLF; the committed blobs are LF and are the ones that count.)

### What WebKit needed beyond the contract

**1. Telling a character a cipher font lacks from its own (a patch applied at load time).** The decoder decides whether a cipher font really draws a character by drawing it with the font and with a family that does not exist; a character the font lacks comes out the same both ways (`coreCovers`). WebKit does not give the same answer: a loaded font asked for a character it lacks falls back to the **sans-serif** fallback, a family that does not exist to the **serif** one. Measured on the captured chapter's first cipher font (a woff2 face that lacks 36 of the chapter's characters, 90 occurrences): the unknown-family comparison finds 0 of the 36; comparing with `sans-serif` finds all 36 and none of the 387 characters the font has. Unpatched, the 90 literal characters were shape matched (1,819 glyphs decoded instead of 1,729, and two came out wrong: 啥 as 晗 and 奈 as 亲). Chromium, where the unknown family and the lacking font fall back alike, is not affected.

`HardScripts.webKitAdjusted` therefore compares with the generic families too (the unknown family, `sans-serif`, `serif`, `system-ui`, `monospace`) before it calls a character the font's own. The change is applied to the text the web views run, only where the original line is found, so the resource file stays Android's bytes and a shared file that carries the fix itself is used as it is. In Chromium the first comparison already decides, so the patch changes no answer there; **it should be made in the shared file** (one statement in `coreCovers`) so both apps run one text. `HardWebKitDiagnostics` fixes the rule against the 36 characters.

With the patch the captured chapter decodes exactly in WebKit (all 94 paragraphs, 1,729 shape-matched glyphs, 69 below the confidence thresholds, 90 passed through, nothing missing) with the verified table off; with it on, 1,729 glyphs are verified and the reference is never built, as on Chromium. The screenshot excerpt decodes exactly. A woff2 face loaded from an `ArrayBuffer` draws on a canvas in WebKit like a TrueType one.

**2. Desktop identity.** `WebPageDriving.emulateDesktop(userAgent:width:height:)` is implemented by `WKWebPage`: `customUserAgent` is the Chrome string of the contract, the web view's frame is set to the requested size (the layout viewport follows it), and on iOS the page asks for the desktop content mode on every navigation (`webView(_:decidePolicyFor:preferences:)`), because a phone is otherwise given the phone layout. The layout viewport is not exactly what is asked for: macOS reports 1280 x 800, and an iPhone simulator in the desktop content mode reported 2560 x 1476 CSS px for the 1280 x 800 frame (`qdn.pageState().viewport`; the adapter logs "Reading as a desktop browser (W x H px)"), the same kind of doubling Android shows on its first page. Nothing in the script depends on it (the live qdn check below ran at 2560 x 1476). Pages stay attached to the hidden window the engine already uses on iOS, so timers and animation frames run.

**3. The script as a document-start user script, main frame only.** `WebPageDriving.addMainFrameInitScript` adds a `WKUserScript` (`atDocumentStart`, `forMainFrameOnly: true`); the existing `addInitScript` is for every frame and is not used for these sources. User scripts are not subject to a page's content security policy, which is why the contract's document-start route is the main one on iOS: `(0, eval)(source)` into a page can be refused by a strict policy. The blank decoder page has no policy and gets the script by evaluation (`ensureHard`).

**4. Calls.** `evaluateJSON` runs an async function body through `callAsyncJavaScript` in the page's content world. The call strings are exactly Android's (`return await window.__npiaHard.<call>;`, `tasks.start(<path literal>, <args>)`, `tasks.poll(<ticket literal>)`), so a scripted test page can answer both platforms alike. Big arguments (the extraction payload, base64 resource chunks) are passed as JavaScript string literals inside the body, like on Android; `HardScripts.literal` escapes U+2028 and U+2029.

**5. Cookies.** `WebEngineProviding.sessionCookies` gives the adapters their cookie access; the default reads `HTTPCookieStorage.shared`, and `WKWebEngine` answers with `WebKitSessionCookies`, which reads, writes and deletes in the Browser tab's store, the scrapers' (`external`) store, every other profile opened so far, and the shared jar. This is needed because `CookieBridge` mirrors stores into the shared jar only as they change and only by adding, and an iOS app can start with a login in a store nobody has opened yet. Status, restore and sign-out all go through it, so a sign-out leaves no copy behind that the bridge could carry back.

**6. Sign-in.** `presentLogin` shows `WebLoginSession` (profile `browser`, the Browser tab's store) as a sheet with Cancel and Done; AutoFill works. The pipeline's `loginRequired` handling (`DownloadPipeline.signIn`) already opens it on the book's address, and the adapters raise `loginRequired` once per book exactly as the contract says; the "once" state is process wide (`HardPrompts`) because `SiteEngineRouter` builds a new adapter for the second try. Enter App opens the site's own sign-in address (`SessionSite.signInURL`).

### Sessions on iOS

`SiteSessions` is the contract's, with async calls. The saved copy is `{saved, cookies}` under the Keychain key `<source>.sessionCookies`. WKWebView drops session cookies on relaunch more readily than Android's WebView, so the copy is refreshed more often than the contract requires:

- when a sign-in sheet closes (Enter App, or the pipeline's prompt: the next `parseBook` snapshots it);
- at the end of `parseBook` when the session is usable, and after every batch;
- whenever the app becomes inactive or goes to the background (`RootView`, `scenePhase`).

`restoreOnce` runs at app start (`RootView` `.task`) and at the start of every `parseBook`. The Browser tab's per-site and "all sites" sign-out call `AppModel.forgetSavedSessions`, which calls `forgetSavedCopy`. Restored cookies are session cookies again (no expiry), set in the shared jar and in the Browser tab's store; scraper pages are seeded from the jar when they load.

### Links

`SourceAdapter.canonicalURL` (static; default: the link itself) maps a qdn book or chapter link on `m.<domain>`, `www.<domain>` or the bare domain to `https://www.<domain>/book/<id>/`. It is applied in `SourceInfo.parseURL` (Add screen text, paste, share), `LinkCart`, `AppModel.enqueue` and the Browser tab's "Download this book" button, and `parseBook` reads the desktop address whatever it is given. `SourceCatalog` lists both adapters; `QdnAdapter.matches` and `RbooksAdapter.matches` are Android's matchers.

### Tests

- XCTest over a scripted page (`HardTestSupport.swift` answers the entry points with canned JSON, the way `HardTestSupport.kt` does): `QdnAdapterTests`, `RbooksAdapterTests` (matchers, normalisation, book and chapter flows, refusals, pacing, firewall waits, sign-in prompts, the library, images with the book as referer), `SiteSessionsTests` (status, copy, restore, sign-out, status text, cookie domains), `HardResourcesTests` (hashes, the load-time adjustment, string literals).
- `HardWebKitTests` runs the script in a real `WKWebView` (`swift test` on macOS): the entry points, the font hook, tasks, SHA-256 and base64 against CryptoKit, the saved qdn and rbooks pages of `tests/fixtures/hard_sources` (loaded with their site address as base URL), the glyph decoder on `tests/fixtures/qdn_chapter76_capture.json` and `qdn_screenshot_excerpt.json`, and a synthetic reader with every protection layer (`tests/fixtures/hard_sources/qdn_synthetic_reader.html`, built by `build_reader()` in `tests/hard_sources_support.py`: stylesheet and script-built cipher fonts, mirrored glyphs, characters in random tags ordered by CSS `order`, `::after` attribute text, hidden decoys, poisoned off-screen paragraphs that only swap to the real text near the viewport). The extraction runs in a visible `NSWindow`, because a web view that is not in a window gets no rendering updates and the reader's intersection observers never fire (the iOS app keeps its pages in a hidden window for the same reason).
- Timings on a GitHub macOS runner: building the glyph reference 2.8 s (9,853 characters); decoding the captured chapter by shape 21 s; extracting the synthetic reader 6.6 s and decoding it 9 s; decoding with the verified table 0.3 s.

### Live checks (free content only, no account)

The iOS App workflow's `live_url` job runs the app in an iPhone simulator on a GitHub runner (`AppModel.runLiveDownloadIfAsked`: the first three chapters as EPUB and TXT):

- **qdn**, a completed free-to-start book of 246 chapters (`https://www.<domain>/book/1004575373/`): read as a desktop browser at 2560 x 1476 px, 246 chapters listed, the first three chapters (about 18 KB of text) written as EPUB and TXT, nothing skipped.
- **rbooks**, a series of 439 episodes (`https://<domain>/books/5576001974`): product page, then the book API as a top-level page walked in slices (95, 196, 296, 395 of 439 read, then the chain finished with 439 episodes), the first three episodes (about 36 KB of text) written as EPUB and TXT, nothing skipped.

Bought content and the sign-in sheet were not exercised.

### Limits and what differs

- **Only what the fixtures and free pages cover was exercised.** No account was used. Bought-chapter decryption in a WKWebView, the live cipher fonts and the live ownership answers are not exercised, as on Android.
- **Whether the site's reader decrypts in WKWebView with a spoofed Chrome user agent is unknown**; its checks for embedded browsers could refuse it.
- **Background.** iOS suspends the app, and with it the web views' timers, when it is not in the foreground. A bought chapter's decryption and the paragraph scrolling need the app open; the "keep screen awake while downloading" setting helps.
- **Per-load referer.** The pages do not set one (the sites read both); image downloads from the adapters send the book address as `Referer`.
- **Not possible on a phone**, as on Android: rbooks' PC-viewer export of titles the web viewer refuses.
- **Viewport on a phone** is not verified (see above). The log line "Reading as a desktop browser" reports it at the start of every qdn book.
