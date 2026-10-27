import NpiaSources
import NpiaWebEngine
import SwiftUI

/// A plain browser for signing in to any site. Logins land in the cookie store downloads use,
/// and Password AutoFill offers saved passwords as it does in Safari.
struct BrowserView: View {
    @Environment(AppModel.self) private var model
    var onDownload: () -> Void = {}

    @StateObject private var browser = BrowserState()
    @State private var address = ""
    @FocusState private var editing: Bool

    private var downloadable: Bool {
        browser.url.map { SourceCatalog.adapterType(for: $0) != nil } ?? false
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                TextField("Site address or search", text: $address)
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
            }
            .padding(.horizontal)
            .padding(.vertical, 8)
            if browser.isLoading {
                ProgressView(value: browser.progress).progressViewStyle(.linear)
            }
            if downloadable {
                Button {
                    model.pendingAddText = browser.url?.absoluteString
                    onDownload()
                } label: {
                    Label("Download this book", systemImage: "arrow.down.circle.fill").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .padding(.horizontal)
                .padding(.bottom, 6)
            }
            if browser.url == nil {
                BrowserStartPage { browser.open($0) }
            } else {
                BrowserWebView(state: browser)
            }
        }
        .navigationTitle(browser.title.isEmpty ? "Browser" : browser.title)
        .navigationBarTitleDisplayMode(.inline)
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
        .onChange(of: browser.url) { _, url in
            if !editing, let url { address = url.absoluteString }
        }
    }
}

private struct BrowserStartPage: View {
    var open: (String) -> Void
    @State private var domains: [String] = []

    var body: some View {
        List {
            Section {
                Text("Open any site and log in as you would in Safari. Downloads from that site then use your login. Password AutoFill can save and fill your passwords here.")
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
                    domains = await SiteCookies.domains()
                }
            }
        }
    }
}
