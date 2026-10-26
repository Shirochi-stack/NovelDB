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

## Porting from iOS

Each placeholder carries a `TODO(<workstream>)`. The finished Swift code on the `ios/integration` branch is the closest reference: port its logic and test fixtures, and use the desktop Python as the source of truth when the two differ.

## Rules

- Follow the codename policy in `AGENTS.md`. Kotlin string literals that must hold a provider name or host use `\uXXXX` escapes.
- Adapters return `ChapterResult.Refused` for chapters the account cannot read. Never bypass payment.
- Credentials go through `SecretStore`, never into `AppSettings`.
- Keep the settings JSON keys identical to iOS.
