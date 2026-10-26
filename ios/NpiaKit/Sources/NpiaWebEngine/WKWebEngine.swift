import Foundation
import NpiaCore
import WebKit

/// Offscreen WKWebView pages, the replacement for the desktop Playwright/CDP driver.
///
/// Each profile gets its own persistent data store so logins survive relaunches, the way the
/// desktop app kept a Chrome profile. Cookies flow both ways between every profile's
/// `WKHTTPCookieStore` and `HTTPCookieStorage.shared`, so `NpiaNet` requests carry the same
/// login as the pages. On iOS, pages are attached to a hidden window so WebKit does not
/// throttle their timers. Logins go through `WebLoginPresenting` (see `WebLogin.swift`).
public final class WKWebEngine: WebEngineProviding, @unchecked Sendable {
    private let persistent: Bool
    private let presenter: (any WebLoginPresenting)?

    /// - Parameters:
    ///   - persistent: false keeps every profile in memory only (tests, private sessions).
    ///   - loginPresenter: shows login sheets; defaults to `WebLoginCoordinator.shared`.
    public init(persistent: Bool = true, loginPresenter: (any WebLoginPresenting)? = nil) {
        self.persistent = persistent
        self.presenter = loginPresenter
    }

    public func newPage(profile: String) async throws -> any WebPageDriving {
        await makePage(profile: profile)
    }

    public func presentLogin(url: URL, profile: String) async throws {
        try await runLogin(url: url, profile: profile)
    }

    /// Copies the profile's web-view cookies into `HTTPCookieStorage.shared` and back.
    @MainActor
    public func syncCookies(profile: String) async {
        let store = Self.dataStore(for: profile, persistent: persistent)
        await CookieBridge.shared.copyToShared(from: store.httpCookieStore)
        await CookieBridge.shared.copyFromShared(into: store.httpCookieStore, for: nil)
    }

    /// Removes every cookie and cached item of a profile (sign out).
    @MainActor
    public func clearProfile(_ profile: String) async {
        let store = Self.dataStore(for: profile, persistent: persistent)
        let cookies = await store.httpCookieStore.allCookies()
        for cookie in cookies {
            HTTPCookieStorage.shared.deleteCookie(cookie)
        }
        await store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast)
    }

    @MainActor
    private func makePage(profile: String) async -> WKWebPage {
        let store = Self.dataStore(for: profile, persistent: persistent)
        await CookieBridge.shared.copyFromShared(into: store.httpCookieStore, for: nil)
        return WKWebPage(store: store)
    }

    @MainActor
    private func runLogin(url: URL, profile: String) async throws {
        let presenter = self.presenter ?? WebLoginCoordinator.shared
        guard presenter.canPresent else { throw WebEngineError.unavailable }
        let store = Self.dataStore(for: profile, persistent: persistent)
        await CookieBridge.shared.copyFromShared(into: store.httpCookieStore, for: url)
        let session = WebLoginSession(url: url, profile: profile, store: store)
        defer { presenter.dismiss(session) }
        try await session.run(presenter: presenter)
        await CookieBridge.shared.copyToShared(from: store.httpCookieStore)
    }

    @MainActor static var stores: [String: WKWebsiteDataStore] = [:]

    @MainActor
    static func dataStore(for profile: String, persistent: Bool) -> WKWebsiteDataStore {
        let key = (persistent ? "p:" : "m:") + profile
        if let store = stores[key] { return store }
        let store = persistent ? WKWebsiteDataStore(forIdentifier: profileUUID(profile)) : .nonPersistent()
        stores[key] = store
        CookieBridge.shared.observe(store.httpCookieStore)
        return store
    }

    /// Stable UUID per profile name (name-based, so the same profile maps to the same store).
    static func profileUUID(_ profile: String) -> UUID {
        var bytes = [UInt8](repeating: 0, count: 16)
        for (index, byte) in profile.utf8.enumerated() {
            bytes[index % 16] = bytes[index % 16] &* 31 &+ byte
        }
        bytes[6] = (bytes[6] & 0x0F) | 0x40
        bytes[8] = (bytes[8] & 0x3F) | 0x80
        return UUID(uuid: (bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
                           bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]))
    }
}

