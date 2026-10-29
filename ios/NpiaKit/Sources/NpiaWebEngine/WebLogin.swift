import Combine
import Foundation
import WebKit

/// Shows login sessions to the user. The app implements this (or uses `WebLoginCoordinator`)
/// and presents `session.webView` in a sheet, calling `session.finish()` or `session.cancel()`.
@MainActor
public protocol WebLoginPresenting: AnyObject {
    /// False when no UI is attached; `presentLogin` then fails with `WebEngineError.unavailable`.
    var canPresent: Bool { get }
    func present(_ session: WebLoginSession)
    func dismiss(_ session: WebLoginSession)
}

/// One visible sign-in page backed by a profile's data store.
@MainActor
public final class WebLoginSession: NSObject, ObservableObject, Identifiable, WKNavigationDelegate, WKUIDelegate {
    public let id = UUID()
    public let url: URL
    public let profile: String
    public let webView: WKWebView
    @Published public private(set) var currentURL: URL?
    @Published public private(set) var title: String = ""
    @Published public private(set) var isLoading = false

    private var continuation: CheckedContinuation<Void, Error>?

    init(url: URL, profile: String, store: WKWebsiteDataStore) {
        self.url = url
        self.profile = profile
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = store
        configuration.applicationNameForUserAgent = BrowserProfile.safariApplicationName
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.navigationDelegate = self
        webView.uiDelegate = self
    }

    /// Presents the session and returns when the user taps Done; throws `CancellationError`
    /// when they dismiss it.
    func run(presenter: any WebLoginPresenting) async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                self.continuation = continuation
                webView.load(URLRequest(url: url))
                presenter.present(self)
            }
        } onCancel: {
            Task { @MainActor in self.cancel() }
        }
    }

    /// The user finished signing in.
    public func finish() {
        resume(with: nil)
    }

    /// The user dismissed the sheet without finishing.
    public func cancel() {
        resume(with: CancellationError())
    }

    public func reload() {
        webView.reload()
    }

    private func resume(with error: Error?) {
        guard let continuation else { return }
        self.continuation = nil
        webView.stopLoading()
        if let error {
            continuation.resume(throwing: error)
        } else {
            continuation.resume()
        }
    }

    private func refresh(loading: Bool) {
        currentURL = webView.url
        title = webView.title ?? ""
        isLoading = loading
    }

    public nonisolated func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        MainActor.assumeIsolated { self.refresh(loading: true) }
    }

    public nonisolated func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        MainActor.assumeIsolated { self.refresh(loading: true) }
    }

    public nonisolated func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        MainActor.assumeIsolated { self.refresh(loading: false) }
    }

    public nonisolated func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        MainActor.assumeIsolated { self.refresh(loading: false) }
    }

    public nonisolated func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        MainActor.assumeIsolated { self.refresh(loading: false) }
    }

    // Sign-in pages often open providers in a popup; keep them in the same view.
    public nonisolated func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                                    for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        MainActor.assumeIsolated {
            if navigationAction.targetFrame == nil {
                self.webView.load(navigationAction.request)
            }
        }
        return nil
    }
}

/// Default presenter: publishes the pending session for a SwiftUI sheet. Attach it with
/// `.webLoginSheet()` on the root view (iOS), which also sets `isAttached`.
@MainActor
public final class WebLoginCoordinator: ObservableObject, WebLoginPresenting {
    public static let shared = WebLoginCoordinator()

    @Published public var session: WebLoginSession?
    /// Set by the UI once something observes `session`.
    public var isAttached = false

    public init() {}

    public var canPresent: Bool { isAttached }

    public func present(_ session: WebLoginSession) {
        self.session?.cancel()
        self.session = session
    }

    public func dismiss(_ session: WebLoginSession) {
        if self.session === session {
            self.session = nil
        }
    }
}

#if canImport(UIKit) && canImport(SwiftUI)
import SwiftUI
import UIKit

/// Sheet content for a login session: the web view with Cancel and Done buttons.
public struct WebLoginView: View {
    @ObservedObject private var session: WebLoginSession

    public init(session: WebLoginSession) {
        self.session = session
    }

    public var body: some View {
        NavigationStack {
            WebViewContainer(webView: session.webView)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle(session.title.isEmpty ? (session.currentURL?.host ?? session.url.host ?? "") : session.title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { session.cancel() }
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") { session.finish() }
                    }
                    ToolbarItem(placement: .bottomBar) {
                        HStack {
                            Button { session.webView.goBack() } label: { Image(systemName: "chevron.backward") }
                            Spacer()
                            if session.isLoading { ProgressView() }
                            Spacer()
                            Button { session.reload() } label: { Image(systemName: "arrow.clockwise") }
                        }
                    }
                }
        }
        .onDisappear { session.cancel() }
    }
}

struct WebViewContainer: UIViewRepresentable {
    let webView: WKWebView

    func makeUIView(context: Context) -> WKWebView { webView }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

private struct WebLoginSheetModifier: ViewModifier {
    @ObservedObject var coordinator: WebLoginCoordinator

    func body(content: Content) -> some View {
        content
            .sheet(item: $coordinator.session) { session in
                WebLoginView(session: session)
            }
            .onAppear { coordinator.isAttached = true }
    }
}

public extension View {
    /// Presents `WKWebEngine.presentLogin` sessions as sheets.
    func webLoginSheet(_ coordinator: WebLoginCoordinator = .shared) -> some View {
        modifier(WebLoginSheetModifier(coordinator: coordinator))
    }
}
#endif
