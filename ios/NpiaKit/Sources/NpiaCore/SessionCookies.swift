import Foundation

/// Access to the cookies that web pages and the HTTP client share, for the sources whose sign-in is a
/// browser session (see `SiteSessions` in NpiaSources). The calls are async because the web views'
/// own cookie stores are.
public protocol SessionCookieAccess: Sendable {
    /// Name to value of the non-empty cookies that apply to `url`.
    func cookies(for url: URL) async -> [String: String]
    /// Stores a session cookie for `url`; `domain` widens it to subdomains (`.example.com`).
    func setCookie(name: String, value: String, url: URL, domain: String) async
    /// Deletes the cookie `name` that `url` sees, on the host and on the parent domains it was set for.
    func removeCookie(name: String, url: URL) async
}

public enum CookieDomain {
    /// True when a cookie with `cookieDomain` is sent to `host`: a domain cookie (leading dot) goes to
    /// the domain and its subdomains, a host-only cookie to that host alone.
    public static func covers(_ cookieDomain: String, host: String) -> Bool {
        let domain = cookieDomain.lowercased()
        let host = host.lowercased()
        guard !host.isEmpty, !domain.isEmpty else { return false }
        if domain.hasPrefix(".") {
            let bare = String(domain.dropFirst())
            return host == bare || host.hasSuffix("." + bare)
        }
        return host == domain
    }

    /// Cookie properties for a session cookie (no expiry) scoped to `domain` and its subdomains.
    public static func sessionCookie(name: String, value: String, url: URL, domain: String) -> HTTPCookie? {
        let scoped = domain.hasPrefix(".") ? domain : "." + domain
        return HTTPCookie(properties: [
            .name: name,
            .value: value,
            .domain: scoped,
            .path: "/",
            .originURL: url,
        ])
    }
}

/// The default: `HTTPCookieStorage.shared`, which the web engine mirrors its web views into.
public struct HTTPCookieStorageAccess: SessionCookieAccess, @unchecked Sendable {
    private let storage: HTTPCookieStorage

    public init(storage: HTTPCookieStorage = .shared) {
        self.storage = storage
    }

    public func cookies(for url: URL) async -> [String: String] {
        var found: [String: String] = [:]
        for cookie in storage.cookies(for: url) ?? [] where !cookie.value.isEmpty && found[cookie.name] == nil {
            found[cookie.name] = cookie.value
        }
        return found
    }

    public func setCookie(name: String, value: String, url: URL, domain: String) async {
        guard let cookie = CookieDomain.sessionCookie(name: name, value: value, url: url, domain: domain) else { return }
        storage.setCookie(cookie)
    }

    public func removeCookie(name: String, url: URL) async {
        let host = url.host ?? ""
        for cookie in storage.cookies ?? [] where cookie.name == name && CookieDomain.covers(cookie.domain, host: host) {
            storage.deleteCookie(cookie)
        }
    }
}
