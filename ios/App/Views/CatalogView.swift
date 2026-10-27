import NpiaSources
import SwiftUI
import UIKit
import WebKit

/// The catalog site the Catalog tab opens (Settings > Catalog site).
enum CatalogSite {
    static let storageKey = "catalogSite"
    /// The NovelDB catalog website, hosted on GitHub Pages.
    static let defaultAddress = "https://shirochi-stack.github.io/NovelDB/"

    static var current: URL {
        UserDefaults.standard.string(forKey: storageKey).flatMap(URL.init(string:)) ?? URL(string: defaultAddress)!
    }
}

/// The catalog website's web view. It lives as long as the root view, so switching tabs does not
/// reload the site (it keeps a large catalog in memory). On the NovelDB website
/// `CatalogBridgeScript` makes cards open View Details instead of the novel page, sends outside
/// links to the Browser tab and adds the link-cart button. Another site picked in Settings has no
/// cart buttons, so every link that leaves it goes to the cart instead.
@MainActor
final class CatalogState: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    let webView: WKWebView
    @Published private(set) var progress = 0.0
    @Published private(set) var isLoading = false
    private var observations: [NSKeyValueObservation] = []
    weak var model: AppModel?
    private(set) var site = CatalogSite.current
    private var isNovelDB: Bool { site.absoluteString == CatalogSite.defaultAddress }

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        let controller = WKUserContentController()
        controller.addUserScript(WKUserScript(source: CatalogBridgeScript.source, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        configuration.userContentController = controller
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = true
        super.init()
        let refresh = UIRefreshControl()
        refresh.addTarget(self, action: #selector(pulled(_:)), for: .valueChanged)
        webView.scrollView.refreshControl = refresh
        controller.add(WeakMessageHandler(self), name: "noveldb")
        webView.navigationDelegate = self
        webView.uiDelegate = self
        observations = [
            webView.observe(\.estimatedProgress) { [weak self] view, _ in Task { @MainActor in self?.progress = view.estimatedProgress } },
            webView.observe(\.isLoading) { [weak self] view, _ in Task { @MainActor in self?.isLoading = view.isLoading } },
        ]
        webView.load(URLRequest(url: site))
    }

    /// Opens another catalog site (from Settings).
    func load(_ url: URL) {
        guard url != site else { return }
        site = url
        webView.load(URLRequest(url: url))
    }

    /// Tells the page which cards are in the cart, so their buttons show it.
    func showCart(_ items: [CartItem]) {
        guard let data = try? JSONEncoder().encode(items.map(\.url)), let urls = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.NovelDBApp && window.NovelDBApp.setCart(\(urls))", completionHandler: nil)
    }

    @objc private func pulled(_ control: UIRefreshControl) {
        reload()
        control.endRefreshing()
    }

    func reload() {
        if webView.url == nil { webView.load(URLRequest(url: site)) } else { webView.reload() }
    }

    /// A link out of the catalog: the Browser tab on NovelDB, the link cart on other sites.
    private func leave(_ url: URL, title: String = "") {
        guard url.scheme == "http" || url.scheme == "https" else { return }
        if isNovelDB {
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
            if let model { showCart(model.cart.items) }
        case "cartAdd":
            if field("url").hasPrefix("http") { model?.cart.add(CartItem(url: field("url"), title: field("title"), source: field("source"))) }
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

    var body: some View {
        VStack(spacing: 0) {
            if state.isLoading {
                ProgressView(value: state.progress).progressViewStyle(.linear)
            }
            CatalogWebView(state: state)
        }
        .navigationTitle("Catalog")
        // The catalog site has its own header; pull down to reload.
        .toolbar(.hidden, for: .navigationBar)
        .onAppear {
            state.model = model
            state.showCart(model.cart.items)
        }
        .onChange(of: model.cart.items) { _, items in state.showCart(items) }
        .onChange(of: site) { _, address in
            if let url = URL(string: address) { state.load(url) }
        }
    }
}
