# Npia Mobile (iOS)

Native SwiftUI rewrite of the desktop npia downloader and external downloader in one app.

## Layout

- `NpiaKit/`: Swift package holding all logic. It builds for iOS 17 and macOS 14, so `swift test` runs on a Mac without a simulator.
  - `NpiaCore`: models, `AppSettings`, desktop `config.json` import/export, Keychain secrets, and the `SourceAdapter`, `HTTPFetching` and `WebPageDriving` protocols.
  - `NpiaNet`: URLSession client with pacing (`intervalMin`/`intervalMax`) and retries.
  - `NpiaWebEngine`: offscreen WKWebView pages, the replacement for Playwright/CDP.
  - `NpiaImages`: image recompression.
  - `NpiaOutput`: EPUB, TXT, PDF and HTML writers.
  - `NpiaUpload`: share-link hosts (temp.sh, gofile, Google Drive, ...).
  - `NpiaSources`: one adapter per source codename, registered in `SourceCatalog`.
  - `NpiaPipeline`: runs one download end to end.
  - `NpiaUpdate`: the app's own updates (build stamp, release manifest, check, verified download).
- `App/`: the SwiftUI app target.
- `project.yml`: XcodeGen spec. The `.xcodeproj` is generated, not committed.

## Building

On a Mac:

```bash
cd ios/NpiaKit && swift test
cd ios && xcodegen generate && open NpiaMobile.xcodeproj
```

Without a Mac, push an `ios/*` branch. The `iOS App` workflow runs the tests and uploads an unsigned IPA artifact. Run it by hand with `release_tag` to attach the IPA to a release. Sideload the unsigned IPA with AltStore, Sideloadly, or your own signing.

## Workstreams

Each placeholder carries a `TODO(<workstream>)` naming its owner:

| Workstream | Owns |
|---|---|
| A. npia core | `NpiaAdapter`, `EPUBWriter`, `HTMLWriter`, npia login |
| B. upload | `NpiaUpload` |
| C. http sources | `Sfc`, `Nweb`, `Jara`, `Yeduji`, `Maru` adapters |
| D. generic reader | `ReaderAdapter` (rules-lib.js in JavaScriptCore) |
| E. webengine | `NpiaWebEngine`, `GlobalNpia`, `Kpage`, `Floo`, `Fenrir`, `Xiyuwx`, `Kobo` adapters |
| F. images and UI | `NpiaImages`, `PDFWriter`, `App/` screens, settings import/export UI |
| Phase 5 | `Npia` browser path, `Mpia`, `Ntk`, `Rbooks`, `Qdn` (after device spikes) |

## Site engines

A link goes through the site engines in the order set in Settings > Site engines (default: the built-in scrapers, the novel-downloader rules, the WebToEpub parsers) until one finds chapters. `SiteEngineRouter` runs them; when a built-in scraper owns the site, the other two only follow it if they list the site (`EngineSites`). A web page no built-in scraper owns is accepted even when no site list names it, because WebToEpub also recognises some sites by their page; "Not supported" means no enabled engine can try the link (`SiteEngineRouter.canTry`).

- novel-downloader (`rules-lib.js`) runs in JavaScriptCore, then in a web view if that fails. WebToEpub (`Reader/Resources/webtoepub/`, GPL-3.0) needs a real DOM and only runs in a web view. Both share one page load (`EnginePage`). Requests the page's own `fetch` refuses go through the `npiaTransport` message handler to the app's HTTP client (`EngineTransport`).
- Both engines ship with the app. With the opt-in toggles on, `EngineUpdater` downloads newer builds from the `engines` release (`novel-downloader-manifest.json`, `webtoepub-manifest.json`) into Application Support and checks the SHA-256, the API (1) and that the build is newer. A download that fails to load is deleted, remembered as rejected, and the shipped copy is used.
- A live check downloads the first three chapters of a real book inside the app on a simulator: run the iOS App workflow by hand with `live_url` set (for example a WebToEpub-only site). It needs the site to be reachable from GitHub's runners.
- The config keys `source_engine_order`, `source_engines_off`, `novel_downloader_auto_update` and `webtoepub_auto_update` match the desktop app.

## Catalog and Browser tabs

The Catalog tab shows a website in a web view, and `CatalogBridge` (`NpiaWebEngine/Resources/catalog-bridge.js`) runs on every page of it. The app decides what the page on screen is each time its address changes, single-page sites included (`CatalogPages.mode`, `CatalogPageTracker`):

