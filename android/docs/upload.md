# Upload (`:upload`)

Share-link hosts for finished outputs, ported from the iOS `NpiaUpload` module.
`UploaderCatalog.upload(file, settings, context)` tries each id in `UploadSettings.hosts` in
order, zips folder outputs (separate TXT) first, and records the result in `history`
(`UploadHistoryStore.shared` by default, which the app sets at startup).

| id | Host | Limit | Kept | Account |
|---|---|---|---|---|
| `tempsh` | temp.sh | 4 GB | 3 days | no |
| `gofile` | Gofile | none published | ~10 days without downloads | optional token (`UploadSecrets.GOFILE_TOKEN`) |
| `catbox` | Catbox | 200 MB | permanent | optional user hash (`UploadSecrets.CATBOX_USER_HASH`) |
| `pixeldrain` | Pixeldrain | plan dependent | 90 days after last view | API key (`UploadSecrets.PIXELDRAIN_API_KEY`) |
| `tmpfiles` | tmpfiles.org | 100 MB | 48 hours (`expire` set to the maximum) | no |
| `krakenfiles` | KrakenFiles | 1 GB | 30 days without downloads | no |
| `sendnow` | Send.now (was send.cm; `sendcm` still resolves) | 100 GB | 15 days without downloads | no |
| `gdrive` | Google Drive | 5 TB / quota | until deleted | Google sign-in |

Multipart bodies are streamed from disk in 64 KiB pieces through an OkHttp `RequestBody` that
reports `UploadProgress`; Drive sends 8 MiB chunks. Memory stays flat for multi-GB files.

## ZIP bundles and file names

With `UploadSettings.zipBundle` on (the default, desktop key `upload_zip_bundle`) an upload of two or
more files goes through `UploaderCatalog.plan` and `uploadAll`: `ZipBundling.plan` packs the files by
first-fit decreasing into ZIPs of at most `min(zipMaxMb, first usable host's maxBytes)`
(`upload_zip_max_mb`, 50 to 4096, default 1024), counting 1 KB of overhead per entry. A file bigger
than that goes alone, and so does a file left alone in its ZIP. Folders are never bundled.

- Entries keep the original (NFC) file name with the UTF-8 name flag set, no folders; EPUB, CBZ, PDF
  and ZIP entries are stored, everything else is deflated. A repeated name becomes `name (2).ext`.
- ZIP names are ASCII: `NovelDB_<n>_EPUBs.zip` when every entry is an EPUB, else
  `NovelDB_<n>_files.zip`, with `_2`, `_3` added to repeats within one upload.
- ZIPs are built in `UploadContext.temporaryDirectory` and deleted afterwards. `UploadListener`
  reports "preparing ZIP i of n", the host and the byte progress. `UploadResult.contents` lists the
  original names inside a ZIP, and `UploadResult.label` reads "NovelDB_3_EPUBs.zip (3 books)".

Single files go up under an ASCII name (`UploadNames.ascii`): the name itself when it is plain
printable ASCII, else the ASCII-safe title when it keeps at least 3 ASCII letters (digits alone do not
count, so a title of digits and Korean is not `100.epub`), else `<stem>.<ext>` with the stem
`<source>_<book id>` (`UploadStem`, for example `npia_123456`). Only the ASCII name is sent in the
multipart `filename=`: an RFC 5987 `filename*` with the real title overrides it on hosts that read
it, and they reduce Korean to `-_.epub`.

Debug builds of the app can point uploads at a local test server (`LocalTestUploader`, set with the
`noveldb_upload_base` and `noveldb_upload_max_bytes` intent extras) to check all of this without a
public host.

## API for the UI

- `UploaderCatalog.all`: `id`, `displayName`, `maxBytes`, `retentionSeconds`, `requiresAccount`.
  `isReady(context)` is false for an account host without credentials, so the UI can show
  "Sign in" or "Add API key".
- `UploadContext(secrets = <app SecretStore>, settings = ...)`. Tests pass a client that talks to
  MockWebServer.
- History: `UploadHistoryStore(File(filesDir, "upload-history.json"))`, assigned to
  `UploadHistoryStore.shared`. `entries` is a `StateFlow` (newest first); `active()`, `remove()`,
  `pruneExpired()`, `clear()`. `UploadResult.isExpired()`.
- Google sign-in: `GoogleDriveAuth(GoogleOAuthConfig.configured()!!, secrets).signIn(browser)`,
  `.signOut()`, `.isSignedIn`. `configured()` returns null until a client ID is filled in, so hide
  or disable Drive when it is null.
- `OAuthBrowser` is the one piece `:app` implements: open the URL in a Custom Tab, catch the
  redirect to `callbackScheme` (an activity with an intent filter for that scheme), and return the
  full redirect URI. Throw `CancellationException` when the user backs out.

## Google Drive setup

OAuth 2.0 with PKCE, no Google SDK and no client secret. Scope is `drive.file`: the app only
sees files it created. Only the refresh token is stored (`SecretKey.GOOGLE_DRIVE_REFRESH_TOKEN`).

1. In Google Cloud Console create (or pick) a project and enable the **Google Drive API**.
2. Configure the **OAuth consent screen** (External; add yourself as a test user while the app
   is in testing; `drive.file` is non-sensitive, so no verification is needed).
3. **Credentials → Create credentials → OAuth client ID → Android.** Package name
   `com.shirochi.npiamobile` (or the `applicationId` you build with) and the SHA-1 of the signing key
   (`keytool -list -v -keystore ~/.android/debug.keystore` for debug builds). Under the client's
   **Advanced settings**, enable **Custom URI scheme**.
4. Paste the client ID (`1234-abc.apps.googleusercontent.com`) into
   `GoogleOAuthConfig.BUNDLED_CLIENT_ID` in
   `android/upload/src/main/kotlin/com/shirochi/npia/upload/GoogleDriveAuth.kt`, or set
   `GoogleOAuthConfig.clientIdOverride` at app startup (for example from a `BuildConfig` field).
5. The redirect URI is the reversed id, `com.googleusercontent.apps.1234-abc:/oauth2redirect`.
   The app manifest needs an intent filter for the scheme `com.googleusercontent.apps.1234-abc`
   on the activity that completes `OAuthBrowser.authenticate`.

Set `UploadSettings.googleDriveFolderId` to upload into a folder; each upload is shared as
"anyone with the link" (reader).
