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
- Android Google Drive connections use `GoogleDriveAccount` with credentials supplied by the user.
  Upload settings contain the OAuth client ID, optional client secret, and refresh token. The
  Sign in to Google button validates the grant before saving it in `EncryptedSecretStore`.
  `UploadContext.googleAccessToken` supplies the account's cached or refreshed access token.
- `GoogleDriveAuth` remains available for other clients with an existing OAuth grant. Public OAuth
  clients omit the client secret. Clients that require a secret include the user's supplied value.

## User-supplied Google Drive credentials

Each user authorizes their own OAuth client and enters its credentials in Settings > Upload >
Google Drive. The Android connection refreshes that existing grant through Google's token endpoint.

1. In your Google Cloud project, enable the Drive API and configure the OAuth consent screen.
   For the OAuth Playground procedure below, create a Web application OAuth client and add
   `https://developers.google.com/oauthplayground` as an authorized redirect URI.
2. Open https://developers.google.com/oauthplayground/. In its settings, select Use your own
   OAuth credentials and enter your client ID and client secret. Select offline access.
3. Authorize `https://www.googleapis.com/auth/drive.file` using the Google account whose Drive
   you want to upload to. Exchange the authorization code for tokens.
4. In NovelDB's Google Drive settings, enter that same client ID, its client secret if required,
   and the refresh token returned by Google. Tap Sign in to Google.
5. A successful token refresh saves the complete connection as one encrypted record. Client
   secrets and refresh tokens are masked in the form and excluded from settings exports.
   Disconnect clears the stored connection and cached tokens.

The optional Folder ID chooses the parent folder. The app uploads files it creates with the
`drive.file` scope and shares finished uploads with anyone who has the resulting link.
A Google project in Testing must allow the chosen account as a test user. Google controls token
expiry and revocation. An invalid or revoked refresh token requires authorizing that client again.

Failed validation keeps the previous working connection. A disconnect during validation or token
refresh prevents the pending response from restoring credentials. Connection errors stay visible
in Upload settings for the current app session.

Google's OAuth Playground documents the user-provided client option at
https://developers.google.com/oauthplayground/. The refresh-token flow is described at
https://developers.google.com/identity/protocols/oauth2/web-server#offline.
