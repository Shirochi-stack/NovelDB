import SwiftUI

enum Panel: String, CaseIterable, Hashable, Identifiable {
    case add, queue, uploads, settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .add: return "Add"
        case .queue: return "Queue"
        case .uploads: return "Uploads"
        case .settings: return "Settings"
        }
    }

    var icon: String {
        switch self {
        case .add: return "plus.circle"
        case .queue: return "arrow.down.circle"
        case .uploads: return "icloud.and.arrow.up"
        case .settings: return "gearshape"
        }
    }
}

/// Tabs on iPhone; a sidebar split on iPad and other regular-width layouts.
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var selection: Panel? = .add

    var body: some View {
        Group {
            if sizeClass == .regular {
                NavigationSplitView {
                    List(Panel.allCases, selection: $selection) { panel in
                        NavigationLink(value: panel) {
                            Label(panel.title, systemImage: panel.icon)
                                .badge(panel == .queue ? activeCount : 0)
                        }
                    }
                    .navigationTitle("Npia Mobile")
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
                        .badge(panel == .queue ? activeCount : 0)
                        .tag(panel)
                    }
                }
            }
        }
        .alert("Npia Mobile", isPresented: Binding(get: { model.message != nil }, set: { if !$0 { model.message = nil } })) {
            Button("OK", role: .cancel) { model.message = nil }
        } message: {
            Text(model.message ?? "")
        }
    }

    private var activeCount: Int {
        model.jobs.filter { $0.state.isActive }.count
    }

    @ViewBuilder
    private func content(_ panel: Panel) -> some View {
        switch panel {
        case .add: AddURLView { selection = .queue }
        case .queue: QueueView()
        case .uploads: UploadHistoryView()
        case .settings: SettingsView()
        }
    }
}
