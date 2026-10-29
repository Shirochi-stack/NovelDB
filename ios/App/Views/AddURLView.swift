import NpiaCore
import NpiaSources
import SwiftUI

/// One entry point for every source: paste URLs, check the detected source, and set the
/// download options on one compact screen. Every option is saved immediately, the way the
/// desktop External dialog saves its `ext_*` keys (npia edits the main window keys).
struct AddURLView: View {
    @Environment(AppModel.self) private var model
    var onQueued: () -> Void = {}

    @State private var text = ""

    private var urls: [URL] {
        var seen = Set<String>()
        return (SourceInfo.urls(in: text) + model.cart.items.compactMap { URL(string: $0.url) })
            .filter { seen.insert($0.absoluteString).inserted }
    }
    // A link is downloadable when any enabled site engine can try it: the script engines (novel-downloader
    // rules, WebToEpub) try sites that no list names, since WebToEpub also recognises some sites by their page.
    private var supported: [URL] { urls.filter { SiteEngineRouter.canTry($0, engines: model.settings.engines) } }
    private var source: SourceID? { supported.first.flatMap { SourceCatalog.adapterType(for: $0)?.id } }
    private var effective: SharedSettings { source.map { model.settings.effective(for: $0) } ?? model.settings.shared }

    var body: some View {
        Form {
            Section {
                TextField("Novel URL (one per line)", text: $text, axis: .vertical)
                    .lineLimit(1...4)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    .autocorrectionDisabled()
                HStack {
                    PasteButton(payloadType: String.self) { strings in
                        guard let first = strings.first else { return }
                        Task { @MainActor in
                            text = text.isEmpty ? first : text + "\n" + first
                        }
                    }
                    .labelStyle(.titleAndIcon)
                    .controlSize(.small)
                    Spacer()
                    if !text.isEmpty {
                        Button("Clear", role: .destructive) { text = "" }
                            .controlSize(.small)
                    }
                }
            } footer: {
                detectionSummary
            }

            if !model.cart.items.isEmpty {
                Section {
                    NavigationLink {
                        LinkCartView()
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("🛒 Link cart (\(model.cart.items.count))")
                            Text("Books from the Catalog tab; Download queues them too")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }

            Section {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Format").font(.caption).foregroundStyle(.secondary)
                    FlowLayout(spacing: 8) {
                        ForEach(OutputFormat.allCases, id: \.self) { format in
                            ChipToggle(title: format.rawValue.uppercased(), isOn: formatBinding(format))
                        }
                    }
                    if effective.formats.contains(.txt) {
                        Toggle("Separate TXT files", isOn: overridable(\.separateTXT, \.separateTXT))
                            .font(.subheadline)
                    }
                }
                FlowLayout(spacing: 20) {
                    CompactStepper(title: "Threads", value: overridable(\.threads, \.threads), range: 1...16)
                    CompactStepper(title: "Image Workers", value: sharedOnly(\.imageWorkers), range: 1...16)
                }
                HStack(spacing: 8) {
                    Text("Rate Limiting").font(.subheadline)
                    CompactDecimal(title: "Min", value: overridable(\.intervalMin, \.intervalMin))
                    CompactDecimal(title: "Max", value: overridable(\.intervalMax, \.intervalMax))
                    Text("s").font(.caption).foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                }
                HStack(spacing: 8) {
                    Text("Chapter Range").font(.subheadline)
                    CompactNumber(title: "From", value: rangeBinding(\.rangeFrom, \.rangeFrom))
                    CompactNumber(title: "To", value: rangeBinding(\.rangeTo, \.rangeTo))
                    Spacer(minLength: 0)
                }
                FlowLayout(spacing: 8) {
                    ChipToggle(title: "Skip paid", isOn: sharedOnly(\.skipPaid))
                    ChipToggle(title: "Number chapters", isOn: overridable(\.numberChapters, \.numberChapters))
                    ChipToggle(title: "Generate on stop", isOn: sharedOnly(\.generateOnStop))
                    ChipToggle(title: "Use Cache", isOn: overridable(\.useCache, \.useCache))
                    ChipToggle(title: "Cache Images", isOn: overridable(\.cacheImages, \.cacheImages))
                }
            } header: {
                Text(source.map { "Download settings (\($0.rawValue))" } ?? "Download settings")
            }

            if let source {
                sourceSection(source)
            }

            Section {
                Button {
                    queue()
                } label: {
                    Label(supported.count > 1 ? "Download \(supported.count) books" : "Download", systemImage: "arrow.down.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(supported.isEmpty)
                // Like the desktop Download page, the sign-ins sit next to the Download button.
                NavigationLink {
                    EnterAppView()
                } label: {
                    Label("Enter App", systemImage: "key").frame(maxWidth: .infinity)
                }
            } footer: {
                Text("Sign in to sfc, qdn and rbooks to read the chapters your account bought.")
            }
        }
        .themedBackground()
        .environment(\.defaultMinListRowHeight, 34)
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle("Add")
        .onAppear { takePendingText() }
        .onChange(of: model.pendingAddText) { takePendingText() }
    }

    private func takePendingText() {
        guard let pending = model.pendingAddText else { return }
        model.pendingAddText = nil
        text = text.isEmpty ? pending : text + "\n" + pending
    }

    // MARK: Bindings that save straight into the app settings

    private func overridable<T>(_ sharedPath: WritableKeyPath<SharedSettings, T>, _ overridePath: WritableKeyPath<SharedOverrides, T?>) -> Binding<T> {
        let source = source
        return Binding(
            get: { effective[keyPath: sharedPath] },
            set: { model.settings.setShared(sharedPath, overridePath, $0, for: source) }
        )
    }

    private func sharedOnly<T>(_ path: WritableKeyPath<SharedSettings, T>) -> Binding<T> {
        Binding(get: { model.settings.shared[keyPath: path] }, set: { model.settings.shared[keyPath: path] = $0 })
    }

    private func rangeBinding(_ sharedPath: WritableKeyPath<SharedSettings, Int?>, _ overridePath: WritableKeyPath<SharedOverrides, Int?>) -> Binding<Int?> {
        let source = source
        return Binding(
            get: { effective[keyPath: sharedPath] },
            set: { model.settings.setRange(sharedPath, overridePath, $0, for: source) }
        )
    }

    /// At least one format stays selected; order follows `OutputFormat.allCases`.
    private func formatBinding(_ format: OutputFormat) -> Binding<Bool> {
        let formats = overridable(\.formats, \.formats)
        return Binding(
            get: { formats.wrappedValue.contains(format) },
            set: { on in
                let current = formats.wrappedValue
                if on {
                    formats.wrappedValue = OutputFormat.allCases.filter { current.contains($0) || $0 == format }
                } else if current.count > 1 {
                    formats.wrappedValue = current.filter { $0 != format }
                }
            }
        )
    }

    private func settingsBinding<T>(_ path: WritableKeyPath<AppSettings, T>) -> Binding<T> {
        Binding(get: { model.settings[keyPath: path] }, set: { model.settings[keyPath: path] = $0 })
    }

    // MARK: Sections

    @ViewBuilder
    private var detectionSummary: some View {
        if urls.isEmpty {
            Text("Paste a book page or chapter list URL. The source is detected automatically.")
        } else {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(urls.filter { !supported.contains($0) }, id: \.self) { url in
                    Label("Not supported: \(url.host ?? url.absoluteString)", systemImage: "xmark.circle")
                        .foregroundStyle(.red)
                }
                // No site list names these, so only the script engines will try them.
                ForEach(supported.filter { SourceCatalog.adapterType(for: $0) == nil }, id: \.self) { url in
                    Label("\(url.host ?? url.absoluteString) is on no engine's site list; the site engines will still try it",
                          systemImage: "questionmark.circle")
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder
    private func sourceSection(_ source: SourceID) -> some View {
        let groups = SourceInfo.groups(for: source)
        if !groups.isEmpty {
            Section("\(source.rawValue) options") {
                FlowLayout(spacing: 8) {
                    ForEach(groups, id: \.self) { group in
                        switch group {
                        case .npia:
                            ChipToggle(title: "Download Author Notices", isOn: settingsBinding(\.npia.includeNotices))
                            ChipToggle(title: "Browser fallback", isOn: settingsBinding(\.npia.useBrowserFallback))
                        case .kpage:
                            ChipToggle(title: "Skip last page", isOn: settingsBinding(\.sources.kpage.skipLastPage))
                            ChipToggle(title: "Keep filler", isOn: settingsBinding(\.sources.kpage.keepFiller))
                            ChipToggle(title: "Dedupe images", isOn: settingsBinding(\.sources.kpage.dedupeImages))
                        case .ntkCover:
                            ChipToggle(title: "Prefer source cover", isOn: settingsBinding(\.sources.ntkNpiaCover))
                        case .nwebCover:
                            ChipToggle(title: "Store cover", isOn: settingsBinding(\.sources.nwebStoreCover))
                        case .koboLayout:
                            ChipToggle(title: "Horizontal Japanese text", isOn: settingsBinding(\.sources.koboHorizontalLayout))
                        case .longImages:
                            ChipToggle(title: "Long image layout", isOn: settingsBinding(\.images.longImageLayout))
                        }
                    }
                }
                if groups.contains(.kpage), model.settings.sources.kpage.dedupeImages {
                    CompactStepper(title: "Leading images to compare", value: settingsBinding(\.sources.kpage.dedupeLeadingImages), range: 1...10)
                }
            }
        }
    }

    private func queue() {
        let settings = model.settings
        model.enqueue(supported) { _ in settings }
        model.cart.removeAll(Set(supported.map(\.absoluteString)))
        text = ""
        onQueued()
    }
}

/// The books in the link cart; swipe one to remove it.
struct LinkCartView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            if model.cart.items.isEmpty {
                Text("The cart is empty. Tap the ⬇️ button on a card in the Catalog tab to add a book.")
                    .foregroundStyle(.secondary)
            }
            ForEach(model.cart.items) { item in
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title.isEmpty ? item.url : item.title).lineLimit(1)
                    Text([item.source, URL(string: item.url)?.host ?? ""].filter { !$0.isEmpty }.joined(separator: " \u{00B7} "))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                .swipeActions {
                    Button("Remove", role: .destructive) { model.cart.remove(item.url) }
                }
            }
        }
        .themedBackground()
        .navigationTitle("Link cart")
        .toolbar {
            if !model.cart.items.isEmpty {
                Button("Clear cart", role: .destructive) {
                    model.cart.clear()
                    dismiss()
                }
            }
        }
    }
}
