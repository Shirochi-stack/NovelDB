import NpiaWebEngine
import SwiftUI

enum Panel: String, CaseIterable, Hashable, Identifiable {
    case add, catalog, browser, queue, settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .add: return "Add"
        case .catalog: return "Catalog"
        case .browser: return "Browser"
        case .queue: return "Queue"
        case .settings: return "Settings"
        }
    }

    var icon: String {
        switch self {
        case .add: return "plus.circle"
        case .catalog: return "books.vertical"
        case .browser: return "globe"
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
    @StateObject private var catalog = CatalogState()

    var body: some View {
        Group {
            if sizeClass == .regular {
                NavigationSplitView {
                    List(Panel.allCases, selection: $selection) { panel in
                        NavigationLink(value: panel) {
                            Label(panel.title, systemImage: panel.icon)
                                .badge(badgeCount(panel))
                        }
                    }
                    .themedBackground()
                    .navigationTitle("NovelDB")
                } detail: {
                    NavigationStack {
                        content(selection ?? .add)
                    }
                }
            } else {
                TabView(selection: Binding(get: { selection ?? .add }, set: { selection = $0 })) {
                    ForEach(Panel.allCases) { panel in
                        NavigationStack {
                            content(panel)
                        }
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
            if phase == .active { Task { await model.checkAppUpdate(automatic: true) } }
            // WebKit can drop session cookies when the app is stopped, so keep a copy of the logins.
            if phase == .inactive || phase == .background { Task { await model.snapshotSessions() } }
        }
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
        case .add: AddURLView { selection = .queue }
        case .catalog: CatalogView(state: catalog)
        case .browser: BrowserView { selection = .add }
        case .queue: QueueView()
        case .settings: SettingsView()
        }
    }
}
