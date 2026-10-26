import XCTest
import NpiaCore
@testable import NpiaUpload

final class GoogleDriveTests: UploadTestCase {
    let config = GoogleOAuthConfig(clientID: "1234-abcd.apps.googleusercontent.com")!

    struct StubBrowser: OAuthBrowser {
        var respond: @Sendable (URL) -> URL
        func authenticate(url: URL, callbackScheme: String) async throws -> URL { respond(url) }
    }

    func testPlaceholderClientIDIsNotAConfig() {
        XCTAssertNil(GoogleOAuthConfig(clientID: nil))
        XCTAssertNil(GoogleOAuthConfig(clientID: ""))
        XCTAssertNil(GoogleOAuthConfig(clientID: "REPLACE_ME"))
        XCTAssertNil(GoogleOAuthConfig(clientID: "$(NPIA_GOOGLE_CLIENT_ID)"))
    }

    func testRedirectUsesReversedClientID() {
        XCTAssertEqual(config.callbackScheme, "com.googleusercontent.apps.1234-abcd")
        XCTAssertEqual(config.redirectURI, "com.googleusercontent.apps.1234-abcd:/oauth2redirect")
    }

    func testPKCEMatchesRFC7636Vector() {
        let pkce = PKCE(verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        XCTAssertEqual(pkce.challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuIUPHdTEUc")
        let generated = PKCE.generate().verifier
        XCTAssertGreaterThanOrEqual(generated.count, 43)
        XCTAssertNil(generated.rangeOfCharacter(from: CharacterSet(charactersIn: "+/=")))
    }

    func testAuthorizationURL() {
        let pkce = PKCE(verifier: "v")
        let url = GoogleDriveAuth.authorizationURL(config: config, pkce: pkce, state: "s1")
        let request = URLRequest(url: url)
        XCTAssertEqual(url.host, "accounts.google.com")
        XCTAssertEqual(request.query["client_id"], config.clientID)
        XCTAssertEqual(request.query["redirect_uri"], config.redirectURI)
        XCTAssertEqual(request.query["response_type"], "code")
        XCTAssertEqual(request.query["scope"], "https://www.googleapis.com/auth/drive.file")
        XCTAssertEqual(request.query["code_challenge"], pkce.challenge)
        XCTAssertEqual(request.query["code_challenge_method"], "S256")
        XCTAssertEqual(request.query["state"], "s1")
    }

    func testCallbackParsing() throws {
        let ok = URL(string: "com.googleusercontent.apps.1234-abcd:/oauth2redirect?state=s1&code=4/abc&scope=x")!
        XCTAssertEqual(try GoogleDriveAuth.code(fromCallback: ok, state: "s1"), "4/abc")
        XCTAssertThrowsError(try GoogleDriveAuth.code(fromCallback: ok, state: "other"))
        let denied = URL(string: "com.googleusercontent.apps.1234-abcd:/oauth2redirect?error=access_denied&state=s1")!
        XCTAssertThrowsError(try GoogleDriveAuth.code(fromCallback: denied, state: "s1")) { error in
            XCTAssertTrue(error is CancellationError)
        }
    }

    func testSignInExchangesCodeAndStoresRefreshToken() async throws {
        let secrets = InMemorySecretStore()
        StubURLProtocol.handler = { request in
            XCTAssertEqual(request.url, GoogleOAuthConfig.tokenEndpoint)
            XCTAssertEqual(request.httpMethod, "POST")
            return .json(#"{"access_token":"at1","expires_in":3599,"refresh_token":"rt1","token_type":"Bearer"}"#)
        }
        let auth = GoogleDriveAuth(config: config, secrets: secrets, session: StubURLProtocol.session())
        let browser = StubBrowser { url in
            let state = URLRequest(url: url).query["state"] ?? ""
            return URL(string: "com.googleusercontent.apps.1234-abcd:/oauth2redirect?code=c0de&state=\(state)")!
        }

        try await auth.signIn(using: browser)

        XCTAssertEqual(secrets.string(for: .googleDriveRefreshToken), "rt1")
        XCTAssertTrue(auth.isSignedIn)
        let body = try XCTUnwrap(StubURLProtocol.seen.first?.body.map { String(decoding: $0, as: UTF8.self) })
        let fields = Dictionary(uniqueKeysWithValues: body.split(separator: "&").map { pair -> (String, String) in
            let parts = pair.split(separator: "=", maxSplits: 1).map(String.init)
            return (parts[0], parts[1].removingPercentEncoding ?? parts[1])
        })
        XCTAssertEqual(fields["grant_type"], "authorization_code")
        XCTAssertEqual(fields["code"], "c0de")
        XCTAssertEqual(fields["redirect_uri"], config.redirectURI)
        XCTAssertEqual(fields["client_id"], config.clientID)
        XCTAssertNotNil(fields["code_verifier"])
        let cached = try await auth.accessToken()
        XCTAssertEqual(cached, "at1", "cached token is reused without a request")
        XCTAssertEqual(StubURLProtocol.seen.count, 1)
    }

    func testRevokedRefreshTokenSignsOut() async throws {
        let secrets = InMemorySecretStore([.googleDriveRefreshToken: "old"])
        StubURLProtocol.handler = { _ in .json(#"{"error":"invalid_grant"}"#, status: 400) }
        let auth = GoogleDriveAuth(config: config, secrets: secrets, session: StubURLProtocol.session())
        do {
            _ = try await auth.accessToken()
            XCTFail("expected failure")
        } catch {
            XCTAssertEqual(error as? UploadError, .notSignedIn("Google Drive"))
        }
        XCTAssertNil(secrets.string(for: .googleDriveRefreshToken))
    }

    func testResumableUploadInChunksThenShares() async throws {
        let file = try makeFile("Novel.epub", bytes: 600 * 1024)
        let session = URL(string: "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=U1")!
        let failedOnce = ProgressBox()
        StubURLProtocol.handler = { request in
            let url = request.url!
            if url == GoogleOAuthConfig.tokenEndpoint {
                return .json(#"{"access_token":"at","expires_in":3600}"#)
            }
            if url.path == "/upload/drive/v3/files", request.httpMethod == "POST" {
                return .json("", headers: ["Location": session.absoluteString])
            }
            if url == session {
                let range = request.value(forHTTPHeaderField: "Content-Range") ?? ""
                switch range {
                case "bytes 0-262143/614400":
                    return .json("", status: 308, headers: ["Range": "bytes=0-262143"])
                case "bytes 262144-524287/614400":
                    if failedOnce.all.isEmpty {
                        failedOnce.add(UploadProgress(sentBytes: 0, totalBytes: 0))
                        return .text("backend error", status: 503)
                    }
                    return .json("", status: 308, headers: ["Range": "bytes=0-524287"])
                case "bytes */614400":
                    return .json("", status: 308, headers: ["Range": "bytes=0-262143"])
                case "bytes 524288-614399/614400":
                    return .json(#"{"id":"F1","name":"Novel.epub","webViewLink":"https://drive.google.com/file/d/F1/view?usp=drivesdk"}"#)
                default:
                    XCTFail("unexpected range \(range)")
                    return .text("", status: 400)
                }
            }
            if url.path == "/drive/v3/files/F1/permissions" {
                return .json(#"{"id":"anyoneWithLink","type":"anyone","role":"reader"}"#)
            }
            XCTFail("unexpected request \(url)")
            return .text("", status: 404)
        }
        var settings = UploadSettings()
        settings.googleDriveFolderID = "FOLDER"
        var uploader = GoogleDriveUploader()
        uploader.chunkSize = 256 * 1024
        let ctx = context(secrets: [.googleDriveRefreshToken: "rt"], settings: settings, google: config)
        XCTAssertTrue(uploader.isReady(ctx))

        let result = try await uploader.upload(file, context: ctx, progress: nil)

        XCTAssertEqual(result.link.absoluteString, "https://drive.google.com/file/d/F1/view?usp=drivesdk")
        XCTAssertNil(result.expiresAt)
        let seen = StubURLProtocol.seen.map(\.request)

        let start = try XCTUnwrap(seen.first { $0.url?.path == "/upload/drive/v3/files" && $0.httpMethod == "POST" })
        XCTAssertEqual(start.query["uploadType"], "resumable")
        XCTAssertEqual(start.value(forHTTPHeaderField: "Authorization"), "Bearer at")
        XCTAssertEqual(start.value(forHTTPHeaderField: "X-Upload-Content-Type"), "application/epub+zip")
        XCTAssertEqual(start.value(forHTTPHeaderField: "X-Upload-Content-Length"), "614400")
        let metadata = try XCTUnwrap(StubURLProtocol.seen.first { $0.request == start }?.body)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: metadata) as? [String: Any])
        XCTAssertEqual(json["name"] as? String, "Novel.epub")
        XCTAssertEqual(json["parents"] as? [String], ["FOLDER"])

        let ranges = seen.filter { $0.url == session }.compactMap { $0.value(forHTTPHeaderField: "Content-Range") }
        XCTAssertEqual(ranges, [
            "bytes 0-262143/614400",
            "bytes 262144-524287/614400",
            "bytes */614400",
            "bytes 262144-524287/614400",
            "bytes 524288-614399/614400",
        ])

        let share = try XCTUnwrap(seen.last)
        XCTAssertEqual(share.url?.path, "/drive/v3/files/F1/permissions")
        let shareBody = try XCTUnwrap(StubURLProtocol.seen.last?.body)
        XCTAssertEqual(try JSONSerialization.jsonObject(with: shareBody) as? [String: String], ["role": "reader", "type": "anyone"])
    }

    func testNextOffsetFromRangeHeader() {
        let reply = HTTPReply(status: 308, headers: ["range": "bytes=0-1048575"], body: Data())
        XCTAssertEqual(GoogleDriveUploader.nextOffset(from: reply), 1_048_576)
        XCTAssertEqual(GoogleDriveUploader.nextOffset(from: HTTPReply(status: 308, headers: [:], body: Data())), 0)
    }

    func testNotReadyWithoutClientIDOrToken() {
        XCTAssertFalse(GoogleDriveUploader().isReady(context(secrets: [.googleDriveRefreshToken: "rt"])))
        XCTAssertFalse(GoogleDriveUploader().isReady(context(google: config)))
    }
}

final class UploadHistoryTests: UploadTestCase {
    func testPersistsNewestFirstAndPrunes() throws {
        let url = directory.appendingPathComponent("h/history.json")
        let store = UploadHistoryStore(fileURL: url)
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let old = UploadResult(hostID: "tempsh", fileName: "a.epub", link: URL(string: "https://temp.sh/a")!, expiresAt: now.addingTimeInterval(-1), uploadedAt: now.addingTimeInterval(-10))
        let fresh = UploadResult(hostID: "gdrive", fileName: "b.epub", link: URL(string: "https://drive.google.com/file/d/b/view")!, uploadedAt: now)
        store.add(old)
        store.add(fresh)
        store.add(fresh)

        let reloaded = UploadHistoryStore(fileURL: url)
        XCTAssertEqual(reloaded.all.map(\.fileName), ["b.epub", "a.epub"])
        XCTAssertEqual(reloaded.all.first?.uploadedAt, now)
        XCTAssertEqual(reloaded.active(at: now).map(\.fileName), ["b.epub"])

        reloaded.pruneExpired(at: now)
        XCTAssertEqual(UploadHistoryStore(fileURL: url).all.map(\.fileName), ["b.epub"])
    }

    func testLimitDropsOldest() {
        let store = UploadHistoryStore(fileURL: directory.appendingPathComponent("h.json"), limit: 2)
        for index in 0..<3 {
            store.add(UploadResult(hostID: "x", fileName: "\(index)", link: URL(string: "https://e.test/\(index)")!))
        }
        XCTAssertEqual(store.all.map(\.fileName), ["2", "1"])
    }
}
