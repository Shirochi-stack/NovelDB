import Combine
import Foundation
import NpiaCore
import WebKit

/// The data store behind the Browser tab. `CookieBridge` mirrors it into
/// `HTTPCookieStorage.shared`, and scraper pages are seeded from there, so signing in to any site
/// here signs downloads from that site in.
@MainActor
public enum BrowserProfile {
    public static let name = "browser"

    public static func dataStore() -> WKWebsiteDataStore {
        WKWebEngine.dataStore(for: name, persistent: true)
    }

    /// Turns typed text into a URL: full URLs pass through, bare domains get https, anything
    /// else becomes a search.
    public nonisolated static func normalize(_ input: String) -> URL? {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if text.range(of: #"^[a-zA-Z][a-zA-Z0-9+.-]*://"#, options: .regularExpression) != nil {
            return URL(string: text)
        }
        if !text.contains(" "), text.range(of: #"^[^/]+\.[a-zA-Z]{2,}(/.*)?$"#, options: .regularExpression) != nil {
            return URL(string: "https://" + text)
        }
        var components = URLComponents(string: "https://duckduckgo.com/")!
        components.queryItems = [URLQueryItem(name: "q", value: text)]
        return components.url
    }
}

/// Signed-in sites, read from the cookie stores, with sign-out per site.
@MainActor
public enum SiteCookies {
    /// Registrable domains holding cookies in the browser, any scraper profile, or the shared jar.
    public static func domains() async -> [String] {
        var hosts = Set<String>()
        for cookie in await allCookies() {
            hosts.insert(registrableDomain(cookie.domain))
        }
        return hosts.filter { !$0.isEmpty }.sorted()
    }

    /// Deletes every cookie for `domain` and its subdomains from every store.
    public static func signOut(_ domain: String) async {
        let matches: (HTTPCookie) -> Bool = { registrableDomain($0.domain) == domain }
        for store in knownStores() {
            for cookie in await store.httpCookieStore.allCookies() where matches(cookie) {
                await store.httpCookieStore.deleteCookie(cookie)
            }
        }
        for cookie in HTTPCookieStorage.shared.cookies ?? [] where matches(cookie) {
            HTTPCookieStorage.shared.deleteCookie(cookie)
        }
    }

    /// Removes all cookies and site data from every store. Saved passwords in iCloud Keychain stay.
    public static func signOutAll() async {
        for store in knownStores() {
            await store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast)
        }
        for cookie in HTTPCookieStorage.shared.cookies ?? [] {
            HTTPCookieStorage.shared.deleteCookie(cookie)
        }
    }

    /// `.www.a.example.co.kr` -> `example.co.kr`; good enough for cookie domains without a suffix list.
    public nonisolated static func registrableDomain(_ host: String) -> String {
        let labels = host.lowercased().split(separator: ".").map(String.init)
        guard labels.count > 2 else { return labels.joined(separator: ".") }
        let secondLevel: Set<String> = ["co", "com", "ne", "net", "or", "org", "go", "ac", "ed", "gov", "edu"]
        let take = labels.last!.count == 2 && secondLevel.contains(labels[labels.count - 2]) ? 3 : 2
        return labels.suffix(take).joined(separator: ".")
    }

    private static func knownStores() -> [WKWebsiteDataStore] {
        _ = BrowserProfile.dataStore()
        return Array(WKWebEngine.stores.values)
    }

    private static func allCookies() async -> [HTTPCookie] {
        var cookies = HTTPCookieStorage.shared.cookies ?? []
        for store in knownStores() {
            cookies += await store.httpCookieStore.allCookies()
        }
        return cookies
    }
}

#if canImport(UIKit) && canImport(SwiftUI)
import SwiftUI

/// State of the Browser tab's single web view. Password AutoFill (iCloud Keychain or another
/// password manager) works in it like in Safari.
@MainActor
public final class BrowserState: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate {
    public let webView: WKWebView
    @Published public private(set) var url: URL?
    @Published public private(set) var title = ""
    @Published public private(set) var progress = 0.0
    @Published public private(set) var isLoading = false
    @Published public private(set) var canGoBack = false
    @Published public private(set) var canGoForward = false
    private var observations: [NSKeyValueObservation] = []

    public override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = BrowserProfile.dataStore()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = true
        super.init()
        webView.navigationDelegate = self
        webView.uiDelegate = self
        observations = [
            webView.observe(\.url) { [weak self] view, _ in Task { @MainActor in self?.url = view.url } },
            webView.observe(\.title) { [weak self] view, _ in Task { @MainActor in self?.title = view.title ?? "" } },
            webView.observe(\.estimatedProgress) { [weak self] view, _ in Task { @MainActor in self?.progress = view.estimatedProgress } },
            webView.observe(\.isLoading) { [weak self] view, _ in Task { @MainActor in self?.isLoading = view.isLoading } },
            webView.observe(\.canGoBack) { [weak self] view, _ in Task { @MainActor in self?.canGoBack = view.canGoBack } },
            webView.observe(\.canGoForward) { [weak self] view, _ in Task { @MainActor in self?.canGoForward = view.canGoForward } },
        ]
    }

    public func open(_ text: String) {
        guard let url = BrowserProfile.normalize(text) else { return }
        webView.load(URLRequest(url: url))
    }

    /// Popups (common on sign-in pages) load in this view.
    public func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                        for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if navigationAction.targetFrame == nil { webView.load(navigationAction.request) }
        return nil
    }
}

/// Hosts a `BrowserState`'s web view in SwiftUI.
public struct BrowserWebView: UIViewRepresentable {
    let state: BrowserState

    public init(state: BrowserState) {
        self.state = state
    }

    public func makeUIView(context: Context) -> WKWebView { state.webView }
    public func updateUIView(_ uiView: WKWebView, context: Context) {}
}
#endif
