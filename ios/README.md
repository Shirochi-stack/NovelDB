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

## Rules

- Follow the repository codename policy (`AGENTS.md`). Swift string literals that must hold a provider name or host use `\u{..}` escapes.
- Adapters return `ChapterResult.refused` for chapters the account cannot read. Never bypass payment.
- Credentials go through `SecretStore`, never into `AppSettings`.
- New settings fields need defaults. `SettingsCoding` fills missing keys, so no migration is needed.