// MARK: - Cookies

/// Mirrors web-view cookies into `HTTPCookieStorage.shared` as they change, and seeds web
/// views with cookies `NpiaNet` received (for example from an API login).
@MainActor
final class CookieBridge: NSObject, WKHTTPCookieStoreObserver {
    static let shared = CookieBridge()

    private var observed: [ObjectIdentifier: WKHTTPCookieStore] = [:]
    private var copying: Set<ObjectIdentifier> = []

    func observe(_ store: WKHTTPCookieStore) {
        let key = ObjectIdentifier(store)
        guard observed[key] == nil else { return }
        observed[key] = store
        store.add(self)
    }

    func copyToShared(from store: WKHTTPCookieStore) async {
        let cookies = await store.allCookies()
        let jar = HTTPCookieStorage.shared
        for cookie in cookies {
            jar.setCookie(cookie)
        }
    }

    /// Copies shared cookies (all, or those that apply to `url`) into a web-view store.
    func copyFromShared(into store: WKHTTPCookieStore, for url: URL?) async {
        let jar = HTTPCookieStorage.shared
        let cookies = (url.flatMap { jar.cookies(for: $0) } ?? jar.cookies) ?? []
        guard !cookies.isEmpty else { return }
        let key = ObjectIdentifier(store)
        copying.insert(key)
        let existing = await store.allCookies()
        for cookie in cookies where !existing.contains(where: { Self.same($0, cookie) && $0.value == cookie.value }) {
            await store.setCookie(cookie)
        }
        copying.remove(key)
    }

    static func same(_ lhs: HTTPCookie, _ rhs: HTTPCookie) -> Bool {
        lhs.name == rhs.name && lhs.domain == rhs.domain && lhs.path == rhs.path
    }

    nonisolated func cookiesDidChange(in cookieStore: WKHTTPCookieStore) {
        Task { @MainActor in
            guard !self.copying.contains(ObjectIdentifier(cookieStore)) else { return }
            await self.copyToShared(from: cookieStore)
        }
    }
}

// MARK: - Page

@MainActor
final class WKWebPage: NSObject, WebPageDriving, WKNavigationDelegate {
    let webView: WKWebView
    private let store: WKWebsiteDataStore
    private var navigation: CheckedContinuation<Void, Error>?
    private var navigationToken = 0
    private var waitState: WebLoadState = .load

