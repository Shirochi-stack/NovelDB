import Foundation
import WebKit

/// The Catalog's generic search page: the desktop's page with its app side in the page (see
/// scripts/generic/catalog-app.js), built into `Resources/catalog` by scripts/build_mobile_catalog.py.
///
/// It is served on a scheme of its own (`noveldb-catalog://app/`), so the page can fetch its scripts
/// and keep its storage. Its requests go to the app through the `noveldbCatalogTransport` message
/// handler, which answers that page only.
public enum CatalogPage {
    public static let scheme = "noveldb-catalog"
    public static let url = URL(string: "noveldb-catalog://app/index.html")!
    /// `window.webkit.messageHandlers.<name>` carries the page's requests (`window.NovelDBTransport`).
    public static let transportName = "noveldbCatalogTransport"

    public static func isPage(_ url: URL?) -> Bool { url?.scheme == scheme }

    /// Serves the page's files from the package's resources.
    public static func server() -> any WKURLSchemeHandler { Server() }

    /// The page's transport: `handler` answers each request (JSON text in, JSON text out). Requests
    /// from any other page are refused; the handler's requests carry the web view's cookies.
    public static func transport(store: WKWebsiteDataStore,
                                 handler: @escaping @Sendable (String) async -> String) -> any WKScriptMessageHandlerWithReply {
        Relay(store: store, handler: handler)
    }

    static func file(_ path: String) -> URL? {
        let clean = path.split(separator: "/").filter { $0 != ".." && !$0.isEmpty }.joined(separator: "/")
        guard !clean.isEmpty else { return nil }
        let name = (clean as NSString).deletingPathExtension
        let ext = (clean as NSString).pathExtension
        for folder in ["Resources/catalog", "catalog"] {
            if let found = Bundle.module.url(forResource: name, withExtension: ext, subdirectory: folder) { return found }
        }
        return nil
    }

    static func mimeType(_ ext: String) -> String {
        switch ext.lowercased() {
        case "html": return "text/html"
        case "js": return "text/javascript"
        case "json": return "application/json"
        case "webp": return "image/webp"
        case "png": return "image/png"
        case "css": return "text/css"
        default: return "application/octet-stream"
        }
    }

    private final class Server: NSObject, WKURLSchemeHandler {
        func webView(_ webView: WKWebView, start urlSchemeTask: any WKURLSchemeTask) {
            guard let url = urlSchemeTask.request.url, let file = CatalogPage.file(url.path),
                  let data = try? Data(contentsOf: file) else {
                urlSchemeTask.didFailWithError(URLError(.fileDoesNotExist))
                return
            }
            let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: [
                "Content-Type": CatalogPage.mimeType(file.pathExtension) + "; charset=utf-8",
                "Content-Length": String(data.count),
                "Cache-Control": "no-cache",
            ])!
            urlSchemeTask.didReceive(response)
            urlSchemeTask.didReceive(data)
            urlSchemeTask.didFinish()
        }

        func webView(_ webView: WKWebView, stop urlSchemeTask: any WKURLSchemeTask) {}
    }

    private final class Relay: NSObject, WKScriptMessageHandlerWithReply {
        private let store: WKWebsiteDataStore
        private let handler: @Sendable (String) async -> String

        init(store: WKWebsiteDataStore, handler: @escaping @Sendable (String) async -> String) {
            self.store = store
            self.handler = handler
            super.init()
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) async -> (Any?, String?) {
            guard message.frameInfo.isMainFrame, message.frameInfo.securityOrigin.protocol == CatalogPage.scheme else {
                return (nil, "Only the generic search page can use this.")
            }
            guard let body = message.body as? String else { return (nil, "The message must be a string.") }
            await CookieBridge.shared.copyToShared(from: store.httpCookieStore)
            return (await handler(body), nil)
        }
    }
}
