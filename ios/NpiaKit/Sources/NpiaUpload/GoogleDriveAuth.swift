import CryptoKit
import Foundation
import NpiaCore
#if canImport(AuthenticationServices)
import AuthenticationServices
#endif

/// The app's Google OAuth client. Register an iOS client in Google Cloud Console (see
/// `ios/docs/upload.md`) and put its id in the app's Info.plist under
/// `NpiaGoogleDriveClientID`.
public struct GoogleOAuthConfig: Sendable, Equatable {
    public static let infoPlistKey = "NpiaGoogleDriveClientID"
    static let clientSuffix = ".apps.googleusercontent.com"
    public static let scope = "https://www.googleapis.com/auth/drive.file"
    static let authorizeEndpoint = URL(string: "https://accounts.google.com/o/oauth2/v2/auth")!
    static let tokenEndpoint = URL(string: "https://oauth2.googleapis.com/token")!
    static let revokeEndpoint = URL(string: "https://oauth2.googleapis.com/revoke")!

    public let clientID: String

    /// nil for an empty or placeholder id.
    public init?(clientID: String?) {
        let id = (clientID ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard id.hasSuffix(Self.clientSuffix), id.count > Self.clientSuffix.count, !id.contains("$(") else { return nil }
        self.clientID = id
    }

    public static func fromBundle(_ bundle: Bundle = .main) -> GoogleOAuthConfig? {
        GoogleOAuthConfig(clientID: bundle.object(forInfoDictionaryKey: infoPlistKey) as? String)
    }

    /// iOS clients redirect to the reversed client id, e.g. `com.googleusercontent.apps.123-abc`.
    public var callbackScheme: String {
        "com.googleusercontent.apps." + String(clientID.dropLast(Self.clientSuffix.count))
    }

    public var redirectURI: String { callbackScheme + ":/oauth2redirect" }
}

/// RFC 7636 code verifier and S256 challenge.
public struct PKCE: Sendable, Equatable {
    public let verifier: String
    public let challenge: String

    public init(verifier: String) {
        self.verifier = verifier
        self.challenge = Self.base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
    }

    public static func generate() -> PKCE {
        PKCE(verifier: randomToken(bytes: 48))
    }

    static func randomToken(bytes: Int) -> String {
        var generator = SystemRandomNumberGenerator()
        return base64URL(Data((0..<bytes).map { _ in UInt8.random(in: .min ... .max, using: &generator) }))
    }

    static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// Opens the consent page and returns the redirect URL. `WebAuthenticationBrowser` is the
/// app's implementation; tests stub it.
public protocol OAuthBrowser: Sendable {
    func authenticate(url: URL, callbackScheme: String) async throws -> URL
}

struct GoogleTokens: Decodable, Sendable {
    var accessToken: String
    var expiresIn: Double?
    var refreshToken: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case expiresIn = "expires_in"
        case refreshToken = "refresh_token"
    }
}

/// Google sign-in with PKCE (no Google SDK). Only the refresh token is stored, in
/// `SecretStore` under `.googleDriveRefreshToken`; access tokens stay in memory.
public final class GoogleDriveAuth: @unchecked Sendable {
    public let config: GoogleOAuthConfig
    let secrets: any SecretStore
    let session: URLSession
    private let lock = NSLock()
    private var cached: (token: String, expiry: Date)?

    public init(config: GoogleOAuthConfig, secrets: any SecretStore, session: URLSession = .shared) {
        self.config = config
        self.secrets = secrets
        self.session = session
    }

    public var isSignedIn: Bool {
        !(secrets.string(for: .googleDriveRefreshToken) ?? "").isEmpty
    }

