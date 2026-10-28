# Npia Mobile (Android)

Native Kotlin and Jetpack Compose version of the merged npia and external downloader. It mirrors the iOS app in `ios/`: same module split, same `SourceAdapter` and uploader contracts, and the same settings JSON. A settings file exported on one app imports on the other, and both import the desktop `config.json`.

## Modules

| Module | Kind | Contents | iOS counterpart |
|---|---|---|---|
| `:core` | JVM | Models, `AppSettings`, `DesktopConfig`, `SecretStore`, and the `SourceAdapter`, `HttpFetching`, `WebPageDriving` and `ImageProcessing` interfaces | `NpiaCore` |
| `:net` | JVM | OkHttp `HttpFetching` with pacing and retries, and the app update client | `NpiaNet` |
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

## App updates

The app checks the releases of this repository for a newer build of itself and installs it after the user taps Update. Desktop, Android and iOS follow one contract; the Android part is `AppUpdater` (`:app`), `AppUpdateClient` (`:net`) and `AppUpdate.kt` (`:core`, the manifest and comparison rules).

- **Build number.** CI passes `-PnoveldbBuild=<Unix seconds> -PnoveldbTag=v98 -PnoveldbLabel=none` (Mobile Release calls `android.yml` with `build_id`, `release_tag` and `release_label`). They become `BuildConfig.NOVELDB_BUILD`, `NOVELDB_TAG` and `NOVELDB_LABEL`, `versionCode = build / 60` and `versionName` `98` or `98-hotfix3`. Without a build number (local, debug and pull request builds, the old `GITHUB_RUN_NUMBER` codes) the app is a development build: it says "Development build: app updates are off" and never offers an update.
- **Manifest.** `releases/latest/download/update-android.json`, schema 1, one `apk` entry with `variant` and `arch` `universal`, its `build`, `size` and `sha256`. An update exists when the entry's `build` is larger than the app's. A 404, a bad manifest, another schema or a missing entry means no update. Unknown fields are ignored.
- **Download.** Only after the user taps Update, to `cache/app-update`, with progress and Cancel. The size and SHA-256 must match the manifest; otherwise the file is deleted and the app says "The download did not match its checksum; nothing was installed." The verified file goes to `PackageInstaller`, which asks the user to confirm. Android refuses an APK signed with another key, and the app says to uninstall the old NovelDB first. Installing needs "Allow from this source" for NovelDB (`REQUEST_INSTALL_PACKAGES`); the app explains it and opens that settings page.
- **Setting.** Settings > App updates > "Check for app updates automatically", on by default, `app_update_check` in the desktop `config.json` and `appUpdateCheck` in the app settings file. The check runs when the app starts or returns to the foreground, at most once per 24 hours (the last check time is in app preferences, not in the settings). "Check for app updates now" always checks.
- **Testing against a local server (debug builds only).** Settings > App updates > "Update server", or `adb shell am start -n com.shirochi.npiamobile/com.shirochi.npia.app.MainActivity --es noveldb_update_base http://10.0.2.2:8000` (an empty value goes back to GitHub). The address replaces `https://github.com/Shirochi-stack/NovelDB/releases`, so serve `<address>/latest/download/update-android.json` and `<address>/download/<tag>/<name>`. Plain http is allowed for `10.0.2.2`, `localhost` and `127.0.0.1` only (`app/src/debug`); on a phone use `adb reverse tcp:8000 tcp:8000` and `http://localhost:8000`. Build the app with a small `-PnoveldbBuild` and the update with a larger one, both signed with the same key.

## Porting from iOS

Each placeholder carries a `TODO(<workstream>)`. The finished Swift code on the `ios/integration` branch is the closest reference: port its logic and test fixtures, and use the desktop Python as the source of truth when the two differ.

## Rules

- Follow the codename policy in `AGENTS.md`. Kotlin string literals that must hold a provider name or host use `\uXXXX` escapes.
- Adapters return `ChapterResult.Refused` for chapters the account cannot read. Never bypass payment.
- Credentials go through `SecretStore`, never into `AppSettings`.
- Keep the settings JSON keys identical to iOS.
