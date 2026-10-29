import NpiaSources
import NpiaWebEngine
import SwiftUI

/// A plain browser for signing in to any site. Logins land in the cookie store downloads use,
/// and Password AutoFill offers saved passwords as it does in Safari. A page NovelDB can download
/// gets "Download this book" (hands it to the Add tab) and "Add to link cart" (the cart the Catalog
/// tab uses). The 文A menu shows the page in another language; see `BrowserTranslate`.
struct BrowserView: View {
    @Environment(AppModel.self) private var model
    var onDownload: () -> Void = {}

    @StateObject private var browser = BrowserState()
    @State private var address = ""
    /// The page (or, on a translated copy, the original site's page) can be downloaded.
    @State private var downloadable = false
    @State private var showLanguage = false
    @AppStorage(BrowserTranslate.storageKey) private var languageCode = BrowserTranslate.defaultLanguage
    @FocusState private var editing: Bool

    private var language: TranslateLanguage { BrowserTranslate.language(languageCode) }
    private var translateActive: Bool { browser.translating || browser.onTranslatedCopy }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                TextField("Address or search", text: $address)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .submitLabel(.go)
                    .focused($editing)
                    .onSubmit { browser.open(address) }
                Button {
                    if browser.isLoading {
                        browser.webView.stopLoading()
                    } else {
                        browser.webView.reload()
                    }
                } label: {
                    Image(systemName: browser.isLoading ? "xmark" : "arrow.clockwise")
                }
                .disabled(browser.url == nil)
                translateMenu
            }
            .padding(.horizontal)
            .padding(.vertical, 8)
            if browser.isLoading {
                ProgressView(value: browser.progress).progressViewStyle(.linear)
            }
            translateNotes
            if downloadable, let page = browser.sourceURL {
                downloadRow(page)
            }
            if browser.url == nil {
                BrowserStartPage { browser.open($0) }
            } else {
                BrowserWebView(state: browser)
            }
        }
        .navigationTitle(browser.title.isEmpty ? "Browser" : browser.title)
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(isPresented: $showLanguage) { BrowserSettingsForm() }
        .toolbar {
            ToolbarItemGroup(placement: .bottomBar) {
                Button { browser.webView.goBack() } label: { Image(systemName: "chevron.backward") }
                    .disabled(!browser.canGoBack)
                Button { browser.webView.goForward() } label: { Image(systemName: "chevron.forward") }
                    .disabled(!browser.canGoForward)
                Spacer()
                NavigationLink("Sites") { SitesView { browser.open("https://" + $0) } }
            }
        }
        .onAppear {
            takePendingURL()
            refreshDownloadable()
        }
        .onChange(of: model.pendingBrowserURL) { takePendingURL() }
        .onChange(of: browser.url) { _, url in
            if !editing, let url { address = url.absoluteString }
            refreshDownloadable()
        }
        // Switching a script engine on or off changes which pages can be downloaded.
        .onChange(of: model.settings.engines) { _, _ in refreshDownloadable() }
    }

    // MARK: - Translate

    private var translateMenu: some View {
        Menu {
            if translateActive {
                Button("Show original") { browser.showOriginal() }
            } else {
                Button("Translate to \(language.name)") { browser.startTranslating(language: language.code) }
            }
            if !browser.onTranslatedCopy {
                Button("Open a translated copy") { browser.openTranslatedCopy(language: language.code) }
            }
            Button("Language: \(language.name)") { showLanguage = true }
        } label: {
            Text("文A")
                .fontWeight(translateActive ? .bold : .regular)
                .foregroundStyle(translateActive ? Color.accentColor : Color.secondary)
        }
        .disabled(browser.url == nil)
        .accessibilityLabel("Translate")
    }

    @ViewBuilder
    private var translateNotes: some View {
        if browser.translating && browser.translateStatus == .working {
            note("Translating to \(language.name)...")
        }
        if browser.translating && browser.translateStatus == .unchanged {
            note("Nothing was translated. The page may already be in \(language.name).")
        }
        if browser.translating && browser.translateStatus == .blocked {
            HStack {
                Text("This site blocks the translator. A translated copy cannot use your sign-ins.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer(minLength: 8)
                Button("Open a translated copy") { browser.openTranslatedCopy(language: language.code) }
                    .font(.caption)
            }
            .padding(.horizontal)
            .padding(.vertical, 2)
        }
        if browser.onTranslatedCopy {
            note("Translated copy from Google: sign-ins do not apply here. Downloads use the original site.")
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal)
            .padding(.vertical, 2)
    }

    // MARK: - Download and link cart

    /// "Download this book" and "Add to link cart" side by side. `page` is the site's own address,
    /// also while a translated copy is on screen.
    private func downloadRow(_ page: URL) -> some View {
        // The phone site's address becomes the desktop one the scraper reads.
        let pageAddress = SourceCatalog.canonicalURL(page).absoluteString
        let inCart = model.cart.contains(pageAddress)
        return HStack(spacing: 8) {
            Button {
                model.pendingAddText = pageAddress
                onDownload()
            } label: {
                Label("Download this book", systemImage: "arrow.down.circle.fill")
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            Button {
                if inCart {
                    model.cart.remove(pageAddress)
                } else {
                    let source = SourceCatalog.adapterType(for: page).map { $0.id.rawValue } ?? ""
                    model.cart.add(CartItem(url: pageAddress, title: CatalogPages.cleanTitle(browser.title, for: page), source: source))
                }
            } label: {
                HStack(spacing: 6) {
                    if !inCart {
                        Image("HalgakosCart").resizable().scaledToFit().frame(width: 22, height: 22)
                    }
                    Text(inCart ? "✓ In link cart" : "Add to link cart")
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
        }
        .padding(.horizontal)
        .padding(.bottom, 6)
    }

    /// Decided when the address or the engine settings change, not on every redraw.
    private func refreshDownloadable() {
        if let page = browser.sourceURL {
            downloadable = SourceCatalog.downloadablePage(page, engines: model.settings.engines)
        } else {
            downloadable = false
        }
    }

    private func takePendingURL() {
        guard let url = model.pendingBrowserURL else { return }
        model.pendingBrowserURL = nil
        browser.webView.load(URLRequest(url: url))
    }
}

private struct BrowserStartPage: View {
    var open: (String) -> Void
    @State private var domains: [String] = []

    var body: some View {
        List {
            Section {
                Text("Open any site and log in as you would in Safari. Downloads from that site then use your login. Password AutoFill can save and fill your passwords here. Pages NovelDB can download show Download and link cart buttons.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            if !domains.isEmpty {
                Section("Sites with saved logins") {
                    ForEach(domains, id: \.self) { domain in
                        Button(domain) { open(domain) }
                    }
                }
            }
        }
        .themedBackground()
        .task { domains = await SiteCookies.domains() }
    }
}

/// Sites that hold cookies, with per-site and global sign-out.
struct SitesView: View {
    @Environment(AppModel.self) private var model
    var open: (String) -> Void = { _ in }
    @State private var domains: [String] = []
    @State private var confirmAll = false

    var body: some View {
        List {
            Section {
                if domains.isEmpty {
                    Text("No saved logins yet.").foregroundStyle(.secondary)
                }
                ForEach(domains, id: \.self) { domain in
                    HStack {
                        Button(domain) { open(domain) }
                        Spacer()
                        Button("Sign out", role: .destructive) {
                            Task {
                                await SiteCookies.signOut(domain)
                                // The saved copy of a qdn or rbooks login would bring it back at the next start.
                                model.forgetSavedSessions(forDomain: domain)
                                domains = await SiteCookies.domains()
                            }
                        }
                        .buttonStyle(.borderless)
                    }
                }
            } footer: {
                Text("Signing out removes that site's cookies, including its login. Saved passwords stay in your password manager.")
            }
            Section {
                Button("Sign out of all sites", role: .destructive) { confirmAll = true }
            }
        }
        .themedBackground()
        .navigationTitle("Signed-in sites")
        .task { domains = await SiteCookies.domains() }
        .refreshable { domains = await SiteCookies.domains() }
        .confirmationDialog("Sign out of all sites?", isPresented: $confirmAll, titleVisibility: .visible) {
            Button("Sign out", role: .destructive) {
                Task {
                    await SiteCookies.signOutAll()
                    model.forgetSavedSessions(forDomain: nil)
                    domains = await SiteCookies.domains()
                }
            }
        }
    }
}