    static func authorizationURL(config: GoogleOAuthConfig, pkce: PKCE, state: String) -> URL {
        var components = URLComponents(url: GoogleOAuthConfig.authorizeEndpoint, resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "client_id", value: config.clientID),
            URLQueryItem(name: "redirect_uri", value: config.redirectURI),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "scope", value: GoogleOAuthConfig.scope),
            URLQueryItem(name: "code_challenge", value: pkce.challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "state", value: state),
        ]
        return components.url!
    }

    /// Pulls the authorization code out of the redirect, checking `state`.
    static func code(fromCallback url: URL, state: String) throws -> String {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func value(_ name: String) -> String? { items.first { $0.name == name }?.value }
        if let error = value("error") {
            if error == "access_denied" { throw CancellationError() }
            throw UploadError.rejected(host: "Google Drive", message: error)
        }
        guard value("state") == state else {
            throw UploadError.rejected(host: "Google Drive", message: "sign-in state mismatch")
        }
        guard let code = value("code"), !code.isEmpty else {
            throw UploadError.rejected(host: "Google Drive", message: "no authorization code")
        }
        return code
    }

    static func tokenRequest(_ pairs: [(String, String)]) -> URLRequest {
        var request = URLRequest(url: GoogleOAuthConfig.tokenEndpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = formEncoded(pairs)
        return request
    }

    static func exchangeRequest(config: GoogleOAuthConfig, code: String, pkce: PKCE) -> URLRequest {
        tokenRequest([
            ("client_id", config.clientID),
            ("code", code),
            ("code_verifier", pkce.verifier),
            ("grant_type", "authorization_code"),
            ("redirect_uri", config.redirectURI),
        ])
    }

    static func refreshRequest(config: GoogleOAuthConfig, refreshToken: String) -> URLRequest {
        tokenRequest([
            ("client_id", config.clientID),
            ("grant_type", "refresh_token"),
            ("refresh_token", refreshToken),
        ])
    }

    private func requestTokens(_ request: URLRequest) async throws -> GoogleTokens {
        let (data, response) = try await session.data(for: request)
        let reply = HTTPReply(data, response)
        if !reply.isSuccess {
            let error = (reply.json() as? [String: Any])?["error"] as? String
            if error == "invalid_grant" {
                secrets.set(nil, for: .googleDriveRefreshToken)
                throw UploadError.notSignedIn("Google Drive")
            }
            throw UploadError.badResponse(host: "Google Drive", status: reply.status, body: error ?? reply.snippet)
        }
        return try JSONDecoder().decode(GoogleTokens.self, from: data)
    }

    private func store(_ tokens: GoogleTokens) {
        lock.lock(); defer { lock.unlock() }
        cached = (tokens.accessToken, Date().addingTimeInterval((tokens.expiresIn ?? 3_600) - 60))
        if let refresh = tokens.refreshToken, !refresh.isEmpty {
            secrets.set(refresh, for: .googleDriveRefreshToken)
        }
    }

    /// Runs the consent flow and saves the refresh token.
    public func signIn(using browser: any OAuthBrowser) async throws {
        let pkce = PKCE.generate()
        let state = PKCE.randomToken(bytes: 16)
        let url = Self.authorizationURL(config: config, pkce: pkce, state: state)
        let callback = try await browser.authenticate(url: url, callbackScheme: config.callbackScheme)
        let code = try Self.code(fromCallback: callback, state: state)
        let tokens = try await requestTokens(Self.exchangeRequest(config: config, code: code, pkce: pkce))
        guard tokens.refreshToken != nil else {
            throw UploadError.rejected(host: "Google Drive", message: "no refresh token was issued")
        }
        store(tokens)
    }

    /// Returns a cached access token, refreshing it when it is about to expire.
    public func accessToken(forceRefresh: Bool = false) async throws -> String {
        if !forceRefresh {
            lock.lock()
            let current = cached
            lock.unlock()
            if let current, current.expiry > Date() { return current.token }
        }
        guard let refresh = secrets.string(for: .googleDriveRefreshToken), !refresh.isEmpty else {
            throw UploadError.notSignedIn("Google Drive")
        }
        let tokens = try await requestTokens(Self.refreshRequest(config: config, refreshToken: refresh))
        store(tokens)
        return tokens.accessToken
    }

    /// Revokes the grant (best effort) and forgets the tokens.
    public func signOut() async {
        if let refresh = secrets.string(for: .googleDriveRefreshToken) {
            var request = URLRequest(url: GoogleOAuthConfig.revokeEndpoint)
            request.httpMethod = "POST"
            request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
            request.httpBody = formEncoded([("token", refresh)])
            _ = try? await session.data(for: request)
        }
        secrets.set(nil, for: .googleDriveRefreshToken)
        lock.lock(); cached = nil; lock.unlock()
    }
}

#if canImport(AuthenticationServices)
/// `ASWebAuthenticationSession` front end. Pass the window to present from (the app's key
/// window); without one the system picks a fresh anchor.
public final class WebAuthenticationBrowser: NSObject, OAuthBrowser, ASWebAuthenticationPresentationContextProviding, @unchecked Sendable {
    private let anchor: (@MainActor () -> ASPresentationAnchor?)?
    private var session: ASWebAuthenticationSession?

    public init(anchor: (@MainActor () -> ASPresentationAnchor?)? = nil) {
        self.anchor = anchor
    }

    public func authenticate(url: URL, callbackScheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
            let once = ResumeOnce(continuation)
            DispatchQueue.main.async {
                let session = ASWebAuthenticationSession(url: url, callbackURLScheme: callbackScheme) { [weak self] callback, error in
                    self?.session = nil
                    if let callback {
                        once.resume(.success(callback))
                    } else if let error = error as? ASWebAuthenticationSessionError, error.code == .canceledLogin {
                        once.resume(.failure(CancellationError()))
                    } else {
                        once.resume(.failure(error ?? UploadError.rejected(host: "Google Drive", message: "sign-in failed")))
                    }
                }
                session.presentationContextProvider = self
                self.session = session
                if !session.start() {
                    self.session = nil
                    once.resume(.failure(UploadError.rejected(host: "Google Drive", message: "could not open the sign-in page")))
                }
            }
        }
    }

    public func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            anchor?() ?? ASPresentationAnchor()
        }
    }
}

/// The session can report failure both from `start()` and its handler; resume only once.
private final class ResumeOnce: @unchecked Sendable {
    private var continuation: CheckedContinuation<URL, Error>?
    private let lock = NSLock()

    init(_ continuation: CheckedContinuation<URL, Error>) {
        self.continuation = continuation
    }

    func resume(_ result: Result<URL, Error>) {
        lock.lock()
        let pending = continuation
        continuation = nil
        lock.unlock()
        pending?.resume(with: result)
    }
}
#endif
