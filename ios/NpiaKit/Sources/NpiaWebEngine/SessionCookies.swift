import Foundation
import NpiaCore
import WebKit

/// The cookies the app's sign-ins live in: those of every web-view profile (the Browser tab and the
/// sign-in sheets use `BrowserProfile`, scrapers use their own) and `HTTPCookieStorage.shared`, which
/// downloads use. `CookieBridge` keeps them in step as they change, but only for stores it has seen
/// and only by adding, so reading, restoring and deleting look at all of them.
///
/// A cookie found in several places counts once; the Browser tab's store wins, because that is where
/// the user signs in.
public struct WebKitSessionCookies: SessionCookieAccess {
    public init() {}

    public func cookies(for url: URL) async -> [String: String] {
        await Self.read(url)
    }

    public func setCookie(name: String, value: String, url: URL, domain: String) async {
        await Self.write(name: name, value: value, url: url, domain: domain)
    }

    public func removeCookie(name: String, url: URL) async {
        await Self.delete(name: name, url: url)
    }

    @MainActor
    private static func stores() -> [WKWebsiteDataStore] {
        let browser = BrowserProfile.dataStore()
        return [browser] + WKWebEngine.stores.values.filter { $0 !== browser }
    }

    /// Whether a browser would send `cookie` to `url`.
    static func applies(_ cookie: HTTPCookie, to url: URL) -> Bool {
        guard CookieDomain.covers(cookie.domain, host: url.host ?? "") else { return false }
        if cookie.isSecure, url.scheme?.lowercased() != "https" { return false }
        let path = url.path.isEmpty ? "/" : url.path
        return cookie.path == "/" || path.hasPrefix(cookie.path)
    }

    @MainActor
    private static func read(_ url: URL) async -> [String: String] {
        var found: [String: String] = [:]
        for store in stores() {
            for cookie in await store.httpCookieStore.allCookies() where applies(cookie, to: url) {
                if !cookie.value.isEmpty, found[cookie.name] == nil { found[cookie.name] = cookie.value }
            }
        }
        for cookie in HTTPCookieStorage.shared.cookies(for: url) ?? [] {
            if !cookie.value.isEmpty, found[cookie.name] == nil { found[cookie.name] = cookie.value }
        }
        return found
    }

    @MainActor
    private static func write(name: String, value: String, url: URL, domain: String) async {
        guard let cookie = CookieDomain.sessionCookie(name: name, value: value, url: url, domain: domain) else { return }
        HTTPCookieStorage.shared.setCookie(cookie)
        await BrowserProfile.dataStore().httpCookieStore.setCookie(cookie)
    }

    @MainActor
    private static func delete(name: String, url: URL) async {
        let host = url.host ?? ""
        for store in stores() {
            for cookie in await store.httpCookieStore.allCookies() where cookie.name == name && CookieDomain.covers(cookie.domain, host: host) {
                await store.httpCookieStore.deleteCookie(cookie)
            }
        }
        for cookie in HTTPCookieStorage.shared.cookies ?? [] where cookie.name == name && CookieDomain.covers(cookie.domain, host: host) {
            HTTPCookieStorage.shared.deleteCookie(cookie)
        }
    }
}