- NovelDB and NovelDB Next: links are never renamed; outside links open in the Browser tab.
- A supported site (a built-in scraper's site, or one a switched-on rule engine lists): outside links open in the Browser tab, and a downloadable page gets a floating "🔗 Download" button that adds it to the link cart ("✓ In cart", tap again to remove).
- Any other site: the "Send outside links to the link cart" setting decides whether outside links are renamed and sent to the cart.

A page is downloadable (`SourceCatalog.downloadablePage`) when a built-in scraper's `matches` takes it while the built-in scrapers are on, when the novel-downloader rules list it (the `@match`, `@include` and `@exclude` patterns in the header of the rules bundle in use, read by `UserScriptPatterns` with Greasemonkey semantics), or when WebToEpub lists its host and it is not the home page. `SourceAdapter.sites` lists each built-in scraper's hosts; `SourceCatalog.supportedSite` says whether a site is one of ours even with the built-in scrapers off.

- The bridge sends `{type: 'location', url, title}` and the app answers `setMode({cartLinks, download: {url, title} | null})`; `download.url` must be the reported address. Reports count only from the host the view shows.
- `catalog-bridge.js` is byte-identical to the Android and desktop copies (SHA-256 `f58147ea...` in `CatalogBridgeTests`, LF line endings, marked `-text` in `.gitattributes`). Change all three together.
- The Browser tab shows "Download this book" and "🔗 Add to link cart" side by side on a downloadable page. The 文A menu translates the page in place (`BrowserTranslate`, `browser-translate.js`): Google's website translator is injected and its language box is set, which keeps sign-ins working. A site that blocks the translator can be opened as a translated copy through Google's proxy (sign-ins do not apply there); the buttons then use the original address. Settings > Browser picks the language.

## Queue log and notifications

The Queue tab has a Jobs / Log switch. Log shows every job's lines in time order (time, level, short job title), with a menu to limit it to one job, a Details switch for debug lines, Copy, Share and Clear. It follows new lines until you scroll up ("Latest" jumps back). The last 5,000 lines are kept in `Application Support/queue-log.jsonl` (`LogBuffer`, NpiaCore) and survive a relaunch. Each job also keeps up to 1,500 lines on its page.

Where the lines come from: the pipeline (engine attempts and fallbacks, source and settings, chapter counts, skipped or refused chapters, images, the files written, uploads and timing), `URLSessionHTTPClient` (every retry with its reason, requests that kept failing, connection errors; a plain 404 is a debug line), and the app (queued, started, cancelled, failed, finished, uploads).

The app posts a local notification when downloads continue in the background and when a run ends while the app is not in front. Tapping it opens Queue > Log for all jobs, or that book's page when the run was a single book (`QueueTarget`, carried in the notification's `userInfo`; the Queue's navigation path lives in `AppModel`). The system progress UI of iOS 26 background tasks belongs to iOS: a tap on it only opens the app.

## App updates

NovelDB checks its own GitHub releases for a newer build, the same way the desktop and Android apps do (`NpiaUpdate`, shown in Settings > App updates).

- Build number: CI stamps `NOVELDB_BUILD` (Unix seconds, planned by Mobile Release), `NOVELDB_TAG` (`v98`) and `NOVELDB_LABEL` (`none`, `hotfix3`) into the Info.plist as `NovelDBBuild`, `NovelDBTag` and `NovelDBLabel`. `ci/release_stamp.sh` turns the workflow inputs `build_id`, `release_tag` and `release_label` into those build settings and also sets `CURRENT_PROJECT_VERSION` (build / 60) and `MARKETING_VERSION` (the tag's number); `ci/check_stamp.sh` checks the built app. A build without a number (every local, push and pull request build) is a development build: no update is ever offered.
- Check: `update-ios.json` of the latest release lists the IPA (`kind` `ipa`, `variant` and `arch` `universal`) with its `build`, `size` and `sha256`. An update exists when that `build` is greater than the app's. A 404, a manifest with another `schema` than 1, or a manifest without the entry means no update. It runs at start and when the app returns to the foreground, at most once per 24 hours (the time is kept in `UserDefaults`), when "Check for app updates automatically" is on (`app_update_check` in the desktop config; on by default). "Check for app updates now" always fetches.
- Update: the alert-style sheet offers Update, Release notes and Later. Update streams the IPA into a partial file while hashing it, with progress and Cancel, and keeps it only when size and SHA-256 match; otherwise it is deleted ("The download did not match its checksum; nothing was installed."). iOS apps cannot install themselves, so the checked file goes to the share sheet (SideStore, AltStore and similar tools accept it there) or to Files.
- Testing: a Debug build takes `-NovelDBUpdateBase <releases address>` (default `https://github.com/Shirochi-stack/NovelDB/releases`) to read `<base>/latest/download/update-ios.json` and `<base>/download/<tag>/<file>` from a local server, and `-NovelDBUpdateReport` to download at once and write `update-check-report.txt` to Documents. The `update-check` job of the iOS App workflow builds a stamped app and runs it against `ci/update_fixture.py`, which imitates GitHub's redirects.

## Rules

- Follow the repository codename policy (`AGENTS.md`). Swift string literals that must hold a provider name or host use `\u{..}` escapes.
- Adapters return `ChapterResult.refused` for chapters the account cannot read. Never bypass payment.
- Credentials go through `SecretStore`, never into `AppSettings`.
- New settings fields need defaults. `SettingsCoding` fills missing keys, so no migration is needed.
