import Foundation
import NpiaCore
import WebKit

/// Offscreen WKWebView pages. Each profile gets its own persistent data store so logins
/// survive relaunches, the way the desktop app kept a Chrome profile.
///
/// TODO(webengine workstream): attach pages to a hidden window on iOS so timers are not
/// throttled, sync `WKHTTPCookieStore` with `HTTPCookieStorage.shared`, and implement
/// `presentLogin` with a visible sheet.
public final class WKWebEngine: WebEngineProviding, @unchecked Sendable {
    public init() {}

    public func newPage(profile: String) async throws -> any WebPageDriving {
        await WKWebPage(profile: profile)
    }

    public func presentLogin(url: URL, profile: String) async throws {
        throw WebEngineError.unavailable
    }

    @MainActor
    static func dataStore(for profile: String) -> WKWebsiteDataStore {
        WKWebsiteDataStore(forIdentifier: profileUUID(profile))
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

@MainActor
final class WKWebPage: NSObject, WebPageDriving, WKNavigationDelegate {
    private let webView: WKWebView
    private var navigation: CheckedContinuation<Void, Error>?
    private var waitState: WebLoadState = .load

    init(profile: String) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = WKWebEngine.dataStore(for: profile)
        webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 1280, height: 2000), configuration: configuration)
        super.init()
        webView.navigationDelegate = self
    }

    func load(_ url: URL, until state: WebLoadState, timeout: TimeInterval) async throws {
        finish(with: WebEngineError.navigationFailed("superseded"))
        waitState = state
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            navigation = continuation
            webView.load(URLRequest(url: url))
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                self?.finish(with: WebEngineError.timeout(url))
            }
        }
    }

    func evaluateJSON(_ functionBody: String) async throws -> Data {
        let wrapped = "return JSON.stringify(await (async () => { \(functionBody) })()) ?? 'null';"
        do {
            let value = try await webView.callAsyncJavaScript(wrapped, arguments: [:], in: nil, contentWorld: .page)
            return Data(((value as? String) ?? "null").utf8)
        } catch {
            throw WebEngineError.scriptFailed(error.localizedDescription)
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

    // MARK: WKNavigationDelegate

    nonisolated func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        Task { @MainActor in
            guard self.waitState == .domContentLoaded else { return }
            // Commit happens before DOMContentLoaded; poll readyState briefly.
            for _ in 0..<100 {
                if let state = try? await self.webView.evaluateJavaScript("document.readyState") as? String,
                   state != "loading" {
                    self.finish(with: nil)
                    return
                }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
    }

    nonisolated func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        Task { @MainActor in self.finish(with: nil) }
    }

    nonisolated func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        let message = error.localizedDescription
        Task { @MainActor in self.finish(with: WebEngineError.navigationFailed(message)) }
    }

    nonisolated func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let message = error.localizedDescription
        Task { @MainActor in self.finish(with: WebEngineError.navigationFailed(message)) }
    }
}
