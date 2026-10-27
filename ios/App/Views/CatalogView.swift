import SwiftUI
import WebKit

/// The NovelDB catalog website, hosted on GitHub Pages.
let catalogURL = URL(string: "https://shirochi-stack.github.io/NovelDB/")!

/// The catalog website's web view. It lives as long as the root view, so switching tabs does not
/// reload the site (it keeps a large catalog in memory). `CatalogBridgeScript` makes cards open
/// View Details instead of the novel page, sends outside links to the Browser tab and adds the
/// link-cart button.
@MainActor
final class CatalogState: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    let webView: WKWebView
    @Published private(set) var progress = 0.0
    @Published private(set) var isLoading = false
    private var observations: [NSKeyValueObservation] = []
    weak var model: AppModel?

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        let controller = WKUserContentController()
        controller.addUserScript(WKUserScript(source: CatalogBridgeScript.source, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        configuration.userContentController = controller
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = true
        super.init()
        controller.add(WeakMessageHandler(self), name: "noveldb")
        webView.navigationDelegate = self
        webView.uiDelegate = self
        observations = [
            webView.observe(\.estimatedProgress) { [weak self] view, _ in Task { @MainActor in self?.progress = view.estimatedProgress } },
            webView.observe(\.isLoading) { [weak self] view, _ in Task { @MainActor in self?.isLoading = view.isLoading } },
        ]
        webView.load(URLRequest(url: catalogURL))
    }

    /// Tells the page which cards are in the cart, so their buttons show it.
    func showCart(_ items: [CartItem]) {
        guard let data = try? JSONEncoder().encode(items.map(\.url)), let urls = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.NovelDBApp && window.NovelDBApp.setCart(\(urls))", completionHandler: nil)
    }

    func reload() {
        if webView.url == nil { webView.load(URLRequest(url: catalogURL)) } else { webView.reload() }
    }

    private func open(_ url: URL) {
        guard url.scheme == "http" || url.scheme == "https" else { return }
        model?.pendingBrowserURL = url
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
            if let url = URL(string: field("url")) { open(url) }
        default:
            break
        }
    }

    /// Pages outside the catalog open in the Browser tab.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, navigationAction.targetFrame?.isMainFrame ?? true,
              url.scheme == "http" || url.scheme == "https",
              !url.absoluteString.hasPrefix(catalogURL.absoluteString) else {
            decisionHandler(.allow)
            return
        }
        decisionHandler(.cancel)
        open(url)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { open(url) }
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

    var body: some View {
        VStack(spacing: 0) {
            if state.isLoading {
                ProgressView(value: state.progress).progressViewStyle(.linear)
            }
            CatalogWebView(state: state)
        }
        .navigationTitle("Catalog")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Button { state.reload() } label: { Image(systemName: "arrow.clockwise") }
        }
        .onAppear {
            state.model = model
            state.showCart(model.cart.items)
        }
        .onChange(of: model.cart.items) { _, items in state.showCart(items) }
    }
}
