import NpiaWebEngine
import SwiftUI

enum Panel: String, CaseIterable, Hashable, Identifiable {
    case add, catalog, browser, library, queue, settings

    var id: String { rawValue }

    /// The tabs shown: Add and Catalog are one tab ("Download") with a switch at its top.
    static let shown: [Panel] = [.add, .browser, .library, .queue, .settings]

    /// The shown tab a panel lives in.
    var tab: Panel { self == .catalog ? .add : self }

    var title: String {
        switch self {
        case .add: return "Download"
        case .catalog: return "Catalog"
        case .browser: return "Browser"
        case .library: return "Library"
        case .queue: return "Queue"
        case .settings: return "Settings"
        }
    }

    var icon: String {
        switch self {
        case .add: return "square.and.arrow.down"
        case .catalog: return "books.vertical"
        case .browser: return "globe"
        case .library: return "books.vertical.fill"
        case .queue: return "arrow.down.circle"
        case .settings: return "gearshape"
        }
    }
}

/// Tabs on iPhone; a sidebar split on iPad and other regular-width layouts.
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.scenePhase) private var scenePhase
    @State private var selection: Panel? = .add
    /// Which half of the Download tab shows: the Catalog or the Add links form.
    @State private var downloadMode: Panel = .add
    @StateObject private var catalog = CatalogState()

    var body: some View {
        Group {
            if sizeClass == .regular {
                NavigationSplitView {
                    List(Panel.shown, selection: tabSelection) { panel in
                        NavigationLink(value: panel) {
                            Label(panel.title, systemImage: panel.icon)
                                .badge(badgeCount(panel))
                        }
                    }
                    .themedBackground()
                    .navigationTitle("NovelDB")
                } detail: {
                    stack((selection ?? .add).tab)
                }
            } else {
                TabView(selection: Binding(get: { (selection ?? .add).tab }, set: { tabSelection.wrappedValue = $0 })) {
                    ForEach(Panel.shown) { panel in
                        stack(panel)
                        .tabItem { Label(panel.title, systemImage: panel.icon) }
                        .badge(badgeCount(panel))
                        .tag(panel)
                    }
                }
            }
        }
        .background(KeyboardDismissInstaller())
        .webLoginSheet()
        // Rule engines the user opted in to update themselves at most once a day. (Not in
        // AppModel.init: a task started there made the app close right after launch.)
        .task { await model.checkEngineUpdates(automatic: true) }
        // A qdn login the web view dropped while the app was stopped comes back from its saved copy.
        .task { await model.restoreSessions() }
        // The app's own updates: at start and each time the app returns to the foreground, at most
        // once a day and only with the setting on. (Same reason: not in AppModel.init.)
        .task { await model.checkAppUpdate(automatic: true) }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                model.appBecameActive()
                Task { await model.checkAppUpdate(automatic: true) }
            case .inactive:
                // WebKit can drop session cookies when the app is stopped, so keep a copy of the logins.
                Task { await model.snapshotSessions() }
            case .background:
                Task { await model.snapshotSessions() }
                model.appDidEnterBackground()
            default:
                break
            }
        }
        // A tap on a queue notification asks for the Queue tab (see AppModel.open).
        .onChange(of: model.requestedPanel) { _, _ in applyRequestedPanel() }
        .onAppear { applyRequestedPanel() }
        #if DEBUG
        .task { await model.runLiveDownloadIfAsked() }
        .task { await model.appUpdate.writeReportIfAsked(to: model.outputDirectory) }
        #endif
        .sheet(isPresented: Binding(get: { model.appUpdate.showingOffer }, set: { model.appUpdate.showingOffer = $0 })) {
            AppUpdateSheet()
        }
        .onChange(of: model.pendingBrowserURL) { _, url in
            if url != nil { selection = .browser }
        }
        .sheet(item: Binding(get: { model.pendingUpload }, set: { model.pendingUpload = $0 })) { pending in
            ShareAgreementView(pending: pending)
        }
        .alert("NovelDB", isPresented: Binding(get: { model.message != nil }, set: { if !$0 { model.message = nil } })) {
            Button("OK", role: .cancel) { model.message = nil }
        } message: {
            Text(model.message ?? "")
        }
    }

    private func applyRequestedPanel() {
        guard let panel = model.requestedPanel else { return }
        selection = panel
        model.requestedPanel = nil
    }

    /// The tab picked in the bar or sidebar; the Download tab reopens the half used last.
    private var tabSelection: Binding<Panel?> {
        Binding(get: { selection?.tab }, set: { panel in
            selection = panel == .add ? downloadMode : panel
        })
    }

    /// The navigation stack of a tab. The Queue's keeps its path in the model, so a notification
    /// can open a job's page.
    @ViewBuilder
    private func stack(_ panel: Panel) -> some View {
        if panel == .queue {
            NavigationStack(path: Bindable(model).queuePath) {
                content(panel)
            }
        } else {
            NavigationStack {
                content(panel)
            }
        }
    }

    private func badgeCount(_ panel: Panel) -> Int {
        switch panel {
        case .queue: return model.jobs.filter { $0.state.isActive }.count
        case .add: return model.cart.items.count
        default: return 0
        }
    }

    @ViewBuilder
    private func content(_ panel: Panel) -> some View {
        switch panel {
        case .add, .catalog:
            Group {
                if downloadMode == .catalog {
                    CatalogView(state: catalog)
                } else {
                    AddURLView { model.showQueue() }
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                // Not over the Catalog's reader in full screen.
                if !(downloadMode == .catalog && catalog.readerFullScreen) {
                    Picker("Download", selection: Binding(get: { downloadMode }, set: { downloadMode = $0; selection = $0 })) {
                        Label("Catalog", systemImage: "books.vertical").tag(Panel.catalog)
                        Label("Add links", systemImage: "plus.circle").tag(Panel.add)
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 6)
                    .background(.bar)
                }
            }
            .onChange(of: selection) { _, panel in
                if panel == .add || panel == .catalog, let panel { downloadMode = panel }
            }
        case .browser: BrowserView { selection = .add }
        case .library: LibraryView()
        case .queue: QueueView()
        case .settings: SettingsView()
        }
    }
}
