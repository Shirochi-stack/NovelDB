import NpiaCore
import NpiaSources
import NpiaWebEngine
import SwiftUI
import UIKit
import WebKit

/// The catalog site the Catalog tab opens (Settings > Catalog site).
enum CatalogSite {
    static let storageKey = "catalogSite"
    /// The NovelDB catalog website, hosted on GitHub Pages.
    static let defaultAddress = CatalogPages.defaultAddress
    /// NovelDB Next, offered as a suggestion; its cards get link-cart buttons too.
    static let nextAddress = CatalogPages.nextAddress
    /// On sites other than NovelDB, NovelDB Next and the sites NovelDB downloads from, links to
    /// other sites go to the link cart (and read "🛒 Download"); off, they open in the Browser tab.
    static let cartLinksKey = "catalogCartLinks"

    static var cartLinksEnabled: Bool {
        UserDefaults.standard.object(forKey: cartLinksKey) as? Bool ?? true
    }

    static var current: URL {
        valid(UserDefaults.standard.string(forKey: storageKey)) ?? URL(string: defaultAddress)!
    }

    /// An address the Catalog tab can open: http or https with a site name ("https://" alone is not).
    static func valid(_ text: String?) -> URL? {
        guard let text = text?.trimmingCharacters(in: .whitespacesAndNewlines), let url = URL(string: text),
              let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host, !host.isEmpty else { return nil }
        return url
    }
}

/// The catalog website's web view. It lives as long as the root view, so switching tabs does not
/// reload the site (it keeps a large catalog in memory). `CatalogBridge` runs on every page and
/// reports its address; the app decides what the page is (`CatalogPages.mode`) every time the
/// address changes, single-page sites included:
/// - NovelDB and NovelDB Next: cards open View Details and carry link-cart buttons, outside links
///   open in the Browser tab.
/// - A site we download from: outside links open in the Browser tab too, and a downloadable page
///   gets a floating Download button that adds it to the cart.
/// - Any other site: every link that leaves it goes to the cart (setting on) and reads "Download".
@MainActor
final class CatalogState: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    let webView: WKWebView
    @Published private(set) var progress = 0.0
    @Published private(set) var isLoading = false
    /// Why the catalog site failed to load; shown instead of a blank page.
    @Published private(set) var error: String?
    private var observations: [NSKeyValueObservation] = []
    weak var model: AppModel?
    private(set) var site: URL
    /// The page on screen (it follows the site's own navigation, so it differs from `site` after the
    /// first tap) and the address and title its bridge last reported.
    private var tracker: CatalogPageTracker
    private var engines: EngineSettings { model?.settings.engines ?? EngineSettings() }
    private var mode: CatalogPageMode {
        CatalogPages.mode(for: tracker.pageURL, engines: engines, cartLinksSetting: CatalogSite.cartLinksEnabled)
    }

    /// Tells the page what it is: whether to rename outside links, and the page to offer for
    /// download (if any). The page only shows a button for the address it reported.
    func showMode() {
        let now = mode
        let script = CatalogPages.setModeScript(now, download: tracker.download(for: now))
        webView.evaluateJavaScript(script, completionHandler: nil)
    }

    override init() {
        let start = CatalogSite.current
        site = start
        tracker = CatalogPageTracker(pageURL: start)
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        let controller = WKUserContentController()
        controller.addUserScript(WKUserScript(source: CatalogBridge.source, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        configuration.userContentController = controller
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = true
        super.init()
        let refresh = UIRefreshControl()
        refresh.addTarget(self, action: #selector(pulled(_:)), for: .valueChanged)
        webView.scrollView.refreshControl = refresh
        controller.add(WeakMessageHandler(self), name: CatalogBridge.handlerName)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        observations = [
            webView.observe(\.estimatedProgress) { [weak self] view, _ in Task { @MainActor in self?.progress = view.estimatedProgress } },
            webView.observe(\.isLoading) { [weak self] view, _ in Task { @MainActor in self?.isLoading = view.isLoading } },
            // Every address change, same-document ones included (single-page sites push a new address without loading).
            webView.observe(\.url) { [weak self] _, _ in Task { @MainActor in self?.navigated() } },
        ]
        webView.load(URLRequest(url: site))
    }

    /// Opens another catalog site (from Settings).
    func load(_ url: URL) {
        guard url != site else { return }
        site = url
        tracker.reset(to: url)
        error = nil
        webView.load(URLRequest(url: url))
    }

    /// The view moved to another address: classify it again. A Download button made for the old
    /// address goes away until the page reports the new one.
    private func navigated() {
        guard let url = webView.url, url.absoluteString != "about:blank" else { return }
        tracker.navigated(to: url)
        showMode()
    }

    /// The page's bridge reported its address and title. A page can only speak for the site the view shows.
    private func located(url: String, title: String) {
        guard tracker.located(url: url, title: title, shown: webView.url) else { return }
        showMode()
        if let model { showCart(model.cart.items) }
    }

    /// Tells the page which cards are in the cart, so their buttons show it. A phone-site page counts as
    /// in the cart when the desktop address it is kept under is.
    func showCart(_ items: [CartItem]) {
        var addresses = items.map(\.url)
        if let page = tracker.reported?.url, !addresses.contains(page), let url = URL(string: page),
           addresses.contains(SourceCatalog.canonicalURL(url).absoluteString) {
            addresses.append(page)
        }
        guard let data = try? JSONEncoder().encode(addresses), let urls = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.NovelDBApp && window.NovelDBApp.setCart(\(urls))", completionHandler: nil)
    }

    @objc private func pulled(_ control: UIRefreshControl) {
        reload()
        control.endRefreshing()
    }

    func reload() {
        error = nil
        if webView.url == nil { webView.load(URLRequest(url: site)) } else { webView.reload() }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: any Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: any Error) {
        failed(error)
    }

    /// Navigation cancelled on purpose (a link sent to the cart or the Browser tab) is not a failure.
    private func failed(_ error: any Error) {
        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled { return }
        if nsError.domain == "WebKitErrorDomain" && nsError.code == 102 { return }
        self.error = nsError.localizedDescription
    }

    /// A link out of the catalog: the link cart on sites we do not know (setting on), else the Browser tab.
    private func leave(_ url: URL, title: String = "") {
        guard url.scheme == "http" || url.scheme == "https" else { return }
        if !mode.cartLinks {
            model?.pendingBrowserURL = url
        } else {
            let source = SourceCatalog.adapterType(for: url).map { $0.id.rawValue } ?? ""
            model?.cart.add(CartItem(url: url.absoluteString, title: title, source: source))
        }
    }

    /// Same site as the page on screen (a site may have moved to www. or another host on load).
    private func sameSite(_ url: URL) -> Bool {
        let page = webView.url ?? site
        return url.scheme == page.scheme && url.host == page.host && url.port == page.port
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let text = message.body as? String, let data = text.data(using: .utf8),
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let field = { (name: String) in body[name] as? String ?? "" }
        switch field("type") {
        case "ready":
            showMode()
            if let model { showCart(model.cart.items) }
        case "location":
            located(url: field("url"), title: field("title"))
        case "cartAdd":
            if field("url").hasPrefix("http") {
                var source = field("source")
                if source.isEmpty, let url = URL(string: field("url")) {
                    source = SourceCatalog.adapterType(for: url).map { $0.id.rawValue } ?? ""
                }
                model?.cart.add(CartItem(url: field("url"), title: field("title"), source: source))
            }
        case "cartRemove":
            model?.cart.remove(field("url"))
        case "open":
            if let url = URL(string: field("url")) { leave(url, title: field("title")) }
        default:
            break
        }
    }

    /// Pages outside the catalog site leave it (see `leave`).
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        // Redirects and script navigation load here; only a tapped link to another site leaves.
        guard let url = navigationAction.request.url, navigationAction.targetFrame?.isMainFrame ?? true,
              navigationAction.navigationType == .linkActivated,
              url.scheme == "http" || url.scheme == "https", !sameSite(url) else {
            decisionHandler(.allow)
            return
        }
        decisionHandler(.cancel)
        leave(url)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            if sameSite(url) { webView.load(navigationAction.request) } else { leave(url) }
        }
        return nil
    }
}

