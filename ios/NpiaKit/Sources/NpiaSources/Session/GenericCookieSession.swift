import Foundation
import NpiaCore

/// A login whose cookies live in the shared cookie store the generic reader engine reads, with a copy kept
/// in the secret store and put back when the app starts. Unlike `SiteSessions`, it keeps the login by value
/// as a whole cookie header — for sites that are not `SourceID` adapters (tapas, webtoons), which sign in
/// through their own HTTP endpoints and then download through the generic engine.
///
/// The desktop keeps these the same way (`tapas_app_cookie.txt` / `webtoons_cookie.txt` plus a per-request
/// injection in `external_scraper.py`); here the cookie goes straight into the store every request shares.
public struct GenericCookieSession: Sendable {
    private let key: SecretKey
    /// Every host the login cookies are valid on; the cookie is written for each.
    private let cookieURLs: [URL]
    /// The cookie domain, without the leading dot; the write widens the cookie to its subdomains.
    private let domain: String

    public init(key: SecretKey, cookieURLs: [URL], domain: String) {
        self.key = key
        self.cookieURLs = cookieURLs
        self.domain = domain
    }

    /// The saved cookie header, or nil when nothing is saved.
    public func savedHeader(_ secrets: any SecretStore) -> String? {
        guard let value = secrets.string(for: key)?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }

    public func hasSession(_ secrets: any SecretStore) -> Bool { savedHeader(secrets) != nil }

    /// Writes `header`'s cookies into the shared store and keeps a copy. False when it has no `name=value`.
    @discardableResult
    public func save(_ header: String, secrets: any SecretStore, cookies: any SessionCookieAccess) async -> Bool {
        let pairs = Self.pairs(header)
        guard !pairs.isEmpty else { return false }
        await inject(pairs, cookies: cookies)
        secrets.set(pairs.map { "\($0.0)=\($0.1)" }.joined(separator: "; "), for: key)
        return true
    }

    /// Puts the saved cookies back into the store (a web view may drop session cookies on restart).
    @discardableResult
    public func restore(_ secrets: any SecretStore, cookies: any SessionCookieAccess) async -> Int {
        guard let header = savedHeader(secrets) else { return 0 }
        let pairs = Self.pairs(header)
        await inject(pairs, cookies: cookies)
        return pairs.count
    }

    /// Removes the login cookies from the store and drops the saved copy.
    public func signOut(_ secrets: any SecretStore, cookies: any SessionCookieAccess) async {
        for url in cookieURLs {
            for (name, _) in Self.pairs(savedHeader(secrets) ?? "") {
                await cookies.removeCookie(name: name, url: url)
            }
        }
        secrets.set(nil, for: key)
    }

    private func inject(_ pairs: [(String, String)], cookies: any SessionCookieAccess) async {
        for url in cookieURLs {
            for (name, value) in pairs {
                await cookies.setCookie(name: name, value: value, url: url, domain: domain)
            }
        }
    }

    static func pairs(_ header: String) -> [(String, String)] {
        header.split(separator: ";").compactMap { item in
            let trimmed = item.trimmingCharacters(in: .whitespaces)
            guard let equals = trimmed.firstIndex(of: "="), equals != trimmed.startIndex else { return nil }
            return (String(trimmed[..<equals]), String(trimmed[trimmed.index(after: equals)...]))
        }
    }
}
