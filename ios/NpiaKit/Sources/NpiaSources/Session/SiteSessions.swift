import Foundation
import NpiaCore

/// A site whose sign-in is a browser session: the user signs in in the app's own browser and the login
/// lives in the cookies that downloads share. qdn and rbooks are such sites.
///
/// Some login cookies are session cookies, which a web view may drop when the app restarts (the
/// desktop snapshots and restores them for the same reason), so `SiteSessions` keeps a copy of the
/// values in the Keychain and puts them back.
public struct SessionSite: Sendable {
    public let source: SourceID
    /// The page the app's browser opens for signing in.
    public let signInURL: URL
    /// URLs whose cookies are the login (every host the login cookies are valid on).
    public let cookieURLs: [URL]
    /// Domain the login cookies belong to, without the leading dot.
    public let domain: String
    /// The cookies that carry the login; only these are kept in the saved copy.
    public let cookieNames: [String]
    /// Plain-words description of what makes a login usable, for the screen ("ywkey and ywguid").
    public let requirement: String
    private let usableWith: @Sendable (Set<String>) -> Bool

    public init(source: SourceID, signInURL: URL, cookieURLs: [URL], domain: String, cookieNames: [String],
                requirement: String, usableWith: @escaping @Sendable (Set<String>) -> Bool) {
        self.source = source
        self.signInURL = signInURL
        self.cookieURLs = cookieURLs
        self.domain = domain
        self.cookieNames = cookieNames
        self.requirement = requirement
        self.usableWith = usableWith
    }

    /// Enough of the login cookies are present for downloads to use them.
    public func isUsable(_ present: Set<String>) -> Bool {
        usableWith(present)
    }
}

public struct SessionStatus: Equatable, Sendable {
    /// Enough login cookies are present for downloads to use them.
    public var usable: Bool
    /// Names of the login cookies currently in the cookie stores.
    public var present: Set<String>
    /// A saved copy exists in the Keychain.
    public var hasSavedCopy: Bool

    public init(usable: Bool, present: Set<String>, hasSavedCopy: Bool) {
        self.usable = usable
        self.present = present
        self.hasSavedCopy = hasSavedCopy
    }
}

public extension SessionStatus {
    /// The line the Enter App screen shows for this status.
    func summary(for site: SessionSite) -> String {
        let found = present.sorted().joined(separator: ", ")
        if usable { return "Signed in (found \(found))." }
        if !present.isEmpty { return "Partly signed in: found \(found), but \(site.requirement) are needed." }
        if hasSavedCopy { return "Not signed in. A saved copy exists and is restored when a download starts." }
        return "Not signed in. Free chapters still download."
    }
}

/// Status, saved copy, restore and sign-out of browser-session logins. It only talks to the secret
/// store and a `SessionCookieAccess`, so it is tested with fakes; the app gives it the web engine's
/// cookies and the Keychain. Mirrors the Android `SiteSessions`.
public struct SiteSessions: Sendable {
    private let secrets: any SecretStore
    private let cookies: any SessionCookieAccess

    public init(secrets: any SecretStore, cookies: any SessionCookieAccess) {
        self.secrets = secrets
        self.cookies = cookies
    }

    private func key(_ site: SessionSite) -> SecretKey {
        SecretKey.source(site.source, "sessionCookies")
    }

    /// The login cookies the cookie stores hold right now, name to value.
    public func live(_ site: SessionSite) async -> [String: String] {
        var found: [String: String] = [:]
        for url in site.cookieURLs {
            for (name, value) in await cookies.cookies(for: url) {
                if site.cookieNames.contains(name), !value.isEmpty, found[name] == nil { found[name] = value }
            }
        }
        return found
    }

    private func saved(_ site: SessionSite) -> [String: String] {
        guard let raw = secrets.string(for: key(site)),
              let object = (try? JSONSerialization.jsonObject(with: Data(raw.utf8))) as? [String: Any],
              let values = object["cookies"] as? [String: Any] else { return [:] }
        var result: [String: String] = [:]
        for (name, value) in values {
            let text = HTTPSourceSupport.string(value)
            if site.cookieNames.contains(name), !text.isEmpty { result[name] = text }
        }
        return result
    }

    public func status(_ site: SessionSite) async -> SessionStatus {
        let present = Set(await live(site).keys)
        return SessionStatus(usable: site.isUsable(present), present: present, hasSavedCopy: !saved(site).isEmpty)
    }

    /// Saves the login cookies that are present. Nothing present keeps the earlier copy: a browser that
    /// never opened the site says nothing about the login. Returns how many were saved.
    @discardableResult
    public func snapshot(_ site: SessionSite) async -> Int {
        let current = await live(site)
        if current.isEmpty { return 0 }
        let value: [String: Any] = ["saved": Int(Date().timeIntervalSince1970 * 1000), "cookies": current]
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return 0 }
        secrets.set(String(decoding: data, as: UTF8.self), for: key(site))
        return current.count
    }

    /// Puts saved login cookies the cookie stores lack back. Returns how many were restored.
    @discardableResult
    public func restore(_ site: SessionSite) async -> Int {
        guard let url = site.cookieURLs.first else { return 0 }
        let present = Set(await live(site).keys)
        var restored = 0
        for (name, value) in saved(site).sorted(by: { $0.key < $1.key }) where !present.contains(name) {
            await cookies.setCookie(name: name, value: value, url: url, domain: "." + site.domain)
            restored += 1
        }
        return restored
    }

    /// Restores at most once per process and site. The stores only lose session cookies when the process
    /// restarts, so a later restore could only bring back a login the user ended in the browser.
    @discardableResult
    public func restoreOnce(_ site: SessionSite) async -> Int {
        guard Self.restoredSources.insert(site.source) else { return 0 }
        return await restore(site)
    }

    /// Removes the login cookies and the saved copy.
    public func signOut(_ site: SessionSite) async {
        for url in site.cookieURLs {
            for name in site.cookieNames { await cookies.removeCookie(name: name, url: url) }
        }
        secrets.set(nil, for: key(site))
    }

    /// Drops the saved copy only (the browser's own sign-out already removed the cookies).
    public func forgetSavedCopy(_ site: SessionSite) {
        secrets.set(nil, for: key(site))
    }

    private static let restoredSources = SourceSet()

    /// Tests start from a fresh process.
    static func resetProcessState() {
        restoredSources.removeAll()
    }
}

/// A lock-protected set of sources, for "at most once per process".
private final class SourceSet: @unchecked Sendable {
    private let lock = NSLock()
    private var values = Set<SourceID>()

    /// True when `source` was not in the set.
    func insert(_ source: SourceID) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return values.insert(source).inserted
    }

    func removeAll() {
        lock.lock(); defer { lock.unlock() }
        values = []
    }
}
