# Npia Mobile (Android)

Native Kotlin and Jetpack Compose version of the merged npia and external downloader. It mirrors the iOS app in `ios/`: same module split, same `SourceAdapter` and uploader contracts, and the same settings JSON. A settings file exported on one app imports on the other, and both import the desktop `config.json`.

## Modules

| Module | Kind | Contents | iOS counterpart |
|---|---|---|---|
| `:core` | JVM | Models, `AppSettings`, `DesktopConfig`, `SecretStore`, and the `SourceAdapter`, `HttpFetching`, `WebPageDriving` and `ImageProcessing` interfaces | `NpiaCore` |
| `:net` | JVM | OkHttp `HttpFetching` with pacing and retries | `NpiaNet` |
| `:output` | JVM | EPUB, TXT and HTML writers. PDF is registered by the app | `NpiaOutput` |
| `:upload` | JVM | Share-link hosts | `NpiaUpload` |
| `:sources` | JVM | One adapter per source codename, plus `SourceCatalog` | `NpiaSources` |
| `:pipeline` | JVM | Runs one download end to end | `NpiaPipeline` |
| `:webengine` | Android | Offscreen WebView pages | `NpiaWebEngine` |
| `:images` | Android | Bitmap/ImageDecoder recompression | `NpiaImages` |
| `:app` | Android | Compose UI and encrypted secret store | `App/` |

The JVM modules hold most of the logic, so their unit tests run without a device.

## Building

CI (`.github/workflows/android.yml`) runs on pushes to `android/**` branches. It runs `gradle test`, builds `NpiaMobile-debug.apk`, and uploads the APK as an artifact. The debug APK is signed with the debug key, so it installs directly with `adb install` or from the phone.

Locally, you need JDK 17 and Gradle 8.10 (no wrapper is committed):

```bash
cd android && gradle test :app:assembleDebug
```

## Site engines

A link is read by the site engines in the order of Settings > Site engines (default: the built-in scrapers, the novel-downloader rules, then the WebToEpub parsers), until one returns chapters. The settings and `config.json` keys match the desktop app (`source_engine_order`, `source_engines_off`, `novel_downloader_auto_update`, `webtoepub_auto_update`).

- The novel-downloader rules (`rules-lib.js`) run in QuickJS, with a web view as the fallback. The WebToEpub parsers (`reader/webtoepub/`) need a real DOM and always run in a web view; the two web view paths share one loaded page. WebToEpub is GPL-3.0 (`reader/webtoepub/LICENSE.md`); its files are copied byte for byte from the desktop build, and `.gitattributes` keeps their line endings so the manifest checksum stays valid.
- Both rule engines can update themselves from the `engines` release of this repository. This is opt in and checked at most once a day. A download is used only when its checksum and API match and it is newer than the shipped copy; one that fails to load is deleted and not fetched again (`sources/.../engines/`).
- `WebViewTransportTest` and `WebToEpubLiveTest` (`app/src/androidTest`) run on an emulator or device: `gradle :app:connectedDebugAndroidTest`, and `-Pandroid.testInstrumentationRunnerArguments.live=true` for the live site check.

## Browser-driven sources (qdn, rbooks) and Enter App

qdn and rbooks need a real rendering engine and the user's own sign-in. Their page-side logic is one JavaScript file, `sources/src/main/resources/com/shirochi/npia/sources/hard/hard-sources.js`, that the iOS app runs unchanged; the Kotlin side only navigates, keeps the session, paces loads and builds results. The contract (entry points, JSON shapes, resources, session handling, what differs from the desktop) is [`docs/hard-sources.md`](docs/hard-sources.md).

- **Enter App** (Settings > Enter App, and a button on the Add screen) signs in to sfc (username and password through the app API, or a pasted app cookie) and, in the app's own browser, to qdn and rbooks. Downloads use the browser's cookies, and a saved copy of the login cookies is restored if the cookie store loses them.
- qdn bought chapters are decrypted by the site's own script, read off the screen and matched against a bundled reference font (`hard/qdn/`). Chapters the account has not bought are refused, never bypassed.
- The JavaScript is tested in Playwright Chromium from the repository root: `python -m pytest tests/test_hard_sources_js.py` (needs `playwright`, `fonttools`, `brotli`; the fixtures are in `tests/fixtures/`). The Kotlin side has scripted-page tests in `:sources`.

## Porting from iOS

Each placeholder carries a `TODO(<workstream>)`. The finished Swift code on the `ios/integration` branch is the closest reference: port its logic and test fixtures, and use the desktop Python as the source of truth when the two differ.

## Rules

- Follow the codename policy in `AGENTS.md`. Kotlin string literals that must hold a provider name or host use `\uXXXX` escapes.
- Adapters return `ChapterResult.Refused` for chapters the account cannot read. Never bypass payment.
- Credentials go through `SecretStore`, never into `AppSettings`.
- Keep the settings JSON keys identical to iOS.
