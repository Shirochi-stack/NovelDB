import Foundation
import NpiaCore

/// Where the `LOGINKEY` cookie goes. The app uses `HTTPCookieStorage.shared`, which
/// `URLSessionHTTPClient` and the web engine read; tests record the value instead.
public protocol NpiaCookieSetting: Sendable {
    func setLoginKey(_ key: String)
}

public struct SharedCookieStorageJar: NpiaCookieSetting {
    public init() {}

    public func setLoginKey(_ key: String) {
        for domain in [NpiaParser.host, "." + NpiaParser.host] {
            let properties: [HTTPCookiePropertyKey: Any] = [
                .name: NpiaAuth.cookieName, .value: key, .domain: domain, .path: "/", .secure: "TRUE",
            ]
            if let cookie = HTTPCookie(properties: properties) {
                HTTPCookieStorage.shared.setCookie(cookie)
            }
        }
    }
}

/// Port of `npia_auth.py`: a random `LOGINKEY` cookie that a successful `/proc/login` binds to
/// the account. The key is stored in `SecretStore` (`.npiaLoginKey`) and reused later.
public enum NpiaAuth {
    public static let cookieName = "LOGINKEY"
    /// "감사합니다", the text the login response contains on success.
    static let successMarker = "\u{ac10}\u{c0ac}\u{d569}\u{b2c8}\u{b2e4}"

    /// Mobile fingerprint the desktop client sends with every request.
    static let headers: [String: String] = [
        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://\(NpiaParser.host)",
        "Referer": "https://\(NpiaParser.host)/",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    ]

    static func headers(referer: String? = nil, form: Bool = false) -> [String: String] {
        var value = headers
        if let referer { value["Referer"] = referer }
        if form { value["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8" }
        return value
    }

    /// 32 hex chars, `_`, 32 hex chars.
    public static func generateLoginKey() -> String {
        let digits = Array("0123456789abcdef")
        func part() -> String { String((0..<32).map { _ in digits.randomElement()! }) }
        return part() + "_" + part()
    }

    static func formBody(_ fields: [(String, String)]) -> Data {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._*")
        let encoded = fields.map { key, value in
            let k = key.addingPercentEncoding(withAllowedCharacters: allowed) ?? key
            let v = (value.addingPercentEncoding(withAllowedCharacters: allowed.union(.init(charactersIn: " "))) ?? value)
                .replacingOccurrences(of: " ", with: "+")
            return "\(k)=\(v)"
        }
        return Data(encoded.joined(separator: "&").utf8)
    }

    /// Signs in with email and password. On success the new key is stored and returned.
    @discardableResult
    public static func login(email: String, password: String, context: SourceContext, cookies: any NpiaCookieSetting) async throws -> String? {
        let key = generateLoginKey()
        cookies.setLoginKey(key)
        let url = URL(string: "https://\(NpiaParser.host)/proc/login")!
        let body = formBody([("redirectrurl", ""), ("email", email), ("wd", password)])
        let response = try await context.http.send(HTTPRequest(url: url, method: "POST", headers: headers(form: true), body: body))
        guard response.text.contains(successMarker) else {
            context.logger.log(.warning, "npia login failed.")
            return nil
        }
        context.secrets.set(key, for: .npiaLoginKey)
        context.logger.log(.info, "npia login succeeded.")
        return key
    }

    /// Picks the session for a job. Order: (a) auto-login with saved credentials, (b) the saved
    /// session key, (c) a sign-in with saved credentials even though auto-login is off, (d) a
    /// throwaway key, which reads free chapters only. A failed sign-in logs a warning and falls
    /// through to the next step.
    @discardableResult
    public static func prepare(context: SourceContext, cookies: any NpiaCookieSetting) async throws -> String {
        let secrets = context.secrets
        let email = secrets.string(for: .npiaEmail) ?? ""
        let password = secrets.string(for: .npiaPassword) ?? ""
        let hasCredentials = !email.isEmpty && !password.isEmpty
        var triedSignIn = false

        if context.settings.npia.autoLogin, hasCredentials {
            triedSignIn = true
            if let key = try await signIn(email: email, password: password, context: context, cookies: cookies) {
                return key
            }
        }
        if let key = secrets.string(for: .npiaLoginKey), !key.isEmpty {
            cookies.setLoginKey(key)
            return key
        }
        if hasCredentials, !triedSignIn {
            if let key = try await signIn(email: email, password: password, context: context, cookies: cookies) {
                return key
            }
        }
        let key = generateLoginKey()
        cookies.setLoginKey(key)
        context.logger.log(.info, "npia: no saved login; only free chapters can be read.")
        return key
    }

    /// `login` that reports every failure, a network error included, as a warning and nil.
    private static func signIn(email: String, password: String, context: SourceContext, cookies: any NpiaCookieSetting) async throws -> String? {
        do {
            if let key = try await login(email: email, password: password, context: context, cookies: cookies) {
                return key
            }
        } catch {
            if error is CancellationError || Task.isCancelled { throw CancellationError() }
            context.logger.log(.warning, "npia sign-in error: \(error.localizedDescription)")
        }
        context.logger.log(.warning, "npia sign-in failed; check the email and password in Settings > npia login")
        return nil
    }
}
