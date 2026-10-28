# NpiaUpload

Share-link hosts for finished outputs. `UploaderCatalog.upload(_:settings:)` tries each id in
`UploadSettings.hosts` in order, zips folder outputs (separate TXT) first, and records the
result in `UploadHistoryStore.shared` (`Application Support/upload-history.json`).

| id | Host | Limit | Kept | Account |
|---|---|---|---|---|
| `tempsh` | temp.sh | 4 GB | 3 days | no |
| `gofile` | Gofile | none published | ~10 days without downloads | optional token (`.gofileToken`) |
| `catbox` | Catbox | 200 MB | permanent | optional user hash (`.catboxUserHash`) |
| `pixeldrain` | Pixeldrain | plan dependent | 90 days after last view | API key (`.pixeldrainAPIKey`) |
| `tmpfiles` | tmpfiles.org | 100 MB | 48 hours (`expire` set to the maximum) | no |
| `krakenfiles` | KrakenFiles | 1 GB | 30 days without downloads | no |
| `sendnow` | Send.now (was send.cm; `sendcm` still resolves) | 100 GB | 15 days without downloads | no |
| `gdrive` | Google Drive | 5 TB / quota | until deleted | Google sign-in |

Not ported from the desktop `bot.py`: 0x0.st, file.io, transfer.sh (closed or gone) and the
Oracle bucket (needs a private pre-authenticated URL).

Multipart bodies are written to a temp file in 1 MiB pieces and streamed with
`URLSession.upload(fromFile:)`; Drive sends 8 MiB chunks. Memory stays flat for multi-GB files.

## File names

Some hosts drop non-ASCII characters from the multipart file name, so a Korean title arrived as
".epub" (the phone showed links ending in `/epub`, `/-_.epub` and `/VR_.epub`). A file goes up
under a useful ASCII name (`asciiUploadName`, `UploadNaming.asciiTitle`):

1. The title, when it is plain ASCII, is sent unchanged.
2. A title with other characters keeps its ASCII part (the other characters become spaces, empty
   brackets and loose punctuation at the ends are dropped, at most 100 characters) when that part
   holds at least three ASCII letters or digits: `Solo Leveling ...` stays `Solo Leveling.epub`.
3. Otherwise the stand-in `<source>_<book id>.<ext>` is used, like `npia_419368.epub`
   (`UploadNaming.nameID(source:bookID:url:)`: the adapter's book id, else the last number in the
   job URL, else a short slug, else `book`). The queue, the Upload action and the automatic upload
   after a download all pass it, with the job's source codename.

The multipart header carries that name in `filename` and the real one in the RFC 5987
`filename*` parameter, Pixeldrain's URL uses the ASCII name, and `UploadResult.fileName` keeps the
real name. Names inside a ZIP are always the original ones.

## ZIP bundles

`UploaderCatalog.uploadBatch(_:settings:events:)` uploads several files (`UploadItem`: file, book
title, stand-in name). With `UploadSettings.zipBundle` on (`upload_zip_bundle`, default on) and
two or more files they are packed into as few ZIPs as fit `zipMaxMB` (`upload_zip_max_mb`,
default 1024, 50 to 4096), or the first usable host's own per-file maximum when that is lower:

- First-fit decreasing by size. Every entry counts its size plus 1 KB for its headers.
- A file too big for a ZIP on its own is uploaded by itself; a ZIP that would hold one file is
  uploaded as that file; a folder (separate TXT) is zipped by the single-file path and never packed.
- One entry per file under its original name (UTF-8 name flag set, no folders). EPUB, CBZ, PDF and
  ZIP entries are stored, TXT and HTML are deflated.
- Names are ASCII: `NovelDB_<n>_EPUBs.zip` when every entry is an EPUB, else
  `NovelDB_<n>_files.zip`; a later ZIP of the same upload with the same name gets `_2`, `_3`, ...
- ZIPs are built one at a time in the temporary folder and deleted after the upload, success or
  failure. A ZIP no host takes is reported in `UploadBatchOutcome.failures` and the others go on.
- `UploadResult.contents` lists what a ZIP holds; `summary` reads `NovelDB_3_EPUBs.zip (3 books)`
  and the Uploads screen shows the titles inside.

The Queue has an Upload action on every finished job (swipe right, long-press, the job page) and a
Select mode (checkbox per finished job, Select all finished, a count, Share and Upload at the
bottom). The automatic upload after a download bundles the job's own files the same way.

## Progress

`UploaderCatalog.uploadBatch(..., events:)` adds `UploadBatchEvent`: `starting` (upload n of m),
`preparing` ("Preparing ZIP 1 of 3"), `host` (the values below), `uploaded` and `failed`.

`UploaderCatalog.upload(..., events:)` reports `UploadEvent` values: `trying` (the host about to
receive the file and its position among the configured hosts), `progress` (bytes sent to that
host), and `failed` (why a host did not take the file before the next one is tried). The queue
and the Uploads screen show them as a bar and a status line.

## Sharing agreement

The first time Upload is tapped the app asks the user to confirm they may share the file, and
remembers the answer (`shareAgreementAccepted`). Automatic uploads stay off until it is accepted.

## Google Drive setup

The app uses OAuth 2.0 with PKCE through `ASWebAuthenticationSession`, so there is no Google
SDK and no client secret. Scope is `drive.file`: the app only sees files it created.

1. In Google Cloud Console create (or pick) a project and enable the **Google Drive API**.
2. Configure the **OAuth consent screen** (External, add yourself as a test user while the
   app is in testing; `drive.file` is a non-sensitive scope, so no verification is needed).
3. **Credentials → Create credentials → OAuth client ID → iOS.** Bundle ID
   `com.shirochi.npiamobile` (or yours if you re-sign the app under another id).
4. Copy the client ID (`1234-abc.apps.googleusercontent.com`) into `ios/project.yml` under
   `NpiaGoogleDriveClientID` and regenerate the project. The redirect URI is the reversed id,
   `com.googleusercontent.apps.1234-abc:/oauth2redirect`; `ASWebAuthenticationSession`
   catches it, so no URL type needs registering.

In the app:

```swift
let auth = GoogleDriveAuth(config: GoogleOAuthConfig.fromBundle()!, secrets: KeychainSecretStore())
try await auth.signIn(using: WebAuthenticationBrowser { keyWindow })
```

The refresh token goes to `SecretStore` (`.googleDriveRefreshToken`). `signOut()` revokes it.
Set `UploadSettings.googleDriveFolderID` to upload into a folder the app created or the user
picked; each upload is shared as "anyone with the link" (reader).