/// Breaks the retain cycle between the content controller and its handler.
private final class WeakMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: (any WKScriptMessageHandler)?

    init(_ target: any WKScriptMessageHandler) {
        self.target = target
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(userContentController, didReceive: message)
    }
}

private struct CatalogWebView: UIViewRepresentable {
    let state: CatalogState

    func makeUIView(context: Context) -> WKWebView { state.webView }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

struct CatalogView: View {
    @Environment(AppModel.self) private var model
    @ObservedObject var state: CatalogState
    @AppStorage(CatalogSite.storageKey) private var site = CatalogSite.defaultAddress
    @AppStorage(CatalogSite.cartLinksKey) private var cartLinks = true

    var body: some View {
        VStack(spacing: 0) {
            if state.isLoading {
                ProgressView(value: state.progress).progressViewStyle(.linear)
            }
            CatalogWebView(state: state)
        }
        .overlay {
            if let error = state.error {
                VStack(spacing: 10) {
                    Text("The catalog site could not load").font(.headline)
                    Text(state.site.absoluteString).font(.caption).multilineTextAlignment(.center)
                    Text(error).font(.caption).foregroundStyle(.red).multilineTextAlignment(.center)
                    HStack {
                        Button("Retry") { state.reload() }.buttonStyle(.borderedProminent)
                        if state.site != URL(string: CatalogSite.defaultAddress) {
                            Button("Use NovelDB") { site = CatalogSite.defaultAddress }.buttonStyle(.bordered)
                        }
                    }
                }
                .padding(24)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color(uiColor: .systemBackground))
            }
        }
        .navigationTitle("Catalog")
        // The catalog site has its own header; pull down to reload.
        .toolbar(.hidden, for: .navigationBar)
        .onAppear {
            state.model = model
            // The site may have changed in Settings before this tab was first shown.
            state.load(CatalogSite.current)
            state.showMode()
            state.showCart(model.cart.items)
        }
        .onChange(of: model.cart.items) { _, items in state.showCart(items) }
        .onChange(of: site) { _, _ in
            state.load(CatalogSite.current)
            state.showMode()
        }
        .onChange(of: cartLinks) { _, _ in state.showMode() }
        // Switching a script engine on or off changes which sites count as supported.
        .onChange(of: model.settings.engines) { _, _ in state.showMode() }
    }
}
