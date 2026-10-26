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
