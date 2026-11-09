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
- Android Google sign-in uses the Google Play services Authorization API through
  `GoogleDriveSignInActivity`. Upload settings show Sign in to Google and the local connection
  state. `GoogleDriveAccount` supplies access tokens through `UploadContext.googleAccessToken`.
  Tokens stay in memory and Play services owns the grant.
- `GoogleDriveAuth` remains available for clients with an existing OAuth refresh-token grant.
  Android uses native authorization instead of the legacy browser callback flow.

## Google Drive setup for the app publisher

Users tap Sign in to Google, choose their Google account, and grant Drive access on Google's
permission screen. Play services supplies and refreshes the access tokens. The app requests
`drive.file` and shares completed uploads with anyone who has the resulting link.

The following Cloud Console configuration is performed once by the app publisher.

NovelDB uses the publisher project `noveldb-514213`. Its Android release client is registered
for the package and certificate below. The native authorization API resolves this client from
the installed APK identity.

1. Enable the Google Drive API in the publisher's Google Cloud project and configure its
   consent screen. Add test accounts while the project is in testing.
2. Register an Android OAuth client for `com.shirochi.npiamobile` and the SHA-1 certificate
   fingerprint of the installed build. Register debug and release certificates separately.
3. Open Settings > Upload, add Google Drive, and tap Sign in to Google. Google Play services
   shows the account and permission UI. The native API identifies the client from its package
   and certificate. The APK does not need a client secret or browser callback scheme.
4. The optional Folder ID chooses the parent folder. Disconnect clears the app's local
   connection and cached access token.

The Android authorization flow is documented at
https://developer.android.com/identity/authorization.

### Authorization failures

The app reads Google's status from the returned intent even when the activity result is cancelled.
The Upload settings retain the error code and description until another sign-in attempt starts.
Error 10 and `UNREGISTERED_ON_API_CONSOLE` identify publisher configuration failures.
The latter can arrive with Google error 8. Check the Android OAuth package and signing certificate
in the publisher's Google Cloud project. Error 16 can carry a more specific authorization failure,
so retain its description when reporting the problem.

For the ND114 release APK, the package is `com.shirochi.npiamobile` and the signing certificate SHA-1
is `6D:B7:60:78:05:BC:C2:F5:44:50:B3:92:5C:0D:59:F6:90:2B:5C:82`. A locally built debug APK has its
own signing certificate and needs a separately registered Android OAuth client.