    init(store: WKWebsiteDataStore) {
        self.store = store
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = store
        configuration.suppressesIncrementalRendering = false
        #if os(iOS)
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        #endif
        webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 1280, height: 2000), configuration: configuration)
        super.init()
        webView.navigationDelegate = self
        HiddenHost.attach(webView)
    }

    func load(_ url: URL, until state: WebLoadState, timeout: TimeInterval) async throws {
        finish(with: WebEngineError.navigationFailed("superseded"))
        if !url.isFileURL, url.scheme != "data" {
            await CookieBridge.shared.copyFromShared(into: store.httpCookieStore, for: url)
        }
        waitState = state
        navigationToken += 1
        let token = navigationToken
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            navigation = continuation
            if url.isFileURL {
                webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
            } else {
                webView.load(URLRequest(url: url))
            }
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(max(0.1, timeout) * 1_000_000_000))
                guard let self, self.navigationToken == token else { return }
                self.finish(with: WebEngineError.timeout(url))
            }
        }
    }

    func evaluateJSON(_ functionBody: String) async throws -> Data {
        let wrapped = "return JSON.stringify(await (async () => { \(functionBody) })()) ?? 'null';"
        do {
            let value = try await webView.callAsyncJavaScript(wrapped, arguments: [:], in: nil, contentWorld: .page)
            return Data(((value as? String) ?? "null").utf8)
        } catch {
            throw WebEngineError.scriptFailed(Self.describe(error))
        }
    }

    func addInitScript(_ source: String) async {
        let script = WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: false)
        webView.configuration.userContentController.addUserScript(script)
    }

    func currentURL() async -> URL? {
        webView.url
    }

    func html() async throws -> String {
        let data = try await evaluateJSON("return document.documentElement.outerHTML;")
        return try JSONDecoder().decode(String.self, from: data)
    }

    func close() async {
        finish(with: CancellationError())
        webView.stopLoading()
        webView.navigationDelegate = nil
        webView.configuration.userContentController.removeAllUserScripts()
        HiddenHost.detach(webView)
        await CookieBridge.shared.copyToShared(from: store.httpCookieStore)
    }

    private func finish(with error: Error?) {
        guard let continuation = navigation else { return }
        navigation = nil
        if let error {
            continuation.resume(throwing: error)
        } else {
            continuation.resume()
        }
    }

    nonisolated static func describe(_ error: Error) -> String {
        let nsError = error as NSError
        if let message = nsError.userInfo["WKJavaScriptExceptionMessage"] as? String {
            return message
        }
        return nsError.localizedDescription
    }

    /// Cancelled navigations (a redirect or script replaced the request) are not failures;
    /// the replacement navigation finishes the wait instead.
    nonisolated static func isInterruption(_ error: Error) -> Bool {
        let nsError = error as NSError
        return nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled
    }

    // MARK: WKNavigationDelegate

    nonisolated func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        Task { @MainActor in
            guard self.waitState == .domContentLoaded, self.navigation != nil else { return }
            let token = self.navigationToken
            // Commit happens before DOMContentLoaded; poll readyState briefly.
            for _ in 0..<200 {
                guard self.navigationToken == token, self.navigation != nil else { return }
                if let state = try? await self.webView.evaluateJavaScript("document.readyState") as? String,
                   state != "loading" {
                    if self.navigationToken == token { self.finish(with: nil) }
                    return
                }
                try? await Task.sleep(nanoseconds: 50_000_000)
            }
        }
    }

    nonisolated func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        Task { @MainActor in self.finish(with: nil) }
    }

    nonisolated func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        guard !Self.isInterruption(error) else { return }
        let message = error.localizedDescription
        Task { @MainActor in self.finish(with: WebEngineError.navigationFailed(message)) }
    }

    nonisolated func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard !Self.isInterruption(error) else { return }
        let message = error.localizedDescription
        Task { @MainActor in self.finish(with: WebEngineError.navigationFailed(message)) }
    }

    nonisolated func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        Task { @MainActor in self.finish(with: WebEngineError.navigationFailed("web content process terminated")) }
    }
}

// MARK: - Hidden host

#if canImport(UIKit)
import UIKit

/// A window below the app's main window. Views inside it count as on screen, so WebKit keeps
/// running timers and animation frames at full rate, while the app window covers them.
@MainActor
enum HiddenHost {
    private static var window: UIWindow?

    static func attach(_ view: UIView) {
        guard let host = hostView() else { return }
        host.insertSubview(view, at: 0)
    }

    static func detach(_ view: UIView) {
        view.removeFromSuperview()
    }

    private static func hostView() -> UIView? {
        if let window, window.windowScene != nil { return window.rootViewController?.view }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first else {
            return nil
        }
        let created = UIWindow(windowScene: scene)
        created.windowLevel = UIWindow.Level(rawValue: UIWindow.Level.normal.rawValue - 1)
        created.isUserInteractionEnabled = false
        let controller = UIViewController()
        controller.view.isUserInteractionEnabled = false
        controller.view.clipsToBounds = true
        created.rootViewController = controller
        created.isHidden = false
        window = created
        return controller.view
    }
}
#else
@MainActor
enum HiddenHost {
    static func attach(_ view: WKWebView) {}
    static func detach(_ view: WKWebView) {}
}
#endif
